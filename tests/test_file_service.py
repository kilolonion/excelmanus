"""WorkspaceFileService: lineage, receipt, recover, UI observe_live."""

from __future__ import annotations

from pathlib import Path

import pytest

from excelmanus.workbook_commit import CommitError, content_version_of
from excelmanus.workspace.file_service import WorkspaceFileService
from excelmanus.workspace.revisions import RevisionStore


def test_lifecycle_create_update_move_delete_restore(tmp_path: Path) -> None:
    svc = WorkspaceFileService(tmp_path)
    created = svc.create("life.txt", b"v1")
    assert created.state == "committed"
    lineage = created.targets[0].lineage_id
    assert lineage
    assert created.targets[0].exists_after is True

    updated = svc.update("life.txt", b"v2", expected_version=created.primary_version())
    assert updated.targets[0].lineage_id == lineage

    moved = svc.move("life.txt", "life2.txt", expected_version=updated.primary_version())
    assert moved.targets[0].lineage_id == lineage
    assert not (tmp_path / "life.txt").exists()
    assert (tmp_path / "life2.txt").read_bytes() == b"v2"

    hist = svc.list_history("life2.txt")
    assert any(r.reason == "moved" for r in hist)

    deleted = svc.delete("life2.txt", expected_version=moved.primary_version())
    assert deleted.targets[0].exists_after is False
    assert deleted.targets[0].after_version is None
    assert deleted.targets[0].before_version == content_version_of(b"v2")
    empty = svc.create("empty.txt", b"")
    assert empty.targets[0].exists_after is True
    assert empty.targets[0].after_version == content_version_of(b"")

    recs = RevisionStore(tmp_path).list("life2.txt")
    tomb = next(r for r in recs if r.reason == "deleted")
    restored = svc.restore("life2.txt", tomb.id, expected_version=None, restore_missing=True)
    assert restored.state == "committed"
    assert (tmp_path / "life2.txt").read_bytes() == b"v2"
    assert restored.targets[0].lineage_id == lineage


def test_delete_then_recreate_new_lineage(tmp_path: Path) -> None:
    svc = WorkspaceFileService(tmp_path)
    first = svc.create("slot.txt", b"old")
    svc.delete("slot.txt", expected_version=first.primary_version())
    second = svc.create("slot.txt", b"new")
    assert first.targets[0].lineage_id != second.targets[0].lineage_id
    recs = RevisionStore(tmp_path).list("slot.txt")
    tomb = next(r for r in recs if r.reason == "deleted")
    with pytest.raises(CommitError) as ei:
        svc.restore("slot.txt", tomb.id, expected_version=second.primary_version())
    assert ei.value.code == "PATH_OCCUPIED"


def test_operation_id_replay(tmp_path: Path) -> None:
    svc = WorkspaceFileService(tmp_path)
    first = svc.create("a.txt", b"x", operation_id="op-1")
    second = svc.create("a.txt", b"x", operation_id="op-1")
    assert second.tx_id == first.tx_id
    with pytest.raises(CommitError) as ei:
        svc.create("a.txt", b"y", operation_id="op-1")
    assert ei.value.code == "OPERATION_ID_REUSED"


def test_recover_prepared_aborts(tmp_path: Path) -> None:
    svc = WorkspaceFileService(tmp_path)
    svc.create("a.txt", b"before")
    tx_id = "deadbeefdeadbeef"
    svc.txlog.write_intent(
        {
            "tx_id": tx_id,
            "operation_id": "op-dead",
            "state": "prepared",
            "targets": [
                {
                    "path": "a.txt",
                    "op": "update",
                    "exists_before": True,
                    "exists_after": True,
                    "after_sha256": "00",
                    "publish_status": "pending",
                }
            ],
        }
    )
    svc.recover()
    assert svc.txlog.read_intent(tx_id)["state"] == "aborted"
    assert (tmp_path / "a.txt").read_bytes() == b"before"


def test_ui_observe_live_deletes_workspace_root(tmp_path: Path) -> None:
    svc = WorkspaceFileService(tmp_path)
    (tmp_path / "report.xlsx").write_bytes(b"root")
    (tmp_path / "uploads").mkdir()
    (tmp_path / "uploads" / "report.xlsx").write_bytes(b"copy")
    receipt = svc.delete("report.xlsx", expected_version=None, observe_live=True)
    assert receipt.state == "committed"
    assert not (tmp_path / "report.xlsx").exists()
    assert (tmp_path / "uploads" / "report.xlsx").read_bytes() == b"copy"


