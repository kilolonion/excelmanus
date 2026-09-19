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

from excelmanus.workbook_commit import (
    CommitResult,
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


def prepare_pending_run_dir(workspace_root: str | Path, run_id: str) -> Path:
    """Create this run's pending directory before launching the sandbox.

    ``chmod 700`` is extra hardening. Same-uid sibling ``run_code`` processes
    are isolated by the wrapper's ``_is_under_foreign_pending`` path checks,
    not by Unix mode bits.
    """
    import os

    run_dir = pending_run_dir(workspace_root, run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(run_dir, 0o700)
    except OSError:
        pass
    return run_dir


def discard_orphan_pending_dirs(workspace_root: str | Path) -> int:
    """Startup: leftover pending dirs are unpublished orphans. Never auto-publish."""
    root = Path(workspace_root).resolve() / ".excelmanus" / "pending"
    if not root.is_dir():
        return 0
    removed = 0
    try:
        children = list(root.iterdir())
    except OSError:
        return 0
    for child in children:
        if not child.is_dir():
            continue
        shutil.rmtree(child, ignore_errors=True)
        removed += 1
    return removed


def discard_pending_run_dir(workspace_root: str | Path, run_id: str) -> int:
    """只读执行：丢弃本次 run_code 的全部 pending 写入，返回丢弃条数。

    只读引擎的 run_code 允许执行计算，但其工作区写入尝试一律不发布；
    清掉整个 per-run 目录，发布步骤据此知道没有任何文件落盘。
    """
    import shutil

    if not run_id or not _SAFE_RUN_ID.match(run_id):
        return 0
    run_dir = pending_run_dir(workspace_root, run_id)
    if not run_dir.is_dir():
        return 0
    try:
        rows = len(_read_pending_manifest(run_dir))
    except Exception:
        rows = 0
    shutil.rmtree(run_dir, ignore_errors=True)
    return rows


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
    by_rel: dict[str, str] = {}
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
            by_rel[rel] = name
    return list(by_rel.items())


def publish_bytes(
    workspace_root: str | Path,
    file_path: str,
    data: bytes,
    *,
    expected_version: str | None = None,
    record_history: bool = True,
) -> CommitResult:
    """Resolve identity then atomically publish bytes onto the live user path."""
    del record_history
    from excelmanus.workspace.file_service import receipt_to_commit_result, WorkspaceFileService

    root = Path(workspace_root)
    canon = resolve_canonical(root, file_path)
    dest = root / canon.relative
    svc = WorkspaceFileService(root)
    if dest.is_file():
        receipt = svc.update(canon.relative, data, expected_version=expected_version)
    else:
        receipt = svc.create(canon.relative, data)
    return receipt_to_commit_result(receipt, bytes_written=len(data))


def publish_pending_writes(
    workspace_root: str | Path,
    stderr: str = "",
    *,
    run_id: str | None = None,
    expected_versions: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Host-side Runtime commit for sandbox pending writes.

    Source bytes come only from ``.excelmanus/pending/{run_id}/`` listed in
    that directory's manifest. Stderr is ignored for path selection.
    ``expected_versions`` is the host-seen CAS map; existing files without
    an entry conflict instead of matching the live disk hash.
    Duplicate manifest rows for the same rel keep the last entry.
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
                from excelmanus.workspace.file_service import (
                    WorkspaceFileService,
                    receipt_to_commit_result,
                )

                svc = WorkspaceFileService(root)
                if dest.is_file():
                    receipt = svc.update(rel, data, expected_version=expected)
                else:
                    receipt = svc.create(rel, data)
                result = receipt_to_commit_result(receipt, bytes_written=len(data))
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
