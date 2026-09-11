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
    return format_spreadsheet(
        file_path=str(path),
        operations=operations,
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

    def test_fill_type_none(self) -> None:
        fill = _build_fill({"fill_type": "none"})
        # openpyxl 将 fill_type='none' 存储为 patternType=None
        assert fill.patternType is None


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
