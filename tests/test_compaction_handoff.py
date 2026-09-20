"""Compaction progress crosses the real message, snapshot and request boundaries."""
from __future__ import annotations

import asyncio
import json
import sqlite3
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from excelmanus.agent.session import AgentEngine
from excelmanus.chat_history import ChatHistoryStore
from excelmanus.compaction import capture_progress, compact_for_pre_step, handoff_from_memory, sync_compaction_boundary
from excelmanus.config import ExcelManusConfig
from excelmanus.database import Database
from excelmanus.engine_core.session_state import SessionState
from excelmanus.request.compiler import compile_request, create_extra_from_engine
from excelmanus.session import SessionManager
from excelmanus.task_list import TaskStatus
from excelmanus.tools.registry import ToolRegistry
from excelmanus.workspace.file_service import WorkspaceFileService


def cfg(tmp_path, **options):
    return ExcelManusConfig(api_key="test", model="test-model", protocol="openai",
        base_url="https://test.invalid/v1", workspace_root=str(tmp_path), memory_enabled=False,
        main_model_vision="false", max_context_tokens=100_000, compaction_keep_recent_turns=1,
        compaction_pruner_enabled=False, **options)


def reply(text="摘要：结果文件已保存，待补充说明。"):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text, tool_calls=None))])


def history(engine):
    for i in range(5):
        engine.memory.add_user_message(f"旧要求 {i}")
        engine.memory.add_assistant_message("已读数据记录。" * 500)
    engine.memory.add_user_message("继续生成报告，保留原数据和公式")


def facts(engine):
    service = WorkspaceFileService(engine.workspace.root_dir)
    receipt = service.create("result.txt", b"committed once", operation_id="saved-write")
    version = receipt.primary_version()
    engine._state.record_affected_file("result.txt")
    engine._state.remember_file_version("result.txt", version)
    engine._state.record_write_operation(tool_name="write_text_file", file_path="result.txt", summary="已提交结果")
    engine._task_store.create("生成报告", ["保存结果", "补充说明"])
    engine._task_store.update_item(0, TaskStatus.IN_PROGRESS)
    engine._task_store.update_item(0, TaskStatus.COMPLETED, "result.txt 已保存")
    engine._driver._turn_record = {"status": "interrupted", "task": "生成报告", "turn_id": "t1"}
    return version


async def compact(engine, **kwargs):
    result = await engine._compaction_manager.manual_compact(
        engine.memory, [{"role": "system", "content": "test"}], client=engine._client,
        summary_model="test-model", progress_provider=lambda: capture_progress(engine), **kwargs)
    if result.success:
        engine.record_compaction_handoff(result.handoff)
        sync_compaction_boundary(engine)
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize("session_log", [False, True])
@pytest.mark.parametrize("snapshot_lags", [False, True])
async def test_sqlite_restore_handoff_and_next_model_input(tmp_path, monkeypatch, session_log, snapshot_lags):
    config = cfg(tmp_path, session_log_enabled=session_log)
    path, copied = tmp_path / "source.db", tmp_path / "restart.db"
    def manager(database):
        return SessionManager(max_sessions=5, ttl_seconds=60, config=config, registry=ToolRegistry(),
            database=database, chat_history=ChatHistoryStore(database))
    original = manager(Database(str(path)))
    sid, engine = await original.acquire_for_chat(None)
    history(engine)
    version = facts(engine)
    engine.save_session_snapshot()
    engine._client.chat.completions.create = AsyncMock(return_value=reply())
    if snapshot_lags:
        monkeypatch.setattr(engine._checkpoint_store, "save_session_snapshot", lambda **_: (_ for _ in ()).throw(OSError("crash gap")))
    text = await engine._command_handler.handle("/compact")
    assert "压缩完成" in text
    artifact, error = handoff_from_memory(engine.memory)
    assert error is None
    assert artifact == engine._state.compaction_handoff
    assert artifact["generation"] == artifact["continuity"]["generation"] == 1
    assert artifact["progress"]["file_versions"]["result.txt"] == version
    assert "补充说明" in artifact["next_step"]
    with sqlite3.connect(path) as src, sqlite3.connect(copied) as dst:
        src.backup(dst)
    restored_manager = manager(Database(str(copied)))
    restored = await restored_manager.get_or_restore_engine(sid)
    assert restored._compaction_handoff == artifact
    assert restored._compaction_generation == 1
    assert restored._task_store.current.items[0].status == TaskStatus.COMPLETED
    import excelmanus.api_routes_sessions as routes
    monkeypatch.setattr(routes, "_has_session_access", AsyncMock(return_value=True))
    monkeypatch.setattr(routes, "get_session_manager", lambda: restored_manager)
    restored._client.chat.completions.create = AsyncMock(return_value=reply("补充说明完成"))
    queried = await routes.get_compaction_handoff(sid, None)
    assert queried["handoff"] == artifact
    restored._client.chat.completions.create.assert_not_awaited()
    await restored.followup("根据已完成的工作继续说明")
    messages = restored._client.chat.completions.create.call_args.kwargs["messages"]
    wire = json.dumps(messages, ensure_ascii=False)
    assert version in wire and "saved-write" not in wire  # Facts contain versions, not invented commit keys.
    assert "保存结果" in wire and "completed" in wire and "补充说明" in wire
    assert "不重放已提交写入" in wire
    assert Path(restored.workspace.root_dir, "result.txt").read_bytes() == b"committed once"
    assert len(WorkspaceFileService(restored.workspace.root_dir).txlog.iter_intents()) == 1
    await original.release_for_chat(sid)


