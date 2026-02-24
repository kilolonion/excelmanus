"""Per-user workspace isolation.

When auth is enabled, each user gets their own subdirectory under the
global workspace root:

    {workspace_root}/users/{user_id}/

This module provides helpers to resolve user-specific paths and
ensure the directory structure exists.
"""

from __future__ import annotations

import os
from pathlib import Path


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
