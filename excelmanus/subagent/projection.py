"""把子循环事件投影为父作用域的 SUBAGENT_*，避免子工具变成并列 tool_call 卡。"""

from __future__ import annotations

from dataclasses import replace

from excelmanus.events import EventCallback, EventType, ToolCallEvent
from excelmanus.logger import get_logger
from excelmanus.subagent.models import SubagentDescriptor

logger = get_logger("subagent.projection")

_REMAP = {
    EventType.TOOL_CALL_START: EventType.SUBAGENT_TOOL_START,
    EventType.TOOL_CALL_END: EventType.SUBAGENT_TOOL_END,
    EventType.ITERATION_START: EventType.SUBAGENT_ITERATION,
}


def project_child_event(
    event: ToolCallEvent,
    *,
    descriptor: SubagentDescriptor,
    tool_index: int,
) -> ToolCallEvent | None:
    """子事件 → 父作用域投影。无法投影的父面事件丢弃。"""
    mapped = _REMAP.get(event.event_type)
    if mapped is None:
        return None
    return replace(
        event,
        event_type=mapped,
        subagent_name=descriptor.agent_name,
        subagent_conversation_id=descriptor.run_id,
        subagent_tool_index=tool_index,
        subagent_iterations=event.iteration,
        subagent_tool_calls=tool_index,
    )


def wrap_on_event(
    on_event: EventCallback | None,
    descriptor: SubagentDescriptor,
) -> EventCallback:
    """包装子引擎 on_event：只向父流投递投影后的 SUBAGENT_*。"""
    state = {"index": 0}

    def _wrapped(event: ToolCallEvent) -> None:
        if event.event_type == EventType.TOOL_CALL_START:
            state["index"] += 1
        projected = project_child_event(
            event,
            descriptor=descriptor,
            tool_index=state["index"],
        )
        if projected is None or on_event is None:
            return
        try:
            on_event(projected)
        except Exception:
            logger.warning("子代理事件投影监听器异常", exc_info=True)

    return _wrapped