@pytest.mark.asyncio
async def test_handoff_is_detached_and_does_not_restore_after_clear(tmp_path):
    db = Database(str(tmp_path / "state.db"))
    engine = AgentEngine(cfg(tmp_path), ToolRegistry(), database=db)
    engine._session_id = "clear-handoff"
    history(engine)
    facts(engine)
    engine._client.chat.completions.create = AsyncMock(return_value=reply())
    result = await compact(engine)
    assert result.success
    result.handoff["progress"]["task"] = "mutated caller copy"
    assert engine._compaction_handoff["progress"]["task"] == "生成报告"
    status = engine.get_compaction_status()
    status["handoff"]["progress"]["tasks"]["items"].clear()
    assert len(engine._compaction_handoff["progress"]["tasks"]["items"]) == 2
    restored = SessionState.from_dict(engine._state.to_dict())
    assert restored.file_content_versions == engine._state.file_content_versions
    assert restored.write_operations_log == engine._state.write_operations_log
    engine.clear_memory()
    assert engine.get_compaction_status()["handoff"] == {}
    assert engine._compaction_generation == engine.memory._compaction_generation == 0
    second = AgentEngine(cfg(tmp_path), ToolRegistry(), database=db)
    second._session_id = "clear-handoff"
    assert second.restore_session_snapshot()
    assert second._compaction_handoff == {}


@pytest.mark.asyncio
async def test_second_compaction_keeps_host_write_facts_across_turn_reset(tmp_path):
    engine = AgentEngine(cfg(tmp_path), ToolRegistry())
    history(engine)
    version = facts(engine)
    engine._client.chat.completions.create = AsyncMock(return_value=reply())
    first = await compact(engine)
    assert first.success
    engine._state.reset_loop_stats()
    history(engine)
    second = await compact(engine)
    assert second.success
    assert second.handoff["generation"] == 2
    assert second.handoff["handoff_id"] != first.handoff["handoff_id"]
    assert second.handoff["progress"]["affected_files"] == first.handoff["progress"]["affected_files"]
    assert len(second.handoff["progress"]["write_operations"]) == 1
    assert second.handoff["progress"]["file_versions"]["result.txt"] == version
    assert sum("_compaction_handoff" in m for m in engine.memory.messages) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["summary", "progress"])
async def test_corrupt_handoff_blocks_compilation_without_a_model_call(tmp_path, damage):
    engine = AgentEngine(cfg(tmp_path), ToolRegistry())
    history(engine)
    engine._client.chat.completions.create = AsyncMock(return_value=reply())
    assert (await compact(engine)).success
    if damage == "summary":
        engine.memory.messages[1]["content"] = "被修改的摘要"
    else:
        engine.memory.messages[0]["_compaction_handoff"]["progress"]["task"] = "已做完所有事"
    engine._client.chat.completions.create.reset_mock()
    prepared, error = await compile_request(engine)
    assert prepared is None and "不一致" in error
    assert (await compact(engine)).success is False
    engine._client.chat.completions.create.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("edit", ["append", "replace", "cancel"])
