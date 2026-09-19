"""One-shot overlay leftover import."""

from __future__ import annotations

from pathlib import Path

from excelmanus.workspace.migrate import (
    ensure_overlay_migrated,
    migrate_overlay_backups,
)
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


def _write_overlay_fixture(tmp_path: Path) -> None:
    (tmp_path / "sales.xlsx").write_bytes(b"live")
    backups = tmp_path / "outputs" / "backups"
    backups.mkdir(parents=True)
    (backups / "sales_20260911T091344_f525.xlsx").write_bytes(b"overlay-copy")


def test_ensure_overlay_migrated_is_idempotent(tmp_path: Path) -> None:
    _write_overlay_fixture(tmp_path)
    first = ensure_overlay_migrated(tmp_path)
    assert len(first["migrated"]) == 1
    assert first["already_migrated"] is False

    store = RevisionStore(tmp_path)
    count = len(store.list("sales.xlsx"))

    second = ensure_overlay_migrated(tmp_path)
    assert second["already_migrated"] is True
    assert len(store.list("sales.xlsx")) == count


def test_overlay_skips_ambiguous_basename(tmp_path: Path) -> None:
    (tmp_path / "report.xlsx").write_bytes(b"root")
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    (uploads / "aabbccdd_report.xlsx").write_bytes(b"upload")
    backups = tmp_path / "outputs" / "backups"
    backups.mkdir(parents=True)
    (backups / "report.xlsx").write_bytes(b"overlay")
    summary = migrate_overlay_backups(tmp_path)
    assert summary["migrated"] == []
    assert any(item.get("reason") == "unmapped" for item in summary["skipped"])
    store = RevisionStore(tmp_path)
    assert store.list("report.xlsx") == []


def test_ensure_overlay_migrated_without_backups_leaves_marker_open(tmp_path: Path) -> None:
    first = ensure_overlay_migrated(tmp_path)
    assert first["migrated"] == []
    marker = tmp_path / ".excelmanus" / "migrations" / "overlay-backups.json"
    assert not marker.exists()

    # 如果后来恢复了旧备份目录，下一次仍会自动导入（无需 --force）。
    (tmp_path / "outputs" / "backups").mkdir(parents=True)
    (tmp_path / "sales.xlsx").write_bytes(b"live")
    (tmp_path / "outputs" / "backups" / "sales_20260911T091344_f525.xlsx").write_bytes(b"copy")
    second = ensure_overlay_migrated(tmp_path)
    assert len(second["migrated"]) == 1
    assert marker.is_file()
