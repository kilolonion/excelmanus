"""聊天记录持久化：SQLite 存储。

Schema 由 Database 迁移系统统一管理，ChatHistoryStore 仅负责查询与写入。
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, overload

from excelmanus.db_adapter import ConnectionAdapter

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

    @classmethod
    def from_database(cls, database: "Database") -> "ChatHistoryStore":
        """向后兼容入口——等同于直接构造。"""
        return cls(database)

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _next_updated_at(self) -> str:
        """严格递增的 updated_at：Windows 下 datetime.now 粒度约 15.6ms，
        同刻写会导致 ORDER BY 平局时 created_at/rowid 反超真实时序。"""
        now = self._now_iso()
        row = self._conn.execute("SELECT MAX(updated_at) FROM sessions").fetchone()
        prev = row[0] if row else None
        if isinstance(prev, str) and prev >= now:
            now = (
                datetime.fromisoformat(prev) + timedelta(microseconds=1)
            ).isoformat()
        return now

    @staticmethod
    def _durable_payload(msg: dict) -> dict:
        """Persist refs only: drop request-only keys and migrate leftover data URIs."""
        payload = {k: v for k, v in msg.items() if not str(k).startswith("_")}
        # Compaction metadata describes durable history, not a request projection.
        # Preserve it in messages-table restores when session_events is disabled.
        if msg.get("_prompt_kind") == "compaction":
            for key in ("_prompt_kind", "_ui_hidden", "_compaction_handoff", "_source_message_ids"):
                if key in msg:
                    payload[key] = msg[key]
        content = payload.get("content")
        if isinstance(content, list):
            from excelmanus.attachments.migrate import migrate_content
            from excelmanus.attachments.project import strip_projection_meta

            migrated = migrate_content(content)
            wrapped = strip_projection_meta([{"content": migrated}])
            payload["content"] = wrapped[0].get("content", migrated)
        return payload

    @staticmethod
    def _serialize_content(msg: dict) -> str:
        return json.dumps(ChatHistoryStore._durable_payload(msg), ensure_ascii=False)

    @staticmethod
    def _deserialize_message(row: object) -> dict:
        raw = row["content"]  # type: ignore[index]
        try:
            return json.loads(raw) if raw else {}
        except (json.JSONDecodeError, TypeError):
            return {"role": "unknown", "content": raw}

    # ── Session CRUD ──────────────────────────────────

    def create_session(
        self,
        session_id: str,
        title: str = "",
        *,
        user_id: str | None = None,
        workspace_path: str = "",
        workspace_id: str | None = None,
        blank: bool = True,
    ) -> None:
        now = self._now_iso()
        updated_now = self._next_updated_at()
        path = workspace_path or ""
        self._conn.execute(
            "INSERT OR IGNORE INTO sessions "
            "(id, title, created_at, updated_at, user_id, title_source, "
            " workspace_path, workspace_id, blank) "
            "VALUES (?, ?, ?, ?, ?, 'fallback', ?, ?, ?)",
            (session_id, title, now, updated_now, None, path, workspace_id, 1 if blank else 0),
        )
        if path:
            self._conn.execute(
                "UPDATE sessions SET workspace_path = ?, "
                "workspace_id = COALESCE(workspace_id, ?) "
                "WHERE id = ? AND (workspace_path IS NULL OR workspace_path = '')",
                (path, workspace_id, session_id),
            )
        self._conn.commit()

    def session_exists(self, session_id: str, *, user_id: str | None = None) -> bool:
        """检查会话是否存在。不再按 user_id 过滤。"""
        row = self._conn.execute(
            "SELECT 1 FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        return row is not None

    def session_owned_by(self, session_id: str, user_id: str) -> bool:
        """兼容旧调用：单用户架构下等价于 session_exists。"""
        return self.session_exists(session_id)

    def get_session_meta(self, session_id: str) -> dict | None:
        """返回会话元数据，不存在时返回 None。"""
        row = self._conn.execute(
            "SELECT id, title, created_at, updated_at, message_count, "
            "workspace_path, workspace_id, blank "
            "FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        if row is None:
            return None
        return dict(row)

    def find_blank_session(self, workspace_path: str) -> dict | None:
        """Latest unused session for this folder, if any."""
        row = self._conn.execute(
            "SELECT id, title, created_at, updated_at, message_count, "
            "workspace_path, workspace_id, blank "
            "FROM sessions WHERE workspace_path = ? AND blank = 1 "
            "AND COALESCE(message_count, 0) = 0 "
            "ORDER BY updated_at DESC, created_at DESC, rowid DESC LIMIT 1",
            (workspace_path,),
        ).fetchone()
        return dict(row) if row is not None else None

    def set_session_blank(self, session_id: str, blank: bool) -> None:
        now = self._next_updated_at()
        self._conn.execute(
            "UPDATE sessions SET blank = ?, updated_at = ? WHERE id = ?",
            (1 if blank else 0, now, session_id),
        )
        self._conn.commit()

    def backfill_workspace_paths(
        self, workspace_path: str, workspace_id: str | None = None
    ) -> int:
        """Fill empty workspace_path on existing rows with the process default."""
        if not workspace_path:
            return 0
        cur = self._conn.execute(
            "UPDATE sessions SET workspace_path = ?, "
            "workspace_id = COALESCE(workspace_id, ?) "
            "WHERE workspace_path IS NULL OR workspace_path = ''",
            (workspace_path, workspace_id),
        )
        self._conn.execute(
            "UPDATE sessions SET blank = 0 "
            "WHERE COALESCE(message_count, 0) > 0 AND blank != 0"
        )
        self._conn.commit()
        return int(cur.rowcount or 0)

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
        for key in ("title", "title_source"):
            if key in kwargs:
                sets.append(f"{key} = ?")
                vals.append(kwargs[key])
        if not sets:
            return
        sets.append("updated_at = ?")
        vals.append(self._next_updated_at())
        vals.append(session_id)
        self._conn.execute(
            f"UPDATE sessions SET {', '.join(sets)} WHERE id = ?", vals
        )
        self._conn.commit()

    def delete_session(self, session_id: str) -> bool:
        cur = self._conn.execute(
            "DELETE FROM sessions WHERE id = ?", (session_id,)
        )
        if self._conn.table_exists("session_events"):
            self._conn.execute(
                "DELETE FROM session_events WHERE session_id = ?", (session_id,)
            )
        self._conn.commit()
        return cur.rowcount > 0

    def delete_all_sessions(self, *, user_id: str | None = None) -> tuple[int, int]:
        cur_msg = self._conn.execute("SELECT COUNT(*) FROM messages")
        msg_row = cur_msg.fetchone()
        msg_count = msg_row[0] if msg_row else 0  # type: ignore[index]
        cur_sess = self._conn.execute("SELECT COUNT(*) FROM sessions")
        sess_row = cur_sess.fetchone()
        sess_count = sess_row[0] if sess_row else 0  # type: ignore[index]
        self._conn.execute("DELETE FROM messages")
        self._conn.execute("DELETE FROM sessions")
        if self._conn.table_exists("session_events"):
            self._conn.execute("DELETE FROM session_events")
        self._conn.commit()
        return sess_count, msg_count

    def clear_messages(self, session_id: str, *, clear_events: bool = False) -> bool:
        """清空 surface 快照（messages 表）。

        ``clear_events=False``（默认）：仅重写快照，事件日志保留——压缩/回退
        等快照重写路径走这里，原文仍在 ``session_events`` 中可审计。
        ``clear_events=True``：用户显式清除会话，事件日志一并删除。
        """
        if not self.session_exists(session_id):
            return False
        now = self._next_updated_at()
        self._conn.execute(
            "DELETE FROM messages WHERE session_id = ?", (session_id,)
        )
        if clear_events and self._conn.table_exists("session_events"):
            self._conn.execute(
                "DELETE FROM session_events WHERE session_id = ?", (session_id,)
            )
        self._conn.execute(
            "UPDATE sessions SET message_count = 0, blank = 1, updated_at = ? WHERE id = ?",
            (now, session_id),
        )
        self._conn.commit()
        return True

    # ── Session Events（append-only 事实源）────────────

    def _has_events_table(self) -> bool:
        return self._conn.table_exists("session_events")

    def save_events(self, session_id: str, events: list[dict]) -> int:
        """批量追加事件行。(seq) 唯一索引保证不重写；违反即报错。"""
        if not events or not self._has_events_table():
            return 0
        import time as _time

        now = _time.time()
        rows = []
        for ev in events:
            rows.append(
                (
                    session_id,
                    int(ev["seq"]),
                    str(ev["kind"]),
                    int(ev.get("turn") or 0),
                    int(ev.get("step") or 0),
                    ev.get("payload"),
                    ev.get("surface_op"),
                    ev.get("shadow_start"),
                    ev.get("shadow_end"),
                    ev.get("source_seqs"),
                    ev.get("created_at") or now,
                )
            )
        self._conn.executemany(
            "INSERT OR IGNORE INTO session_events "
            "(session_id, seq, kind, turn, step, payload, surface_op, "
            " shadow_start, shadow_end, source_seqs, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        self._conn.commit()
        return len(rows)

    def iter_events(self, session_id: str) -> list[dict]:
        """按 seq 顺序返回会话全部事件行。"""
        if not self._has_events_table():
            return []
        rows = self._conn.execute(
            "SELECT seq, kind, turn, step, payload, surface_op, "
            "shadow_start, shadow_end, source_seqs, created_at "
            "FROM session_events WHERE session_id = ? ORDER BY seq ASC",
            (session_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def has_events(self, session_id: str) -> bool:
        if not self._has_events_table():
            return False
        row = self._conn.execute(
            "SELECT 1 FROM session_events WHERE session_id = ? LIMIT 1",
            (session_id,),
        ).fetchone()
        return row is not None

    def max_event_seq(self, session_id: str) -> int:
        """已落盘的最大事件 seq（持久化对账水位）。"""
        if not self._has_events_table():
            return 0
        row = self._conn.execute(
            "SELECT MAX(seq) FROM session_events WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return int(row[0]) if row and row[0] is not None else 0

    def list_sessions(
        self,
        limit: int = 100,
        offset: int = 0,
        *,
        user_id: str | None = None,
    ) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM sessions ORDER BY updated_at DESC, created_at DESC, rowid DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]

    # ── Message CRUD ──────────────────────────────────

    def save_turn_messages(
        self, session_id: str, messages: list[dict], turn_number: int = 0
    ) -> None:
        if not messages:
            return
        now = self._next_updated_at()
        rows: list[tuple[str, str, str, int, str, str]] = []
        batch_ids: set[str] = set()
        for msg in messages:
            if not isinstance(msg, dict):
                payload = {"role": "unknown", "content": msg}
                mid = uuid.uuid4().hex
            else:
                payload = dict(msg)
                mid = str(payload.get("message_id") or "").strip() or uuid.uuid4().hex
                payload["message_id"] = mid
            if mid in batch_ids:
                continue
            batch_ids.add(mid)
            rows.append(
                (
                    session_id,
                    str(payload.get("role", "unknown")),
                    self._serialize_content(payload),
                    turn_number,
                    now,
                    mid,
                )
            )
        if not rows:
            return
        self._conn.executemany(
            "INSERT OR IGNORE INTO messages "
            "(session_id, role, content, turn_number, created_at, message_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )
        self._conn.execute(
            "UPDATE sessions SET message_count = "
            "(SELECT COUNT(*) FROM messages WHERE session_id = ?), "
            "blank = CASE WHEN (SELECT COUNT(*) FROM messages WHERE session_id = ?) = 0 "
            "THEN 1 ELSE 0 END, "
            "updated_at = ? WHERE id = ?",
            (session_id, session_id, now, session_id),
        )
        self._conn.commit()

    def load_messages(
        self, session_id: str, limit: int = 10000, offset: int = 0
    ) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id, content FROM messages WHERE session_id = ? "
            "ORDER BY id ASC LIMIT ? OFFSET ?",
            (session_id, limit, offset),
        ).fetchall()
        messages: list[dict] = []
        for row in rows:
            message = self._deserialize_message(row)
            if not message.get("message_id"):
                message["message_id"] = f"db:{row['id']}"  # type: ignore[index]
            messages.append(message)
        return messages

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
