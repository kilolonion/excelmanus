"""高级格式工具：阈值图标、卡片样式、单位缩放、暗色仪表盘。"""

from __future__ import annotations

import json
import re
from typing import Any

from openpyxl import load_workbook
from openpyxl.cell.cell import Cell, MergedCell
from openpyxl.chart.shapes import GraphicalProperties
from openpyxl.drawing.text import CharacterProperties
from openpyxl.styles import Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.utils.cell import range_boundaries

from excelmanus.logger import get_logger
from excelmanus.security import FileAccessGuard
from excelmanus.tools.format_tools import COLOR_NAME_MAP
from excelmanus.tools.registry import ToolDef

logger = get_logger("tools.advanced_format")
SKILL_NAME = "advanced_format"
SKILL_DESCRIPTION = "高级格式工具：阈值图标、卡片样式、单位缩放、暗色仪表盘"
_guard: FileAccessGuard | None = None


def _get_guard() -> FileAccessGuard:
    global _guard
    if _guard is None:
        _guard = FileAccessGuard(".")
    return _guard


def init_guard(workspace_root: str) -> None:
    global _guard
    _guard = FileAccessGuard(workspace_root)


def _iter_cells(ws: Any, cell_range: str) -> list[Cell | MergedCell]:
    cell_data = ws[cell_range]
    if isinstance(cell_data, (Cell, MergedCell)):
        return [cell_data]
    if isinstance(cell_data, tuple) and cell_data and not isinstance(cell_data[0], tuple):
        return list(cell_data)
    return [cell for row in cell_data for cell in row]


def _normalize_hex_color(value: str | None, fallback: str) -> str:
    if value is None:
        return fallback
    raw = str(value).strip()
    if not raw:
        return fallback
    if raw in COLOR_NAME_MAP:
        return COLOR_NAME_MAP[raw]
    lower = raw.lower()
    if lower in COLOR_NAME_MAP:
        return COLOR_NAME_MAP[lower]
    if raw.startswith("#"):
        raw = raw[1:]
    if re.fullmatch(r"[0-9A-Fa-f]{6}", raw):
        return raw.upper()
    return fallback


def _to_display_number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:.12g}"


def _to_divisor_text(divisor: float) -> str:
    return str(int(divisor)) if float(divisor).is_integer() else f"{divisor:.12g}"


def _is_formula_scaled(formula: str, divisor_text: str) -> bool:
    compact = re.sub(r"\s+", "", formula)
    return bool(re.search(rf"/{re.escape(divisor_text)}$", compact))


def _build_unit_number_format(*, decimals: int, suffix: str, thousand_separator: bool) -> str:
    int_part = "#,##0" if thousand_separator else "0"
    decimal_part = f".{('0' * decimals)}" if decimals > 0 else ""
    safe_suffix = suffix.replace('"', '""')
    return f'{int_part}{decimal_part}"{safe_suffix}"'


def _set_chart_title_color(chart: Any, color_hex: str) -> None:
    title = getattr(chart, "title", None)
    if title is None or getattr(title, "tx", None) is None:
        return
    rich = getattr(title.tx, "rich", None)
    if rich is None:
        return
    for paragraph in rich.p or []:
        for run in paragraph.r or []:
            run.rPr = run.rPr or CharacterProperties()
            run.rPr.solidFill = color_hex


def apply_threshold_icon_format(
    file_path: str,
    cell_range: str,
    sheet_name: str | None = None,
    high_threshold: float = 1.2,
    mid_threshold: float = 0.8,
    high_symbol: str = "↑",
    mid_symbol: str = "—",
    low_symbol: str = "↓",
    high_color: str = "Green",
    mid_color: str = "Yellow",
    low_color: str = "Red",
    show_value: bool = False,
    decimals: int = 2,
) -> str:
    """三段阈值图标化显示。使用 number_format 精确支持中间“横线”符号。"""
    if high_threshold <= mid_threshold:
        raise ValueError("high_threshold 必须大于 mid_threshold")
    if decimals < 0:
        raise ValueError("decimals 不能小于 0")

    guard = _get_guard()
    safe_path = guard.resolve_and_validate(file_path)
    wb = load_workbook(safe_path)
    ws = wb[sheet_name] if sheet_name and sheet_name in wb.sheetnames else wb.active

    high_s, mid_s, low_s = (s.replace('"', '""') for s in (high_symbol, mid_symbol, low_symbol))
    high_t, mid_t = _to_display_number(high_threshold), _to_display_number(mid_threshold)
    value_fmt = f"0.{('0' * decimals)}" if decimals > 0 else "0"
    if show_value:
        number_format = (
            f'[{high_color}][>={high_t}]{value_fmt}" {high_s}";'
            f'[{mid_color}][>={mid_t}]{value_fmt}" {mid_s}";'
            f'[{low_color}]{value_fmt}" {low_s}"'
        )
    else:
        number_format = (
            f'[{high_color}][>={high_t}]"{high_s}";'
            f'[{mid_color}][>={mid_t}]"{mid_s}";'
            f'[{low_color}]"{low_s}"'
        )

    cells = _iter_cells(ws, cell_range)
    for cell in cells:
        cell.number_format = number_format

    wb.save(safe_path)
    wb.close()
    return json.dumps(
        {
            "status": "success",
            "file": safe_path.name,
            "range": cell_range,
            "cells_formatted": len(cells),
            "number_format": number_format,
        },
        ensure_ascii=False,
    )


