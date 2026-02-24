"""Unified workspace isolation layer.

Every session operates within an ``IsolatedWorkspace`` that encapsulates
file-system root, transactional staging, sandbox configuration, and quotas.
"""

from __future__ import annotations

import logging
import os
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from excelmanus.file_versions import FileVersionManager

logger = logging.getLogger(__name__)

_EXCEL_EXTENSIONS = frozenset({".xlsx", ".xls", ".xlsm", ".xlsb", ".csv"})


# ── Quota helpers (migrated from auth/workspace.py) ────────


DEFAULT_MAX_SIZE_MB = 100
DEFAULT_MAX_FILES = 5


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if raw:
        try:
            return int(raw)
        except ValueError:
            pass
    return default


@dataclass(frozen=True)
class QuotaPolicy:
    """Per-workspace storage limits."""

    max_bytes: int
    max_files: int

    @staticmethod
    def from_env() -> QuotaPolicy:
        max_mb = _env_int("EXCELMANUS_WORKSPACE_MAX_SIZE_MB", DEFAULT_MAX_SIZE_MB)
        max_files = _env_int("EXCELMANUS_WORKSPACE_MAX_FILES", DEFAULT_MAX_FILES)
        return QuotaPolicy(max_bytes=max_mb * 1024 * 1024, max_files=max_files)

    @property
    def max_size_mb(self) -> float:
        return round(self.max_bytes / (1024 * 1024), 2)


@dataclass
class WorkspaceUsage:
    """Current storage consumption for a workspace."""

    total_bytes: int
    file_count: int
    max_bytes: int
    max_files: int
    files: list[dict]

    @property
    def size_mb(self) -> float:
        return round(self.total_bytes / (1024 * 1024), 2)

    @property
    def max_size_mb(self) -> float:
        return round(self.max_bytes / (1024 * 1024), 2)

    @property
    def over_size(self) -> bool:
        return self.total_bytes > self.max_bytes

    @property
    def over_files(self) -> bool:
        return self.file_count > self.max_files

    def to_dict(self) -> dict:
        return {
            "total_bytes": self.total_bytes,
            "size_mb": self.size_mb,
            "file_count": self.file_count,
            "max_size_mb": self.max_size_mb,
            "max_files": self.max_files,
            "over_size": self.over_size,
            "over_files": self.over_files,
            "files": self.files,
        }


def scan_workspace(workspace_dir: str) -> list[dict]:
    """Walk *workspace_dir* and return file metadata sorted by mtime."""
    results: list[dict] = []
    ws_path = Path(workspace_dir)
    if not ws_path.is_dir():
        return results
    for entry in ws_path.rglob("*"):
        if not entry.is_file():
            continue
        try:
            stat = entry.stat()
            results.append({
                "path": str(entry.relative_to(ws_path)),
                "name": entry.name,
                "size": stat.st_size,
                "modified_at": stat.st_mtime,
            })
        except OSError:
            continue
    results.sort(key=lambda f: f["modified_at"])
    return results


def _cleanup_empty_parents(child: Path, root: Path) -> None:
    parent = child.parent
    while parent != root and parent.is_dir():
        try:
            parent.rmdir()
            parent = parent.parent
        except OSError:
            break


# ── Sandbox configuration ──────────────────────────────────


@dataclass(frozen=True)
class SandboxConfig:
    """Per-workspace sandbox settings."""

    docker_enabled: bool = False
    default_tier: str = "GREEN"


# ── WorkspaceTransaction ───────────────────────────────────


