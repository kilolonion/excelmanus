"""Canonical workspace paths and display titles."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_TITLE_NUMBER_SUFFIX = re.compile(r"^(.*?)(?:\s*[\(（](\d+)[\)）])\s*$")


def canonicalize_workspace_path(path: str | Path) -> str:
    """Return an absolute, expanded, resolved workspace path string."""
    return str(Path(path).expanduser().resolve())


def default_workspace_path(config: Any) -> str:
    """Keep the process workspace outside the application checkout by default."""
    from excelmanus.data_home import get_data_home, get_package_root

    data_root = str(getattr(config, "data_root", "") or "").strip()
    workspace_root = str(getattr(config, "workspace_root", "") or "").strip()
    candidate = data_root or workspace_root
    if not candidate or canonicalize_workspace_path(candidate) == canonicalize_workspace_path(get_package_root()):
        return canonicalize_workspace_path(get_data_home())
    return canonicalize_workspace_path(candidate)


def workspace_title_from_path(path: str | Path) -> str:
    """Folder basename for sidebar group headers."""
    resolved = Path(path)
    name = resolved.name.strip()
    return name or str(resolved)


def unique_workspace_title(desired: str, taken: set[str] | frozenset[str]) -> str:
    """Keep the requested name, or append `` (n)`` when that title is already used."""
    base = (desired or "").strip() or "工作区"
    if base not in taken:
        return base
    match = _TITLE_NUMBER_SUFFIX.fullmatch(base)
    stem = match.group(1).rstrip() if match else base
    if not stem:
        stem = "工作区"
    n = 1
    while True:
        candidate = f"{stem} ({n})"
        if candidate not in taken:
            return candidate
        n += 1


def paths_equal(left: str | Path, right: str | Path) -> bool:
    try:
        return canonicalize_workspace_path(left) == canonicalize_workspace_path(right)
    except (OSError, ValueError):
        return str(left) == str(right)
