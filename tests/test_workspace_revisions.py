"""RevisionStore — capture pair, prune, discard tx, restore."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from excelmanus.workspace.revisions import RevisionIntegrityError, RevisionStore


def test_put_list_get_and_blob_roundtrip(tmp_path: Path) -> None:
    store = RevisionStore(tmp_path)
    rec = store.add_record(
        path="./sales.xlsx",
        data=b"before-bytes",
        reason="beforeEdit",
        transaction_id="tx1",
    )
    assert rec.sequence == 1
    assert rec.path == "sales.xlsx"
    assert rec.reason == "beforeEdit"
    assert rec.created_at
    assert rec.to_public_dict()["created_at"] == rec.created_at
    listed = store.list("sales.xlsx")
    assert [r.id for r in listed] == [rec.id]
    loaded = store.get("sales.xlsx", rec.id)
    assert loaded is not None
    assert loaded.sha256 == rec.sha256
    assert store.read_blob("sales.xlsx", rec.sha256) == b"before-bytes"
    key_dir = store.root / store.path_key("sales.xlsx")
    assert (key_dir / "records" / f"{rec.id}.json").is_file()
    assert (key_dir / "blobs" / rec.sha256).is_file()


def test_put_blob_skips_existing(tmp_path: Path) -> None:
    store = RevisionStore(tmp_path)
    first = store.put_blob("a.xlsx", b"same")
    second = store.put_blob("a.xlsx", b"same")
    assert first == second
    blob = store.root / store.path_key("a.xlsx") / "blobs" / first
    assert blob.read_bytes() == b"same"


def test_read_blob_accepts_legacy_xlsx_suffix(tmp_path: Path) -> None:
    store = RevisionStore(tmp_path)
    digest = hashlib.sha256(b"legacy").hexdigest()
    blob_dir = store.root / store.path_key("a.docx") / "blobs"
    blob_dir.mkdir(parents=True, exist_ok=True)
    (blob_dir / f"{digest}.xlsx").write_bytes(b"legacy")
    assert store.read_blob("a.docx", digest) == b"legacy"


def test_read_blob_verifies_digest(tmp_path: Path) -> None:
    store = RevisionStore(tmp_path)
    digest = store.put_blob("a.xlsx", b"good")
    blob = store.root / store.path_key("a.xlsx") / "blobs" / digest
    blob.write_bytes(b"tampered")
    with pytest.raises(RevisionIntegrityError):
        store.read_blob("a.xlsx", digest)


def test_capture_edit_pair_and_prune(tmp_path: Path) -> None:
    store = RevisionStore(tmp_path)
    tx, before, after = store.capture_edit_pair(
        "book.xlsx",
        before_bytes=b"old",
        after_bytes=b"new",
        transaction_id="tx-pair",
    )
    assert tx == "tx-pair"
    assert before is not None
    assert before.reason == "beforeEdit"
    assert after.reason == "afterEdit"
    assert after.parent_revision_id == before.id
    listed = store.list("book.xlsx")
    assert [r.reason for r in listed] == ["beforeEdit", "afterEdit"]


def test_capture_edit_pair_create_has_after_only(tmp_path: Path) -> None:
    store = RevisionStore(tmp_path)
    _tx, before, after = store.capture_edit_pair(
        "new.xlsx",
        before_bytes=None,
        after_bytes=b"created",
    )
    assert before is None
    assert after.reason == "afterEdit"
    assert store.list("new.xlsx") == [after]


def test_discard_transaction_removes_records_and_blobs(tmp_path: Path) -> None:
    store = RevisionStore(tmp_path)
    store.capture_edit_pair("book.xlsx", before_bytes=b"a", after_bytes=b"b", transaction_id="keep")
    store.capture_edit_pair("book.xlsx", before_bytes=b"b", after_bytes=b"c", transaction_id="drop")
    removed = store.discard_transaction("drop")
    assert removed == 2
    listed = store.list("book.xlsx")
    assert [r.transaction_id for r in listed] == ["keep", "keep"]
    keep_blobs = {r.sha256 for r in listed}
    blob_dir = store.root / store.path_key("book.xlsx") / "blobs"
    on_disk = {p.stem if p.suffix else p.name for p in blob_dir.iterdir() if p.is_file()}
    assert on_disk == keep_blobs
    assert hashlib.sha256(b"c").hexdigest() not in on_disk


def test_failed_transaction_never_lists(tmp_path: Path) -> None:
    store = RevisionStore(tmp_path)
    store.add_record(path="a.xlsx", data=b"x", reason="beforeEdit", transaction_id="bad")
    store.discard_transaction("bad")
    assert store.list("a.xlsx") == []
    assert store.find_by_transaction("bad") == []


def test_prune_keeps_newest(tmp_path: Path) -> None:
    store = RevisionStore(tmp_path)
    ids: list[str] = []
    for i in range(5):
        rec = store.add_record(
            path="book.xlsx",
            data=f"v{i}".encode(),
            reason="afterEdit",
            transaction_id=f"tx{i}",
        )
        ids.append(rec.id)
    removed = store.prune("book.xlsx", keep=2)
    assert removed == 3
    remaining = store.list("book.xlsx")
    assert [r.id for r in remaining] == ids[-2:]


def test_restore_prepare_and_finish(tmp_path: Path) -> None:
    store = RevisionStore(tmp_path)
    _tx, _before, after = store.capture_edit_pair(
        "book.xlsx",
        before_bytes=b"v0",
        after_bytes=b"v1",
    )
    tx, before_restore, blob = store.restore_prepare(
        "book.xlsx",
        after.id,
        current_bytes=b"v1",
    )
    assert blob == b"v1"
    assert before_restore is not None
    assert before_restore.reason == "beforeRestore"
    finished = store.finish_restore(
        "book.xlsx",
        transaction_id=tx,
        after_bytes=blob,
        parent_revision_id=before_restore.id,
    )
    assert finished.reason == "afterEdit"
    reasons = [r.reason for r in store.list("book.xlsx")]
    assert "beforeRestore" in reasons
    assert reasons[-1] == "afterEdit"


def test_prune_keeps_labeled_checkpoint(tmp_path: Path) -> None:
    store = RevisionStore(tmp_path)
    store.checkpoint("book.xlsx", b"snap", label="keep-me")
    for i in range(45):
        store.add_record(
            path="book.xlsx",
            data=f"v{i}".encode(),
            reason="afterEdit",
            transaction_id=f"tx{i}",
        )
    store.prune("book.xlsx", keep=40)
    labels = [r.label for r in store.list("book.xlsx")]
    assert "keep-me" in labels


def test_checkpoint_does_not_change_path(tmp_path: Path) -> None:
    store = RevisionStore(tmp_path)
    rec = store.checkpoint("book.xlsx", b"snap", label="before")
    assert rec.reason == "checkpoint"
    assert rec.label == "before"
    assert rec.to_public_dict()["revision_id"] == rec.id
    assert rec.to_public_dict()["content_version"] == f"sha256:{rec.sha256}"