class WorkspaceTransaction:
    """Unified transactional file layer.

    All staged mutations live under ``staging_dir`` until explicitly
    committed or rolled back.

    Internally delegates to :class:`FileVersionManager` for version
    tracking and physical file operations, while preserving the original
    public interface for backward compatibility.
    """

    def __init__(
        self,
        workspace_root: Path,
        staging_dir: Path,
        tx_id: str,
        *,
        scope: str = "all",
        fvm: "FileVersionManager | None" = None,
    ) -> None:
        from excelmanus.file_versions import FileVersionManager as _FVM

        self._workspace_root = workspace_root
        self._staging_dir = staging_dir
        self._staging_dir.mkdir(parents=True, exist_ok=True)
        self._tx_id = tx_id
        self._scope = scope

        # 统一版本管理器：优先使用外部注入，否则自建
        self._fvm: _FVM = fvm if fvm is not None else _FVM(workspace_root)

    # -- Properties ----------------------------------------------------------

    @property
    def tx_id(self) -> str:
        return self._tx_id

    @property
    def scope(self) -> str:
        return self._scope

    @property
    def staging_dir(self) -> Path:
        return self._staging_dir

    @property
    def workspace_root(self) -> Path:
        return self._workspace_root

    @property
    def fvm(self) -> "FileVersionManager":
        """底层 FileVersionManager 实例（供 ApprovalManager 等共享）。"""
        from excelmanus.file_versions import FileVersionManager
        return self._fvm

    @property
    def tracked_originals(self) -> set[str]:
        return set(self._fvm.staged_file_map().keys())

    # -- Path helpers --------------------------------------------------------

    def _resolve_and_validate(self, file_path: str) -> Path:
        raw = Path(file_path).expanduser()
        path = raw if raw.is_absolute() else self._workspace_root / raw
        resolved = path.resolve(strict=False)
        try:
            resolved.relative_to(self._workspace_root)
        except ValueError:
            raise ValueError(
                f"文件路径在工作区外，无法操作：{file_path}"
            )
        return resolved

    # -- Core operations -----------------------------------------------------

    def stage_for_write(self, file_path: str) -> str:
        """Ensure a staged copy exists for *file_path*.

        Delegates to FileVersionManager.stage_for_write() which handles
        original-version checkpointing and staging copy creation.
        """
        return self._fvm.stage_for_write(
            file_path, ref_id=self._tx_id, scope=self._scope,
        )

    def resolve_read(self, file_path: str) -> str:
        """Return the staged path if it exists, otherwise the original."""
        resolved = self._resolve_and_validate(file_path)
        rel = self._fvm._to_rel(resolved)
        staged = self._fvm.get_staged_path(rel)
        return staged if staged is not None else str(resolved)

    def commit_all(self) -> list[dict[str, str]]:
        """Copy all staged files back to their original locations."""
        return self._fvm.commit_all_staged()

    def commit_one(self, file_path: str) -> dict[str, str] | None:
        """Commit a single staged file back to its original location."""
        return self._fvm.commit_staged(file_path)

    def rollback_one(self, file_path: str) -> bool:
        """Discard a single staged file."""
        return self._fvm.discard_staged(file_path)

    def rollback_all(self) -> None:
        """Discard all staged files."""
        self._fvm.discard_all_staged()

    def cleanup_stale(self) -> int:
        """Remove entries whose staged file no longer exists on disk."""
        return self._fvm.prune_stale_staging()

    def to_relative(self, abs_path: str) -> str:
        """Convert an absolute path to a workspace-relative ``./`` path."""
        try:
            rel = Path(abs_path).relative_to(self._workspace_root)
            return f"./{rel}"
        except ValueError:
            return abs_path

    def list_staged(self) -> list[dict[str, str]]:
        """List all currently staged files."""
        return self._fvm.list_staged()

    def register_cow_mappings(self, mapping: dict[str, str]) -> None:
        """Merge subprocess-level CoW mappings into this transaction.

        Delegates to FileVersionManager.register_cow_mapping() which
        records both the staging entry and a version checkpoint.
        """
        if not mapping:
            return
        for src_rel, dst_rel in mapping.items():
            self._fvm.register_cow_mapping(src_rel, dst_rel)


# ── SandboxEnv ─────────────────────────────────────────────


class SandboxEnv:
    """Execution environment for a sandboxed code run.

    Binds sandbox configuration, workspace mount paths, and the active
    transaction so that CoW logs are written into the transaction's
    staging area.
    """

    def __init__(
        self,
        workspace: "IsolatedWorkspace",
        transaction: WorkspaceTransaction | None = None,
    ) -> None:
        self.workspace = workspace
        self.transaction = transaction

    @property
    def docker_enabled(self) -> bool:
        return self.workspace.sandbox_config.docker_enabled

    def get_docker_mount(self) -> str:
        return str(self.workspace.root_dir)

    def get_cow_log_path(self) -> Path:
        if self.transaction is not None:
            return self.transaction.staging_dir / f"_cow_{self.transaction.tx_id[:12]}.log"
        tmpdir = self.workspace.root_dir / ".tmp"
        tmpdir.mkdir(parents=True, exist_ok=True)
        return tmpdir / f"_cow_{secrets.token_hex(6)}.log"

    def get_tmp_dir(self) -> Path:
        tmpdir = self.workspace.root_dir / ".tmp"
        tmpdir.mkdir(parents=True, exist_ok=True)
        return tmpdir


# ── IsolatedWorkspace ──────────────────────────────────────


