"""L1 系统级可靠性修复回归测试。

覆盖修复：
- L1-2: search_excel_values fuzzy 模糊匹配
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import openpyxl
import pytest


# ═══════════════════════════════════════════════════════════════════
# L1-2: search_excel_values fuzzy 模糊匹配
# ═══════════════════════════════════════════════════════════════════


@pytest.fixture()
def fuzzy_test_workbook(tmp_path: Path) -> Path:
    """创建一个用于 fuzzy 搜索测试的 Excel 文件。"""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(["姓名", "班级", "成绩"])
    ws.append(["张三", "24级电子信息科学与技术1班", 90])
    ws.append(["李四", "24级计算机科学与技术2班", 85])
    ws.append(["王五", "23级软件工程3班", 78])
    ws.append(["赵六", "Advanced Mathematics Class", 92])
    fp = tmp_path / "fuzzy_test.xlsx"
    wb.save(fp)
    return fp


class TestSearchFuzzyMatch:
    """验证 search_excel_values fuzzy 匹配模式。"""

    def _search(self, file_path: Path, query: str, **kwargs) -> dict:
        from excelmanus.workbook.data import search_excel_values
        # Monkey-patch guard to allow test paths
        import excelmanus.workbook.data as dt
        original_get_guard = dt._get_guard
        mock_guard = MagicMock()
        mock_guard.resolve_and_validate = lambda p: file_path  # return Path, not str
        mock_guard.workspace_root = file_path.parent
        dt._get_guard = lambda: mock_guard
        try:
            result = search_excel_values(str(file_path), query, match_mode="fuzzy", **kwargs)
            return result.value
        finally:
            dt._get_guard = original_get_guard

    def test_fuzzy_chinese_digit_conversion(self, fuzzy_test_workbook: Path) -> None:
        """搜索'电子一班'应匹配'电子信息科学与技术1班'（中文数字→阿拉伯数字）。"""
        result = self._search(fuzzy_test_workbook, "电子一班")
        assert result["total_matches"] >= 1
        match_val = result["matches"][0]["value"]
        assert "电子" in match_val and "1班" in match_val

    def test_fuzzy_token_split(self, fuzzy_test_workbook: Path) -> None:
        """搜索'计算机2班'应匹配'计算机科学与技术2班'。"""
        result = self._search(fuzzy_test_workbook, "计算机2班")
        assert result["total_matches"] >= 1

    def test_fuzzy_no_match(self, fuzzy_test_workbook: Path) -> None:
        """搜索'物理4班'不应有匹配。"""
        result = self._search(fuzzy_test_workbook, "物理4班")
        assert result["total_matches"] == 0

    def test_fuzzy_english_tokens(self, fuzzy_test_workbook: Path) -> None:
        """搜索'Advanced Class'应匹配'Advanced Mathematics Class'。"""
        result = self._search(fuzzy_test_workbook, "Advanced Class")
        assert result["total_matches"] >= 1

    def test_fuzzy_case_insensitive(self, fuzzy_test_workbook: Path) -> None:
        """模糊匹配默认大小写不敏感。"""
        result = self._search(fuzzy_test_workbook, "advanced class")
        assert result["total_matches"] >= 1

    def test_fuzzy_single_token_fallback(self, fuzzy_test_workbook: Path) -> None:
        """单个 token 时行为类似 contains。"""
        result = self._search(fuzzy_test_workbook, "张三")
        assert result["total_matches"] >= 1
