"""ApprovalStore SQLite CRUD 测试。"""
from __future__ import annotations

from pathlib import Path

import pytest

from excelmanus.database import Database
from excelmanus.stores.approval_store import ApprovalStore


@pytest.fixture()
def store(tmp_path: Path) -> ApprovalStore:
    db = Database(str(tmp_path / "test.db"))
    return ApprovalStore(db)


def _sample_record() -> dict:
    return {
        "id": "appr_001",
        "tool_name": "write_cells",
        "arguments": {"file": "test.xlsx", "range": "A1:B2"},
        "tool_scope": ["write_cells", "read_excel"],
        "created_at_utc": "2026-01-15T10:30:00+00:00",
        "applied_at_utc": "2026-01-15T10:30:05+00:00",
        "execution_status": "success",
        "undoable": True,
        "result_preview": "已写入 4 个单元格",
        "error_type": None,
        "error_message": None,
        "partial_scan": False,
        "audit_dir": "outputs/approvals/appr_001",
        "manifest_file": "outputs/approvals/appr_001/manifest.json",
        "patch_file": "outputs/approvals/appr_001/changes.patch",
        "repo_diff_before": "outputs/approvals/appr_001/repo_diff_before.txt",
        "repo_diff_after": "outputs/approvals/appr_001/repo_diff_after.txt",
        "changes": [
            {"path": "test.xlsx", "before_exists": True, "after_exists": True}
        ],
        "binary_snapshots": [
            {"path": "test.xlsx", "snapshot_file": "snap.bin", "hash_sha256": "abc", "size_bytes": 1024}
        ],
    }


class TestApprovalStoreSave:
    def test_save_and_get(self, store: ApprovalStore) -> None:
        record = _sample_record()
        store.save(record)
        loaded = store.get("appr_001")
        assert loaded is not None
        assert loaded["id"] == "appr_001"
        assert loaded["tool_name"] == "write_cells"
        assert loaded["arguments"] == {"file": "test.xlsx", "range": "A1:B2"}
        assert loaded["tool_scope"] == ["write_cells", "read_excel"]
        assert loaded["undoable"] is True
        assert loaded["changes"] == [
            {"path": "test.xlsx", "before_exists": True, "after_exists": True}
        ]

    def test_save_upserts(self, store: ApprovalStore) -> None:
        record = _sample_record()
        store.save(record)
        record["result_preview"] = "updated preview"
        store.save(record)
        loaded = store.get("appr_001")
        assert loaded["result_preview"] == "updated preview"

    def test_get_missing_returns_none(self, store: ApprovalStore) -> None:
        assert store.get("nonexistent") is None


class TestApprovalStoreList:
    def test_list_all(self, store: ApprovalStore) -> None:
        for i in range(3):
            r = _sample_record()
            r["id"] = f"appr_{i:03d}"
            store.save(r)
        results = store.list_approvals()
        assert len(results) == 3

    def test_list_by_status(self, store: ApprovalStore) -> None:
        r1 = _sample_record()
        r1["id"] = "a1"
        r1["execution_status"] = "success"
        r2 = _sample_record()
        r2["id"] = "a2"
        r2["execution_status"] = "failed"
        store.save(r1)
        store.save(r2)
        success = store.list_approvals(status="success")
        assert len(success) == 1
        assert success[0]["id"] == "a1"

    def test_list_with_limit_offset(self, store: ApprovalStore) -> None:
        for i in range(5):
            r = _sample_record()
            r["id"] = f"appr_{i:03d}"
            store.save(r)
        page = store.list_approvals(limit=2, offset=1)
        assert len(page) == 2


class TestApprovalStoreDelete:
    def test_delete_existing(self, store: ApprovalStore) -> None:
        store.save(_sample_record())
        assert store.delete("appr_001") is True
        assert store.get("appr_001") is None

    def test_delete_missing(self, store: ApprovalStore) -> None:
        assert store.delete("nope") is False
