"""窗口感知细节计算：滚动位置、状态栏、样式细节提取。"""

from __future__ import annotations

import re
from typing import Any

_RANGE_RE = re.compile(r"^[A-Za-z]+\d+(?::[A-Za-z]+\d+)?$")


def extract_viewport_geometry(
    range_ref: str,
    *,
    default_rows: int,
    default_cols: int,
) -> dict[str, int]:
    """解析视口范围几何信息。"""
    normalized = (range_ref or "").strip().upper()
    if not _RANGE_RE.match(normalized):
        return {
            "start_row": 1,
            "end_row": max(1, int(default_rows)),
            "start_col": 1,
            "end_col": max(1, int(default_cols)),
            "visible_rows": max(1, int(default_rows)),
            "visible_cols": max(1, int(default_cols)),
        }

    if ":" in normalized:
        start_ref, end_ref = normalized.split(":", 1)
    else:
        start_ref, end_ref = normalized, normalized
    start_col_letters, start_row = _split_cell_ref(start_ref)
    end_col_letters, end_row = _split_cell_ref(end_ref)
    start_col = _col_to_index(start_col_letters)
    end_col = _col_to_index(end_col_letters)
    if start_col <= 0 or end_col <= 0 or start_row <= 0 or end_row <= 0:
        return {
            "start_row": 1,
            "end_row": max(1, int(default_rows)),
            "start_col": 1,
            "end_col": max(1, int(default_cols)),
            "visible_rows": max(1, int(default_rows)),
            "visible_cols": max(1, int(default_cols)),
        }

    if end_row < start_row:
        start_row, end_row = end_row, start_row
    if end_col < start_col:
        start_col, end_col = end_col, start_col
    return {
        "start_row": start_row,
        "end_row": end_row,
        "start_col": start_col,
        "end_col": end_col,
        "visible_rows": max(1, end_row - start_row + 1),
        "visible_cols": max(1, end_col - start_col + 1),
    }


def compute_scroll_position(
    geometry: dict[str, int],
    *,
    total_rows: int,
    total_cols: int,
) -> dict[str, float]:
    """根据视口与总规模计算滚动条位置与剩余比例。"""
    visible_rows = max(1, _to_int(geometry.get("visible_rows")))
    visible_cols = max(1, _to_int(geometry.get("visible_cols")))
    start_row = max(1, _to_int(geometry.get("start_row")))
    start_col = max(1, _to_int(geometry.get("start_col")))
    end_row = max(start_row, _to_int(geometry.get("end_row")))
    end_col = max(start_col, _to_int(geometry.get("end_col")))

    total_rows = max(0, _to_int(total_rows))
    total_cols = max(0, _to_int(total_cols))

    vertical_pct = 0.0
    horizontal_pct = 0.0
    if total_rows > visible_rows:
        vertical_pct = ((start_row - 1) / max(1, total_rows - visible_rows)) * 100.0
    if total_cols > visible_cols:
        horizontal_pct = ((start_col - 1) / max(1, total_cols - visible_cols)) * 100.0

    remaining_rows_pct = 0.0
    remaining_cols_pct = 0.0
    if total_rows > 0:
        remaining_rows_pct = max(0.0, (total_rows - min(total_rows, end_row)) / total_rows * 100.0)
    if total_cols > 0:
        remaining_cols_pct = max(0.0, (total_cols - min(total_cols, end_col)) / total_cols * 100.0)

    return {
        "vertical_pct": round(_clamp_pct(vertical_pct), 1),
        "horizontal_pct": round(_clamp_pct(horizontal_pct), 1),
        "remaining_rows_pct": round(_clamp_pct(remaining_rows_pct), 1),
        "remaining_cols_pct": round(_clamp_pct(remaining_cols_pct), 1),
    }


def extract_status_bar(preview_rows: list[Any]) -> dict[str, float | int]:
    """从预览数据近似计算状态栏 SUM/COUNT/AVERAGE。"""
    numeric_values: list[float] = []
    for row in preview_rows:
        if isinstance(row, dict):
            values = row.values()
        elif isinstance(row, list):
            values = row
        else:
            values = (row,)
        for value in values:
            parsed = _parse_numeric(value)
            if parsed is not None:
                numeric_values.append(parsed)

    if not numeric_values:
        return {}

    total = sum(numeric_values)
    count = len(numeric_values)
    average = total / max(1, count)
    return {
        "sum": round(total, 4),
        "count": count,
        "average": round(average, 4),
    }


def extract_column_widths(
    result_json: dict[str, Any] | None,
    *,
    sheet_name: str = "",
) -> dict[str, float]:
    """提取列宽映射。"""
    if not isinstance(result_json, dict):
        return {}

    direct = _coerce_width_map(result_json.get("column_widths"))
    if direct:
        return direct

    adjusted = _coerce_width_map(result_json.get("columns_adjusted"))
    if adjusted:
        return adjusted

    sheets = result_json.get("sheets")
    if isinstance(sheets, list):
        for item in sheets:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            if sheet_name and name and name != sheet_name:
                continue
            widths = _coerce_width_map(item.get("column_widths"))
            if widths:
                return widths

    return {}


