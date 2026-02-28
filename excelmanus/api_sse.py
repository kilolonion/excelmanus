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

    __slots__ = ("event_buffer", "subscriber_queue", "_buffer_limit")

    def __init__(self, buffer_limit: int = 500) -> None:
        self.event_buffer: list[ToolCallEvent] = []
        self.subscriber_queue: asyncio.Queue[ToolCallEvent | None] | None = None
        self._buffer_limit = buffer_limit

    def deliver(self, event: ToolCallEvent) -> None:
        """投递事件：有订阅者时入队，否则缓冲。"""
        q = self.subscriber_queue
        if q is not None:
            q.put_nowait(event)
        else:
            if len(self.event_buffer) < self._buffer_limit:
                self.event_buffer.append(event)

    def attach(self) -> asyncio.Queue[ToolCallEvent | None]:
        """创建新订阅者队列并附着。返回新队列。"""
        q: asyncio.Queue[ToolCallEvent | None] = asyncio.Queue()
        self.subscriber_queue = q
        return q

    def detach(self) -> None:
        """断开当前订阅者，后续事件进入缓冲。"""
        self.subscriber_queue = None

    def drain_buffer(self) -> list[ToolCallEvent]:
        """取出并清空缓冲区。"""
        buf = self.event_buffer
        self.event_buffer = []
        return buf


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
    else:
        data = event.to_dict()

    return sse_format(sse_type, data)
