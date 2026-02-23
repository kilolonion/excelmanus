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

    def delete_all_sessions(self) -> tuple[int, int]:
        """删除所有会话及消息。返回 (删除的会话数, 删除的消息数)。"""
        cur_msg = self._conn.execute("SELECT COUNT(*) FROM messages")
        msg_count = cur_msg.fetchone()[0]
        cur_sess = self._conn.execute("SELECT COUNT(*) FROM sessions")
        sess_count = cur_sess.fetchone()[0]
        self._conn.execute("DELETE FROM messages")
        self._conn.execute("DELETE FROM sessions")
        self._conn.commit()
        return sess_count, msg_count

    def clear_messages(self, session_id: str) -> bool:
        """清除会话的所有消息，但保留会话记录。返回是否存在该会话。"""
        if not self.session_exists(session_id):
            return False
        now = self._now_iso()
        self._conn.execute(
            "DELETE FROM messages WHERE session_id = ?", (session_id,)
        )
        self._conn.execute(
            "UPDATE sessions SET message_count = 0, updated_at = ? WHERE id = ?",
            (now, session_id),
        )
        self._conn.commit()
        return True



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

    # ── Excel Diff / Affected Files 持久化 ────────────

    def _has_excel_tables(self) -> bool:
        """检查 session_excel_diffs 表是否存在（兼容旧 DB）。"""
        row = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='session_excel_diffs'"
        ).fetchone()
        return row is not None

    def save_excel_diff(
        self,
        session_id: str,
        tool_call_id: str,
        file_path: str,
        sheet: str,
        affected_range: str,
        changes: list[dict],
    ) -> None:
        """持久化一条 Excel diff 记录。"""
        if not self._has_excel_tables():
            return
        now = self._now_iso()
        self._conn.execute(
            "INSERT INTO session_excel_diffs "
            "(session_id, tool_call_id, file_path, sheet, affected_range, changes_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                session_id,
                tool_call_id,
                file_path,
                sheet,
                affected_range,
                json.dumps(changes, ensure_ascii=False),
                now,
            ),
        )
        self._conn.commit()

    def save_affected_file(
        self, session_id: str, file_path: str,
    ) -> None:
        """持久化一条改动文件记录（去重）。"""
        if not self._has_excel_tables():
            return
        now = self._now_iso()
        self._conn.execute(
            "INSERT OR IGNORE INTO session_affected_files "
            "(session_id, file_path, created_at) VALUES (?, ?, ?)",
            (session_id, file_path, now),
        )
        self._conn.commit()

    def load_excel_diffs(self, session_id: str) -> list[dict]:
        """加载会话的所有 Excel diff 记录。"""
        if not self._has_excel_tables():
            return []
        rows = self._conn.execute(
            "SELECT tool_call_id, file_path, sheet, affected_range, changes_json, created_at "
            "FROM session_excel_diffs WHERE session_id = ? ORDER BY id ASC",
            (session_id,),
        ).fetchall()
        result = []
        for r in rows:
            try:
                changes = json.loads(r["changes_json"])
            except (json.JSONDecodeError, TypeError):
                changes = []
            result.append({
                "tool_call_id": r["tool_call_id"],
                "file_path": r["file_path"],
                "sheet": r["sheet"],
                "affected_range": r["affected_range"],
                "changes": changes,
                "timestamp": r["created_at"],
            })
        return result

    def load_affected_files(self, session_id: str) -> list[str]:
        """加载会话的所有改动文件路径。"""
        if not self._has_excel_tables():
            return []
        rows = self._conn.execute(
            "SELECT file_path FROM session_affected_files "
            "WHERE session_id = ? ORDER BY id ASC",
            (session_id,),
        ).fetchall()
        return [r["file_path"] for r in rows]

    # ── Excel Preview 持久化 ─────────────────────────

    def save_excel_preview(
        self,
        session_id: str,
        tool_call_id: str,
        file_path: str,
        sheet: str,
        columns: list[str],
        rows: list[list],
        total_rows: int,
        truncated: bool,
    ) -> None:
        """持久化一条 Excel 预览记录（按 tool_call_id 去重）。"""
        if not self._has_excel_tables():
            return
        now = self._now_iso()
        self._conn.execute(
            "INSERT OR REPLACE INTO session_excel_previews "
            "(session_id, tool_call_id, file_path, sheet, columns_json, rows_json, "
            " total_rows, truncated, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                session_id,
                tool_call_id,
                file_path,
                sheet,
                json.dumps(columns, ensure_ascii=False),
                json.dumps(rows, ensure_ascii=False),
                total_rows,
                1 if truncated else 0,
                now,
            ),
        )
        self._conn.commit()

    def load_excel_previews(self, session_id: str) -> list[dict]:
        """加载会话的所有 Excel 预览记录。"""
        if not self._has_excel_tables():
            return []
        rows = self._conn.execute(
            "SELECT tool_call_id, file_path, sheet, columns_json, rows_json, "
            "       total_rows, truncated "
            "FROM session_excel_previews WHERE session_id = ? ORDER BY id ASC",
            (session_id,),
        ).fetchall()
        result = []
        for r in rows:
            try:
                columns = json.loads(r["columns_json"])
            except (json.JSONDecodeError, TypeError):
                columns = []
            try:
                row_data = json.loads(r["rows_json"])
            except (json.JSONDecodeError, TypeError):
                row_data = []
            result.append({
                "tool_call_id": r["tool_call_id"],
                "file_path": r["file_path"],
                "sheet": r["sheet"],
                "columns": columns,
                "rows": row_data,
                "total_rows": r["total_rows"],
                "truncated": bool(r["truncated"]),
            })
        return result
