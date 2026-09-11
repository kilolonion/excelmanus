"""Wave D：压缩 / 技能 / plan / 子代理挂在缝上。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from excelmanus.compaction import compact_for_pre_step, recover_request_overflow, surface_fingerprint
from excelmanus.config import ExcelManusConfig
from excelmanus.engine import AgentEngine
from excelmanus.plan_mode import handle_plan_command, request_exit_plan_approval
from excelmanus.prompt.skill_catalog import (
    attach_skill_catalog,
    parse_skill_gesture,
    prepare_skill_followup,
    render_available_skills,
)
from excelmanus.skillpacks.router import SkillMatchResult
from excelmanus.subagent.guard import reject_readonly_write
from excelmanus.subagent.models import SubagentConfig
from excelmanus.tools.plan_tools import exit_plan_mode
from excelmanus.tools.registry import ToolDef, ToolRegistry


def _make_config(**overrides) -> ExcelManusConfig:
    defaults = {
        "api_key": "test-key",
        "base_url": "https://test.example.com/v1",
        "model": "test-model",
        "max_iterations": 8,
        "max_consecutive_failures": 3,
        "workspace_root": str(Path(__file__).resolve().parent),
        "compaction_enabled": True,
    }
    defaults.update(overrides)
    return ExcelManusConfig(**defaults)


def _make_engine() -> AgentEngine:
    return AgentEngine(_make_config(), ToolRegistry())


def _text_response(content: str):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=None))],
    )


def _tool_response(call_id: str, name: str, arguments: str = "{}"):
    tc = SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=arguments))
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="", tool_calls=[tc]))],
    )


def test_parse_skill_gesture_skips_control_commands() -> None:
    assert parse_skill_gesture("/plan on") is None
    assert parse_skill_gesture("/compact") is None
    assert parse_skill_gesture("请用 /data-basic 分析") == "data-basic"


def test_catalog_is_user_message_when_digest_changes() -> None:
    recorded: list[tuple[str, dict]] = []

    def _add(text: str, **kwargs: object) -> None:
        recorded.append((text, kwargs))
        engine._memory.messages.append(text)

    engine = SimpleNamespace(
        _skill_router=SimpleNamespace(
            _loader=SimpleNamespace(
                get_skillpacks=lambda: {
                    "data-basic": SimpleNamespace(
                        description="分析表格",
                        disable_model_invocation=False,
                    )
                }
            )
        ),
        _skill_resolver=None,
        _full_access_enabled=True,
        _skill_catalog_digest=None,
        _memory=SimpleNamespace(messages=[], add_user_message=_add),
    )
    first = attach_skill_catalog(engine)
    assert "<available_skills>" in first
    assert engine._memory.messages[-1] == first
    assert recorded[-1][1].get("hidden") is True
    assert recorded[-1][1].get("prompt_kind") == "skill_catalog"
    second = attach_skill_catalog(engine)
    assert second == ""
    assert engine._memory.messages.count(first) == 1


def test_prepare_skill_followup_does_not_set_tool_scope() -> None:
    memory = SimpleNamespace(messages=[], add_user_message=lambda text: memory.messages.append(text))
    engine = SimpleNamespace(_memory=memory, _skill_router=None)
    route = SkillMatchResult(
        skills_used=["data-basic"],
        tool_scope=["run_code"],
        route_mode="slash_direct",
        system_contexts=["[Skillpack] data-basic\n执行指引：先 overview"],
        parameterized=True,
    )
    rewritten, invocation = prepare_skill_followup(
        engine,
        user_message="/data-basic 销量",
        slash_command="data-basic",
        route_result=route,
    )
    assert rewritten.tool_scope == []
    assert rewritten.route_mode == "all_tools"
    assert rewritten.system_contexts == []
    assert "<skill-invocation name=\"data-basic\">" in invocation
    assert "先 overview" in invocation


@pytest.mark.asyncio
async def test_plan_command_not_in_model_history() -> None:
    engine = _make_engine()
    engine._client.chat.completions.create = AsyncMock(return_value=_text_response("ok"))
    await engine.followup("/plan")
    await engine.followup("继续分析这张表")
    assert engine._plan_active is True
    assert engine._current_chat_mode == "plan"
    user_blobs = [
        str(item.get("content"))
        for item in engine.memory.messages
        if item.get("role") == "user"
    ]
    assert all("/plan" not in blob for blob in user_blobs)
    kwargs = engine._client.chat.completions.create.call_args.kwargs
    sent = [str(m.get("content")) for m in kwargs["messages"] if m.get("role") == "user"]
    assert all("/plan" not in blob for blob in sent)
    assert engine._client.chat.completions.create.await_count == 1


@pytest.mark.asyncio
async def test_default_write_chat_does_not_undo_plan_command() -> None:
    engine = _make_engine()
    engine._client.chat.completions.create = AsyncMock(return_value=_text_response("ok"))
    await engine.followup("/plan on")
    await engine.followup("下一步", chat_mode="write")
    assert engine._plan_active is True
    assert engine._current_chat_mode == "plan"


def test_exit_plan_mode_enqueues_ask_user() -> None:
    engine = _make_engine()
    handle_plan_command(engine, "on")
    result = exit_plan_mode(
        "# 计划\n范围：全表",
        is_plan_active=lambda: True,
        on_submitted=lambda plan: request_exit_plan_approval(engine, plan),
    )
    assert result.success is True
    assert "呈交" in result.model_text
    assert engine._question_flow.has_pending()
    assert engine._pending_plan_exit is not None
    current = engine._question_flow.current()
    assert current is not None
    assert current.header == "退出计划"


@pytest.mark.asyncio
async def test_compact_failure_does_not_rerun_tools() -> None:
    engine = _make_engine()
    calls: list[str] = []

    def ping() -> str:
        calls.append("ping")
        return "pong"

    engine._registry.register_tool(
        ToolDef(
            name="ping",
            description="ping",
            input_schema={"type": "object", "properties": {}},
            func=ping,
        )
    )
    engine._compaction_manager.should_compact = lambda *_a, **_k: True

    async def _boom(**_kwargs):
        raise RuntimeError("compact failed")

    engine._compaction_manager.auto_compact = _boom
    engine._client.chat.completions.create = AsyncMock(
        side_effect=[
            _tool_response("c1", "ping"),
            _text_response("done"),
        ]
    )
    result = await engine.followup("开始")
    assert result.reply == "done"
    assert calls == ["ping"]
    assert engine._last_compact_failed is True


@pytest.mark.asyncio
async def test_recover_overflow_retries_only_when_surface_advances() -> None:
    engine = _make_engine()
    engine._compaction_manager.should_compact = lambda *_a, **_k: True

    async def _noop_compact(**_kwargs):
        return SimpleNamespace(success=False)

    engine._compaction_manager.auto_compact = _noop_compact
    before = surface_fingerprint(engine.memory)
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "b"},
        {"role": "user", "content": "c"},
    ]
    assert await recover_request_overflow(engine, messages) is None
    assert surface_fingerprint(engine.memory) == before

    async def _advance(**_kwargs):
        engine.memory.add_user_message("summary-replaced")
        return SimpleNamespace(success=True)

    engine._compaction_manager.auto_compact = _advance
    trimmed = await recover_request_overflow(engine, messages)
    assert trimmed is not None
    assert trimmed[0]["role"] == "system"


def test_explorer_write_guard_rejects_mutating_tools() -> None:
    cfg = SubagentConfig(name="explorer", description="x", permission_mode="readOnly")
    assert reject_readonly_write(cfg, "edit_spreadsheet")
    assert reject_readonly_write(cfg, "copy_file")
    assert reject_readonly_write(cfg, "run_code") is None
    assert reject_readonly_write(cfg, "inspect_spreadsheet") is None


def test_render_available_skills_skips_disable_model_invocation() -> None:
    text = render_available_skills(
        {
            "visible": SimpleNamespace(description="ok", disable_model_invocation=False),
            "hidden": SimpleNamespace(description="no", disable_model_invocation=True),
        }
    )
    assert "`visible`" in text
    assert "hidden" not in text


@pytest.mark.asyncio
async def test_compact_for_pre_step_always_enters() -> None:
    engine = _make_engine()
    engine._compaction_manager.should_compact = lambda *_a, **_k: True

    async def _boom(**_kwargs):
        raise RuntimeError("nope")

    engine._compaction_manager.auto_compact = _boom
    assert await compact_for_pre_step(engine) == "enter"
    assert engine._last_compact_failed is True
