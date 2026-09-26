"""SSE 序列化模块：将 ToolCallEvent 转换为 SSE 文本 + SessionStreamState。

从 excelmanus/api.py 提取，集中管理所有 SSE 事件的序列化逻辑。
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid as _uuid
from typing import Any, Callable

from excelmanus.events import TRANSIENT_SSE_TYPES, EventType, ToolCallEvent
from excelmanus.logger import get_logger
from excelmanus.output_guard import (
    sanitize_external_data,
    sanitize_external_text,
    sanitize_streaming_text,
)

logger = get_logger("api.sse")

# 公共路径转换回调类型：由 api.py 注入，避免循环导入
PublicPathFn = Callable[[str], str]

# 默认 pass-through（未注入时原样返回）
_default_public_path: PublicPathFn = lambda path: path


def sse_format(event_type: str, data: dict) -> str:
    """将事件格式化为 SSE 文本行。"""
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event_type}\ndata: {payload}\n\n"


def sse_format_seq(event_type: str, data: dict, seq: int, stream_id: str) -> str:
    """将事件格式化为携带 seq 和 stream_id 的 SSE 文本行。"""
    enriched = {**data, "seq": seq, "stream_id": stream_id}
    payload = json.dumps(enriched, ensure_ascii=False)
    return f"event: {event_type}\ndata: {payload}\n\n"


def inject_seq_into_sse(
    sse_text: str,
    seq: int,
    stream_id: str,
    *,
    replayed: bool = False,
) -> str:
    """向已序列化的 SSE 文本中注入 seq 和 stream_id 字段。

    在 ``data:`` 行的 JSON 对象中追加字段，避免重新完整序列化事件。
    """
    # SSE 格式: "event: xxx\ndata: {...}\n\n"
    # 在 JSON 最后一个 } 之前插入字段
    rpos = sse_text.rfind("}")
    if rpos < 0:
        return sse_text
    insert = f',"seq":{seq},"stream_id":"{stream_id}"'
    if replayed:
        # A reconnect can overlap the previous fetch.  The browser uses this
        # marker together with seq to discard an event it has already applied;
        # it is deliberately absent from live events for wire compatibility.
        insert += ',"replayed":true'
    return sse_text[:rpos] + insert + sse_text[rpos:]


# 带序号的事件条目：(seq, event)
SeqEvent = tuple[int, ToolCallEvent]


class SessionStreamState:
    """管理单个会话的 SSE 事件流状态，支持断连后缓冲与重连。

    每个事件在投递时被分配单调递增的序号（seq），客户端断连后通过
    ``/chat/subscribe`` 携带 ``after_seq`` 精确补发缺失事件。

    当客户端断开时（如页面刷新），事件被缓冲到 event_buffer；
    新客户端通过 /chat/subscribe 重连时，先重放缓冲事件，再接收实时事件。
    """

    __slots__ = (
        "stream_id",
        "event_buffer",
        "subscriber_queue",
        "_buffer_limit",
        "_overflow_warned",
        "_dropped_count",
        "_next_seq",
        "completed_result",
        "completed_at",
    )

    def __init__(self, buffer_limit: int = 500) -> None:
        self.stream_id: str = _uuid.uuid4().hex[:12]
        self.event_buffer: list[SeqEvent] = []
        self.subscriber_queue: asyncio.Queue[SeqEvent | None] | None = None
        self._buffer_limit = buffer_limit
        self._overflow_warned = False
        self._dropped_count = 0
        self._next_seq: int = 1
        # The final ChatResult is kept briefly after the producer finishes so
        # a browser that disconnects between the last tool event and `reply`
        # can reconnect without losing the terminal response.
        self.completed_result: Any | None = None
        self.completed_at: float | None = None

    def mark_completed(self, result: Any) -> None:
        self.completed_result = result
        self.completed_at = time.monotonic()

    def deliver(self, event: ToolCallEvent) -> int:
        """投递事件：有订阅者时入队，否则缓冲。返回分配的 seq。"""
        seq = self._next_seq
        self._next_seq += 1
        item: SeqEvent = (seq, event)
        q = self.subscriber_queue
        if q is not None:
            q.put_nowait(item)
        elif event.event_type in TRANSIENT_SSE_TYPES:
            # 瞬态建议 / Jev 时间线：不进缓冲，刷新 / subscribe 不回放
            return seq
        else:
            if self._buffer_limit <= 0:
                if not self._overflow_warned:
                    logger.warning(
                        "SSE 事件缓冲区大小为 %d，断连期间事件将被丢弃",
                        self._buffer_limit,
                    )
                    self._overflow_warned = True
                self._dropped_count += 1
                return seq

            if len(self.event_buffer) < self._buffer_limit:
                self.event_buffer.append(item)
            else:
                if not self._overflow_warned:
                    logger.warning(
                        "SSE 事件缓冲区已满（%d），将覆盖最旧事件以保留最新事件",
                        self._buffer_limit,
                    )
                    self._overflow_warned = True
                # 保留最新事件，丢弃最旧事件。
                self.event_buffer.pop(0)
                self._dropped_count += 1
                self.event_buffer.append(item)
        return seq

    @property
    def current_seq(self) -> int:
        """已分配的最大 seq（0 表示尚无事件）。"""
        return self._next_seq - 1

    @property
    def first_buffered_seq(self) -> int | None:
        """缓冲区中最早事件的 seq，无缓冲时返回 None。"""
        return self.event_buffer[0][0] if self.event_buffer else None

    @property
    def has_dropped(self) -> bool:
        """是否发生过事件丢弃（缓冲溢出）。"""
        return self._dropped_count > 0

    def attach(self) -> asyncio.Queue[SeqEvent | None]:
        """创建新订阅者队列并附着。返回新队列。"""
        q: asyncio.Queue[SeqEvent | None] = asyncio.Queue()
        self.subscriber_queue = q
        self._overflow_warned = False
        return q

    def detach(self) -> None:
        """断开当前订阅者，后续事件进入缓冲。"""
        self.subscriber_queue = None

    def drain_buffer(self, after_seq: int = 0) -> list[SeqEvent]:
        """取出并清空缓冲区。

        Args:
            after_seq: 仅返回 seq > after_seq 的事件。0 表示返回全部。
        """
        buf = self.event_buffer
        self.event_buffer = []
        self._overflow_warned = False
        if after_seq > 0:
            return [(s, e) for s, e in buf if s > after_seq]
        return buf


def _summarize_tool_args(tool_name: str, arguments: dict[str, Any]) -> str:
    """将工具参数格式化为简洁摘要字符串。

    规则：
    - 跳过 None 或空字符串的参数
    - code 类参数显示行数（如 code=<12行>）
    - 每个参数值截断至 60 字符
    - 总摘要截断至 200 字符
    - 格式: tool_name(key1="val1", key2="val2")
    """
    if not isinstance(arguments, dict) or not arguments:
        return f"{tool_name}()"
    parts: list[str] = []
    for k, v in arguments.items():
        if v is None or v == "":
            continue
        if k == "code" and isinstance(v, str):
            line_count = v.count("\n") + 1
            parts.append(f"code=<{line_count}行>")
            continue
        sv = str(v)
        if len(sv) > 60:
            sv = sv[:57] + "..."
        parts.append(f'{k}="{sv}"')
    summary = f"{tool_name}({', '.join(parts)})"
    if len(summary) > 200:
        summary = summary[:197] + "..."
    return summary


def sse_event_to_sse(
    event: ToolCallEvent,
    *,
    public_path_fn: PublicPathFn = _default_public_path,
) -> str | None:
    """将 ToolCallEvent 转换为 SSE 文本。

    思考 / 工具 / 子代理 / 审批事件一律下发；payload 始终脱敏，
    路径经 public_path_fn 映射为公开工作区身份。
    """
    event_map = {
        EventType.THINKING: "thinking",
        EventType.TOOL_CALL_START: "tool_call_start",
        EventType.TOOL_CALL_END: "tool_call_end",
        EventType.TOOL_CALL_STATE: "tool_call_state",
        EventType.TOOL_CALL_ABORTED: "tool_call_aborted",
        EventType.ITERATION_START: "iteration_start",
        EventType.SUBAGENT_START: "subagent_start",
        EventType.SUBAGENT_ITERATION: "subagent_iteration",
        EventType.SUBAGENT_SUMMARY: "subagent_summary",
        EventType.SUBAGENT_END: "subagent_end",
        EventType.SUBAGENT_TOOL_START: "subagent_tool_start",
        EventType.SUBAGENT_TOOL_END: "subagent_tool_end",
        EventType.USER_QUESTION: "user_question",
        EventType.THINKING_DELTA: "thinking_delta",
        EventType.TEXT_DELTA: "text_delta",
        EventType.TOOL_CALL_ARGS_DELTA: "tool_call_args_delta",
        EventType.EXCEL_PREVIEW: "excel_preview",
        EventType.EXCEL_DIFF: "excel_diff",
        EventType.TEXT_DIFF: "text_diff",
        EventType.TEXT_PREVIEW: "text_preview",
        EventType.FILES_CHANGED: "files_changed",  # 历史 replay only
        EventType.MUTATION: "mutation",
        EventType.PIPELINE_PROGRESS: "pipeline_progress",
        EventType.MEMORY_EXTRACTED: "memory_extracted",
        EventType.COMPACTION: "compaction",
        EventType.FILE_DOWNLOAD: "file_download",
        EventType.VERIFICATION_REPORT: "verification_report",  # 仅用于读取历史，不再产生
        EventType.RETRACT_THINKING: "retract_thinking",
        EventType.RETRACT_TEXT: "retract_text",
        EventType.STAGING_UPDATED: "staging_updated",
        EventType.MODE_CHANGED: "mode_changed",
        EventType.BATCH_PROGRESS: "batch_progress",
        EventType.ROUTE_START: "route_start",
        EventType.ROUTE_END: "route_end",
        EventType.PENDING_APPROVAL: "pending_approval",
        EventType.APPROVAL_RESOLVED: "approval_resolved",
        EventType.CHAT_SUMMARY: "chat_summary",
        EventType.PLAN_CREATED: "plan_created",
        EventType.LLM_RETRY: "llm_retry",
        EventType.FAILURE_GUIDANCE: "failure_guidance",
        EventType.TOOL_CALL_NOTICE: "tool_call_notice",
        EventType.REASONING_NOTICE: "reasoning_notice",
        EventType.TURN_START: "turn_start",
        EventType.TURN_END: "turn_end",
        EventType.TURN_FAILED: "turn_failed",
        EventType.STEP_START: "step_start",
        EventType.STEP_END: "step_end",
        EventType.INBOX_CLAIMED: "inbox_claimed",
        EventType.DISPATCH_ACCEPTED: "dispatch_accepted",
        EventType.DISPATCH_QUEUED: "dispatch_queued",
        EventType.DISPATCH_APPLYING: "dispatch_applying",
        EventType.DISPATCH_APPLIED: "dispatch_applied",
        EventType.DISPATCH_FAILED: "dispatch_failed",
        EventType.UI_HINT: "ui_hint",
        EventType.JEV_TRACE: "jev_trace",
    }
    sse_type = event_map.get(event.event_type, event.event_type.value)

    data: dict[str, Any]

    if event.event_type == EventType.THINKING:
        data = {
            "content": sanitize_external_text(event.thinking, max_len=2000),
            "iteration": event.iteration,
        }
    elif event.event_type in {EventType.TOOL_CALL_START, EventType.TOOL_CALL_STATE}:
        data = {
            "tool_call_id": sanitize_external_text(event.tool_call_id, max_len=160),
            "tool_name": event.tool_name,
            "arguments": sanitize_external_data(
                event.arguments if isinstance(event.arguments, dict) else {},
                max_len=1000,
            ),
            "iteration": event.iteration,
            "execution_id": event.execution_id,
            "execution_state": event.execution_state,
        }
        if event.parent_call_id:
            data["parent_call_id"] = sanitize_external_text(event.parent_call_id, max_len=160)
    elif event.event_type == EventType.TOOL_CALL_END:
        data = {
            "tool_call_id": sanitize_external_text(event.tool_call_id, max_len=160),
            "tool_name": event.tool_name,
            "execution_id": event.execution_id,
            "execution_state": event.execution_state,
            "success": event.success,
            "result": sanitize_external_text(
                event.result[:500] if event.result else "",
                max_len=500,
            ),
            "error": (
                sanitize_external_text(event.error, max_len=300)
                if event.error
                else None
            ),
            "iteration": event.iteration,
        }
        if event.ui:
            data["ui"] = sanitize_external_data(event.ui, max_len=2000)
        if event.parent_call_id:
            data["parent_call_id"] = sanitize_external_text(event.parent_call_id, max_len=160)
    elif event.event_type == EventType.TOOL_CALL_ABORTED:
        # 未执行即放弃：前端据此把卡片定案，不留"进行中"。
        data = {
            "tool_call_id": sanitize_external_text(event.tool_call_id, max_len=160),
            "tool_name": event.tool_name,
            "execution_state": event.execution_state or "failed",
            "reason": sanitize_external_text(event.abort_reason, max_len=60),
            "effect": sanitize_external_text(event.abort_effect, max_len=20),
            "error": (
                sanitize_external_text(event.error, max_len=120)
                if event.error
                else None
            ),
            "message": sanitize_external_text(
                event.result[:500] if event.result else "",
                max_len=500,
            ),
            "iteration": event.iteration,
        }
    elif event.event_type == EventType.ITERATION_START:
        data = {"iteration": event.iteration}
        if event.turn_id:
            data["turn_id"] = event.turn_id
        if event.step_id:
            data["step_id"] = event.step_id
    elif event.event_type == EventType.STEP_START:
        data = {"iteration": event.iteration}
        if event.turn_id:
            data["turn_id"] = event.turn_id
        if event.step_id:
            data["step_id"] = event.step_id
    elif event.event_type == EventType.STEP_END:
        data = {"iteration": event.iteration}
        if event.turn_id:
            data["turn_id"] = event.turn_id
        if event.step_id:
            data["step_id"] = event.step_id
    elif event.event_type == EventType.TURN_START:
        data = {"turn_id": event.turn_id, "iteration": event.iteration, "dispatch": event.dispatch}
    elif event.event_type == EventType.DISPATCH_STATE:
        data = dict(event.dispatch)
    elif event.event_type == EventType.TURN_REPLY:
        data = {"turn_id": event.turn_id, "content": sanitize_streaming_text(event.result),
                "dispatch": event.dispatch, "prompt_tokens": event.prompt_tokens,
                "completion_tokens": event.completion_tokens, "total_tokens": event.total_tokens,
                "cached_tokens": event.cached_tokens,
                "iterations": event.total_iterations}
    elif event.event_type == EventType.TURN_END:
        data = {"turn_id": event.turn_id, "iteration": event.iteration}
    elif event.event_type == EventType.TURN_FAILED:
        data = {
            "turn_id": event.turn_id,
            "iteration": event.iteration,
            "stop_reason": sanitize_external_text(event.stop_reason, max_len=40),
            "error": sanitize_external_text(event.turn_error, max_len=500),
        }
    elif event.event_type == EventType.INBOX_CLAIMED:
        data = {
            "turn_id": event.turn_id,
            "step_id": event.step_id,
            "claimed": event.inbox_claimed or [],
        }
    elif event.event_type in {
        EventType.DISPATCH_ACCEPTED,
        EventType.DISPATCH_QUEUED,
        EventType.DISPATCH_APPLYING,
        EventType.DISPATCH_APPLIED,
        EventType.DISPATCH_FAILED,
    }:
        data = {
            "dispatch_id": sanitize_external_text(event.dispatch_id, max_len=128),
            "client_message_id": sanitize_external_text(event.client_message_id, max_len=128),
            "mode": sanitize_external_text(event.dispatch_mode, max_len=20),
            "status": sanitize_external_text(event.dispatch_status, max_len=40),
            "turn_id": event.turn_id,
            "step_id": event.step_id,
        }
        if event.dispatch_error:
            data["error"] = sanitize_external_text(event.dispatch_error, max_len=300)
    elif event.event_type == EventType.SUBAGENT_START:
        data = {
            "background": event.subagent_background,
            "name": sanitize_external_text(event.subagent_name, max_len=100),
            "reason": sanitize_external_text(event.subagent_reason, max_len=500),
            "tools": event.subagent_tools,
            "permission_mode": sanitize_external_text(
                event.subagent_permission_mode,
                max_len=40,
            ),
            "conversation_id": sanitize_external_text(
                event.subagent_conversation_id,
                max_len=120,
            ),
        }
    elif event.event_type == EventType.SUBAGENT_ITERATION:
        data = {
            "name": sanitize_external_text(event.subagent_name, max_len=100),
            "conversation_id": sanitize_external_text(
                event.subagent_conversation_id,
                max_len=120,
            ),
            "iteration": event.subagent_iterations,
            "tool_calls": event.subagent_tool_calls,
        }
    elif event.event_type == EventType.SUBAGENT_SUMMARY:
        data = {
            "name": sanitize_external_text(event.subagent_name, max_len=100),
            "reason": sanitize_external_text(event.subagent_reason, max_len=500),
            "summary": sanitize_external_text(event.subagent_summary, max_len=4000),
            "tools": event.subagent_tools,
            "permission_mode": sanitize_external_text(
                event.subagent_permission_mode,
                max_len=40,
            ),
            "conversation_id": sanitize_external_text(
                event.subagent_conversation_id,
                max_len=120,
            ),
            "iterations": event.subagent_iterations,
            "tool_calls": event.subagent_tool_calls,
        }
    elif event.event_type == EventType.SUBAGENT_END:
        data = {
            "name": sanitize_external_text(event.subagent_name, max_len=100),
            "reason": sanitize_external_text(event.subagent_reason, max_len=40),
            "stop_reason": sanitize_external_text(event.subagent_reason, max_len=40),
            "diagnostic": sanitize_external_text(event.error or "", max_len=500),
            "success": event.subagent_success,
            "tools": event.subagent_tools,
            "permission_mode": sanitize_external_text(
                event.subagent_permission_mode,
                max_len=40,
            ),
            "conversation_id": sanitize_external_text(
                event.subagent_conversation_id,
                max_len=120,
            ),
            "iterations": event.subagent_iterations,
            "tool_calls": event.subagent_tool_calls,
        }
    elif event.event_type == EventType.SUBAGENT_TOOL_START:
        data = {
            "conversation_id": sanitize_external_text(
                event.subagent_conversation_id,
                max_len=120,
            ),
            "tool_name": event.tool_name,
            "arguments": sanitize_external_data(
                event.arguments if isinstance(event.arguments, dict) else {},
                max_len=500,
            ),
            "tool_index": event.subagent_tool_index,
        }
    elif event.event_type == EventType.SUBAGENT_TOOL_END:
        data = {
            "conversation_id": sanitize_external_text(
                event.subagent_conversation_id,
                max_len=120,
            ),
            "tool_name": event.tool_name,
            "success": event.success,
            "result": sanitize_external_text(
                event.result[:300] if event.result else "",
                max_len=300,
            ),
            "error": (
                sanitize_external_text(event.error, max_len=200)
                if event.error
                else None
            ),
            "tool_index": event.subagent_tool_index,
        }
    elif event.event_type == EventType.USER_QUESTION:
        options: list[dict[str, str]] = []
        for option in event.question_options:
            if not isinstance(option, dict):
                continue
            options.append(
                {
                    "label": sanitize_external_text(
                        str(option.get("label", "") or ""),
                        max_len=80,
                    ),
                    "description": sanitize_external_text(
                        str(option.get("description", "") or ""),
                        max_len=500,
                    ),
                }
            )
        data = {
            "id": sanitize_external_text(event.question_id or "", max_len=120),
            "header": sanitize_external_text(event.question_header or "", max_len=80),
            "text": sanitize_external_text(event.question_text or "", max_len=2000),
            "options": options,
            "multi_select": bool(event.question_multi_select),
            "queue_size": int(event.question_queue_size or 0),
            "selection": event.question_selection,
            "tool_call_id": event.tool_call_id,
        }
    elif event.event_type in {EventType.TASK_LIST_CREATED, EventType.TASK_ITEM_UPDATED}:
        data = {
            "task_list": event.task_list_data,
            "tool_call_id": event.tool_call_id,
            "task_index": event.task_index,
            "task_status": event.task_status,
        }
        sse_type = "task_update"
    elif event.event_type == EventType.THINKING_DELTA:
        data = {
            "content": sanitize_streaming_text(event.thinking_delta),
            "iteration": event.iteration,
        }
    elif event.event_type == EventType.TEXT_DELTA:
        data = {
            "content": event.text_delta,
            "iteration": event.iteration,
        }
    elif event.event_type == EventType.TOOL_CALL_ARGS_DELTA:
        data = {
            "tool_call_id": event.tool_call_id,
            "tool_name": event.tool_name,
            "args_delta": event.args_delta,
        }
    elif event.event_type == EventType.PENDING_APPROVAL:
        data = {
            "approval_id": sanitize_external_text(event.approval_id or "", max_len=120),
            "approval_tool_name": sanitize_external_text(event.approval_tool_name or "", max_len=100),
            "tool_call_id": sanitize_external_text(event.tool_call_id or "", max_len=160),
            "risk_level": sanitize_external_text(event.approval_risk_level or "high", max_len=20),
            "args_summary": sanitize_external_data(
                event.approval_args_summary if isinstance(event.approval_args_summary, dict) else {},
                max_len=1000,
            ),
        }
    elif event.event_type == EventType.APPROVAL_RESOLVED:
        data = {
            "approval_id": sanitize_external_text(event.approval_id or "", max_len=120),
            "approval_tool_name": sanitize_external_text(event.approval_tool_name or "", max_len=100),
            "tool_call_id": sanitize_external_text(event.tool_call_id or "", max_len=160),
            "result": sanitize_external_text(event.result or "", max_len=2000),
            "success": event.success,
            "undoable": event.approval_undoable,
            "has_changes": event.approval_has_changes,
        }
        sse_type = "approval_resolved"
    elif event.event_type == EventType.EXCEL_PREVIEW:
        data = {
            "tool_call_id": sanitize_external_text(event.tool_call_id, max_len=160),
            "file_path": public_path_fn(event.excel_file_path),
            "sheet": sanitize_external_text(event.excel_sheet, max_len=100),
            "columns": event.excel_columns[:100],
            "rows": event.excel_rows[:50],
            "total_rows": event.excel_total_rows,
            "truncated": event.excel_truncated,
            "cell_styles": event.excel_cell_styles[:51] if event.excel_cell_styles else [],
            "merge_ranges": event.excel_merge_ranges[:200] if event.excel_merge_ranges else [],
            "metadata_hints": event.excel_metadata_hints[:20] if event.excel_metadata_hints else [],
        }
    elif event.event_type == EventType.EXCEL_DIFF:
        data = {
            "tool_call_id": sanitize_external_text(event.tool_call_id, max_len=160),
            "file_path": public_path_fn(event.excel_file_path),
            "sheet": sanitize_external_text(event.excel_sheet, max_len=100),
            "affected_range": sanitize_external_text(event.excel_affected_range, max_len=50),
            "changes": event.excel_changes[:200],
            "merge_ranges": event.excel_merge_ranges[:200] if event.excel_merge_ranges else [],
            "old_merge_ranges": event.excel_old_merge_ranges[:200] if event.excel_old_merge_ranges else [],
            "metadata_hints": event.excel_metadata_hints[:20] if event.excel_metadata_hints else [],
        }
        # 跨文件/跨 Sheet 对比扩展字段
        if event.excel_diff_mode:
            data["diff_mode"] = event.excel_diff_mode
            data["file_path_b"] = public_path_fn(event.excel_file_b) if event.excel_file_b else ""
            data["sheet_b"] = sanitize_external_text(event.excel_sheet_b, max_len=100)
            if event.excel_diff_summary:
                data["diff_summary"] = event.excel_diff_summary
    elif event.event_type == EventType.TEXT_DIFF:
        data = {
            "tool_call_id": sanitize_external_text(event.tool_call_id, max_len=160),
            "file_path": sanitize_external_text(event.text_diff_file_path, max_len=500),
            "hunks": event.text_diff_hunks[:300],
            "additions": event.text_diff_additions,
            "deletions": event.text_diff_deletions,
            "truncated": event.text_diff_truncated,
        }
    elif event.event_type == EventType.TEXT_PREVIEW:
        data = {
            "tool_call_id": sanitize_external_text(event.tool_call_id, max_len=160),
            "file_path": sanitize_external_text(event.text_preview_file_path, max_len=500),
            "content": event.text_preview_content[:20000],
            "line_count": event.text_preview_line_count,
            "truncated": event.text_preview_truncated,
        }
    elif event.event_type == EventType.MUTATION:
        mutations = []
        for item in (event.mutations or [])[:50]:
            if not isinstance(item, dict):
                continue
            identity = str(item.get("identity") or "")
            version = str(
                item.get("contentVersion") or item.get("content_version") or ""
            )
            payload = {
                "identity": public_path_fn(identity),
                "content_version": sanitize_external_text(version, max_len=100),
                "source": sanitize_external_text(str(item.get("source") or ""), max_len=40),
            }
            # Deletions must stay distinguishable from writes so every client
            # surface evicts the path instead of re-listing a dead file.
            if bool(item.get("deleted")):
                payload["deleted"] = True
            mutations.append(payload)
        data = {
            "mutations": mutations,
            "files": [
                public_path_fn(f)
                for f in (event.changed_files or [])[:50]
            ],
        }
    elif event.event_type == EventType.FILES_CHANGED:
        # 历史 replay / 旧客户端兼容；新写入统一发 MUTATION。
        data = {
            "files": [
                public_path_fn(f)
                for f in (event.changed_files or [])[:50]
            ],
        }
    elif event.event_type == EventType.UI_HINT:
        data = {
            "surface": sanitize_external_text(event.ui_hint_surface, max_len=40),
            "file_path": public_path_fn(event.ui_hint_file_path) if event.ui_hint_file_path else "",
            "sheet": sanitize_external_text(event.ui_hint_sheet, max_len=120),
            "reason": sanitize_external_text(event.ui_hint_reason, max_len=200),
            "suppress_auto_open": bool(event.ui_hint_suppress_auto_open),
        }
        if event.excel_file_b:
            data["file_path_b"] = public_path_fn(event.excel_file_b)
    elif event.event_type == EventType.JEV_TRACE:
        raw = event.jev_trace if isinstance(event.jev_trace, dict) else {}
        data = sanitize_external_data(dict(raw), max_len=240)
        if not isinstance(data, dict):
            data = {}
        data.pop("api_key", None)
        data.pop("typesafe_api_key", None)
        data.pop("authorization", None)
        data.pop("user_text", None)
        data.pop("state", None)
        data.pop("result_head", None)
        answers = data.get("answers")
        if not isinstance(answers, dict):
            data["answers"] = {}
    elif event.event_type == EventType.PIPELINE_PROGRESS:
        data = {
            "stage": sanitize_external_text(event.pipeline_stage, max_len=60),
            "message": sanitize_external_text(event.pipeline_message, max_len=200),
        }
        if event.tool_call_id:
            data["tool_call_id"] = sanitize_external_text(event.tool_call_id, max_len=160)
        if event.turn_id:
            data["turn_id"] = event.turn_id
        if event.step_id:
            data["step_id"] = event.step_id
    elif event.event_type == EventType.COMPACTION:
        data = sanitize_external_data(event.compaction, max_len=1000)
    elif event.event_type == EventType.MEMORY_EXTRACTED:
        data = {
            "entries": (event.memory_entries or [])[:50],
            "trigger": event.memory_trigger or "session_end",
            "count": len(event.memory_entries or []),
        }
    elif event.event_type == EventType.FILE_DOWNLOAD:
        data = {
            "tool_call_id": sanitize_external_text(event.tool_call_id, max_len=160),
            "file_path": public_path_fn(event.download_file_path),
            "filename": sanitize_external_text(event.download_filename, max_len=260),
            "description": sanitize_external_text(event.download_description, max_len=500),
        }
    elif event.event_type in (EventType.RETRACT_THINKING, EventType.RETRACT_TEXT):
        data = {"iteration": event.iteration}
    elif event.event_type == EventType.STAGING_UPDATED:
        data = {
            "action": event.staging_action,
            "files": event.staging_files[:50],
            "pending_count": event.staging_pending_count,
        }
    elif event.event_type == EventType.MODE_CHANGED:
        data = {
            "mode_name": event.mode_name,
            "enabled": event.mode_enabled,
            "value": event.mode_value,
        }
    elif event.event_type == EventType.BATCH_PROGRESS:
        data = {
            "batch_index": event.batch_index,
            "batch_total": event.batch_total,
            "batch_item_name": sanitize_external_text(event.batch_item_name, max_len=200),
            "batch_status": event.batch_status,
            "batch_elapsed_seconds": event.batch_elapsed_seconds,
            "message": sanitize_external_text(event.pipeline_message, max_len=200),
        }
    elif event.event_type == EventType.ROUTE_START:
        data = {}
    elif event.event_type == EventType.ROUTE_END:
        data = {
            "route_mode": event.route_mode,
            "skills_used": event.skills_used[:20],
        }
    elif event.event_type == EventType.CHAT_SUMMARY:
        data = {
            "total_iterations": event.total_iterations,
            "total_tool_calls": event.total_tool_calls,
            "success_count": event.success_count,
            "failure_count": event.failure_count,
            "elapsed_seconds": event.elapsed_seconds,
            "prompt_tokens": event.prompt_tokens,
            "completion_tokens": event.completion_tokens,
            "total_tokens": event.total_tokens,
            "cached_tokens": event.cached_tokens,
        }
    elif event.event_type == EventType.PLAN_CREATED:
        data = {
            "plan_file_path": sanitize_external_text(event.plan_file_path, max_len=500),
            "plan_title": sanitize_external_text(event.plan_title, max_len=200),
            "plan_task_count": event.plan_task_count,
        }
    elif event.event_type == EventType.LLM_RETRY:
        data = {
            "retry_attempt": event.retry_attempt,
            "retry_max_attempts": event.retry_max_attempts,
            "retry_delay_seconds": event.retry_delay_seconds,
            "retry_error_message": sanitize_external_text(
                event.retry_error_message, max_len=300,
            ),
            "retry_status": event.retry_status,
        }
    elif event.event_type == EventType.FAILURE_GUIDANCE:
        data = {
            "category": event.fg_category,
            "code": event.fg_code,
            "title": sanitize_external_text(event.fg_title, max_len=60),
            "message": sanitize_external_text(event.fg_message, max_len=300),
            "stage": event.fg_stage,
            "retryable": event.fg_retryable,
            "diagnostic_id": event.fg_diagnostic_id,
            "actions": event.fg_actions[:3],
            "provider": event.fg_provider,
            "model": event.fg_model,
        }
    elif event.event_type == EventType.TOOL_CALL_NOTICE:
        data = {
            "tool_name": event.tool_name,
            "args_summary": _summarize_tool_args(event.tool_name, event.arguments),
            "iteration": event.iteration,
        }
    elif event.event_type == EventType.REASONING_NOTICE:
        data = {
            "content": sanitize_external_text(event.thinking, max_len=4000),
            "iteration": event.iteration,
        }
    else:
        data = event.to_dict()

    for field in ("turn_id", "step_id", "trace_id", "span_id", "parent_span_id", "request_id"):
        value = getattr(event, field, "")
        if value and field not in data:
            data[field] = value
    return sse_format(sse_type, data)
