"""引用图谱缓存 — 含 contextvar 多会话隔离。

键必须是 snapshot.id.key()（workspace|relative|content_version）。
put_tier1 驱逐同一文件的旧 version。正式路径不使用模块单例。
"""
from __future__ import annotations

import contextvars

from .models import CellNode, WorkbookRefIndex

_Tier1Entry = tuple[str | None, WorkbookRefIndex]

_current_cache: contextvars.ContextVar["RefCache | None"] = contextvars.ContextVar(
    "_current_ref_cache", default=None,
)


def _identity_prefix(key: str) -> str:
    parts = str(key).split("|")
    if len(parts) >= 3:
        return f"{parts[0]}|{parts[1]}|"
    return f"{key}|"


def get_session_cache() -> "RefCache":
    """获取当前上下文的 RefCache。缺失时在本 context 内新建，不回退模块单例。"""
    cache = _current_cache.get(None)
    if cache is not None:
        return cache
    cache = RefCache()
    _current_cache.set(cache)
    return cache


def set_session_cache(cache: "RefCache") -> contextvars.Token:
    """设置当前上下文的 RefCache，返回恢复 token。"""
    return _current_cache.set(cache)


def reset_session_cache(token: contextvars.Token) -> None:
    """将 contextvar 恢复为先前值。"""
    _current_cache.reset(token)


class RefCache:
    """Tier 1 / Tier 2 引用索引缓存。键为 snapshot.id.key()。"""

    def __init__(self) -> None:
        self._tier1: dict[str, _Tier1Entry] = {}
        self._tier2: dict[str, CellNode] = {}

    def get_tier1(self, cache_key: str) -> WorkbookRefIndex | None:
        entry = self._tier1.get(cache_key)
        if entry is None:
            return None
        return entry[1]

    def put_tier1(self, cache_key: str, index: WorkbookRefIndex) -> None:
        prefix = _identity_prefix(cache_key)
        for existing in list(self._tier1):
            if existing != cache_key and existing.startswith(prefix):
                self.invalidate(existing)
        version = cache_key.rsplit("|", 1)[-1] if "|" in cache_key else None
        self._tier1[cache_key] = (version, index)

    def _tier2_key(self, cache_key: str, sheet: str, address: str) -> str:
        return f"{cache_key}|{sheet}|{address}"

    def get_tier2(self, cache_key: str, sheet: str, address: str) -> CellNode | None:
        return self._tier2.get(self._tier2_key(cache_key, sheet, address))

    def put_tier2(self, cache_key: str, sheet: str, address: str, node: CellNode) -> None:
        self._tier2[self._tier2_key(cache_key, sheet, address)] = node

    def invalidate(self, cache_key: str) -> None:
        self._tier1.pop(cache_key, None)
        prefix = f"{cache_key}|"
        keys = [k for k in self._tier2 if k == cache_key or k.startswith(prefix)]
        for k in keys:
            del self._tier2[k]

    def invalidate_all(self) -> None:
        self._tier1.clear()
        self._tier2.clear()

    def all_tier1(self) -> dict[str, WorkbookRefIndex]:
        return {key: index for key, (_fp, index) in self._tier1.items()}
