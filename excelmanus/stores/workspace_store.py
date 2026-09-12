"""Registered user folders (workspaces). Session logs stay in the process data home."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from excelmanus.workspace.paths import canonicalize_workspace_path, workspace_title_from_path

if TYPE_CHECKING:
    from excelmanus.database import Database
    from excelmanus.db_adapter import ConnectionAdapter


class WorkspacePathError(ValueError):
    """Path is missing, not a directory, or not registered."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class WorkspaceStore:
    """CRUD for the ``workspaces`` registration table."""

    def __init__(self, conn: Any) -> None:
        if hasattr(conn, "conn") and not hasattr(conn, "execute"):
            self._conn = conn.conn
        else:
            self._conn = conn

    @classmethod
    def from_database(cls, database: "Database") -> "WorkspaceStore":
        return cls(database)

    def _row(self, row: object) -> dict[str, Any]:
        data = dict(row)  # type: ignore[arg-type]
        return {
            "id": data.get("id"),
            "path": data.get("path"),
            "title": data.get("title") or workspace_title_from_path(data.get("path") or ""),
            "created_at": data.get("created_at") or "",
            "updated_at": data.get("updated_at") or "",
            "sort_index": int(data.get("sort_index") or 0),
        }

    def list(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT id, path, title, created_at, updated_at, sort_index "
            "FROM workspaces ORDER BY sort_index ASC, created_at ASC"
        ).fetchall()
        return [self._row(r) for r in rows]

    def get(self, workspace_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT id, path, title, created_at, updated_at, sort_index "
            "FROM workspaces WHERE id = ?",
            (workspace_id,),
        ).fetchone()
        return self._row(row) if row is not None else None

    def get_by_path(self, path: str) -> dict[str, Any] | None:
        canon = canonicalize_workspace_path(path)
        row = self._conn.execute(
            "SELECT id, path, title, created_at, updated_at, sort_index "
            "FROM workspaces WHERE path = ?",
            (canon,),
        ).fetchone()
        return self._row(row) if row is not None else None

    def create(self, path: str, *, title: str = "") -> tuple[dict[str, Any], bool]:
        """Adopt an existing directory. Same path returns the old row (created=False)."""
        canon = canonicalize_workspace_path(path)
        target = Path(canon)
        if not target.is_dir():
            raise WorkspacePathError(f"工作区目录不存在: {canon}")
        existing = self.get_by_path(canon)
        if existing is not None:
            return existing, False
        now = _now_iso()
        display = (title or "").strip() or workspace_title_from_path(canon)
        row = self._conn.execute("SELECT COALESCE(MAX(sort_index), -1) AS m FROM workspaces").fetchone()
        sort_index = int(row["m"] if row is not None else -1) + 1
        workspace_id = str(uuid.uuid4())
        self._conn.execute(
            "INSERT INTO workspaces (id, path, title, created_at, updated_at, sort_index) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (workspace_id, canon, display, now, now, sort_index),
        )
        self._conn.commit()
        rec = self.get(workspace_id)
        assert rec is not None
        return rec, True

    def ensure_path(self, path: str, *, title: str = "") -> dict[str, Any]:
        """Register path if needed. Caller must mkdir the default root first."""
        rec, _created = self.create(path, title=title)
        return rec

    def rename(self, workspace_id: str, title: str) -> dict[str, Any] | None:
        return self.update(workspace_id, title=title)

    def update(
        self,
        workspace_id: str,
        *,
        title: str | None = None,
        path: str | None = None,
    ) -> dict[str, Any] | None:
        rec = self.get(workspace_id)
        if rec is None:
            return None
        next_title = rec["title"]
        next_path = rec["path"]
        if title is not None:
            trimmed = title.strip()
            if not trimmed:
                raise WorkspacePathError("工作区名称不能为空")
            next_title = trimmed
        if path is not None:
            canon = canonicalize_workspace_path(path)
            if not Path(canon).is_dir():
                raise WorkspacePathError(f"工作区目录不存在: {canon}")
            existing = self.get_by_path(canon)
            if existing is not None and existing["id"] != workspace_id:
                raise WorkspacePathError("该文件夹已被其他工作区使用")
            next_path = canon
        now = _now_iso()
        self._conn.execute(
            "UPDATE workspaces SET title = ?, path = ?, updated_at = ? WHERE id = ?",
            (next_title, next_path, now, workspace_id),
        )
        self._conn.commit()
        return self.get(workspace_id)

    def delete(self, workspace_id: str) -> bool:
        """Drop the registration only. Disk and sessions stay."""
        cur = self._conn.execute("DELETE FROM workspaces WHERE id = ?", (workspace_id,))
        self._conn.commit()
        return cur.rowcount > 0
