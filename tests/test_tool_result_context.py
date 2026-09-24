"""Recovery advice keeps tool provenance through projection, persistence and replay."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from excelmanus.chat_history import ChatHistoryStore
from excelmanus.config import ExcelManusConfig
from excelmanus.database import Database
from excelmanus.engine_types import ToolCallResult
from excelmanus.memory import ConversationMemory
from excelmanus.session_log import OP_REPLACE, SessionEventLog
from excelmanus.system_one.host import maybe_advise_after_tools


def _config(**overrides) -> ExcelManusConfig:
    return ExcelManusConfig(
        api_key="offline", base_url="https://offline.invalid/v1", model="test",
        **overrides,
    )


def _memory() -> ConversationMemory:
    memory = ConversationMemory(_config())
    memory.add_user_message("核对收款表")
    memory.add_tool_call("failed-1", "observe_spreadsheet", "{}")
    memory.add_tool_result("failed-1", "原始失败详情", projection_content="精简失败详情")
    return memory


def _annotate(memory: ConversationMemory, text: str = "刷新已失效的版本号") -> bool:
    return memory.annotate_tool_result("failed-1", source="Jev 错误恢复", text=text)


def test_annotation_preserves_raw_and_compact_results_without_new_messages() -> None:
    memory = _memory()
    before = deepcopy(memory.messages)
    assert _annotate(memory)
    assert len(memory.messages) == len(before)
    assert memory.messages[:-1] == before[:-1]
    assert memory.messages[-1]["content"] == "原始失败详情"
    assert memory.messages[-1]["_projection_content"] == "精简失败详情"
    assert memory.messages[-1]["message_id"] == before[-1]["message_id"]
    for method, expected in ((memory.get_messages, "原始失败详情"), (memory.project_for_request, "精简失败详情")):
        projected = method(["system"])
        tool = projected[-1]
        assert len(projected) == len(before) + 1
        assert tool["role"] == "tool"
        assert tool["tool_call_id"] == "failed-1"
        assert tool["content"].startswith(expected)
        assert "Jev 错误恢复" in tool["content"]
        assert "非用户要求，不改变任务或授权" in tool["content"]
        assert "刷新已失效的版本号" in tool["content"]
        assert "_tool_result_context" not in tool
        assert [m["content"] for m in projected if m["role"] == "user"] == ["核对收款表"]


@pytest.mark.parametrize("call_id,source,text", [
    ("missing", "Jev", "建议"), ("", "Jev", "建议"), ("failed-1", "", "建议"), ("failed-1", "Jev", ""),
])
def test_annotation_without_a_matching_result_or_attribution_does_not_inject(call_id: str, source: str, text: str) -> None:
    memory = _memory()
    before = deepcopy(memory.messages)
    assert not memory.annotate_tool_result(call_id, source=source, text=text)
    assert memory.messages == before


@pytest.mark.parametrize("replacement", ["已执行成功", "原始失败详情"])
def test_replacing_a_result_clears_obsolete_advice_even_if_text_is_unchanged(replacement: str) -> None:
    memory = _memory()
    assert _annotate(memory)
    memory.project_for_request(["system"])
    assert memory.replace_tool_result("failed-1", replacement)
    assert memory.messages[-1]["content"] == replacement
    assert "_tool_result_context" not in memory.messages[-1]
    assert "_projection_content" not in memory.messages[-1]
    assert memory._projection_dirty
    assert memory.project_for_request(["system"])[-1]["content"] == replacement


def test_annotation_roundtrips_event_log_as_a_replace_not_an_extra_turn(tmp_path: Path) -> None:
    memory = ConversationMemory(_config())
    log = SessionEventLog("recovery")
    memory.attach_event_log(log)
    memory.add_user_message("核对收款表")
    memory.add_tool_call("failed-1", "observe_spreadsheet", "{}")
    memory.add_tool_result("failed-1", "原始失败详情", projection_content="精简失败详情")
    assert _annotate(memory)
    assert len(log.events) == 4
    assert log.events[-1].kind == "tool/result"
    assert log.events[-1].surface_op == OP_REPLACE
    assert log.events[-1].source_seqs == (3,)
    assert len(log.surface_messages()) == 3
    assert _annotate(memory)
    assert len(log.events) == 4  # Repeating identical advice is idempotent.
    database = Database(str(tmp_path / "history.db"))
    try:
        store = ChatHistoryStore(database)
        store.create_session("recovery")
        store.save_events("recovery", [event.to_row() for event in log.pending_events()])
        restored = ConversationMemory(_config())
        restored.load_from_log(SessionEventLog("recovery", events=store.iter_events("recovery")))
        assert len(restored.messages) == 3
        assert restored.messages[-1]["content"] == "原始失败详情"
        assert restored.messages[-1]["_projection_content"] == "精简失败详情"
        assert restored.project_for_request(["system"])[-1]["content"] == memory.project_for_request(["system"])[-1]["content"]
        assert restored.replace_tool_result("failed-1", "已执行成功")
        assert restored.event_log.surface_messages()[-1]["content"] == "已执行成功"
        assert "_tool_result_context" not in restored.event_log.surface_messages()[-1]
    finally:
        database.close()


def test_annotation_roundtrips_legacy_message_snapshot(tmp_path: Path) -> None:
    memory = _memory()
    assert _annotate(memory)
    database = Database(str(tmp_path / "history.db"))
    try:
        store = ChatHistoryStore(database)
        store.create_session("recovery")
        store.save_turn_messages("recovery", memory.messages, turn_number=1)
        loaded = store.load_messages("recovery")
        assert len(loaded) == len(memory.messages)
        assert loaded[-1]["content"] == "原始失败详情"
        assert loaded[-1]["_tool_result_context"] == memory.messages[-1]["_tool_result_context"]
        assert all(m["role"] == original["role"] for m, original in zip(loaded, memory.messages))
        log = SessionEventLog("recovery")
        for message in loaded:
            log.append("legacy/import", message)
        restored = ConversationMemory(_config())
        restored.load_from_log(log)
        assert "刷新已失效的版本号" in restored.project_for_request(["system"])[-1]["content"]
    finally:
        database.close()


def test_annotation_updates_token_estimates_and_invalidates_old_usage_anchor() -> None:
    memory = _memory()
    original_count = memory._count_message(memory.messages[-1])
    memory.project_for_request(["system"])
    memory.note_provider_prompt_tokens(50000)
    assert memory._total_tokens_with_system_messages(None) == 50000
    assert _annotate(memory, "有意义的错误恢复事实 " * 300)
    assert memory._projection_dirty
    assert memory._count_message(memory.messages[-1]) > original_count + 300
    assert memory._total_tokens_with_system_messages(None) != 50000
    assert memory._usage_anchor is None


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", ["openai", "anthropic", "gemini", "openai_responses"])
async def test_annotation_recompiles_sent_request_with_an_explicit_rewrite_event(protocol: str) -> None:
    from excelmanus.request.compiler import compile_request
    from excelmanus.request.series import series_of
    from tests.test_prepared_request_integration import engine_for

    engine = engine_for(protocol)
    engine.memory.add_tool_call("failed-1", "observe_spreadsheet", "{}")
    engine.memory.add_tool_result("failed-1", "原始失败详情")
    first, error = await compile_request(engine)
    assert error is None
    generation = engine._last_envelope.projection_generation
    series_of(engine).accept(first.header)
    assert _annotate(engine.memory)
    second, error = await compile_request(engine)
    assert error is None, error
    assert engine._last_envelope.projection_generation > generation
    assert second.header.content_identity != first.header.content_identity
    assert {"type": "rollback/edit", "source": "tool_result_projection"} in series_of(engine).events
    assert series_of(engine).last_accepted == first.header
    assert "刷新已失效的版本号" in str(second.provider_body)
    assert len(engine.memory.messages) == 3


@pytest.mark.asyncio
async def test_replacing_sent_result_declares_rewrite_and_keeps_untracked_edits_blocked() -> None:
    from excelmanus.request.compiler import compile_request
    from excelmanus.request.series import series_of
    from tests.test_prepared_request_integration import engine_for

    engine = engine_for()
    engine.memory.add_tool_call("failed-1", "observe_spreadsheet", "{}")
    engine.memory.add_tool_result("failed-1", "待确认")
    first, error = await compile_request(engine)
    assert error is None
    series_of(engine).accept(first.header)
    assert engine.memory.replace_tool_result("failed-1", "确认后执行的真实结果")
    second, error = await compile_request(engine)
    assert error is None
    assert "确认后执行的真实结果" in str(second.provider_body)
    series_of(engine).accept(second.header)
    engine.memory.messages[0]["content"] = "未记录的历史篡改"
    rejected, error = await compile_request(engine)
    assert rejected is None
    assert "前缀" in error
    assert series_of(engine).last_accepted == second.header


@pytest.mark.asyncio
async def test_failed_compilation_preserves_dirty_annotation_for_retry() -> None:
    from excelmanus.request.compiler import compile_request
    from excelmanus.request.series import series_of
    from tests.test_prepared_request_integration import engine_for

    engine = engine_for()
    engine.memory.add_tool_call("failed-1", "observe_spreadsheet", "{}")
    engine.memory.add_tool_result("failed-1", "失败详情")
    first, error = await compile_request(engine)
    assert error is None
    series = series_of(engine)
    series.accept(first.header)
    previous_events = deepcopy(series.events)
    generation = engine._projection_generation
    assert _annotate(engine.memory)
    rejected, error = await compile_request(engine, extra={"extra_body": {"messages": []}})
    assert rejected is None and error
    assert engine.memory._projection_dirty
    assert engine._projection_generation == generation
    assert series.events == previous_events
    retried, error = await compile_request(engine)
    assert error is None
    assert "刷新已失效的版本号" in str(retried.provider_body)
    assert engine._projection_generation > generation
    assert sum(event.get("source") == "tool_result_projection" for event in series.events) == 1


@pytest.mark.asyncio
async def test_annotating_a_new_result_does_not_break_the_existing_request_prefix() -> None:
    from excelmanus.request.compiler import compile_request
    from excelmanus.request.series import series_of
    from tests.test_prepared_request_integration import engine_for

    engine = engine_for()
    first, error = await compile_request(engine)
    assert error is None
    series_of(engine).accept(first.header)
    engine.memory.add_tool_call("failed-1", "observe_spreadsheet", "{}")
    engine.memory.add_tool_result("failed-1", "失败详情")
    assert _annotate(engine.memory)
    assert not engine.memory._projection_dirty
    second, error = await compile_request(engine)
    assert error is None
    assert second.header.content_payload.startswith(first.header.content_payload)
    assert second.header.prompt_cache_key == first.header.prompt_cache_key
    assert not any(event.get("source") == "tool_result_projection" for event in series_of(engine).events)


def _host(memory: ConversationMemory) -> SimpleNamespace:
    return SimpleNamespace(
        config=_config(), _is_host_session=True, _subagent_config=None,
        _memory=memory, memory=memory,
        _recovery_hint={"next": "inspect_more", "source": "jev", "delivered": False},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["observe_spreadsheet", "apply_spreadsheet_changes", "apply_spreadsheet_changes"])
async def test_successful_reads_and_writes_do_not_evaluate_or_inject_advice(tool_name: str) -> None:
    memory = _memory()
    engine = _host(memory)
    before = deepcopy(memory.messages)
    with (
        patch("excelmanus.system_one.host._jev_connected", return_value=True),
        patch("excelmanus.system_one.host.maybe_suggest_recovery", AsyncMock()) as suggest,
        patch("excelmanus.system_one.evaluate", AsyncMock()) as evaluate,
    ):
        assert await maybe_advise_after_tools(engine, [ToolCallResult(tool_name, {}, "ok", True)]) == ""
    suggest.assert_not_awaited()
    evaluate.assert_not_awaited()
    assert memory.messages == before


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_id,delivered", [("failed-1", True), ("missing", False), ("", False)])
async def test_recovery_advice_is_attached_only_to_the_supplied_failed_result(failed_id: str, delivered: bool) -> None:
    memory = _memory()
    engine = _host(memory)
    before_count = len(memory.messages)
    with (
        patch("excelmanus.system_one.host._jev_connected", return_value=True),
        patch("excelmanus.system_one.host.maybe_suggest_recovery", AsyncMock(return_value="核对工具错误")),
        patch("excelmanus.system_one.host.record_host_effect", Mock()) as record,
    ):
        reply = await maybe_advise_after_tools(
            engine, [ToolCallResult("observe_spreadsheet", {}, "failed", False, error="failed")],
            failed_tool_call_id=failed_id,
        )
    assert reply == ""
    assert engine._recovery_hint["delivered"] is delivered
    assert len(memory.messages) == before_count
    assert ("_tool_result_context" in memory.messages[-1]) is delivered
    assert record.call_count == int(delivered)


@pytest.mark.asyncio
async def test_breaker_advice_is_returned_without_mutating_tool_or_user_messages() -> None:
    memory = _memory()
    engine = _host(memory)
    before = deepcopy(memory.messages)
    with (
        patch("excelmanus.system_one.host._jev_connected", return_value=True),
        patch("excelmanus.system_one.host.maybe_suggest_recovery", AsyncMock(return_value="终止重试，报告原因")),
    ):
        reply = await maybe_advise_after_tools(
            engine, [ToolCallResult("observe_spreadsheet", {}, "failed", False, error="failed")],
            failed_tool_call_id="failed-1", breaker_triggered=True,
        )
    assert reply == "终止重试，报告原因"
    assert memory.messages == before


@pytest.mark.asyncio
@pytest.mark.parametrize("parallel", [False, True])
async def test_real_loop_attaches_recovery_to_the_actual_failed_call_before_next_request(tmp_path: Path, parallel: bool) -> None:
    from excelmanus.agent.loop import run_tool_loop
    from excelmanus.engine import AgentEngine
    from excelmanus.skillpacks import SkillMatchResult
    from excelmanus.tools import ToolRegistry

    engine = AgentEngine(_config(
        workspace_root=str(tmp_path), max_iterations=3, max_consecutive_failures=3,
        parallel_readonly_tools=parallel,
    ), ToolRegistry())
    engine.memory.add_user_message("核对收款表")
    calls = [SimpleNamespace(
        id=call_id, function=SimpleNamespace(name="observe_spreadsheet", arguments='{"file_path":"book.xlsx"}'),
    ) for call_id in ("first-failure", "actual-last-failure")]
    first_response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=None, tool_calls=calls))])
    final_response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="已说明错误和下一步", tool_calls=None))])
    create = AsyncMock(side_effect=[first_response, final_response])
    engine._client.chat.completions.create = create
    results = [ToolCallResult("observe_spreadsheet", {}, f"原始错误 {index}", False, error="failed") for index in (1, 2)]
    engine._execute_tool_call = AsyncMock(side_effect=results)

    async def parallel_results(tool_calls, *_args, **_kwargs):
        return list(zip(tool_calls, results))

    engine._execute_tool_calls_parallel = AsyncMock(side_effect=parallel_results)

    async def suggest(_engine, _results, **_kwargs):
        _engine._recovery_hint = {"next": "inspect_more", "source": "jev", "delivered": False}
        return "核对失败位置再继续"

    with (
        patch("excelmanus.system_one.host._jev_connected", return_value=True),
        patch("excelmanus.system_one.host.maybe_suggest_recovery", AsyncMock(side_effect=suggest)) as recovery,
        patch("excelmanus.system_one.host.record_host_effect", Mock()),
        patch("excelmanus.system_one.evaluate", AsyncMock(side_effect=AssertionError("remote evaluation forbidden"))),
    ):
        result = await run_tool_loop(engine, SkillMatchResult(skills_used=[], route_mode="fallback", system_contexts=[]), on_event=None)
    assert result.reply == "已说明错误和下一步"
    assert create.await_count == 2
    recovery.assert_awaited_once()
    assert recovery.call_args.args[1] == results
    if parallel:
        engine._execute_tool_calls_parallel.assert_awaited_once()
    else:
        assert engine._execute_tool_call.await_count == 2
    tool_messages = {m["tool_call_id"]: m for m in engine.memory.messages if m["role"] == "tool"}
    assert tool_messages["first-failure"]["content"] == "原始错误 1"
    assert "_tool_result_context" not in tool_messages["first-failure"]
    assert tool_messages["actual-last-failure"]["content"] == "原始错误 2"
    assert tool_messages["actual-last-failure"]["_tool_result_context"]["text"] == "核对失败位置再继续"
    sent = create.call_args_list[1].kwargs["messages"]
    last_tool = next(m for m in sent if m.get("tool_call_id") == "actual-last-failure")
    assert last_tool["role"] == "tool"
    assert "原始错误 2" in last_tool["content"]
    assert "核对失败位置再继续" in last_tool["content"]
    assert [m["content"] for m in sent if m["role"] == "user"] == ["核对收款表"]
