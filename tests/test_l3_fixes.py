"""L3 修复单元测试：fuzzy sheet matching。"""

from __future__ import annotations

from excelmanus.tools._helpers import (
    _find_closest_sheet_name,
    resolve_sheet_name,
)


class TestFuzzySheetNameMatching:
    """resolve_sheet_name 第三级 fuzzy matching。"""

    def test_exact_match_still_works(self) -> None:
        assert resolve_sheet_name("Sheet1", ["Sheet1", "Sheet2"]) == "Sheet1"

    def test_case_insensitive_still_works(self) -> None:
        assert resolve_sheet_name("sheet1", ["Sheet1", "Sheet2"]) == "Sheet1"

    def test_similar_name_is_rejected_not_silently_corrected(self) -> None:
        """近似名不再被静默改写；仅大小写不敏感匹配视为命中。"""
        assert resolve_sheet_name("工作表标題", ["工作表标题", "数据源"]) is None

    def test_partial_name_is_rejected(self) -> None:
        """部分/拼写错误不再自动纠正（避免错误表名被传播）。"""
        assert resolve_sheet_name("Shet1", ["Sheet1", "Sheet2"]) is None

    def test_fuzzy_match_returns_none_for_very_different(self) -> None:
        """完全不相关的名称不应匹配。"""
        result = resolve_sheet_name("ABCXYZ", ["Sheet1", "数据源"])
        assert result is None

    def test_best_candidate_available_as_hint_not_correction(self) -> None:
        """最相似候选只用于错误提示，不用于自动纠正。"""
        assert resolve_sheet_name("Sheet1x", ["Sheet1", "Sheet2", "Sheet3"]) is None
        from excelmanus.tools._helpers import _find_closest_sheet_name

        name, ratio = _find_closest_sheet_name("Sheet1x", ["Sheet1", "Sheet2", "Sheet3"])
        assert name == "Sheet1" and ratio >= 0.6

    def test_none_input_returns_none(self) -> None:
        assert resolve_sheet_name(None, ["Sheet1"]) is None

    def test_empty_available_returns_none(self) -> None:
        assert resolve_sheet_name("Sheet1", []) is None


class TestFindClosestSheetName:
    """_find_closest_sheet_name 辅助函数。"""

    def test_returns_best_match(self) -> None:
        name, ratio = _find_closest_sheet_name("Shet1", ["Sheet1", "Sheet2"])
        assert name == "Sheet1"
        assert ratio > 0.7

    def test_empty_available(self) -> None:
        name, ratio = _find_closest_sheet_name("Sheet1", [])
        assert name is None
        assert ratio == 0.0

    def test_case_insensitive_comparison(self) -> None:
        """fuzzy matching 应该是大小写不敏感的。"""
        name, ratio = _find_closest_sheet_name("SHET1", ["Sheet1", "data"])
        assert name == "Sheet1"
        assert ratio > 0.5


class TestCheckSheetNameEnhanced:
    """check_sheet_name 错误消息增强（需要真实 Excel 文件）。"""

    def test_typo_is_rejected_with_closest_hint(self, tmp_path) -> None:
        """check_sheet_name 不再静默纠正拼写；拒绝并在错误里给出最接近候选。"""
        from openpyxl import Workbook
        from excelmanus.tools._helpers import check_sheet_name

        wb = Workbook()
        ws = wb.active
        ws.title = "销售数据"
        wb.save(tmp_path / "test.xlsx")
        wb.close()

        resolved, err = check_sheet_name(tmp_path / "test.xlsx", "销售数据x")
        assert resolved is None
        assert err is not None
        payload = err.value
        assert payload.get("closest_match") == "销售数据"

    def test_exact_case_insensitive_match_still_accepted(self, tmp_path) -> None:
        """大小写不敏感精确匹配仍视为命中。"""
        from openpyxl import Workbook
        from excelmanus.tools._helpers import check_sheet_name

        wb = Workbook()
        ws = wb.active
        ws.title = "Sheet1"
        wb.save(tmp_path / "test.xlsx")
        wb.close()

        resolved, err = check_sheet_name(tmp_path / "test.xlsx", "sheet1")
        assert err is None
        assert resolved == "Sheet1"

    def test_error_includes_closest_match(self, tmp_path) -> None:
        """当 fuzzy 也匹配不上时，错误应包含 closest_match。"""
        from openpyxl import Workbook
        from excelmanus.tools._helpers import check_sheet_name

        wb = Workbook()
        ws = wb.active
        ws.title = "Sheet1"
        wb.save(tmp_path / "test.xlsx")
        wb.close()

        resolved, err = check_sheet_name(tmp_path / "test.xlsx", "完全不同的名字ABCXYZ")
        assert resolved is None
        assert err is not None
        payload = err.value
        assert payload["error_code"] == "SHEET_NOT_FOUND"
        assert "available_sheets" in payload
        assert "hint" in payload
