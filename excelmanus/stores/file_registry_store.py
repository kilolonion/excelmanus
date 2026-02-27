"""FileRegistryStore：文件注册表持久化层（支持 SQLite / PostgreSQL）。

管理 file_registry / file_registry_aliases / file_registry_events 三张表的 CRUD。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from excelmanus.database import Database
    from excelmanus.db_adapter import ConnectionAdapter

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class FileRegistryStore:
    """文件注册表 SQLite / PostgreSQL 持久化。"""

    def __init__(self, database: "Database") -> None:
        self._conn: ConnectionAdapter = database.conn

    # ── file_registry CRUD ───────────────────────────────────

    def upsert_file(self, record: dict[str, Any]) -> None:
        """插入或更新文件记录。"""
        now = _now_iso()
        self._conn.execute(
            "INSERT INTO file_registry ("
            "  id, workspace, canonical_path, original_name, file_type,"
            "  size_bytes, origin, origin_session_id, origin_turn, origin_tool,"
            "  parent_file_id, sheet_meta_json, content_hash, mtime_ns,"
            "  staging_path, is_active_cow, created_at, updated_at, deleted_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(workspace, canonical_path) DO UPDATE SET"
            "  original_name=excluded.original_name,"
            "  file_type=excluded.file_type,"
            "  size_bytes=excluded.size_bytes,"
            "  sheet_meta_json=excluded.sheet_meta_json,"
            "  content_hash=excluded.content_hash,"
            "  mtime_ns=excluded.mtime_ns,"
            "  staging_path=excluded.staging_path,"
            "  is_active_cow=excluded.is_active_cow,"
            "  updated_at=excluded.updated_at,"
            "  deleted_at=NULL",
            (
                record["id"],
                record["workspace"],
                record["canonical_path"],
                record["original_name"],
                record.get("file_type", "other"),
                record.get("size_bytes", 0),
                record["origin"],
                record.get("origin_session_id"),
                record.get("origin_turn"),
                record.get("origin_tool"),
                record.get("parent_file_id"),
                json.dumps(record.get("sheet_meta", []), ensure_ascii=False),
                record.get("content_hash", ""),
                record.get("mtime_ns", 0),
                record.get("staging_path"),
                1 if record.get("is_active_cow") else 0,
                record.get("created_at", now),
                now,
                None,
            ),
        )
        self._conn.commit()

    def upsert_batch(self, records: list[dict[str, Any]]) -> int:
        """批量 upsert 文件记录。返回写入数量。"""
        if not records:
            return 0
        now = _now_iso()
        rows = [
            (
                r["id"],
                r["workspace"],
                r["canonical_path"],
                r["original_name"],
                r.get("file_type", "other"),
                r.get("size_bytes", 0),
                r["origin"],
                r.get("origin_session_id"),
                r.get("origin_turn"),
                r.get("origin_tool"),
                r.get("parent_file_id"),
                json.dumps(r.get("sheet_meta", []), ensure_ascii=False),
                r.get("content_hash", ""),
                r.get("mtime_ns", 0),
                r.get("staging_path"),
                1 if r.get("is_active_cow") else 0,
                r.get("created_at", now),
                now,
                None,
            )
            for r in records
        ]
        self._conn.executemany(
            "INSERT INTO file_registry ("
            "  id, workspace, canonical_path, original_name, file_type,"
            "  size_bytes, origin, origin_session_id, origin_turn, origin_tool,"
            "  parent_file_id, sheet_meta_json, content_hash, mtime_ns,"
            "  staging_path, is_active_cow, created_at, updated_at, deleted_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(workspace, canonical_path) DO UPDATE SET"
            "  original_name=excluded.original_name,"
            "  file_type=excluded.file_type,"
            "  size_bytes=excluded.size_bytes,"
            "  sheet_meta_json=excluded.sheet_meta_json,"
            "  content_hash=excluded.content_hash,"
            "  mtime_ns=excluded.mtime_ns,"
            "  staging_path=excluded.staging_path,"
            "  is_active_cow=excluded.is_active_cow,"
            "  updated_at=excluded.updated_at,"
            "  deleted_at=NULL",
            rows,
        )
        self._conn.commit()
        return len(rows)

    def get_by_id(self, file_id: str) -> dict[str, Any] | None:
        """按 ID 查询。"""
        row = self._conn.execute(
            "SELECT * FROM file_registry WHERE id = ?", (file_id,)
        ).fetchone()
        return self._row_to_dict(row) if row else None

    def get_by_path(self, workspace: str, canonical_path: str) -> dict[str, Any] | None:
        """按工作区 + 规范路径查询。"""
        row = self._conn.execute(
            "SELECT * FROM file_registry"
            " WHERE workspace = ? AND canonical_path = ?",
            (workspace, canonical_path),
        ).fetchone()
        return self._row_to_dict(row) if row else None

    def list_all(
        self,
        workspace: str,
        *,
        include_deleted: bool = False,
    ) -> list[dict[str, Any]]:
        """列出工作区所有文件记录。"""
        if include_deleted:
            rows = self._conn.execute(
                "SELECT * FROM file_registry WHERE workspace = ?"
                " ORDER BY canonical_path",
                (workspace,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM file_registry"
                " WHERE workspace = ? AND deleted_at IS NULL"
                " ORDER BY canonical_path",
                (workspace,),
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def get_children(self, parent_file_id: str) -> list[dict[str, Any]]:
        """获取指定文件的所有子文件（备份/副本）。"""
        rows = self._conn.execute(
            "SELECT * FROM file_registry"
            " WHERE parent_file_id = ? AND deleted_at IS NULL"
            " ORDER BY created_at",
            (parent_file_id,),
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def soft_delete(self, workspace: str, canonical_path: str) -> bool:
        """软删除文件记录。"""
        now = _now_iso()
        cur = self._conn.execute(
            "UPDATE file_registry SET deleted_at = ?, updated_at = ?"
            " WHERE workspace = ? AND canonical_path = ? AND deleted_at IS NULL",
            (now, now, workspace, canonical_path),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def update_staging(
        self,
        workspace: str,
        canonical_path: str,
        staging_path: str | None,
    ) -> bool:
        """更新文件的 staging 路径。"""
        now = _now_iso()
        cur = self._conn.execute(
            "UPDATE file_registry SET staging_path = ?, updated_at = ?"
            " WHERE workspace = ? AND canonical_path = ?",
            (staging_path, now, workspace, canonical_path),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def update_cow_status(
        self,
        workspace: str,
        canonical_path: str,
        is_active_cow: bool,
    ) -> bool:
        """更新文件的 CoW 活跃状态。"""
        now = _now_iso()
        cur = self._conn.execute(
            "UPDATE file_registry SET is_active_cow = ?, updated_at = ?"
            " WHERE workspace = ? AND canonical_path = ?",
            (1 if is_active_cow else 0, now, workspace, canonical_path),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def list_active_cow(self, workspace: str) -> list[dict[str, Any]]:
        """列出所有活跃的 CoW 副本。"""
        rows = self._conn.execute(
            "SELECT * FROM file_registry"
            " WHERE workspace = ? AND is_active_cow = 1 AND deleted_at IS NULL",
            (workspace,),
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def list_staged(self, workspace: str) -> list[dict[str, Any]]:
        """列出所有有 staging 路径的记录。"""
        rows = self._conn.execute(
            "SELECT * FROM file_registry"
            " WHERE workspace = ? AND staging_path IS NOT NULL AND deleted_at IS NULL",
            (workspace,),
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    # ── file_registry_aliases CRUD ───────────────────────────

    def add_alias(
        self,
        alias_id: str,
        file_id: str,
        alias_type: str,
        alias_value: str,
    ) -> None:
        """添加路径/名称别名。"""
        self._conn.execute(
            "INSERT INTO file_registry_aliases (id, file_id, alias_type, alias_value)"
            " VALUES (?, ?, ?, ?)"
            " ON CONFLICT(file_id, alias_type, alias_value) DO NOTHING",
            (alias_id, file_id, alias_type, alias_value),
        )
        self._conn.commit()

    def get_aliases(self, file_id: str) -> list[dict[str, str]]:
        """获取文件的所有别名。"""
        rows = self._conn.execute(
            "SELECT id, alias_type, alias_value FROM file_registry_aliases"
            " WHERE file_id = ?",
            (file_id,),
        ).fetchall()
        return [
            {"id": r["id"], "alias_type": r["alias_type"], "alias_value": r["alias_value"]}
            for r in rows
        ]

    def get_all_aliases_for_files(self, file_ids: list[str]) -> dict[str, list[dict[str, str]]]:
        """批量获取多个文件的别名（避免 N+1 查询）。返回 file_id → aliases 映射。"""
        if not file_ids:
            return {}
        result: dict[str, list[dict[str, str]]] = {fid: [] for fid in file_ids}
        # SQLite 不支持 ANY(array)，用 IN + 占位符
        placeholders = ",".join("?" for _ in file_ids)
        rows = self._conn.execute(
            f"SELECT file_id, alias_type, alias_value FROM file_registry_aliases"
            f" WHERE file_id IN ({placeholders})",
            tuple(file_ids),
        ).fetchall()
        for r in rows:
            fid = r["file_id"]
            if fid in result:
                result[fid].append({
                    "alias_type": r["alias_type"],
                    "alias_value": r["alias_value"],
                })
        return result

    def find_by_alias(self, alias_value: str) -> dict[str, Any] | None:
        """通过别名值查找文件记录。"""
        row = self._conn.execute(
            "SELECT fr.* FROM file_registry fr"
            " JOIN file_registry_aliases fra ON fr.id = fra.file_id"
            " WHERE fra.alias_value = ? AND fr.deleted_at IS NULL"
            " LIMIT 1",
            (alias_value,),
        ).fetchone()
        return self._row_to_dict(row) if row else None

    def remove_aliases_for_file(self, file_id: str) -> int:
        """删除文件的所有别名。"""
        cur = self._conn.execute(
            "DELETE FROM file_registry_aliases WHERE file_id = ?",
            (file_id,),
        )
        self._conn.commit()
        return cur.rowcount

    # ── file_registry_events CRUD ────────────────────────────

    def add_event(self, event: dict[str, Any]) -> None:
        """插入生命周期事件。"""
        now = _now_iso()
        self._conn.execute(
            "INSERT INTO file_registry_events"
            " (id, file_id, event_type, session_id, turn, tool_name, details_json, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event["id"],
                event["file_id"],
                event["event_type"],
                event.get("session_id"),
                event.get("turn"),
                event.get("tool_name"),
                json.dumps(event.get("details", {}), ensure_ascii=False),
                event.get("created_at", now),
            ),
        )
        self._conn.commit()

    def get_events(
        self,
        file_id: str,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """获取文件的事件历史（时间正序）。"""
        rows = self._conn.execute(
            "SELECT * FROM file_registry_events"
            " WHERE file_id = ? ORDER BY created_at ASC LIMIT ?",
            (file_id, limit),
        ).fetchall()
        return [self._event_row_to_dict(r) for r in rows]

    def get_events_by_session(
        self,
        session_id: str,
        *,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """按会话 ID 获取事件。"""
        rows = self._conn.execute(
            "SELECT * FROM file_registry_events"
            " WHERE session_id = ? ORDER BY created_at ASC LIMIT ?",
            (session_id, limit),
        ).fetchall()
        return [self._event_row_to_dict(r) for r in rows]

    def get_events_by_turn(
        self,
        session_id: str,
        turn: int,
    ) -> list[dict[str, Any]]:
        """按会话 + 轮次获取事件。"""
        rows = self._conn.execute(
            "SELECT * FROM file_registry_events"
            " WHERE session_id = ? AND turn = ? ORDER BY created_at ASC",
            (session_id, turn),
        ).fetchall()
        return [self._event_row_to_dict(r) for r in rows]

    # ── 内部工具 ─────────────────────────────────────────────

    @staticmethod
    def _row_to_dict(row: object) -> dict[str, Any]:
        """file_registry 行 → dict。"""
        d: dict[str, Any] = {}
        for key in (
            "id", "workspace", "canonical_path", "original_name", "file_type",
            "size_bytes", "origin", "origin_session_id", "origin_turn",
            "origin_tool", "parent_file_id", "content_hash", "mtime_ns",
            "staging_path", "created_at", "updated_at", "deleted_at",
        ):
            d[key] = row[key]  # type: ignore[index]
        d["is_active_cow"] = bool(row["is_active_cow"])  # type: ignore[index]
        raw_meta = row["sheet_meta_json"]  # type: ignore[index]
        try:
            d["sheet_meta"] = json.loads(raw_meta) if raw_meta else []
        except (json.JSONDecodeError, TypeError):
            d["sheet_meta"] = []
        return d

    @staticmethod
    def _event_row_to_dict(row: object) -> dict[str, Any]:
        """file_registry_events 行 → dict。"""
        d: dict[str, Any] = {}
        for key in (
            "id", "file_id", "event_type", "session_id", "turn",
            "tool_name", "created_at",
        ):
            d[key] = row[key]  # type: ignore[index]
        raw = row["details_json"]  # type: ignore[index]
        try:
            d["details"] = json.loads(raw) if raw else {}
        except (json.JSONDecodeError, TypeError):
            d["details"] = {}
        return d
