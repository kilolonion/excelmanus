"""Window identity value objects."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExplorerIdentity:
    """Identity for explorer windows."""

    directory_norm: str


@dataclass(frozen=True)
class SheetIdentity:
    """Identity for sheet windows."""

    file_path_norm: str
    sheet_name_norm: str


WindowIdentity = ExplorerIdentity | SheetIdentity
