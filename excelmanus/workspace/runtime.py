"""Workbook publish entry: CanonicalPath + AtomicPublish.

Publish path: CanonicalPath + AtomicPublish.
"""

from __future__ import annotations

import json
import re
import secrets
import shutil
from pathlib import Path
from typing import Any

from excelmanus.security.guard import FileAccessGuard
from excelmanus.workbook_commit import (
    CommitResult,
    commit_bytes,
    content_version_of,
    normalize_version_path,
)
from excelmanus.workspace.identity import (
    StaleVersionError,
    WorkbookVersionRef,
    resolve_canonical,
)

PENDING_RUN_ID_ENV = "EXCELMANUS_PENDING_RUN_ID"
_SAFE_RUN_ID = re.compile(r"^[0-9a-fA-F]{8,64}$")


def allocate_pending_run_id() -> str:
    """Host-issued id for one run_code pending directory."""
    return secrets.token_hex(16)


def pending_run_dir(workspace_root: str | Path, run_id: str) -> Path:
    return Path(workspace_root).resolve() / ".excelmanus" / "pending" / run_id


def _contained_file(root: Path, candidate: Path) -> Path | None:
    """Return resolved file if it stays inside root; never follow a path out."""
    try:
        resolved_root = root.resolve()
        resolved = candidate.resolve()
        resolved.relative_to(resolved_root)
    except (OSError, ValueError):
        return None
    if not resolved.is_file():
        return None
    return resolved


def _read_pending_manifest(run_dir: Path) -> list[tuple[str, str]]:
    manifest = run_dir / "manifest.jsonl"
    if not manifest.is_file():
        return []
    entries: list[tuple[str, str]] = []
    try:
        text = manifest.read_text(encoding="utf-8")
    except OSError:
        return []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if not isinstance(rec, dict):
            continue
        rel = normalize_version_path(str(rec.get("rel") or ""))
        name = Path(str(rec.get("name") or "")).name
        if rel and name and name != "manifest.jsonl":
            entries.append((rel, name))
    return entries


def publish_bytes(
    workspace_root: str | Path,
    file_path: str,
    data: bytes,
    *,
    expected_version: str | None = None,
    record_history: bool = True,
) -> CommitResult:
    """Resolve identity then atomically publish bytes onto the live user path."""
    root = Path(workspace_root)
    canon = resolve_canonical(root, file_path)
    guard = FileAccessGuard(str(root))
    return commit_bytes(
        guard=guard,
        file_path=canon.relative,
        data=data,
        expected_version=expected_version,
        record_history=record_history,
    )


def publish_pending_writes(
    workspace_root: str | Path,
    stderr: str = "",
    *,
    run_id: str | None = None,
    expected_versions: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Host-side Runtime commit for sandbox pending spreadsheet saves.

    Source bytes come only from ``.excelmanus/pending/{run_id}/`` listed in
    that directory's manifest. Stderr is ignored for path selection.
    ``expected_versions`` is the host-seen CAS map; existing files without
    an entry conflict instead of matching the live disk hash.
    """
    del stderr
    from excelmanus.workbook_commit import CommitError, export_seen_versions

    published: list[dict[str, Any]] = []
    if not run_id or not _SAFE_RUN_ID.match(run_id):
        return published

    root = Path(workspace_root).resolve()
    run_dir = pending_run_dir(root, run_id)
    if not run_dir.is_dir():
        return published

    mapping = dict(expected_versions) if expected_versions is not None else export_seen_versions()
    mapping = {normalize_version_path(k): v for k, v in mapping.items() if k and v}

    try:
        for rel, name in _read_pending_manifest(run_dir):
            source = _contained_file(run_dir, run_dir / name)
            if source is None:
                published.append({
                    "path": rel,
                    "status": "error",
                    "error": "PENDING_PATH_INVALID",
                    "message": "pending source is not inside the run directory",
                })
                continue
            try:
                data = source.read_bytes()
            except OSError as exc:
                published.append({
                    "path": rel,
                    "status": "error",
                    "error": "SAVE_FAILED",
                    "message": str(exc),
                })
                continue

            dest = root / rel
            expected = mapping.get(rel)
            if dest.is_file() and not expected:
                published.append({
                    "path": rel,
                    "status": "error",
                    "error": "VERSION_CONFLICT",
                    "message": f"{rel} 已存在，缺少 expected_version",
                })
                continue
            try:
                result = publish_bytes(root, rel, data, expected_version=expected)
                published.append({
                    "path": result.path,
                    "content_version": result.content_version,
                    "status": "committed",
                })
            except (CommitError, ValueError) as exc:
                code = getattr(exc, "code", "SAVE_FAILED")
                published.append({
                    "path": rel,
                    "status": "error",
                    "error": code,
                    "message": str(exc),
                })
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)
    return published


def read_version_bytes(
    workspace_root: str | Path,
    ref: WorkbookVersionRef,
) -> bytes:
    """Load bytes for a VersionRef. Current fingerprint mismatch → stale."""
    root = Path(workspace_root).expanduser().resolve()
    canon = resolve_canonical(root, ref.path)
    if ref.kind == "current":
        dest = root / canon.relative
        if not dest.is_file():
            raise FileNotFoundError(canon.relative)
        data = dest.read_bytes()
        actual = content_version_of(data)
        if ref.content_version and ref.content_version != actual:
            raise StaleVersionError(canon.relative, ref.content_version, actual)
        return data
    if not ref.revision_id:
        raise ValueError("revision ref requires revision_id")
    from excelmanus.workspace.revisions import RevisionStore

    _rec, data = RevisionStore(root).read_revision(canon.relative, ref.revision_id)
    if ref.content_version:
        actual = content_version_of(data)
        if actual != ref.content_version:
            raise StaleVersionError(canon.relative, ref.content_version, actual)
    return data
