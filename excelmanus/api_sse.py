"""SSE 序列化模块：将 ToolCallEvent 转换为 SSE 文本 + SessionStreamState。

从 excelmanus/api.py 提取，集中管理所有 SSE 事件的序列化逻辑。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Callable

from excelmanus.events import EventType, ToolCallEvent
from excelmanus.logger import get_logger
from excelmanus.output_guard import sanitize_external_data, sanitize_external_text

logger = get_logger("api.sse")

# 公共路径转换回调类型：由 api.py 注入，避免循环导入
PublicPathFn = Callable[[str, bool], str]

# 默认 pass-through（未注入时原样返回）
_default_public_path: PublicPathFn = lambda path, safe_mode: path


def sse_format(event_type: str, data: dict) -> str:
    """将事件格式化为 SSE 文本行。"""
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event_type}\ndata: {payload}\n\n"


class SessionStreamState:
    """管理单个会话的 SSE 事件流状态，支持断连后缓冲与重连。

    当客户端断开时（如页面刷新），事件被缓冲到 event_buffer；
    新客户端通过 /chat/subscribe 重连时，先重放缓冲事件，再接收实时事件。
    """

    __slots__ = (
        "event_buffer",
        "subscriber_queue",
        "_buffer_limit",
        "_overflow_warned",
    )

    def __init__(self, buffer_limit: int = 500) -> None:
        self.event_buffer: list[ToolCallEvent] = []
        self.subscriber_queue: asyncio.Queue[ToolCallEvent | None] | None = None
        self._buffer_limit = buffer_limit
        self._overflow_warned = False

    def deliver(self, event: ToolCallEvent) -> None:
        """投递事件：有订阅者时入队，否则缓冲。"""
        q = self.subscriber_queue
        if q is not None:
            q.put_nowait(event)
        else:
            if self._buffer_limit <= 0:
                if not self._overflow_warned:
                    logger.warning(
                        "SSE 事件缓冲区大小为 %d，断连期间事件将被丢弃",
                        self._buffer_limit,
                    )
                    self._overflow_warned = True
                return

            if len(self.event_buffer) < self._buffer_limit:
                self.event_buffer.append(event)
            else:
                if not self._overflow_warned:
                    logger.warning(
                        "SSE 事件缓冲区已满（%d），将覆盖最旧事件以保留最新事件",
                        self._buffer_limit,
                    )
                    self._overflow_warned = True
                # 保留最新事件，丢弃最旧事件。
                self.event_buffer.pop(0)
                self.event_buffer.append(event)

    def attach(self) -> asyncio.Queue[ToolCallEvent | None]:
        """创建新订阅者队列并附着。返回新队列。"""
        q: asyncio.Queue[ToolCallEvent | None] = asyncio.Queue()
        self.subscriber_queue = q
        self._overflow_warned = False
        return q

    def detach(self) -> None:
        """断开当前订阅者，后续事件进入缓冲。"""
        self.subscriber_queue = None

    def drain_buffer(self) -> list[ToolCallEvent]:
        """取出并清空缓冲区。"""
        buf = self.event_buffer
        self.event_buffer = []
        self._overflow_warned = False
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
    safe_mode: bool,
    public_path_fn: PublicPathFn = _default_public_path,
) -> str | None:
    """将 ToolCallEvent 转换为 SSE 文本。

    Args:
        event: 引擎事件。
        safe_mode: 是否启用对外安全模式（过滤内部事件）。
        public_path_fn: 路径脱敏回调，签名 (path, safe_mode) -> str。
    """
    if safe_mode and event.event_type in {
        EventType.THINKING,
        EventType.THINKING_DELTA,
        EventType.TOOL_CALL_START,
        EventType.TOOL_CALL_END,
        EventType.ITERATION_START,
        EventType.SUBAGENT_START,
        EventType.SUBAGENT_ITERATION,
        EventType.SUBAGENT_SUMMARY,
        EventType.SUBAGENT_END,
        EventType.SUBAGENT_TOOL_START,
        EventType.SUBAGENT_TOOL_END,
        EventType.PENDING_APPROVAL,
        EventType.APPROVAL_RESOLVED,
        EventType.RETRACT_THINKING,
    }:
        return None

    event_map = {
        EventType.THINKING: "thinking",
        EventType.TOOL_CALL_START: "tool_call_start",
        EventType.TOOL_CALL_END: "tool_call_end",
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
        EventType.FILES_CHANGED: "files_changed",
        EventType.PIPELINE_PROGRESS: "pipeline_progress",
        EventType.MEMORY_EXTRACTED: "memory_extracted",
        EventType.FILE_DOWNLOAD: "file_download",
        EventType.VERIFICATION_REPORT: "verification_report",
        EventType.RETRACT_THINKING: "retract_thinking",
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
    }
    sse_type = event_map.get(event.event_type, event.event_type.value)

    data: dict[str, Any]

    if event.event_type == EventType.THINKING:
        data = {
            "content": sanitize_external_text(event.thinking, max_len=2000),
            "iteration": event.iteration,
        }
    elif event.event_type == EventType.TOOL_CALL_START:
        data = {
            "tool_call_id": sanitize_external_text(event.tool_call_id, max_len=160),
            "tool_name": event.tool_name,
            "arguments": sanitize_external_data(
                event.arguments if isinstance(event.arguments, dict) else {},
                max_len=1000,
            ),
            "iteration": event.iteration,
        }
    elif event.event_type == EventType.TOOL_CALL_END:
        data = {
            "tool_call_id": sanitize_external_text(event.tool_call_id, max_len=160),
            "tool_name": event.tool_name,
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
    elif event.event_type == EventType.ITERATION_START:
        data = {"iteration": event.iteration}
    elif event.event_type == EventType.SUBAGENT_START:
        data = {
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
            "reason": sanitize_external_text(event.subagent_reason, max_len=500),
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
        }
    elif event.event_type in {EventType.TASK_LIST_CREATED, EventType.TASK_ITEM_UPDATED}:
        data = {
            "task_list": event.task_list_data,
            "task_index": event.task_index,
            "task_status": event.task_status,
        }
        sse_type = "task_update"
    elif event.event_type == EventType.THINKING_DELTA:
        data = {
            "content": event.thinking_delta,
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
            "file_path": public_path_fn(event.excel_file_path, safe_mode),
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
            "file_path": public_path_fn(event.excel_file_path, safe_mode),
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
            data["file_path_b"] = public_path_fn(event.excel_file_b, safe_mode) if event.excel_file_b else ""
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
    elif event.event_type == EventType.FILES_CHANGED:
        data = {
            "files": [
                public_path_fn(f, safe_mode)
                for f in (event.changed_files or [])[:50]
            ],
        }
    elif event.event_type == EventType.PIPELINE_PROGRESS:
        data = {
            "stage": sanitize_external_text(event.pipeline_stage, max_len=60),
            "message": sanitize_external_text(event.pipeline_message, max_len=200),
            "phase_index": event.pipeline_phase_index,
            "total_phases": event.pipeline_total_phases,
            "spec_path": event.pipeline_spec_path,
            "diff": event.pipeline_diff,
            "checkpoint": event.pipeline_checkpoint,
        }
        if event.tool_call_id:
            data["tool_call_id"] = sanitize_external_text(event.tool_call_id, max_len=160)
    elif event.event_type == EventType.MEMORY_EXTRACTED:
        data = {
            "entries": (event.memory_entries or [])[:50],
            "trigger": event.memory_trigger or "session_end",
            "count": len(event.memory_entries or []),
        }
    elif event.event_type == EventType.FILE_DOWNLOAD:
        data = {
            "tool_call_id": sanitize_external_text(event.tool_call_id, max_len=160),
            "file_path": public_path_fn(event.download_file_path, safe_mode),
            "filename": sanitize_external_text(event.download_filename, max_len=260),
            "description": sanitize_external_text(event.download_description, max_len=500),
        }
    elif event.event_type == EventType.VERIFICATION_REPORT:
        data = {
            "verdict": event.verification_verdict,
            "confidence": event.verification_confidence,
            "checks": event.verification_checks[:10],
            "issues": event.verification_issues[:10],
            "mode": event.verification_mode,
        }
    elif event.event_type == EventType.RETRACT_THINKING:
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

    return sse_format(sse_type, data)
