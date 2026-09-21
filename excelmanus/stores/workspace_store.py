"""Registered user folders (workspaces). Session logs stay in the process data home."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from excelmanus.workspace.paths import (
    canonicalize_workspace_path,
    unique_workspace_title,
    workspace_title_from_path,
)
from excelmanus.security.source_isolation import set_workspace_source_access

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
        set_workspace_source_access(str(data.get("path") or ""), bool(data.get("source_access")))
        return {
            "id": data.get("id"),
            "path": data.get("path"),
            "title": data.get("title") or workspace_title_from_path(data.get("path") or ""),
            "created_at": data.get("created_at") or "",
            "updated_at": data.get("updated_at") or "",
            "sort_index": int(data.get("sort_index") or 0),
            "source_access": bool(data.get("source_access")),
        }

    def _rows(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT id, path, title, created_at, updated_at, sort_index, source_access "
            "FROM workspaces ORDER BY sort_index ASC, created_at ASC"
        ).fetchall()
        return [self._row(r) for r in rows]

    def _taken_titles(self, *, exclude_id: str | None = None) -> set[str]:
        taken: set[str] = set()
        for item in self._rows():
            if exclude_id and item["id"] == exclude_id:
                continue
            title = str(item.get("title") or "").strip()
            if title:
                taken.add(title)
        return taken

    def _persist_unique_titles(self, items: list[dict[str, Any]]) -> None:
        taken: set[str] = set()
        now = _now_iso()
        dirty = False
        for item in items:
            current = str(item.get("title") or "").strip() or "工作区"
            unique = unique_workspace_title(current, taken)
            taken.add(unique)
            if unique == current:
                continue
            item["title"] = unique
            item["updated_at"] = now
            self._conn.execute(
                "UPDATE workspaces SET title = ?, updated_at = ? WHERE id = ?",
                (unique, now, item["id"]),
            )
            dirty = True
        if dirty:
            self._conn.commit()

    def _ensure_unique_titles(self) -> None:
        self._persist_unique_titles(self._rows())

    def _allocate_title(self, desired: str, *, exclude_id: str | None = None) -> str:
        self._ensure_unique_titles()
        return unique_workspace_title(desired, self._taken_titles(exclude_id=exclude_id))

    def list(self) -> list[dict[str, Any]]:
        items = self._rows()
        self._persist_unique_titles(items)
        return items

    def reorder(self, workspace_ids: list[str]) -> list[dict[str, Any]]:
        """Persist the user-visible workspace order.

        Callers may omit registrations that are intentionally hidden from the
        workspace picker (for example, an old package-root registration).  The
        requested ids are therefore moved to the front in the supplied order,
        while omitted registrations retain their relative order afterwards.
        """
        current = self._rows()
        known_ids = {str(item["id"]) for item in current if item.get("id")}
        requested = [str(item).strip() for item in workspace_ids if str(item).strip()]
        if len(requested) != len(set(requested)):
            raise WorkspacePathError("工作区顺序包含重复项目")
        unknown = [workspace_id for workspace_id in requested if workspace_id not in known_ids]
        if unknown:
            raise WorkspacePathError("工作区顺序包含未知项目")

        requested_set = set(requested)
        ordered = [
            *(next(item for item in current if str(item["id"]) == workspace_id) for workspace_id in requested),
            *(item for item in current if str(item["id"]) not in requested_set),
        ]
        now = _now_iso()
        self._conn.executemany(
            "UPDATE workspaces SET sort_index = ?, updated_at = ? WHERE id = ?",
            ((index, now, str(item["id"])) for index, item in enumerate(ordered)),
        )
        self._conn.commit()
        return self._rows()

    def get(self, workspace_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT id, path, title, created_at, updated_at, sort_index, source_access "
            "FROM workspaces WHERE id = ?",
            (workspace_id,),
        ).fetchone()
        return self._row(row) if row is not None else None

    def get_by_path(self, path: str) -> dict[str, Any] | None:
        canon = canonicalize_workspace_path(path)
        row = self._conn.execute(
            "SELECT id, path, title, created_at, updated_at, sort_index, source_access "
            "FROM workspaces WHERE path = ?",
            (canon,),
        ).fetchone()
        return self._row(row) if row is not None else None

    def create(self, path: str, *, title: str = "", source_access: bool = True) -> tuple[dict[str, Any], bool]:
        """Adopt an existing directory. Same path returns the old row (created=False)."""
        canon = canonicalize_workspace_path(path)
        target = Path(canon)
        if not target.is_dir():
            raise WorkspacePathError(f"工作区目录不存在: {canon}")
        existing = self.get_by_path(canon)
        if existing is not None:
            if source_access and not existing["source_access"]:
                self._conn.execute("UPDATE workspaces SET source_access = 1 WHERE id = ?", (existing["id"],))
                self._conn.commit()
                existing["source_access"] = True
                set_workspace_source_access(canon, True)
            return existing, False
        now = _now_iso()
        display = self._allocate_title((title or "").strip() or workspace_title_from_path(canon))
        row = self._conn.execute("SELECT COALESCE(MAX(sort_index), -1) AS m FROM workspaces").fetchone()
        sort_index = int(row["m"] if row is not None else -1) + 1
        workspace_id = str(uuid.uuid4())
        self._conn.execute(
            "INSERT INTO workspaces (id, path, title, created_at, updated_at, sort_index, source_access) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (workspace_id, canon, display, now, now, sort_index, int(source_access)),
        )
        self._conn.commit()
        rec = self.get(workspace_id)
        assert rec is not None
        return rec, True

    def ensure_path(self, path: str, *, title: str = "") -> dict[str, Any]:
        """Register path if needed. Caller must mkdir the default root first."""
        rec, _created = self.create(path, title=title, source_access=False)
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
            next_title = self._allocate_title(trimmed, exclude_id=workspace_id)
        if path is not None:
            canon = canonicalize_workspace_path(path)
            if not Path(canon).is_dir():
                raise WorkspacePathError(f"工作区目录不存在: {canon}")
            existing = self.get_by_path(canon)
            if existing is not None and existing["id"] != workspace_id:
                raise WorkspacePathError("该文件夹已被其他工作区使用")
            next_path = canon
            set_workspace_source_access(rec["path"], False)
        now = _now_iso()
        self._conn.execute(
            "UPDATE workspaces SET title = ?, path = ?, updated_at = ?, source_access = ? WHERE id = ?",
            (next_title, next_path, now, int(bool(path) or rec["source_access"]), workspace_id),
        )
        self._conn.commit()
        return self.get(workspace_id)

    def delete(self, workspace_id: str) -> bool:
        """Drop the registration only. Disk and sessions stay."""
        rec = self.get(workspace_id)
        cur = self._conn.execute("DELETE FROM workspaces WHERE id = ?", (workspace_id,))
        self._conn.commit()
        if rec is not None:
            set_workspace_source_access(rec["path"], False)
        return cur.rowcount > 0