def style_card_blocks(
    file_path: str,
    sheet_name: str,
    ranges: list[str],
    card_fill_color: str = "2E3A46",
    border_color: str = "5A6B7A",
    shadow_color: str = "1A2128",
    text_color: str = "FFFFFF",
    border_style: str = "thick",
    corner_style: str = "medium",
    add_shadow: bool = True,
) -> str:
    """批量应用卡片样式（粗边框 + 圆角模拟 + 阴影模拟）。"""
    if not ranges:
        raise ValueError("ranges 不能为空")

    guard = _get_guard()
    safe_path = guard.resolve_and_validate(file_path)
    wb = load_workbook(safe_path)
    ws = wb[sheet_name]

    card_fill = PatternFill(fill_type="solid", fgColor=_normalize_hex_color(card_fill_color, "2E3A46"))
    shadow_fill = PatternFill(fill_type="solid", fgColor=_normalize_hex_color(shadow_color, "1A2128"))
    text_font = Font(color=_normalize_hex_color(text_color, "FFFFFF"))

    border_hex = _normalize_hex_color(border_color, "5A6B7A")
    edge_side = Side(style=border_style, color=border_hex)
    corner_side = Side(style=corner_style, color=border_hex)

    details: list[dict[str, Any]] = []
    for block in ranges:
        min_col, min_row, max_col, max_row = range_boundaries(block)
        for r in range(min_row, max_row + 1):
            for c in range(min_col, max_col + 1):
                cell = ws.cell(r, c)
                cell.fill = card_fill
                cell.font = text_font
                top = edge_side if r == min_row else Side()
                bottom = edge_side if r == max_row else Side()
                left = edge_side if c == min_col else Side()
                right = edge_side if c == max_col else Side()
                if r == min_row and c == min_col:
                    top, left = corner_side, corner_side
                elif r == min_row and c == max_col:
                    top, right = corner_side, corner_side
                elif r == max_row and c == min_col:
                    bottom, left = corner_side, corner_side
                elif r == max_row and c == max_col:
                    bottom, right = corner_side, corner_side
                cell.border = Border(left=left, right=right, top=top, bottom=bottom)

        if add_shadow:
            shadow_row, shadow_col = max_row + 1, max_col + 1
            for c in range(min_col + 1, max_col + 2):
                ws.cell(shadow_row, c).fill = shadow_fill
            for r in range(min_row + 1, max_row + 2):
                ws.cell(r, shadow_col).fill = shadow_fill

        details.append(
            {
                "range": block,
                "shadow_applied": add_shadow,
                "anchor": f"{get_column_letter(min_col)}{min_row}",
            }
        )

    wb.save(safe_path)
    wb.close()
    return json.dumps(
        {
            "status": "success",
            "file": safe_path.name,
            "sheet": sheet_name,
            "blocks_styled": len(details),
            "details": details,
        },
        ensure_ascii=False,
    )