async def test_concurrent_history_changes_do_not_get_lost(tmp_path, edit):
    engine = AgentEngine(cfg(tmp_path), ToolRegistry())
    history(engine)
    entered, release = asyncio.Event(), asyncio.Event()
    async def create(**_):
        entered.set()
        await release.wait()
        return reply()
    engine._client.chat.completions.create = create
    job = asyncio.create_task(compact(engine))
    await asyncio.wait_for(entered.wait(), 2)
    if edit == "replace":
        engine.memory.replace_message_content(0, "新的要求")
    if edit == "append":
        engine.memory.add_user_message("新增约束，禁止删除公式")
    before = deepcopy(engine.memory.messages)
    if edit == "cancel":
        job.cancel()
        with pytest.raises(asyncio.CancelledError):
            await job
        assert engine.memory.messages == before
    else:
        release.set()
        result = await job
        if edit == "replace":
            assert not result.success and "历史已变化" in result.error
            assert engine.memory.messages == before
        else:
            assert result.success
            assert engine.memory.messages[-1]["content"] == "新增约束，禁止删除公式"


@pytest.mark.asyncio
async def test_unresolved_tool_not_summarized_and_recent_pair_is_retained(tmp_path):
    engine = AgentEngine(cfg(tmp_path), ToolRegistry())
    engine.memory.add_user_message("第一轮")
    engine.memory.add_tool_call("pending", "write_text_file", "{}")
    history(engine)
    engine._client.chat.completions.create = AsyncMock(return_value=reply())
    result = await compact(engine)
    assert not result.success and "未配对" in result.error
    engine._client.chat.completions.create.assert_not_awaited()
    engine.memory.clear()
    history(engine)
    engine.memory.add_tool_call("current", "write_text_file", "{}")
    assert (await compact(engine)).success
    assert engine.memory.messages[-1]["tool_calls"][0]["id"] == "current"
    assert engine.memory.messages[-2]["content"] == "继续生成报告，保留原数据和公式"


@pytest.mark.asyncio
@pytest.mark.parametrize("empty", [False, True])
async def test_auto_compaction_or_fallback_preserves_progress(tmp_path, empty):
    engine = AgentEngine(cfg(tmp_path), ToolRegistry())
    history(engine)
    facts(engine)
    engine._compaction_manager.should_compact = lambda *_: True
    engine._client.chat.completions.create = AsyncMock(return_value=reply())
    if empty:
        engine._compaction_manager._empty_streak = 3
    await compact_for_pre_step(engine)
    artifact, error = handoff_from_memory(engine.memory)
    assert error is None and artifact
    assert artifact["source"] == ("fallback" if empty else "auto")
    assert engine._compaction_handoff == artifact
    assert engine.memory.messages[-1]["content"] == "继续生成报告，保留原数据和公式"
    if empty:
        engine._client.chat.completions.create.assert_not_awaited()
        assert "未生成语义摘要" in artifact["summary"]


@pytest.mark.asyncio
async def test_compaction_discards_old_responses_continuation(tmp_path):
    engine = AgentEngine(cfg(tmp_path, responses_continuation_enabled=True), ToolRegistry())
    history(engine)
    engine.memory.add_assistant_tool_message({"role": "assistant", "content": "recent", "replay_state": {"response_id": "resp_old"},
        "replay_source": {"protocol": "openai_responses", "model": "test-model"}})
    engine._responses_last_response = {"id": "resp_old", "protocol": "openai_responses", "model": "test-model"}
    engine._compile_extra = {"_responses_previous_response_id": "resp_old"}
    engine._client.chat.completions.create = AsyncMock(return_value=reply())
    assert (await compact(engine)).success
    engine._active_protocol = "openai_responses"
    extra = create_extra_from_engine(engine)
    assert "_responses_previous_response_id" not in extra
    assert "_responses_previous_response_id" not in engine._compile_extra
    engine._responses_last_response = {"id": "resp_new", "protocol": "openai_responses", "model": "test-model", "compaction_generation": 1}
    assert create_extra_from_engine(engine)["_responses_previous_response_id"] == "resp_new"


