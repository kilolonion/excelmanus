"""模式由用户命令与 plan 状态机拥有，不再注册 suggest_mode_switch。"""

from __future__ import annotations

from pathlib import Path

from excelmanus.config import ExcelManusConfig
from excelmanus.engine import AgentEngine
from excelmanus.tools.registry import ToolRegistry


def _make_engine() -> AgentEngine:
    cfg = ExcelManusConfig(
        api_key="test-key",
        base_url="https://test.example.com/v1",
        model="test-model",
        max_iterations=20,
        max_consecutive_failures=3,
        workspace_root=str(Path(__file__).resolve().parent),
    )
    return AgentEngine(config=cfg, registry=ToolRegistry())


def test_suggest_mode_switch_not_in_catalog() -> None:
    engine = _make_engine()
    names = {item["function"]["name"] for item in engine._meta_tool_builder.build_meta_tools()}
    assert "suggest_mode_switch" not in names


def test_suggest_mode_switch_handler_removed() -> None:
    import excelmanus.engine_core.tool_handlers as handlers

    assert not hasattr(handlers, "SuggestModeSwitchHandler")
