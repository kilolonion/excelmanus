"""VBA 支持相关回归测试：keep_vba、VBA 信息提取。"""

from __future__ import annotations

from pathlib import Path


# ── VBA 信息提取 ─────────────────────────────────────────────


class TestCollectVbaInfo:
    """_collect_vba_info 的单元测试。"""

    def test_xlsx_returns_no_vba(self, tmp_path: Path) -> None:
        """对 .xlsx 文件应返回 has_vba=False。"""
        import openpyxl

        from excelmanus.workbook.data import _collect_vba_info

        xlsx_path = tmp_path / "test.xlsx"
        wb = openpyxl.Workbook()
        wb.save(xlsx_path)
        wb.close()

        info = _collect_vba_info(xlsx_path)
        assert info["has_vba"] is False
        assert info["modules"] == []

    def test_xlsm_without_actual_vba(self, tmp_path: Path) -> None:
        """对无 VBA 内容的 .xlsm 文件应返回 has_vba=False。"""
        import openpyxl

        from excelmanus.workbook.data import _collect_vba_info

        # openpyxl 创建的 .xlsm 不包含 vbaProject.bin
        xlsm_path = tmp_path / "test.xlsm"
        wb = openpyxl.Workbook()
        wb.save(xlsm_path)
        wb.close()

        info = _collect_vba_info(xlsm_path)
        assert info["has_vba"] is False

    def test_non_excel_returns_no_vba(self, tmp_path: Path) -> None:
        """对非 Excel 文件应返回 has_vba=False。"""
        from excelmanus.workbook.data import _collect_vba_info

        txt_path = tmp_path / "test.txt"
        txt_path.write_text("not excel")

        info = _collect_vba_info(txt_path)
        assert info["has_vba"] is False

    def test_vba_dimension_in_include_dimensions(self) -> None:
        """vba 应在 INCLUDE_DIMENSIONS 中注册。"""
        from excelmanus.workbook.data import INCLUDE_DIMENSIONS

        assert "vba" in INCLUDE_DIMENSIONS

    def test_vba_dimension_in_scan_files_dimensions(self) -> None:
        """vba 应在 _SCAN_FILES_DIMENSIONS 中注册。"""
        from excelmanus.workbook.data import _SCAN_FILES_DIMENSIONS

        assert "vba" in _SCAN_FILES_DIMENSIONS
