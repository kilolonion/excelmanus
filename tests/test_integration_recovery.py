"""Recovery through interrupted production writes; no real process/network use."""
from pathlib import Path

import pytest

from excelmanus.workbook_commit import CommitError
from excelmanus.workspace.file_service import TargetSpec, WorkspaceFileService


def test_recovery_completes_every_history_item_and_receipt(tmp_path, monkeypatch):
    svc = WorkspaceFileService(tmp_path)
    original = svc._project_one
    def fail_second(tx, item):
        if item.rel == "b.txt":
            raise OSError("disk full")
        return original(tx, item)
    with monkeypatch.context() as m:
        m.setattr(svc, "_project_one", fail_second)
        receipt = svc.apply_batch([TargetSpec("create", "a.txt", data=b"a"), TargetSpec("create", "b.txt", data=b"b")])
    assert receipt.history_state == "pending_recover"
    svc.recover()
    svc.recover()
    assert len(svc.list_history("a.txt")) == len(svc.list_history("b.txt")) == 1
    assert svc.txlog.read_receipt(receipt.operation_id)["history_state"] == "recorded"


def test_committed_without_receipt_replays_success(tmp_path, monkeypatch):
    svc = WorkspaceFileService(tmp_path)
    with monkeypatch.context() as m:
        m.setattr(svc.txlog, "write_receipt", lambda *a: (_ for _ in ()).throw(OSError("interrupted")))
        with pytest.raises(OSError):
            svc.create("a.txt", b"done", operation_id="op")
    receipt = svc.create("a.txt", b"done", operation_id="op")
    assert receipt.state == "committed"
    assert receipt.targets[0].after_version
    assert len(svc.list_history("a.txt")) == 1


def test_partial_publish_resumes_once(tmp_path, monkeypatch):
    svc = WorkspaceFileService(tmp_path)
    original = svc._publish_one
    def fail_second(item):
        if item.rel == "b.txt":
            raise CommitError("SAVE_FAILED", "interrupted")
        return original(item)
    specs = [TargetSpec("create", "a.txt", data=b"a"), TargetSpec("create", "b.txt", data=b"b")]
    with monkeypatch.context() as m:
        m.setattr(svc, "_publish_one", fail_second)
        partial = svc.apply_batch(specs, operation_id="op")
    assert partial.state == "failed_partial"
    assert svc.list_history("a.txt")
    recovered = svc.apply_batch(specs, operation_id="op")
    assert recovered.state == "committed"
    assert (tmp_path / "b.txt").read_bytes() == b"b"
    assert len(svc.list_history("a.txt")) == 1


def test_partial_recovery_preserves_external_edit_and_can_abort(tmp_path, monkeypatch):
    svc = WorkspaceFileService(tmp_path)
    first = svc.create("b.txt", b"before")
    original = svc._publish_one
    def fail_second(item):
        if item.rel == "b.txt":
            raise CommitError("SAVE_FAILED", "interrupted")
        return original(item)
    specs = [TargetSpec("create", "a.txt", data=b"a"), TargetSpec("update", "b.txt", data=b"after", expected_version=first.primary_version())]
    with monkeypatch.context() as m:
        m.setattr(svc, "_publish_one", fail_second)
        svc.apply_batch(specs, operation_id="op")
    (tmp_path / "b.txt").write_bytes(b"external")
    svc.recover()
    assert svc.txlog.read_receipt("op")["state"] == "failed_partial"
    assert (tmp_path / "b.txt").read_bytes() == b"external"
    aborted = svc.abort_recovery("op")
    assert aborted.state == "aborted" and aborted.published_paths == ("a.txt",)
    svc.create("unrelated.txt", b"ok")


def test_builder_id_binds_canonical_command(tmp_path):
    svc = WorkspaceFileService(tmp_path)
    created = svc.create("a.txt", b"before")
    args = dict(expected_version=created.primary_version(), operation_id="op")
    with pytest.raises(CommitError, match="intent descriptor"):
        svc.update_with_builder("a.txt", lambda _: b"x", **args)
    first = svc.update_with_builder("a.txt", lambda _: b"x", intent={"set": "x"}, **args)
    again = svc.update_with_builder("a.txt", lambda _: b"x", intent={"set": "x"}, **args)
    assert first.tx_id == again.tx_id
    with pytest.raises(CommitError) as exc:
        svc.update_with_builder("a.txt", lambda _: b"y", intent={"set": "y"}, **args)
    assert exc.value.code == "OPERATION_ID_REUSED"


def test_changed_source_dependency_prevents_all_targets(tmp_path):
    from excelmanus.workspace.file_service import ReadDependency
    svc = WorkspaceFileService(tmp_path)
    src = svc.create("source.txt", b"old")
    (tmp_path / "source.txt").write_bytes(b"new")
    with pytest.raises(CommitError):
        svc.apply_batch([TargetSpec("create", "out.txt", data=b"old")], read_dependencies=[ReadDependency("source.txt", src.primary_version())])
    assert not (tmp_path / "out.txt").exists()
