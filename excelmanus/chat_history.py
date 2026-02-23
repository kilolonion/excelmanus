"""聊天记录持久化：SQLite 存储后端。"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from excelmanus.database import Database

logger = logging.getLogger(__name__)

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sessions (
    id            TEXT PRIMARY KEY,
    title         TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    message_count INTEGER DEFAULT 0,
    status        TEXT DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    role        TEXT NOT NULL,
    content     TEXT,
    turn_number INTEGER DEFAULT 0,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id);
"""


class ChatHistoryStore:
    """SQLite 聊天记录存储。线程安全（sqlite3 默认 serialized 模式）。"""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._owns_conn = True
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_SCHEMA_SQL)
        self._conn.commit()

    @classmethod
    def from_database(cls, database: "Database") -> "ChatHistoryStore":
        """从共享 Database 实例创建（表已由 Database 迁移创建）。"""
        instance = object.__new__(cls)
        instance._db_path = database.db_path
        instance._owns_conn = False
        instance._conn = database.conn
        return instance

    def close(self) -> None:
        """关闭数据库连接（仅关闭自己拥有的连接）。"""
        if self._owns_conn:
            self._conn.close()

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _serialize_content(msg: dict) -> str:
        """将完整消息 dict 序列化为 JSON 字符串。"""
        return json.dumps(msg, ensure_ascii=False)

    @staticmethod
    def _deserialize_message(row: sqlite3.Row) -> dict:
        """将 messages 表行反序列化为消息 dict。"""
        raw = row["content"]
        try:
            return json.loads(raw) if raw else {}
        except (json.JSONDecodeError, TypeError):
            return {"role": "unknown", "content": raw}

    # ── Session CRUD ──────────────────────────────────

    def create_session(self, session_id: str, title: str = "") -> None:
        """创建会话记录（幂等，已存在则忽略）。"""
        now = self._now_iso()
        self._conn.execute(
            "INSERT OR IGNORE INTO sessions (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (session_id, title, now, now),
        )
        self._conn.commit()

    def session_exists(self, session_id: str) -> bool:
        """检查会话是否存在。"""
        row = self._conn.execute(
            "SELECT 1 FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        return row is not None

    def update_session(self, session_id: str, **kwargs: str) -> None:
        """更新会话元数据（title, status）。"""
        sets: list[str] = []
        vals: list[str] = []
        for key in ("title", "status"):
            if key in kwargs:
                sets.append(f"{key} = ?")
                vals.append(kwargs[key])
        if not sets:
            return
        sets.append("updated_at = ?")
        vals.append(self._now_iso())
        vals.append(session_id)
        self._conn.execute(
            f"UPDATE sessions SET {', '.join(sets)} WHERE id = ?", vals
        )
        self._conn.commit()

    def delete_session(self, session_id: str) -> bool:
        """删除会话及其所有消息（CASCADE）。返回是否成功删除。"""
        cur = self._conn.execute(
            "DELETE FROM sessions WHERE id = ?", (session_id,)
        )
        self._conn.commit()
        return cur.rowcount > 0

    def list_sessions(
        self,
        limit: int = 100,
        offset: int = 0,
        include_archived: bool = False,
    ) -> list[dict]:
        """列出会话列表，按 updated_at 降序排列。"""
        if include_archived:
            rows = self._conn.execute(
                "SELECT * FROM sessions ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM sessions WHERE status = 'active' "
                "ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [dict(r) for r in rows]

    # ── Message CRUD ──────────────────────────────────

    def save_turn_messages(
        self, session_id: str, messages: list[dict], turn_number: int = 0
    ) -> None:
        """批量写入一轮消息，并更新会话的 message_count 和 updated_at。"""
        if not messages:
            return
        now = self._now_iso()
        rows = [
            (
                session_id,
                msg.get("role", "unknown"),
                self._serialize_content(msg),
                turn_number,
                now,
            )
            for msg in messages
        ]
        self._conn.executemany(
            "INSERT INTO messages (session_id, role, content, turn_number, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            rows,
        )
        self._conn.execute(
            "UPDATE sessions SET message_count = "
            "(SELECT COUNT(*) FROM messages WHERE session_id = ?), "
            "updated_at = ? WHERE id = ?",
            (session_id, now, session_id),
        )
        self._conn.commit()

    def load_messages(
        self, session_id: str, limit: int = 10000, offset: int = 0
    ) -> list[dict]:
        """分页加载会话消息，按插入顺序排列。"""
        rows = self._conn.execute(
            "SELECT content FROM messages WHERE session_id = ? "
            "ORDER BY id ASC LIMIT ? OFFSET ?",
            (session_id, limit, offset),
        ).fetchall()
        return [self._deserialize_message(r) for r in rows]

    def get_message_count(self, session_id: str) -> int:
        """获取会话的消息总数。"""
        row = self._conn.execute(
            "SELECT COUNT(*) as cnt FROM messages WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return row["cnt"] if row else 0
