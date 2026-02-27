"""属性测试：Excel 扩展名扫描一致性（FileRegistry 版本）。

Feature: excel-extension-inconsistency
- Property 1 (fault condition): .xls/.xlsb 也应被 FileRegistry 扫描
- Property 2 (preservation): .xlsx/.xlsm 原有行为保持不变
"""

from __future__ import annotations

import time
from pathlib import Path

from openpyxl import Workbook

from excelmanus.database import Database
from excelmanus.file_registry import FileRegistry


def _write_valid_workbook(path: Path, sheet_name: str, headers: list[str]) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_name
    ws.append(headers)
    ws.append(["v" for _ in headers])
    wb.save(path)


def test_fault_condition(tmp_path: Path) -> None:
    """Property 1: .xls/.xlsb 应被 FileRegistry scan 扫描到。"""
    (tmp_path / "report.xls").write_bytes(b"dummy-xls")
    (tmp_path / "data.xlsb").write_bytes(b"dummy-xlsb")
    _write_valid_workbook(tmp_path / "baseline.xlsx", "Base", ["A", "B"])

    db = Database(str(tmp_path / "test.db"))
    reg = FileRegistry(db, tmp_path)
    reg.scan_workspace(excel_only=True)
    names = {e.original_name for e in reg.list_all()}
    assert "report.xls" in names
    assert "data.xlsb" in names

    # 增量扫描：新增 .xls 文件也应被检测
    (tmp_path / "newly_added.xls").write_bytes(b"dummy-new-xls")
    reg.scan_workspace(excel_only=True)
    names2 = {e.original_name for e in reg.list_all()}
    assert "newly_added.xls" in names2


def test_preservation(tmp_path: Path) -> None:
    """Property 2: .xlsx/.xlsm 扫描 + 增量缓存行为保持不变。"""
    xlsx = tmp_path / "sales.xlsx"
    xlsm = tmp_path / "macro.xlsm"
    _write_valid_workbook(xlsx, "Sales", ["name", "amount"])
    _write_valid_workbook(xlsm, "Macro", ["k", "v"])

    db = Database(str(tmp_path / "test.db"))
    reg = FileRegistry(db, tmp_path)
    reg.scan_workspace(excel_only=True)
    names = {e.original_name for e in reg.list_all()}
    assert names == {"sales.xlsx", "macro.xlsm"}

    sales = reg.get_by_path("sales.xlsx")
    macro = reg.get_by_path("macro.xlsm")
    assert sales is not None and sales.sheet_meta and sales.sheet_meta[0]["name"] == "Sales"
    assert macro is not None and macro.sheet_meta and macro.sheet_meta[0]["name"] == "Macro"
    sales_mtime = sales.mtime_ns
    macro_mtime = macro.mtime_ns

    # 无变化时增量扫描应缓存命中
    r2 = reg.scan_workspace(excel_only=True)
    assert r2.cache_hits >= 2
    sales2 = reg.get_by_path("sales.xlsx")
    assert sales2 is not None and sales2.mtime_ns == sales_mtime

    # 修改 .xlsx，验证增量更新
    time.sleep(0.05)
    _write_valid_workbook(xlsx, "SalesV2", ["name", "amount", "region"])

    reg.scan_workspace(excel_only=True)
    sales3 = reg.get_by_path("sales.xlsx")
    macro3 = reg.get_by_path("macro.xlsm")
    assert sales3 is not None and sales3.sheet_meta[0]["name"] == "SalesV2"
    assert sales3.mtime_ns > sales_mtime
    assert macro3 is not None and macro3.mtime_ns == macro_mtime
