"""Fast user-file discovery using scandir's cached metadata and one scope check."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from excelmanus.security.source_isolation import protected_source_paths
from excelmanus.workspace.identity import (
    display_name_for, is_hidden_name, is_reserved_relative, is_sensitive_relative,
)

_SKIP_DIRS = frozenset({"node_modules", "__pycache__"})
EXCEL_EXTENSIONS = frozenset({".xlsx", ".xls", ".xlsm", ".xlsb", ".csv"})


def scan_workspace(root: str | Path, *, limit: int | None = 2000,
                   excel_only: bool = False) -> tuple[list[dict[str, Any]], bool]:
    root = Path(root).expanduser().resolve()
    # Compute source permission once, not several realpath operations per entry.
    protected = {
        Path(path).relative_to(root).as_posix()
        for path in protected_source_paths(root)
    }
    results: list[dict[str, Any]] = []
    incomplete = False
    pending = [(str(root), "")]
    while pending:
        directory, parent = pending.pop()
        try:
            with os.scandir(directory) as iterator:
                entries = sorted(iterator, key=lambda e: (e.name != "uploads", e.name))
        except OSError:
            # A permission/race failure means the result is only a partial
            # snapshot. Mark it so callers do not prune valid history entries.
            incomplete = True
            continue
        children = []
        for entry in entries:
            name = entry.name
            relative = f"{parent}/{name}" if parent else name
            if (is_hidden_name(name) or is_reserved_relative(relative)
                    or is_sensitive_relative(relative) or relative in protected
                    or name.startswith(("_rc_", "_sw_")) or relative == "scripts/temp"):
                continue
            try:
                # Never descend into symlinks or Windows junctions. Regular
                # entries need no realpath; file links get the full boundary check.
                is_dir = entry.is_dir(follow_symlinks=False)
                is_link = entry.is_symlink() or getattr(entry, "is_junction", lambda: False)()
                if is_link:
                    # A link inside the workspace is an alias, not a second
                    # user file.  Listing it would expose a path that later
                    # resolves to the target and creates duplicate identities.
                    continue
                if is_dir:
                    if name in _SKIP_DIRS:
                        continue
                    children.append((entry.path, relative))
                    if excel_only:
                        continue
                elif not entry.is_file() or (excel_only and Path(name).suffix.lower() not in EXCEL_EXTENSIONS):
                    continue
                if limit is not None and len(results) >= limit:
                    return results, True
                results.append({
                    "path": relative, "filename": name if is_dir else display_name_for(relative),
                    "modified_at": entry.stat().st_mtime, "is_dir": is_dir,
                })
            except (OSError, ValueError):
                incomplete = True
                continue
        pending.extend(reversed(children))
    return results, incomplete
