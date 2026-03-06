"""引用图谱缓存。"""
from __future__ import annotations

from .models import CellNode, WorkbookRefIndex


class RefCache:
    """Tier 1 / Tier 2 引用索引缓存。"""

    def __init__(self) -> None:
        self._tier1: dict[str, WorkbookRefIndex] = {}
        self._tier2: dict[str, CellNode] = {}

    def get_tier1(self, file_path: str) -> WorkbookRefIndex | None:
        return self._tier1.get(file_path)

    def put_tier1(self, file_path: str, index: WorkbookRefIndex) -> None:
        self._tier1[file_path] = index

    def _tier2_key(self, file_path: str, sheet: str, address: str) -> str:
        return f"{file_path}|{sheet}|{address}"

    def get_tier2(self, file_path: str, sheet: str, address: str) -> CellNode | None:
        return self._tier2.get(self._tier2_key(file_path, sheet, address))

    def put_tier2(self, file_path: str, sheet: str, address: str, node: CellNode) -> None:
        self._tier2[self._tier2_key(file_path, sheet, address)] = node

    def invalidate(self, file_path: str) -> None:
        self._tier1.pop(file_path, None)
        prefix = f"{file_path}|"
        keys = [k for k in self._tier2 if k.startswith(prefix)]
        for k in keys:
            del self._tier2[k]

    def invalidate_all(self) -> None:
        self._tier1.clear()
        self._tier2.clear()

    def all_tier1(self) -> dict[str, WorkbookRefIndex]:
        return dict(self._tier1)