class IsolatedWorkspace:
    """Central abstraction for user/session file-system isolation.

    Holds the resolved workspace root, sandbox settings, and quota policy.
    Creates per-session ``WorkspaceTransaction`` instances for staging
    file mutations.
    """

    def __init__(
        self,
        root_dir: str | Path,
        *,
        owner_id: str | None = None,
        sandbox_config: SandboxConfig | None = None,
        quota: QuotaPolicy | None = None,
        transaction_enabled: bool = True,
        transaction_scope: str = "all",
    ) -> None:
        self._root_dir = Path(root_dir).expanduser().resolve()
        self._root_dir.mkdir(parents=True, exist_ok=True)
        self._owner_id = owner_id
        self._sandbox_config = sandbox_config or SandboxConfig()
        self._quota = quota or QuotaPolicy.from_env()
        self._transaction_enabled = transaction_enabled
        self._transaction_scope = transaction_scope
        self._staging_base = (self._root_dir / "outputs" / "backups").resolve()

    # -- Properties ----------------------------------------------------------

    @property
    def root_dir(self) -> Path:
        return self._root_dir

    @property
    def owner_id(self) -> str | None:
        return self._owner_id

    @property
    def sandbox_config(self) -> SandboxConfig:
        return self._sandbox_config

    @property
    def quota(self) -> QuotaPolicy:
        return self._quota

    @property
    def transaction_enabled(self) -> bool:
        return self._transaction_enabled

    @transaction_enabled.setter
    def transaction_enabled(self, value: bool) -> None:
        self._transaction_enabled = value

    @property
    def transaction_scope(self) -> str:
        return self._transaction_scope

    # -- Factory helpers -----------------------------------------------------

    def create_transaction(
        self,
        tx_id: str | None = None,
        fvm: "FileVersionManager | None" = None,
    ) -> WorkspaceTransaction:
        """Create a new ``WorkspaceTransaction`` bound to this workspace."""
        if tx_id is None:
            tx_id = secrets.token_hex(8)
        return WorkspaceTransaction(
            workspace_root=self._root_dir,
            staging_dir=self._staging_base,
            tx_id=tx_id,
            scope=self._transaction_scope,
            fvm=fvm,
        )

    def create_sandbox_env(
        self, transaction: WorkspaceTransaction | None = None,
    ) -> SandboxEnv:
        """Create a ``SandboxEnv`` for executing code in this workspace."""
        return SandboxEnv(workspace=self, transaction=transaction)

    # -- Quota operations (delegated from auth/workspace.py) -----------------

    def get_usage(self) -> WorkspaceUsage:
        files = scan_workspace(str(self._root_dir))
        total = sum(f["size"] for f in files)
        return WorkspaceUsage(
            total_bytes=total,
            file_count=len(files),
            max_bytes=self._quota.max_bytes,
            max_files=self._quota.max_files,
            files=files,
        )

    def enforce_quota(self) -> list[str]:
        """Delete oldest files until within quota. Returns deleted paths."""
        files = scan_workspace(str(self._root_dir))
        total = sum(f["size"] for f in files)
        deleted: list[str] = []
        while files and (
            len(files) > self._quota.max_files or total > self._quota.max_bytes
        ):
            oldest = files.pop(0)
            full_path = self._root_dir / oldest["path"]
            try:
                full_path.unlink(missing_ok=True)
                total -= oldest["size"]
                deleted.append(oldest["path"])
                logger.info("Quota: deleted %s (%d bytes)", oldest["path"], oldest["size"])
                _cleanup_empty_parents(full_path, self._root_dir)
            except OSError:
                logger.warning("Quota: failed to delete %s", oldest["path"], exc_info=True)
        return deleted

    def check_upload_allowed(self, incoming_size: int) -> tuple[bool, str]:
        """Pre-check whether an upload of *incoming_size* bytes is allowed."""
        files = scan_workspace(str(self._root_dir))
        current_size = sum(f["size"] for f in files)
        current_count = len(files)
        if current_count >= self._quota.max_files:
            return False, f"工作空间文件数已达上限 ({self._quota.max_files} 个)"
        if current_size + incoming_size > self._quota.max_bytes:
            limit_mb = round(self._quota.max_bytes / (1024 * 1024), 1)
            return False, f"工作空间存储已满 (上限 {limit_mb} MB)"
        return True, ""

    def get_upload_dir(self) -> Path:
        """Return the upload directory, creating it if needed."""
        upload_dir = self._root_dir / "uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)
        return upload_dir

    # -- Workspace resolution (replaces auth/workspace helpers) ──────────

    @staticmethod
    def resolve(
        global_workspace_root: str,
        *,
        user_id: str | None = None,
        auth_enabled: bool = False,
        sandbox_config: SandboxConfig | None = None,
        transaction_enabled: bool = True,
        transaction_scope: str = "all",
    ) -> "IsolatedWorkspace":
        """Resolve the workspace for a request.

        - auth_enabled + user_id  ->  per-user workspace
        - otherwise               ->  shared workspace (backward compat)
        """
        if auth_enabled and user_id:
            root = os.path.join(global_workspace_root, "users", user_id)
        else:
            root = global_workspace_root
        return IsolatedWorkspace(
            root_dir=root,
            owner_id=user_id if (auth_enabled and user_id) else None,
            sandbox_config=sandbox_config,
            transaction_enabled=transaction_enabled,
            transaction_scope=transaction_scope,
        )
