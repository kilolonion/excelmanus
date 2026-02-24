"""Per-user workspace isolation and quota enforcement.

When auth is enabled, each user gets their own subdirectory under the
global workspace root:

    {workspace_root}/users/{user_id}/

This module provides helpers to resolve user-specific paths,
ensure the directory structure exists, and enforce per-user
storage quotas.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

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


def get_quota_config() -> tuple[int, int]:
    """Return (max_size_bytes, max_files) from env or defaults."""
    max_mb = _env_int("EXCELMANUS_WORKSPACE_MAX_SIZE_MB", DEFAULT_MAX_SIZE_MB)
    max_files = _env_int("EXCELMANUS_WORKSPACE_MAX_FILES", DEFAULT_MAX_FILES)
    return max_mb * 1024 * 1024, max_files


def get_user_workspace(workspace_root: str, user_id: str) -> str:
    """Return the per-user workspace directory, creating it if needed."""
    user_dir = os.path.join(workspace_root, "users", user_id)
    os.makedirs(user_dir, exist_ok=True)
    return str(Path(user_dir).resolve())


def get_user_upload_dir(workspace_root: str, user_id: str) -> str:
    """Return the per-user upload directory, creating it if needed."""
    upload_dir = os.path.join(workspace_root, "users", user_id, "uploads")
    os.makedirs(upload_dir, exist_ok=True)
    return upload_dir


def get_shared_workspace(workspace_root: str) -> str:
    """Return the shared workspace (no auth or legacy mode)."""
    return str(Path(workspace_root).resolve())


def resolve_workspace_for_request(
    workspace_root: str,
    user_id: str | None,
    auth_enabled: bool,
) -> str:
    """Determine which workspace root to use for a given request.

    - auth_enabled + user_id → per-user workspace
    - otherwise → shared workspace (backward compat)
    """
    if auth_enabled and user_id:
        return get_user_workspace(workspace_root, user_id)
    return get_shared_workspace(workspace_root)


# ── Workspace usage stats ──────────────────────────────────


@dataclass
class WorkspaceUsage:
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
    """Walk workspace_dir and return list of {path, name, size, modified_at} sorted by mtime asc."""
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


def get_workspace_usage(workspace_dir: str) -> WorkspaceUsage:
    """Calculate current usage for a workspace directory."""
    max_bytes, max_files = get_quota_config()
    files = scan_workspace(workspace_dir)
    total = sum(f["size"] for f in files)
    return WorkspaceUsage(
        total_bytes=total,
        file_count=len(files),
        max_bytes=max_bytes,
        max_files=max_files,
        files=files,
    )


# ── Quota enforcement ──────────────────────────────────────


def enforce_quota(workspace_dir: str) -> list[str]:
    """Delete oldest files until workspace is within quota limits.

    Returns list of deleted file paths (relative to workspace_dir).
    """
    max_bytes, max_files = get_quota_config()
    files = scan_workspace(workspace_dir)
    total = sum(f["size"] for f in files)
    deleted: list[str] = []
    ws_path = Path(workspace_dir)

    while files and (len(files) > max_files or total > max_bytes):
        oldest = files.pop(0)
        full_path = ws_path / oldest["path"]
        try:
            full_path.unlink(missing_ok=True)
            total -= oldest["size"]
            deleted.append(oldest["path"])
            logger.info("Quota: deleted %s (%d bytes)", oldest["path"], oldest["size"])
            _cleanup_empty_parents(full_path, ws_path)
        except OSError:
            logger.warning("Quota: failed to delete %s", oldest["path"], exc_info=True)

    return deleted


def check_upload_allowed(
    workspace_dir: str, incoming_size: int,
) -> tuple[bool, str]:
    """Pre-check whether an upload of ``incoming_size`` bytes is allowed.

    Returns (allowed, reason). When not allowed, the caller should reject
    the upload with a 413 or 400 error.
    """
    max_bytes, max_files = get_quota_config()
    files = scan_workspace(workspace_dir)
    current_size = sum(f["size"] for f in files)
    current_count = len(files)

    if current_count >= max_files:
        return False, f"工作空间文件数已达上限 ({max_files} 个)"
    if current_size + incoming_size > max_bytes:
        limit_mb = round(max_bytes / (1024 * 1024), 1)
        return False, f"工作空间存储已满 (上限 {limit_mb} MB)"
    return True, ""


def _cleanup_empty_parents(child: Path, root: Path) -> None:
    """Remove empty parent directories up to (but not including) root."""
    parent = child.parent
    while parent != root and parent.is_dir():
        try:
            parent.rmdir()
            parent = parent.parent
        except OSError:
            break
