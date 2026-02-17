"""Projection builder service."""

from __future__ import annotations

from typing import Any

from .domain import ExplorerWindow, SheetWindow, Window
from .models import ChangeRecord, WindowType
from .projection_models import ConfirmationProjection, NoticeProjection, ToolPayloadProjection


def project_notice(window: Window, ctx: dict[str, Any] | None = None) -> NoticeProjection:
    """Build read-only notice projection from mutable window state."""

    context = ctx if isinstance(ctx, dict) else {}
    identity = str(context.get("identity") or _infer_identity(window))
    if isinstance(window, ExplorerWindow):
        rows = len(window.entries)
        cols = 1 if rows > 0 else 0
        range_ref = "-"
        sheet_tabs: tuple[str, ...] = ()
        preview_rows: tuple[dict[str, Any], ...] = ()
    else:
        rows = int(window.total_rows or (window.viewport.total_rows if window.viewport else 0) or len(window.data_buffer))
        cols = int(window.total_cols or (window.viewport.total_cols if window.viewport else 0) or len(window.columns or window.schema))
        range_ref = window.viewport_range or (window.viewport.range_ref if window.viewport else "-")
        sheet_tabs = tuple(window.sheet_tabs)
        preview_rows = tuple(window.preview_rows)

    return NoticeProjection(
        window_id=window.id,
        kind="explorer" if window.type == WindowType.EXPLORER else "sheet",
        title=window.title,
        identity=identity,
        range_ref=range_ref,
        rows=max(0, rows),
        cols=max(0, cols),
        intent=window.intent_tag.value,
        summary=window.summary or "",
        sheet_tabs=sheet_tabs,
        preview_rows=preview_rows,
    )


def project_tool_payload(window: Window | None) -> ToolPayloadProjection | None:
    """Build read-only tool payload projection from window state."""

    if window is None:
        return None

    if isinstance(window, ExplorerWindow):
        safe_entries = tuple(str(item) for item in window.entries[:12])
        return ToolPayloadProjection(
            window_type="explorer",
            title=window.title,
            identity=_infer_identity(window),
            directory=window.directory or ".",
            entries=safe_entries,
        )

    viewport = window.viewport
    scroll = window.scroll_position
    status_bar = window.status_bar
    column_widths = window.column_widths
    row_heights = window.row_heights
    merged_ranges = window.merged_ranges
    conditional_effects = window.conditional_effects
    return ToolPayloadProjection(
        window_type="sheet",
        title=window.title,
        identity=_infer_identity(window),
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
        sheet_dimensions=tuple(
            (name, rows, cols)
            for name, (rows, cols) in (window.sheet_dimensions or {}).items()
        ),
    )


def project_confirmation(
    window: Window,
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
        hint = (
            f"intent[{intent}] repeat read detected in {window.id}; "
            "re-read only when scope changes"
        )
        localized_hint = (
            f"当前意图[{intent}]检测到重复读取（{window.id}）；"
            "仅在范围或工作表变化时再读取"
        )

    sheet_dims = tuple(
        (name, r, c)
        for name, (r, c) in (window.sheet_dimensions or {}).items()
    ) if hasattr(window, 'sheet_dimensions') else ()

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
        sheet_dimensions=sheet_dims,
    )


def _infer_identity(window: Window) -> str:
    if isinstance(window, ExplorerWindow):
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
