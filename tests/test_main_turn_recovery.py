"""主任务的独立完成通知与执行边界恢复；模型通信使用可控客户端。"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from excelmanus.agent.session import AgentEngine
from excelmanus.chat_history import ChatHistoryStore
from excelmanus.config import ExcelManusConfig
from excelmanus.database import Database
from excelmanus.events import EventType
from excelmanus.session import SessionManager
from excelmanus.tools.registry import ToolDef, ToolRegistry


def response(text="", calls=None):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text, tool_calls=calls))])


def config(tmp_path):
    return ExcelManusConfig(api_key="test", model="test-model", base_url="https://test.invalid/v1",
                           workspace_root=str(tmp_path), memory_enabled=False, main_model_vision="false")


def make_engine(tmp_path):
    return AgentEngine(config(tmp_path), ToolRegistry())


@pytest.mark.asyncio
async def test_each_followup_returns_while_the_next_turn_is_still_running(tmp_path):
    engine = make_engine(tmp_path)
    first_entered, release_first, second_entered, release_second = (asyncio.Event() for _ in range(4))
    calls = 0

    async def create(**_):
        nonlocal calls
        calls += 1
        if calls == 1:
            first_entered.set()
            await release_first.wait()
            return response("第一条完成")
        second_entered.set()
        await release_second.wait()
        return response("第二条完成")

    engine._client.chat.completions.create = create
    first_events, second_events = [], []
    first = asyncio.create_task(engine.followup("第一条", on_event=first_events.append))
    await asyncio.wait_for(first_entered.wait(), 2)
    second = asyncio.create_task(engine.followup("第二条", on_event=second_events.append))
    await asyncio.sleep(0)
    release_first.set()
    await asyncio.wait_for(second_entered.wait(), 2)
    try:
        assert (await asyncio.wait_for(asyncio.shield(first), 0.5)).reply == "第一条完成"
        assert not second.done()
        assert all(event.turn_id != "t2" for event in first_events)
        assert any(event.event_type == EventType.TURN_START and event.turn_id == "t2" for event in second_events)
    finally:
        release_second.set()
        await asyncio.gather(first, second)


@pytest.mark.asyncio
async def test_pre_step_failure_settles_its_item_and_does_not_fail_the_next(tmp_path):
    engine = make_engine(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()
    attempts = 0

    async def attachment(_):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            entered.set()
            await release.wait()
            raise ValueError("pre-step failed")
        return "enter"

    engine._driver.add_pre_step_attachment(attachment)
    engine._client.chat.completions.create = AsyncMock(return_value=response("后续输入完成"))
    first = asyncio.create_task(engine.followup("失败的输入"))
    await asyncio.wait_for(entered.wait(), 2)
    second = asyncio.create_task(engine.followup("独立的后续输入"))
    await asyncio.sleep(0)
    release.set()
    with pytest.raises(ValueError, match="pre-step failed"):
        await asyncio.wait_for(first, 2)
    assert (await asyncio.wait_for(second, 2)).reply == "后续输入完成"
    assert engine._driver.current_turn()["status"] == "completed"


@pytest.mark.asyncio
async def test_queueing_a_followup_does_not_repair_a_tool_that_is_still_running(tmp_path):
    import threading

    engine = make_engine(tmp_path)
    entered, release = threading.Event(), threading.Event()

    def running_tool():
        entered.set()
        release.wait(timeout=5)
        return "实际执行结果"

    engine.registry.register_tool(ToolDef(name="pending_work", description="work", func=running_tool,
        input_schema={"type": "object", "properties": {}}, write_effect="none"))
    tc = SimpleNamespace(id="inflight-tool", function=SimpleNamespace(name="pending_work", arguments="{}"))
    engine._client.chat.completions.create = AsyncMock(side_effect=[response(calls=[tc]), response("first"), response("second")])
    first = asyncio.create_task(engine.followup("执行工具"))
    assert await asyncio.to_thread(entered.wait, 2)
    second = asyncio.create_task(engine.followup("排队输入"))
    await asyncio.sleep(0)
    try:
        assert not any(m.get("tool_call_id") == tc.id for m in engine.memory.messages)
    finally:
        release.set()
        await asyncio.gather(first, second)
    results = [m for m in engine.memory.messages if m.get("tool_call_id") == tc.id]
    assert len(results) == 1
    assert "实际执行结果" in results[0]["content"]


@pytest.mark.asyncio
async def test_caller_cancellation_keeps_actor_alive_and_explicit_stop_leaves_queue_resumable(tmp_path):
    engine = make_engine(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()

    async def create(**_):
        entered.set()
        await release.wait()
        return response("完成")

    engine._client.chat.completions.create = create
    first = asyncio.create_task(engine.followup("原任务"))
    await asyncio.wait_for(entered.wait(), 2)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    assert engine._driver.running
    second = asyncio.create_task(engine.followup("排队任务"))
    await asyncio.sleep(0)
    await engine._driver.stop()
    with pytest.raises(asyncio.CancelledError):
        await second
    assert [item.content for item in engine._driver.inbox.next_turn] == ["排队任务"]
    assert engine._driver.current_turn()["can_resume"]
    assert not engine._driver.runtime_state()["driver"]["active"]
    release.set()
    assert (await engine.followup("/resume 先完成原任务")).reply == "完成"
    await engine._driver.kick()
    assert engine._driver.inbox.next_turn == ()


@pytest.mark.asyncio
async def test_claimed_request_and_staged_input_survive_pre_step_interruption(tmp_path):
    source = make_engine(tmp_path)
    entered = asyncio.Event()

    async def attachment(_):
        entered.set()
        await asyncio.Future()

    source._driver.add_pre_step_attachment(attachment)
    source.inject("保留原来的统计口径")
    task = asyncio.create_task(source.followup("在读模式继续分析", chat_mode="read"))
    await asyncio.wait_for(entered.wait(), 2)
    snapshot = json.loads(json.dumps(source._driver.runtime_state()))
    assert snapshot["driver"]["inbox"]["next-turn"] == []
    assert snapshot["driver"]["turn"]["task"] == "在读模式继续分析"
    await source._driver.stop()
    await task
    restored = make_engine(tmp_path)
    restored._driver.restore_runtime_state(snapshot)
    create = AsyncMock(return_value=response("继续完成"))
    restored._client.chat.completions.create = create
    assert restored._driver.current_turn()["status"] == "interrupted"
    assert (await restored.followup("/resume")).reply == "继续完成"
    assert restored._current_chat_mode == "read"
    sent = create.call_args.kwargs["messages"]
    assert any("保留原来的统计口径" in str(m.get("content")) for m in sent)
    assert any("在读模式继续分析" in str(m.get("content")) for m in sent)
    assert restored._driver.current_turn()["resumed_from"] == "t1"


@pytest.mark.asyncio
@pytest.mark.parametrize("session_log", [True, False])
async def test_restart_uses_durable_tool_result_without_replaying_completed_write(tmp_path, monkeypatch, session_log):
    from dataclasses import replace
    from excelmanus.engine_core.tool_result import ok_result
    from excelmanus.chat_turn import run_engine_followup

    monkeypatch.setattr(AgentEngine, "initialize_mcp", AsyncMock())
    cfg = replace(config(tmp_path), session_log_enabled=session_log)
    source_file = tmp_path / "source.db"
    copied_file = tmp_path / "restart.db"
    db = Database(str(source_file))
    registry = ToolRegistry()
    writes = []

    def write_once():
        writes.append("committed")
        (tmp_path / "result.txt").write_text("已经完成的内容", encoding="utf-8")
        return ok_result({"output": "已提交 result.txt"})

    registry.register_tool(ToolDef(name="write_once", description="write", func=write_once,
        input_schema={"type": "object", "properties": {}}, write_effect="workspace_write"))

    def manager(database):
        return SessionManager(max_sessions=5, ttl_seconds=60, config=cfg, registry=registry,
                              database=database, chat_history=ChatHistoryStore(database))

    original_manager = manager(db)
    sid, source = await original_manager.acquire_for_chat(None)
    entered = asyncio.Event()
    tc = SimpleNamespace(id="write-call", function=SimpleNamespace(name="write_once", arguments="{}"))
    calls = 0

    async def create(**_):
        nonlocal calls
        calls += 1
        if calls == 1:
            return response(calls=[tc])
        entered.set()
        await asyncio.Future()

    source._client.chat.completions.create = create
    job = asyncio.create_task(source.followup("写入结果后说明内容"))
    await asyncio.wait_for(entered.wait(), 3)
    # 模拟进程失去内存，保留此刻已经落盘的数据，不把 orderly stop 的快照带入副本。
    with sqlite3.connect(str(source_file)) as src, sqlite3.connect(str(copied_file)) as dst:
        src.backup(dst)
    await source._driver.stop()
    await job
    await original_manager.release_for_chat(sid)
    restored_db = Database(str(copied_file))
    restored_manager = manager(restored_db)
    restored = await restored_manager.get_or_restore_engine(sid)
    assert restored is not None
    try:
        assert restored._driver.current_turn()["status"] == "interrupted"
        assert restored._driver.current_turn()["step_index"] == 2
        assert any(m.get("tool_call_id") == "write-call" and "已提交 result.txt" in m["content"] for m in restored.memory.messages)
        resumed_create = AsyncMock(return_value=response("结果已保存，继续完成说明"))
        restored._client.chat.completions.create = resumed_create
        outcome = await run_engine_followup(restored, "/resume 请用中文说明")
        assert outcome.result.reply == "结果已保存，继续完成说明"
        assert writes == ["committed"]
        assert (tmp_path / "result.txt").read_text(encoding="utf-8") == "已经完成的内容"
        assert not restored._driver.current_turn()["can_resume"]
        restored_again = manager(restored_db)
        final = await restored_again.get_or_restore_engine(sid)
        assert final._driver.current_turn()["status"] == "completed"
        await final.followup("/clear")
        cleared = await manager(restored_db).get_or_restore_engine(sid)
        assert cleared._driver.current_turn()["status"] == "idle"
        assert not cleared.memory.messages
    finally:
        await source.shutdown_agents()
        await restored.shutdown_agents()
        db.close()
        restored_db.close()


@pytest.mark.asyncio
async def test_resume_without_interruption_does_not_call_model_and_clear_removes_recovery(tmp_path):
    engine = make_engine(tmp_path)
    create = AsyncMock(return_value=response("done"))
    engine._client.chat.completions.create = create
    assert "没有可继续" in (await engine.followup("/resume")).reply
    create.assert_not_called()
    await engine.followup("一条消息")
    await engine.followup("/clear")
    assert engine._driver.current_turn()["status"] == "idle"
    assert "没有可继续" in (await engine.followup("/resume")).reply
    assert create.await_count == 1


@pytest.mark.asyncio
async def test_queued_resume_is_reused_after_another_restart(tmp_path):
    source = make_engine(tmp_path)
    source._driver._turn_record = {
        "status": "interrupted", "turn_id": "t1", "task": "原任务", "input": {"extra": {}},
    }
    queued = source._driver.inbox.push("followup", "继续原任务", extra={
        "resumed_from": "t1", "resume_task": "原任务", "chat_mode": "read",
    })
    restored = make_engine(tmp_path)
    restored._driver.restore_runtime_state(json.loads(json.dumps(source._driver.runtime_state())))
    create = AsyncMock(return_value=response("done"))
    restored._client.chat.completions.create = create
    await restored.followup("/resume")
    await restored._driver.kick()
    assert create.await_count == 1
    assert restored._driver.current_turn()["item_id"] == queued.id


@pytest.mark.asyncio
async def test_main_turn_api_reports_recovery_without_starting_the_actor(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from excelmanus import api_routes_sessions as routes

    engine = make_engine(tmp_path)
    engine._driver.restore_runtime_state({"driver": {"turn_index": 3, "turn": {
        "status": "running", "turn_id": "t3", "task": "未完成分析", "step_index": 2,
        "input": {"extra": {"on_event": "private"}},
    }}})
    create = AsyncMock()
    engine._client.chat.completions.create = create
    manager = SimpleNamespace(get_or_restore_engine=AsyncMock(return_value=engine))
    monkeypatch.setattr(routes, "get_session_manager", lambda: manager)
    monkeypatch.setattr(routes, "_has_session_access", AsyncMock(return_value=True))
    app = FastAPI()
    app.include_router(routes.router)
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
        reply = await client.get("/api/v1/sessions/main-test/turn")
    assert reply.status_code == 200
    assert reply.json()["turn"]["status"] == "interrupted"
    assert reply.json()["turn"]["can_resume"] is True
    assert "input" not in reply.json()["turn"]
    assert engine._driver._runner_task is None
    create.assert_not_called()


@pytest.mark.asyncio
async def test_restored_interaction_is_retained_but_not_reported_as_a_resumed_consumer(tmp_path):
    engine = make_engine(tmp_path)
    engine._driver.restore_runtime_state({
        "driver": {"turn": {"status": "running", "turn_id": "t1", "task": "等待审批"}},
        "approval": {"approval_id": "approve-1", "tool_name": "write_text_file", "arguments": {}},
    })
    create = AsyncMock()
    engine._client.chat.completions.create = create
    assert not engine.discard_stale_web_approval(in_flight=False)
    assert engine.current_pending_approval().approval_id == "approve-1"
    assert engine._driver.current_turn()["resume_blocked_by"] == ["approval"]
    assert engine._driver.current_turn()["can_resume"] is False
    assert "尚不支持" in (await engine.followup("/resume")).reply
    create.assert_not_called()


@pytest.mark.asyncio
async def test_resume_repairs_incomplete_tool_results_before_adding_hidden_context(tmp_path):
    engine = make_engine(tmp_path)
    engine.memory.add_assistant_tool_message({"role": "assistant", "content": "", "tool_calls": [{
        "id": "uncertain-call", "type": "function", "function": {"name": "write_text_file", "arguments": "{}"},
    }]})
    engine._driver._turn_record = {"turn_id": "t1", "status": "interrupted", "task": "完成原任务", "input": {}}
    create = AsyncMock(return_value=response("继续完成"))
    engine._client.chat.completions.create = create
    await engine.followup("/resume")
    sent = create.call_args.kwargs["messages"]
    result_index = next(i for i, message in enumerate(sent) if message.get("tool_call_id") == "uncertain-call")
    context_index = next(i for i, message in enumerate(sent) if "中断任务的原始要求" in str(message.get("content")))
    assert result_index < context_index
    assert "结果未完整记录" in sent[result_index]["content"]
    assert any(message.get("_prompt_kind") == "task_resume" and message.get("_ui_hidden") for message in engine.memory.messages)


def test_snapshot_retention_keeps_clear_even_after_many_prior_turns(tmp_path):
    from excelmanus.stores.session_state_store import SessionStateStore

    db = Database(str(tmp_path / "retention.db"))
    try:
        store = SessionStateStore(db)
        for turn in range(1, 25):
            store.save_session_snapshot(session_id="many-turns", state_dict={"turn": turn}, task_list_dict={}, turn_number=turn)
        store.save_session_snapshot(session_id="many-turns", state_dict={"cleared": True}, task_list_dict={}, turn_number=0)
        assert store.load_latest_checkpoint("many-turns")["state_dict"] == {"cleared": True}
    finally:
        db.close()