@pytest.mark.asyncio
async def test_failed_summary_does_not_replace_previous_handoff(tmp_path):
    engine = AgentEngine(cfg(tmp_path), ToolRegistry())
    history(engine)
    engine._client.chat.completions.create = AsyncMock(return_value=reply())
    assert (await compact(engine)).success
    saved = deepcopy(engine._compaction_handoff)
    history(engine)
    engine._client.chat.completions.create = AsyncMock(return_value=reply(""))
    assert not (await compact(engine)).success
    assert engine._compaction_handoff == saved
    assert handoff_from_memory(engine.memory)[0] == saved


@pytest.mark.asyncio
async def test_manual_compactions_are_serialized(tmp_path):
    engine = AgentEngine(cfg(tmp_path), ToolRegistry())
    history(engine)
    entered, release = asyncio.Event(), asyncio.Event()
    active, peak = 0, 0
    async def create(**_):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        entered.set()
        await release.wait()
        active -= 1
        return reply()
    engine._client.chat.completions.create = create
    first = asyncio.create_task(compact(engine))
    await asyncio.wait_for(entered.wait(), 2)
    second = asyncio.create_task(compact(engine))
    await asyncio.sleep(0)
    assert active == peak == 1
    release.set()
    await asyncio.gather(first, second)
    assert peak == 1
    assert handoff_from_memory(engine.memory)[1] is None


@pytest.mark.asyncio
async def test_api_handoff_access_and_busy_compaction(tmp_path, monkeypatch):
    from fastapi import HTTPException
    import excelmanus.api_routes_sessions as routes

    engine = AgentEngine(cfg(tmp_path), ToolRegistry())
    manager = SimpleNamespace(get_or_restore_engine=AsyncMock(return_value=engine))
    monkeypatch.setattr(routes, "get_session_manager", lambda: manager)
    monkeypatch.setattr(routes, "_has_session_access", AsyncMock(return_value=False))
    with pytest.raises(HTTPException) as exc:
        await routes.get_compaction_handoff("no-access", None)
    assert exc.value.status_code == 404
    manager.get_or_restore_engine.assert_not_awaited()
    monkeypatch.setattr(routes, "_has_session_access", AsyncMock(return_value=True))
    engine._command_handler.handle = AsyncMock()
    engine._driver._runner_task = asyncio.create_task(asyncio.Event().wait())
    try:
        result = await routes.compact_session_context("busy", None)
        assert result.status_code == 409
        engine._command_handler.handle.assert_not_awaited()
    finally:
        engine._driver._runner_task.cancel()
        await asyncio.gather(engine._driver._runner_task, return_exceptions=True)


def test_large_progress_reports_omissions_without_mutating_task_store(tmp_path):
    engine = AgentEngine(cfg(tmp_path), ToolRegistry())
    engine._task_store.create("many tasks", [f"Task {i}" + "x" * 600 for i in range(60)])
    progress = capture_progress(engine)
    assert len(progress["tasks"]["items"]) == 50
    assert progress["omitted"]["tasks"] == 10
    assert progress["text_truncated"] is True
    assert len(engine._task_store.current.items) == 60


@pytest.mark.asyncio
async def test_reset_after_rollback_preserves_summary_metadata(tmp_path):
    from excelmanus.conversation_persistence import ConversationPersistence

    db = Database(str(tmp_path / "rollback.db"))
    history_store = ChatHistoryStore(db)
    engine = AgentEngine(cfg(tmp_path), ToolRegistry())
    engine._session_id = "rollback"
    history(engine)
    engine._client.chat.completions.create = AsyncMock(return_value=reply())
    assert (await compact(engine)).success
    history_store.create_session("rollback")
    persistence = ConversationPersistence(history_store)
    persistence.reset_after_rollback("rollback", engine)
    loaded = history_store.load_messages("rollback")
    assert loaded[0]["_compaction_handoff"] == engine._compaction_handoff
    assert loaded[0]["_ui_hidden"] is True
    # A rollback/clear that removes the summary must not resurrect a snapshot copy.
    engine.memory.clear()
    engine.restore_compaction_handoff()
    assert engine._compaction_handoff == {}
