from __future__ import annotations

import asyncio
import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from excelmanus.agent.session import AgentEngine
from excelmanus.api_sse import sse_event_to_sse
from excelmanus.chat_history import ChatHistoryStore
from excelmanus.config import ExcelManusConfig
from excelmanus.database import Database
from excelmanus.engine_core.llm_caller import reset_degraded_params
from excelmanus.events import EventType as E, ToolCallEvent
from excelmanus.providers.stream_types import StreamDelta
from excelmanus.session import SessionManager
from excelmanus.subagent.models import SubagentStartRequest
from excelmanus.tools.registry import ToolDef, ToolRegistry
from excelmanus.trace import TraceRecorder


def test_trace_recorder_links_and_closes_nested_spans() -> None:
    trace = TraceRecorder(trace_id="trace-test")
    turn = trace.start("turn:t1", "turn", "t1")
    step = trace.start("turn:t1:step:s1", "step", "s1", parent_key="turn:t1")
    tool = trace.start("tool:c1", "tool", "inspect", parent_key="turn:t1:step:s1")
    trace.finish("tool:c1", status="ok", result="success")
    trace.finish("turn:t1:step:s1", status="ok")
    trace.finish("turn:t1", status="ok")

    snapshot = trace.snapshot()
    assert snapshot["trace_id"] == "trace-test"
    assert len(snapshot["spans"]) == 3
    assert step.parent_span_id == turn.span_id
    assert tool.parent_span_id == step.span_id
    assert all(span["status"] == "ok" for span in snapshot["spans"])


def config(tmp_path, **kwargs):
    return ExcelManusConfig(api_key="trace-secret", base_url="https://test.invalid/v1",
        model="test-model", protocol="openai", workspace_root=str(tmp_path),
        main_model_vision="false", memory_enabled=False, **kwargs)


def response(text="ok", calls=None, usage=None):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text, tool_calls=calls))], usage=usage)


def spans(engine, kind):
    return [s for s in engine._trace.snapshot()["spans"] if s["kind"] == kind]


def assert_tree(snapshot):
    ids = {s["span_id"] for s in snapshot["spans"]}
    assert len(ids) == len(snapshot["spans"])
    for span in snapshot["spans"]:
        assert span["trace_id"] == snapshot["trace_id"]
        assert span["parent_span_id"] is None or span["parent_span_id"] in ids


def test_bounded_trace_prunes_indexes_and_keeps_valid_parent_links():
    trace = TraceRecorder(max_spans=32)
    trace.start("root", "turn", "root")
    for index in range(200):
        trace.start(f"step:{index}", "step", "step", parent_key="root")
        trace.instant("retry", "retry", parent_key=f"step:{index}")
        trace.finish(f"step:{index}")
    assert len(trace.spans) == 32
    assert len(trace._by_key) <= 32
    assert len(trace._started_mono) == len(trace._open) == 1
    assert trace.dropped_spans > 0
    assert_tree(trace.snapshot())
    trace.finish("root", status="error")
    trace.finish("root", status="ok")
    assert trace.get("root").status == "error"


def test_snapshot_is_detached_and_restore_does_not_mutate_original():
    trace = TraceRecorder()
    trace.start("root", "turn", "root", nested={"value": 1})
    saved = trace.snapshot()
    saved["spans"][0]["attributes"]["nested"]["value"] = 2
    assert trace.get("root").attributes["nested"]["value"] == 1
    restored = TraceRecorder.from_snapshot(saved)
    assert restored.trace_id == trace.trace_id
    assert restored.spans[0].status == "interrupted"
    assert restored.spans[0].duration_ms is None
    assert saved["spans"][0]["status"] == "running"
    assert TraceRecorder.from_snapshot(None).spans == []


def test_approval_denial_preserves_tool_parent_and_terminal_ids(tmp_path):
    engine = AgentEngine(config(tmp_path), ToolRegistry())
    engine._driver.turn_id, engine._driver.step_id = "t1", "s1"
    engine._emit(None, ToolCallEvent(E.TURN_START))
    engine._emit(None, ToolCallEvent(E.STEP_START))
    start = ToolCallEvent(E.TOOL_CALL_START, tool_call_id="c1", tool_name="run_shell")
    engine._emit(None, start)
    pending = ToolCallEvent(E.PENDING_APPROVAL, tool_call_id="c1", approval_id="a1")
    engine._emit(None, pending)
    engine._emit(None, ToolCallEvent(E.TOOL_CALL_END, tool_call_id="c1", success=True))
    assert spans(engine, "tool")[0]["status"] == "running"
    done = ToolCallEvent(E.APPROVAL_RESOLVED, tool_call_id="c1", approval_id="a1", success=False)
    engine._emit(None, done)
    assert done.span_id == pending.span_id
    assert done.parent_span_id == start.span_id
    assert spans(engine, "tool")[0]["status"] == "denied"


