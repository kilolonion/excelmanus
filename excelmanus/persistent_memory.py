"""持久记忆管理器：通过 MemoryStorageBackend 委托存储操作。

PersistentMemory 不再内部持有 MemoryStore 的条件分支——
存储后端通过构造函数注入，由上层（SessionManager）决定使用文件还是数据库。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from excelmanus.memory_format import format_entries, parse_entries
from excelmanus.memory_models import MemoryCategory, MemoryEntry
from excelmanus.stores.file_memory_backend import (
    CORE_MEMORY_FILE,
    FileMemoryBackend,
    _infer_category_by_filename,
)

if TYPE_CHECKING:
    from excelmanus.stores.memory_backend import MemoryStorageBackend

logger = logging.getLogger(__name__)


class PersistentMemory:
    """持久记忆管理器。

    所有存储操作委托给注入的 MemoryStorageBackend，
    自身只保留领域逻辑（格式化、解析等工具方法）。
    """

    def __init__(
        self,
        backend: "MemoryStorageBackend | str" = "",
        auto_load_lines: int = 200,
        *,
        memory_dir: str = "",
        **_kwargs: Any,
    ) -> None:
        # 向后兼容：接受旧的 memory_dir 关键字参数或字符串位置参数
        if isinstance(backend, str):
            path = backend or memory_dir
            if not path:
                raise ValueError("backend (MemoryStorageBackend) or memory_dir (str) required")
            backend = FileMemoryBackend(
                memory_dir=path, auto_load_lines=auto_load_lines,
            )
        self._backend = backend
        self._auto_load_lines = auto_load_lines

    @property
    def memory_dir(self) -> Path:
        """返回记忆存储目录路径（仅 FileMemoryBackend 有意义）。"""
        if isinstance(self._backend, FileMemoryBackend):
            return self._backend.memory_dir
        return Path(".")

    @property
    def auto_load_lines(self) -> int:
        return self._auto_load_lines

    @property
    def read_only_mode(self) -> bool:
        if isinstance(self._backend, FileMemoryBackend):
            return self._backend.read_only_mode
        return False

    # ── 核心操作（委托 backend）────────────────────────────

    def load_core(self) -> str:
        """加载核心记忆文本。"""
        return self._backend.load_core(limit=self._auto_load_lines)

    def load_topic(self, topic_name: str) -> str:
        """按主题文件名加载记忆（通过类别推断）。"""
        cat = _infer_category_by_filename(topic_name)
        if cat is not None:
            entries = self._backend.load_by_category(cat)
            if entries:
                return format_entries(entries)
        return ""

    def save_entries(self, entries: list[MemoryEntry]) -> None:
        """保存记忆条目。"""
        if entries:
            self._backend.save_entries(entries)

    def list_entries(
        self,
        category: MemoryCategory | None = None,
    ) -> list[MemoryEntry]:
        """列出所有记忆条目（可按类别筛选）。"""
        if category is not None:
            return self._backend.load_by_category(category)
        return self._backend.load_all()

    def delete_entry(self, entry_id: str) -> bool:
        """按 ID 删除指定记忆条目。"""
        return self._backend.delete_entry(entry_id)

    # ── 格式化 / 解析工具方法（保留向后兼容）──────────────────

    @staticmethod
    def format_entries(entries: list[MemoryEntry]) -> str:
        """将 MemoryEntry 列表序列化为 Markdown 文本。"""
        return format_entries(entries)

    @staticmethod
    def parse_entries(content: str) -> list[MemoryEntry]:
        """解析 Markdown 为结构化条目。"""
        return parse_entries(content)
