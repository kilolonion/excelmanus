"""属性测试：Excel 扩展名扫描一致性。

Feature: excel-extension-inconsistency
- Property 1 (fault condition): .xls/.xlsb 也应被 manifest 扫描
- Property 2 (preservation): .xlsx/.xlsm 原有行为保持不变
"""

from __future__ import annotations

import time
from pathlib import Path

from openpyxl import Workbook

from excelmanus.workspace_manifest import build_manifest, refresh_manifest


def _write_valid_workbook(path: Path, sheet_name: str, headers: list[str]) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_name
    ws.append(headers)
    ws.append(["v" for _ in headers])
    wb.save(path)


def test_fault_condition(tmp_path: Path) -> None:
    """Property 1: 触发 bug 的输入（.xls/.xlsb）应被 build/refresh 扫描到。"""
    # 先放置两个历史扩展文件（内容无需可读，目标是验证扫描范围）
    (tmp_path / "report.xls").write_bytes(b"dummy-xls")
    (tmp_path / "data.xlsb").write_bytes(b"dummy-xlsb")
    _write_valid_workbook(tmp_path / "baseline.xlsx", "Base", ["A", "B"])

    manifest = build_manifest(str(tmp_path))
    built_names = {f.name for f in manifest.files}
    assert "report.xls" in built_names
    assert "data.xlsb" in built_names

    # refresh 路径：新增一个 .xls 文件后，增量更新也应能检测到
    (tmp_path / "newly_added.xls").write_bytes(b"dummy-new-xls")
    refreshed = refresh_manifest(manifest)
    refreshed_names = {f.name for f in refreshed.files}
    assert "newly_added.xls" in refreshed_names


def test_preservation(tmp_path: Path) -> None:
    """Property 2: .xlsx/.xlsm 的扫描与增量缓存行为保持不变。"""
    xlsx = tmp_path / "sales.xlsx"
    xlsm = tmp_path / "macro.xlsm"
    _write_valid_workbook(xlsx, "Sales", ["name", "amount"])
    _write_valid_workbook(xlsm, "Macro", ["k", "v"])

    manifest = build_manifest(str(tmp_path))
    names = {f.name for f in manifest.files}
    assert names == {"sales.xlsx", "macro.xlsm"}

    sales_before = next(f for f in manifest.files if f.name == "sales.xlsx")
    macro_before = next(f for f in manifest.files if f.name == "macro.xlsm")
    assert sales_before.sheets and sales_before.sheets[0].name == "Sales"
    assert macro_before.sheets and macro_before.sheets[0].name == "Macro"

    # 无文件变化时 refresh 应复用缓存元数据
    refreshed_no_change = refresh_manifest(manifest)
    sales_no_change = next(f for f in refreshed_no_change.files if f.name == "sales.xlsx")
    macro_no_change = next(f for f in refreshed_no_change.files if f.name == "macro.xlsm")
    assert sales_no_change.modified_ts == sales_before.modified_ts
    assert macro_no_change.modified_ts == macro_before.modified_ts
    assert sales_no_change.sheets[0].name == "Sales"
    assert macro_no_change.sheets[0].name == "Macro"

    # 修改 .xlsx，验证增量更新逻辑仍生效（仅变化文件会重新扫描）
    time.sleep(0.05)
    _write_valid_workbook(xlsx, "SalesV2", ["name", "amount", "region"])

    refreshed_changed = refresh_manifest(refreshed_no_change)
    sales_changed = next(f for f in refreshed_changed.files if f.name == "sales.xlsx")
    macro_unchanged = next(f for f in refreshed_changed.files if f.name == "macro.xlsm")

    assert sales_changed.sheets and sales_changed.sheets[0].name == "SalesV2"
    assert sales_changed.modified_ts > sales_before.modified_ts
    assert macro_unchanged.modified_ts == macro_before.modified_ts
