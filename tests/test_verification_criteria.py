"""单元测试：VerificationCriteria 数据模型与 _parse_verification 解析。"""

from __future__ import annotations

import pytest

from excelmanus.task_list import (
    TaskItem,
    TaskStatus,
    TaskStore,
    VerificationCriteria,
    _parse_verification,
)


class TestVerificationCriteriaSerialization:
    """VerificationCriteria 序列化 / 反序列化。"""

    def test_minimal_to_dict(self) -> None:
        """仅 check_type 时 to_dict 只含 check_type。"""
        vc = VerificationCriteria(check_type="row_count")
        d = vc.to_dict()
        assert d == {"check_type": "row_count"}

    def test_full_to_dict(self) -> None:
        """所有字段填充时 to_dict 完整。"""
        vc = VerificationCriteria(
            check_type="row_count",
            target_file="report.xlsx",
            target_sheet="客户汇总",
            target_range="A1:C100",
            expected="38",
            actual="38",
            passed=True,
        )
        d = vc.to_dict()
        assert d["check_type"] == "row_count"
        assert d["target_file"] == "report.xlsx"
        assert d["target_sheet"] == "客户汇总"
        assert d["target_range"] == "A1:C100"
        assert d["expected"] == "38"
        assert d["actual"] == "38"
        assert d["passed"] is True

    def test_roundtrip(self) -> None:
        """to_dict → from_dict 完整往返。"""
        original = VerificationCriteria(
            check_type="formula_exists",
            target_file="data.xlsx",
            target_sheet="Sheet1",
            target_range="B2:B50",
            expected="VLOOKUP",
        )
        restored = VerificationCriteria.from_dict(original.to_dict())
        assert restored.check_type == original.check_type
        assert restored.target_file == original.target_file
        assert restored.target_sheet == original.target_sheet
        assert restored.target_range == original.target_range
        assert restored.expected == original.expected
        assert restored.actual is None
        assert restored.passed is None

    def test_from_dict_defaults(self) -> None:
        """from_dict 缺少可选字段时使用默认值。"""
        vc = VerificationCriteria.from_dict({"check_type": "sheet_exists"})
        assert vc.check_type == "sheet_exists"
        assert vc.target_file == ""
        assert vc.target_sheet == ""
        assert vc.expected == ""

    def test_from_dict_missing_check_type_defaults_to_custom(self) -> None:
        """from_dict 缺少 check_type 时默认为 custom。"""
        vc = VerificationCriteria.from_dict({"expected": "行数 > 0"})
        assert vc.check_type == "custom"
        assert vc.expected == "行数 > 0"

    def test_passed_none_not_in_dict(self) -> None:
        """passed=None 时不出现在序列化结果中。"""
        vc = VerificationCriteria(check_type="row_count")
        assert "passed" not in vc.to_dict()
        assert "actual" not in vc.to_dict()

    def test_passed_false_in_dict(self) -> None:
        """passed=False 时出现在序列化结果中。"""
        vc = VerificationCriteria(check_type="row_count", passed=False, actual="0")
        d = vc.to_dict()
        assert d["passed"] is False
        assert d["actual"] == "0"


class TestParseVerification:
    """_parse_verification 统一解析器。"""

    def test_none_returns_none(self) -> None:
        assert _parse_verification(None) is None

    def test_empty_string_returns_none(self) -> None:
        assert _parse_verification("") is None
        assert _parse_verification("   ") is None

    def test_string_returns_custom(self) -> None:
        vc = _parse_verification("行数 > 0")
        assert vc is not None
        assert vc.check_type == "custom"
        assert vc.expected == "行数 > 0"

    def test_dict_returns_structured(self) -> None:
        vc = _parse_verification({
            "check_type": "row_count",
            "target_file": "data.xlsx",
            "target_sheet": "Sheet1",
            "expected": "500",
        })
        assert vc is not None
        assert vc.check_type == "row_count"
        assert vc.target_file == "data.xlsx"
        assert vc.expected == "500"

    def test_verification_criteria_passthrough(self) -> None:
        original = VerificationCriteria(check_type="sheet_exists", target_sheet="汇总")
        result = _parse_verification(original)
        assert result is original

    def test_dict_without_check_type(self) -> None:
        """dict 缺少 check_type 默认为 custom。"""
        vc = _parse_verification({"expected": "非空"})
        assert vc is not None
        assert vc.check_type == "custom"


