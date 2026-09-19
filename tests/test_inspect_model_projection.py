"""E01：inspect/analyze 正文必须带上行列、隐藏表、小窗口 values 与未缓存公式。"""

from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook

from excelmanus.security import FileAccessGuard
from excelmanus.tools._guard_ctx import set_guard
from excelmanus.tools.intent_tools import analyze_spreadsheet, init_guard, inspect_spreadsheet
from excelmanus.workbook.data import init_guard as init_data_guard
from excelmanus.workbook.sheets import init_guard as init_sheets_guard
from excelmanus.workbook_commit import seed_seen_versions


def _bind(root: Path) -> None:
    workspace = str(root)
    set_guard(FileAccessGuard(workspace))
    init_guard(workspace)
    init_data_guard(workspace)
    init_sheets_guard(workspace)
    seed_seen_versions({})


def _messy_book(path: Path) -> Path:
    wb = Workbook()
    detail = wb.active
    assert detail is not None
    detail.title = "明细"
    headers = ["区域", "数量", "单价", "金额", "备注", "日期", "销售", "渠道"]
    detail.append(headers)
    detail["A2"] = "华东"
    detail["B2"] = 2
    detail["C2"] = 3
    detail["D2"] = "=B2*C2"
    for row in range(3, 15):
        detail[f"A{row}"] = "西  部" if row % 2 else "east"
        detail[f"B{row}"] = row
        detail[f"C{row}"] = 10
        detail[f"D{row}"] = row * 10
    notes = wb.create_sheet("随便写写")
    notes["A1"] = "口头备忘"
    hidden = wb.create_sheet("隐藏底稿")
    hidden["A1"] = "华东"
    hidden["B1"] = 860000
    hidden.sheet_state = "hidden"
    wb.save(path)
    wb.close()
    return path


def test_overview_model_text_has_shape_hidden_and_include(tmp_path: Path) -> None:
    _bind(tmp_path)
    path = _messy_book(tmp_path / "q3.xlsx")
    result = inspect_spreadsheet(
        mode="overview",
        file_path=str(path),
        include=["columns", "merges", "formulas"],
    )
    assert result.success
    text = result.model_text
    assert "3 sheets" in text
    assert "明细" in text and "14×8" in text
    assert "隐藏底稿" in text and "hidden" in text
    assert "列=区域,数量,单价,金额" in text
    assert "公式" in text and "=B2*C2" in text
    sheets = result.value["sheets"]
    hidden = next(item for item in sheets if item["name"] == "隐藏底稿")
    assert hidden["sheet_state"] == "hidden"
    assert hidden["hidden"] is True


def test_overview_lists_multiple_formula_samples(tmp_path: Path) -> None:
    _bind(tmp_path)
    path = tmp_path / "kpi.xlsx"
    wb = Workbook()
    raw = wb.active
    assert raw is not None
    raw.title = "原始数"
    raw["B3"] = 95
    raw["C3"] = 0
    dash = wb.create_sheet("看板")
    dash["B2"] = "=原始数!B3/原始数!C3"
    dash["B3"] = "=SUM(原始数!B2:B2)"
    dash["B4"] = "=去年看板!B9"
    dash["B5"] = "=原始数!B4/原始数!B3-1"
    wb.save(path)
    wb.close()
    result = inspect_spreadsheet(mode="overview", file_path=str(path), include=["formulas"])
    assert result.success
    text = result.model_text
    assert "公式4" in text
    assert "B2=" in text
    assert "B3=" in text
    assert "B4=" in text
    assert "B5=" in text
    assert "SUM(原始数!B2:B2)" in text
    assert "去年看板!B9" in text


def test_small_range_projects_full_window_and_uncached_formula(tmp_path: Path) -> None:
    _bind(tmp_path)
    path = _messy_book(tmp_path / "q3.xlsx")
    result = inspect_spreadsheet(
        mode="range",
        file_path=str(path),
        sheet="明细",
        range="A1:H14",
    )
    assert result.success
    text = result.model_text
    assert "14 行 × 8 列" in text
    assert "values:" in text
    assert "华东" in text
    assert "西  部" in text
    assert "前 3 行样本" not in text
    assert "单元格样本" not in text
    assert '"cell": "D2"' in text or "'cell': 'D2'" in text or '"cell":"D2"' in text
    assert "=B2*C2" in text
    formulas_at = text.find("formulas:")
    values_at = text.find("values:")
    assert formulas_at != -1 and values_at != -1 and formulas_at < values_at
    assert result.value.get("formulas")
    assert result.value["values"][1][3] is None


def test_quality_model_text_scopes_sheet_and_names_columns(tmp_path: Path) -> None:
    _bind(tmp_path)
    path = _messy_book(tmp_path / "q3.xlsx")
    result = analyze_spreadsheet(
        mode="quality",
        file_path=str(path),
        sheet="明细",
    )
    assert result.success
    text = result.model_text
    assert "本次只扫「明细」" in text
    assert "missing_data(明细." in text or "empty_column(明细." in text
    assert '"quality_signals"' not in text
