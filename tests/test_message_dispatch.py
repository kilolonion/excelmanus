"""Real actor and SSE ownership with controlled model/tool boundaries."""
from __future__ import annotations

import asyncio
import json
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from starlette.requests import Request

from excelmanus.agent.dispatch import DispatchConflict
from excelmanus.agent.session import AgentEngine
from excelmanus.api_app_state import get_runtime
from excelmanus.config import ExcelManusConfig, load_config, ConfigError
from excelmanus.database import Database
from excelmanus.events import EventType as E
from excelmanus.session import SessionManager
from excelmanus.tools.registry import ToolDef, ToolRegistry


def response(text="done", calls=None):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text, tool_calls=calls))])


def engine(tmp_path, **kwargs):
    cfg = ExcelManusConfig(api_key="test", model="test-model", base_url="https://test.invalid/v1",
        workspace_root=str(tmp_path), main_model_vision="false", memory_enabled=False, jev_enabled="off")
    e = AgentEngine(cfg, ToolRegistry(), **kwargs)
    e._client.chat.completions.create = AsyncMock(return_value=response())
    return e


def submit(e, text="second", mode="queue", key="c2"):
    return e.dispatch_message(text, mode=mode, client_message_id=key, extra={"chat_mode": "write"})


@pytest.mark.asyncio
async def test_idempotency_conflicts_and_terminal_receipts_survive_snapshot(tmp_path):
    e = engine(tmp_path)
    one = submit(e)
    assert submit(e)["dispatch_id"] == one["dispatch_id"]
    with pytest.raises(DispatchConflict):
        submit(e, "changed")
    with pytest.raises(DispatchConflict):
        submit(e, mode="interrupt")
    await e._driver.kick()
    assert submit(e)["status"] == "completed"
    assert e._client.chat.completions.create.await_count == 1
    restored = engine(tmp_path)
    restored._driver.restore_runtime_state(json.loads(json.dumps(e._driver.runtime_state())))
    assert submit(restored)["dispatch_id"] == one["dispatch_id"]
    assert restored._driver.inbox.next_turn == ()


@pytest.mark.asyncio
async def test_queued_receipt_is_durable_before_ack_and_can_resume(tmp_path):
    db = Database(str(tmp_path / "state.db"))
    e = engine(tmp_path, database=db)
    e._session_id = "s"
    receipt = submit(e)
    restored = engine(tmp_path, database=db)
    restored._session_id = "s"
    assert restored.restore_session_snapshot()
    assert submit(restored)["dispatch_id"] == receipt["dispatch_id"]
    await restored.followup("/resume-queue")
    assert restored._driver.dispatch_receipt("c2")["status"] == "completed"
    assert [m["content"] for m in restored.memory.messages if m["role"] == "user"] == ["second"]


