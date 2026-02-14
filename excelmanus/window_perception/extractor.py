"""窗口感知层数据提取器。"""

from __future__ import annotations

import json
import re
from typing import Any

from .perception_details import (
    compute_scroll_position,
    extract_column_widths,
    extract_conditional_effects,
    extract_merged_range_delta,
    extract_merged_ranges,
    extract_row_heights,
    extract_status_bar,
    extract_viewport_geometry,
)

__all__ = [
    "compute_scroll_position",
    "extract_column_widths",
    "extract_conditional_effects",
    "extract_directory",
    "extract_explorer_entries",
    "extract_file_path",
    "extract_freeze_panes",
    "extract_merged_range_delta",
    "extract_merged_ranges",
    "extract_preview_rows",
    "extract_range_ref",
    "extract_row_heights",
    "extract_shape",
    "extract_sheet_name",
    "extract_sheet_tabs",
    "extract_status_bar",
    "extract_style_summary",
    "extract_viewport_geometry",
    "is_excel_path",
    "normalize_path",
    "parse_json_payload",
]

_RANGE_RE = re.compile(r"^[A-Za-z]+\d+(?::[A-Za-z]+\d+)?$")


def parse_json_payload(text: str) -> dict[str, Any] | list[Any] | None:
    """将工具文本结果解析为 JSON。"""
    if not isinstance(text, str):
        return None
    content = text.strip()
    if not content:
        return None
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return None
    if isinstance(parsed, (dict, list)):
        return parsed
    return None


def normalize_path(path: Any) -> str:
    """规范化路径字符串。"""
    if not isinstance(path, str):
        return ""
    normalized = path.strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def is_excel_path(path: str) -> bool:
    """判断是否 Excel 文件路径。"""
    lower = (path or "").lower()
    return lower.endswith(".xlsx") or lower.endswith(".xlsm") or lower.endswith(".xls")


def extract_file_path(arguments: dict[str, Any], result_json: dict[str, Any] | None) -> str:
    """提取文件路径。"""
    keys = (
        "file_path",
        "source_file",
        "target_file",
        "fileAbsolutePath",
        "file",
        "path",
    )
    for key in keys:
        candidate = normalize_path(arguments.get(key))
        if candidate and (
            is_excel_path(candidate)
            or key in {"file", "file_path", "source_file", "target_file", "fileAbsolutePath"}
        ):
            return candidate

    if isinstance(result_json, dict):
        candidate = normalize_path(result_json.get("file"))
        if candidate and is_excel_path(candidate):
            return candidate
        candidate = normalize_path(result_json.get("path"))
        if candidate and is_excel_path(candidate):
            return candidate
    return ""


def extract_sheet_name(arguments: dict[str, Any], result_json: dict[str, Any] | None) -> str:
    """提取工作表名称。"""
    for key in ("sheet_name", "source_sheet", "target_sheet", "sheet"):
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    if isinstance(result_json, dict):
        for key in ("sheet", "source_sheet", "new_sheet", "current_sheet"):
            value = result_json.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def extract_sheet_tabs(result_json: dict[str, Any] | None) -> list[str]:
    """提取工作表标签列表。"""
    if not isinstance(result_json, dict):
        return []

    if isinstance(result_json.get("all_sheets"), list):
        return [str(item).strip() for item in result_json["all_sheets"] if str(item).strip()]

    if isinstance(result_json.get("sheets"), list):
        tabs: list[str] = []
        for item in result_json["sheets"]:
            if isinstance(item, dict):
                name = str(item.get("name", "")).strip()
                if name:
                    tabs.append(name)
            elif isinstance(item, str) and item.strip():
                tabs.append(item.strip())
        return tabs

    return []


def extract_shape(result_json: dict[str, Any] | None) -> tuple[int, int]:
    """提取行列规模。"""
    if not isinstance(result_json, dict):
        return 0, 0

    shape = result_json.get("shape")
    if isinstance(shape, dict):
        rows = _to_int(shape.get("rows"))
        cols = _to_int(shape.get("columns"))
        if rows > 0 or cols > 0:
            return rows, cols

    rows = _to_int(result_json.get("rows"))
    cols = _to_int(result_json.get("columns"))
    if rows > 0 or cols > 0:
        return rows, cols

    return 0, 0


