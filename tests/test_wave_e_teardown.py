"""Wave E：拆除拼盘组装器与默认路径残留。"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from excelmanus.config import ExcelManusConfig
from excelmanus.engine import AgentEngine, ChatResult, _tool_access_from_chat_mode
from excelmanus.prompt.assemble import prepare_system_prompts_for_request
from excelmanus.skillpacks import Skillpack
from excelmanus.subagent.registry import SubagentRegistry
from excelmanus.tools.registry import ToolRegistry


def _make_config(**overrides) -> ExcelManusConfig:
    defaults = {
        "api_key": "test-key",
        "base_url": "https://test.example.com/v1",
        "model": "test-model",
        "max_iterations": 8,
        "max_consecutive_failures": 3,
        "workspace_root": str(Path(__file__).resolve().parent),
    }
    defaults.update(overrides)
    return ExcelManusConfig(**defaults)


def test_context_builder_module_removed() -> None:
    spec = importlib.util.find_spec("excelmanus.engine_core.context_builder")
    assert spec is None


def test_default_system_has_no_file_list_or_mcp_guide() -> None:
    engine = AgentEngine(_make_config(), ToolRegistry())
    engine.state.file_content_versions = {"sales.xlsx": "sha256:abc"}
    prompts, error = engine._prepare_system_prompts_for_request([])
    assert error is None
    blob = "\n".join(prompts)
    assert "工作区文件" not in blob
    assert "文件全景" not in blob
    assert "搜索工具选择指南" not in blob
    assert "工具分类" not in blob
    assert "当前计划与任务清单" not in blob


def test_plan_catalog_matches_write() -> None:
    engine = AgentEngine(_make_config(), ToolRegistry())
    write_names = {
        item["function"]["name"]
        for item in engine._meta_tool_builder.build_v5_tools(
            tool_access=_tool_access_from_chat_mode("write"),
        )
    }
    plan_names = {
        item["function"]["name"]
        for item in engine._meta_tool_builder.build_v5_tools(
            tool_access=_tool_access_from_chat_mode("plan"),
        )
    }
    assert write_names == plan_names
    assert "suggest_mode_switch" not in write_names


def test_verifier_not_builtin() -> None:
    registry = SubagentRegistry(_make_config())
    assert registry.get("verifier") is None


def test_parse_slash_skill_exists() -> None:
    from excelmanus.skillpacks.loader import SkillpackLoader
    from excelmanus.skillpacks.router import SkillRouter

    cfg = _make_config()
    router = SkillRouter(cfg, SkillpackLoader(cfg, ToolRegistry()))
    assert hasattr(router, "parse_slash_skill")


def test_assemble_ignores_file_registry_on_engine() -> None:
    engine = MagicMock()
    engine.memory.system_prompt = "You are ExcelManus."
    engine._prompt_composer = None
    engine._transient_hook_contexts = []
    engine.full_access_enabled = False
    engine.max_context_tokens = 100000
    engine.state.prompt_injection_snapshots = []
    engine.state.injected_context_fingerprint = None
    engine.file_registry = object()
    engine._current_chat_mode = "write"
    engine._present_as = "native"
    engine._runtime_vars = {"workspace_root": "/tmp/ws", "model": "test-model"}
    prompts, error = prepare_system_prompts_for_request(engine, [])
    assert error is None
    assert all("工作区文件" not in block for block in prompts)


@pytest.mark.asyncio
async def test_active_skill_body_not_copied_into_system_contexts() -> None:
    engine = AgentEngine(_make_config(), ToolRegistry())
    engine._active_skills = [
        Skillpack(
            name="demo",
            description="d",
            instructions="SECRET_SKILL_BODY_SHOULD_NOT_BE_SYSTEM",
            source="project",
            root_dir="/tmp/demo",
        )
    ]
    with patch(
        "excelmanus.agent.loop.run_tool_loop",
        new_callable=AsyncMock,
        return_value=ChatResult(reply="ok"),
    ) as loop_mock:
        await engine.followup("继续")
    route = loop_mock.await_args.args[1]
    assert route.system_contexts == []
    assert "SECRET_SKILL_BODY_SHOULD_NOT_BE_SYSTEM" not in str(route.system_contexts)


def test_engine_facade_is_under_300_lines() -> None:
    path = Path(__file__).resolve().parents[1] / "excelmanus" / "engine.py"
    lines = path.read_text(encoding="utf-8").splitlines()
    assert path.is_file()
    assert len(lines) < 300


def test_prompt_composer_module_removed() -> None:
    spec = importlib.util.find_spec("excelmanus.prompt_composer")
    assert spec is None


def test_agent_engine_lives_in_session_module() -> None:
    from excelmanus.agent.session import AgentEngine as SessionAgentEngine

    assert AgentEngine is SessionAgentEngine
    assert AgentEngine.__module__ == "excelmanus.agent.session"
