"""SuggestModeSwitchHandler 模式切换回归测试。

验证用户确认/拒绝后，_current_chat_mode 和 _tools_cache 是否正确更新。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from excelmanus.config import ExcelManusConfig
from excelmanus.engine import AgentEngine
from excelmanus.tools.registry import ToolRegistry


# ── helpers ──────────────────────────────────────────────────


def _make_config(**overrides) -> ExcelManusConfig:
    defaults = {
        "api_key": "test-key",
        "base_url": "https://test.example.com/v1",
        "model": "test-model",
        "max_iterations": 20,
        "max_consecutive_failures": 3,
        "workspace_root": str(Path(__file__).resolve().parent),
        "backup_enabled": False,
    }
    defaults.update(overrides)
    return ExcelManusConfig(**defaults)


def _make_engine(**overrides) -> AgentEngine:
    cfg = _make_config(**overrides)
    registry = ToolRegistry()
    return AgentEngine(config=cfg, registry=registry)


def _accepted_payload(target_label: str) -> dict:
    """模拟用户选择"切换到X"的回答 payload。"""
    return {
        "selected_options": [
            {"index": 1, "label": f"切换到{target_label}"},
        ],
        "other_text": None,
        "raw_input": "",
    }


def _rejected_payload() -> dict:
    """模拟用户选择"保持当前模式"的回答 payload。"""
    return {
        "selected_options": [
            {"index": 2, "label": "保持当前模式"},
        ],
        "other_text": None,
        "raw_input": "",
    }


# ── tests ────────────────────────────────────────────────────


class TestSuggestModeSwitchHandler:
    """测试 suggest_mode_switch 工具处理器的模式切换逻辑。"""

    @pytest.fixture
    def engine(self):
        e = _make_engine()
        e._current_chat_mode = "read"
        # mock 交互基础设施
        e._question_flow = MagicMock()
        e._question_flow.enqueue = MagicMock(return_value=MagicMock(question_id="q1"))
        e._question_flow.pop_current = MagicMock()
        e._interaction_handler = MagicMock()
        e._interaction_handler.emit_user_question_event = MagicMock()
        e._interaction_registry = MagicMock()
        e._interaction_registry.cleanup_done = MagicMock()
        # 预设非空 tools cache
        e._tools_cache = [{"type": "function", "function": {"name": "dummy"}}]
        return e

    @pytest.mark.asyncio
    async def test_accept_switches_mode(self, engine):
        """用户确认切换后，_current_chat_mode 应更新且 tools_cache 失效。"""
        from excelmanus.engine_core.tool_handlers import SuggestModeSwitchHandler

        handler = SuggestModeSwitchHandler(engine, MagicMock())
        engine.await_question_answer = AsyncMock(
            return_value=_accepted_payload("写入"),
        )

        result = await handler.handle(
            "suggest_mode_switch", "tc1",
            {"target_mode": "write", "reason": "用户要求执行"},
        )

        assert engine._current_chat_mode == "write"
        assert engine._tools_cache is None
        assert "已确认切换" in result.result_str
        assert "写入" in result.result_str

    @pytest.mark.asyncio
    async def test_reject_keeps_mode(self, engine):
        """用户拒绝切换后，_current_chat_mode 不应变化。"""
        from excelmanus.engine_core.tool_handlers import SuggestModeSwitchHandler

        handler = SuggestModeSwitchHandler(engine, MagicMock())
        engine.await_question_answer = AsyncMock(
            return_value=_rejected_payload(),
        )

        result = await handler.handle(
            "suggest_mode_switch", "tc2",
            {"target_mode": "write", "reason": "用户要求执行"},
        )

        assert engine._current_chat_mode == "read"
        assert engine._tools_cache is not None  # 未失效
        assert "保持当前" in result.result_str

    @pytest.mark.asyncio
    async def test_timeout_keeps_mode(self, engine):
        """超时/取消时，模式不应变化。"""
        from excelmanus.engine_core.tool_handlers import SuggestModeSwitchHandler

        handler = SuggestModeSwitchHandler(engine, MagicMock())
        engine.await_question_answer = AsyncMock(
            side_effect=asyncio.TimeoutError(),
        )

        result = await handler.handle(
            "suggest_mode_switch", "tc3",
            {"target_mode": "write", "reason": "test"},
        )

        assert engine._current_chat_mode == "read"
        assert "超时" in result.result_str

    @pytest.mark.asyncio
    async def test_switch_read_to_plan(self, engine):
        """read → plan 切换验证。"""
        from excelmanus.engine_core.tool_handlers import SuggestModeSwitchHandler

        handler = SuggestModeSwitchHandler(engine, MagicMock())
        engine.await_question_answer = AsyncMock(
            return_value=_accepted_payload("计划"),
        )

        result = await handler.handle(
            "suggest_mode_switch", "tc4",
            {"target_mode": "plan", "reason": "任务复杂需先规划"},
        )

        assert engine._current_chat_mode == "plan"
        assert engine._tools_cache is None
        assert "计划" in result.result_str

    @pytest.mark.asyncio
    async def test_switch_write_to_read(self):
        """write → read 切换验证。"""
        from excelmanus.engine_core.tool_handlers import SuggestModeSwitchHandler

        engine = _make_engine()
        engine._current_chat_mode = "write"
        engine._question_flow = MagicMock()
        engine._question_flow.enqueue = MagicMock(return_value=MagicMock(question_id="q5"))
        engine._question_flow.pop_current = MagicMock()
        engine._interaction_handler = MagicMock()
        engine._interaction_handler.emit_user_question_event = MagicMock()
        engine._interaction_registry = MagicMock()
        engine._interaction_registry.cleanup_done = MagicMock()
        engine._tools_cache = [{"dummy": True}]

        handler = SuggestModeSwitchHandler(engine, MagicMock())
        engine.await_question_answer = AsyncMock(
            return_value=_accepted_payload("读取"),
        )

        result = await handler.handle(
            "suggest_mode_switch", "tc5",
            {"target_mode": "read", "reason": "用户只需分析数据"},
        )

        assert engine._current_chat_mode == "read"
        assert engine._tools_cache is None
        assert "读取" in result.result_str

    @pytest.mark.asyncio
    async def test_invalid_target_mode_rejected(self, engine):
        """无效 target_mode 不应导致模式变化。"""
        from excelmanus.engine_core.tool_handlers import SuggestModeSwitchHandler

        handler = SuggestModeSwitchHandler(engine, MagicMock())
        engine.await_question_answer = AsyncMock(
            return_value=_accepted_payload("invalid"),
        )

        result = await handler.handle(
            "suggest_mode_switch", "tc6",
            {"target_mode": "invalid_mode", "reason": "test"},
        )

        assert engine._current_chat_mode == "read"  # 未变化
