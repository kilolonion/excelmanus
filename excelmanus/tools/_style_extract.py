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


def resolve_color(color_obj: Any) -> str | None:
    """将 openpyxl Color 对象转为 #RRGGBB 字符串。"""
    if color_obj is None:
        return None
    try:
        if color_obj.type == "rgb" and color_obj.rgb and color_obj.rgb != "00000000":
            rgb = str(color_obj.rgb)
            if len(rgb) == 8:
                return f"#{rgb[2:]}"
            elif len(rgb) == 6:
                return f"#{rgb}"
        if color_obj.type == "indexed" and color_obj.indexed is not None:
            return _IDX_COLORS.get(color_obj.indexed)
        if color_obj.type == "theme" and color_obj.theme is not None:
            return _THEME_COLORS.get(color_obj.theme)
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
