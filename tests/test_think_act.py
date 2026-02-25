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
