"""Tests for /tools and /reasoning commands and their SSE serialization."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from excelmanus.events import EventType, ToolCallEvent
from excelmanus.api_sse import sse_event_to_sse, _summarize_tool_args


# ── _summarize_tool_args 单元测试 ─────────────────────────


class TestSummarizeToolArgs:
    """_summarize_tool_args helper function tests."""

    def test_empty_args(self):
        assert _summarize_tool_args("read_cells", {}) == "read_cells()"

    def test_none_args(self):
        assert _summarize_tool_args("read_cells", None) == "read_cells()"

    def test_basic_args(self):
        result = _summarize_tool_args("read_cells", {"sheet": "Sheet1", "range": "A1:D10"})
        assert result == 'read_cells(sheet="Sheet1", range="A1:D10")'

    def test_skip_none_and_empty(self):
        result = _summarize_tool_args("write_cells", {"sheet": "S1", "empty": "", "none_val": None, "range": "A1"})
        assert "empty" not in result
        assert "none_val" not in result
        assert 'sheet="S1"' in result
        assert 'range="A1"' in result

    def test_code_param_shows_line_count(self):
        code = "import os\nprint('hello')\nprint('world')"
        result = _summarize_tool_args("run_code", {"code": code})
        assert "code=<3行>" in result

    def test_long_value_truncated(self):
        long_val = "x" * 100
        result = _summarize_tool_args("tool", {"key": long_val})
        assert '..."' in result
        assert len(result) < 200

    def test_total_summary_truncated(self):
        args = {f"key_{i}": f"value_{i}_{'x' * 40}" for i in range(20)}
        result = _summarize_tool_args("big_tool", args)
        assert len(result) <= 200
        assert result.endswith("...")


# ── SSE 序列化测试 ─────────────────────────────────────────


class TestNoticeSSESerialization:
    """TOOL_CALL_NOTICE and REASONING_NOTICE SSE serialization."""

    def _default_path_fn(self, path: str) -> str:
        return path

    def test_tool_call_notice_serialized(self):
        event = ToolCallEvent(
            event_type=EventType.TOOL_CALL_NOTICE,
            tool_call_id="call_123",
            tool_name="read_cells",
            arguments={"sheet": "Sheet1", "range": "A1:D10"},
            iteration=1,
        )
        result = sse_event_to_sse(event, public_path_fn=self._default_path_fn)
        assert result is not None
        assert "event: tool_call_notice" in result
        lines = result.strip().split("\n")
        data_line = [l for l in lines if l.startswith("data: ")][0]
        data = json.loads(data_line[6:])
        assert data["tool_name"] == "read_cells"
        assert "args_summary" in data
        assert "read_cells(" in data["args_summary"]
        assert data["iteration"] == 1

    def test_reasoning_notice_serialized(self):
        event = ToolCallEvent(
            event_type=EventType.REASONING_NOTICE,
            thinking="Let me analyze the data structure...",
            iteration=2,
        )
        result = sse_event_to_sse(event, public_path_fn=self._default_path_fn)
        assert result is not None
        assert "event: reasoning_notice" in result
        lines = result.strip().split("\n")
        data_line = [l for l in lines if l.startswith("data: ")][0]
        data = json.loads(data_line[6:])
        assert "analyze the data" in data["content"]
        assert data["iteration"] == 2

    def test_notice_and_thinking_events_always_emit(self):
        """Notice 与 THINKING 事件一律下发，不再按安全模式过滤。"""
        tool_event = ToolCallEvent(
            event_type=EventType.TOOL_CALL_NOTICE,
            tool_name="read_cells",
            arguments={},
            iteration=0,
        )
        reasoning_event = ToolCallEvent(
            event_type=EventType.REASONING_NOTICE,
            thinking="thinking...",
            iteration=0,
        )
        thinking_event = ToolCallEvent(
            event_type=EventType.THINKING,
            thinking="thinking...",
            iteration=0,
        )
        assert sse_event_to_sse(tool_event, public_path_fn=self._default_path_fn) is not None
        assert sse_event_to_sse(reasoning_event, public_path_fn=self._default_path_fn) is not None
        assert sse_event_to_sse(thinking_event, public_path_fn=self._default_path_fn) is not None


# ── CommandHandler 测试 ────────────────────────────────────


class TestToolsReasoningCommandHandler:
    """Test /tools and /reasoning command handling logic."""

    def _make_handler(self):
        """Create a CommandHandler with a mock engine."""
        from excelmanus.engine_core.command_handler import CommandHandler

        engine = MagicMock()
        engine._show_tool_calls = False
        engine._show_reasoning = False
        handler = CommandHandler(engine)
        return handler, engine

    def test_tools_on(self):
        handler, engine = self._make_handler()
        result = handler._handle_tools_command(["/tools", "on"])
        assert engine._show_tool_calls is True
        assert "开启" in result

    def test_tools_off(self):
        handler, engine = self._make_handler()
        engine._show_tool_calls = True
        result = handler._handle_tools_command(["/tools", "off"])
        assert engine._show_tool_calls is False
        assert "关闭" in result

    def test_tools_status(self):
        handler, engine = self._make_handler()
        engine._show_tool_calls = True
        result = handler._handle_tools_command(["/tools", "status"])
        assert "开启" in result

    def test_tools_toggle(self):
        handler, engine = self._make_handler()
        engine._show_tool_calls = False
        result = handler._handle_tools_command(["/tools"])
        assert engine._show_tool_calls is True
        assert "开启" in result

    def test_tools_invalid_args(self):
        handler, _ = self._make_handler()
        result = handler._handle_tools_command(["/tools", "on", "extra"])
        assert "无效" in result

    def test_reasoning_on(self):
        handler, engine = self._make_handler()
        result = handler._handle_reasoning_command(["/reasoning", "on"])
        assert engine._show_reasoning is True
        assert "开启" in result

    def test_reasoning_off(self):
        handler, engine = self._make_handler()
        engine._show_reasoning = True
        result = handler._handle_reasoning_command(["/reasoning", "off"])
        assert engine._show_reasoning is False
        assert "关闭" in result

    def test_reasoning_status(self):
        handler, engine = self._make_handler()
        engine._show_reasoning = False
        result = handler._handle_reasoning_command(["/reasoning", "status"])
        assert "关闭" in result

    def test_reasoning_toggle(self):
        handler, engine = self._make_handler()
        engine._show_reasoning = False
        result = handler._handle_reasoning_command(["/reasoning"])
        assert engine._show_reasoning is True

    def test_reasoning_invalid_args(self):
        handler, _ = self._make_handler()
        result = handler._handle_reasoning_command(["/reasoning", "on", "extra"])
        assert "无效" in result


# ── EventType 枚举测试 ────────────────────────────────────


class TestEventTypeEnum:
    """Verify new event types exist."""

    def test_tool_call_notice_exists(self):
        assert EventType.TOOL_CALL_NOTICE.value == "tool_call_notice"

    def test_reasoning_notice_exists(self):
        assert EventType.REASONING_NOTICE.value == "reasoning_notice"
