"""审批/问答重启续接：真实 Driver、HTTP 决策入口、SQLite 与工具执行。"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from excelmanus.agent.session import AgentEngine
from excelmanus.chat_history import ChatHistoryStore
from excelmanus.chat_turn import run_engine_followup, submit_approval, submit_question_answer
from excelmanus.database import Database
from excelmanus.engine_core.tool_result import ToolUiMeta, ok_result
from excelmanus.events import EventType
from excelmanus.session import SessionManager
from excelmanus.tools.registry import ToolDef, ToolRegistry
from tests.test_main_turn_recovery import config, response


def tool_response(name, arguments, call_id="original-call"):
    tc = SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=json.dumps(arguments)))
    return response(calls=[tc])


@pytest_asyncio.fixture
async def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(AgentEngine, "initialize_mcp", AsyncMock())
    cfg, registry = config(tmp_path), ToolRegistry()
    registry.register_builtin_tools(str(tmp_path))
    databases, managers, tasks, writes = [], [], [], []

    def write():
        writes.append("write")
        (tmp_path / "approved.txt").write_text("approved once", encoding="utf-8")
        return ok_result({"output": "已写入 approved.txt"}, ui_meta=ToolUiMeta(files=["approved.txt"]))

    registry.register_tool(ToolDef(name="gated_write", description="write", func=write,
        input_schema={"type": "object", "properties": {}}, write_effect="workspace_write"))

    def manager(path):
        db = Database(str(path))
        databases.append(db)
        sm = SessionManager(max_sessions=5, ttl_seconds=60, config=cfg, registry=registry,
                            database=db, chat_history=ChatHistoryStore(db))
        managers.append(sm)
        return sm

    async def start(first_response, *, full_access=False):
        path = tmp_path / "source.db"
        sm = manager(path)
        sid, engine = await sm.acquire_for_chat(None)
        engine._full_access_enabled = full_access
        engine._tool_runtime.add_pre_execute(lambda token: "ask" if token.name == "gated_write" else "allow")
        engine._client.chat.completions.create = AsyncMock(side_effect=[first_response, response("完成")])
        events = asyncio.Queue()
        def on_event(event):
            if event.event_type in {EventType.PENDING_APPROVAL, EventType.USER_QUESTION}:
                events.put_nowait(event)
        task = asyncio.create_task(engine.followup("完成任务", on_event=on_event))
        tasks.append(task)
        event = await asyncio.wait_for(events.get(), 4)
        return SimpleNamespace(engine=engine, path=path, sid=sid, manager=sm, task=task, events=events, event=event)

    async def clone(source, name="restored.db"):
        path = tmp_path / name
        with sqlite3.connect(str(source.path)) as src, sqlite3.connect(str(path)) as dst:
            src.backup(dst)
        sm = manager(path)
        engine = await sm.get_or_restore_engine(source.sid)
        assert engine is not None
        engine._client.chat.completions.create = AsyncMock(return_value=response("恢复后完成"))
        return SimpleNamespace(engine=engine, manager=sm, path=path, sid=source.sid)

    def client(sm):
        from excelmanus import api_routes_chat, api_routes_sessions
        monkeypatch.setattr(api_routes_chat, "get_session_manager", lambda: sm)
        monkeypatch.setattr(api_routes_chat, "_has_session_access", AsyncMock(return_value=True))
        monkeypatch.setattr(api_routes_sessions, "get_session_manager", lambda: sm)
        app = FastAPI()
        app.include_router(api_routes_chat.router)
        app.include_router(api_routes_sessions.router)
        return AsyncClient(transport=ASGITransport(app), base_url="http://test")

    yield SimpleNamespace(start=start, clone=clone, client=client, manager=manager, tasks=tasks,
                          registry=registry, writes=writes, path=tmp_path)
    for sm in managers:
        for entry in list(sm._sessions.values()):
            await entry.engine.shutdown_agents()
    await asyncio.gather(*tasks, return_exceptions=True)
    for db in databases:
        db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["accept", "reject"])
async def test_restored_approval_api_continues_the_exact_call_once(setup, decision):
    source = await setup.start(tool_response("gated_write", {}))
    approval_id = source.event.approval_id
    restored = await setup.clone(source)
    await source.engine._driver.stop()
    async with setup.client(restored.manager) as client:
        detail = (await client.get(f"/api/v1/sessions/{source.sid}")).json()
        assert detail["pending_approval"]["approval_id"] == approval_id
        assert detail["turn"]["can_resume"]
        submit = await client.post(f"/api/v1/chat/{source.sid}/approve", json={"approval_id": approval_id, "decision": decision})
        assert submit.status_code == 200 and submit.json()["resume_required"]
        # 重复请求即使给出相反决策，也不会改写已经提交的决定。
        await client.post(f"/api/v1/chat/{source.sid}/approve", json={"approval_id": approval_id, "decision": "reject" if decision == "accept" else "accept"})
    restored.engine._client.chat.completions.create.assert_not_called()
    outcome = await run_engine_followup(restored.engine, "/resume")
    assert outcome.result.reply == "恢复后完成"
    assert setup.writes == (["write"] if decision == "accept" else [])
    assert len(outcome.result.tool_calls) == 1
    assert outcome.result.tool_calls[0].success is (decision == "accept")
    results = [m for m in restored.engine.memory.messages if m.get("tool_call_id") == "original-call"]
    assert len(results) == 1
    assert ("已写入" if decision == "accept" else "已拒绝") in results[0]["content"]
    if decision == "accept":
        assert "./approved.txt" in restored.engine._state.affected_files
    again = await setup.clone(restored, "completed.db")
    assert not again.engine._interaction_handler.can_recover()
    assert "没有可继续" in (await again.engine.followup("/resume")).reply


@pytest.mark.asyncio
async def test_approval_decision_survives_a_crash_before_execution(setup):
    source = await setup.start(tool_response("gated_write", {}))
    assert submit_approval(source.engine, source.event.approval_id, "accept")
    # 同步复制决策提交时的数据库，尚未让原 actor 获得执行机会。
    path = setup.path / "decided.db"
    with sqlite3.connect(str(source.path)) as src, sqlite3.connect(str(path)) as dst:
        src.backup(dst)
    source.engine._driver.request_cancel()
    await source.engine._driver.stop()
    sm = setup.manager(path)
    restored = await sm.get_or_restore_engine(source.sid)
    restored._client.chat.completions.create = AsyncMock(return_value=response("done"))
    assert restored.web_actionable_pending_approval() is None
    resolver = AsyncMock(side_effect=AssertionError("已保存的决策不应再询问"))
    await restored.followup("/resume", approval_resolver=resolver)
    resolver.assert_not_called()
    assert setup.writes == ["write"]


QUESTIONS = {"questions": [
    {"header": "范围", "text": "选择区域", "multi_select": True, "options": [{"label": "华东"}, {"label": "华南"}]},
    {"header": "输出", "text": "需要什么格式？", "options": [{"label": "表格"}, {"label": "文字"}]},
]}


@pytest.mark.asyncio
async def test_workbook_selection_survives_restart_and_resumes_original_tool(setup):
    from openpyxl import Workbook

    wb = Workbook()
    wb.active.title = "明细"
    wb.active.append(["产品", "金额"])
    wb.save(setup.path / "sales.xlsx")
    source = await setup.start(tool_response("ask_user", {"questions": [{"text": "选择金额范围", "selection": {
        "file_path": "sales.xlsx", "sheet": "明细", "ranges": ["B2:B9"],
    }}]}))
    restored = await setup.clone(source)
    await source.engine._driver.stop()
    async with setup.client(restored.manager) as client:
        detail = (await client.get(f"/api/v1/sessions/{source.sid}")).json()
        q = detail["pending_question"]
        assert q["selection"]["ranges"] == ["B2:B9"]
        selected = {**q["selection"], "ranges": ["B2:B5", "D2:D5"]}
        submit = await client.post(f"/api/v1/chat/{source.sid}/answer", json={"question_id": q["id"], "selection": selected})
        assert submit.status_code == 200 and submit.json()["resume_required"]
    await run_engine_followup(restored.engine, "/resume")
    answers = json.loads(next(m["content"] for m in restored.engine.memory.messages if m.get("tool_call_id") == "original-call"))
    answer = answers[0] if isinstance(answers, list) else answers
    assert answer["status"] == "confirmed" and answer["selection"] == selected


@pytest.mark.asyncio
async def test_show_workbook_uses_registered_handler_in_read_mode(setup):
    from openpyxl import Workbook

    wb = Workbook()
    wb.save(setup.path / "display.xlsx")
    sm = setup.manager(setup.path / "presentation.db")
    _sid, engine = await sm.acquire_for_chat(None)
    engine._client.chat.completions.create = AsyncMock(side_effect=[
        tool_response("show_workbook", {"target": {"file_path": "display.xlsx", "sheet": "Sheet", "ranges": ["A1:B2"]}, "stage": "planned", "summary": "准备整理这些单元格"}),
        response("已展示计划范围"),
    ])
    events = []
    result = await engine.followup("展示计划修改范围", chat_mode="read", on_event=events.append)
    call = result.tool_calls[0]
    assert call.tool_name == "show_workbook" and call.success
    payload = json.loads(call.result)
    assert payload["kind"] == "workbook_presentation" and payload["stage"] == "planned"
    assert any(event.event_type == EventType.TOOL_CALL_END and event.tool_name == "show_workbook" for event in events)


@pytest.mark.asyncio
async def test_partial_answers_survive_two_restarts_and_resume_through_answer_api(setup):
    source = await setup.start(tool_response("ask_user", QUESTIONS))
    q1 = source.event.question_id
    assert submit_question_answer(source.engine, q1, "1\n2\n只看已确认订单")
    second = await asyncio.wait_for(source.events.get(), 3)
    q2 = second.question_id
    restored = await setup.clone(source)
    await source.engine._driver.stop()
    assert restored.engine.current_pending_question().question_id == q2
    async with setup.client(restored.manager) as client:
        detail = (await client.get(f"/api/v1/sessions/{source.sid}")).json()
        assert detail["pending_question"]["queue_size"] == 1
        submit = await client.post(f"/api/v1/chat/{source.sid}/answer", json={"question_id": q2, "answer": "表格"})
        assert submit.status_code == 200 and submit.json()["resume_required"]
        await client.post(f"/api/v1/chat/{source.sid}/answer", json={"question_id": q2, "answer": "文字"})
    again = await setup.clone(restored, "answered.db")
    assert again.engine.current_pending_question() is None
    resolver = AsyncMock(side_effect=AssertionError("已回答的问题不能重问"))
    await again.engine.followup("/resume", question_resolver=resolver)
    resolver.assert_not_called()
    answers = json.loads(next(m["content"] for m in again.engine.memory.messages if m.get("tool_call_id") == "original-call"))
    assert len(answers) == 2
    assert [o["label"] for o in answers[0]["selected_options"]][:2] == ["华东", "华南"]
    assert answers[0]["other_text"] == "只看已确认订单"
    assert answers[1]["raw_input"] == "表格"


@pytest.mark.asyncio
async def test_resume_rebuilds_a_waiter_and_can_be_interrupted_again(setup):
    source = await setup.start(tool_response("ask_user", QUESTIONS))
    restored = await setup.clone(source)
    await source.engine._driver.stop()
    observed = asyncio.Queue()
    def collect(event):
        if event.event_type == EventType.USER_QUESTION:
            observed.put_nowait(event)
    resumed = asyncio.create_task(restored.engine.followup("/resume", on_event=collect))
    setup.tasks.append(resumed)
    question = await asyncio.wait_for(observed.get(), 3)
    async with setup.client(restored.manager) as client:
        submitted = await client.post(f"/api/v1/chat/{source.sid}/answer", json={"question_id": question.question_id, "answer": "1"})
        assert not submitted.json()["resume_required"]
    q2 = await asyncio.wait_for(observed.get(), 3)
    await restored.engine._driver.stop()
    await resumed
    again = await setup.clone(restored, "paused-again.db")
    assert again.engine.current_pending_question() is None
    asked = []
    async def answer(question):
        asked.append(question.question_id)
        return "表格"
    await again.engine.followup("/resume", question_resolver=answer)
    assert asked == [q2.question_id]


@pytest.mark.asyncio
@pytest.mark.parametrize("receipt", [True, False])
async def test_started_approved_write_is_not_replayed_when_its_result_is_uncertain(setup, monkeypatch, receipt):
    import threading
    entered, release = threading.Event(), threading.Event()
    tool = setup.registry.get_tool("gated_write")
    original_write = tool.func
    def blocking_write():
        result = original_write()
        entered.set()
        release.wait(timeout=5)
        return result
    tool.func = blocking_write
    source = await setup.start(tool_response("gated_write", {}))
    active = await setup.clone(source, "resumed-write.db")
    await source.engine._driver.stop()
    submit_approval(active.engine, source.event.approval_id, "accept")
    active_task = asyncio.create_task(active.engine.followup("/resume"))
    setup.tasks.append(active_task)
    assert await asyncio.to_thread(entered.wait, 3)
    restored = await setup.clone(active)
    stopping = asyncio.create_task(active.engine._driver.stop())
    await asyncio.sleep(0)
    assert not stopping.done()
    release.set()
    await stopping
    if receipt:
        assert restored.engine._approval.get_applied(source.event.approval_id) is not None
    else:
        monkeypatch.setattr(restored.engine._approval, "get_applied", lambda _: None)
    await restored.engine.followup("/resume")
    assert setup.writes == ["write"]
    content = next(m["content"] for m in restored.engine.memory.messages if m.get("tool_call_id") == "original-call")
    assert ("已写入" if receipt else "不能假定已经生效") in content


@pytest.mark.asyncio
async def test_completed_interaction_result_is_backfilled_without_reexecution(setup, monkeypatch):
    source = await setup.start(tool_response("gated_write", {}))
    restored = await setup.clone(source)
    await source.engine._driver.stop()
    captured_path = setup.path / "result-not-consumed.db"
    original_persist = restored.engine._interaction_handler._persist
    captured = False
    def capture():
        nonlocal captured
        original_persist()
        frame = restored.engine._interaction_handler.snapshot() or {}
        if not captured and frame.get("phase") == "completed" and not frame.get("consumed"):
            with sqlite3.connect(str(restored.path)) as src, sqlite3.connect(str(captured_path)) as dst:
                src.backup(dst)
            captured = True
    monkeypatch.setattr(restored.engine._interaction_handler, "_persist", capture)
    submit_approval(restored.engine, source.event.approval_id, "accept")
    await restored.engine.followup("/resume")
    assert captured
    sm = setup.manager(captured_path)
    again = await sm.get_or_restore_engine(source.sid)
    again._client.chat.completions.create = AsyncMock(return_value=response("done"))
    await again.followup("/resume")
    assert setup.writes == ["write"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["question", "approval"])
async def test_script_interaction_attaches_to_the_root_without_rerunning_script(setup, kind):
    prefix = setup.path / "prefix.txt"
    suffix = setup.path / "suffix.txt"
    for name, path in [("prefix_write", prefix), ("suffix_write", suffix)]:
        def write_marker(path=path):
            path.write_text("once", encoding="utf-8")
            return ok_result({"output": "written"})
        setup.registry.register_tool(ToolDef(name=name, description="marker", func=write_marker,
            input_schema={"type": "object", "properties": {}}, write_effect="workspace_write"))
    call = "em.ask_user(questions=[{'text':'继续吗？','options':[{'label':'继续'}]}])" if kind == "question" else "em.gated_write()"
    code = f"import em\nem.prefix_write()\n{call}\nem.suffix_write()\n"
    source = await setup.start(tool_response("run_code", {"code": code, "python_command": sys.executable,
        "require_excel_deps": False, "timeout_seconds": 20}, call_id="script-root"))
    restored = await setup.clone(source)
    await asyncio.wait_for(source.engine._driver.stop(), 3)
    assert prefix.exists()
    if kind == "question":
        submit_question_answer(restored.engine, source.event.question_id, "继续")
    else:
        submit_approval(restored.engine, source.event.approval_id, "accept")
    await restored.engine.followup("/resume")
    results = [m for m in restored.engine.memory.messages if m.get("role") == "tool"]
    root = next(m for m in results if m.get("tool_call_id") == "script-root")
    assert json.loads(root["content"])["status"] == "interrupted"
    assert all(m["tool_call_id"] == "script-root" for m in results)
    assert not suffix.exists()
    if kind == "approval":
        assert setup.writes == ["write"]
