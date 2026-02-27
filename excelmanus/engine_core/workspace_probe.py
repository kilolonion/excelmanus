"""轻量工作区变更探针（mtime/size）。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Sequence

from excelmanus.tools.policy import (
    WORKSPACE_SCAN_EXCLUDE_PREFIXES,
    WORKSPACE_SCAN_MAX_FILES,
)

WorkspaceMtimeIndex = dict[str, tuple[int, int]]


def _normalize_rel_prefix(prefix: str) -> str:
    return prefix.replace("\\", "/").strip("/")


def _is_excluded_rel(rel_path: str, prefixes: Sequence[str]) -> bool:
    normalized = rel_path.replace("\\", "/").strip("/")
    if not normalized:
        return False
    for prefix in prefixes:
        if normalized == prefix or normalized.startswith(f"{prefix}/"):
            return True
    return False


def collect_workspace_mtime_index(
    workspace_root: str | Path,
    *,
    max_files: int = WORKSPACE_SCAN_MAX_FILES,
    exclude_prefixes: Sequence[str] = WORKSPACE_SCAN_EXCLUDE_PREFIXES,
) -> tuple[WorkspaceMtimeIndex, bool]:
    """收集工作区文件 mtime/size 索引，返回 (index, partial_scan)。"""
    root = Path(workspace_root).expanduser().resolve()
    index: WorkspaceMtimeIndex = {}
    partial_scan = False
    scanned_files = 0

    normalized_prefixes = tuple(
        _normalize_rel_prefix(prefix)
        for prefix in exclude_prefixes
        if _normalize_rel_prefix(prefix)
    )

    for walk_root, dirs, files in os.walk(root):
        walk_root_path = Path(walk_root)
        walk_rel_root = walk_root_path.relative_to(root)

        kept_dirs: list[str] = []
        for name in dirs:
            rel_path = (
                (walk_rel_root / name).as_posix()
                if str(walk_rel_root) != "."
                else Path(name).as_posix()
            )
            if _is_excluded_rel(rel_path, normalized_prefixes):
                continue
            kept_dirs.append(name)
        dirs[:] = kept_dirs

        for name in files:
            rel_path = (
                (walk_rel_root / name).as_posix()
                if str(walk_rel_root) != "."
                else Path(name).as_posix()
            )
            if _is_excluded_rel(rel_path, normalized_prefixes):
                continue

            file_path = walk_root_path / name
            if file_path.is_symlink() or not file_path.is_file():
                continue

            scanned_files += 1
            if scanned_files > max_files:
                partial_scan = True
                return index, partial_scan

            try:
                stat = file_path.stat()
            except OSError:
                continue

            index[rel_path] = (int(stat.st_mtime_ns), int(stat.st_size))

    return index, partial_scan


def has_workspace_mtime_changes(before: WorkspaceMtimeIndex, after: WorkspaceMtimeIndex) -> bool:
    """比较两次 mtime/size 索引是否发生变化。"""
    if len(before) != len(after):
        return True
    for rel_path, snapshot in before.items():
        if after.get(rel_path) != snapshot:
            return True
    return False


def diff_workspace_mtime_paths(
    before: WorkspaceMtimeIndex,
    after: WorkspaceMtimeIndex,
    *,
    max_paths: int = 128,
) -> list[str]:
    """返回发生变化的相对路径列表（按字典序，最多 max_paths 个）。"""
    changed: list[str] = []
    for rel_path in sorted(set(before) | set(after)):
        if before.get(rel_path) != after.get(rel_path):
            changed.append(rel_path)
            if len(changed) >= max_paths:
                break
    return changed
