"""Stuck Detection 单元测试。

覆盖 SessionState 中的滑动窗口记录、指纹生成、两种退化模式检测。
"""

from __future__ import annotations

import pytest

from excelmanus.engine_core.session_state import (
    SessionState,
    _ACTION_REPEAT_THRESHOLD,
    _READ_ONLY_LOOP_THRESHOLD,
)


class TestArgsFingerprint:
    """参数指纹生成。"""

    def test_same_args_same_fingerprint(self) -> None:
        fp1 = SessionState._args_fingerprint({"file": "a.xlsx", "sheet": "Sheet1"})
        fp2 = SessionState._args_fingerprint({"sheet": "Sheet1", "file": "a.xlsx"})
        assert fp1 == fp2

    def test_different_args_different_fingerprint(self) -> None:
        fp1 = SessionState._args_fingerprint({"file": "a.xlsx"})
        fp2 = SessionState._args_fingerprint({"file": "b.xlsx"})
        assert fp1 != fp2

    def test_empty_args(self) -> None:
        fp = SessionState._args_fingerprint({})
        assert isinstance(fp, str) and len(fp) == 8


class TestRecordToolCall:
    """记录工具调用到滑动窗口。"""

    def test_records_tool_call(self) -> None:
        state = SessionState()
        state.record_tool_call_for_stuck_detection("read_excel", {"file": "a.xlsx"})
        assert len(state._recent_tool_calls) == 1

    def test_sliding_window_max_size(self) -> None:
        state = SessionState()
        for i in range(20):
            state.record_tool_call_for_stuck_detection(f"tool_{i}", {"n": i})
        from excelmanus.engine_core.session_state import _STUCK_WINDOW_SIZE
        assert len(state._recent_tool_calls) == _STUCK_WINDOW_SIZE

    def test_reset_loop_clears_window(self) -> None:
        state = SessionState()
        state.record_tool_call_for_stuck_detection("read_excel", {"file": "a.xlsx"})
        state.reset_loop_stats()
        assert len(state._recent_tool_calls) == 0
        assert state.stuck_warning_fired is False


class TestActionRepeatDetection:
    """Pattern 1：连续相同工具+相同参数检测。"""

    def test_no_stuck_below_threshold(self) -> None:
        state = SessionState()
        for _ in range(_ACTION_REPEAT_THRESHOLD - 1):
            state.record_tool_call_for_stuck_detection("read_excel", {"file": "a.xlsx"})
        assert state.detect_stuck_pattern() is None

    def test_detects_action_repeat(self) -> None:
        state = SessionState()
        for _ in range(_ACTION_REPEAT_THRESHOLD):
            state.record_tool_call_for_stuck_detection("read_excel", {"file": "a.xlsx"})
        warning = state.detect_stuck_pattern()
        assert warning is not None
        assert "read_excel" in warning
        assert "重复操作" in warning

    def test_no_repeat_with_different_args(self) -> None:
        state = SessionState()
        for i in range(_ACTION_REPEAT_THRESHOLD):
            state.record_tool_call_for_stuck_detection("read_excel", {"file": f"file_{i}.xlsx"})
        assert state.detect_stuck_pattern() is None

    def test_no_repeat_with_different_tools(self) -> None:
        state = SessionState()
        for i in range(_ACTION_REPEAT_THRESHOLD):
            state.record_tool_call_for_stuck_detection(f"tool_{i}", {"file": "a.xlsx"})
        assert state.detect_stuck_pattern() is None

    def test_warning_fires_only_once(self) -> None:
        state = SessionState()
        for _ in range(_ACTION_REPEAT_THRESHOLD):
            state.record_tool_call_for_stuck_detection("read_excel", {"file": "a.xlsx"})
        warning1 = state.detect_stuck_pattern()
        warning2 = state.detect_stuck_pattern()
        assert warning1 is not None
        assert warning2 is None  # 第二次不触发


class TestReadOnlyLoopDetection:
    """Pattern 2：write_hint=may_write 时持续只读检测。"""

    def test_no_warning_without_may_write(self) -> None:
        state = SessionState()
        state.current_write_hint = "read_only"
        # 使用不同参数避免触发 Action Repeat
        for i in range(_READ_ONLY_LOOP_THRESHOLD):
            state.record_tool_call_for_stuck_detection("read_excel", {"file": f"file_{i}.xlsx"})
        assert state.detect_stuck_pattern() is None

    def test_detects_read_only_loop(self) -> None:
        state = SessionState()
        state.current_write_hint = "may_write"
        # 使用不同参数避免触发 Action Repeat
        for i in range(_READ_ONLY_LOOP_THRESHOLD):
            state.record_tool_call_for_stuck_detection("read_excel", {"file": f"file_{i}.xlsx"})
        warning = state.detect_stuck_pattern()
        assert warning is not None
        assert "只读循环" in warning

    def test_no_warning_if_write_happened(self) -> None:
        state = SessionState()
        state.current_write_hint = "may_write"
        state.has_write_tool_call = True  # 已有写入
        for i in range(_READ_ONLY_LOOP_THRESHOLD):
            state.record_tool_call_for_stuck_detection("read_excel", {"file": f"file_{i}.xlsx"})
        assert state.detect_stuck_pattern() is None

    def test_warning_fires_only_once(self) -> None:
        state = SessionState()
        state.current_write_hint = "may_write"
        for i in range(_READ_ONLY_LOOP_THRESHOLD):
            state.record_tool_call_for_stuck_detection("read_excel", {"file": f"file_{i}.xlsx"})
        warning1 = state.detect_stuck_pattern()
        warning2 = state.detect_stuck_pattern()
        assert warning1 is not None
        assert warning2 is None


class TestResetClearsStuckState:
    """验证 reset_session 也清理 stuck detection 状态。"""

    def test_reset_session_clears_stuck(self) -> None:
        state = SessionState()
        for _ in range(_ACTION_REPEAT_THRESHOLD):
            state.record_tool_call_for_stuck_detection("read_excel", {"file": "a.xlsx"})
        state.detect_stuck_pattern()  # 触发警告
        assert state.stuck_warning_fired is True

        state.reset_session()
        assert state.stuck_warning_fired is False
        assert len(state._recent_tool_calls) == 0
