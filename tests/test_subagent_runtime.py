"""SubagentRuntime：发布边界、超时、并行 mutation 拒绝。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from excelmanus.events import EventType, ToolCallEvent
from excelmanus.subagent.driver import InProcessDriver
from excelmanus.subagent.errors import SubagentError
from excelmanus.subagent.models import (
    SubagentConfig,
    SubagentResult,
    SubagentRun,
    SubagentStartRequest,
)
from excelmanus.subagent.parallel import ParallelTask
from excelmanus.subagent.runtime import SubagentRuntime


def _registry_get(name: str) -> SubagentConfig | None:
    if name == "explorer":
        return SubagentConfig(name="explorer", description="x", permission_mode="readOnly")
    if name == "subagent":
        return SubagentConfig(name="subagent", description="x", permission_mode="acceptEdits")
    return None


def _make_parent(*, enabled: bool = True) -> SimpleNamespace:
    resolver = SimpleNamespace(
        normalize_skill_agent_name=lambda x: (x or "").strip() or "subagent",
        run_skill_hook=lambda **_k: None,
        resolve_hook_result=AsyncMock(return_value=None),
    )
    return SimpleNamespace(
        _subagent_enabled=enabled,
        _active_skills=[],
        _delegation_depth=0,
        _session_id="sess-1",
        _skill_resolver=resolver,
        _subagent_registry=SimpleNamespace(get=_registry_get, list_all=lambda: [
            _registry_get("subagent"),
            _registry_get("explorer"),
        ]),
        _config=SimpleNamespace(subagent_timeout_seconds=0, parallel_subagent_max=3),
    )


async def _completed_run(
    self,
    parent,
    config,
    *,
    prompt,
    descriptor,
    on_event=None,
    timeout=None,
    child=None,
):
    return SubagentResult(
        stop_reason="completed",
        output="ok",
        subagent_name=config.name,
        permission_mode=config.permission_mode,
        conversation_id=descriptor.run_id,
    )


@pytest.mark.asyncio
async def test_disabled_raises_before_start(monkeypatch) -> None:
    events: list[ToolCallEvent] = []
    parent = _make_parent(enabled=False)
    monkeypatch.setattr(InProcessDriver, "compose", lambda self, p, c: SimpleNamespace(_delegation_depth=1))
    runtime = SubagentRuntime(parent)
    with pytest.raises(SubagentError) as exc:
        await runtime.start(SubagentStartRequest(task="hello", on_event=events.append))
    assert exc.value.code == "DISABLED"
    assert events == []


@pytest.mark.asyncio
async def test_empty_task_raises_before_start() -> None:
    events: list[ToolCallEvent] = []
    runtime = SubagentRuntime(_make_parent())
    with pytest.raises(SubagentError) as exc:
        await runtime.start(SubagentStartRequest(task="   ", on_event=events.append))
    assert exc.value.code == "EMPTY_TASK"
    assert events == []


@pytest.mark.asyncio
async def test_start_emits_paired_lifecycle(monkeypatch) -> None:
    events: list[ToolCallEvent] = []
    monkeypatch.setattr(InProcessDriver, "compose", lambda self, p, c: SimpleNamespace(_delegation_depth=1))
    monkeypatch.setattr(InProcessDriver, "run", _completed_run)
    runtime = SubagentRuntime(_make_parent())
    run = await runtime.start(SubagentStartRequest(task="hello", on_event=events.append))
    result = await run.result
    assert result.stop_reason == "completed"
    kinds = [event.event_type for event in events]
    assert EventType.SUBAGENT_START in kinds
    assert EventType.SUBAGENT_END in kinds
    start = next(event for event in events if event.event_type == EventType.SUBAGENT_START)
    end = next(event for event in events if event.event_type == EventType.SUBAGENT_END)
    assert start.subagent_conversation_id == end.subagent_conversation_id == run.id


@pytest.mark.asyncio
async def test_post_hook_denial_is_the_published_result(monkeypatch) -> None:
    from excelmanus.hooks import HookDecision

    events: list[ToolCallEvent] = []
    parent = _make_parent()
    parent._active_skills = [SimpleNamespace(name="hook-skill")]
    resolver = parent._skill_resolver
    resolver.run_skill_hook = lambda **_k: SimpleNamespace()
    resolver.resolve_hook_result = AsyncMock(
        side_effect=[
            None,
            SimpleNamespace(decision=HookDecision.DENY, reason="blocked by hook"),
        ]
    )
    monkeypatch.setattr(InProcessDriver, "compose", lambda self, p, c: SimpleNamespace(_delegation_depth=1))
    monkeypatch.setattr(InProcessDriver, "run", _completed_run)

    runtime = SubagentRuntime(parent)
    run = await runtime.start(SubagentStartRequest(task="hello", on_event=events.append))
    result = await run.result
    assert result.stop_reason == "refusal"
    end = next(event for event in events if event.event_type == EventType.SUBAGENT_END)
    assert end.subagent_success is False
    assert end.subagent_reason == "refusal"


@pytest.mark.asyncio
async def test_timeout_maps_aborted(monkeypatch) -> None:
    async def _aborted_run(self, parent, config, **kwargs):
        return SubagentResult(
            stop_reason="aborted",
            output="子代理 explorer 执行超时，已终止。",
            diagnostic="子代理 explorer 执行超时，已终止。",
            subagent_name=config.name,
            permission_mode=config.permission_mode,
            conversation_id=kwargs["descriptor"].run_id,
        )

    monkeypatch.setattr(InProcessDriver, "compose", lambda self, p, c: SimpleNamespace(_delegation_depth=1))
    monkeypatch.setattr(InProcessDriver, "run", _aborted_run)
    runtime = SubagentRuntime(_make_parent())
    run = await runtime.start(SubagentStartRequest(task="slow", agent_name="explorer"))
    result = await run.result
    assert result.stop_reason == "aborted"


@pytest.mark.asyncio
async def test_parallel_mutation_rejected_before_publish() -> None:
    events: list[ToolCallEvent] = []
    runtime = SubagentRuntime(_make_parent())
    with pytest.raises(SubagentError) as exc:
        await runtime.start_parallel(
            [
                ParallelTask(task="写 A", agent_name="subagent"),
                ParallelTask(task="写 B", agent_name="subagent"),
            ],
            on_event=events.append,
        )
    assert exc.value.code == "PARALLEL_CONFLICT"
    assert events == []


@pytest.mark.asyncio
async def test_parallel_write_plus_readonly_rejected() -> None:
    events: list[ToolCallEvent] = []
    runtime = SubagentRuntime(_make_parent())
    with pytest.raises(SubagentError) as exc:
        await runtime.start_parallel(
            [
                ParallelTask(task="写 A", agent_name="subagent", file_paths=["a.xlsx"]),
                ParallelTask(task="读 B", agent_name="explorer", file_paths=["b.xlsx"]),
            ],
            on_event=events.append,
        )
    assert exc.value.code == "PARALLEL_CONFLICT"
    assert events == []


@pytest.mark.asyncio
async def test_parallel_explorers_same_file_allowed() -> None:
    runtime = SubagentRuntime(_make_parent())
    calls: list[str] = []

    async def _fake_start(request: SubagentStartRequest) -> SubagentRun:
        calls.append(request.task)
        run = SubagentRun(f"run-{len(calls)}")
        run.set_result(
            SubagentResult(
                stop_reason="completed",
                output="ok",
                subagent_name="explorer",
                permission_mode="readOnly",
                conversation_id=run.id,
            )
        )
        return run

    runtime.start = _fake_start  # type: ignore[method-assign]
    outcome = await runtime.start_parallel(
        [
            ParallelTask(task="读表头", agent_name="explorer", file_paths=["sales.xlsx"]),
            ParallelTask(task="读合计", agent_name="explorer", file_paths=["sales.xlsx"]),
        ]
    )
    assert outcome.success is True
    assert len(outcome.results) == 2
    assert calls == ["读表头", "读合计"]


@pytest.mark.asyncio
async def test_unknown_background_run_reports_not_found() -> None:
    runtime = SubagentRuntime(_make_parent())
    with pytest.raises(SubagentError) as exc:
        await runtime.send_message("run-1", "hi")
    assert exc.value.code == "NOT_FOUND"


def test_list_catalog() -> None:
    runtime = SubagentRuntime(_make_parent())
    text = runtime.list_catalog()
    assert "explorer" in text
    assert "subagent" in text