@pytest.mark.asyncio
async def test_trace_failure_never_repeats_provider_call(tmp_path, monkeypatch):
    engine = AgentEngine(config(tmp_path), ToolRegistry())
    engine._client.chat.completions.create = AsyncMock(return_value=response())
    monkeypatch.setattr(engine._trace, "start", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("trace broken")))
    result = await engine._llm_caller._send_attempt(model="test", messages=[])
    assert result.choices[0].message.content == "ok"
    engine._client.chat.completions.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_trace_query_checks_session_access(monkeypatch):
    from fastapi import HTTPException
    import excelmanus.api_routes_sessions as routes

    monkeypatch.setattr(routes, "_has_session_access", AsyncMock(return_value=False))
    manager = SimpleNamespace(get_or_restore_engine=AsyncMock())
    monkeypatch.setattr(routes, "get_session_manager", lambda: manager)
    with pytest.raises(HTTPException) as exc:
        await routes.get_session_trace("other-session", None)
    assert exc.value.status_code == 404
    manager.get_or_restore_engine.assert_not_awaited()


@pytest.mark.asyncio
async def test_reused_tool_call_id_in_later_turn_has_distinct_span(tmp_path):
    engine = AgentEngine(config(tmp_path), ToolRegistry())
    engine.registry.register_tool(ToolDef(name="count_rows", description="test",
        input_schema={"type": "object", "properties": {}}, func=lambda: "3", write_effect="none"))
    call = SimpleNamespace(id="same-id", function=SimpleNamespace(name="count_rows", arguments="{}"))
    engine._client.chat.completions.create = AsyncMock(side_effect=[response(calls=[call]), response(), response(calls=[call]), response()])
    await engine.followup("one")
    await engine.followup("two")
    tools = spans(engine, "tool")
    assert len(tools) == 2
    assert tools[0]["span_id"] != tools[1]["span_id"]
    assert tools[0]["parent_span_id"] != tools[1]["parent_span_id"]
    assert_tree(engine._trace.snapshot())


@pytest.mark.asyncio
async def test_request_tool_events_and_terminal_snapshot_share_ids(tmp_path):
    db = Database(str(tmp_path / "state.db"))
    engine = AgentEngine(config(tmp_path), ToolRegistry(), database=db)
    engine._session_id = "trace-session"
    engine.registry.register_tool(ToolDef(name="count_rows", description="test",
        input_schema={"type": "object", "properties": {}}, func=lambda: "3", write_effect="none"))
    call = SimpleNamespace(id="c1", function=SimpleNamespace(name="count_rows", arguments="{}"))
    usage = SimpleNamespace(prompt_tokens=30, completion_tokens=4, total_tokens=34)
    engine._client.chat.completions.create = AsyncMock(side_effect=[response(calls=[call], usage=usage), response(usage=usage)])
    events = []
    await engine.followup("read rows", on_event=events.append)
    request = spans(engine, "request")[0]
    tool = spans(engine, "tool")[0]
    assert tool["parent_span_id"] == request["span_id"]
    assert request["attributes"]["request_id"]
    assert request["attributes"]["prompt_tokens"] == 30
    assert request["attributes"]["cost_usd"] is None
    for start, end in ((E.TURN_START, E.TURN_END), (E.STEP_START, E.STEP_END), (E.TOOL_CALL_START, E.TOOL_CALL_END)):
        first = next(e for e in events if e.event_type == start)
        last = next(e for e in events if e.event_type == end)
        assert first.span_id and first.span_id == last.span_id
        assert first.parent_span_id == last.parent_span_id
        wire = json.loads(sse_event_to_sse(last).split("data: ", 1)[1])
        assert wire["trace_id"] == engine._trace.trace_id
        assert wire["span_id"] == last.span_id
    saved = engine._checkpoint_store.load_latest_checkpoint(engine._session_id)["state_dict"]["runtime_state"]["trace"]
    assert saved == engine._trace.snapshot()
    assert all(s["status"] == "ok" for s in saved["spans"])
    assert_tree(saved)
    assert "trace-secret" not in json.dumps(saved)


