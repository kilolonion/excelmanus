"""Single entrypoint for domain mutation."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from .delta import (
    ExplorerDelta,
    FieldAppendDelta,
    FieldSetDelta,
    IntentDelta,
    LifecycleDelta,
    SheetFilterDelta,
    SheetFocusDelta,
    SheetReadDelta,
    SheetStyleDelta,
    SheetWriteDelta,
    WindowDelta,
)
from .domain import ExplorerWindow, SheetWindow, Window


class DeltaReject(ValueError):
    """Raised when a delta cannot be applied to a window."""


def apply_delta(window: Window, delta: WindowDelta) -> Window:
    """Apply a delta through kind-checked mutation pipeline."""

    if window.kind != delta.kind:
        raise DeltaReject(f"kind mismatch: window={window.kind} delta={delta.kind}")

    _append_audit(window, delta)

    if isinstance(window, ExplorerWindow) and isinstance(delta, ExplorerDelta) and delta.directory is not None:
        window.directory = delta.directory

    if isinstance(window, SheetWindow) and isinstance(delta, SheetReadDelta):
        window.viewport_range = delta.range_ref
        window.total_rows = max(window.total_rows, delta.rows)
        window.total_cols = max(window.total_cols, delta.cols)
        if delta.change_summary:
            window.summary = delta.change_summary

    if isinstance(window, SheetWindow) and isinstance(delta, SheetWriteDelta):
        if delta.change_summary:
            window.summary = delta.change_summary
        if delta.target_range:
            window.viewport_range = delta.target_range

    if isinstance(window, SheetWindow) and isinstance(delta, SheetFilterDelta):
        window.filter_state = delta.filter_state
        if delta.filtered_rows > 0:
            window.summary = f"筛选结果: {delta.filtered_rows}行"

    if isinstance(window, SheetWindow) and isinstance(delta, SheetStyleDelta):
        if delta.style_summary:
            window.style_summary = delta.style_summary
        if delta.freeze_panes is not None:
            window.freeze_panes = delta.freeze_panes
        if delta.column_widths is not None:
            window.column_widths = delta.column_widths
        if delta.row_heights is not None:
            window.row_heights = delta.row_heights
        if delta.merged_ranges is not None:
            window.merged_ranges = delta.merged_ranges
        if delta.conditional_effects is not None:
            window.conditional_effects = delta.conditional_effects

    if isinstance(window, SheetWindow) and isinstance(delta, SheetFocusDelta):
        window.focus.last_action = delta.action
        if delta.detail_level is not None:
            window.detail_level = delta.detail_level
        if delta.is_active is not None:
            window.focus.is_active = bool(delta.is_active)

    if isinstance(delta, LifecycleDelta):
        if delta.detail_level is not None:
            window.detail_level = delta.detail_level
        if delta.idle_turns is not None:
            window.idle_turns = delta.idle_turns
        if delta.last_access_seq is not None:
            window.last_access_seq = delta.last_access_seq
        if delta.dormant is not None:
            window.dormant = delta.dormant

    if isinstance(delta, IntentDelta):
        if delta.tag is not None:
            window.intent_tag = delta.tag
        if delta.confidence is not None:
            window.intent_confidence = delta.confidence
        if delta.source is not None:
            window.intent_source = delta.source
        if delta.updated_turn is not None:
            window.intent_updated_turn = delta.updated_turn
        if delta.lock_until_turn is not None:
            window.intent_lock_until_turn = delta.lock_until_turn

    if isinstance(delta, FieldSetDelta):
        setattr(window, str(delta.field), delta.value)

    if isinstance(delta, FieldAppendDelta):
        target = getattr(window, str(delta.field), None)
        if target is None:
            raise DeltaReject(f"append target missing: {delta.field}")
        append = getattr(target, "append", None)
        if not callable(append):
            raise DeltaReject(f"append target not list-like: {delta.field}")
        append(delta.value)
    return window


def _append_audit(window: Window, delta: WindowDelta) -> None:
    payload: dict[str, Any] = asdict(delta)
    payload["kind"] = delta.kind
    payload["delta_type"] = type(delta).__name__
    window.audit.delta_audit.append(payload)
