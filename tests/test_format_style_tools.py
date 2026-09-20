"""格式化工具增强功能测试：颜色映射、样式读取、合并单元格、行高调整。"""

from __future__ import annotations

from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill, Border, Side, Alignment

from excelmanus.engine_core.tool_result import ToolResult
from excelmanus.security import FileAccessGuard
from excelmanus.tools._guard_ctx import set_guard
from excelmanus.workbook.styles import (
    COLOR_NAME_MAP,
    _resolve_color,
    _build_font,
    _build_fill,
    _build_border,
    _build_side,
    _extract_font,
    _extract_fill,
    _extract_border,
    _extract_alignment,
    _color_to_hex,
    read_cell_styles,
    init_guard,
)
from excelmanus.tools.intent_tools import format_spreadsheet
from excelmanus.tools.intent_tools import init_guard as init_intent_guard
from excelmanus.workbook_commit import content_version_of_file, seed_seen_versions


@pytest.fixture()
def sample_xlsx(tmp_path: Path) -> Path:
    """创建一个带有样式的示例 Excel 文件。"""
    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"

    # 写入数据
    ws["A1"] = "标题"
    ws["B1"] = "数据"
    ws["A2"] = 100
    ws["B2"] = 200
    ws["A3"] = 300
    ws["B3"] = 400

    # 给 A1 设置样式
    ws["A1"].font = Font(name="微软雅黑", size=14, bold=True, color="FF0000")
    ws["A1"].fill = PatternFill(start_color="FFFF00", end_color="FFFF00", fill_type="solid")
    ws["A1"].border = Border(
        left=Side(style="thin", color="000000"),
        right=Side(style="medium", color="000000"),
        top=Side(style="thin", color="000000"),
        bottom=Side(style="thick", color="000000"),
    )
    ws["A1"].alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    # 合并 A3:B3
    ws.merge_cells("A3:B3")

    file_path = tmp_path / "test_styled.xlsx"
    wb.save(file_path)
    wb.close()
    return file_path


@pytest.fixture(autouse=True)
def _init_guard(tmp_path: Path) -> None:
    workspace = str(tmp_path)
    set_guard(FileAccessGuard(workspace))
    init_guard(workspace)
    init_intent_guard(workspace)
    seed_seen_versions({})


def _format(path: Path, operations: list[dict]) -> ToolResult:
    # 注入文件里真实的活动表名；硬编码 "Sheet1" 会依赖已被移除的静默纠错
    from openpyxl import load_workbook

    wb = load_workbook(path, read_only=True)
    try:
        active = wb.active.title if wb.active is not None else ""
    finally:
        wb.close()
    filled = []
    for op in operations:
        item = dict(op)
        if "sheet" not in item and "sheet_name" not in item:
            raw_range = str(item.get("range") or "")
            if "!" not in raw_range and active:
                item["sheet"] = active
        filled.append(item)
    return format_spreadsheet(
        file_path=str(path),
        operations=filled,
        expected_version=content_version_of_file(path),
    )


# ── _resolve_color 测试 ──────────────────────────────────


class TestResolveColor:
    def test_chinese_color_name(self) -> None:
        assert _resolve_color("红色") == "FF0000"
        assert _resolve_color("浅蓝") == "5B9BD5"
        assert _resolve_color("深绿色") == "006100"

    def test_english_color_name(self) -> None:
        assert _resolve_color("red") == "FF0000"
        assert _resolve_color("blue") == "0000FF"
        assert _resolve_color("lightgray") == "D3D3D3"

    def test_hex_code_passthrough(self) -> None:
        assert _resolve_color("FF0000") == "FF0000"
        assert _resolve_color("00ff00") == "00FF00"

    def test_hex_with_hash(self) -> None:
        assert _resolve_color("#FF0000") == "FF0000"
        assert _resolve_color("#00ff00") == "00FF00"

    def test_none_returns_none(self) -> None:
        assert _resolve_color(None) is None

    def test_unknown_passthrough(self) -> None:
        assert _resolve_color("unknown_color") == "unknown_color"


# ── _build_font 增强测试 ─────────────────────────────────


