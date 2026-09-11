"""One-shot: leftover ``outputs/backups`` copies → RevisionStore blobs.

Maps timestamped / basename overlay copies onto a live identity when that
file exists. Unmapped leftovers are left on disk (catalog still hides them).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from excelmanus.workspace.identity import (
    looks_like_timestamped_backup_name,
    resolve_canonical,
    IdentityError,
)
from excelmanus.workspace.revisions import RevisionStore

logger = logging.getLogger(__name__)


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

    for candidate in candidates:
        for rel in (candidate, f"uploads/{candidate}"):
            try:
                ident = resolve_canonical(root, rel)
            except IdentityError:
                continue
            if (root / ident.relative).is_file():
                return ident.relative
        uploads = root / "uploads"
        if uploads.is_dir():
            try:
                for entry in uploads.iterdir():
                    if entry.is_file() and entry.name.endswith(candidate) and entry.name[9:] == candidate:
                        try:
                            return resolve_canonical(root, f"uploads/{entry.name}").relative
                        except IdentityError:
                            continue
            except OSError:
                pass
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
        default=os.environ.get("EXCELMANUS_WORKSPACE_ROOT", "."),
        help="workspace root (default: EXCELMANUS_WORKSPACE_ROOT or cwd)",
    )
    args = parser.parse_args(argv)
    summary = migrate_overlay_backups(args.workspace)
    json.dump(summary, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 1 if summary.get("errors") else 0


if __name__ == "__main__":
    raise SystemExit(main())
