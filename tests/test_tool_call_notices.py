"""SSE 工具/思考/子代理/审批事件一律下发，payload 仍脱敏。"""

from __future__ import annotations

import json

from excelmanus.api_sse import sse_event_to_sse
from excelmanus.events import EventType, ToolCallEvent


class TestSseEventsAlwaysEmitted:
    """思考、工具、子代理、审批事件一律进入 SSE；payload 仍脱敏。"""

    def test_tool_call_start_emitted_and_sanitized(self):
        event = ToolCallEvent(
            event_type=EventType.TOOL_CALL_START,
            tool_call_id="tc1",
            tool_name="read_excel",
            arguments={"path": "/Users/demo/private.xlsx"},
            iteration=1,
        )
        result = sse_event_to_sse(event)
        assert result is not None
        assert "tool_call_start" in result
        assert "read_excel" in result
        assert "/Users/demo/private.xlsx" not in result

    def test_tool_call_end_emitted(self):
        event = ToolCallEvent(
            event_type=EventType.TOOL_CALL_END,
            tool_call_id="tc1",
            tool_name="read_excel",
            success=True,
            result="OK",
            iteration=1,
        )
        result = sse_event_to_sse(event)
        assert result is not None
        assert "tool_call_end" in result

    def test_subagent_start_end_emitted(self):
        start = sse_event_to_sse(ToolCallEvent(
            event_type=EventType.SUBAGENT_START,
            subagent_name="data_analysis",
            subagent_reason="分析数据",
        ))
        end = sse_event_to_sse(ToolCallEvent(
            event_type=EventType.SUBAGENT_END,
            subagent_name="data_analysis",
            subagent_success=True,
        ))
        assert start is not None and "subagent_start" in start
        assert end is not None and "subagent_end" in end

    def test_subagent_tool_and_approval_events_emitted(self):
        for et in (
            EventType.SUBAGENT_TOOL_START,
            EventType.SUBAGENT_TOOL_END,
            EventType.PENDING_APPROVAL,
            EventType.APPROVAL_RESOLVED,
        ):
            event = ToolCallEvent(
                event_type=et,
                tool_name="write_excel",
                subagent_conversation_id="conv1",
                approval_id="ap1",
                approval_tool_name="delete_file",
                tool_call_id="tc1",
            )
            assert sse_event_to_sse(event) is not None

    def test_thinking_and_loop_events_emitted(self):
        thinking = sse_event_to_sse(ToolCallEvent(
            event_type=EventType.THINKING,
            thinking="internal thought /Users/demo/secret.xlsx",
            iteration=1,
        ))
        assert thinking is not None
        assert "thinking" in thinking
        assert "/Users/demo/secret.xlsx" not in thinking

        delta = sse_event_to_sse(ToolCallEvent(
            event_type=EventType.THINKING_DELTA,
            thinking_delta="partial thought",
            iteration=1,
        ))
        assert delta is not None
        assert "thinking_delta" in delta

        iteration = sse_event_to_sse(ToolCallEvent(
            event_type=EventType.ITERATION_START,
            iteration=1,
        ))
        assert iteration is not None
        assert "iteration_start" in iteration

    def test_thinking_delta_preserves_chunk_boundary_whitespace(self):
        chunks = ["Let me ", " ", "read the ", "file.\n"]
        assembled: list[str] = []
        for chunk in chunks:
            sse = sse_event_to_sse(ToolCallEvent(
                event_type=EventType.THINKING_DELTA,
                thinking_delta=chunk,
                iteration=1,
            ))
            assert sse is not None
            data_line = next(line for line in sse.splitlines() if line.startswith("data:"))
            payload = json.loads(data_line[5:])
            assembled.append(payload["content"])
        assert "".join(assembled) == "".join(chunks)

    def test_thinking_delta_keeps_space_only_chunk_and_still_masks_paths(self):
        space_sse = sse_event_to_sse(ToolCallEvent(
            event_type=EventType.THINKING_DELTA,
            thinking_delta=" ",
        ))
        assert space_sse is not None
        space_payload = json.loads(
            next(line for line in space_sse.splitlines() if line.startswith("data:"))[5:]
        )
        assert space_payload["content"] == " "

        path_sse = sse_event_to_sse(ToolCallEvent(
            event_type=EventType.THINKING_DELTA,
            thinking_delta=" see /Users/demo/secret.xlsx ",
        ))
        assert path_sse is not None
        assert "/Users/demo/secret.xlsx" not in path_sse
        path_payload = json.loads(
            next(line for line in path_sse.splitlines() if line.startswith("data:"))[5:]
        )
        assert path_payload["content"].startswith(" ")
        assert path_payload["content"].endswith(" ")
