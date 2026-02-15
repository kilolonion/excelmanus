"""Delta contracts for window mutation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import DetailLevel, IntentTag


@dataclass(frozen=True)
class ExplorerDelta:
    """Mutation contract for explorer windows."""

    directory: str | None = None
    kind: str = "explorer"


@dataclass(frozen=True)
class SheetReadDelta:
    """Mutation contract for sheet read ingestion."""

    range_ref: str
    rows: int
    cols: int
    change_summary: str
    kind: str = "sheet"


@dataclass(frozen=True)
class SheetWriteDelta:
    """Mutation contract for sheet write updates."""

    target_range: str
    change_summary: str = ""
    kind: str = "sheet"


@dataclass(frozen=True)
class SheetFilterDelta:
    """Mutation contract for sheet filter updates."""

    filter_state: dict[str, Any] | None
    filtered_rows: int = 0
    kind: str = "sheet"


@dataclass(frozen=True)
class SheetStyleDelta:
    """Mutation contract for sheet style updates."""

    style_summary: str = ""
    freeze_panes: str | None = None
    column_widths: dict[str, Any] | None = None
    row_heights: dict[str, Any] | None = None
    merged_ranges: list[str] | None = None
    conditional_effects: list[str] | None = None
    kind: str = "sheet"


@dataclass(frozen=True)
class SheetFocusDelta:
    """Mutation contract for sheet focus lifecycle transitions."""

    action: str
    detail_level: DetailLevel | None = None
    is_active: bool | None = None
    kind: str = "sheet"


@dataclass(frozen=True)
class LifecycleDelta:
    """Mutation contract for shared lifecycle state."""

    detail_level: DetailLevel | None = None
    idle_turns: int | None = None
    last_access_seq: int | None = None
    dormant: bool | None = None
    kind: str = "sheet"


@dataclass(frozen=True)
class IntentDelta:
    """Mutation contract for shared intent state."""

    tag: IntentTag | None = None
    confidence: float | None = None
    source: str | None = None
    updated_turn: int | None = None
    lock_until_turn: int | None = None
    kind: str = "sheet"


@dataclass(frozen=True)
class FieldSetDelta:
    """Generic field-set delta for manager-side controlled mutations."""

    field: str
    value: Any
    kind: str


@dataclass(frozen=True)
class FieldAppendDelta:
    """Generic append delta for manager-side controlled mutations."""

    field: str
    value: Any
    kind: str


WindowDelta = (
    ExplorerDelta
    | SheetReadDelta
    | SheetWriteDelta
    | SheetFilterDelta
    | SheetStyleDelta
    | SheetFocusDelta
    | LifecycleDelta
    | IntentDelta
    | FieldSetDelta
    | FieldAppendDelta
)