def test_run_code_timeout_discards_pending(tmp_path: Path) -> None:
    from excelmanus.workspace.runtime import (
        discard_pending_run_dir,
        pending_run_dir,
        prepare_pending_run_dir,
        publish_pending_writes,
    )

    run_id = "ab" * 16
    run_dir = prepare_pending_run_dir(tmp_path, run_id)
    (run_dir / "late.bin").write_bytes(b"late")
    (run_dir / "manifest.jsonl").write_text(
        '{"rel":"late.txt","name":"late.bin"}\n', encoding="utf-8"
    )
    discarded = discard_pending_run_dir(tmp_path, run_id)
    assert discarded >= 1
    assert not pending_run_dir(tmp_path, run_id).exists()
    assert not (tmp_path / "late.txt").exists()
    published = publish_pending_writes(tmp_path, run_id=run_id, expected_versions={})
    assert published == []


def test_history_failure_after_publish_is_pending_recover(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from excelmanus.workspace import revisions as rev_mod

    def boom(self, *args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(rev_mod.RevisionStore, "add_record", boom)
    svc = WorkspaceFileService(tmp_path)
    receipt = svc.create("book.xlsx", b"ok")
    assert receipt.state == "committed"
    assert receipt.history_state == "pending_recover"
    assert (tmp_path / "book.xlsx").read_bytes() == b"ok"


def test_txlog_prunes_terminal_blobs_but_keeps_receipts(tmp_path: Path) -> None:
    """终态事务的 blobs 可回收；pending_recover 与最近事务保留；receipt 存活供幂等重放。"""
    import hashlib
    import os

    from excelmanus.workspace.txlog import TxLog

    log = TxLog(tmp_path)

    def _seed(tx_id: str, operation_id: str, state: str, data: bytes, mtime: float) -> None:
        log.write_intent({"tx_id": tx_id, "operation_id": operation_id, "state": state})
        log.put_blob(tx_id, data, hashlib.sha256(data).hexdigest())
        os.utime(log.dir_for(tx_id), (mtime, mtime))

    _seed("old", "op-old", "committed", b"old-bytes", mtime=1_000_000_000.0)
    log.write_receipt("op-old", {"state": "committed", "history_state": "recorded", "tx_id": "old"})
    _seed("pending", "op-p", "failed_partial", b"pending-bytes", mtime=1_000_000_100.0)
    _seed("recent", "op-r", "committed", b"recent-bytes", mtime=1_000_000_200.0)

    removed = log.prune_terminal_blobs(keep=1)
    assert removed == 1
    assert not (log.dir_for("old") / "blobs").is_dir() or not any((log.dir_for("old") / "blobs").iterdir())
    assert any((log.dir_for("pending") / "blobs").iterdir())
    assert any((log.dir_for("recent") / "blobs").iterdir())
    # receipt 保留：幂等重放不受 GC 影响
    assert log.read_receipt("op-old")["state"] == "committed"


def test_apply_batch_prunes_accumulated_tx_blobs(tmp_path: Path) -> None:
    """超过 keep 窗口的终态事务，在下一次写入时被机会式回收。"""
    import hashlib

    from excelmanus.workspace.txlog import TxLog

    svc = WorkspaceFileService(tmp_path)
    log = TxLog(tmp_path)
    for i in range(3):
        log.write_intent({"tx_id": f"stale{i}", "operation_id": f"op{i}", "state": "committed"})
        log.put_blob(f"stale{i}", f"bytes{i}".encode(), hashlib.sha256(f"bytes{i}".encode()).hexdigest())
        os_utime_stale(log.dir_for(f"stale{i}"))

    svc.create("fresh.txt", b"fresh")
    blobs_left = [
        child
        for i in range(3)
        if (log.dir_for(f"stale{i}") / "blobs").is_dir()
        and any((log.dir_for(f"stale{i}") / "blobs").iterdir())
    ]
    assert blobs_left == []


def os_utime_stale(directory) -> None:
    import os

    os.utime(directory, (1_000_000_000.0, 1_000_000_000.0))
