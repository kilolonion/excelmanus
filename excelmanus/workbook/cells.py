"""单元格级内部辅助：值强制转换、合并格重定向、单元格 diff。

写入走 `edit_spreadsheet`；本模块不再提交工作簿。
"""

from __future__ import annotations

from typing import Any

from excelmanus.security import FileAccessGuard

_guard: FileAccessGuard | None = None


def init_guard(workspace_root: str) -> None:
    global _guard
    _guard = FileAccessGuard(workspace_root)


def _coerce_value(raw: Any) -> Any:
    """尊重 JSON/Python 类型；字符串不解析成数字。以 '=' 开头的保留为公式。"""
    if raw is None or not isinstance(raw, str):
        return raw
    stripped = raw.strip()
    if stripped.startswith("="):
        return stripped
    return raw


def assign_cell_value(ws: Any, row: int, col: int, raw: Any) -> None:
    """写入或清空单元格。``ws.cell(..., value=None)`` 不会改已有值。"""
    cell = ws.cell(row=row, column=col)
    cell.value = _coerce_value(raw)


def _resolve_merged_cell(ws: Any, row: int, col: int) -> tuple[int, int, bool]:
    """若 (row, col) 在合并区域内，返回主单元格坐标。"""
    for merged_range in ws.merged_cells.ranges:
        if (
            merged_range.min_row <= row <= merged_range.max_row
            and merged_range.min_col <= col <= merged_range.max_col
        ):
            if row == merged_range.min_row and col == merged_range.min_col:
                return row, col, False
            return merged_range.min_row, merged_range.min_col, True
    return row, col, False


def _compute_cell_diff(
    before: list[dict[str, Any]],
    after: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """对比写入前后快照，返回变化的单元格列表。"""
    before_map = {item["cell"]: item["value"] for item in before}
    after_map = {item["cell"]: item["value"] for item in after}
    before_style_map = {item["cell"]: item.get("style") for item in before}
    after_style_map = {item["cell"]: item.get("style") for item in after}
    all_cells = sorted(set(before_map) | set(after_map))
    changes: list[dict[str, Any]] = []
    for cell_ref in all_cells:
        old_val = before_map.get(cell_ref)
        new_val = after_map.get(cell_ref)
        old_s = before_style_map.get(cell_ref)
        new_s = after_style_map.get(cell_ref)
        if old_val != new_val:
            entry: dict[str, Any] = {
                "cell": cell_ref,
                "old": _serialize_cell_value(old_val),
                "new": _serialize_cell_value(new_val),
            }
            if old_s is not None:
                entry["old_style"] = old_s
            if new_s is not None:
                entry["new_style"] = new_s
            changes.append(entry)
        elif old_s != new_s and (old_s is not None or new_s is not None):
            entry = {
                "cell": cell_ref,
                "old": _serialize_cell_value(old_val),
                "new": _serialize_cell_value(new_val),
                "style_only": True,
            }
            if old_s is not None:
                entry["old_style"] = old_s
            if new_s is not None:
                entry["new_style"] = new_s
            changes.append(entry)
    return changes


def _serialize_cell_value(val: Any) -> Any:
    if val is None:
        return None
    if isinstance(val, (int, float, bool)):
        return val
    return str(val)
