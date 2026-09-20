"""子代理生命周期：start/end 成对，身份必须一致。"""

from __future__ import annotations

from typing import Any

from excelmanus.events import EventCallback, EventType, ToolCallEvent
from excelmanus.logger import get_logger
from excelmanus.subagent.errors import SubagentError
from excelmanus.subagent.models import SubagentDescriptor, SubagentResult
from excelmanus.subagent.result import bound_diagnostic

logger = get_logger("subagent.lifecycle")


def _safe_emit(on_event: EventCallback | None, event: ToolCallEvent) -> None:
    if on_event is None:
        return
    try:
        on_event(event)
    except Exception:
        logger.warning("子代理生命周期监听器异常: %s", event.event_type, exc_info=True)


def emit_start(
    on_event: EventCallback | None,
    descriptor: SubagentDescriptor,
    *,
    reason: str,
    permission_mode: str,
) -> None:
    """发布 start。调用方必须先挂好投影观察器。"""
    _safe_emit(
        on_event,
        ToolCallEvent(
            event_type=EventType.SUBAGENT_START,
            subagent_name=descriptor.agent_name,
            subagent_reason=reason,
            subagent_permission_mode=permission_mode,
            subagent_conversation_id=descriptor.run_id,
            subagent_background=descriptor.mode == "background",
        ),
    )


def emit_end(
    on_event: EventCallback | None,
    descriptor: SubagentDescriptor,
    result: SubagentResult,
) -> None:
    """发布 end。run_id 必须与 start 一致。"""
    validate_pair(descriptor, result)
    summary = bound_diagnostic(result.output)
    _safe_emit(
        on_event,
        ToolCallEvent(
            event_type=EventType.SUBAGENT_END,
            subagent_name=descriptor.agent_name,
            subagent_success=result.success,
            subagent_conversation_id=descriptor.run_id,
            subagent_iterations=result.iterations,
            subagent_tool_calls=result.tool_calls_count,
            subagent_summary=summary,
            subagent_reason=result.stop_reason,
            error=result.diagnostic,
        ),
    )
    if result.output:
        _safe_emit(
            on_event,
            ToolCallEvent(
                event_type=EventType.SUBAGENT_SUMMARY,
                subagent_name=descriptor.agent_name,
                subagent_summary=summary,
                subagent_conversation_id=descriptor.run_id,
                subagent_iterations=result.iterations,
                subagent_tool_calls=result.tool_calls_count,
                subagent_success=result.success,
            ),
        )


def validate_pair(descriptor: SubagentDescriptor, result: SubagentResult) -> None:
    """start/end 身份必须一致。"""
    if result.conversation_id != descriptor.run_id:
        raise SubagentError(
            "LIFECYCLE_MISMATCH",
            f"subagent/end identity diverges from start: {result.conversation_id!r} != {descriptor.run_id!r}",
        )
    if result.subagent_name != descriptor.agent_name:
        raise SubagentError(
            "LIFECYCLE_MISMATCH",
            f"subagent/end name diverges from start: {result.subagent_name!r} != {descriptor.agent_name!r}",
        )


def parent_session_id(parent: Any) -> str:
    return str(getattr(parent, "_session_id", None) or "")