def scale_range_unit(
    file_path: str,
    cell_range: str,
    sheet_name: str | None = None,
    divisor: float = 10000.0,
    suffix: str = "万",
    decimals: int = 2,
    thousand_separator: bool = False,
    scale_formulas: bool = True,
) -> str:
    """按除数缩放范围内数值/公式，并设置统一单位格式。"""
    if divisor <= 0:
        raise ValueError("divisor 必须大于 0")
    if decimals < 0:
        raise ValueError("decimals 不能小于 0")

    guard = _get_guard()
    safe_path = guard.resolve_and_validate(file_path)
    wb = load_workbook(safe_path)
    ws = wb[sheet_name] if sheet_name and sheet_name in wb.sheetnames else wb.active

    number_format = _build_unit_number_format(decimals=decimals, suffix=suffix, thousand_separator=thousand_separator)
    divisor_text = _to_divisor_text(divisor)
    numeric_scaled = formula_scaled = skipped = 0

    for cell in _iter_cells(ws, cell_range):
        value = cell.value
        converted = False
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            cell.value = float(value) / divisor
            numeric_scaled += 1
            converted = True
        elif isinstance(value, str) and value.startswith("=") and scale_formulas:
            formula = value[1:].strip()
            if not _is_formula_scaled(formula, divisor_text):
                cell.value = f"=({formula})/{divisor_text}"
                formula_scaled += 1
                converted = True
        elif isinstance(value, str):
            raw = value.replace(",", "").strip()
            if raw:
                try:
                    parsed = float(raw)
                except ValueError:
                    parsed = None
                if parsed is not None:
                    cell.value = parsed / divisor
                    numeric_scaled += 1
                    converted = True

        if converted or value is not None:
            cell.number_format = number_format
        if not converted and value is not None:
            skipped += 1

    wb.save(safe_path)
    wb.close()
    return json.dumps(
        {
            "status": "success",
            "file": safe_path.name,
            "range": cell_range,
            "number_format": number_format,
            "divisor": divisor,
            "numeric_scaled": numeric_scaled,
            "formula_scaled": formula_scaled,
            "skipped": skipped,
        },
        ensure_ascii=False,
    )


def apply_dashboard_dark_theme(
    file_path: str,
    sheet_name: str,
    cell_range: str | None = None,
    background_color: str = "1B2631",
    text_color: str = "DDE3EA",
    card_ranges: list[str] | None = None,
    card_fill_color: str = "2E3A46",
    card_border_color: str = "455A64",
    metric_ranges: list[str] | None = None,
    metric_color: str = "4FC3F7",
    chart_style: int = 13,
    chart_area_color: str = "1B2631",
    plot_area_color: str = "243447",
    chart_title_color: str = "E5E7EB",
) -> str:
    """暗色仪表盘主题：基础底色、卡片区、指标高亮、图表区域样式。"""
    guard = _get_guard()
    safe_path = guard.resolve_and_validate(file_path)
    wb = load_workbook(safe_path)
    ws = wb[sheet_name]

    if cell_range is None:
        cell_range = f"A1:{get_column_letter(ws.max_column)}{ws.max_row}"

    bg_fill = PatternFill(fill_type="solid", fgColor=_normalize_hex_color(background_color, "1B2631"))
    base_font = Font(color=_normalize_hex_color(text_color, "DDE3EA"))
    base_cells = _iter_cells(ws, cell_range)
    for cell in base_cells:
        cell.fill = bg_fill
        cell.font = base_font

    if card_ranges:
        card_fill = PatternFill(fill_type="solid", fgColor=_normalize_hex_color(card_fill_color, "2E3A46"))
        side = Side(style="medium", color=_normalize_hex_color(card_border_color, "455A64"))
        for block in card_ranges:
            min_col, min_row, max_col, max_row = range_boundaries(block)
            for r in range(min_row, max_row + 1):
                for c in range(min_col, max_col + 1):
                    cell = ws.cell(r, c)
                    cell.fill = card_fill
                    cell.border = Border(
                        left=side if c == min_col else Side(),
                        right=side if c == max_col else Side(),
                        top=side if r == min_row else Side(),
                        bottom=side if r == max_row else Side(),
                    )

    if metric_ranges:
        metric_font = Font(color=_normalize_hex_color(metric_color, "4FC3F7"), bold=True)
        for rg in metric_ranges:
            for cell in _iter_cells(ws, rg):
                cell.font = metric_font

    chart_count = 0
    chart_errors: list[str] = []
    chart_area_hex = _normalize_hex_color(chart_area_color, "1B2631")
    plot_area_hex = _normalize_hex_color(plot_area_color, "243447")
    title_hex = _normalize_hex_color(chart_title_color, "E5E7EB")

    for idx, chart in enumerate(getattr(ws, "_charts", []), start=1):
        chart_count += 1
        try:
            if chart_style > 0:
                chart.style = chart_style
            chart.graphical_properties = GraphicalProperties(solidFill=chart_area_hex)
            plot_area = getattr(chart, "plot_area", None)
            if plot_area is not None:
                plot_area.graphicalProperties = GraphicalProperties(solidFill=plot_area_hex)
            _set_chart_title_color(chart, title_hex)
        except Exception as exc:  # noqa: BLE001
            chart_errors.append(f"chart#{idx}: {exc}")

    wb.save(safe_path)
    wb.close()
    return json.dumps(
        {
            "status": "success",
            "file": safe_path.name,
            "sheet": sheet_name,
            "base_cells": len(base_cells),
            "card_blocks": len(card_ranges or []),
            "metric_ranges": len(metric_ranges or []),
            "charts_total": chart_count,
            "chart_errors": chart_errors,
        },
        ensure_ascii=False,
    )