@pytest.mark.asyncio
async def test_real_retry_has_distinct_attempts_and_no_error_text_leak(tmp_path, monkeypatch):
    engine = AgentEngine(config(tmp_path), ToolRegistry())
    failure = RuntimeError("429 secret-in-error")
    engine._client.chat.completions.create = AsyncMock(side_effect=[failure, response()])
    monkeypatch.setattr("excelmanus.agent.loop.compute_retry_delay", lambda *_: 0)
    await engine.followup("secret-in-prompt")
    requests = spans(engine, "request")
    assert [s["status"] for s in requests] == ["error", "ok"]
    assert [s["attributes"]["attempt"] for s in requests] == [1, 2]
    assert requests[0]["attributes"]["request_id"] == requests[1]["attributes"]["request_id"]
    assert requests[0]["span_id"] != requests[1]["span_id"]
    retry = spans(engine, "retry")[0]
    assert retry["parent_span_id"] == requests[0]["span_id"]
    assert retry["attributes"]["retry_status"] == "retrying"
    encoded = json.dumps(engine._trace.snapshot())
    assert "secret-in-error" not in encoded and "secret-in-prompt" not in encoded


@pytest.mark.asyncio
async def test_internal_parameter_degradation_is_also_traced(tmp_path):
    reset_degraded_params()
    engine = AgentEngine(config(tmp_path), ToolRegistry())
    engine._driver.turn_id, engine._driver.step_id = "t1", "s1"
    engine._emit(None, ToolCallEvent(E.TURN_START))
    engine._emit(None, ToolCallEvent(E.STEP_START))
    engine._client.chat.completions.create = AsyncMock(side_effect=[
        TypeError("unexpected keyword argument 'prompt_cache_key'"), response(),
    ])
    try:
        await engine._llm_caller.create_chat_completion_with_retry({"model": "test-model", "messages": [], "prompt_cache_key": "key"})
        requests = spans(engine, "request")
        assert [s["status"] for s in requests] == ["error", "ok"]
        assert [s["attributes"]["attempt"] for s in requests] == [1, 2]
    finally:
        reset_degraded_params()


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["ok", "error", "cancelled", "incomplete"])
async def test_stream_span_covers_consumption_and_closes(tmp_path, outcome):
    engine = AgentEngine(config(tmp_path), ToolRegistry())
    entered = asyncio.Event()
    closed = []
    async def stream():
        try:
            yield StreamDelta(content_delta="hello")
            entered.set()
            if outcome == "error":
                raise RuntimeError("stream broke")
            if outcome == "cancelled":
                await asyncio.Future()
            yield StreamDelta(finish_reason="length" if outcome == "incomplete" else "stop",
                              usage={"prompt_tokens": 2, "completion_tokens": 1, "cost_usd": 0.01})
        finally:
            closed.append(True)
    engine._client.chat.completions.create = AsyncMock(return_value=stream())
    outbound = await engine._llm_caller._send_attempt(model="test", messages=[], stream=True)
    assert spans(engine, "request")[0]["status"] == "running"
    events = []
    job = asyncio.create_task(engine._llm_caller.consume_stream(outbound, events.append, 1))
    await asyncio.wait_for(entered.wait(), 2)
    if outcome == "cancelled":
        job.cancel()
        with pytest.raises(asyncio.CancelledError):
            await job
    elif outcome == "error":
        with pytest.raises(RuntimeError):
            await job
    else:
        await job
    request = spans(engine, "request")[0]
    assert request["status"] == outcome
    assert request["duration_ms"] is not None
    assert closed == [True]
    assert events[0].span_id == request["span_id"]
    assert events[0].request_id == request["attributes"]["request_id"]


@pytest.mark.asyncio
async def test_turn_cancel_closes_open_step_and_request(tmp_path):
    engine = AgentEngine(config(tmp_path), ToolRegistry())
    entered = asyncio.Event()
    async def create(**_):
        entered.set()
        await asyncio.Future()
    engine._client.chat.completions.create = create
    job = asyncio.create_task(engine.followup("wait"))
    await asyncio.wait_for(entered.wait(), 2)
    await engine._driver.stop()
    await job
    for kind in ("turn", "step", "request"):
        assert spans(engine, kind)[0]["status"] == "cancelled"
    assert not engine._trace._open


