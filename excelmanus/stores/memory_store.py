"""MemoryStore：持久记忆存储（支持 SQLite / PostgreSQL）。"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, overload

from excelmanus.db_adapter import ConnectionAdapter, user_filter_clause
from excelmanus.memory_models import MemoryCategory, MemoryEntry

if TYPE_CHECKING:
    from excelmanus.database import Database

logger = logging.getLogger(__name__)

_TIMESTAMP_FMT = "%Y-%m-%d %H:%M"


class MemoryStore:
    """持久记忆 CRUD（支持 SQLite / PostgreSQL）。"""

    @overload
    def __init__(self, conn: ConnectionAdapter, *, user_id: str | None = None) -> None: ...
    @overload
    def __init__(self, conn: "Database", *, user_id: str | None = None) -> None: ...

    def __init__(self, conn: Any, *, user_id: str | None = None) -> None:
        # 兼容旧签名：接收 Database 实例时自动取 .conn
        if isinstance(conn, ConnectionAdapter):
            self._conn = conn
        else:
            self._conn = conn.conn  # Database 实例
        self._user_id = user_id
        self._uid_clause, self._uid_params = user_filter_clause("user_id", user_id)

    @staticmethod
    def _hash_content(text: str, user_id: str | None = None) -> str:
        """SHA-256[:16] 内容哈希，用于去重。"""
        normalized = " ".join((text or "").split())
        scoped = f"{user_id}::{normalized}" if user_id else normalized
        return hashlib.sha256(scoped.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    def save_entries(self, entries: list[MemoryEntry]) -> int:
        """批量保存记忆条目，通过 UNIQUE 约束自动去重。返回实际新增数量。"""
        if not entries:
            return 0
        added = 0
        for entry in entries:
            content_hash = self._hash_content(entry.content, self._user_id)
            created_at = entry.timestamp.isoformat() if entry.timestamp else self._now_iso()
            try:
                cur = self._conn.execute(
                    "INSERT OR IGNORE INTO memory_entries "
                    "(category, content, content_hash, source, created_at, user_id) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        entry.category.value,
                        entry.content,
                        content_hash,
                        entry.source or "",
                        created_at,
                        self._user_id,
                    ),
                )
                if cur.rowcount > 0:
                    added += 1
            except Exception:
                logger.warning("保存记忆条目失败", exc_info=True)
        self._conn.commit()
        return added

    def load_core(self, limit: int = 200) -> str:
        """加载最近 N 条记忆，返回格式化 Markdown 文本。"""
        rows = self._conn.execute(
            "SELECT category, content, created_at FROM memory_entries "
            f"WHERE {self._uid_clause} "
            "ORDER BY created_at DESC, id DESC LIMIT ?",
            (*self._uid_params, limit),
        ).fetchall()
        if not rows:
            return ""
        # 按时间正序输出（从旧到新）
        rows = list(reversed(rows))
        parts: list[str] = []
        for row in rows:
            ts = row["created_at"]
            # 尝试截断到分钟精度
            try:
                dt = datetime.fromisoformat(ts)
                ts = dt.strftime(_TIMESTAMP_FMT)
            except (ValueError, TypeError):
                pass
            category = row["category"]
            content = row["content"]
            parts.append(f"### [{ts}] {category}\n\n{content}\n\n---")
        return "\n\n".join(parts)

    def load_by_category(self, category: MemoryCategory) -> list[MemoryEntry]:
        """按类别加载所有记忆条目。"""
        rows = self._conn.execute(
            "SELECT category, content, source, created_at FROM memory_entries "
            f"WHERE category = ? AND {self._uid_clause} ORDER BY created_at ASC",
            (category.value, *self._uid_params),
        ).fetchall()
        return [self._row_to_entry(row) for row in rows]

    def load_all(self) -> list[MemoryEntry]:
        """加载所有记忆条目，按时间正序。"""
        rows = self._conn.execute(
            "SELECT category, content, source, created_at FROM memory_entries "
            f"WHERE {self._uid_clause} ORDER BY created_at ASC",
            self._uid_params,
        ).fetchall()
        return [self._row_to_entry(row) for row in rows]

    def count(self) -> int:
        """返回记忆条目总数。"""
        row = self._conn.execute(
            f"SELECT COUNT(*) as cnt FROM memory_entries WHERE {self._uid_clause}",
            self._uid_params,
        ).fetchone()
        return row["cnt"] if row else 0

    def count_by_category(self, category: MemoryCategory) -> int:
        """按类别返回记忆条目数。"""
        row = self._conn.execute(
            "SELECT COUNT(*) as cnt FROM memory_entries "
            f"WHERE category = ? AND {self._uid_clause}",
            (category.value, *self._uid_params),
        ).fetchone()
        return row["cnt"] if row else 0

    def enforce_capacity(self, max_entries: int = 500) -> int:
        """超容量时删除最旧的条目，保留最近 max_entries 条。返回删除数量。"""
        current = self.count()
        if current <= max_entries:
            return 0
        to_delete = current - max_entries
        self._conn.execute(
            "DELETE FROM memory_entries WHERE id IN ("
            f"  SELECT id FROM memory_entries WHERE {self._uid_clause} "
            "  ORDER BY created_at ASC, id ASC LIMIT ?"
            ")",
            (*self._uid_params, to_delete),
        )
        self._conn.commit()
        return to_delete

    def delete_entry(self, entry_id: str) -> bool:
        """按 MemoryEntry.id 删除条目。

        entry_id 是基于 category+content+timestamp 的 SHA256[:12] 短哈希，
        需遍历行逐一匹配（条目量有限，性能可接受）。
        """
        from excelmanus.memory_models import _compute_entry_id

        rows = self._conn.execute(
            "SELECT id, category, content, created_at FROM memory_entries "
            f"WHERE {self._uid_clause}",
            self._uid_params,
        ).fetchall()
        for row in rows:
            ts_str = row["created_at"]
            try:
                timestamp = datetime.fromisoformat(ts_str)
            except (ValueError, TypeError):
                timestamp = datetime.now()
            computed_id = _compute_entry_id(row["category"], row["content"], timestamp)
            if computed_id == entry_id:
                self._conn.execute(
                    "DELETE FROM memory_entries WHERE id = ?", (row["id"],)
                )
                self._conn.commit()
                return True
        return False

    @staticmethod
    def _row_to_entry(row: object) -> MemoryEntry:
        """将 sqlite3.Row 转为 MemoryEntry。"""
        ts_str = row["created_at"]  # type: ignore[index]
        try:
            timestamp = datetime.fromisoformat(ts_str)
        except (ValueError, TypeError):
            timestamp = datetime.now()
        return MemoryEntry(
            content=row["content"],  # type: ignore[index]
            category=MemoryCategory(row["category"]),  # type: ignore[index]
            timestamp=timestamp,
            source=row["source"] or "",  # type: ignore[index]
        )
