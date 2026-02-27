"""共享的 openpyxl 单元格样式提取工具。

将 openpyxl Cell 对象的样式信息转为轻量 dict（Univer 兼容格式），
供 snapshot / diff / API 等多处复用。
"""

from __future__ import annotations

from typing import Any


# ── 颜色解析 ──────────────────────────────────────────────────

_IDX_COLORS = {
    0: "#000000", 1: "#FFFFFF", 2: "#FF0000", 3: "#00FF00",
    4: "#0000FF", 5: "#FFFF00", 6: "#FF00FF", 7: "#00FFFF",
    8: "#000000", 9: "#FFFFFF", 10: "#FF0000", 11: "#00FF00",
    12: "#0000FF", 13: "#FFFF00", 14: "#FF00FF", 15: "#00FFFF",
    16: "#800000", 17: "#008000", 18: "#000080", 19: "#808000",
    20: "#800080", 21: "#008080", 22: "#C0C0C0", 23: "#808080",
}

_THEME_COLORS = {
    0: "#FFFFFF", 1: "#000000", 2: "#44546A", 3: "#E7E6E6",
    4: "#4472C4", 5: "#ED7D31", 6: "#A5A5A5", 7: "#FFC000",
    8: "#5B9BD5", 9: "#70AD47",
}

_BORDER_STYLE_MAP = {
    "thin": 1, "medium": 2, "thick": 3, "dashed": 4,
    "dotted": 5, "double": 6, "hair": 7,
    "mediumDashed": 8, "dashDot": 9, "mediumDashDot": 10,
    "dashDotDot": 11, "mediumDashDotDot": 12, "slantDashDot": 13,
}


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


def resolve_color(color_obj: Any) -> str | None:
    """将 openpyxl Color 对象转为 #RRGGBB 字符串。"""
    if color_obj is None:
        return None
    try:
        if color_obj.type == "rgb" and color_obj.rgb and color_obj.rgb != "00000000":
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
            base = _THEME_COLORS.get(color_obj.theme)
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
            if font.size and font.size != 11:
                style["fs"] = font.size
            if font.name and font.name != "Calibri":
                style["ff"] = font.name
            fc = resolve_color(font.color)
            if fc:
                style["cl"] = {"rgb": fc}
    except Exception:
        pass
    try:
        fill = cell_obj.fill
        if fill and fill.patternType and fill.patternType != "none":
            bg = resolve_color(fill.fgColor)
            if bg:
                style["bg"] = {"rgb": bg}
    except Exception:
        pass
    try:
        alignment = cell_obj.alignment
        if alignment:
            h_map = {"left": 0, "center": 1, "right": 2, "justify": 3}
            v_map = {"top": 0, "center": 1, "bottom": 2}
            if alignment.horizontal and alignment.horizontal in h_map:
                style["ht"] = h_map[alignment.horizontal]
            if alignment.vertical and alignment.vertical in v_map:
                style["vt"] = v_map[alignment.vertical]
            if alignment.wrapText:
                style["tb"] = 1
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
                    bc = resolve_color(side.color)
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
