"""单元测试：VerificationGate 验证门控。"""

from __future__ import annotations

import pytest

from excelmanus.engine_core.verification_gate import (
    VerificationGate,
    VerificationIntensity,
    VerificationResult,
)
from excelmanus.task_list import (
    TaskItem,
    TaskStatus,
    TaskStore,
    VerificationCriteria,
)


# ── VerificationIntensity 推断 ──────────────────────────────


class TestVerificationIntensityInference:
    """测试验证强度推断逻辑。"""

    def _make_gate(self) -> VerificationGate:
        """构造一个无 engine 的 gate（仅测试推断方法）。"""
        gate = VerificationGate.__new__(VerificationGate)
        gate._fix_attempt_count = {}
        gate._global_fix_count = 0
        gate._pending_fix_notice = ""
        return gate

    def test_read_mode_is_skip(self) -> None:
        gate = self._make_gate()
        assert gate.infer_intensity(chat_mode="read") == VerificationIntensity.SKIP

    def test_plan_mode_is_skip(self) -> None:
        gate = self._make_gate()
        assert gate.infer_intensity(chat_mode="plan") == VerificationIntensity.SKIP

    def test_user_requests_verification_is_strict(self) -> None:
        gate = self._make_gate()
        assert gate.infer_intensity(
            chat_mode="write", user_message="请仔细检查结果"
        ) == VerificationIntensity.STRICT

    def test_cross_sheet_tag_is_strict(self) -> None:
        gate = self._make_gate()
        assert gate.infer_intensity(
            chat_mode="write", task_tags=("cross_sheet",)
        ) == VerificationIntensity.STRICT

    def test_formula_tag_is_strict(self) -> None:
        gate = self._make_gate()
        assert gate.infer_intensity(
            chat_mode="write", task_tags=("formula",)
        ) == VerificationIntensity.STRICT

    def test_single_cell_no_task_list_is_light(self) -> None:
        gate = self._make_gate()
        assert gate.infer_intensity(
            chat_mode="write", has_task_list=False, write_hint="single_cell"
        ) == VerificationIntensity.LIGHT

    def test_small_task_list_is_light(self) -> None:
        gate = self._make_gate()
        assert gate.infer_intensity(
            chat_mode="write", has_task_list=True, task_count=2
        ) == VerificationIntensity.LIGHT

    def test_default_is_standard(self) -> None:
        gate = self._make_gate()
        assert gate.infer_intensity(
            chat_mode="write", has_task_list=True, task_count=5
        ) == VerificationIntensity.STANDARD


# ── should_verify_tool ──────────────────────────────────────


class TestShouldVerifyTool:
    """测试工具验证触发判断。"""

    def _make_gate(self) -> VerificationGate:
        gate = VerificationGate.__new__(VerificationGate)
        gate._fix_attempt_count = {}
        gate._global_fix_count = 0
        gate._pending_fix_notice = ""
        return gate

    def test_write_cells_triggers(self) -> None:
        gate = self._make_gate()
        assert gate.should_verify_tool("write_cells") is True

    def test_write_excel_triggers(self) -> None:
        gate = self._make_gate()
        assert gate.should_verify_tool("write_excel") is True

    def test_read_excel_does_not_trigger(self) -> None:
        gate = self._make_gate()
        assert gate.should_verify_tool("read_excel") is False

    def test_run_code_without_write_does_not_trigger(self) -> None:
        gate = self._make_gate()
        assert gate.should_verify_tool("run_code", has_write_effect=False) is False

    def test_run_code_with_write_triggers(self) -> None:
        gate = self._make_gate()
        assert gate.should_verify_tool("run_code", has_write_effect=True) is True

    def test_create_sheet_triggers(self) -> None:
        gate = self._make_gate()
        assert gate.should_verify_tool("create_sheet") is True


# ── _compare_numeric ────────────────────────────────────────


