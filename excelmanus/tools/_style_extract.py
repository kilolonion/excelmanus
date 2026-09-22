"""共享的 openpyxl 单元格样式提取工具。

将 openpyxl Cell 对象的样式信息转为轻量 dict（Univer 兼容格式），
供 snapshot / diff / API 等多处复用。
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from openpyxl.styles.colors import COLOR_INDEX
from openpyxl.xml.functions import fromstring


# ── 颜色解析 ──────────────────────────────────────────────────

_IDX_COLORS = {index: f"#{rgb[-6:]}" for index, rgb in enumerate(COLOR_INDEX)}

_THEME_COLORS = {
    0: "#FFFFFF", 1: "#000000", 2: "#EEECE1", 3: "#1F497D",
    4: "#4F81BD", 5: "#C0504D", 6: "#9BBB59", 7: "#8064A2",
    8: "#4BACC6", 9: "#F79646", 10: "#0000FF", 11: "#800080",
}

_BORDER_STYLE_MAP = {
    "thin": 1, "hair": 2, "dotted": 3, "dashed": 4,
    "dashDot": 5, "dashDotDot": 6, "double": 7, "medium": 8,
    "mediumDashed": 9, "mediumDashDot": 10,
    "mediumDashDotDot": 11, "slantDashDot": 12, "thick": 13,
}


@lru_cache(maxsize=32)
def _theme_colors(theme: bytes | str | None) -> dict[int, str]:
    """Theme indices use light/dark order, not clrScheme's XML child order."""
    if not theme:
        return _THEME_COLORS
    colors = dict(_THEME_COLORS)
    try:
        ns = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
        scheme = fromstring(theme).find(f"{ns}themeElements/{ns}clrScheme")
        if scheme is None:
            return colors
        names = ("lt1", "dk1", "lt2", "dk2", "accent1", "accent2", "accent3",
                 "accent4", "accent5", "accent6", "hlink", "folHlink")
        for index, name in enumerate(names):
            entry = scheme.find(f"{ns}{name}")
            if entry is not None and len(entry):
                value = entry[0].get("lastClr") or entry[0].get("val")
                if value and len(value) == 6:
                    int(value, 16)
                    colors[index] = f"#{value.upper()}"
    except (ValueError, TypeError, SyntaxError):
        pass
    return colors


def _apply_tint(hex_color: str, tint: float) -> str:
    """对 #RRGGBB 颜色应用 Excel tint/shade 修饰。

    tint > 0 → 向白色靠近（变亮），tint < 0 → 向黑色靠近（变暗）。
    """
    r = int(hex_color[1:3], 16)
    g = int(hex_color[3:5], 16)
    b = int(hex_color[5:7], 16)
    if tint > 0:
        r = int(r + (255 - r) * tint)
        g = int(g + (255 - g) * tint)
        b = int(b + (255 - b) * tint)
    elif tint < 0:
        factor = 1 + tint
        r = int(r * factor)
        g = int(g * factor)
        b = int(b * factor)
    r = max(0, min(255, r))
    g = max(0, min(255, g))
    b = max(0, min(255, b))
    return f"#{r:02X}{g:02X}{b:02X}"


def resolve_color(color_obj: Any, theme_colors: dict[int, str] | None = None) -> str | None:
    """将 openpyxl Color 对象转为 #RRGGBB 字符串。"""
    if color_obj is None:
        return None
    try:
        if color_obj.type == "rgb" and color_obj.rgb:
            rgb = str(color_obj.rgb)
            if len(rgb) == 8:
                hex_val = f"#{rgb[2:]}"
            elif len(rgb) == 6:
                hex_val = f"#{rgb}"
            else:
                return None
            tint = getattr(color_obj, "tint", 0.0) or 0.0
            return _apply_tint(hex_val, tint) if tint else hex_val
        if color_obj.type == "indexed" and color_obj.indexed is not None:
            return _IDX_COLORS.get(color_obj.indexed)
        if color_obj.type == "theme" and color_obj.theme is not None:
            base = (theme_colors if theme_colors is not None else _THEME_COLORS).get(color_obj.theme)
            if base is None:
                return None
            tint = getattr(color_obj, "tint", 0.0) or 0.0
            return _apply_tint(base, tint) if tint else base
    except Exception:
        pass
    return None


