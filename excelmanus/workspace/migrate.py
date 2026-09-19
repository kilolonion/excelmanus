"""One-shot: leftover ``outputs/backups`` copies → RevisionStore blobs.

Maps timestamped / basename overlay copies onto a live identity when that
file exists. Unmapped leftovers are left on disk (catalog still hides them).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from excelmanus.workspace.identity import (
    looks_like_timestamped_backup_name,
    resolve_canonical,
    IdentityError,
)
from excelmanus.workspace.revisions import RevisionStore

logger = logging.getLogger(__name__)

_MIGRATION_VERSION = 1
_MARKER_REL = Path(".excelmanus") / "migrations" / "overlay-backups.json"
_COMPLETE_STATUSES = frozenset({"ok", "ok_with_skipped"})


def migration_marker_path(workspace_root: str | Path) -> Path:
    """Return the one-shot overlay migration marker path for a workspace."""
    root = Path(workspace_root).expanduser().resolve()
    return root / _MARKER_REL


def _read_marker(workspace_root: str | Path) -> dict[str, Any] | None:
    marker = migration_marker_path(workspace_root)
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("version") != _MIGRATION_VERSION:
        return None
    return data


def _write_marker(workspace_root: str | Path, summary: dict[str, Any]) -> None:
    marker = migration_marker_path(workspace_root)
    marker.parent.mkdir(parents=True, exist_ok=True)
    has_skipped = bool(summary.get("skipped"))
    payload = {
        "version": _MIGRATION_VERSION,
        "status": "ok_with_skipped" if has_skipped else "ok",
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "migrated_count": len(summary.get("migrated") or []),
        "skipped_count": len(summary.get("skipped") or []),
    }
    marker.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + chr(10),
        encoding="utf-8",
    )


def ensure_overlay_migrated(
    workspace_root: str | Path,
    *,
    force: bool = False,
) -> dict[str, Any]:
    """Run the overlay import once per workspace.

    The marker makes startup cheap and prevents duplicate revisions on repeat
    visits. ``force=True`` re-scans even after a completed marker (manual run).
    Failed runs leave no marker, so they retry on the next startup.
    """
    if not force:
        marker = _read_marker(workspace_root)
        if marker is not None and marker.get("status") in _COMPLETE_STATUSES:
            return {
                "migrated": [],
                "skipped": [],
                "errors": [],
                "already_migrated": True,
                "marker": marker,
            }
    summary = migrate_overlay_backups(workspace_root)
    summary["already_migrated"] = False
    root = Path(workspace_root).expanduser().resolve()
    backups_existed = (root / "outputs" / "backups").is_dir()
    if not summary.get("errors") and backups_existed:
        try:
            _write_marker(workspace_root, summary)
        except OSError:
            logger.warning("无法写入 overlay 迁移 marker: %s", workspace_root, exc_info=True)
    return summary


def migrate_overlay_backups(workspace_root: str | Path) -> dict[str, Any]:
    """Import leftover overlay copies into ``.excelmanus/revisions/``.

    Returns a summary: ``migrated``, ``skipped``, ``errors``.
    """
    root = Path(workspace_root).expanduser().resolve()
    backups = root / "outputs" / "backups"
    store = RevisionStore(root)
    migrated: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    errors: list[dict[str, str]] = []

    if not backups.is_dir():
        return {"migrated": migrated, "skipped": skipped, "errors": errors}

    for src in sorted(backups.rglob("*")):
        if not src.is_file():
            continue
        identity = _identity_for_backup(root, src)
        if identity is None:
            skipped.append({"path": str(src.relative_to(root).as_posix()), "reason": "unmapped"})
            continue
        try:
            data = src.read_bytes()
            rec = store.checkpoint(
                identity,
                data,
                label="migrated-overlay",
            )
            migrated.append({
                "from": src.relative_to(root).as_posix(),
                "identity": identity,
                "revision_id": rec.id,
            })
        except Exception as exc:
            logger.debug("overlay migrate failed: %s", src, exc_info=True)
            errors.append({
                "path": src.relative_to(root).as_posix(),
                "error": str(exc),
            })
    return {"migrated": migrated, "skipped": skipped, "errors": errors}


def _identity_for_backup(root: Path, src: Path) -> str | None:
    name = src.name
    stem = src.stem
    ext = src.suffix
    candidates: list[str] = []
    if looks_like_timestamped_backup_name(name):
        # {stem}_{YYYYMMDDTHHMMSS}_{4hex}{ext}
        parts = stem.rsplit("_", 2)
        if len(parts) == 3:
            candidates.append(f"{parts[0]}{ext}")
    candidates.append(name)

    hits: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        for rel in (candidate, f"uploads/{candidate}"):
            try:
                ident = resolve_canonical(root, rel)
            except IdentityError:
                continue
            if (root / ident.relative).is_file() and ident.relative not in seen:
                seen.add(ident.relative)
                hits.append(ident.relative)
        uploads = root / "uploads"
        if uploads.is_dir():
            try:
                for entry in uploads.iterdir():
                    if entry.is_file() and entry.name.endswith(candidate) and entry.name[9:] == candidate:
                        try:
                            rel = resolve_canonical(root, f"uploads/{entry.name}").relative
                        except IdentityError:
                            continue
                        if rel not in seen:
                            seen.add(rel)
                            hits.append(rel)
            except OSError:
                pass
    if len(hits) == 1:
        return hits[0]
    return None


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json
    import os
    import sys

    parser = argparse.ArgumentParser(
        description="Import leftover outputs/backups copies into .excelmanus/revisions",
    )
    parser.add_argument(
        "workspace",
        nargs="?",
        default=".",
        help="workspace root (default: cwd)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="re-scan even if the workspace marker says migration already completed",
    )
    args = parser.parse_args(argv)
    summary = ensure_overlay_migrated(args.workspace, force=args.force)
    json.dump(summary, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 1 if summary.get("errors") else 0


if __name__ == "__main__":
    raise SystemExit(main())
