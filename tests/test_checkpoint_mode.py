"""轮次 checkpoint 模式的单元测试。"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from excelmanus.file_versions import FileVersionManager, TurnCheckpoint


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    """创建临时工作区并写入测试文件。"""
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "data.xlsx").write_bytes(b"original-content-xlsx")
    (ws / "report.xlsx").write_bytes(b"original-content-report")
    return ws


@pytest.fixture()
def fvm(workspace: Path) -> FileVersionManager:
    return FileVersionManager(workspace)


# ── create_turn_checkpoint ──────────────────────────────


class TestCreateTurnCheckpoint:
    def test_basic_checkpoint(self, fvm: FileVersionManager, workspace: Path):
        """基本快照：文件被修改后创建 checkpoint。"""
        cp = fvm.create_turn_checkpoint(
            turn_number=1,
            dirty_files=["data.xlsx"],
            tool_names=["write_cells"],
        )
        assert cp is not None
        assert cp.turn_number == 1
        assert len(cp.version_ids) == 1
        assert cp.files_modified == [fvm._to_rel((workspace / "data.xlsx").resolve())]
        assert cp.tool_names == ["write_cells"]

    def test_dedup_unchanged_file(self, fvm: FileVersionManager, workspace: Path):
        """去重：文件内容未变时不创建新版本。"""
        cp1 = fvm.create_turn_checkpoint(1, ["data.xlsx"])
        assert cp1 is not None

        # 第二次内容相同 → 应该返回 None
        cp2 = fvm.create_turn_checkpoint(2, ["data.xlsx"])
        assert cp2 is None

    def test_dedup_changed_file(self, fvm: FileVersionManager, workspace: Path):
        """文件内容变化后创建新版本。"""
        cp1 = fvm.create_turn_checkpoint(1, ["data.xlsx"])
        assert cp1 is not None

        # 修改文件
        (workspace / "data.xlsx").write_bytes(b"modified-content")

        cp2 = fvm.create_turn_checkpoint(2, ["data.xlsx"])
        assert cp2 is not None
        assert cp2.turn_number == 2

    def test_multiple_files(self, fvm: FileVersionManager, workspace: Path):
        """多文件快照。"""
        cp = fvm.create_turn_checkpoint(
            turn_number=1,
            dirty_files=["data.xlsx", "report.xlsx"],
            tool_names=["run_code"],
        )
        assert cp is not None
        assert len(cp.version_ids) == 2
        assert len(cp.files_modified) == 2

    def test_nonexistent_file_tombstone(self, fvm: FileVersionManager):
        """不存在的文件创建 tombstone。"""
        cp = fvm.create_turn_checkpoint(1, ["nonexistent.xlsx"])
        assert cp is not None
        assert len(cp.version_ids) == 1

    def test_max_checkpoints_eviction(self, fvm: FileVersionManager, workspace: Path):
        """超出上限时淘汰最早的 checkpoint。"""
        fvm._max_turn_checkpoints = 3
        for i in range(1, 6):
            (workspace / "data.xlsx").write_bytes(f"content-{i}".encode())
            fvm.create_turn_checkpoint(i, ["data.xlsx"])

        cps = fvm.list_turn_checkpoints()
        assert len(cps) == 3
        assert cps[0].turn_number == 3  # 1 和 2 被淘汰

    def test_empty_dirty_files(self, fvm: FileVersionManager):
        """空 dirty_files 列表返回 None。"""
        cp = fvm.create_turn_checkpoint(1, [])
        assert cp is None


# ── list_turn_checkpoints ──────────────────────────────


class TestListTurnCheckpoints:
    def test_empty(self, fvm: FileVersionManager):
        assert fvm.list_turn_checkpoints() == []

    def test_returns_copy(self, fvm: FileVersionManager, workspace: Path):
        """返回的是副本，不影响内部状态。"""
        fvm.create_turn_checkpoint(1, ["data.xlsx"])
        cps = fvm.list_turn_checkpoints()
        cps.clear()
        assert len(fvm.list_turn_checkpoints()) == 1


# ── rollback_to_turn ──────────────────────────────────


class TestRollbackToTurn:
    def test_rollback_restores_file(self, fvm: FileVersionManager, workspace: Path):
        """回退恢复文件到指定轮次之前的状态。

        需先有 staging 基线（模拟 backup 模式在首次写入前的快照）。
        """
        data_file = workspace / "data.xlsx"
        original = data_file.read_bytes()

        # 基线快照：模拟 backup 模式的 stage_for_write
        fvm.checkpoint("data.xlsx", reason="staging")

        # Turn 1: 修改文件
        data_file.write_bytes(b"turn1-content")
        fvm.create_turn_checkpoint(1, ["data.xlsx"])

        # Turn 2: 再次修改
        data_file.write_bytes(b"turn2-content")
        fvm.create_turn_checkpoint(2, ["data.xlsx"])

        # 回退到 turn 1（即恢复到 turn 1 之前 = 原始状态）
        restored = fvm.rollback_to_turn(1)
        assert len(restored) >= 1
        assert data_file.read_bytes() == original

    def test_rollback_partial(self, fvm: FileVersionManager, workspace: Path):
        """回退到 turn 2 只影响 turn 2 及之后的变更。"""
        data_file = workspace / "data.xlsx"

        # Turn 1
        data_file.write_bytes(b"turn1-content")
        fvm.create_turn_checkpoint(1, ["data.xlsx"])
        after_turn1 = data_file.read_bytes()

        # Turn 2
        data_file.write_bytes(b"turn2-content")
        fvm.create_turn_checkpoint(2, ["data.xlsx"])

        # 回退到 turn 2（恢复到 turn 2 之前 = turn 1 之后）
        fvm.rollback_to_turn(2)
        assert data_file.read_bytes() == after_turn1

    def test_rollback_removes_checkpoints(self, fvm: FileVersionManager, workspace: Path):
        """回退后移除被回退的 checkpoint。"""
        for i in range(1, 4):
            (workspace / "data.xlsx").write_bytes(f"turn{i}".encode())
            fvm.create_turn_checkpoint(i, ["data.xlsx"])

        assert len(fvm.list_turn_checkpoints()) == 3
        fvm.rollback_to_turn(2)
        assert len(fvm.list_turn_checkpoints()) == 1
        assert fvm.list_turn_checkpoints()[0].turn_number == 1

    def test_rollback_nonexistent_turn(self, fvm: FileVersionManager, workspace: Path):
        """回退到不存在的轮次返回空列表。"""
        fvm.create_turn_checkpoint(1, ["data.xlsx"])
        restored = fvm.rollback_to_turn(999)
        assert restored == []

    def test_rollback_multiple_files(self, fvm: FileVersionManager, workspace: Path):
        """多文件回退。"""
        data_file = workspace / "data.xlsx"
        report_file = workspace / "report.xlsx"
        orig_data = data_file.read_bytes()
        orig_report = report_file.read_bytes()

        # 基线快照
        fvm.checkpoint("data.xlsx", reason="staging")
        fvm.checkpoint("report.xlsx", reason="staging")

        # Turn 1: 修改两个文件
        data_file.write_bytes(b"data-modified")
        report_file.write_bytes(b"report-modified")
        fvm.create_turn_checkpoint(1, ["data.xlsx", "report.xlsx"])

        fvm.rollback_to_turn(1)
        assert data_file.read_bytes() == orig_data
        assert report_file.read_bytes() == orig_report

    def test_rollback_without_baseline_uses_first_checkpoint(
        self, fvm: FileVersionManager, workspace: Path,
    ):
        """无基线时回退到第一个 checkpoint 的状态（最佳努力）。"""
        data_file = workspace / "data.xlsx"

        # 无 staging 基线，直接修改并 checkpoint
        data_file.write_bytes(b"turn1-content")
        fvm.create_turn_checkpoint(1, ["data.xlsx"])

        data_file.write_bytes(b"turn2-content")
        fvm.create_turn_checkpoint(2, ["data.xlsx"])

        # 回退到 turn 2 → 恢复到 turn 1 的状态
        fvm.rollback_to_turn(2)
        assert data_file.read_bytes() == b"turn1-content"


# ── TurnCheckpoint dataclass ──────────────────────────


class TestTurnCheckpointDataclass:
    def test_fields(self):
        cp = TurnCheckpoint(
            turn_number=5,
            created_at=time.time(),
            version_ids=["abc", "def"],
            files_modified=["a.xlsx", "b.xlsx"],
            tool_names=["write_cells", "run_code"],
        )
        assert cp.turn_number == 5
        assert len(cp.version_ids) == 2
        assert len(cp.tool_names) == 2