class TestBuildFont:
    def test_underline(self) -> None:
        font = _build_font({"underline": "single"})
        assert font.underline == "single"

    def test_strikethrough(self) -> None:
        font = _build_font({"strikethrough": True})
        assert font.strike is True

    def test_color_name_resolved(self) -> None:
        font = _build_font({"color": "红色"})
        # openpyxl 的 Font.color 是 Color 对象，rgb 格式为 AARRGGBB
        assert font.color.rgb.endswith("FF0000")


# ── _build_fill 增强测试 ─────────────────────────────────


class TestBuildFill:
    def test_color_name_resolved(self) -> None:
        fill = _build_fill({"color": "浅黄色"})
        assert fill.start_color.rgb == "00FFF2CC"

    def test_pattern_and_type_aliases(self) -> None:
        fill = _build_fill({"color": "FF0000", "pattern": "solid"})
        assert fill.patternType == "solid"
        fill2 = _build_fill({"color": "FF0000", "type": "solid"})
        assert fill2.patternType == "solid"


# ── _build_border 增强测试 ───────────────────────────────


class TestBuildBorder:
    def test_uniform_mode(self) -> None:
        border = _build_border({"style": "medium", "color": "红色"})
        assert border.left.style == "medium"
        assert border.left.color.rgb == "00FF0000"

    def test_per_side_mode(self) -> None:
        border = _build_border({
            "left": {"style": "thin", "color": "000000"},
            "top": {"style": "thick"},
        })
        assert border.left.style == "thin"
        assert border.top.style == "thick"
        # right/bottom 未指定，应为默认空 Side
        assert border.right.style is None
        assert border.bottom.style is None

    def test_side_string_shorthand(self) -> None:
        side = _build_side("medium")
        assert side.style == "medium"


# ── _extract_* 测试 ──────────────────────────────────────


class TestExtractFunctions:
    def test_extract_font_non_default(self) -> None:
        font = Font(name="Arial", size=14, bold=True, color="FF0000")
        info = _extract_font(font)
        assert info is not None
        assert info["name"] == "Arial"
        assert info["size"] == 14
        assert info["bold"] is True
        assert info["color"] == "FF0000"

    def test_extract_font_default_returns_none(self) -> None:
        font = Font()
        info = _extract_font(font)
        assert info is None

    def test_extract_fill_solid(self) -> None:
        fill = PatternFill(start_color="FFFF00", fill_type="solid")
        info = _extract_fill(fill)
        assert info is not None
        assert info["type"] == "solid"
        assert info["color"] == "FFFF00"

    def test_extract_fill_none_returns_none(self) -> None:
        fill = PatternFill(fill_type=None)
        info = _extract_fill(fill)
        assert info is None

    def test_extract_border_with_styles(self) -> None:
        border = Border(left=Side(style="thin"), top=Side(style="medium"))
        info = _extract_border(border)
        assert info is not None
        assert info["left"] == "thin"
        assert info["top"] == "medium"

    def test_extract_border_empty_returns_none(self) -> None:
        border = Border()
        info = _extract_border(border)
        assert info is None

    def test_extract_alignment_non_default(self) -> None:
        alignment = Alignment(horizontal="center", wrap_text=True)
        info = _extract_alignment(alignment)
        assert info is not None
        assert info["horizontal"] == "center"
        assert info["wrap_text"] is True

    def test_extract_alignment_default_returns_none(self) -> None:
        alignment = Alignment()
        info = _extract_alignment(alignment)
        assert info is None


# ── read_cell_styles 测试 ────────────────────────────────


class TestReadCellStyles:
    def test_reads_styled_cells(self, sample_xlsx: Path) -> None:
        result = read_cell_styles(str(sample_xlsx), "A1:B3").value
        assert result["status"] == "success"
        assert result["total_cells"] == 6
        # A1 应该被检测到有样式
        styled = result["styled_cells"]
        a1_entries = [c for c in styled if c["cell"] == "A1"]
        assert len(a1_entries) == 1
        a1 = a1_entries[0]
        assert a1["font"]["bold"] is True
        assert a1["font"]["name"] == "微软雅黑"
        assert "fill" in a1
        assert "border" in a1
        assert "alignment" in a1

    def test_summary_only(self, sample_xlsx: Path) -> None:
        result = read_cell_styles(str(sample_xlsx), "A1:B3", summary_only=True).value
        assert result["status"] == "success"
        assert "styled_cells" not in result
        assert "summary" in result
        assert result["summary"]["has_merged_cells"] is True
        assert "A3:B3" in result["summary"]["merged_ranges"]

    def test_detects_merged_cells(self, sample_xlsx: Path) -> None:
        result = read_cell_styles(str(sample_xlsx), "A1:B3").value
        # A3 是合并区域的一部分
        a3_entries = [c for c in result["styled_cells"] if c["cell"] == "A3"]
        assert any(e.get("merged") for e in a3_entries)