class TestTaskItemWithVerificationCriteria:
    """TaskItem 与 VerificationCriteria 集成。"""

    def test_to_dict_with_structured_verification(self) -> None:
        """结构化验证条件序列化为 dict 而非 str。"""
        item = TaskItem(
            title="写入汇总",
            verification_criteria=VerificationCriteria(
                check_type="row_count",
                target_file="report.xlsx",
                target_sheet="汇总",
                expected="38",
            ),
        )
        d = item.to_dict()
        assert isinstance(d["verification"], dict)
        assert d["verification"]["check_type"] == "row_count"

    def test_from_dict_with_structured_verification(self) -> None:
        """from_dict 解析结构化 verification dict。"""
        item = TaskItem.from_dict({
            "title": "写入公式",
            "status": "pending",
            "verification": {
                "check_type": "formula_exists",
                "target_file": "data.xlsx",
                "target_sheet": "Sheet1",
                "target_range": "B2:B50",
            },
        })
        assert item.verification_criteria is not None
        assert item.verification_criteria.check_type == "formula_exists"
        assert item.verification_criteria.target_range == "B2:B50"

    def test_from_dict_with_string_verification_backward_compat(self) -> None:
        """from_dict 兼容旧的 str 格式 verification。"""
        item = TaskItem.from_dict({
            "title": "旧任务",
            "status": "pending",
            "verification": "行数应为 100",
        })
        assert item.verification_criteria is not None
        assert item.verification_criteria.check_type == "custom"
        assert item.verification_criteria.expected == "行数应为 100"

    def test_from_dict_without_verification(self) -> None:
        """from_dict 无 verification 时为 None。"""
        item = TaskItem.from_dict({"title": "探查", "status": "pending"})
        assert item.verification_criteria is None

    def test_roundtrip_structured(self) -> None:
        """结构化验证条件 to_dict → from_dict 往返。"""
        original = TaskItem(
            title="跨表填充",
            verification_criteria=VerificationCriteria(
                check_type="value_match",
                target_file="result.xlsx",
                target_sheet="Sheet1",
                target_range="D2:D100",
                expected="非空",
            ),
        )
        restored = TaskItem.from_dict(original.to_dict())
        assert restored.verification_criteria is not None
        assert restored.verification_criteria.check_type == "value_match"
        assert restored.verification_criteria.target_range == "D2:D100"
        assert restored.verification_criteria.expected == "非空"

    def test_truthiness_for_blocking_check(self) -> None:
        """VerificationCriteria 实例应为 truthy（用于 _has_verification_failed_blocking_task）。"""
        vc = VerificationCriteria(check_type="custom", expected="任何条件")
        assert bool(vc) is True


class TestTaskItemForceRetry:
    """TaskItem.force_retry() 强制重试。"""

    def test_force_retry_from_failed(self) -> None:
        item = TaskItem(title="测试", status=TaskStatus.FAILED)
        item.force_retry()
        assert item.status == TaskStatus.IN_PROGRESS

    def test_force_retry_from_non_failed_is_noop(self) -> None:
        """非 FAILED 状态调用 force_retry 无操作。"""
        for status in [TaskStatus.PENDING, TaskStatus.IN_PROGRESS, TaskStatus.COMPLETED]:
            item = TaskItem(title="测试", status=status)
            item.force_retry()
            assert item.status == status  # 不变


class TestTaskStoreCreateWithStructuredVerification:
    """TaskStore.create 支持结构化 verification。"""

    def test_create_with_dict_verification(self) -> None:
        store = TaskStore()
        store.create("测试", [
            {"title": "步骤1", "verification": {
                "check_type": "row_count",
                "target_file": "a.xlsx",
                "target_sheet": "Sheet1",
                "expected": "100",
            }},
            {"title": "步骤2"},
            "步骤3",
        ])
        items = store.current.items
        assert items[0].verification_criteria is not None
        assert items[0].verification_criteria.check_type == "row_count"
        assert items[1].verification_criteria is None
        assert items[2].verification_criteria is None

    def test_create_with_string_verification(self) -> None:
        store = TaskStore()
        store.create("测试", [
            {"title": "步骤1", "verification": "行数 > 0"},
        ])
        vc = store.current.items[0].verification_criteria
        assert vc is not None
        assert vc.check_type == "custom"
        assert vc.expected == "行数 > 0"

    def test_create_with_mixed_formats(self) -> None:
        """混合字符串和结构化 verification。"""
        store = TaskStore()
        store.create("混合", [
            {"title": "A", "verification": "简单条件"},
            {"title": "B", "verification": {"check_type": "sheet_exists", "target_sheet": "汇总"}},
            "C",
        ])
        items = store.current.items
        assert items[0].verification_criteria.check_type == "custom"
        assert items[1].verification_criteria.check_type == "sheet_exists"
        assert items[2].verification_criteria is None
