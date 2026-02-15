"""Window domain models with typed payloads."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class BaseWindow:
    """Common base for all window kinds."""

    id: str
    title: str
    kind: str


@dataclass
class ExplorerData:
    """Explorer-specific data payload."""

    directory: str


@dataclass
class SheetData:
    """Sheet-specific data payload."""

    file_path: str
    sheet_name: str


@dataclass
class ExplorerWindow(BaseWindow):
    """Explorer window with typed explorer payload."""

    data: ExplorerData

    @classmethod
    def new(cls, *, id: str, title: str, directory: str) -> "ExplorerWindow":
        return cls(id=id, title=title, kind="explorer", data=ExplorerData(directory=directory))


@dataclass
class SheetWindow(BaseWindow):
    """Sheet window with typed sheet payload."""

    data: SheetData

    @classmethod
    def new(cls, *, id: str, title: str, file_path: str, sheet_name: str) -> "SheetWindow":
        return cls(
            id=id,
            title=title,
            kind="sheet",
            data=SheetData(file_path=file_path, sheet_name=sheet_name),
        )