# ── merge / unmerge 测试 ─────────────────────────────────


class TestMergeCells:
    def test_merge_and_unmerge(self, tmp_path: Path) -> None:
        wb = Workbook()
        ws = wb.active
        ws["A1"] = "合并测试"
        ws["B1"] = "数据"
        file_path = tmp_path / "merge_test.xlsx"
        wb.save(file_path)
        wb.close()

        result = _format(file_path, [{"kind": "merge", "range": "A1:B1"}])
        assert not result.success
        assert result.value["affected_cells"] == ["B1"]
        unchanged = load_workbook(file_path)
        assert unchanged.active["B1"].value == "数据"
        unchanged.close()
        result = _format(file_path, [{"kind": "merge", "range": "A1:B1", "allow_data_loss": True}])
        assert result.success
        styles = read_cell_styles(str(file_path), "A1:B1").value
        assert styles["summary"]["has_merged_cells"] is True

        result = _format(file_path, [{"kind": "unmerge", "range": "A1:B1"}])
        assert result.success


class TestAdjustRowHeight:
    def test_manual_row_height(self, tmp_path: Path) -> None:
        wb = Workbook()
        ws = wb.active
        ws["A1"] = "行高测试"
        file_path = tmp_path / "row_height.xlsx"
        wb.save(file_path)
        wb.close()

        result = _format(file_path, [{"kind": "size", "rows": {"1": 30.0, "2": 25.0}}])
        assert result.success
        wb = load_workbook(file_path)
        assert wb.active.row_dimensions[1].height == 30.0
        assert wb.active.row_dimensions[2].height == 25.0
        wb.close()

    def test_auto_fit_row_height(self, tmp_path: Path) -> None:
        wb = Workbook()
        ws = wb.active
        ws["A1"] = "测试"
        file_path = tmp_path / "row_auto.xlsx"
        wb.save(file_path)
        wb.close()

        result = _format(file_path, [{"kind": "size", "auto_fit": True, "axis": "row"}])
        assert result.success
        wb = load_workbook(file_path)
        assert wb.active.row_dimensions[1].height is not None
        wb.close()


class TestFormatCellsColorName:
    def test_chinese_color_name_in_font(self, tmp_path: Path) -> None:
        wb = Workbook()
        ws = wb.active
        ws["A1"] = "颜色测试"
        file_path = tmp_path / "color_name.xlsx"
        wb.save(file_path)
        wb.close()

        result = _format(
            file_path,
            [{
                "kind": "format",
                "range": "A1",
                "font": {"color": "红色", "bold": True},
                "fill": {"color": "浅黄色"},
            }],
        )
        assert result.success
        styles = read_cell_styles(str(file_path), "A1").value
        a1 = styles["styled_cells"][0]
        assert a1["font"]["bold"] is True
        assert a1["font"]["color"] == "FF0000"


