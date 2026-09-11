"""One-shot overlay leftover import."""

from __future__ import annotations

from pathlib import Path

from excelmanus.workspace.migrate import migrate_overlay_backups
from excelmanus.workspace.revisions import RevisionStore


def test_migrate_maps_timestamped_copy_onto_live_identity(tmp_path: Path) -> None:
    (tmp_path / "sales.xlsx").write_bytes(b"live")
    backups = tmp_path / "outputs" / "backups"
    backups.mkdir(parents=True)
    src = backups / "sales_20260911T091344_f525.xlsx"
    src.write_bytes(b"overlay-copy")

    summary = migrate_overlay_backups(tmp_path)
    assert len(summary["migrated"]) == 1
    assert summary["migrated"][0]["identity"] == "sales.xlsx"
    assert summary["errors"] == []

    store = RevisionStore(tmp_path)
    recs = store.list("sales.xlsx")
    assert recs
    assert recs[-1].reason == "checkpoint"
    assert recs[-1].label == "migrated-overlay"
    assert store.read_blob("sales.xlsx", recs[-1].sha256) == b"overlay-copy"


def test_migrate_skips_unmapped_leftover(tmp_path: Path) -> None:
    backups = tmp_path / "outputs" / "backups"
    backups.mkdir(parents=True)
    (backups / "orphan.xlsx").write_bytes(b"nobody")
    summary = migrate_overlay_backups(tmp_path)
    assert summary["migrated"] == []
    assert summary["skipped"]
    assert (backups / "orphan.xlsx").is_file()
