"""工作区隔离与规范身份。

``IsolatedWorkspace`` 仍是现有隔离层。新代码走 ``identity`` / ``revisions``；
不要再往 ``outputs/backups`` 写公开身份。
"""

from excelmanus.workspace.identity import (
    CanonicalPath,
    DiffQuery,
    IdentityError,
    StaleVersionError,
    WorkbookVersionRef,
    catalog,
    collect_public_identities,
    display_name_for,
    public_identity,
    resolve_canonical,
)
from excelmanus.workspace.file_service import WorkspaceFileService
from excelmanus.workspace.runtime import publish_bytes, publish_pending_writes, read_version_bytes
from excelmanus.workspace.isolated import (
    IsolatedWorkspace,
    SandboxConfig,
    SandboxEnv,
)
from excelmanus.workspace.revisions import (
    RevisionIntegrityError,
    RevisionRecord,
    RevisionStore,
)
from excelmanus.workspace.migrate import (
    ensure_overlay_migrated,
    migrate_overlay_backups,
)

__all__ = [
    "CanonicalPath",
    "DiffQuery",
    "IdentityError",
    "IsolatedWorkspace",
    "RevisionIntegrityError",
    "RevisionRecord",
    "RevisionStore",
    "SandboxConfig",
    "SandboxEnv",
    "StaleVersionError",
    "WorkbookVersionRef",
    "WorkspaceFileService",
    "catalog",
    "collect_public_identities",
    "display_name_for",
    "ensure_overlay_migrated",
    "migrate_overlay_backups",
    "public_identity",
    "publish_bytes",
    "publish_pending_writes",
    "read_version_bytes",
    "resolve_canonical",
]
