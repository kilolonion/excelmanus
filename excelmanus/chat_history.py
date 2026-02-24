"""聊天记录持久化：支持 SQLite / PostgreSQL 存储后端。"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from excelmanus.db_adapter import ConnectionAdapter, create_sqlite_adapter

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
    """聊天记录存储。兼容 SQLite / PostgreSQL 后端。"""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._owns_conn = True
        self._conn = create_sqlite_adapter(db_path)
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
        if self._owns_conn:
            self._conn.close()

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _serialize_content(msg: dict) -> str:
        return json.dumps(msg, ensure_ascii=False)

    @staticmethod
    def _deserialize_message(row: object) -> dict:
        raw = row["content"]  # type: ignore[index]
        try:
            return json.loads(raw) if raw else {}
        except (json.JSONDecodeError, TypeError):
            return {"role": "unknown", "content": raw}

    # ── Session CRUD ──────────────────────────────────

    def create_session(self, session_id: str, title: str = "") -> None:
        now = self._now_iso()
        self._conn.execute(
            "INSERT OR IGNORE INTO sessions (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (session_id, title, now, now),
        )
        self._conn.commit()

    def session_exists(self, session_id: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        return row is not None

    def update_session(self, session_id: str, **kwargs: str) -> None:
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
        cur = self._conn.execute(
            "DELETE FROM sessions WHERE id = ?", (session_id,)
        )
        self._conn.commit()
        return cur.rowcount > 0

    def delete_all_sessions(self) -> tuple[int, int]:
        cur_msg = self._conn.execute("SELECT COUNT(*) FROM messages")
        msg_row = cur_msg.fetchone()
        msg_count = msg_row[0] if msg_row else 0  # type: ignore[index]
        cur_sess = self._conn.execute("SELECT COUNT(*) FROM sessions")
        sess_row = cur_sess.fetchone()
        sess_count = sess_row[0] if sess_row else 0  # type: ignore[index]
        self._conn.execute("DELETE FROM messages")
        self._conn.execute("DELETE FROM sessions")
        self._conn.commit()
        return sess_count, msg_count

    def clear_messages(self, session_id: str) -> bool:
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
        rows = self._conn.execute(
            "SELECT content FROM messages WHERE session_id = ? "
            "ORDER BY id ASC LIMIT ? OFFSET ?",
            (session_id, limit, offset),
        ).fetchall()
        return [self._deserialize_message(r) for r in rows]

    def get_message_count(self, session_id: str) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) as cnt FROM messages WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return row["cnt"] if row else 0  # type: ignore[index]

    # ── Excel Diff / Affected Files 持久化 ────────────

    def _has_excel_tables(self) -> bool:
        return self._conn.table_exists("session_excel_diffs")

    def save_excel_diff(
        self,
        session_id: str,
        tool_call_id: str,
        file_path: str,
        sheet: str,
        affected_range: str,
        changes: list[dict],
    ) -> None:
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
                changes = json.loads(r["changes_json"])  # type: ignore[index]
            except (json.JSONDecodeError, TypeError):
                changes = []
            result.append({
                "tool_call_id": r["tool_call_id"],  # type: ignore[index]
                "file_path": r["file_path"],  # type: ignore[index]
                "sheet": r["sheet"],  # type: ignore[index]
                "affected_range": r["affected_range"],  # type: ignore[index]
                "changes": changes,
                "timestamp": r["created_at"],  # type: ignore[index]
            })
        return result

    def load_affected_files(self, session_id: str) -> list[str]:
        if not self._has_excel_tables():
            return []
        rows = self._conn.execute(
            "SELECT file_path FROM session_affected_files "
            "WHERE session_id = ? ORDER BY id ASC",
            (session_id,),
        ).fetchall()
        return [r["file_path"] for r in rows]  # type: ignore[index]

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
        if not self._has_excel_tables():
            return
        now = self._now_iso()
        self._conn.execute(
            "INSERT INTO session_excel_previews "
            "(session_id, tool_call_id, file_path, sheet, columns_json, rows_json, "
            " total_rows, truncated, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(tool_call_id) DO UPDATE SET "
            "session_id=EXCLUDED.session_id, file_path=EXCLUDED.file_path, "
            "sheet=EXCLUDED.sheet, columns_json=EXCLUDED.columns_json, "
            "rows_json=EXCLUDED.rows_json, total_rows=EXCLUDED.total_rows, "
            "truncated=EXCLUDED.truncated, created_at=EXCLUDED.created_at",
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
                columns = json.loads(r["columns_json"])  # type: ignore[index]
            except (json.JSONDecodeError, TypeError):
                columns = []
            try:
                row_data = json.loads(r["rows_json"])  # type: ignore[index]
            except (json.JSONDecodeError, TypeError):
                row_data = []
            result.append({
                "tool_call_id": r["tool_call_id"],  # type: ignore[index]
                "file_path": r["file_path"],  # type: ignore[index]
                "sheet": r["sheet"],  # type: ignore[index]
                "columns": columns,
                "rows": row_data,
                "total_rows": r["total_rows"],  # type: ignore[index]
                "truncated": bool(r["truncated"]),  # type: ignore[index]
            })
        return result
