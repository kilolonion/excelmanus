"""Real Driver/dispatcher scheduling with controlled tools and fake model responses."""
from __future__ import annotations

import asyncio
import json
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from excelmanus.agent.session import AgentEngine
from excelmanus.config import ExcelManusConfig
from excelmanus.engine_core.tool_result import error_result, ok_result
from excelmanus.engine_types import ToolCallResult
from excelmanus.events import EventType as E
from excelmanus.tools.registry import ToolDef, ToolRegistry


def call(cid, label, dependencies=None, *, name="list_directory", envelope=False, field="depends_on"):
    args = {"label": label}
    if dependencies is not None and not envelope:
        args[field] = dependencies
    payload = {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
    if envelope and dependencies is not None:
        payload[field] = dependencies
    return payload


def response(calls=None):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="done" if not calls else "", tool_calls=calls))])


def engine(tmp_path, *, parallel=True, limit=2, read=None, business_dependency=False):
    config = ExcelManusConfig(api_key="test", base_url="https://test.invalid/v1", model="test-model",
        protocol="openai", workspace_root=str(tmp_path), main_model_vision="false", memory_enabled=False,
        parallel_readonly_tools=parallel, parallel_tool_max=limit, max_consecutive_failures=2)
    registry = ToolRegistry()
    seen = []
    def default_read(label, **kwargs):
        seen.append((label, kwargs))
        if label == "fail":
            return error_result("precondition failed", code="INVALID_ARGS")
        return ok_result({"directory": label})
    def write(label):
        seen.append((label, {}))
        (tmp_path / f"{label}.txt").write_text("actually written", encoding="utf-8")
        return ok_result({"file_path": f"{label}.txt"})
    properties = {"label": {"type": "string"}}
    if business_dependency:
        properties["depends_on"] = {"type": "string"}
    registry.register_tool(ToolDef(name="list_directory", description="read fixture", func=read or default_read,
        async_func=read if asyncio.iscoroutinefunction(read) else None,
        input_schema={"type": "object", "properties": properties, "required": ["label"], "additionalProperties": False}, write_effect="none"))
    registry.register_tool(ToolDef(name="save_report", description="write fixture", func=write,
        input_schema={"type": "object", "properties": {"label": {"type": "string"}}, "required": ["label"]}, write_effect="workspace_write"))
    result = AgentEngine(config, registry)
    result._full_access_enabled = True
    result.registry.configure_schema_validation(mode="enforce", canary_percent=100, strict_path=False)
    return result, seen


def model(engine, calls):
    engine._client.chat.completions.create = AsyncMock(side_effect=[response(calls), response()])


@pytest.mark.asyncio
@pytest.mark.parametrize("parallel", [False, True])
async def test_dependency_failures_skip_writes_but_independent_call_continues(tmp_path, parallel):
    e, seen = engine(tmp_path, parallel=parallel)
    model(e, [call("a", "fail"), call("b", "skipped", ["a"], name="save_report"),
              call("c", "downstream", ["b"], name="save_report"), call("d", "independent")])
    events = []
    result = await e.followup("execute batch", on_event=events.append)
    assert result.reply == "done"
    assert [label for label, _ in seen] == ["fail", "independent"]
    assert not (tmp_path / "skipped.txt").exists() and not (tmp_path / "downstream.txt").exists()
    assert [r.error for r in result.tool_calls[1:3]] == ["DEPENDENCY_FAILED"] * 2
    for cid in ("b", "c"):
        ends = [event for event in events if event.event_type == E.TOOL_CALL_END and event.tool_call_id == cid]
        assert len(ends) == 1 and not ends[0].success
        assert json.loads(ends[0].result)["executed"] is False
    wire = e._client.chat.completions.create.call_args.kwargs["messages"]
    results = [m for m in wire if m["role"] == "tool"]
    assert {m["tool_call_id"] for m in results} == {"a", "b", "c", "d"}


