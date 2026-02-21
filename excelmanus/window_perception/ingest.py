"""WURM ingest 逻辑：数据提取、合并、写入与筛选。"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .domain import Window
from .models import CachedRange, ChangeRecord, ColumnDef, DetailLevel


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
    """提取并推断列信息。

    当列名为 Unnamed 模式时，从数据行中提取样本值作为标注，
    帮助 LLM 更准确地识别列含义。
    """
    raw_columns = result_json.get("columns") if isinstance(result_json, dict) else None
    names: list[str] = []

    if isinstance(raw_columns, list):
        names = [str(item).strip() for item in raw_columns if str(item).strip()]
    elif rows:
        names = [str(key) for key in rows[0].keys()]

    if not names:
        return []

    # 构建原始名称到标注名称的映射
    has_unnamed = any(name.startswith("Unnamed") for name in names)
    annotated_names: list[str] = []
    if has_unnamed and rows:
        for name in names:
            if name.startswith("Unnamed"):
                sample = None
                for row in rows[:3]:
                    val = row.get(name)
                    if val is not None and str(val).strip():
                        sample = str(val).strip()
                        break
                if sample:
                    if len(sample) > 20:
                        sample = sample[:20] + "…"
                    annotated_names.append(f"{name}(样本:{sample})")
                else:
                    annotated_names.append(name)
            else:
                annotated_names.append(name)
    else:
        annotated_names = list(names)

    inferred: list[ColumnDef] = []
    for idx, annotated_name in enumerate(annotated_names):
        # 用原始列名从 rows 中取值
        original_key = names[idx]
        values = [row.get(original_key) for row in rows[:50]]
        inferred.append(ColumnDef(name=annotated_name, inferred_type=_infer_type(values, fallback_idx=idx)))
    return inferred


def summarize_shape(
    rows: list[dict[str, Any]],
    columns: list[ColumnDef],
    *,
    explicit_rows: int = 0,
    explicit_cols: int = 0,
) -> tuple[int, int]:
    """Return rows/cols shape with explicit shape as first priority."""

    row_count = int(explicit_rows or len(rows))
    col_count = int(explicit_cols or len(columns))
    return max(0, row_count), max(0, col_count)


def ingest_read_result(
    window: Window,
    *,
    new_range: str,
    new_rows: list[dict[str, Any]],
    iteration: int,
) -> list[int]:
    """将读取结果写入窗口，直接替换视口（不做范围合并）。

    Phase 2 架构原则：每次读取即最新快照，不与旧缓存合并。
    这确保了 data_buffer 始终与磁盘上一次读取结果一致。
    """
    if not new_range:
        new_range = window.viewport_range or "A1:A1"

    # 替换整个缓存为当前视口快照
    window.cached_ranges = [
        CachedRange(
            range_ref=new_range,
            rows=list(new_rows),
            is_current_viewport=True,
            added_at_iteration=iteration,
        )
    ]
    window.viewport_range = new_range
    window.data_buffer = list(new_rows)
    window.preview_rows = list(new_rows[:50])
    window.stale_hint = None
    window.detail_level = DetailLevel.FULL
    window.idle_turns = 0

    if not new_rows:
        return []
    return list(range(len(new_rows)))


def ingest_write_result(
    window: Window,
    *,
    target_range: str,
    result_json: dict[str, Any] | None,
    iteration: int,
) -> list[int]:
    """处理写入结果：清空全部数据缓存，强制下次从磁盘读取。

    Phase 2 架构原则：Window 不在内存中模拟写入结果。
    写入后的真实状态只能通过 read_excel 从磁盘获取。
    """
    # 彻底清空所有数据缓冲
    window.data_buffer = []
    window.preview_rows = []
    window.cached_ranges = []
    window.unfiltered_buffer = None

    hint_range = target_range or window.viewport_range or "当前视口"
    window.stale_hint = (
        f"{hint_range} 已写入磁盘，缓存已清空。"
        "请调用 read_excel 刷新数据以确认写入结果。"
    )
    window.detail_level = DetailLevel.FULL
    window.current_iteration = iteration
    return []


def ingest_filter_result(
    window: Window,
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
    window.preview_rows = list(filtered_rows[:50])
    window.detail_level = DetailLevel.FULL
    window.current_iteration = iteration
    if not filtered_rows:
        return []
    return list(range(len(filtered_rows)))


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


