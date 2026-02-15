"""WURM ingest 逻辑：数据提取、合并、写入与筛选。"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from openpyxl.utils.cell import get_column_letter, range_boundaries

from .models import CachedRange, ChangeRecord, ColumnDef, DetailLevel, WindowState


def extract_data_rows(result_json: dict[str, Any] | None, tool_name: str) -> list[dict[str, Any]]:
    """从工具结果提取行级数据。"""
    if not isinstance(result_json, dict):
        return []

    candidates = (
        result_json.get("data"),
        result_json.get("preview"),
        result_json.get("preview_after"),
    )
    for candidate in candidates:
        rows = _normalize_rows(candidate)
        if rows:
            return rows

    # metadata-only 工具可不返回数据行
    _ = tool_name
    return []


def extract_columns(result_json: dict[str, Any] | None, rows: list[dict[str, Any]]) -> list[ColumnDef]:
    """提取并推断列信息。"""
    raw_columns = result_json.get("columns") if isinstance(result_json, dict) else None
    names: list[str] = []

    if isinstance(raw_columns, list):
        names = [str(item).strip() for item in raw_columns if str(item).strip()]
    elif rows:
        names = [str(key) for key in rows[0].keys()]

    if not names:
        return []

    inferred: list[ColumnDef] = []
    for idx, name in enumerate(names):
        values = [row.get(name) for row in rows[:50]]
        inferred.append(ColumnDef(name=name, inferred_type=_infer_type(values, fallback_idx=idx)))
    return inferred


def ingest_read_result(
    window: WindowState,
    *,
    new_range: str,
    new_rows: list[dict[str, Any]],
    iteration: int,
) -> list[int]:
    """将读取结果写入窗口并执行范围块合并。"""
    if not new_range:
        new_range = window.viewport_range or "A1:A1"

    merged_index: int | None = None
    for idx, cached in enumerate(window.cached_ranges):
        if is_adjacent_or_overlapping(cached.range_ref, new_range):
            cached.range_ref = union_range(cached.range_ref, new_range)
            cached.rows = deduplicated_merge(cached.rows, new_rows)
            cached.is_current_viewport = True
            cached.added_at_iteration = iteration
            merged_index = idx
            break

    if merged_index is None:
        for cached in window.cached_ranges:
            cached.is_current_viewport = False
        window.cached_ranges.append(
            CachedRange(
                range_ref=new_range,
                rows=list(new_rows),
                is_current_viewport=True,
                added_at_iteration=iteration,
            )
        )
    else:
        for idx, cached in enumerate(window.cached_ranges):
            if idx != merged_index:
                cached.is_current_viewport = False

    _trim_cached_ranges(window)
    window.viewport_range = new_range
    _rebuild_data_buffer(window)
    window.stale_hint = None
    window.detail_level = DetailLevel.FULL
    window.idle_turns = 0

    if not new_rows:
        return []
    start = max(0, len(window.data_buffer) - len(new_rows))
    return list(range(start, len(window.data_buffer)))


def ingest_write_result(
    window: WindowState,
    *,
    target_range: str,
    result_json: dict[str, Any] | None,
    iteration: int,
) -> list[int]:
    """处理写入结果：优先局部更新，失败时仅 stale 标记。"""
    updated_rows: list[int] = []
    preview_after = result_json.get("preview_after") if isinstance(result_json, dict) else None
    normalized_preview = _to_matrix(preview_after)
    if normalized_preview and window.columns:
        updated_rows = _apply_preview_patch(window, target_range=target_range, matrix=normalized_preview)

    hint_range = target_range or window.viewport_range or "当前视口"
    window.stale_hint = f"{hint_range} 已修改，依赖此区域的公式值可能已变化"
    window.detail_level = DetailLevel.FULL
    window.current_iteration = iteration
    return updated_rows


def ingest_filter_result(
    window: WindowState,
    *,
    filter_condition: dict[str, Any],
    filtered_rows: list[dict[str, Any]],
    iteration: int,
) -> list[int]:
    """处理筛选结果。"""
    if window.unfiltered_buffer is None:
        window.unfiltered_buffer = list(window.data_buffer)

    window.data_buffer = list(filtered_rows)
    window.filter_state = dict(filter_condition)
    window.cached_ranges = [
        CachedRange(
            range_ref=window.viewport_range or "A1:A1",
            rows=list(filtered_rows),
            is_current_viewport=True,
            added_at_iteration=iteration,
        )
    ]
    window.detail_level = DetailLevel.FULL
    window.current_iteration = iteration
    if not filtered_rows:
        return []
    return list(range(len(filtered_rows)))


def is_adjacent_or_overlapping(range_a: str, range_b: str) -> bool:
    """判断两个矩形范围是否连续或重叠。"""
    try:
        a_min_col, a_min_row, a_max_col, a_max_row = range_boundaries(range_a)
        b_min_col, b_min_row, b_max_col, b_max_row = range_boundaries(range_b)
    except ValueError:
        return False

    col_gap = max(0, max(a_min_col, b_min_col) - min(a_max_col, b_max_col) - 1)
    row_gap = max(0, max(a_min_row, b_min_row) - min(a_max_row, b_max_row) - 1)
    return col_gap == 0 and row_gap == 0


def union_range(range_a: str, range_b: str) -> str:
    """合并两个范围为最小包围框。"""
    a_min_col, a_min_row, a_max_col, a_max_row = range_boundaries(range_a)
    b_min_col, b_min_row, b_max_col, b_max_row = range_boundaries(range_b)
    min_col = min(a_min_col, b_min_col)
    min_row = min(a_min_row, b_min_row)
    max_col = max(a_max_col, b_max_col)
    max_row = max(a_max_row, b_max_row)
    return f"{get_column_letter(min_col)}{min_row}:{get_column_letter(max_col)}{max_row}"


def deduplicated_merge(
    existing_rows: list[dict[str, Any]],
    incoming_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """按行内容去重合并，后出现的数据覆盖前值。"""
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for row in [*existing_rows, *incoming_rows]:
        key = _row_signature(row)
        if key not in merged:
            order.append(key)
        merged[key] = row
    return [merged[key] for key in order]


def make_change_record(
    *,
    operation: str,
    tool_summary: str,
    affected_range: str,
    change_type: str,
    iteration: int,
    affected_row_indices: list[int],
) -> ChangeRecord:
    """构造变更记录。"""
    return ChangeRecord(
        operation=operation,
        tool_summary=tool_summary,
        affected_range=affected_range,
        change_type=change_type,
        iteration=iteration,
        affected_row_indices=affected_row_indices,
    )


def _normalize_rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    rows: list[dict[str, Any]] = []
    for idx, item in enumerate(value):
        if isinstance(item, dict):
            rows.append({str(k): v for k, v in item.items()})
            continue
        if isinstance(item, list):
            rows.append({f"col_{col_idx + 1}": cell for col_idx, cell in enumerate(item)})
            continue
        rows.append({"value": item, "_idx": idx})
    return rows


def _infer_type(values: Iterable[Any], *, fallback_idx: int) -> str:
    _ = fallback_idx
    for value in values:
        if value is None:
            continue
        if isinstance(value, bool):
            return "text"
        if isinstance(value, (int, float)):
            return "number"
        text = str(value)
        if "-" in text and len(text) >= 8:
            return "date"
        return "text"
    return "unknown"


def _trim_cached_ranges(window: WindowState) -> None:
    total_rows = sum(len(cached.rows) for cached in window.cached_ranges)
    max_rows = max(1, int(window.max_cached_rows))
    while total_rows > max_rows:
        candidates = [item for item in window.cached_ranges if not item.is_current_viewport]
        if not candidates:
            break
        oldest = min(candidates, key=lambda item: item.added_at_iteration)
        total_rows -= len(oldest.rows)
        window.cached_ranges.remove(oldest)


def _rebuild_data_buffer(window: WindowState) -> None:
    buffer: list[dict[str, Any]] = []
    for cached in window.cached_ranges:
        buffer.extend(cached.rows)
    window.data_buffer = buffer


def _to_matrix(value: Any) -> list[list[Any]]:
    if not isinstance(value, list) or not value:
        return []
    matrix: list[list[Any]] = []
    for row in value:
        if isinstance(row, list):
            matrix.append(list(row))
        else:
            matrix.append([row])
    return matrix


def _apply_preview_patch(window: WindowState, *, target_range: str, matrix: list[list[Any]]) -> list[int]:
    try:
        min_col, min_row, _, _ = range_boundaries(target_range)
        viewport_min_col, viewport_min_row, _, _ = range_boundaries(window.viewport_range or target_range)
    except ValueError:
        return []

    if not window.data_buffer:
        return []

    updated: list[int] = []
    column_names = [col.name for col in window.columns]
    row_base = min_row - viewport_min_row
    col_base = min_col - viewport_min_col

    for r_idx, row_vals in enumerate(matrix):
        buffer_index = row_base + r_idx
        if buffer_index < 0 or buffer_index >= len(window.data_buffer):
            continue
        row_obj = window.data_buffer[buffer_index]
        for c_idx, value in enumerate(row_vals):
            col_index = col_base + c_idx
            if col_index < 0 or col_index >= len(column_names):
                continue
            row_obj[column_names[col_index]] = value
        updated.append(buffer_index)
    return updated


def _row_signature(row: dict[str, Any]) -> str:
    parts = [f"{key}={row.get(key)!r}" for key in sorted(row.keys())]
    return "|".join(parts)

