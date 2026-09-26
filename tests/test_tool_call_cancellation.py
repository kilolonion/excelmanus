"""Per-execution cancellation through the real Driver, dispatcher and SDK."""
from __future__ import annotations

import asyncio
import json
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from excelmanus.engine_core.tool_result import ok_result
from excelmanus.events import EventType as E
from tests.test_tool_scheduling import engine, call, model, response
from tests.test_output_contract_closure import make_engine, register


def row_for(e, call_id):
    return next(row for row in reversed(e._tool_runtime.call_states()) if row["tool_call_id"] == call_id)


def raw_call(cid, name, args):
    return {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def assert_paired(events, call_id, *, success=False):
    starts = [ev for ev in events if ev.event_type == E.TOOL_CALL_START and ev.tool_call_id == call_id]
    ends = [ev for ev in events if ev.event_type == E.TOOL_CALL_END and ev.tool_call_id == call_id]
    assert len(starts) == len(ends) == 1
    assert starts[0].execution_id == ends[0].execution_id
    assert ends[0].success is success


@pytest.mark.asyncio
async def test_cancel_queued_call_blocks_dependents_and_keeps_independent_work(tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()
    seen = []
    async def read(label):
        seen.append(label)
        if label == "hold":
            entered.set()
            await release.wait()
        return ok_result({"directory": label})
    e, _ = engine(tmp_path, limit=1, read=read)
    model(e, [call("a", "hold"), call("b", "cancel-me"), call("c", "blocked", ["b"], name="save_report"), call("d", "independent")])
    events = []
    job = asyncio.create_task(e.followup("execute", on_event=events.append))
    await asyncio.wait_for(entered.wait(), 3)
    row = row_for(e, "b")
    assert row["status"] == "queued"
    cancelled = e._tool_runtime.cancel_call(row["execution_id"])
    assert cancelled["status"] == "cancelled"
    assert e._tool_runtime.cancel_call(row["execution_id"]) == cancelled
    release.set()
    result = await asyncio.wait_for(job, 3)
    assert result.reply == "done" and not result.truncated
    assert seen == ["hold", "independent"]
    assert [r.error for r in result.tool_calls[1:3]] == ["CANCELLED", "DEPENDENCY_FAILED"]
    assert not (tmp_path / "blocked.txt").exists()
    assert_paired(events, "b")
    assert not e._tool_dispatcher.is_cancelled()


@pytest.mark.asyncio
async def test_cancel_active_async_call_does_not_cancel_its_parallel_sibling(tmp_path):
    entered = asyncio.Event()
    seen = []
    async def read(label):
        seen.append(label)
        if label == "hold":
            entered.set()
            await asyncio.Event().wait()
        return ok_result({"directory": label})
    e, _ = engine(tmp_path, read=read)
    model(e, [call("a", "hold"), call("b", "independent"), call("c", "blocked", ["a"], name="save_report")])
    events = []
    job = asyncio.create_task(e.followup("execute", on_event=events.append))
    await asyncio.wait_for(entered.wait(), 3)
    e._tool_runtime.cancel_call(row_for(e, "a")["execution_id"])
    result = await asyncio.wait_for(job, 3)
    assert result.reply == "done" and seen == ["hold", "independent"]
    assert result.tool_calls[0].error == "CANCELLED" and result.tool_calls[1].success
    assert result.tool_calls[2].error == "DEPENDENCY_FAILED"
    assert_paired(events, "a")
    assert_paired(events, "b", success=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("stop_parent", [False, True])
async def test_sync_write_drains_and_preserves_completed_facts(tmp_path, stop_parent):
    entered, release = threading.Event(), threading.Event()
    e, seen = engine(tmp_path, parallel=False)
    def write(label):
        entered.set()
        release.wait(5)
        (tmp_path / "committed.txt").write_text("saved")
        return ok_result({"file_path": "committed.txt", "content_version": "sha256:done", "operation_id": "op-done"})
    e.registry.get_tool("save_report").func = write
    model(e, [call("a", "write", name="save_report"), call("b", "dependent", ["a"]), call("c", "independent")])
    events = []
    job = asyncio.create_task(e.followup("execute", on_event=events.append))
    assert await asyncio.to_thread(entered.wait, 2)
    state = e._tool_runtime.cancel_call(row_for(e, "a")["execution_id"])
    assert state["status"] == "cancelling"
    await asyncio.sleep(0.02)
    assert not job.done() and not e._tool_dispatcher.is_cancelled()
    stop = asyncio.create_task(e._driver.stop()) if stop_parent else None
    if stop:
        await asyncio.sleep(0.02)
        assert not stop.done()
    release.set()
    if stop:
        await asyncio.wait_for(stop, 3)
    result = await asyncio.wait_for(job, 3)
    assert (tmp_path / "committed.txt").read_text() == "saved"
    assert_paired(events, "a")
    end = next(ev for ev in events if ev.event_type == E.TOOL_CALL_END and ev.tool_call_id == "a")
    assert end.ui["files"] == ["committed.txt"]
    payload = json.loads(end.result)
    assert payload["execution_completed"] and payload["completed_success"]
    assert payload["operation_id"] == "op-done"
    if stop_parent:
        assert result.truncated and seen == []
    else:
        assert result.reply == "done" and seen == [("independent", {})]


@pytest.mark.asyncio
async def test_sleep_uses_its_own_cooperative_cancel_signal(tmp_path):
    from excelmanus.tools.sleep_tools import get_tools
    e, seen = engine(tmp_path)
    e.registry.register_tools(get_tools())
    entered = asyncio.Event()
    def on_event(event):
        if event.event_type == E.TOOL_CALL_START and event.tool_name == "sleep":
            entered.set()
    model(e, [raw_call("sleep", "sleep", {"seconds": 30}), call("next", "independent")])
    job = asyncio.create_task(e.followup("execute", on_event=on_event))
    await asyncio.wait_for(entered.wait(), 3)
    e._tool_runtime.cancel_call(row_for(e, "sleep")["execution_id"])
    result = await asyncio.wait_for(job, 3)
    assert result.tool_calls[0].error == "CANCELLED"
    assert seen == [("independent", {})] and not e._tool_dispatcher.is_cancelled()


@pytest.mark.asyncio
@pytest.mark.parametrize("inline", [False, True])
async def test_cancel_approval_rejects_original_gate_and_continues(tmp_path, inline):
    e = make_engine(tmp_path)
    e._full_access_enabled = False
    (tmp_path / "keep.txt").write_text("keep")
    entered = asyncio.Event()
    events = []
    def on_event(event):
        events.append(event)
        if event.event_type == E.PENDING_APPROVAL:
            entered.set()
    async def resolver(_):
        await asyncio.Event().wait()
    model(e, [raw_call("delete", "delete_file", {"file_path": "keep.txt"}), raw_call("next", "list_directory", {})])
    job = asyncio.create_task(e.followup("execute", on_event=on_event, approval_resolver=resolver if inline else None))
    await asyncio.wait_for(entered.wait(), 3)
    e._tool_runtime.cancel_call(row_for(e, "delete")["execution_id"])
    result = await asyncio.wait_for(job, 3)
    assert result.reply == "done" and result.tool_calls[0].error == "CANCELLED"
    assert result.tool_calls[1].success and (tmp_path / "keep.txt").exists()
    assert e._approval.pending is None
    assert any(ev.event_type == E.APPROVAL_RESOLVED and not ev.success for ev in events)
    wire = e._client.chat.completions.create.call_args.kwargs["messages"]
    deleted = [m for m in wire if m.get("tool_call_id") == "delete"]
    assert len(deleted) == 1 and json.loads(deleted[0]["content"])["error_code"] == "CANCELLED"
    assert_paired(events, "delete")


@pytest.mark.asyncio
async def test_cancel_question_closes_waiter(tmp_path):
    e = make_engine(tmp_path)
    entered = asyncio.Event()
    events = []
    def on_event(event):
        events.append(event)
        if event.event_type == E.USER_QUESTION:
            entered.set()
    model(e, [raw_call("ask", "ask_user", {"questions": [{"text": "which?", "options": [{"label": "one"}]}]}), raw_call("next", "list_directory", {})])
    job = asyncio.create_task(e.followup("execute", on_event=on_event))
    await asyncio.wait_for(entered.wait(), 3)
    e._tool_runtime.cancel_call(row_for(e, "ask")["execution_id"])
    result = await asyncio.wait_for(job, 3)
    assert result.reply == "done" and result.tool_calls[0].error == "CANCELLED"
    assert e._question_flow.current() is None
    assert_paired(events, "ask")


@pytest.mark.asyncio
async def test_old_execution_identity_cannot_cancel_a_later_reused_call_id(tmp_path):
    entered = asyncio.Event()
    count = 0
    async def read(label):
        nonlocal count
        count += 1
        if count == 2:
            entered.set()
            await asyncio.Event().wait()
        return ok_result({"directory": label})
    e, _ = engine(tmp_path, read=read)
    e._client.chat.completions.create = AsyncMock(side_effect=[response([call("same", "first")]), response(), response([call("same", "second")]), response()])
    await e.followup("first")
    old = row_for(e, "same")["execution_id"]
    job = asyncio.create_task(e.followup("second"))
    await asyncio.wait_for(entered.wait(), 3)
    new = row_for(e, "same")["execution_id"]
    assert old != new
    assert e._tool_runtime.cancel_call(old)["status"] == "completed"
    assert not job.done()
    e._tool_runtime.cancel_call(new)
    assert (await asyncio.wait_for(job, 3)).reply == "done"


@pytest.mark.asyncio
async def test_sdk_child_cancellation_keeps_parent_script_and_later_child_running(tmp_path):
    e = make_engine(tmp_path)
    entered = asyncio.Event()
    async def wait():
        entered.set()
        await asyncio.Event().wait()
    tool = register(e, "wait_for_cancel", wait, {"type": "string"})
    tool.async_func = wait
    register(e, "after_cancel", lambda: "continued", {"type": "string"})
    code = "import em\ntry:\n    em.wait_for_cancel()\nexcept em.HostToolError as e:\n    print(e.code)\nprint(em.after_cancel())"
    model(e, [raw_call("code", "run_code", {"code": code})])
    events = []
    job = asyncio.create_task(e.followup("execute", on_event=events.append))
    await asyncio.wait_for(entered.wait(), 5)
    child = next(row for row in e._tool_runtime.call_states() if row["tool_name"] == "wait_for_cancel")
    assert child["parent_execution_id"] == row_for(e, "code")["execution_id"]
    e._tool_runtime.cancel_call(child["execution_id"])
    result = await asyncio.wait_for(job, 5)
    # Catching the cancelled child lets the script continue, but cannot report
    # all SDK work successful. Process success and business outcome are separate.
    assert not result.tool_calls[0].success
    assert result.tool_calls[0].structured.error.code == "SDK_SUBCALL_FAILED"
    assert result.tool_calls[0].structured.value["return_code"] == 0
    assert "CANCELLED\ncontinued" in result.tool_calls[0].structured.value["stdout_tail"]
    assert not e._tool_dispatcher.is_cancelled()
    assert_paired(events, child["tool_call_id"])


@pytest.mark.asyncio
async def test_cancel_api_checks_session_access_and_never_starts_a_model(tmp_path, monkeypatch):
    import httpx
    from fastapi import FastAPI
    import excelmanus.api_routes_sessions as routes
    e, _ = engine(tmp_path)
    from excelmanus.engine_utils import _normalize_tool_calls
    tc = _normalize_tool_calls([call("queued", "read")])[0]
    row = e._tool_runtime.prepare_call(tc, None, 0)
    restore = AsyncMock(return_value=e)
    monkeypatch.setattr(routes, "get_session_manager", lambda: SimpleNamespace(get_or_restore_engine=restore))
    monkeypatch.setattr(routes, "_has_session_access", AsyncMock(side_effect=lambda sid, _: sid == "allowed"))
    app = FastAPI(); app.include_router(routes.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/api/v1/sessions/allowed/tool-calls")).json()["calls"][0]["status"] == "queued"
        assert (await client.post(f"/api/v1/sessions/other/tool-calls/{row.execution_id}/cancel")).status_code == 404
        response = await client.post(f"/api/v1/sessions/allowed/tool-calls/{row.execution_id}/cancel")
        assert response.json()["call"]["status"] == "cancelled"
        assert (await client.post("/api/v1/sessions/allowed/tool-calls/unknown/cancel")).status_code == 404
    assert e._driver.current_turn()["status"] == "idle"


@pytest.mark.asyncio
async def test_cancel_sdk_parent_waits_past_bridge_timeout_for_sync_child(tmp_path, monkeypatch):
    from excelmanus.code_mode import CodeModeSession

    e = make_engine(tmp_path)
    entered, release = threading.Event(), threading.Event()
    bridge_settled = asyncio.Event()
    real_wait = CodeModeSession.wait_settlement
    async def short_wait(self, timeout=2.0):
        try:
            return await real_wait(self, timeout=0.01)
        finally:
            bridge_settled.set()
    monkeypatch.setattr(CodeModeSession, "wait_settlement", short_wait)
    def write():
        entered.set()
        release.wait(5)
        (tmp_path / "child-commit.txt").write_text("saved")
        return ok_result({"file_path": "child-commit.txt"})
    register(e, "slow_write", write, {"type": "object"}, effect="workspace_write")
    model(e, [raw_call("code", "run_code", {"code": "import em\nem.slow_write()"}), raw_call("next", "list_directory", {})])
    events = []
    job = asyncio.create_task(e.followup("execute", on_event=events.append))
    assert await asyncio.to_thread(entered.wait, 3)
    e._tool_runtime.cancel_call(row_for(e, "code")["execution_id"])
    await asyncio.wait_for(bridge_settled.wait(), 3)
    assert not job.done()
    assert not any(ev.event_type == E.TOOL_CALL_START and ev.tool_call_id == "next" for ev in events)
    release.set()
    result = await asyncio.wait_for(job, 3)
    assert result.reply == "done" and result.tool_calls[0].error == "CANCELLED"
    assert result.tool_calls[1].success
    assert (tmp_path / "child-commit.txt").read_text() == "saved"
    assert all(row["status"] in {"completed", "cancelled"} for row in e._tool_runtime.call_states())


def test_execution_history_is_bounded_without_evicting_active_calls(tmp_path):
    from excelmanus.engine_utils import _normalize_tool_calls
    e, _ = engine(tmp_path)
    active = e._tool_runtime.prepare_call(_normalize_tool_calls([call("keep", "keep")])[0], None, 0)
    for index in range(270):
        row = e._tool_runtime.prepare_call(_normalize_tool_calls([call(str(index), "read")])[0], None, 0)
        e._tool_runtime.cancel_call(row.execution_id)
    assert len(e._tool_runtime.call_states()) == 256
    assert any(row["execution_id"] == active.execution_id for row in e._tool_runtime.call_states())
