"""WorkspaceTransaction 单元测试（原 BackupManager 测试的迁移版本）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from excelmanus.database import Database
from excelmanus.file_registry import FileRegistry
from excelmanus.workspace import WorkspaceTransaction


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """创建临时工作区并放入测试文件。"""
    (tmp_path / "data").mkdir()
    src = tmp_path / "data" / "销售.xlsx"
    src.write_bytes(b"fake-excel-content")
    return tmp_path


@pytest.fixture
def tmp_db(workspace: Path) -> Database:
    """临时 SQLite 数据库。"""
    return Database(str(workspace / "test.db"))


@pytest.fixture
def registry(tmp_db: Database, workspace: Path) -> FileRegistry:
    """基于当前工作区的 FileRegistry（启用版本/暂存）。"""
    return FileRegistry(tmp_db, workspace, enable_versions=True)


@pytest.fixture
def tx(workspace: Path, registry: FileRegistry) -> WorkspaceTransaction:
    staging = workspace / "outputs" / "backups"
    return WorkspaceTransaction(
        workspace_root=workspace,
        staging_dir=staging,
        tx_id="test-tx",
        registry=registry,
    )


class TestStageForWrite:
    def test_first_touch_copies_file(self, tx: WorkspaceTransaction, workspace: Path):
        original = workspace / "data" / "销售.xlsx"
        staged_path = tx.stage_for_write(str(original))
        assert Path(staged_path).exists()
        assert Path(staged_path).read_bytes() == b"fake-excel-content"
        assert "outputs/backups/" in staged_path
        assert original.exists()

    def test_second_touch_returns_cached(self, tx: WorkspaceTransaction, workspace: Path):
        original = str(workspace / "data" / "销售.xlsx")
        first = tx.stage_for_write(original)
        second = tx.stage_for_write(original)
        assert first == second

    def test_nonexistent_file_returns_original_path(self, tx: WorkspaceTransaction, workspace: Path):
        """目标文件不存在时，返回原路径（不创建虚假映射）。"""
        new_file = str(workspace / "new_report.xlsx")
        result_path = tx.stage_for_write(new_file)
        assert "outputs/backups/" not in result_path
        assert result_path == str(Path(new_file).resolve())

    def test_outside_workspace_rejected(self, tx: WorkspaceTransaction):
        with pytest.raises(ValueError, match="工作区外"):
            tx.stage_for_write("/tmp/outside.xlsx")


class TestResolveRead:
    def test_returns_staged_if_exists(self, tx: WorkspaceTransaction, workspace: Path):
        original = str(workspace / "data" / "销售.xlsx")
        staged = tx.stage_for_write(original)
        assert tx.resolve_read(original) == staged

    def test_returns_original_if_not_staged(self, tx: WorkspaceTransaction, workspace: Path):
        original = str(workspace / "data" / "销售.xlsx")
        assert tx.resolve_read(original) == original


class TestCommitAll:
    def test_commit_copies_back(self, tx: WorkspaceTransaction, workspace: Path):
        original = workspace / "data" / "销售.xlsx"
        staged_path = tx.stage_for_write(str(original))
        Path(staged_path).write_bytes(b"modified-content")
        results = tx.commit_all()
        assert len(results) == 1
        assert original.read_bytes() == b"modified-content"

    def test_commit_new_file(self, tx: WorkspaceTransaction, workspace: Path):
        new_file = workspace / "new.xlsx"
        staged_path = tx.stage_for_write(str(new_file))
        Path(staged_path).parent.mkdir(parents=True, exist_ok=True)
        Path(staged_path).write_bytes(b"new-content")
        tx.commit_all()
        assert new_file.read_bytes() == b"new-content"


class TestListStaged:
    def test_empty_initially(self, tx: WorkspaceTransaction):
        assert tx.list_staged() == []

    def test_lists_after_stage(self, tx: WorkspaceTransaction, workspace: Path):
        tx.stage_for_write(str(workspace / "data" / "销售.xlsx"))
        staged = tx.list_staged()
        assert len(staged) == 1
        assert staged[0]["original"].endswith("销售.xlsx")


class TestScope:
    def test_excel_only_skips_txt(self, workspace: Path, registry: FileRegistry):
        txt = workspace / "notes.txt"
        txt.write_text("hello")
        staging = workspace / "outputs" / "backups"
        tx = WorkspaceTransaction(
            workspace_root=workspace,
            staging_dir=staging,
            tx_id="scope-test",
            scope="excel_only",
            registry=registry,
        )
        result = tx.stage_for_write(str(txt))
        assert result == str(txt.resolve())

    def test_excel_only_stages_xlsx(self, workspace: Path, registry: FileRegistry):
        staging = workspace / "outputs" / "backups"
        tx = WorkspaceTransaction(
            workspace_root=workspace,
            staging_dir=staging,
            tx_id="scope-test",
            scope="excel_only",
            registry=registry,
        )
        result = tx.stage_for_write(str(workspace / "data" / "销售.xlsx"))
        assert "outputs/backups/" in result


class TestRollback:
    def test_rollback_all_clears_mapping(self, tx: WorkspaceTransaction, workspace: Path):
        tx.stage_for_write(str(workspace / "data" / "销售.xlsx"))
        tx.rollback_all()
        assert tx.list_staged() == []


class TestPathRedirection:
    """测试路径重定向逻辑。"""

    def test_redirect_write(self, tx: WorkspaceTransaction, workspace: Path):
        original = str(workspace / "data" / "销售.xlsx")
        staged = tx.stage_for_write(original)
        assert staged != original
        assert "outputs/backups" in staged

    def test_redirect_read_after_write(self, tx: WorkspaceTransaction, workspace: Path):
        original = str(workspace / "data" / "销售.xlsx")
        staged = tx.stage_for_write(original)
        resolved = tx.resolve_read(original)
        assert resolved == staged
