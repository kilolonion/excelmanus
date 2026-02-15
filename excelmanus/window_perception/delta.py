"""Delta contracts for window mutation."""

from __future__ import annotations

from dataclasses import dataclass


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


WindowDelta = ExplorerDelta | SheetReadDelta