@pytest.mark.asyncio
@pytest.mark.parametrize("session_log", [False, True])
async def test_sqlite_restart_and_trace_query_do_not_start_model(tmp_path, monkeypatch, session_log):
    cfg = config(tmp_path, session_log_enabled=session_log)
    source_path, copy_path = tmp_path / "source.db", tmp_path / "copy.db"
    db = Database(str(source_path))
    def manager(database):
        return SessionManager(max_sessions=5, ttl_seconds=60, config=cfg, registry=ToolRegistry(),
                              database=database, chat_history=ChatHistoryStore(database))
    original_manager = manager(db)
    sid, engine = await original_manager.acquire_for_chat(None)
    entered = asyncio.Event()
    async def create(**_):
        entered.set()
        await asyncio.Future()
    engine._client.chat.completions.create = create
    job = asyncio.create_task(engine.followup("中断任务"))
    await asyncio.wait_for(entered.wait(), 2)
    expected = engine._trace.snapshot()
    with sqlite3.connect(source_path) as src, sqlite3.connect(copy_path) as dst:
        src.backup(dst)
    await engine._driver.stop()
    await job
    await original_manager.release_for_chat(sid)
    restored_manager = manager(Database(str(copy_path)))
    restored = await restored_manager.get_or_restore_engine(sid)
    assert restored._trace.trace_id == expected["trace_id"]
    assert {s["span_id"] for s in restored._trace.snapshot()["spans"]} == {s["span_id"] for s in expected["spans"]}
    assert all(s["status"] == "interrupted" for s in restored._trace.snapshot()["spans"])
    restored._client.chat.completions.create = AsyncMock(return_value=response())
    import excelmanus.api_routes_sessions as routes
    monkeypatch.setattr(routes, "_has_session_access", AsyncMock(return_value=True))
    monkeypatch.setattr(routes, "get_session_manager", lambda: restored_manager)
    snapshot = await routes.get_session_trace(sid, None)
    assert snapshot["trace"] == restored._trace.snapshot()
    restored._client.chat.completions.create.assert_not_awaited()
    await restored.followup("继续新的输入")
    assert len(spans(restored, "turn")) == 2
    assert spans(restored, "turn")[-1]["status"] == "ok"
    assert_tree(restored._trace.snapshot())
    old_id = restored._trace.trace_id
    restored.clear_memory()
    assert restored._trace.trace_id != old_id
    assert restored._trace.spans == []
    again = AgentEngine(cfg, ToolRegistry(), database=restored_manager.database)
    again._session_id = sid
    assert again.restore_session_snapshot()
    assert again._trace.spans == []


@pytest.mark.asyncio
async def test_background_child_keeps_ancestry_after_parent_turn_end(tmp_path, monkeypatch):
    parent = AgentEngine(config(tmp_path), ToolRegistry())
    entered, release = asyncio.Event(), asyncio.Event()
    async def child_create(**_):
        entered.set()
        await release.wait()
        return response()
    monkeypatch.setattr("excelmanus.engine_core.llm_client_manager.create_client", lambda **_: SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=child_create))))
    events = []
    parent._driver.turn_id, parent._driver.step_id = "t1", "s1"
    parent._emit(events.append, ToolCallEvent(E.TURN_START))
    parent._emit(events.append, ToolCallEvent(E.STEP_START))
    run_id = await parent._subagent_runtime.start_background(SubagentStartRequest(task="background", on_event=events.append))
    await asyncio.wait_for(entered.wait(), 2)
    parent._driver._turn_record = {"status": "completed"}
    parent._emit(events.append, ToolCallEvent(E.TURN_END))
    child_span = spans(parent, "subagent")[0]
    assert child_span["status"] == "running"
    child_turn = [s for s in spans(parent, "turn") if s["parent_span_id"] == child_span["span_id"]][0]
    assert child_turn["status"] == "running"
    parent._driver.turn_id = "t2"
    parent._emit(events.append, ToolCallEvent(E.TURN_START))
    release.set()
    await parent._subagent_runtime.wait(run_id, 2)
    end = next(e for e in events if e.event_type == E.SUBAGENT_END)
    assert end.span_id == child_span["span_id"]
    assert end.turn_id == "t1"
    assert spans(parent, "subagent")[0]["status"] == "ok"
    assert spans(parent, "request")[0]["parent_span_id"] in {s["span_id"] for s in spans(parent, "step")}
    assert_tree(parent._trace.snapshot())
