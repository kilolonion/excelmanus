"""子循环事件投影为父作用域 SUBAGENT_*。"""

from __future__ import annotations

from excelmanus.events import EventType, ToolCallEvent
from excelmanus.subagent.models import SubagentDescriptor
from excelmanus.subagent.projection import project_child_event, wrap_on_event


def test_tool_call_start_projects_with_conversation_id() -> None:
    descriptor = SubagentDescriptor(run_id="child-7", agent_name="explorer")
    projected = project_child_event(
        ToolCallEvent(
            event_type=EventType.TOOL_CALL_START,
            tool_name="observe_spreadsheet",
            iteration=2,
        ),
        descriptor=descriptor,
        tool_index=1,
    )
    assert projected is not None
    assert projected.event_type == EventType.SUBAGENT_TOOL_START
    assert projected.subagent_conversation_id == "child-7"
    assert projected.subagent_name == "explorer"
    assert projected.tool_name == "observe_spreadsheet"


def test_wrap_on_event_remaps_and_drops_parent_surface() -> None:
    descriptor = SubagentDescriptor(run_id="child-8", agent_name="explorer")
    events: list[ToolCallEvent] = []
    wrapped = wrap_on_event(events.append, descriptor)
    wrapped(ToolCallEvent(event_type=EventType.TOOL_CALL_START, tool_name="observe_spreadsheet"))
    wrapped(ToolCallEvent(event_type=EventType.ITERATION_START, iteration=1))
    wrapped(ToolCallEvent(event_type=EventType.TOOL_CALL_END, tool_name="observe_spreadsheet", success=True))
    wrapped(ToolCallEvent(event_type=EventType.TURN_START))
    kinds = [event.event_type for event in events]
    assert kinds == [
        EventType.SUBAGENT_TOOL_START,
        EventType.SUBAGENT_ITERATION,
        EventType.SUBAGENT_TOOL_END,
    ]
    assert all(event.subagent_conversation_id == "child-8" for event in events)