@pytest.mark.asyncio
@pytest.mark.parametrize("parallel", [False, True])
@pytest.mark.parametrize("envelope", [False, True])
async def test_forward_dependency_and_metadata_removed_before_business_validation(tmp_path, parallel, envelope):
    e, seen = engine(tmp_path, parallel=parallel)
    original = call("b", "second", ["a"], envelope=envelope)
    model(e, [original, call("a", "first"), call("c", "independent")])
    result = await e.followup("forward dependency")
    labels = [label for label, _ in seen]
    assert labels.index("first") < labels.index("second")
    assert all(result.success for result in result.tool_calls)
    assert all(kwargs == {} for _, kwargs in seen)
    assert ("depends_on" in json.loads(original["function"]["arguments"])) is (not envelope)
    wire_calls = [tc for msg in e._client.chat.completions.create.call_args.kwargs["messages"]
                  for tc in msg.get("tool_calls", [])]
    assert all("depends_on" not in tc for tc in wire_calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid,code", [
    ("missing", "INVALID_DEPENDENCY"), ([1], "INVALID_DEPENDENCY"),
    (["a"], "DEPENDENCY_CYCLE"), ("", "INVALID_DEPENDENCY"),
])
async def test_invalid_dependencies_never_execute(tmp_path, invalid, code):
    e, seen = engine(tmp_path)
    model(e, [call("a", "blocked", invalid), call("b", "independent")])
    result = await e.followup("validate dependencies")
    assert [label for label, _ in seen] == ["independent"]
    assert next(r for r in result.tool_calls if r.arguments["label"] == "blocked").error == code


@pytest.mark.asyncio
async def test_cycles_are_not_replayed_in_original_order(tmp_path):
    e, seen = engine(tmp_path)
    model(e, [call("a", "one", ["b"]), call("b", "two", ["a"]),
              call("c", "downstream", ["a"]), call("d", "independent")])
    result = await e.followup("cycle")
    assert [label for label, _ in seen] == ["independent"]
    errors = {r.arguments["label"]: r.error for r in result.tool_calls}
    assert errors["one"] == errors["two"] == "DEPENDENCY_CYCLE"
    assert errors["downstream"] == "DEPENDENCY_FAILED"


@pytest.mark.asyncio
async def test_success_dependency_does_not_treat_ordering_barrier_as_requirement(tmp_path):
    e, seen = engine(tmp_path)
    model(e, [call("a", "fail"), call("b", "independent-write", name="save_report")])
    result = await e.followup("separate operations")
    assert result.tool_calls[-1].success
    assert (tmp_path / "independent-write.txt").exists()


@pytest.mark.asyncio
async def test_business_field_named_depends_on_is_preserved(tmp_path):
    e, seen = engine(tmp_path, business_dependency=True)
    model(e, [call("a", "business", "domain-entity")])
    result = await e.followup("business parameter")
    assert result.tool_calls[0].success
    assert seen == [("business", {"depends_on": "domain-entity"})]


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [1, 3])
async def test_worker_count_is_bounded_and_queued_calls_are_not_running(tmp_path, limit):
    entered, release = asyncio.Event(), asyncio.Event()
    active, peak = 0, 0
    async def read(label):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        if active == limit:
            entered.set()
        try:
            await release.wait()
            return ok_result({"directory": label})
        finally:
            active -= 1
    e, _ = engine(tmp_path, limit=limit, read=read)
    model(e, [call(str(i), str(i)) for i in range(9)])
    events = []
    job = asyncio.create_task(e.followup("bounded batch", on_event=events.append))
    await asyncio.wait_for(entered.wait(), 3)
    assert len([event for event in events if event.event_type == E.TOOL_CALL_START]) == limit
    assert active == peak == limit
    release.set()
    result = await job
    assert len(result.tool_calls) == 9 and all(r.success for r in result.tool_calls)
    assert [r.arguments["label"] for r in result.tool_calls] == [str(i) for i in range(9)]
    assert peak == limit


@pytest.mark.asyncio
async def test_cancellation_does_not_start_queued_calls_and_waits_for_sync_work(tmp_path):
    entered, release = threading.Event(), threading.Event()
    seen = []
    def read(label):
        seen.append(label)
        entered.set()
        release.wait(timeout=5)
        return ok_result({"directory": label})
    e, _ = engine(tmp_path, limit=1, read=read)
    model(e, [call(str(i), str(i)) for i in range(4)])
    job = asyncio.create_task(e.followup("stop while waiting"))
    assert await asyncio.to_thread(entered.wait, 2)
    stop = asyncio.create_task(e._driver.stop())
    await asyncio.sleep(0.01)
    assert not stop.done()
    release.set()
    await stop
    result = await job
    assert result.truncated
    assert seen == ["0"]


