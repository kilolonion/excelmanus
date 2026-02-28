"""聊天记录持久化：支持 SQLite / PostgreSQL 存储后端。

Schema 由 Database 迁移系统统一管理，ChatHistoryStore 仅负责查询与写入。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, overload

from excelmanus.db_adapter import ConnectionAdapter, user_filter_clause

if TYPE_CHECKING:
    from excelmanus.database import Database

logger = logging.getLogger(__name__)


class ChatHistoryStore:
    """聊天记录存储（纯查询 / 写入层）。

    必须通过 Database 实例或 ConnectionAdapter 创建——所有表结构由 Database 迁移管理。
    """

    @overload
    def __init__(self, conn: ConnectionAdapter, *, user_id: str | None = None) -> None: ...
    @overload
    def __init__(self, conn: "Database", *, user_id: str | None = None) -> None: ...

    def __init__(self, conn: Any, *, user_id: str | None = None) -> None:
        if isinstance(conn, ConnectionAdapter):
            self._conn = conn
            self._db_path = ""
        else:
            # Database 实例
            self._db_path = conn.db_path
            self._conn = conn.conn
        self._user_id = user_id
        self._uid_clause, self._uid_params = user_filter_clause("user_id", user_id)

    @classmethod
    def from_database(cls, database: "Database") -> "ChatHistoryStore":
        """向后兼容入口——等同于直接构造。"""
        return cls(database)

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

    def create_session(
        self, session_id: str, title: str = "", *, user_id: str | None = None
    ) -> None:
        now = self._now_iso()
        self._conn.execute(
            "INSERT OR IGNORE INTO sessions "
            "(id, title, created_at, updated_at, user_id, title_source) "
            "VALUES (?, ?, ?, ?, ?, 'fallback')",
            (session_id, title, now, now, user_id),
        )
        self._conn.commit()

    def session_exists(self, session_id: str, *, user_id: str | None = None) -> bool:
        """检查会话是否存在。若提供 user_id，则同时校验归属（仅 user_id 一致时通过）。

        user_id 为 None 时（如 CLI 单用户模式）：仅检查 id 存在。
        user_id 非空时：要求 DB 中 user_id 非空且一致，否则视为不存在（legacy 无主会话不可访问）。

        注意：此方法接受显式 user_id 参数以支持跨用户校验场景（如 ConversationPersistence）。
        若不传 user_id，则使用构造时绑定的 self._user_id。
        """
        effective_uid = user_id if user_id is not None else self._user_id
        if effective_uid is None:
            row = self._conn.execute(
                "SELECT 1 FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            return row is not None
        row = self._conn.execute(
            "SELECT user_id FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if row is None:
            return False
        db_user_id = row["user_id"] if hasattr(row, "__getitem__") else row[0]
        return db_user_id is not None and db_user_id == effective_uid

    def session_owned_by(self, session_id: str, user_id: str) -> bool:
        """检查会话是否属于指定用户。"""
        return self.session_exists(session_id, user_id=user_id)

    def get_title_source(self, session_id: str) -> str | None:
        """返回会话的 title_source 字段，会话不存在时返回 None。"""
        row = self._conn.execute(
            "SELECT title_source FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if row is None:
            return None
        return row["title_source"] if hasattr(row, "__getitem__") else row[0]

    def update_session(self, session_id: str, **kwargs: str) -> None:
        sets: list[str] = []
        vals: list[str] = []
        for key in ("title", "status", "title_source"):
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

    def delete_all_sessions(self, *, user_id: str | None = None) -> tuple[int, int]:
        effective_uid = user_id if user_id is not None else self._user_id
        if effective_uid is not None:
            sess_ids = [
                r[0] for r in self._conn.execute(
                    "SELECT id FROM sessions WHERE user_id = ?", (effective_uid,)
                ).fetchall()
            ]
            if not sess_ids:
                return 0, 0
            placeholders = ",".join("?" * len(sess_ids))
            cur_msg = self._conn.execute(
                f"SELECT COUNT(*) FROM messages WHERE session_id IN ({placeholders})",
                sess_ids,
            )
            msg_count = (cur_msg.fetchone() or (0,))[0]  # type: ignore[index]
            self._conn.execute(
                f"DELETE FROM messages WHERE session_id IN ({placeholders})",
                sess_ids,
            )
            self._conn.execute(
                f"DELETE FROM sessions WHERE id IN ({placeholders})",
                sess_ids,
            )
            self._conn.commit()
            return len(sess_ids), msg_count

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
        *,
        user_id: str | None = None,
    ) -> list[dict]:
        effective_uid = user_id if user_id is not None else self._user_id
        conditions: list[str] = []
        params: list[Any] = []

        if effective_uid is not None:
            uid_clause, uid_params = user_filter_clause("user_id", effective_uid)
            conditions.append(uid_clause)
            params.extend(uid_params)

        if not include_archived:
            conditions.append("status = 'active'")

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = self._conn.execute(
            f"SELECT * FROM sessions {where} "
            "ORDER BY updated_at DESC LIMIT ? OFFSET ?",
            (*params, limit, offset),
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
        cell_styles: list[list] | None = None,
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
