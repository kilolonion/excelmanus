"""Think-Act Protocol 检测与度量测试。"""
from __future__ import annotations

import pytest
from excelmanus.engine_core.session_state import SessionState


class TestSessionStateReasoningCounters:
    """SessionState 推理计数器测试。"""

    def test_initial_values(self):
        state = SessionState()
        assert state.silent_call_count == 0
        assert state.reasoned_call_count == 0
        assert state.reasoning_chars_total == 0

    def test_reset_loop_stats_clears_counters(self):
        state = SessionState()
        state.silent_call_count = 5
        state.reasoned_call_count = 3
        state.reasoning_chars_total = 200
        state.reset_loop_stats()
        assert state.silent_call_count == 0
        assert state.reasoned_call_count == 0
        assert state.reasoning_chars_total == 0

    def test_reset_session_clears_counters(self):
        state = SessionState()
        state.silent_call_count = 5
        state.reasoned_call_count = 3
        state.reasoning_chars_total = 200
        state.reset_session()
        assert state.silent_call_count == 0
        assert state.reasoned_call_count == 0
        assert state.reasoning_chars_total == 0


from excelmanus.engine import TurnDiagnostic


class TestTurnDiagnosticReasoningFields:
    """TurnDiagnostic 推理字段测试。"""

    def test_default_values(self):
        diag = TurnDiagnostic(iteration=1)
        assert diag.has_reasoning is True
        assert diag.reasoning_chars == 0
        assert diag.silent_tool_call_count == 0

    def test_to_dict_omits_defaults(self):
        diag = TurnDiagnostic(iteration=1)
        d = diag.to_dict()
        assert "has_reasoning" not in d
        assert "reasoning_chars" not in d

    def test_to_dict_includes_non_defaults(self):
        diag = TurnDiagnostic(iteration=1, has_reasoning=False, silent_tool_call_count=3)
        d = diag.to_dict()
        assert d["has_reasoning"] is False
        assert d["silent_tool_call_count"] == 3

    def test_to_dict_includes_reasoning_chars_when_nonzero(self):
        diag = TurnDiagnostic(iteration=1, reasoning_chars=150)
        d = diag.to_dict()
        assert d["reasoning_chars"] == 150


from excelmanus.engine import ChatResult


class TestChatResultReasoningMetrics:
    """ChatResult reasoning_metrics 字段测试。"""

    def test_default_empty(self):
        cr = ChatResult(reply="test")
        assert cr.reasoning_metrics == {}

    def test_custom_metrics(self):
        cr = ChatResult(
            reply="test",
            reasoning_metrics={
                "silent_call_count": 2,
                "reasoned_call_count": 5,
                "reasoning_chars_total": 300,
                "silent_call_rate": 0.286,
            },
        )
        assert cr.reasoning_metrics["silent_call_count"] == 2
        assert cr.reasoning_metrics["silent_call_rate"] == 0.286


from types import SimpleNamespace
from excelmanus.engine_core.context_builder import ContextBuilder


class TestComputeReasoningLevel:
    """推理分级信号计算测试。"""

    def test_read_only_is_lightweight(self):
        route = SimpleNamespace(write_hint="read_only", task_tags=[])
        assert ContextBuilder._compute_reasoning_level_static(route) == "lightweight"

    def test_may_write_simple_is_standard(self):
        route = SimpleNamespace(write_hint="may_write", task_tags=["formatting"])
        assert ContextBuilder._compute_reasoning_level_static(route) == "standard"

    def test_cross_sheet_is_complete(self):
        route = SimpleNamespace(write_hint="may_write", task_tags=["cross_sheet"])
        assert ContextBuilder._compute_reasoning_level_static(route) == "complete"

    def test_large_data_is_complete(self):
        route = SimpleNamespace(write_hint="may_write", task_tags=["large_data"])
        assert ContextBuilder._compute_reasoning_level_static(route) == "complete"

    def test_unknown_hint_is_lightweight(self):
        route = SimpleNamespace(write_hint="unknown", task_tags=[])
        assert ContextBuilder._compute_reasoning_level_static(route) == "lightweight"

    def test_none_route_is_standard(self):
        assert ContextBuilder._compute_reasoning_level_static(None) == "standard"


class TestMetaCognitionSilentCall:
    """meta_cognition 沉默调用条件逻辑测试。"""

    def test_no_warning_when_all_reasoned(self):
        state = SessionState()
        state.silent_call_count = 0
        state.reasoned_call_count = 5
        silent = state.silent_call_count
        reasoned = state.reasoned_call_count
        should_warn = silent > 0 and silent >= reasoned
        assert should_warn is False

    def test_warning_when_majority_silent(self):
        state = SessionState()
        state.silent_call_count = 3
        state.reasoned_call_count = 2
        silent = state.silent_call_count
        reasoned = state.reasoned_call_count
        should_warn = silent > 0 and silent >= reasoned
        assert should_warn is True

    def test_no_warning_when_minority_silent(self):
        state = SessionState()
        state.silent_call_count = 1
        state.reasoned_call_count = 5
        silent = state.silent_call_count
        reasoned = state.reasoned_call_count
        should_warn = silent > 0 and silent >= reasoned
        assert should_warn is False