class TestFormatFreezeAndWholeColumn:
    def test_freeze_first_row_and_appearance_echo(self, tmp_path: Path) -> None:
        wb = Workbook()
        ws = wb.active
        ws.title = "Sheet1"
        ws["A1"] = "h"
        ws["A2"] = 1
        path = tmp_path / "freeze.xlsx"
        wb.save(path)
        wb.close()
        result = _format(
            path,
            [{"kind": "freeze", "sheet": "Sheet1", "freeze_panes": "A2"}],
        )
        assert result.success
        payload = result.value
        assert any(str(item).startswith("freeze:") for item in payload["applied"])
        sheets = payload["appearance"]["sheets"]
        assert sheets[0]["freeze_panes"] == "A2"
        from openpyxl import load_workbook

        check = load_workbook(path)
        assert check.active.freeze_panes == "A2"
        check.close()

    def test_whole_column_clips_to_used_range(self, tmp_path: Path) -> None:
        wb = Workbook()
        ws = wb.active
        ws.title = "Sheet1"
        ws["B1"] = "金额"
        ws["B2"] = 100
        ws["B3"] = 200
        path = tmp_path / "col.xlsx"
        wb.save(path)
        wb.close()
        result = _format(
            path,
            [{"kind": "format", "sheet": "Sheet1", "range": "B:B", "number_format": "#,##0"}],
        )
        assert result.success, result.model_text
        from openpyxl import load_workbook

        check = load_workbook(path)
        assert check.active["B2"].number_format == "#,##0"
        assert check.active["B3"].number_format == "#,##0"
        check.close()

    def test_auto_fit_and_freeze_same_batch(self, tmp_path: Path) -> None:
        wb = Workbook()
        ws = wb.active
        ws.title = "Sheet1"
        ws["A1"] = "很长的表头标题"
        ws["A2"] = 1
        path = tmp_path / "both.xlsx"
        wb.save(path)
        wb.close()
        result = _format(
            path,
            [
                {"kind": "size", "sheet": "Sheet1", "auto_fit": True},
                {"kind": "freeze", "sheet": "Sheet1", "freeze_panes": "A2"},
            ],
        )
        assert result.success
        freeze = result.value["appearance"]["sheets"][0]["freeze_panes"]
        assert freeze == "A2"
        widths = result.value["appearance"]["sheets"][0]["column_widths"]
        assert widths



def test_size_columns_name_list_means_autofit(tmp_path: Path) -> None:
    """kind=size 的 columns 传列名列表 ["A","B"] 时按"选列自适应"处理。"""
    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws["A1"] = "非常长非常长的表头标题文字"
    ws["B1"] = "x"
    path = tmp_path / "size_names.xlsx"
    wb.save(path)
    wb.close()
    result = _format(
        path,
        [{"kind": "size", "sheet": "Sheet1", "columns": ["A", "B"]}],
    )
    assert result.success, result.model_text
    widths = result.value["appearance"]["sheets"][0]["column_widths"]
    assert widths.get("A")
    # 列名列表语义是自适应选列，不应报缺 columns/rows
    assert "size:auto_fit" in str(result.model_text) or widths


def test_conditional_format_cell_value_rule(tmp_path: Path) -> None:
    """kind=conditional_format 把 cell_value 规则落盘为真条件格式（R27 用例）。"""
    from openpyxl import load_workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "订单"
    ws["A1"] = "金额"
    for i, v in enumerate([500, 12000, 300], start=2):
        ws.cell(row=i, column=1, value=v)
    path = tmp_path / "cf.xlsx"
    wb.save(path)
    wb.close()

    result = _format(
        path,
        [{
            "kind": "conditional_format",
            "sheet": "订单",
            "range": "A2:A4",
            "rule": {
                "type": "cell_value",
                "operator": "ge",
                "value": 10000,
                "fill": {"color": "浅红"},
                "font": {"color": "深红", "bold": True},
            },
        }],
    )
    assert result.success, result.model_text

    wb2 = load_workbook(path)
    rules = list(wb2["订单"].conditional_formatting)
    wb2.close()
    assert len(rules) == 1
    cf = rules[0]
    assert cf.rules[0].type == "cellIs"
    assert cf.rules[0].operator == "greaterThanOrEqual"
    assert cf.rules[0].formula == ["10000"]


def test_conditional_format_union_and_rule_types(tmp_path: Path) -> None:
    """并集 sqref + data_bar / duplicate / top_n / formula 类型可用。"""
    from openpyxl import load_workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "S"
    for i in range(1, 8):
        ws.cell(row=i, column=1, value=i)
        ws.cell(row=i, column=3, value=i * 10)
    path = tmp_path / "cf2.xlsx"
    wb.save(path)
    wb.close()

    result = _format(
        path,
        [
            {"kind": "conditional_format", "sheet": "S", "range": "A1:A3,A5:A7",
             "rule": {"type": "data_bar", "bar_color": "638EC6"}},
            {"kind": "conditional_format", "sheet": "S", "range": "C1:C7",
             "rule": {"type": "duplicate", "fill": {"color": "浅黄"}}},
            {"kind": "conditional_format", "sheet": "S", "range": "A1:A7",
             "rule": {"type": "top_n", "n": 3}},
        ],
    )
    assert result.success, result.model_text

    wb2 = load_workbook(path)
    rules = list(wb2["S"].conditional_formatting)
    wb2.close()
    types = sorted(cf.rules[0].type for cf in rules)
    assert types == ["dataBar", "duplicateValues", "top10"]