def get_tools() -> list[ToolDef]:
    """返回高级格式工具定义。"""
    border_style_enum = ["thin", "medium", "thick", "double", "dotted", "dashed"]
    return [
        ToolDef(
            name="apply_threshold_icon_format",
            description="三段阈值图标化显示（支持精确上/横/下符号）",
            input_schema={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "sheet_name": {"type": "string"},
                    "cell_range": {"type": "string"},
                    "high_threshold": {"type": "number", "default": 1.2},
                    "mid_threshold": {"type": "number", "default": 0.8},
                    "high_symbol": {"type": "string", "default": "↑"},
                    "mid_symbol": {"type": "string", "default": "—"},
                    "low_symbol": {"type": "string", "default": "↓"},
                    "high_color": {"type": "string", "default": "Green"},
                    "mid_color": {"type": "string", "default": "Yellow"},
                    "low_color": {"type": "string", "default": "Red"},
                    "show_value": {"type": "boolean", "default": False},
                    "decimals": {"type": "integer", "default": 2},
                },
                "required": ["file_path", "cell_range"],
                "additionalProperties": False,
            },
            func=apply_threshold_icon_format,
        ),
        ToolDef(
            name="style_card_blocks",
            description="批量卡片化区域（粗边框+圆角与阴影模拟）",
            input_schema={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "sheet_name": {"type": "string"},
                    "ranges": {"type": "array", "items": {"type": "string"}},
                    "card_fill_color": {"type": "string", "default": "2E3A46"},
                    "border_color": {"type": "string", "default": "5A6B7A"},
                    "shadow_color": {"type": "string", "default": "1A2128"},
                    "text_color": {"type": "string", "default": "FFFFFF"},
                    "border_style": {"type": "string", "enum": border_style_enum, "default": "thick"},
                    "corner_style": {"type": "string", "enum": border_style_enum, "default": "medium"},
                    "add_shadow": {"type": "boolean", "default": True},
                },
                "required": ["file_path", "sheet_name", "ranges"],
                "additionalProperties": False,
            },
            func=style_card_blocks,
        ),
        ToolDef(
            name="scale_range_unit",
            description="按除数缩放数值/公式并统一单位格式（如元转万）",
            input_schema={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "sheet_name": {"type": "string"},
                    "cell_range": {"type": "string"},
                    "divisor": {"type": "number", "default": 10000.0},
                    "suffix": {"type": "string", "default": "万"},
                    "decimals": {"type": "integer", "default": 2},
                    "thousand_separator": {"type": "boolean", "default": False},
                    "scale_formulas": {"type": "boolean", "default": True},
                },
                "required": ["file_path", "cell_range"],
                "additionalProperties": False,
            },
            func=scale_range_unit,
        ),
        ToolDef(
            name="apply_dashboard_dark_theme",
            description="暗色仪表盘主题（单元格、卡片和图表样式）",
            input_schema={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "sheet_name": {"type": "string"},
                    "cell_range": {"type": "string"},
                    "background_color": {"type": "string", "default": "1B2631"},
                    "text_color": {"type": "string", "default": "DDE3EA"},
                    "card_ranges": {"type": "array", "items": {"type": "string"}},
                    "card_fill_color": {"type": "string", "default": "2E3A46"},
                    "card_border_color": {"type": "string", "default": "455A64"},
                    "metric_ranges": {"type": "array", "items": {"type": "string"}},
                    "metric_color": {"type": "string", "default": "4FC3F7"},
                    "chart_style": {"type": "integer", "default": 13},
                    "chart_area_color": {"type": "string", "default": "1B2631"},
                    "plot_area_color": {"type": "string", "default": "243447"},
                    "chart_title_color": {"type": "string", "default": "E5E7EB"},
                },
                "required": ["file_path", "sheet_name"],
                "additionalProperties": False,
            },
            func=apply_dashboard_dark_theme,
        ),
    ]
