"""DashboardRenderer 单元测试。

覆盖：
- start_turn / handle_event / finish_turn / fail_turn 生命周期
- header 包含模型名、回合号、路由模式
- body 时间线包含工具名和结果
- footer 状态条包含动态状态
- subagent 生命周期信息完整显示
- 窄终端退化为紧凑文本
- 渲染异常降级不崩溃
- Live 状态栏在流式输出时暂停/恢复
"""

from __future__ import annotations

from io import StringIO

import pytest
from rich.console import Console

from excelmanus.events import EventType, ToolCallEvent
from excelmanus.renderer_dashboard import DashboardRenderer


def _make_console(width: int = 120) -> Console:
    """创建捕获输出的 Console 实例（非终端，Live 不启动）。"""
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

    def test_start_turn_records_start_time(self) -> None:
        c = _make_console()
        r = DashboardRenderer(c)
        r.start_turn(turn_number=1, model_name="m")
        assert r._start_time > 0

    def test_finish_turn_resets_status(self) -> None:
        c = _make_console()
        r = DashboardRenderer(c)
        r.start_turn(turn_number=1, model_name="m")
        r.finish_turn(elapsed_seconds=1.5, total_tokens=100)
        output = _get_output(c)
        # 应输出摘要信息
        assert "1" in output  # turn number or some metric
        assert r.state.status == "idle"

    def test_fail_turn_shows_error(self) -> None:
        c = _make_console()
        r = DashboardRenderer(c)
        r.start_turn(turn_number=1, model_name="m")
        r.fail_turn(error="连接超时")
        output = _get_output(c)
        assert "连接超时" in output
        assert r.state.status == "idle"

    def test_fail_turn_stops_live(self) -> None:
        """fail_turn 必须清理 Live 状态。"""
        c = _make_console()
        r = DashboardRenderer(c)
        r.start_turn(turn_number=1, model_name="m")
        r.fail_turn(error="err")
        assert r._live is None
        assert r._live_paused is False


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

    def test_tool_start_updates_current_tool_name(self) -> None:
        c = _make_console()
        r = DashboardRenderer(c)
        r.start_turn(turn_number=1, model_name="m")
        r.handle_event(ToolCallEvent(
            event_type=EventType.TOOL_CALL_START,
            tool_name="analyze_data",
            arguments={},
        ))
        assert r._current_tool_name == "analyze_data"

    def test_tool_end_clears_current_tool_name(self) -> None:
        c = _make_console()
        r = DashboardRenderer(c)
        r.start_turn(turn_number=1, model_name="m")
        r.handle_event(ToolCallEvent(
            event_type=EventType.TOOL_CALL_START,
            tool_name="read_excel",
            arguments={},
        ))
        r.handle_event(ToolCallEvent(
            event_type=EventType.TOOL_CALL_END,
            tool_name="read_excel",
            success=True,
        ))
        assert r._current_tool_name == ""

    def test_metrics_track_tool_calls(self) -> None:
        c = _make_console()
        r = DashboardRenderer(c)
        r.start_turn(turn_number=1, model_name="m")
        r.handle_event(ToolCallEvent(
            event_type=EventType.TOOL_CALL_START,
            tool_name="read_excel",
            arguments={},
        ))
        r.handle_event(ToolCallEvent(
            event_type=EventType.TOOL_CALL_END,
            tool_name="read_excel",
            success=True,
        ))
        r.handle_event(ToolCallEvent(
            event_type=EventType.TOOL_CALL_START,
            tool_name="write_excel",
            arguments={},
        ))
        r.handle_event(ToolCallEvent(
            event_type=EventType.TOOL_CALL_END,
            tool_name="write_excel",
            success=False,
            error="fail",
        ))
        assert r.metrics.total_tool_calls == 2
        assert r.metrics.success_count == 1
        assert r.metrics.failure_count == 1


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

    def test_status_bar_build_no_crash(self) -> None:
        """_build_status_bar 在各种状态下不应崩溃。"""
        c = _make_console()
        r = DashboardRenderer(c)
        r.start_turn(turn_number=1, model_name="m")
        for status in ("thinking", "tool_exec", "subagent", "summarizing", "idle"):
            r._state.status = status
            bar = r._build_status_bar()
            assert bar is not None


# ══════════════════════════════════════════════════════════
# 流式文本 Live 暂停/恢复测试
# ══════════════════════════════════════════════════════════


class TestDashboardStreamingLive:
    def test_text_delta_pauses_live(self) -> None:
        """text_delta 开始时应设置 _live_paused。"""
        c = _make_console()
        r = DashboardRenderer(c)
        r.start_turn(turn_number=1, model_name="m")
        r.handle_event(ToolCallEvent(
            event_type=EventType.TEXT_DELTA,
            text_delta="hello",
        ))
        assert r._streaming_text is True
        # 在 StringIO console 下 Live 不启动，所以 _live_paused 保持 False
        # 但 _streaming_text 标记必须正确

    def test_finish_streaming_resets_flags(self) -> None:
        c = _make_console()
        r = DashboardRenderer(c)
        r.start_turn(turn_number=1, model_name="m")
        r.handle_event(ToolCallEvent(
            event_type=EventType.TEXT_DELTA,
            text_delta="hello",
        ))
        assert r._streaming_text is True
        r.finish_streaming()
        assert r._streaming_text is False
        assert r._streaming_thinking is False

    def test_thinking_delta_sets_streaming_flag(self) -> None:
        c = _make_console()
        r = DashboardRenderer(c)
        r.start_turn(turn_number=1, model_name="m")
        r.handle_event(ToolCallEvent(
            event_type=EventType.THINKING_DELTA,
            thinking_delta="analyzing...",
        ))
        assert r._streaming_thinking is True


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

    def test_subagent_status_updates(self) -> None:
        """subagent 各阶段的 status 应正确更新。"""
        c = _make_console()
        r = DashboardRenderer(c)
        r.start_turn(turn_number=1, model_name="m")
        r.handle_event(ToolCallEvent(
            event_type=EventType.SUBAGENT_START,
            subagent_name="writer",
        ))
        assert r.state.status == "subagent"
        r.handle_event(ToolCallEvent(
            event_type=EventType.SUBAGENT_END,
            subagent_name="writer",
            subagent_success=True,
        ))
        assert r.state.status == "thinking"


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


# ══════════════════════════════════════════════════════════
# Live 管理方法测试
# ══════════════════════════════════════════════════════════


class TestDashboardLiveManagement:
    def test_live_not_started_on_stringio(self) -> None:
        """StringIO Console 不是终端，Live 不应启动。"""
        c = _make_console()
        r = DashboardRenderer(c)
        r.start_turn(turn_number=1, model_name="m")
        # StringIO console is_terminal == False, so _live should be None
        assert r._live is None

    def test_stop_live_idempotent(self) -> None:
        """多次调用 _stop_live 不应崩溃。"""
        c = _make_console()
        r = DashboardRenderer(c)
        r._stop_live()
        r._stop_live()
        assert r._live is None

    def test_pause_resume_without_live(self) -> None:
        """没有 Live 时 pause/resume 不崩溃。"""
        c = _make_console()
        r = DashboardRenderer(c)
        r._start_time = 1.0
        r._pause_live()
        r._resume_live()
        assert r._live is None

    def test_refresh_status_without_live(self) -> None:
        """没有 Live 时 _refresh_status 不崩溃。"""
        c = _make_console()
        r = DashboardRenderer(c)
        r._start_time = 1.0
        r._refresh_status()  # 不应抛异常