def test_conditional_format_bad_rule_is_invalid_args(tmp_path: Path) -> None:
    """缺 value 阈值 / 未知 type 报 INVALID_ARGS，不写盘。"""
    wb = Workbook()
    ws = wb.active
    ws.title = "S"
    ws["A1"] = 1
    path = tmp_path / "cf3.xlsx"
    wb.save(path)
    wb.close()

    result = _format(
        path,
        [{"kind": "conditional_format", "sheet": "S", "range": "A1:A5",
          "rule": {"type": "cell_value", "operator": "ge"}}],
    )
    assert not result.success
    assert "value" in result.model_text

    result2 = _format(
        path,
        [{"kind": "conditional_format", "sheet": "S", "range": "A1:A5",
          "rule": {"type": "wat"}}],
    )
    assert not result2.success
    assert "type" in result2.model_text


def _two_sheet_book(tmp_path: Path, name: str = "two.xlsx") -> Path:
    wb = Workbook()
    wb.active.title = "明细"
    wb.create_sheet("汇总")
    path = tmp_path / name
    wb.save(path)
    wb.close()
    return path


class TestFormatSheetContract:
    """P0.1 / P0.3：单表绑定、多表 available_sheets、错误列全缺失。"""

    def test_single_sheet_omit_sheet_with_range_succeeds(self, sample_xlsx: Path) -> None:
        result = format_spreadsheet(
            file_path=str(sample_xlsx),
            operations=[{"kind": "format", "range": "A1:B1", "font": {"bold": True}}],
            expected_version=content_version_of_file(sample_xlsx),
        )
        assert result.success, result.model_text

    def test_two_sheet_omit_sheet_fails_with_available_sheets(self, tmp_path: Path) -> None:
        path = _two_sheet_book(tmp_path)
        result = format_spreadsheet(
            file_path=str(path),
            operations=[{"kind": "format", "range": "A1:B1", "font": {"bold": True}}],
            expected_version=content_version_of_file(path),
        )
        assert not result.success
        payload = result.value if isinstance(result.value, dict) else {}
        assert "available_sheets" in payload
        assert set(payload["available_sheets"]) >= {"明细", "汇总"}
        assert payload.get("error_code") == "SHEET_REQUIRED"
        assert "sheet" in (result.model_text or "")

    def test_missing_kind_range_sheet_lists_all_and_example(self, tmp_path: Path) -> None:
        path = _two_sheet_book(tmp_path, "triple.xlsx")
        result = format_spreadsheet(
            file_path=str(path),
            operations=[{"font": {"bold": True}}],
            expected_version=content_version_of_file(path),
        )
        assert not result.success
        text = result.model_text or ""
        assert "sheet" in text
        assert "range" in text
        assert "kind" in text or "缺省" in text
        assert "最小合法示例" in text
        assert "必须提供 sheet" not in text or "range" in text
        payload = result.value if isinstance(result.value, dict) else {}
        assert payload.get("example")
        assert "available_sheets" in payload
        missing = payload.get("missing_fields") or []
        assert "range" in missing
        assert "sheet" in missing

    def test_single_sheet_omit_sheet_and_range_names_range(self, sample_xlsx: Path) -> None:
        result = format_spreadsheet(
            file_path=str(sample_xlsx),
            operations=[{"font": {"bold": True}}],
            expected_version=content_version_of_file(sample_xlsx),
        )
        assert not result.success
        text = result.model_text or ""
        assert "range" in text
        payload = result.value if isinstance(result.value, dict) else {}
        missing = payload.get("missing_fields") or []
        assert "range" in missing
        assert "sheet" not in missing

    def test_size_omit_sheet_on_single_sheet_with_columns(self, tmp_path: Path) -> None:
        """R30-1：kind=size 把表名塞进 range，单表应绑定成功。"""
        wb = Workbook()
        wb.active.title = "区域月度汇总"
        wb.active["A1"] = 1
        path = tmp_path / "size.xlsx"
        wb.save(path)
        wb.close()
        result = format_spreadsheet(
            file_path=str(path),
            operations=[{
                "kind": "size",
                "range": "区域月度汇总",
                "columns": {"A": 10, "B": 12},
            }],
            expected_version=content_version_of_file(path),
        )
        assert result.success, result.model_text
