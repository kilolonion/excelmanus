"""DashboardRenderer 单元测试。

覆盖：
- start_turn / handle_event / finish_turn / fail_turn 生命周期
- header 包含模型名、回合号、路由模式
- body 时间线包含工具名和结果
- footer 状态条包含动态状态
- subagent 生命周期信息完整显示
- 窄终端退化为紧凑文本
- 渲染异常降级不崩溃
"""

from __future__ import annotations

from io import StringIO
from unittest.mock import MagicMock

import pytest
from rich.console import Console

from excelmanus.events import EventType, ToolCallEvent
from excelmanus.renderer_dashboard import DashboardRenderer


def _make_console(width: int = 120) -> Console:
    """创建捕获输出的 Console 实例。"""
    return Console(file=StringIO(), width=width, force_terminal=True)


def _get_output(console: Console) -> str:
    """提取 Console 已写入的输出文本。"""
    console.file.seek(0)
    return console.file.read()


# ══════════════════════════════════════════════════════════
# 生命周期测试
# ══════════════════════════════════════════════════════════


class TestDashboardRendererLifecycle:
    def test_start_turn_sets_state(self) -> None:
        c = _make_console()
        r = DashboardRenderer(c)
        r.start_turn(turn_number=1, model_name="gpt-4o")
        assert r.state.turn_number == 1
        assert r.state.model_name == "gpt-4o"
        assert r.state.status == "thinking"

    def test_finish_turn_resets_status(self) -> None:
        c = _make_console()
        r = DashboardRenderer(c)
        r.start_turn(turn_number=1, model_name="m")
        r.finish_turn(elapsed_seconds=1.5, total_tokens=100)
        output = _get_output(c)
        # 应输出摘要信息
        assert "1" in output  # turn number or some metric

    def test_fail_turn_shows_error(self) -> None:
        c = _make_console()
        r = DashboardRenderer(c)
        r.start_turn(turn_number=1, model_name="m")
        r.fail_turn(error="连接超时")
        output = _get_output(c)
        assert "连接超时" in output


# ══════════════════════════════════════════════════════════
# Header 测试
# ══════════════════════════════════════════════════════════


class TestDashboardHeader:
    def test_header_contains_model_and_turn(self) -> None:
        c = _make_console()
        r = DashboardRenderer(c)
        r.start_turn(turn_number=3, model_name="qwen-max")
        # Route event to set skills
        r.handle_event(ToolCallEvent(
            event_type=EventType.ROUTE_END,
            route_mode="skill_activated",
            skills_used=["data_basic"],
        ))
        output = _get_output(c)
        assert "qwen-max" in output
        assert "3" in output

    def test_header_contains_route_info(self) -> None:
        c = _make_console()
        r = DashboardRenderer(c)
        r.start_turn(turn_number=1, model_name="m")
        r.handle_event(ToolCallEvent(
            event_type=EventType.ROUTE_END,
            route_mode="fallback",
            skills_used=[],
        ))
        output = _get_output(c)
        assert "fallback" in output or "通用" in output


# ══════════════════════════════════════════════════════════
# Body 时间线测试
# ══════════════════════════════════════════════════════════


class TestDashboardTimeline:
    def test_tool_call_appears_in_timeline(self) -> None:
        c = _make_console()
        r = DashboardRenderer(c)
        r.start_turn(turn_number=1, model_name="m")
        r.handle_event(ToolCallEvent(
            event_type=EventType.TOOL_CALL_START,
            tool_name="read_excel",
            arguments={"file_path": "test.xlsx"},
        ))
        r.handle_event(ToolCallEvent(
            event_type=EventType.TOOL_CALL_END,
            tool_name="read_excel",
            success=True,
            result="读取成功",
        ))
        output = _get_output(c)
        assert "read_excel" in output

    def test_tool_failure_shown(self) -> None:
        c = _make_console()
        r = DashboardRenderer(c)
        r.start_turn(turn_number=1, model_name="m")
        r.handle_event(ToolCallEvent(
            event_type=EventType.TOOL_CALL_START,
            tool_name="write_excel",
            arguments={},
        ))
        r.handle_event(ToolCallEvent(
            event_type=EventType.TOOL_CALL_END,
            tool_name="write_excel",
            success=False,
            error="权限不足",
        ))
        output = _get_output(c)
        assert "write_excel" in output
        assert "权限不足" in output or "❌" in output


# ══════════════════════════════════════════════════════════
# Footer 状态条测试
# ══════════════════════════════════════════════════════════


