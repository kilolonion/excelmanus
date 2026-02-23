"""ManifestStore：基于 SQLite 的 Workspace Manifest 缓存。"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from excelmanus.database import Database

logger = logging.getLogger(__name__)


class ManifestStore:
    """SQLite 后端的 workspace manifest 文件元数据缓存。"""

    def __init__(self, database: "Database") -> None:
        self._conn = database.conn

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    def load_cached(self, workspace: str) -> dict[str, dict[str, Any]]:
        """加载指定 workspace 的全部缓存记录。

        Returns:
            {rel_path: {name, size_bytes, mtime_ns, sheets_json, scanned_at}}
        """
        rows = self._conn.execute(
            "SELECT path, name, size_bytes, mtime_ns, sheets_json, scanned_at "
            "FROM workspace_files WHERE workspace = ?",
            (workspace,),
        ).fetchall()
        result: dict[str, dict[str, Any]] = {}
        for row in rows:
            result[row["path"]] = {
                "name": row["name"],
                "size_bytes": row["size_bytes"],
                "mtime_ns": row["mtime_ns"],
                "sheets_json": row["sheets_json"],
                "scanned_at": row["scanned_at"],
            }
        return result

    def upsert_file(
        self,
        workspace: str,
        path: str,
        name: str,
        size_bytes: int,
        mtime_ns: int,
        sheets_json: str,
    ) -> None:
        """插入或更新单条文件记录。"""
        self._conn.execute(
            "INSERT INTO workspace_files "
            "(workspace, path, name, size_bytes, mtime_ns, sheets_json, scanned_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(workspace, path) DO UPDATE SET "
            "name=excluded.name, size_bytes=excluded.size_bytes, "
            "mtime_ns=excluded.mtime_ns, sheets_json=excluded.sheets_json, "
            "scanned_at=excluded.scanned_at",
            (workspace, path, name, size_bytes, mtime_ns, sheets_json, self._now_iso()),
        )

    def upsert_batch(
        self,
        workspace: str,
        files: list[dict[str, Any]],
    ) -> int:
        """批量 upsert 文件记录。返回写入数量。"""
        if not files:
            return 0
        now = self._now_iso()
        rows = [
            (
                workspace,
                f["path"],
                f["name"],
                f["size_bytes"],
                f["mtime_ns"],
                f["sheets_json"],
                now,
            )
            for f in files
        ]
        self._conn.executemany(
            "INSERT INTO workspace_files "
            "(workspace, path, name, size_bytes, mtime_ns, sheets_json, scanned_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(workspace, path) DO UPDATE SET "
            "name=excluded.name, size_bytes=excluded.size_bytes, "
            "mtime_ns=excluded.mtime_ns, sheets_json=excluded.sheets_json, "
            "scanned_at=excluded.scanned_at",
            rows,
        )
        self._conn.commit()
        return len(rows)

    def remove_stale(self, workspace: str, current_paths: set[str]) -> int:
        """删除 DB 中存在但磁盘已不存在的记录。返回删除数量。"""
        if not current_paths:
            cur = self._conn.execute(
                "DELETE FROM workspace_files WHERE workspace = ?",
                (workspace,),
            )
            self._conn.commit()
            return cur.rowcount

        rows = self._conn.execute(
            "SELECT path FROM workspace_files WHERE workspace = ?",
            (workspace,),
        ).fetchall()
        stale = [row["path"] for row in rows if row["path"] not in current_paths]
        if not stale:
            return 0
        placeholders = ",".join("?" for _ in stale)
        cur = self._conn.execute(
            f"DELETE FROM workspace_files WHERE workspace = ? AND path IN ({placeholders})",
            [workspace, *stale],
        )
        self._conn.commit()
        return cur.rowcount

    def clear(self, workspace: str | None = None) -> None:
        """清空缓存。workspace=None 时清空全部。"""
        if workspace is None:
            self._conn.execute("DELETE FROM workspace_files")
        else:
            self._conn.execute(
                "DELETE FROM workspace_files WHERE workspace = ?",
                (workspace,),
            )
        self._conn.commit()