@pytest.mark.asyncio
async def test_denied_approval_blocks_dependent_work(tmp_path):
    e, seen = engine(tmp_path, parallel=False)
    pending = e._approval.create_pending(tool_name="save_report", arguments={"label": "needs-approval"})
    real = e._execute_tool_call
    async def execute(tc, *a, **kw):
        if tc.id == "a":
            return ToolCallResult(tool_name="save_report", arguments={"label": "needs-approval"},
                result="pending", success=True, pending_approval=True, approval_id=pending.approval_id)
        return await real(tc, *a, **kw)
    e._execute_tool_call = execute
    model(e, [call("a", "needs-approval", name="save_report"), call("b", "blocked", ["a"], name="save_report"), call("c", "independent")])
    result = await e.followup("approval chain", approval_resolver=AsyncMock(return_value="reject"))
    assert result.tool_calls[1].error == "DEPENDENCY_FAILED"
    assert seen == [("independent", {})]


def test_duplicate_ids_and_changed_write_declarations_fail_closed(tmp_path):
    e, _ = engine(tmp_path)
    from excelmanus.engine_utils import _normalize_tool_calls
    plan = e._tool_runtime.plan_execution(_normalize_tool_calls([call("same", "one"), call("same", "two")]))
    assert all(plan.blocked_result(tc, {}).error == "INVALID_DEPENDENCY" for batch in plan.batches for tc in batch.tool_calls)
    tool = e.registry.get_tool("list_directory")
    tool.write_effect = "workspace_write"
    assert not e._tool_runtime.is_concurrency_safe("list_directory", {"label": "one"})


@pytest.mark.asyncio
async def test_write_order_conflict_blocks_cycle_and_leaves_independent_calls(tmp_path):
    e, seen = engine(tmp_path)
    # The first write depends on a later read, which cannot cross the write barrier.
    model(e, [call("a", "blocked-write", ["b"], name="save_report"),
              call("b", "late-read"), call("c", "independent")])
    result = await e.followup("invalid write order")
    assert not (tmp_path / "blocked-write.txt").exists()
    assert [label for label, _ in seen] == ["independent"]
    assert result.reply == "done"


@pytest.mark.asyncio
async def test_parallel_exception_is_paired_and_blocks_dependency(tmp_path):
    e, seen = engine(tmp_path)
    real = e._execute_tool_call
    async def execute(tc, *args, **kwargs):
        if tc.id == "a":
            raise RuntimeError("executor failure")
        return await real(tc, *args, **kwargs)
    e._execute_tool_call = execute
    model(e, [call("a", "exception"), call("b", "independent"), call("c", "blocked", ["a"], name="save_report")])
    events = []
    result = await e.followup("exception", on_event=events.append)
    assert result.tool_calls[-1].error == "DEPENDENCY_FAILED"
    assert [label for label, _ in seen] == ["independent"]
    assert len([ev for ev in events if ev.event_type == E.TOOL_CALL_END and ev.tool_call_id == "a"]) == 1


@pytest.mark.asyncio
async def test_queue_rechecks_tool_effect_after_first_call(tmp_path):
    seen = []
    async def read(label):
        seen.append(label)
        e.registry.get_tool("list_directory").write_effect = "workspace_write"
        return ok_result({"directory": label})
    e, _ = engine(tmp_path, limit=1, read=read)
    model(e, [call("a", "first"), call("b", "must-not-run")])
    result = await e.followup("changed declaration")
    assert seen == ["first"]
    assert result.tool_calls[1].error == "TOOL_NOT_ALLOWED"
    assert result.tool_calls[1].structured.value["executed"] is False


@pytest.mark.asyncio
async def test_concurrency_setting_roundtrips_through_runtime_api(tmp_path, monkeypatch):
    from pydantic import ValidationError
    import excelmanus.api_routes_config as routes

    e, _ = engine(tmp_path)
    saved = {}
    monkeypatch.setattr(routes, "get_config", lambda: e.config)
    monkeypatch.setattr(routes, "get_session_manager", lambda: None)
    monkeypatch.setattr(routes, "_persist_settings", lambda updates: saved.update(updates))
    response = await routes.update_runtime_config(routes.RuntimeConfigUpdate(parallel_tool_max=3), None)
    assert response.status_code == 200
    assert saved["EXCELMANUS_PARALLEL_TOOL_MAX"] == "3"
    payload = json.loads((await routes.get_runtime_config(None)).body)
    assert payload["parallel_tool_max"] == 3
    saved.clear()
    empty = await routes.update_runtime_config(routes.RuntimeConfigUpdate(), None)
    assert empty.status_code == 400
    assert saved == {}
    for value in (0, 33):
        with pytest.raises(ValidationError):
            routes.RuntimeConfigUpdate(parallel_tool_max=value)
