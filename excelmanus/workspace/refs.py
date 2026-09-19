"""WorkspaceRef / FileRef — 本批文件与工作区身份合同。

稳定 file_id 留给第 4 批；这里只用 (工作区, 规范相对路径)。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from excelmanus.workspace.paths import canonicalize_workspace_path, paths_equal


@dataclass(frozen=True)
class WorkspaceRef:
    """工作区身份。有 Store id 时用 id；否则用规范化根路径。"""

    root: Path
    workspace_id: str | None = None
    title: str = ""

    @staticmethod
    def from_root(
        root: str | Path,
        *,
        workspace_id: str | None = None,
        title: str = "",
    ) -> "WorkspaceRef":
        return WorkspaceRef(
            root=Path(canonicalize_workspace_path(root)),
            workspace_id=(str(workspace_id).strip() or None) if workspace_id else None,
            title=title or "",
        )

    def identity_key(self) -> str:
        if self.workspace_id:
            return f"id:{self.workspace_id}"
        return f"path:{canonicalize_workspace_path(self.root)}"

    def equals(self, other: "WorkspaceRef") -> bool:
        if self.workspace_id and other.workspace_id:
            return self.workspace_id == other.workspace_id
        return paths_equal(self.root, other.root)


@dataclass(frozen=True)
class FileRef:
    """工作区内文件定位。observed_version 是第 3 批快照的槽位。"""

    workspace: WorkspaceRef
    relative: str
    observed_version: str | None = None
