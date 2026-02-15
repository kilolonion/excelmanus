"""Test-only migration helpers for the new Window union model."""

from __future__ import annotations

from typing import Any

from excelmanus.window_perception.domain import ExplorerWindow, SheetWindow, Window
from excelmanus.window_perception.models import (
    CachedRange,
    ChangeRecord,
    ColumnDef,
    DetailLevel,
    IntentTag,
    OpEntry,
    Viewport,
    WindowType,
)


def make_window(
    *,
    id: str,
    type: WindowType,
    title: str,
    file_path: str | None = None,
    sheet_name: str | None = None,
    directory: str | None = None,
    sheet_tabs: list[str] | None = None,
    viewport: Viewport | None = None,
    freeze_panes: str | None = None,
    style_summary: str = "",
    preview_rows: list[Any] | None = None,
    summary: str = "",
    metadata: dict[str, Any] | None = None,
    schema: list[ColumnDef] | None = None,
    columns: list[ColumnDef] | None = None,
    total_rows: int | None = None,
    total_cols: int | None = None,
    viewport_range: str | None = None,
    cached_ranges: list[CachedRange] | None = None,
    data_buffer: list[dict[str, Any]] | None = None,
    max_cached_rows: int | None = 200,
    stale_hint: str | None = None,
    filter_state: dict[str, Any] | None = None,
    unfiltered_buffer: list[dict[str, Any]] | None = None,
    operation_history: list[OpEntry] | None = None,
    max_history_entries: int = 20,
    change_log: list[ChangeRecord] | None = None,
    max_change_records: int = 5,
    current_iteration: int = 0,
    detail_level: DetailLevel = DetailLevel.FULL,
    idle_turns: int = 0,
    last_access_seq: int = 0,
    dormant: bool = False,
    intent_tag: IntentTag = IntentTag.GENERAL,
    intent_confidence: float = 0.0,
    intent_source: str = "default",
    intent_updated_turn: int = 0,
    intent_lock_until_turn: int = 0,
) -> Window:
    if type == WindowType.EXPLORER:
        window = ExplorerWindow.new(
            id=id,
            title=title,
            directory=directory or ".",
        )
    else:
        window = SheetWindow.new(
            id=id,
            title=title,
            file_path=file_path or "",
            sheet_name=sheet_name or "",
        )
        window.sheet_tabs = list(sheet_tabs or [])
        window.viewport = viewport
        window.freeze_panes = freeze_panes
        window.style_summary = style_summary
        window.preview_rows = list(preview_rows or [])
        window.schema = list(schema or [])
        window.columns = list(columns or [])
        if total_rows is not None:
            window.total_rows = int(total_rows)
        if total_cols is not None:
            window.total_cols = int(total_cols)
        if viewport_range:
            window.viewport_range = viewport_range
        window.cached_ranges = list(cached_ranges or [])
        window.data_buffer = list(data_buffer or [])
        if max_cached_rows is not None:
            window.max_cached_rows = int(max_cached_rows)
        window.stale_hint = stale_hint
        window.filter_state = filter_state
        window.unfiltered_buffer = list(unfiltered_buffer) if isinstance(unfiltered_buffer, list) else None

    window.summary = summary
    window.operation_history = list(operation_history or [])
    window.max_history_entries = int(max_history_entries)
    window.change_log = list(change_log or [])
    window.max_change_records = int(max_change_records)
    window.current_iteration = int(current_iteration)
    window.detail_level = detail_level
    window.idle_turns = int(idle_turns)
    window.last_access_seq = int(last_access_seq)
    window.dormant = bool(dormant)
    window.intent_tag = intent_tag
    window.intent_confidence = float(intent_confidence)
    window.intent_source = str(intent_source)
    window.intent_updated_turn = int(intent_updated_turn)
    window.intent_lock_until_turn = int(intent_lock_until_turn)

    raw_metadata = dict(metadata or {})
    if isinstance(window, ExplorerWindow):
        entries = raw_metadata.pop("entries", None)
        if isinstance(entries, list):
            window.entries = [str(item) for item in entries]
    else:
        scroll = raw_metadata.pop("scroll_position", None)
        if isinstance(scroll, dict):
            window.scroll_position = scroll
        status = raw_metadata.pop("status_bar", None)
        if isinstance(status, dict):
            window.status_bar = status
        col_widths = raw_metadata.pop("column_widths", None)
        if isinstance(col_widths, dict):
            window.column_widths = col_widths
        row_heights = raw_metadata.pop("row_heights", None)
        if isinstance(row_heights, dict):
            window.row_heights = row_heights
        merged = raw_metadata.pop("merged_ranges", None)
        if isinstance(merged, list):
            window.merged_ranges = [str(item) for item in merged]
        cond = raw_metadata.pop("conditional_effects", None)
        if isinstance(cond, list):
            window.conditional_effects = [str(item) for item in cond]

    window.metadata.update(raw_metadata)
    return window
