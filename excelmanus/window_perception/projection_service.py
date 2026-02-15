"""Projection builder service."""

from __future__ import annotations

from typing import Any

from .models import ChangeRecord, WindowState, WindowType
from .projection_models import ConfirmationProjection, NoticeProjection, ToolPayloadProjection


def project_notice(window: WindowState, ctx: dict[str, Any] | None = None) -> NoticeProjection:
    """Build read-only notice projection from mutable window state."""

    context = ctx if isinstance(ctx, dict) else {}
    identity = str(context.get("identity") or _infer_identity(window))
    rows = int(window.total_rows or (window.viewport.total_rows if window.viewport else 0) or len(window.data_buffer))
    cols = int(window.total_cols or (window.viewport.total_cols if window.viewport else 0) or len(window.columns or window.schema))

    return NoticeProjection(
        window_id=window.id,
        kind="explorer" if window.type == WindowType.EXPLORER else "sheet",
        title=window.title,
        identity=identity,
        range_ref=window.viewport_range or (window.viewport.range_ref if window.viewport else "-"),
        rows=max(0, rows),
        cols=max(0, cols),
        intent=window.intent_tag.value,
        summary=window.summary or "",
        sheet_tabs=tuple(window.sheet_tabs),
        preview_rows=tuple(window.preview_rows),
    )


def project_tool_payload(window: WindowState | None) -> ToolPayloadProjection | None:
    """Build read-only tool payload projection from window state."""

    if window is None:
        return None

    if window.type == WindowType.EXPLORER:
        entries = window.metadata.get("entries")
        safe_entries = tuple(str(item) for item in entries[:12]) if isinstance(entries, list) else ()
        return ToolPayloadProjection(
            window_type="explorer",
            title=window.title,
            directory=window.directory or ".",
            entries=safe_entries,
        )

    viewport = window.viewport
    scroll = window.metadata.get("scroll_position")
    status_bar = window.metadata.get("status_bar")
    column_widths = window.metadata.get("column_widths")
    row_heights = window.metadata.get("row_heights")
    merged_ranges = window.metadata.get("merged_ranges")
    conditional_effects = window.metadata.get("conditional_effects")
    return ToolPayloadProjection(
        window_type="sheet",
        title=window.title,
        file=window.file_path or "",
        sheet=window.sheet_name or "",
        intent=window.intent_tag.value,
        sheet_tabs=tuple(window.sheet_tabs),
        viewport_range=viewport.range_ref if viewport else "",
        visible_rows=viewport.visible_rows if viewport else 0,
        visible_cols=viewport.visible_cols if viewport else 0,
        total_rows=viewport.total_rows if viewport else 0,
        total_cols=viewport.total_cols if viewport else 0,
        freeze_panes=window.freeze_panes or "",
        style_summary=window.style_summary or "",
        scroll_position=scroll if isinstance(scroll, dict) else {},
        status_bar=status_bar if isinstance(status_bar, dict) else {},
        column_widths=column_widths if isinstance(column_widths, dict) else {},
        row_heights=row_heights if isinstance(row_heights, dict) else {},
        merged_ranges=tuple(str(item) for item in merged_ranges) if isinstance(merged_ranges, list) else (),
        conditional_effects=(
            tuple(str(item) for item in conditional_effects)
            if isinstance(conditional_effects, list)
            else ()
        ),
    )


def project_confirmation(
    window: WindowState,
    *,
    tool_name: str,
    repeat_warning: bool = False,
) -> ConfirmationProjection:
    """Build read-only confirmation projection from window state."""

    rows = int(window.total_rows or (window.viewport.total_rows if window.viewport else 0) or len(window.data_buffer))
    cols = int(window.total_cols or (window.viewport.total_cols if window.viewport else 0) or len(window.columns or window.schema))
    file_name = window.file_path or "未知文件"
    sheet_name = window.sheet_name or "未知Sheet"
    window_label = f"{window.id}: {file_name} / {sheet_name}"
    change_summary = _latest_change_summary(window.change_log) or "状态同步"
    intent = window.intent_tag.value
    localized_hint = ""
    hint = ""
    if repeat_warning:
        hint = f"intent[{intent}] data already in window {window.id}"
        localized_hint = f"当前意图[{intent}]下此数据已在窗口 {window.id}"

    return ConfirmationProjection(
        window_label=window_label,
        operation=tool_name,
        range_ref=(window.viewport_range or "-"),
        rows=max(0, rows),
        cols=max(0, cols),
        change_summary=change_summary,
        intent=intent,
        hint=hint,
        localized_hint=localized_hint,
    )


def _infer_identity(window: WindowState) -> str:
    if window.type == WindowType.EXPLORER:
        return window.directory or "."
    file_path = window.file_path or "unknown-file"
    sheet_name = window.sheet_name or "unknown-sheet"
    return f"{file_path}#{sheet_name}"


def _latest_change_summary(change_log: list[ChangeRecord]) -> str:
    if not change_log:
        return ""
    latest = change_log[-1]
    if latest.affected_range and latest.affected_range != "-":
        return f"{latest.change_type}@{latest.affected_range}"
    return latest.change_type or latest.tool_summary
