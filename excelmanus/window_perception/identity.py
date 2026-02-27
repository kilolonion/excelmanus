"""窗口身份值对象。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExplorerIdentity:
    """资源管理器窗口的身份。"""

    directory_norm: str


@dataclass(frozen=True)
class SheetIdentity:
    """表格窗口的身份。"""

    file_path_norm: str
    sheet_name_norm: str


WindowIdentity = ExplorerIdentity | SheetIdentity