def extract_preview_rows(result_json: dict[str, Any] | None) -> list[Any]:
    """提取预览数据。"""
    if not isinstance(result_json, dict):
        return []

    preview = result_json.get("preview")
    if isinstance(preview, list):
        return preview[:25]

    data = result_json.get("data")
    if isinstance(data, list):
        return data[:25]

    return []


def extract_freeze_panes(result_json: dict[str, Any] | None) -> str:
    """提取冻结窗格。"""
    if not isinstance(result_json, dict):
        return ""
    value = result_json.get("freeze_panes")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return ""


def extract_style_summary(result_json: dict[str, Any] | None) -> str:
    """提取样式摘要。"""
    if not isinstance(result_json, dict):
        return ""

    parts: list[str] = []
    if isinstance(result_json.get("styles"), dict):
        styles = result_json["styles"]
        classes = styles.get("style_classes")
        if isinstance(classes, dict) and classes:
            parts.append(f"样式类{len(classes)}种")
        merged = styles.get("merged_ranges")
        if isinstance(merged, list) and merged:
            parts.append(f"合并区域{len(merged)}处")

    conditional = result_json.get("conditional_formatting")
    if isinstance(conditional, list) and conditional:
        parts.append(f"条件格式{len(conditional)}条")

    if not parts:
        return ""
    return " | ".join(parts)


def extract_directory(arguments: dict[str, Any], result_json: dict[str, Any] | None) -> str:
    """提取目录路径。"""
    directory = normalize_path(arguments.get("directory"))
    if directory:
        return directory

    if isinstance(result_json, dict):
        candidate = normalize_path(result_json.get("directory"))
        if candidate:
            return candidate
    return "."


def extract_explorer_entries(result_json: dict[str, Any] | None) -> list[str]:
    """提取资源管理器条目摘要。"""
    if not isinstance(result_json, dict):
        return []

    entries: list[str] = []

    files = result_json.get("files")
    if isinstance(files, list):
        for item in files[:12]:
            if not isinstance(item, dict):
                continue
            file_name = str(item.get("file", "")).strip()
            if not file_name:
                continue
            modified = str(item.get("modified", "")).strip()
            size = str(item.get("size", "")).strip()
            sheets = item.get("sheets")
            sheet_count = len(sheets) if isinstance(sheets, list) else 0
            desc = f"📊 {file_name}"
            details: list[str] = []
            if size:
                details.append(size)
            if modified:
                details.append(modified)
            if sheet_count > 0:
                details.append(f"{sheet_count}个工作表")
            if details:
                desc += " (" + ", ".join(details) + ")"
            entries.append(desc)
        return entries

    if isinstance(result_json.get("entries"), list):
        for item in result_json["entries"][:20]:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            if not name:
                continue
            item_type = str(item.get("type", "")).strip()
            prefix = "📁" if item_type == "directory" else "📄"
            if is_excel_path(name):
                prefix = "📊"
            size = str(item.get("size", "")).strip()
            if size:
                entries.append(f"{prefix} {name} ({size})")
            else:
                entries.append(f"{prefix} {name}")
        return entries

    if isinstance(result_json.get("matches"), list):
        for item in result_json["matches"][:20]:
            if not isinstance(item, dict):
                continue
            path = str(item.get("path", item.get("name", ""))).strip()
            if not path:
                continue
            item_type = str(item.get("type", "")).strip()
            prefix = "📁" if item_type == "directory" else "📄"
            if is_excel_path(path):
                prefix = "📊"
            entries.append(f"{prefix} {path}")
        return entries

    return []


def extract_range_ref(
    arguments: dict[str, Any],
    *,
    default_rows: int,
    default_cols: int,
) -> str:
    """提取视口范围。"""
    for key in ("range", "cell_range", "source_range"):
        value = arguments.get(key)
        if isinstance(value, str) and _RANGE_RE.match(value.strip()):
            return value.strip().upper()

    cell = arguments.get("cell")
    if isinstance(cell, str) and _RANGE_RE.match(cell.strip()):
        normalized = cell.strip().upper()
        if ":" in normalized:
            return normalized
        return f"{normalized}:{normalized}"

    end_col = _col_letter(default_cols)
    return f"A1:{end_col}{default_rows}"


def _to_int(value: Any) -> int:
    try:
        if value is None:
            return 0
        return int(value)
    except (TypeError, ValueError):
        return 0


def _col_letter(index: int) -> str:
    idx = max(1, int(index))
    letters = ""
    while idx > 0:
        idx, rem = divmod(idx - 1, 26)
        letters = chr(65 + rem) + letters
    return letters
