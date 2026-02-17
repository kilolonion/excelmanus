"""写入完成门禁（finish_task）单元测试。"""

from __future__ import annotations

import types
from dataclasses import dataclass, field
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from excelmanus.config import ExcelManusConfig
from excelmanus.engine import AgentEngine, ChatResult, _WRITE_TOOL_NAMES
from excelmanus.skillpacks.models import SkillMatchResult
from excelmanus.tools.registry import ToolRegistry


# ── helpers ──────────────────────────────────────────────────

def _make_config(**overrides) -> ExcelManusConfig:
    defaults = {
        "api_key": "test-key",
        "base_url": "https://test.example.com/v1",
        "model": "test-model",
        "max_iterations": 20,
        "max_consecutive_failures": 3,
        "workspace_root": ".",
    }
    defaults.update(overrides)
    return ExcelManusConfig(**defaults)


def _make_engine(**overrides) -> AgentEngine:
    """构建最小化 AgentEngine 实例用于单元测试。"""
    cfg = _make_config(**{k: v for k, v in overrides.items() if k in ExcelManusConfig.__dataclass_fields__})
    registry = ToolRegistry()
    engine = AgentEngine(config=cfg, registry=registry)
    return engine


def _make_route_result(write_hint: str = "unknown", **kwargs) -> SkillMatchResult:
    defaults = dict(
        skills_used=[],
        tool_scope=[],
        route_mode="all_tools",
        system_contexts=[],
    )
    defaults.update(kwargs)
    defaults["write_hint"] = write_hint
    return SkillMatchResult(**defaults)


# ── SkillMatchResult.write_hint 字段测试 ──

class TestWriteHintField:
    def test_default_is_unknown(self):
        result = SkillMatchResult(
            skills_used=[], tool_scope=[], route_mode="test",
        )
        assert result.write_hint == "unknown"

    def test_explicit_may_write(self):
        result = SkillMatchResult(
            skills_used=[], tool_scope=[], route_mode="test",
            write_hint="may_write",
        )
        assert result.write_hint == "may_write"

    def test_explicit_read_only(self):
        result = SkillMatchResult(
            skills_used=[], tool_scope=[], route_mode="test",
            write_hint="read_only",
        )
        assert result.write_hint == "read_only"


# ── ChatResult.write_guard_triggered 字段测试 ──

class TestChatResultWriteGuard:
    def test_default_is_false(self):
        result = ChatResult(reply="ok")
        assert result.write_guard_triggered is False

    def test_explicit_true(self):
        result = ChatResult(reply="ok", write_guard_triggered=True)
        assert result.write_guard_triggered is True


# ── _build_meta_tools finish_task 注入测试 ──

class TestFinishTaskInjection:
    def test_no_finish_task_when_unknown(self):
        engine = _make_engine()
        engine._current_write_hint = "unknown"
        engine._skill_router = None
        tools = engine._build_meta_tools()
        names = [t["function"]["name"] for t in tools]
        assert "finish_task" not in names

    def test_no_finish_task_when_read_only(self):
        engine = _make_engine()
        engine._current_write_hint = "read_only"
        engine._skill_router = None
        tools = engine._build_meta_tools()
        names = [t["function"]["name"] for t in tools]
        assert "finish_task" not in names

    def test_finish_task_injected_when_may_write(self):
        engine = _make_engine()
        engine._current_write_hint = "may_write"
        engine._skill_router = None
        tools = engine._build_meta_tools()
        names = [t["function"]["name"] for t in tools]
        assert "finish_task" in names

    def test_finish_task_schema(self):
        engine = _make_engine()
        engine._current_write_hint = "may_write"
        engine._skill_router = None
        tools = engine._build_meta_tools()
        ft = [t for t in tools if t["function"]["name"] == "finish_task"][0]
        params = ft["function"]["parameters"]
        assert "summary" in params["properties"]
        assert params["required"] == ["summary"]

    def test_finish_task_reaches_model_tools_when_may_write(self):
        engine = _make_engine()
        route_result = _make_route_result(write_hint="may_write")
        engine._current_write_hint = "may_write"
        scope = engine._get_current_tool_scope(route_result=route_result)
        tools = engine._build_tools_for_scope(scope)
        names = [t["function"]["name"] for t in tools]
        assert "finish_task" in names


class TestWriteGuardPrompt:
    @pytest.mark.asyncio
    async def test_write_guard_prompt_requires_select_skill_then_execute(self):
        engine = _make_engine(max_iterations=2)
        route_result = _make_route_result(write_hint="may_write")
        engine._route_skills = AsyncMock(return_value=route_result)
        engine._client.chat.completions.create = AsyncMock(
            side_effect=[
                types.SimpleNamespace(
                    choices=[types.SimpleNamespace(message=types.SimpleNamespace(content="先确认一下", tool_calls=None))]
                ),
                types.SimpleNamespace(
                    choices=[types.SimpleNamespace(message=types.SimpleNamespace(content="仍然不执行", tool_calls=None))]
                ),
            ]
        )

        _ = await engine.chat("先画图再美化")

        user_messages = [
            str(m.get("content", ""))
            for m in engine.memory.get_messages()
            if m.get("role") == "user"
        ]
        assert any("先调用 select_skill 激活可写技能" in msg for msg in user_messages)