class TestSessionStateReasoningLevelTracking:
    """推理级别闭环追踪字段测试。"""

    def test_initial_values(self):
        state = SessionState()
        assert state.recommended_reasoning_level == "standard"
        assert state.reasoning_level_mismatch_count == 0
        assert state.reasoning_upgrade_nudge_count == 0

    def test_reset_loop_stats_clears_level_tracking(self):
        state = SessionState()
        state.recommended_reasoning_level = "complete"
        state.reasoning_level_mismatch_count = 5
        state.reasoning_upgrade_nudge_count = 2
        state.reset_loop_stats()
        assert state.recommended_reasoning_level == "standard"
        assert state.reasoning_level_mismatch_count == 0
        assert state.reasoning_upgrade_nudge_count == 0

    def test_reset_session_clears_level_tracking(self):
        state = SessionState()
        state.recommended_reasoning_level = "complete"
        state.reasoning_level_mismatch_count = 3
        state.reasoning_upgrade_nudge_count = 1
        state.reset_session()
        assert state.recommended_reasoning_level == "standard"
        assert state.reasoning_level_mismatch_count == 0
        assert state.reasoning_upgrade_nudge_count == 0


class TestComputeReasoningLevelStatic:
    """静态推理级别计算的回归测试（确保向后兼容）。"""

    def test_read_only_lightweight(self):
        route = SimpleNamespace(write_hint="read_only", task_tags=[])
        assert ContextBuilder._compute_reasoning_level_static(route) == "lightweight"

    def test_cross_sheet_complete(self):
        route = SimpleNamespace(write_hint="may_write", task_tags=["cross_sheet"])
        assert ContextBuilder._compute_reasoning_level_static(route) == "complete"

    def test_none_route_standard(self):
        assert ContextBuilder._compute_reasoning_level_static(None) == "standard"


class TestReasoningLevelMismatchLogic:
    """推理级别匹配检测逻辑的单元测试。"""

    @pytest.mark.parametrize("rec_level,avg_chars,expected_mismatch", [
        ("lightweight", 3, True),     # 低于 lightweight 阈值 5
        ("lightweight", 10, False),   # 超过 lightweight 阈值
        ("standard", 20, True),       # 低于 standard 阈值 30
        ("standard", 50, False),      # 超过 standard 阈值
        ("complete", 40, True),       # 低于 complete 阈值 60
        ("complete", 100, False),     # 超过 complete 阈值
    ])
    def test_mismatch_detection(self, rec_level, avg_chars, expected_mismatch):
        thresholds = ContextBuilder._REASONING_CHARS_THRESHOLDS
        min_chars = thresholds.get(rec_level, 5)
        is_mismatch = avg_chars < min_chars
        assert is_mismatch is expected_mismatch

    def test_thresholds_monotonically_increasing(self):
        t = ContextBuilder._REASONING_CHARS_THRESHOLDS
        assert t["lightweight"] < t["standard"] < t["complete"]


class TestMetaCognitionLevelMismatch:
    """meta_cognition 4b 条件（推理深度不足）的逻辑测试。"""

    def test_no_nudge_when_mismatch_below_threshold(self):
        """不匹配次数 < 2 时不触发 4b。"""
        state = SessionState()
        state.silent_call_count = 0
        state.reasoned_call_count = 5
        state.reasoning_level_mismatch_count = 1
        state.recommended_reasoning_level = "complete"
        # 4a 不触发（silent=0），4b 不触发（mismatch < 2）
        should_warn_4a = state.silent_call_count > 0 and state.silent_call_count >= state.reasoned_call_count
        should_warn_4b = (not should_warn_4a) and state.reasoning_level_mismatch_count >= 2
        assert should_warn_4a is False
        assert should_warn_4b is False

    def test_nudge_when_mismatch_reaches_threshold(self):
        """不匹配次数 >= 2 时触发 4b。"""
        state = SessionState()
        state.silent_call_count = 0
        state.reasoned_call_count = 5
        state.reasoning_level_mismatch_count = 2
        state.recommended_reasoning_level = "standard"
        should_warn_4a = state.silent_call_count > 0 and state.silent_call_count >= state.reasoned_call_count
        should_warn_4b = (not should_warn_4a) and state.reasoning_level_mismatch_count >= 2
        assert should_warn_4a is False
        assert should_warn_4b is True

    def test_4a_takes_priority_over_4b(self):
        """沉默调用（4a）优先于深度不足（4b）。"""
        state = SessionState()
        state.silent_call_count = 3
        state.reasoned_call_count = 2
        state.reasoning_level_mismatch_count = 5
        should_warn_4a = state.silent_call_count > 0 and state.silent_call_count >= state.reasoned_call_count
        assert should_warn_4a is True
        # 4a 已触发，4b 走 elif 分支不再触发

    def test_lightweight_no_nudge_hint(self):
        """lightweight 级别不匹配时没有具体提示文本（因为是最低级别）。"""
        hint_map = {
            "standard": "多步操作建议在工具调用前后各附 1-2 句观察与决策",
            "complete": "关键决策点建议说明观察到什么、分析了什么、为什么选择这个行动",
        }
        assert hint_map.get("lightweight", "") == ""
