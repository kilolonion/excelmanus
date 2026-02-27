"""MemoryStorageBackend：持久记忆存储策略接口。

PersistentMemory 通过此 Protocol 访问底层存储，不关心具体实现是文件还是数据库。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from excelmanus.memory_models import MemoryCategory, MemoryEntry


@runtime_checkable
class MemoryStorageBackend(Protocol):
    """持久记忆存储后端协议。"""

    def load_core(self, limit: int = 200) -> str:
        """加载核心记忆，返回格式化 Markdown 文本。"""
        ...

    def load_by_category(self, category: MemoryCategory) -> list[MemoryEntry]:
        """按类别加载记忆条目（时间正序）。"""
        ...

    def load_all(self) -> list[MemoryEntry]:
        """加载所有记忆条目（时间正序）。"""
        ...

    def save_entries(self, entries: list[MemoryEntry]) -> None:
        """保存记忆条目（去重由实现负责）。"""
        ...

    def delete_entry(self, entry_id: str) -> bool:
        """按 MemoryEntry.id 删除记忆条目。"""
        ...