def extract_cell_style(cell_obj: Any) -> dict[str, Any] | None:
    """提取单元格样式，返回 Univer 兼容的样式 dict，无样式返回 None。"""
    style: dict[str, Any] = {}
    workbook = getattr(getattr(cell_obj, "parent", None), "parent", None)
    theme_colors = _theme_colors(getattr(workbook, "loaded_theme", None))
    try:
        font = cell_obj.font
        if font:
            if font.bold:
                style["bl"] = 1
            if font.italic:
                style["it"] = 1
            if font.underline and font.underline != "none":
                style["ul"] = {"s": 1}
            if font.strike:
                style["st"] = {"s": 1}
            if font.size:
                style["fs"] = font.size
            if font.name:
                style["ff"] = font.name
            fc = resolve_color(font.color, theme_colors)
            if fc:
                style["cl"] = {"rgb": fc}
    except Exception:
        pass
    try:
        fill = cell_obj.fill
        if fill and fill.patternType and fill.patternType != "none":
            bg = resolve_color(fill.fgColor, theme_colors)
            if bg:
                style["bg"] = {"rgb": bg}
    except Exception:
        pass
    try:
        alignment = cell_obj.alignment
        if alignment:
            h_map = {"left": 1, "center": 2, "right": 3, "justify": 4}
            v_map = {"top": 1, "center": 2, "bottom": 3}
            if alignment.horizontal and alignment.horizontal in h_map:
                style["ht"] = h_map[alignment.horizontal]
            if alignment.vertical and alignment.vertical in v_map:
                style["vt"] = v_map[alignment.vertical]
            if alignment.wrapText:
                style["tb"] = 3
            if alignment.textRotation:
                style["tr"] = {"a": alignment.textRotation}
            if alignment.indent and alignment.indent > 0:
                style["pd"] = {"l": alignment.indent}
            if alignment.shrinkToFit:
                style["sk"] = 1
    except Exception:
        pass
    try:
        border = cell_obj.border
        if border:
            for side_name, univer_key in [("left", "l"), ("right", "r"), ("top", "t"), ("bottom", "b")]:
                side = getattr(border, side_name, None)
                if side and side.style:
                    bd_entry: dict[str, Any] = {"s": _BORDER_STYLE_MAP.get(side.style, 1)}
                    bc = resolve_color(side.color, theme_colors)
                    if bc:
                        bd_entry["cl"] = {"rgb": bc}
                    style.setdefault("bd", {})[univer_key] = bd_entry
    except Exception:
        pass
    try:
        nf = cell_obj.number_format
        if nf and nf != "General":
            style["n"] = {"pattern": nf}
    except Exception:
        pass
    return style if style else None


def extract_merge_ranges(ws: Any) -> list[dict[str, int]]:
    """提取工作表中的合并单元格区域。

    Returns:
        列表，每项为 {"min_row", "min_col", "max_row", "max_col"}（1-based）。
    """
    merges: list[dict[str, int]] = []
    try:
        for mr in ws.merged_cells.ranges:
            merges.append({
                "min_row": mr.min_row,
                "min_col": mr.min_col,
                "max_row": mr.max_row,
                "max_col": mr.max_col,
            })
    except Exception:
        pass
    return merges


def extract_worksheet_hints(ws: Any) -> list[str]:
    """提取工作表中不可在 diff 预览中展示的元数据特征，返回人类可读的提示列表。"""
    hints: list[str] = []
    try:
        tables = getattr(ws, "_tables", None) or getattr(ws, "tables", None)
        if tables:
            tbl_list = list(tables)
            for tbl in tbl_list[:3]:
                name = getattr(tbl, "displayName", None) or getattr(tbl, "name", "")
                ref = getattr(tbl, "ref", "")
                hints.append(f"表格: {name} ({ref})" if ref else f"表格: {name}")
            if len(tbl_list) > 3:
                hints.append(f"…及另外 {len(tbl_list) - 3} 个表格")
    except Exception:
        pass
    try:
        af = getattr(ws, "auto_filter", None)
        if af and af.ref:
            hints.append(f"自动筛选: {af.ref}")
    except Exception:
        pass
    try:
        cf = getattr(ws, "conditional_formatting", None)
        if cf:
            cf_list = list(cf)
            if cf_list:
                hints.append(f"条件格式: {len(cf_list)} 条规则")
    except Exception:
        pass
    try:
        dv = getattr(ws, "data_validations", None)
        if dv:
            dv_list = getattr(dv, "dataValidation", None) or []
            if dv_list:
                hints.append(f"数据验证: {len(dv_list)} 条规则")
    except Exception:
        pass
    try:
        charts = getattr(ws, "_charts", None)
        if charts:
            hints.append(f"图表: {len(charts)} 个")
    except Exception:
        pass
    try:
        images = getattr(ws, "_images", None)
        if images:
            hints.append(f"图片: {len(images)} 张")
    except Exception:
        pass
    return hints