@pytest.mark.asyncio
async def test_steer_arriving_during_final_answer_runs_before_existing_queue(tmp_path):
    e = engine(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def create(**_):
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            await release.wait()
        return response(str(calls))

    e._client.chat.completions.create = create
    first = asyncio.create_task(e.followup("first"))
    await entered.wait()
    submit(e, "later", "queue", "later")
    submit(e, "correction", "steer", "steer")
    release.set()
    await first
    await e._driver.wait_until_idle()
    assert [m["content"] for m in e.memory.messages if m["role"] == "user"] == ["first", "correction", "later"]


@pytest.mark.asyncio
async def test_interrupt_cancels_model_and_precedes_queue_without_repeated_cancel(tmp_path):
    e = engine(tmp_path)
    entered = asyncio.Event()
    cancelled = []

    async def create(**_):
        if not entered.is_set():
            entered.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                cancelled.append(True)
                raise
        return response("new result")

    e._client.chat.completions.create = create
    events = []
    first = asyncio.create_task(e.followup("first", on_event=events.append, client_message_id="c1"))
    await entered.wait()
    submit(e, "later", "queue", "later")
    receipt = submit(e, "urgent", "interrupt", "urgent")
    assert submit(e, "urgent", "interrupt", "urgent")["dispatch_id"] == receipt["dispatch_id"]
    await first
    await e._driver.wait_until_idle()
    assert cancelled == [True]
    assert [m["content"] for m in e.memory.messages if m["role"] == "user"] == ["first", "urgent", "later"]
    assert e._driver.dispatch_receipt("c1")["status"] == "interrupted"
    assert e._driver.dispatch_receipt("urgent")["status"] == "completed"
    assert len([v for v in events if v.event_type == E.TURN_REPLY]) == 3


@pytest.mark.asyncio
async def test_resume_never_reuses_the_original_user_message_identity(tmp_path):
    e = engine(tmp_path)
    entered = asyncio.Event()

    async def create(**_):
        entered.set()
        await asyncio.Future()

    e._client.chat.completions.create = create
    first = asyncio.create_task(e.followup("original", client_message_id="original-id"))
    await entered.wait()
    await e._driver.stop()
    await first
    e._client.chat.completions.create = AsyncMock(return_value=response())
    await e.followup("/resume")
    original = [m for m in e.raw_messages if m.get("message_id") == "original-id"]
    assert len(original) == 1 and original[0]["content"] == "original"


@pytest.mark.asyncio
async def test_interrupt_waits_for_noncooperative_writer_even_after_cancel_deadline(tmp_path):
    e = engine(tmp_path)
    e._full_access_enabled = True
    e._tool_runtime.cancel_drain_timeout = 0.01
    entered, release = threading.Event(), threading.Event()
    next_started = asyncio.Event()

    def writer():
        entered.set()
        release.wait(3)
        (tmp_path / "finished.txt").write_text("committed", encoding="utf-8")
        return "committed"

    e.registry.register_tool(ToolDef(name="writer", description="fixture", func=writer,
        input_schema={"type": "object", "properties": {}}, write_effect="workspace_write"))
    tc = SimpleNamespace(id="write-1", function=SimpleNamespace(name="writer", arguments="{}"))
    calls = 0

    async def create(**_):
        nonlocal calls
        calls += 1
        if calls == 1:
            return response("", [tc])
        assert (tmp_path / "finished.txt").exists()
        next_started.set()
        return response()

    e._client.chat.completions.create = create
    first = asyncio.create_task(e.followup("write"))
    assert await asyncio.to_thread(entered.wait, 2)
    try:
        submit(e, "urgent", "interrupt")
        await asyncio.sleep(0.08)
        assert not next_started.is_set()
        assert e._driver.running
    finally:
        release.set()
        await first
        await e._driver.wait_until_idle()
    assert next_started.is_set()
    assert e._driver.dispatch_receipt("c2")["status"] == "completed"


def test_cancel_pending_is_idempotent_and_does_not_cancel_other_items(tmp_path):
    e = engine(tmp_path)
    receipt = submit(e)
    submit(e, "third", key="c3")
    assert e._driver.cancel_dispatch(receipt["dispatch_id"])["status"] == "cancelled"
    assert e._driver.cancel_dispatch(receipt["dispatch_id"])["status"] == "cancelled"
    assert [i.content for i in e._driver.inbox.next_turn] == ["third"]


def test_late_steer_does_not_displace_an_interrupt():
    from excelmanus.agent.inbox import Inbox
    inbox = Inbox()
    inbox.push_followup("queued")
    urgent = inbox.push_followup("urgent", extra={"dispatch_mode": "interrupt"})
    inbox.prioritize_interrupt(urgent)
    inbox.push_steer("correction")
    inbox.promote_orphaned_steer()
    assert [i.content for i in inbox.next_turn] == ["urgent", "correction", "queued"]


def test_failed_admission_does_not_leave_queued_work(tmp_path, monkeypatch):
    e = engine(tmp_path)
    monkeypatch.setattr(e, "save_session_snapshot", lambda: False)
    with pytest.raises(OSError):
        submit(e)
    assert e._driver.inbox.next_turn == ()
    assert e._driver.dispatch_snapshot() == []


def test_failed_cancel_keeps_original_queue_order_and_receipt(tmp_path, monkeypatch):
    e = engine(tmp_path)
    first = submit(e)
    submit(e, "third", key="c3")
    monkeypatch.setattr(e, "save_session_snapshot", lambda: False)
    with pytest.raises(OSError):
        e._driver.cancel_dispatch(first["dispatch_id"])
    assert [item.content for item in e._driver.inbox.next_turn] == ["second", "third"]
    assert e._driver.dispatch_receipt("c2")["status"] == "queued"


def test_client_key_cannot_overwrite_a_legacy_history_identity(tmp_path):
    e = engine(tmp_path)
    e.memory.add_user_message("legacy")
    key = e.raw_messages[-1]["message_id"]
    with pytest.raises(DispatchConflict):
        submit(e, "replacement", key=key)
    assert e.raw_messages[-1]["content"] == "legacy"


@pytest.mark.asyncio
async def test_hidden_continuation_remains_hidden_in_receipt_and_durable_history(tmp_path):
    from excelmanus.chat_history import ChatHistoryStore
    e = engine(tmp_path)
    await e.followup("continue", prompt_kind="continue", client_message_id="hidden")
    assert e._driver.dispatch_receipt("hidden")["hidden"] is True
    message = next(m for m in e.raw_messages if m.get("message_id") == "hidden")
    assert ChatHistoryStore._durable_payload(message)["_ui_hidden"] is True


@pytest.mark.asyncio
async def test_rejected_pre_step_is_failed_not_applied(tmp_path):
    e = engine(tmp_path)
    e._driver.add_pre_step_hook(lambda *args: "reject")
    submit(e)
    await e._driver.kick()
    assert e._driver.dispatch_receipt("c2")["status"] == "failed"
    e._client.chat.completions.create.assert_not_awaited()


@pytest.mark.parametrize("mode", ["steer", "queue", "interrupt"])
def test_setting_survives_config_reload(mode):
    assert load_config({"EXCELMANUS_MESSAGE_DISPATCH_DEFAULT": mode}, allow_incomplete=True).message_dispatch_default == mode
    with pytest.raises(ConfigError):
        load_config({"EXCELMANUS_MESSAGE_DISPATCH_DEFAULT": "unknown"}, allow_incomplete=True)


@pytest.mark.asyncio
async def test_sse_owner_and_lock_cover_all_queued_turns(tmp_path, monkeypatch):
    from excelmanus import api_routes_chat as routes
    e = engine(tmp_path)
    manager = SessionManager(5, 60, config=e.config, registry=ToolRegistry())
    from excelmanus.session import _SessionEntry
    manager._sessions["s"] = _SessionEntry(e, 0)
    runtime = get_runtime()
    runtime.session_manager, runtime.config = manager, e.config
    monkeypatch.setattr(routes, "_has_session_access", AsyncMock(return_value=True))
    entered, release, next_entered, next_release = (asyncio.Event() for _ in range(4))

    async def create(**_):
        if not entered.is_set():
            entered.set()
            await release.wait()
            result = response("first result")
        else:
            next_entered.set()
            await next_release.wait()
            result = response("second result")
        result.usage = SimpleNamespace(prompt_tokens=7, completion_tokens=3, total_tokens=10)
        return result

    e._client.chat.completions.create = create
    pool = SimpleNamespace(log_usage=Mock())
    e._pool_account_id = "fixture-account"
    request = Request({"type": "http", "app": SimpleNamespace(state=SimpleNamespace(pool_service=pool))})
    stream = await routes.chat_stream(routes.ChatRequest(session_id="s", message="first"), request)
    chunks = []

    async def read():
        async for chunk in stream.body_iterator:
            chunks.append(chunk)

    reader = asyncio.create_task(read())
    await asyncio.wait_for(entered.wait(), 2)
    try:
        await routes.chat_dispatch("s", routes.DispatchRequest(message="second", mode="queue", client_message_id="c2"), request)
        release.set()
        await asyncio.wait_for(next_entered.wait(), 2)
        assert await manager.is_session_in_flight("s")
        assert not reader.done()
        assert not runtime.active_chat_tasks["s"].done()
    finally:
        release.set()
        next_release.set()
        await asyncio.wait_for(reader, 3)
    assert not await manager.is_session_in_flight("s")
    joined = "".join(chunks)
    assert joined.count("event: turn_reply") == 2
    assert "first result" in joined and "second result" in joined
    pool.log_usage.assert_called_once()
    assert pool.log_usage.call_args.kwargs["total_tokens"] == 20