class TestDashboardFooter:
    def test_thinking_status(self) -> None:
        c = _make_console()
        r = DashboardRenderer(c)
        r.start_turn(turn_number=1, model_name="m")
        r.handle_event(ToolCallEvent(
            event_type=EventType.THINKING,
            thinking="正在分析数据结构",
        ))
        # Status should reflect thinking
        assert r.state.status in ("thinking", "idle")

    def test_tool_exec_status(self) -> None:
        c = _make_console()
        r = DashboardRenderer(c)
        r.start_turn(turn_number=1, model_name="m")
        r.handle_event(ToolCallEvent(
            event_type=EventType.TOOL_CALL_START,
            tool_name="read_excel",
        ))
        assert r.state.status == "tool_exec"


# ══════════════════════════════════════════════════════════
# Subagent 生命周期测试
# ══════════════════════════════════════════════════════════


class TestDashboardSubagent:
    def test_subagent_start_updates_state(self) -> None:
        c = _make_console()
        r = DashboardRenderer(c)
        r.start_turn(turn_number=1, model_name="m")
        r.handle_event(ToolCallEvent(
            event_type=EventType.SUBAGENT_START,
            subagent_name="writer",
            subagent_reason="写入大批量数据",
            subagent_tools=["write_excel", "format_cells"],
            subagent_permission_mode="restricted",
            subagent_conversation_id="conv_001",
        ))
        assert r.state.subagent_active is True
        assert r.state.subagent_name == "writer"
        output = _get_output(c)
        assert "writer" in output

    def test_subagent_iteration_tracks_stats(self) -> None:
        c = _make_console()
        r = DashboardRenderer(c)
        r.start_turn(turn_number=1, model_name="m")
        r.handle_event(ToolCallEvent(
            event_type=EventType.SUBAGENT_START,
            subagent_name="writer",
        ))
        r.handle_event(ToolCallEvent(
            event_type=EventType.SUBAGENT_ITERATION,
            subagent_name="writer",
            subagent_iterations=2,
            subagent_tool_calls=5,
        ))
        assert r.state.subagent_turns == 2
        assert r.state.subagent_tool_calls == 5

    def test_subagent_end_clears_active(self) -> None:
        c = _make_console()
        r = DashboardRenderer(c)
        r.start_turn(turn_number=1, model_name="m")
        r.handle_event(ToolCallEvent(
            event_type=EventType.SUBAGENT_START,
            subagent_name="writer",
        ))
        r.handle_event(ToolCallEvent(
            event_type=EventType.SUBAGENT_END,
            subagent_name="writer",
            subagent_success=True,
            subagent_iterations=3,
            subagent_tool_calls=8,
        ))
        assert r.state.subagent_active is False
        output = _get_output(c)
        assert "writer" in output

    def test_subagent_summary_rendered(self) -> None:
        c = _make_console()
        r = DashboardRenderer(c)
        r.start_turn(turn_number=1, model_name="m")
        r.handle_event(ToolCallEvent(
            event_type=EventType.SUBAGENT_SUMMARY,
            subagent_name="writer",
            subagent_summary="成功写入了100行数据到Sheet1",
        ))
        output = _get_output(c)
        assert "100" in output or "写入" in output


# ══════════════════════════════════════════════════════════
# 窄终端退化测试
# ══════════════════════════════════════════════════════════


class TestDashboardNarrowTerminal:
    def test_narrow_terminal_no_crash(self) -> None:
        """窄终端下不应崩溃，信息不丢失。"""
        c = _make_console(width=40)
        r = DashboardRenderer(c)
        r.start_turn(turn_number=1, model_name="m")
        r.handle_event(ToolCallEvent(
            event_type=EventType.TOOL_CALL_START,
            tool_name="read_excel",
            arguments={"file_path": "test.xlsx"},
        ))
        r.handle_event(ToolCallEvent(
            event_type=EventType.TOOL_CALL_END,
            tool_name="read_excel",
            success=True,
        ))
        r.finish_turn(elapsed_seconds=0.5, total_tokens=50)
        output = _get_output(c)
        assert "read_excel" in output


# ══════════════════════════════════════════════════════════
# 渲染异常降级测试
# ══════════════════════════════════════════════════════════


class TestDashboardRenderFallback:
    def test_render_exception_does_not_crash(self) -> None:
        """渲染异常时不崩溃，降级为纯文本。"""
        c = _make_console()
        r = DashboardRenderer(c)
        r.start_turn(turn_number=1, model_name="m")
        # Inject a broken event that might cause render issues
        event = ToolCallEvent(event_type=EventType.TOOL_CALL_START)
        event.tool_name = ""  # empty tool name
        # Should not raise
        r.handle_event(event)
