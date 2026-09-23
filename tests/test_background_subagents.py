"""后台委派使用真实子 Driver/工具分派，模型通信由事件驱动的假客户端代替。"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from excelmanus.agent.session import AgentEngine
from excelmanus.config import ExcelManusConfig
from excelmanus.database import Database
from excelmanus.events import EventType
from excelmanus.subagent.models import SubagentStartRequest
from excelmanus.task_list import TaskStatus
from excelmanus.tools.registry import ToolRegistry


def response(text):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text, tool_calls=None))])


def engine(tmp_path, *, database=None, **overrides):
    config = ExcelManusConfig(
        api_key="test", model="test-model", base_url="https://test.invalid/v1",
        workspace_root=str(tmp_path), main_model_vision="false", **overrides,
    )
    parent = AgentEngine(config, ToolRegistry(), database=database)
    parent._session_id = "background-test"
    parent._client.chat.completions.create = AsyncMock(return_value=response("主任务完成"))
    return parent


def child_client(monkeypatch, create):
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr("excelmanus.engine_core.llm_client_manager.create_client", lambda **_: client)


@pytest.mark.asyncio
async def test_background_returns_immediately_parent_continues_and_result_survives(monkeypatch, tmp_path):
    parent = engine(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()

    async def create(**_):
        entered.set()
        await release.wait()
        return response("子任务的完整结果")

    child_client(monkeypatch, create)
    events = []
    runtime = parent._subagent_runtime
    run_id = await runtime.start_background(SubagentStartRequest(task="统计", on_event=events.append))
    assert runtime.get_run(run_id)["status"] == "queued"
    await asyncio.wait_for(entered.wait(), 2)
    assert (await parent.followup("独立工作")).reply == "主任务完成"
    assert (await runtime.wait(run_id, 0.001))["status"] == "running"
    release.set()
    row = await runtime.wait(run_id, 2)
    assert row["status"] == "completed"
    assert row["result"]["output"] == "子任务的完整结果"
    assert runtime.get_run(run_id) == row
    assert not runtime.has_active_runs
    assert [e.event_type for e in events].count(EventType.SUBAGENT_START) == 1
    assert [e.event_type for e in events].count(EventType.SUBAGENT_END) == 1
    assert any(run_id in item.content for item in parent._driver.inbox.next_step)


@pytest.mark.asyncio
async def test_send_is_visible_at_next_child_step(monkeypatch, tmp_path):
    parent = engine(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()
    requests = []

    async def create(**kwargs):
        requests.append(kwargs["messages"])
        if len(requests) == 1:
            entered.set()
            await release.wait()
            return response("原口径")
        return response("按华东口径完成")

    child_client(monkeypatch, create)
    runtime = parent._subagent_runtime
    run_id = await runtime.start_background(SubagentStartRequest(task="统计全国"))
    await asyncio.wait_for(entered.wait(), 2)
    await runtime.send_message(run_id, "范围改为华东")
    release.set()
    row = await runtime.wait(run_id, 2)
    assert row["status"] == "completed"
    assert row["result"]["output"] == "按华东口径完成"
    assert len(requests) == 2
    assert any(m["content"] == "范围改为华东" for m in requests[-1])


@pytest.mark.asyncio
async def test_pause_drains_child_and_resume_keeps_history(monkeypatch, tmp_path):
    parent = engine(tmp_path)
    entered, stopped = asyncio.Event(), asyncio.Event()

    async def create(**_):
        entered.set()
        try:
            await asyncio.Future()
        finally:
            stopped.set()

    child_client(monkeypatch, create)
    runtime = parent._subagent_runtime
    run_id = await runtime.start_background(SubagentStartRequest(task="准备月报"))
    await asyncio.wait_for(entered.wait(), 2)
    child = runtime._live[run_id][1]._child
    await runtime.interrupt(run_id, pause=True)
    assert stopped.is_set()
    assert child._driver._runner_task is None
    assert runtime.get_run(run_id)["status"] == "paused"
    seen = []

    async def continuation(**kwargs):
        seen.extend(kwargs["messages"])
        return response("月报完成")

    child_client(monkeypatch, continuation)
    resumed = await runtime.resume(run_id, "继续补完月报")
    assert resumed != run_id
    row = await runtime.wait(resumed, 2)
    assert row["status"] == "completed"
    assert row["resumed_from"] == run_id
    assert any("准备月报" in str(m["content"]) for m in seen)
    assert any("继续补完月报" in str(m["content"]) for m in seen)


@pytest.mark.asyncio
async def test_cancel_before_runner_starts_still_settles(monkeypatch, tmp_path):
    parent = engine(tmp_path)
    create = AsyncMock(return_value=response("不应执行"))
    child_client(monkeypatch, create)
    events = []
    runtime = parent._subagent_runtime
    run_id = await runtime.start_background(SubagentStartRequest(task="取消", on_event=events.append))
    await runtime.interrupt(run_id)
    assert runtime.get_run(run_id)["status"] == "aborted"
    assert not runtime.has_active_runs
    create.assert_not_awaited()
    assert [e.event_type for e in events].count(EventType.SUBAGENT_END) == 1


@pytest.mark.asyncio
async def test_wait_cancellation_does_not_cancel_job(monkeypatch, tmp_path):
    parent = engine(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()

    async def create(**_):
        entered.set()
        await release.wait()
        return response("ok")

    child_client(monkeypatch, create)
    runtime = parent._subagent_runtime
    run_id = await runtime.start_background(SubagentStartRequest(task="继续"))
    await asyncio.wait_for(entered.wait(), 2)
    waiter = asyncio.create_task(runtime.wait(run_id, 10))
    await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert runtime.get_run(run_id)["status"] == "running"
    release.set()
    assert (await runtime.wait(run_id, 2))["status"] == "completed"


@pytest.mark.asyncio
async def test_snapshot_restores_results_and_marks_running_interrupted(monkeypatch, tmp_path):
    db = Database(str(tmp_path / "session.db"))
    parent = engine(tmp_path, database=db)
    child_client(monkeypatch, AsyncMock(return_value=response("持久结果")))
    run_id = await parent._subagent_runtime.start_background(SubagentStartRequest(task="保存结果"))
    await parent._subagent_runtime.wait(run_id, 2)
    parent.save_session_snapshot()
    restored = engine(tmp_path, database=db)
    assert restored.restore_session_snapshot()
    assert restored._subagent_runtime.get_run(run_id)["result"]["output"] == "持久结果"
    rows = parent._subagent_runtime.snapshot()
    rows[0].update(status="running", result=None)
    restored._subagent_runtime.restore(rows)
    assert restored._subagent_runtime.get_run(run_id)["status"] == "interrupted"
    assert not restored._subagent_runtime.has_active_runs
    restarted = await restored._subagent_runtime.resume(run_id)
    assert (await restored._subagent_runtime.wait(restarted, 2))["status"] == "completed"
    db.close()


async def delegate(parent, **arguments):
    tc = SimpleNamespace(id="control", function=SimpleNamespace(name="delegate", arguments=json.dumps(arguments)))
    return await parent._execute_tool_call(tc, None, None, 1)


@pytest.mark.asyncio
async def test_background_is_reachable_through_real_tool_schema_and_dispatch(monkeypatch, tmp_path):
    parent = engine(tmp_path)
    child_client(monkeypatch, AsyncMock(return_value=response("ok")))
    # 非核心工具按当前目录协议先发现，再进入后续请求的直接工具 schema。
    discover = SimpleNamespace(id="discover-delegate", function=SimpleNamespace(
        name="introspect_capability", arguments=json.dumps({"query_type": "tool_detail", "query": "delegate"}),
    ))
    assert (await parent._execute_tool_call(discover, None, None, 1)).success
    schemas = parent._meta_tool_builder.build_v5_tools(tool_access="may_write")
    schema = next(t["function"]["parameters"] for t in schemas if t["function"]["name"] == "delegate")
    assert {"background", "action", "run_id", "message", "wait_seconds"} <= schema["properties"].keys()
    result = await delegate(parent, task="统计", background=True)
    assert result.success
    row = result.structured.value["run"]
    result = await delegate(parent, action="wait", run_id=row["run_id"], wait_seconds=2)
    assert result.success
    assert result.structured.value["run"]["result"]["output"] == "ok"
    listed = await delegate(parent, action="list")
    assert listed.success and len(listed.structured.value["runs"]) == 1
    missing = await delegate(parent, action="status", run_id="missing")
    assert not missing.success


def test_empty_inbox_snapshot_roundtrips_after_used_sequence():
    from excelmanus.agent.inbox import Inbox

    source = Inbox()
    source.push_followup("first")
    source.claim("next-turn", turn=1)
    restored = Inbox()
    restored.load_reconstructed(source.reconstruct())
    assert restored.push_followup("second").id == "inb_2"


@pytest.mark.asyncio
async def test_code_mode_generated_sdk_can_start_and_wait(monkeypatch, tmp_path):
    import sys
    from excelmanus.tools import code_tools
    from excelmanus.engine_core.tool_dispatcher import _SyntheticToolCall

    parent = engine(tmp_path)
    parent.registry.register_tools(code_tools.get_tools())
    parent._full_access_enabled = True
    child_client(monkeypatch, AsyncMock(return_value=response("SDK 子任务结果")))
    code = (
        "import em\n"
        "started = em.delegate(task='独立统计', background=True)\n"
        "row = em.delegate(action='wait', run_id=started['run']['run_id'], wait_seconds=2)\n"
        "print(row['run']['result']['output'])\n"
    )
    result = await parent._execute_tool_call(_SyntheticToolCall(
        call_id="code-bg", name="run_code", arguments={
            "code": code, "python_command": sys.executable,
            "timeout_seconds": 30, "require_excel_deps": False,
        },
    ), None, None, 1)
    assert result.success, result.result
    assert "SDK 子任务结果" in result.result
    assert not parent._subagent_runtime.has_active_runs


@pytest.mark.asyncio
async def test_background_outlives_code_mode_program(monkeypatch, tmp_path):
    import sys
    from excelmanus.tools import code_tools
    from excelmanus.engine_core.tool_dispatcher import _SyntheticToolCall

    parent = engine(tmp_path)
    parent.registry.register_tools(code_tools.get_tools())
    parent._full_access_enabled = True
    entered, release = asyncio.Event(), asyncio.Event()

    async def create(**_):
        entered.set()
        await release.wait()
        return response("程序结束后仍然完成")

    child_client(monkeypatch, create)
    result = await parent._execute_tool_call(_SyntheticToolCall(
        call_id="code-detach", name="run_code", arguments={
            "code": "import em\nprint(em.delegate(task='统计', background=True)['run']['run_id'])",
            "python_command": sys.executable, "timeout_seconds": 30, "require_excel_deps": False,
        },
    ), None, None, 1)
    assert result.success, result.result
    await asyncio.wait_for(entered.wait(), 2)
    row = parent._subagent_runtime.list_runs()[0]
    assert row["status"] == "running"
    release.set()
    final = await parent._subagent_runtime.wait(row["run_id"], 2)
    assert final["result"]["output"] == "程序结束后仍然完成"


@pytest.mark.asyncio
async def test_session_control_api_waits_and_cancels(monkeypatch, tmp_path):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    import excelmanus.api_routes_sessions as routes

    parent = engine(tmp_path)
    entered = asyncio.Event()

    async def create(**_):
        entered.set()
        await asyncio.Future()

    child_client(monkeypatch, create)
    runtime = parent._subagent_runtime
    run_id = await runtime.start_background(SubagentStartRequest(task="API 子任务"))
    await asyncio.wait_for(entered.wait(), 2)
    monkeypatch.setattr(routes, "_has_session_access", AsyncMock(return_value=True))
    manager = SimpleNamespace(get_engine=lambda sid: parent, get_or_restore_engine=AsyncMock(return_value=parent))
    monkeypatch.setattr(routes, "get_session_manager", lambda: manager)
    app = FastAPI()
    app.include_router(routes.router)
    url = f"/api/v1/sessions/s/subagents/{run_id}"
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
        listed = await client.get("/api/v1/sessions/s/subagents")
        assert listed.status_code == 200
        assert listed.json()["runs"][0]["run_id"] == run_id
        waiting = await client.post(url, json={"action": "wait", "wait_seconds": 0})
        assert waiting.json()["run"]["status"] == "running"
        cancelled = await client.post(url, json={"action": "cancel"})
        assert cancelled.json()["run"]["status"] == "aborted"
        missing = await client.post("/api/v1/sessions/s/subagents/nope", json={"action": "status"})
        assert missing.status_code == 404


@pytest.mark.asyncio
async def test_session_task_list_api_returns_current_snapshot(monkeypatch, tmp_path):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    import excelmanus.api_routes_sessions as routes

    parent = engine(tmp_path)
    monkeypatch.setattr(routes, "_has_session_access", AsyncMock(return_value=True))
    manager = SimpleNamespace(get_engine=lambda sid: parent, get_or_restore_engine=AsyncMock(return_value=parent))
    monkeypatch.setattr(routes, "get_session_manager", lambda: manager)
    app = FastAPI()
    app.include_router(routes.router)
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
        empty = await client.get("/api/v1/sessions/s/task-list")
        assert empty.status_code == 200
        assert empty.json()["task_list"] is None

        parent._task_store.create("补齐订单", ["读取订单", "写入结果"])
        parent._task_store.update_item(0, TaskStatus.IN_PROGRESS)
        listed = await client.get("/api/v1/sessions/s/task-list")
        assert listed.status_code == 200
        payload = listed.json()["task_list"]
        assert payload["title"] == "补齐订单"
        assert [item["title"] for item in payload["items"]] == ["读取订单", "写入结果"]
        assert [item["status"] for item in payload["items"]] == ["in_progress", "pending"]


@pytest.mark.asyncio
async def test_blank_session_task_queries_do_not_restore_an_engine(monkeypatch):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    import excelmanus.api_routes_sessions as routes

    restore = AsyncMock(side_effect=AssertionError("blank chat must stay lightweight"))
    manager = SimpleNamespace(
        get_engine=lambda sid: None,
        get_or_restore_engine=restore,
        chat_history=SimpleNamespace(get_session_meta=lambda sid: {"blank": 1, "message_count": 0}),
    )
    monkeypatch.setattr(routes, "_has_session_access", AsyncMock(return_value=True))
    monkeypatch.setattr(routes, "get_session_manager", lambda: manager)
    app = FastAPI()
    app.include_router(routes.router)
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
        assert (await client.get("/api/v1/sessions/blank/subagents")).json() == {"runs": []}
        assert (await client.get("/api/v1/sessions/blank/task-list")).json() == {"task_list": None}
    restore.assert_not_called()


@pytest.mark.asyncio
async def test_pause_waits_for_inflight_local_tool(monkeypatch, tmp_path):
    import threading
    from excelmanus.tools.registry import ToolDef
    from excelmanus.engine_core.tool_result import ok_result, ToolUiMeta

    parent = engine(tmp_path)
    started, released, finished = threading.Event(), threading.Event(), threading.Event()

    def work():
        started.set()
        released.wait(timeout=5)
        (tmp_path / "local.txt").write_text("已完成当前调用", encoding="utf-8")
        finished.set()
        return ok_result({"output": "done"}, ui_meta=ToolUiMeta(files=["local.txt"]))

    parent.registry.register_tool(ToolDef(
        name="local_work", description="本地操作", input_schema={"type": "object", "properties": {}},
        func=work, write_effect="workspace_write",
    ))
    call = SimpleNamespace(id="local", function=SimpleNamespace(name="local_work", arguments="{}"))
    child_client(monkeypatch, AsyncMock(return_value=SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="", tool_calls=[call]))],
    )))
    runtime = parent._subagent_runtime
    run_id = await runtime.start_background(SubagentStartRequest(task="本地操作"))
    assert await asyncio.to_thread(started.wait, 2)
    stopping = asyncio.create_task(runtime.interrupt(run_id, pause=True))
    await asyncio.sleep(0.01)
    assert not stopping.done()
    released.set()
    await asyncio.wait_for(stopping, 2)
    assert finished.is_set()
    assert runtime.get_run(run_id)["status"] == "paused"
    assert runtime.get_run(run_id)["changed_files"] == ["./local.txt"]
    assert (tmp_path / "local.txt").read_text(encoding="utf-8") == "已完成当前调用"


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", [True, False])
@pytest.mark.parametrize("multi_select", [True, False])
async def test_waiting_question_can_be_answered_or_paused(monkeypatch, tmp_path, answer, multi_select):
    from excelmanus.interaction import InteractionRegistry

    parent = engine(tmp_path)
    waiting = asyncio.Event()
    original_create = InteractionRegistry.create

    def create_future(self, interaction_id):
        result = original_create(self, interaction_id)
        waiting.set()
        return result

    monkeypatch.setattr(InteractionRegistry, "create", create_future)
    call = SimpleNamespace(id="question", function=SimpleNamespace(name="ask_user", arguments=json.dumps({
        "questions": [{"text": "按哪个口径？", "multi_select": multi_select,
                       "options": [{"label": "华东"}, {"label": "全国"}]}],
    })))
    create = AsyncMock(side_effect=[
        SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="", tool_calls=[call]))]),
        response("已按选择口径完成"),
    ])
    child_client(monkeypatch, create)
    runtime = parent._subagent_runtime
    run_id = await runtime.start_background(SubagentStartRequest(task="需选择口径"))
    await asyncio.wait_for(waiting.wait(), 2)
    row = runtime.get_run(run_id)
    assert row["status"] == "waiting_input"
    assert row["pending_question"]["text"] == "按哪个口径？"
    if answer:
        await runtime.send_message(run_id, "1\n2\n仅统计已确认订单" if multi_select else "华东")
        assert (await runtime.wait(run_id, 2))["status"] == "completed"
        if multi_select:
            messages = create.call_args.kwargs["messages"]
            tool_reply = next(message for message in messages if message.get("tool_call_id") == "question")
            assert "华东" in tool_reply["content"]
            assert "全国" in tool_reply["content"]
            assert "仅统计已确认订单" in tool_reply["content"]
    else:
        await runtime.interrupt(run_id, pause=True)
        assert runtime.get_run(run_id)["status"] == "paused"
        assert create.await_count == 1


@pytest.mark.asyncio
async def test_reads_do_not_replay_old_data_during_background_work(monkeypatch, tmp_path):
    from excelmanus.engine_core.tool_result import ok_result
    from excelmanus.tools.registry import ToolDef

    parent = engine(tmp_path)
    path = tmp_path / "data.txt"
    path.write_text("before", encoding="utf-8")
    parent.registry.register_tool(ToolDef(
        name="read_data", description="读取数据", input_schema={"type": "object", "properties": {}},
        func=lambda: ok_result({"text": path.read_text(encoding="utf-8")}), write_effect="none",
    ))

    async def read():
        call = SimpleNamespace(id="read", function=SimpleNamespace(name="read_data", arguments="{}"))
        return (await parent._execute_tool_call(call, None, None, 1)).structured.value["text"]

    assert await read() == "before"
    assert await read() == "before"
    entered, release = asyncio.Event(), asyncio.Event()

    async def create(**_):
        path.write_text("after", encoding="utf-8")
        entered.set()
        await release.wait()
        return response("changed")

    child_client(monkeypatch, create)
    runtime = parent._subagent_runtime
    run_id = await runtime.start_background(SubagentStartRequest(task="修改数据"))
    await asyncio.wait_for(entered.wait(), 2)
    assert await read() == "after"
    release.set()
    await runtime.wait(run_id, 2)
    assert await read() == "after"


@pytest.mark.asyncio
async def test_clear_stops_jobs_and_clears_persisted_records(monkeypatch, tmp_path):
    db = Database(str(tmp_path / "clear.db"))
    parent = engine(tmp_path, database=db)
    entered, stopped = asyncio.Event(), asyncio.Event()

    async def create(**_):
        entered.set()
        try:
            await asyncio.Future()
        finally:
            stopped.set()

    child_client(monkeypatch, create)
    await parent._subagent_runtime.start_background(SubagentStartRequest(task="待清除"))
    await asyncio.wait_for(entered.wait(), 2)
    assert "已清除" in (await parent.followup("/clear")).reply
    assert stopped.is_set()
    assert parent._subagent_runtime.list_runs() == []
    assert parent._driver.inbox.next_step == ()
    restored = engine(tmp_path, database=db)
    assert restored.restore_session_snapshot()
    assert restored._subagent_runtime.list_runs() == []
    db.close()


@pytest.mark.asyncio
async def test_queued_background_run_receives_message_and_uses_existing_concurrency(monkeypatch, tmp_path):
    parent = engine(tmp_path, parallel_subagent_max=1)
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def create(**kwargs):
        calls.append(kwargs["messages"])
        if len(calls) == 1:
            entered.set()
            await release.wait()
        return response("finished")

    child_client(monkeypatch, create)
    runtime = parent._subagent_runtime
    first = await runtime.start_background(SubagentStartRequest(task="先处理"))
    await asyncio.wait_for(entered.wait(), 2)
    second = await runtime.start_background(SubagentStartRequest(task="后处理"))
    assert runtime.get_run(second)["status"] == "queued"
    await runtime.send_message(second, "排队期间补充的要求")
    assert len(calls) == 1
    release.set()
    assert (await runtime.wait(first, 2))["status"] == "completed"
    assert (await runtime.wait(second, 2))["status"] == "completed"
    assert any(m["content"] == "排队期间补充的要求" for m in calls[1])