def extract_row_heights(result_json: dict[str, Any] | None) -> dict[str, float]:
    """提取行高映射。"""
    if not isinstance(result_json, dict):
        return {}

    direct = _coerce_height_map(result_json.get("row_heights"))
    if direct:
        return direct

    adjusted = _coerce_height_map(result_json.get("rows_adjusted"))
    if adjusted:
        return adjusted

    return {}


def extract_merged_ranges(result_json: dict[str, Any] | None) -> list[str]:
    """提取合并单元格范围列表。"""
    if not isinstance(result_json, dict):
        return []

    styles = result_json.get("styles")
    if isinstance(styles, dict) and isinstance(styles.get("merged_ranges"), list):
        return _normalize_range_list(styles.get("merged_ranges"))

    summary = result_json.get("summary")
    if isinstance(summary, dict) and isinstance(summary.get("merged_ranges"), list):
        return _normalize_range_list(summary.get("merged_ranges"))

    if isinstance(result_json.get("merged_ranges"), list):
        return _normalize_range_list(result_json.get("merged_ranges"))

    return []


def extract_merged_range_delta(result_json: dict[str, Any] | None) -> tuple[list[str], list[str]]:
    """提取合并/取消合并操作增量。"""
    if not isinstance(result_json, dict):
        return [], []

    add_range = str(result_json.get("merged_range") or "").strip().upper()
    remove_range = str(result_json.get("unmerged_range") or "").strip().upper()
    adds = [add_range] if add_range and _RANGE_RE.match(add_range) else []
    removes = [remove_range] if remove_range and _RANGE_RE.match(remove_range) else []
    return adds, removes


def extract_conditional_effects(result_json: dict[str, Any] | None) -> list[str]:
    """提取条件格式视觉效果摘要。"""
    if not isinstance(result_json, dict):
        return []

    rules = result_json.get("conditional_formatting")
    if not isinstance(rules, list):
        return []

    effects: list[str] = []
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        range_ref = str(rule.get("range") or "").strip()
        rule_type = str(rule.get("type") or "").strip()
        operator = str(rule.get("operator") or "").strip()
        effect = _conditional_type_to_effect(rule_type) or "条件着色"
        if range_ref and rule_type and operator:
            effects.append(f"{range_ref}: {effect}（{rule_type}/{operator}）")
        elif range_ref and rule_type:
            effects.append(f"{range_ref}: {effect}（{rule_type}）")
        elif range_ref:
            effects.append(f"{range_ref}: {effect}")
        elif rule_type:
            effects.append(f"{effect}（{rule_type}）")
        else:
            effects.append(effect)

    deduped: list[str] = []
    seen: set[str] = set()
    for item in effects:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
        if len(deduped) >= 12:
            break
    return deduped


def _to_int(value: Any) -> int:
    try:
        if value is None:
            return 0
        return int(value)
    except (TypeError, ValueError):
        return 0


def _split_cell_ref(cell_ref: str) -> tuple[str, int]:
    match = re.match(r"^([A-Z]+)(\d+)$", str(cell_ref or "").strip().upper())
    if not match:
        return "", 0
    return match.group(1), _to_int(match.group(2))


def _col_to_index(col_letters: str) -> int:
    normalized = str(col_letters or "").strip().upper()
    if not normalized or not normalized.isalpha():
        return 0
    value = 0
    for ch in normalized:
        value = value * 26 + (ord(ch) - ord("A") + 1)
    return value


def _clamp_pct(value: float) -> float:
    return max(0.0, min(100.0, float(value)))


def _parse_numeric(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized:
        return None
    is_percent = normalized.endswith("%")
    cleaned = normalized.rstrip("%").replace(",", "").replace(" ", "")
    if not cleaned:
        return None
    try:
        parsed = float(cleaned)
    except ValueError:
        return None
    if is_percent:
        parsed = parsed / 100.0
    return parsed


def _coerce_width_map(value: Any) -> dict[str, float]:
    if not isinstance(value, dict):
        return {}
    parsed: dict[str, float] = {}
    for key, item in value.items():
        col = str(key or "").strip().upper()
        if not col:
            continue
        try:
            parsed[col] = round(float(item), 2)
        except (TypeError, ValueError):
            continue
    return parsed


def _coerce_height_map(value: Any) -> dict[str, float]:
    if not isinstance(value, dict):
        return {}
    parsed: dict[str, float] = {}
    for key, item in value.items():
        row = str(key or "").strip()
        if not row:
            continue
        try:
            parsed[row] = round(float(item), 2)
        except (TypeError, ValueError):
            continue
    return parsed


def _normalize_range_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    normalized: list[str] = []
    for item in value:
        text = str(item or "").strip().upper()
        if not text:
            continue
        if ":" in text:
            start_ref, end_ref = text.split(":", 1)
            if _RANGE_RE.match(f"{start_ref}:{end_ref}"):
                normalized.append(text)
                continue
        if _RANGE_RE.match(text):
            normalized.append(text)
    return normalized


def _conditional_type_to_effect(rule_type: str) -> str:
    key = str(rule_type or "").strip().lower()
    mapping = {
        "colorscale": "渐变色",
        "databar": "数据条",
        "iconset": "图标集",
        "cellis": "条件着色",
        "expression": "条件着色",
        "containstext": "条件着色",
        "duplicatevalues": "条件着色",
        "top10": "条件着色",
        "aboveaverage": "条件着色",
        "timeperiod": "条件着色",
    }
    return mapping.get(key, "")
