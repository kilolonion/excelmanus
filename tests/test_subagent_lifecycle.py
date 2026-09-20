"""子代理生命周期：start/end 成对，发布前失败不发 start。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from excelmanus.events import EventType, ToolCallEvent
from excelmanus.subagent.driver import InProcessDriver
from excelmanus.subagent.errors import SubagentError
from excelmanus.subagent.lifecycle import emit_end, emit_start, validate_pair
from excelmanus.subagent.models import SubagentDescriptor, SubagentResult, SubagentStartRequest
from excelmanus.subagent.runtime import SubagentRuntime


def test_validate_pair_requires_same_run_id() -> None:
    descriptor = SubagentDescriptor(run_id="run-1", agent_name="explorer")
    result = SubagentResult(
        stop_reason="completed",
        output="ok",
        subagent_name="explorer",
        permission_mode="readOnly",
        conversation_id="run-1",
    )
    validate_pair(descriptor, result)
    with pytest.raises(SubagentError) as exc:
        validate_pair(
            descriptor,
            SubagentResult(
                stop_reason="completed",
                output="ok",
                subagent_name="explorer",
                permission_mode="readOnly",
                conversation_id="other",
            ),
        )
    assert exc.value.code == "LIFECYCLE_MISMATCH"


def test_emit_start_end_share_run_id() -> None:
    events: list[ToolCallEvent] = []
    descriptor = SubagentDescriptor(run_id="run-9", agent_name="explorer")
    emit_start(events.append, descriptor, reason="探查", permission_mode="readOnly")
    emit_end(
        events.append,
        descriptor,
        SubagentResult(
            stop_reason="aborted",
            output="超时",
            diagnostic="超时",
            subagent_name="explorer",
            permission_mode="readOnly",
            conversation_id="run-9",
        ),
    )
    assert events[0].event_type == EventType.SUBAGENT_START
    end = next(event for event in events if event.event_type == EventType.SUBAGENT_END)
    assert events[0].subagent_conversation_id == end.subagent_conversation_id == "run-9"
    assert end.subagent_reason == "aborted"
    assert end.error == "超时"


def test_listener_error_does_not_starve_end() -> None:
    seen: list[EventType] = []

    def _boom(event: ToolCallEvent) -> None:
        seen.append(event.event_type)
        if event.event_type == EventType.SUBAGENT_START:
            raise RuntimeError("observer failed")

    descriptor = SubagentDescriptor(run_id="run-2", agent_name="subagent")
    emit_start(_boom, descriptor, reason="x", permission_mode="default")
    emit_end(
        _boom,
        descriptor,
        SubagentResult(
            stop_reason="completed",
            output="ok",
            subagent_name="subagent",
            permission_mode="default",
            conversation_id="run-2",
        ),
    )
    assert EventType.SUBAGENT_START in seen
    assert EventType.SUBAGENT_END in seen


@pytest.mark.parametrize("mode", ["one-shot", "background"])
def test_start_sse_identifies_independent_background_execution(mode) -> None:
    import json
    from excelmanus.api_sse import sse_event_to_sse

    events = []
    emit_start(events.append, SubagentDescriptor(run_id="run-ui", agent_name="explorer", mode=mode),
               reason="统计", permission_mode="readOnly")
    serialized = sse_event_to_sse(events[0])
    payload = json.loads(next(line[6:] for line in serialized.splitlines() if line.startswith("data: ")))
    assert payload["conversation_id"] == "run-ui"
    assert payload["background"] is (mode == "background")


@pytest.mark.asyncio
async def test_not_found_emits_no_start(monkeypatch) -> None:
    events: list[ToolCallEvent] = []
    parent = SimpleNamespace(
        _subagent_enabled=True,
        _active_skills=[],
        _delegation_depth=0,
        _session_id="s",
        _skill_resolver=SimpleNamespace(
            normalize_skill_agent_name=lambda x: x or "subagent",
            run_skill_hook=lambda **_k: None,
            resolve_hook_result=AsyncMock(return_value=None),
        ),
        _subagent_registry=SimpleNamespace(get=lambda _name: None),
        _config=SimpleNamespace(subagent_timeout_seconds=0),
    )
    composed = {"n": 0}

    def _compose(self, parent, config):
        composed["n"] += 1
        return SimpleNamespace(_delegation_depth=1)

    monkeypatch.setattr(InProcessDriver, "compose", _compose)
    runtime = SubagentRuntime(parent)
    with pytest.raises(SubagentError) as exc:
        await runtime.start(SubagentStartRequest(task="x", agent_name="ghost", on_event=events.append))
    assert exc.value.code == "NOT_FOUND"
    assert events == []
    assert composed["n"] == 0
