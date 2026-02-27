"""FileVersionManager 单元测试。"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest

from excelmanus.file_versions import FileVersion, FileVersionManager


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    """创建临时工作区并写入测试文件。"""
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "report.xlsx").write_bytes(b"excel-content-v1")
    (ws / "data.csv").write_text("a,b,c\n1,2,3\n", encoding="utf-8")
    sub = ws / "subdir"
    sub.mkdir()
    (sub / "nested.txt").write_text("nested", encoding="utf-8")
    return ws


@pytest.fixture()
def fvm(workspace: Path) -> FileVersionManager:
    return FileVersionManager(workspace)


class TestCheckpoint:
    def test_basic_checkpoint(self, fvm: FileVersionManager, workspace: Path) -> None:
        ver = fvm.checkpoint("report.xlsx", reason="staging", ref_id="tx-1")
        assert ver is not None
        assert ver.file_path == "report.xlsx"
        assert ver.reason == "staging"
        assert ver.ref_id == "tx-1"
        assert ver.original_existed is True
        assert ver.content_hash != ""
        assert Path(ver.snapshot_path).exists()
        # 快照内容与原文件一致
        assert Path(ver.snapshot_path).read_bytes() == b"excel-content-v1"

    def test_checkpoint_dedup(self, fvm: FileVersionManager) -> None:
        v1 = fvm.checkpoint("report.xlsx", reason="staging")
        v2 = fvm.checkpoint("report.xlsx", reason="staging")
        assert v1 is not None
        assert v2 is None  # 内容未变，去重

    def test_checkpoint_after_change(self, fvm: FileVersionManager, workspace: Path) -> None:
        v1 = fvm.checkpoint("report.xlsx", reason="staging")
        assert v1 is not None
        # 修改文件
        (workspace / "report.xlsx").write_bytes(b"excel-content-v2")
        v2 = fvm.checkpoint("report.xlsx", reason="audit", ref_id="ap-1")
        assert v2 is not None
        assert v2.content_hash != v1.content_hash

    def test_checkpoint_nonexistent_file(self, fvm: FileVersionManager) -> None:
        ver = fvm.checkpoint("nonexistent.xlsx", reason="staging")
        assert ver is not None
        assert ver.original_existed is False
        assert ver.snapshot_path == ""
        assert ver.content_hash == ""

    def test_checkpoint_many(self, fvm: FileVersionManager) -> None:
        versions = fvm.checkpoint_many(
            ["report.xlsx", "data.csv", "nonexistent.txt"],
            reason="audit",
            ref_id="ap-2",
        )
        assert len(versions) == 3  # 包括 tombstone
        assert versions[0].file_path == "report.xlsx"
        assert versions[1].file_path == "data.csv"
        assert versions[2].original_existed is False

    def test_checkpoint_outside_workspace(self, fvm: FileVersionManager) -> None:
        with pytest.raises(ValueError, match="工作区外"):
            fvm.checkpoint("/etc/passwd", reason="staging")


class TestQuery:
    def test_get_original(self, fvm: FileVersionManager, workspace: Path) -> None:
        fvm.checkpoint("report.xlsx", reason="staging")
        (workspace / "report.xlsx").write_bytes(b"v2")
        fvm.checkpoint("report.xlsx", reason="audit")

        original = fvm.get_original("report.xlsx")
        assert original is not None
        assert original.reason == "staging"
        assert Path(original.snapshot_path).read_bytes() == b"excel-content-v1"

    def test_get_latest(self, fvm: FileVersionManager, workspace: Path) -> None:
        fvm.checkpoint("report.xlsx", reason="staging")
        (workspace / "report.xlsx").write_bytes(b"v2")
        fvm.checkpoint("report.xlsx", reason="audit", ref_id="ap-1")

        latest = fvm.get_latest("report.xlsx")
        assert latest is not None
        assert latest.reason == "audit"

    def test_list_versions(self, fvm: FileVersionManager, workspace: Path) -> None:
        fvm.checkpoint("report.xlsx", reason="staging")
        (workspace / "report.xlsx").write_bytes(b"v2")
        fvm.checkpoint("report.xlsx", reason="audit")
        (workspace / "report.xlsx").write_bytes(b"v3")
        fvm.checkpoint("report.xlsx", reason="audit")

        chain = fvm.list_versions("report.xlsx")
        assert len(chain) == 3
        assert chain[0].reason == "staging"
        assert chain[1].reason == "audit"
        assert chain[2].reason == "audit"

    def test_list_by_ref(self, fvm: FileVersionManager) -> None:
        fvm.checkpoint("report.xlsx", reason="audit", ref_id="ap-1")
        fvm.checkpoint("data.csv", reason="audit", ref_id="ap-1")

        versions = fvm.list_by_ref("ap-1")
        assert len(versions) == 2
        paths = {v.file_path for v in versions}
        assert paths == {"report.xlsx", "data.csv"}

    def test_list_all_tracked(self, fvm: FileVersionManager) -> None:
        fvm.checkpoint("report.xlsx", reason="staging")
        fvm.checkpoint("data.csv", reason="staging")
        tracked = fvm.list_all_tracked()
        assert set(tracked) == {"report.xlsx", "data.csv"}

    def test_query_nonexistent(self, fvm: FileVersionManager) -> None:
        assert fvm.get_original("report.xlsx") is None
        assert fvm.get_latest("report.xlsx") is None
        assert fvm.list_versions("report.xlsx") == []
        assert fvm.list_by_ref("no-such-ref") == []


class TestRestore:
    def test_restore_to_original(self, fvm: FileVersionManager, workspace: Path) -> None:
        fvm.checkpoint("report.xlsx", reason="staging")
        (workspace / "report.xlsx").write_bytes(b"modified")
        assert (workspace / "report.xlsx").read_bytes() == b"modified"

        ok = fvm.restore_to_original("report.xlsx")
        assert ok is True
        assert (workspace / "report.xlsx").read_bytes() == b"excel-content-v1"

    def test_restore_specific_version(self, fvm: FileVersionManager, workspace: Path) -> None:
        v1 = fvm.checkpoint("report.xlsx", reason="staging")
        (workspace / "report.xlsx").write_bytes(b"v2")
        v2 = fvm.checkpoint("report.xlsx", reason="audit")
        (workspace / "report.xlsx").write_bytes(b"v3")

        ok = fvm.restore("report.xlsx", v2.version_id)
        assert ok is True
        assert (workspace / "report.xlsx").read_bytes() == b"v2"

    def test_restore_tombstone(self, fvm: FileVersionManager, workspace: Path) -> None:
        # 文件不存在时创建 tombstone
        ver = fvm.checkpoint("new_file.txt", reason="staging")
        assert ver is not None
        # 然后创建文件
        (workspace / "new_file.txt").write_text("created", encoding="utf-8")
        # 恢复到 tombstone → 删除文件
        ok = fvm.restore("new_file.txt", ver.version_id)
        assert ok is True
        assert not (workspace / "new_file.txt").exists()

    def test_restore_invalidated(self, fvm: FileVersionManager, workspace: Path) -> None:
        fvm.checkpoint("report.xlsx", reason="staging")
        fvm.invalidate_undo({"report.xlsx"})
        ok = fvm.restore_to_original("report.xlsx")
        assert ok is False

    def test_restore_nonexistent_version(self, fvm: FileVersionManager) -> None:
        fvm.checkpoint("report.xlsx", reason="staging")
        ok = fvm.restore("report.xlsx", "no-such-version")
        assert ok is False

    def test_restore_no_chain(self, fvm: FileVersionManager) -> None:
        ok = fvm.restore_to_original("report.xlsx")
        assert ok is False


class TestStaging:
    def test_stage_for_write(self, fvm: FileVersionManager, workspace: Path) -> None:
        staged = fvm.stage_for_write("report.xlsx")
        assert staged != str(workspace / "report.xlsx")
        assert Path(staged).exists()
        assert Path(staged).read_bytes() == b"excel-content-v1"
        # 版本链中应有一个 staging 记录
        chain = fvm.list_versions("report.xlsx")
        assert len(chain) == 1
        assert chain[0].reason == "staging"

    def test_stage_idempotent(self, fvm: FileVersionManager) -> None:
        s1 = fvm.stage_for_write("report.xlsx")
        s2 = fvm.stage_for_write("report.xlsx")
        assert s1 == s2

    def test_stage_nonexistent_returns_original(self, fvm: FileVersionManager, workspace: Path) -> None:
        result = fvm.stage_for_write("nonexistent.xlsx")
        assert result == str(workspace / "nonexistent.xlsx")

    def test_stage_scope_excel_only(self, fvm: FileVersionManager, workspace: Path) -> None:
        # .txt 不在 excel_only scope 内
        result = fvm.stage_for_write("subdir/nested.txt", scope="excel_only")
        assert result == str(workspace / "subdir" / "nested.txt")
        # .xlsx 在 scope 内
        result = fvm.stage_for_write("report.xlsx", scope="excel_only")
        assert result != str(workspace / "report.xlsx")

    def test_commit_staged(self, fvm: FileVersionManager, workspace: Path) -> None:
        staged = fvm.stage_for_write("report.xlsx")
        # 修改 staged 副本
        Path(staged).write_bytes(b"modified-in-staging")
        result = fvm.commit_staged("report.xlsx")
        assert result is not None
        assert result["original"] == str(workspace / "report.xlsx")
        # 原文件应被更新
        assert (workspace / "report.xlsx").read_bytes() == b"modified-in-staging"
        # staging 条目应被清除
        assert fvm.has_staging("report.xlsx") is False

    def test_commit_all_staged(self, fvm: FileVersionManager, workspace: Path) -> None:
        fvm.stage_for_write("report.xlsx")
        fvm.stage_for_write("data.csv")
        results = fvm.commit_all_staged()
        assert len(results) == 2
        assert fvm.list_staged() == []

    def test_discard_staged(self, fvm: FileVersionManager, workspace: Path) -> None:
        staged = fvm.stage_for_write("report.xlsx")
        assert Path(staged).exists()
        ok = fvm.discard_staged("report.xlsx")
        assert ok is True
        assert not Path(staged).exists()
        assert fvm.has_staging("report.xlsx") is False

    def test_discard_all_staged(self, fvm: FileVersionManager) -> None:
        fvm.stage_for_write("report.xlsx")
        fvm.stage_for_write("data.csv")
        count = fvm.discard_all_staged()
        assert count == 2
        assert fvm.list_staged() == []

    def test_list_staged(self, fvm: FileVersionManager) -> None:
        fvm.stage_for_write("report.xlsx")
        staged = fvm.list_staged()
        assert len(staged) == 1
        assert "original" in staged[0]
        assert "backup" in staged[0]
        assert staged[0]["exists"] == "True"

    def test_staged_file_map(self, fvm: FileVersionManager, workspace: Path) -> None:
        fvm.stage_for_write("report.xlsx")
        fm = fvm.staged_file_map()
        assert str(workspace / "report.xlsx") in fm

    def test_prune_stale_staging(self, fvm: FileVersionManager) -> None:
        staged = fvm.stage_for_write("report.xlsx")
        # 手动删除 staged 文件
        Path(staged).unlink()
        count = fvm.prune_stale_staging()
        assert count == 1
        assert fvm.list_staged() == []


class TestCoW:
    def test_register_cow_mapping(self, fvm: FileVersionManager, workspace: Path) -> None:
        # 创建 outputs 目录和副本
        outputs = workspace / "outputs"
        outputs.mkdir(exist_ok=True)
        shutil.copy2(str(workspace / "report.xlsx"), str(outputs / "report_copy.xlsx"))

        fvm.register_cow_mapping("report.xlsx", "outputs/report_copy.xlsx")

        # 应有 staging 条目
        assert fvm.has_staging("report.xlsx")
        # 应有 cow 版本记录
        chain = fvm.list_versions("report.xlsx")
        assert len(chain) == 1
        assert chain[0].reason == "cow"

    def test_lookup_cow_redirect(self, fvm: FileVersionManager, workspace: Path) -> None:
        outputs = workspace / "outputs"
        outputs.mkdir(exist_ok=True)
        shutil.copy2(str(workspace / "report.xlsx"), str(outputs / "report_copy.xlsx"))

        fvm.register_cow_mapping("report.xlsx", "outputs/report_copy.xlsx")
        redirect = fvm.lookup_cow_redirect("report.xlsx")
        assert redirect is not None
        assert "report_copy.xlsx" in redirect

    def test_cow_no_duplicate(self, fvm: FileVersionManager, workspace: Path) -> None:
        outputs = workspace / "outputs"
        outputs.mkdir(exist_ok=True)
        shutil.copy2(str(workspace / "report.xlsx"), str(outputs / "report_copy.xlsx"))

        fvm.register_cow_mapping("report.xlsx", "outputs/report_copy.xlsx")
        fvm.register_cow_mapping("report.xlsx", "outputs/report_copy2.xlsx")
        # 第二次注册应被忽略
        chain = fvm.list_versions("report.xlsx")
        assert len(chain) == 1


class TestInvalidateUndo:
    def test_invalidate(self, fvm: FileVersionManager) -> None:
        fvm.checkpoint("report.xlsx", reason="staging")
        count = fvm.invalidate_undo({"report.xlsx"})
        assert count == 1
        chain = fvm.list_versions("report.xlsx")
        assert all(v.invalidated for v in chain)

    def test_invalidate_prevents_restore(self, fvm: FileVersionManager, workspace: Path) -> None:
        fvm.checkpoint("report.xlsx", reason="staging")
        fvm.invalidate_undo({"report.xlsx"})
        (workspace / "report.xlsx").write_bytes(b"changed")
        ok = fvm.restore_to_original("report.xlsx")
        assert ok is False


class TestGC:
    def test_gc_removes_old_versions(self, fvm: FileVersionManager, workspace: Path) -> None:
        fvm.checkpoint("report.xlsx", reason="staging")
        (workspace / "report.xlsx").write_bytes(b"v2")
        fvm.checkpoint("report.xlsx", reason="audit")
        (workspace / "report.xlsx").write_bytes(b"v3")
        fvm.checkpoint("report.xlsx", reason="audit")

        # 所有版本都是刚创建的，gc 不应删除
        removed = fvm.gc(max_age_seconds=3600)
        assert removed == 0
        assert len(fvm.list_versions("report.xlsx")) == 3

        # 将中间版本的时间戳设为很久以前
        chain = fvm.list_versions("report.xlsx")
        object.__setattr__(chain[1], "created_at", 0.0)

        removed = fvm.gc(max_age_seconds=1)
        assert removed == 1
        assert len(fvm.list_versions("report.xlsx")) == 2