class TestCompareNumeric:
    """测试数值比较逻辑。"""

    def test_exact_match(self) -> None:
        assert VerificationGate._compare_numeric(38, "38") is True

    def test_exact_mismatch(self) -> None:
        assert VerificationGate._compare_numeric(37, "38") is False

    def test_greater_than(self) -> None:
        assert VerificationGate._compare_numeric(10, ">0") is True
        assert VerificationGate._compare_numeric(0, ">0") is False

    def test_greater_equal(self) -> None:
        assert VerificationGate._compare_numeric(0, ">=0") is True
        assert VerificationGate._compare_numeric(-1, ">=0") is False

    def test_less_than(self) -> None:
        assert VerificationGate._compare_numeric(5, "<10") is True
        assert VerificationGate._compare_numeric(10, "<10") is False

    def test_less_equal(self) -> None:
        assert VerificationGate._compare_numeric(10, "<=10") is True

    def test_none_actual(self) -> None:
        assert VerificationGate._compare_numeric(None, "38") is False

    def test_empty_expected(self) -> None:
        assert VerificationGate._compare_numeric(100, "") is True

    def test_invalid_expected(self) -> None:
        assert VerificationGate._compare_numeric(10, "abc") is False


# ── _extract_row_count_from_result ──────────────────────────


class TestExtractRowCount:
    """测试从 read_excel 结果中提取行数。"""

    def test_chinese_format(self) -> None:
        assert VerificationGate._extract_row_count_from_result("共 38 行数据") == 38

    def test_english_format(self) -> None:
        assert VerificationGate._extract_row_count_from_result("500 rows") == 500

    def test_total_rows_format(self) -> None:
        assert VerificationGate._extract_row_count_from_result("total_rows: 123") == 123

    def test_no_match(self) -> None:
        assert VerificationGate._extract_row_count_from_result("empty result") is None


# ── _handle_failure / pending_fix_notice ────────────────────


class TestHandleFailure:
    """测试验证失败处理和修复提示生成。"""

    def _make_gate(self) -> VerificationGate:
        gate = VerificationGate.__new__(VerificationGate)
        gate._engine = None
        gate._fix_attempt_count = {}
        gate._global_fix_count = 0
        gate._pending_fix_notice = ""
        return gate

    def test_first_failure_generates_retry_notice(self) -> None:
        gate = self._make_gate()
        result = VerificationResult(
            passed=False, check_type="row_count",
            expected="38", actual="0", message="行数不匹配",
        )
        gate._handle_failure(0, result, VerificationIntensity.STANDARD)
        notice = gate.pending_fix_notice
        assert "验证未通过" in notice
        assert "1/2" in notice

    def test_second_failure_generates_retry_notice(self) -> None:
        gate = self._make_gate()
        gate._fix_attempt_count[0] = 1
        result = VerificationResult(
            passed=False, check_type="row_count",
            expected="38", actual="0",
        )
        gate._handle_failure(0, result, VerificationIntensity.STANDARD)
        notice = gate.pending_fix_notice
        assert "验证失败" in notice
        assert "耗尽" in notice

    def test_pending_fix_notice_auto_clears(self) -> None:
        gate = self._make_gate()
        gate._pending_fix_notice = "test notice"
        assert gate.pending_fix_notice == "test notice"
        assert gate.pending_fix_notice == ""  # 第二次读取应为空

    def test_global_limit_triggers_failure(self) -> None:
        gate = self._make_gate()
        gate._global_fix_count = 6
        result = VerificationResult(
            passed=False, check_type="row_count",
            expected="100", actual="0",
        )
        gate._handle_failure(5, result, VerificationIntensity.STRICT)
        notice = gate.pending_fix_notice
        assert "耗尽" in notice

    def test_reset_clears_state(self) -> None:
        gate = self._make_gate()
        gate._fix_attempt_count = {0: 2, 1: 1}
        gate._global_fix_count = 3
        gate._pending_fix_notice = "some notice"
        gate.reset()
        assert gate._fix_attempt_count == {}
        assert gate._global_fix_count == 0
        assert gate._pending_fix_notice == ""
