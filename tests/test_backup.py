"""Backup 沙盒模式单元测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from excelmanus.backup import BackupManager


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """创建临时工作区并放入测试文件。"""
    (tmp_path / "data").mkdir()
    src = tmp_path / "data" / "销售.xlsx"
    src.write_bytes(b"fake-excel-content")
    return tmp_path


@pytest.fixture
def manager(workspace: Path) -> BackupManager:
    return BackupManager(workspace_root=str(workspace))


class TestEnsureBackup:
    def test_first_touch_copies_file(self, manager: BackupManager, workspace: Path):
        original = workspace / "data" / "销售.xlsx"
        backup_path = manager.ensure_backup(str(original))
        assert Path(backup_path).exists()
        assert Path(backup_path).read_bytes() == b"fake-excel-content"
        assert "outputs/backups/" in backup_path
        # 原始文件仍存在
        assert original.exists()

    def test_second_touch_returns_cached(self, manager: BackupManager, workspace: Path):
        original = str(workspace / "data" / "销售.xlsx")
        first = manager.ensure_backup(original)
        second = manager.ensure_backup(original)
        assert first == second

    def test_nonexistent_file_returns_backup_path(self, manager: BackupManager, workspace: Path):
        """目标文件不存在时（新建场景），返回备份路径但不复制。"""
        new_file = str(workspace / "new_report.xlsx")
        backup_path = manager.ensure_backup(new_file)
        assert "outputs/backups/" in backup_path
        assert not Path(backup_path).exists()

    def test_outside_workspace_rejected(self, manager: BackupManager):
        with pytest.raises(ValueError, match="工作区外"):
            manager.ensure_backup("/tmp/outside.xlsx")


class TestResolvePath:
    def test_returns_backup_if_exists(self, manager: BackupManager, workspace: Path):
        original = str(workspace / "data" / "销售.xlsx")
        backup = manager.ensure_backup(original)
        assert manager.resolve_path(original) == backup

    def test_returns_original_if_no_backup(self, manager: BackupManager, workspace: Path):
        original = str(workspace / "data" / "销售.xlsx")
        assert manager.resolve_path(original) == original


class TestApplyAll:
    def test_apply_copies_back(self, manager: BackupManager, workspace: Path):
        original = workspace / "data" / "销售.xlsx"
        backup_path = manager.ensure_backup(str(original))
        # 修改备份
        Path(backup_path).write_bytes(b"modified-content")
        results = manager.apply_all()
        assert len(results) == 1
        assert original.read_bytes() == b"modified-content"

    def test_apply_new_file(self, manager: BackupManager, workspace: Path):
        new_file = workspace / "new.xlsx"
        backup_path = manager.ensure_backup(str(new_file))
        Path(backup_path).parent.mkdir(parents=True, exist_ok=True)
        Path(backup_path).write_bytes(b"new-content")
        manager.apply_all()
        assert new_file.read_bytes() == b"new-content"


class TestListBackups:
    def test_empty_initially(self, manager: BackupManager):
        assert manager.list_backups() == []

    def test_lists_after_ensure(self, manager: BackupManager, workspace: Path):
        manager.ensure_backup(str(workspace / "data" / "销售.xlsx"))
        backups = manager.list_backups()
        assert len(backups) == 1
        assert backups[0]["original"].endswith("销售.xlsx")


class TestScope:
    def test_excel_only_skips_txt(self, workspace: Path):
        txt = workspace / "notes.txt"
        txt.write_text("hello")
        mgr = BackupManager(workspace_root=str(workspace), scope="excel_only")
        result = mgr.ensure_backup(str(txt))
        assert result == str(txt.resolve())

    def test_excel_only_backs_up_xlsx(self, workspace: Path):
        mgr = BackupManager(workspace_root=str(workspace), scope="excel_only")
        result = mgr.ensure_backup(str(workspace / "data" / "销售.xlsx"))
        assert "outputs/backups/" in result


class TestDiscard:
    def test_discard_clears_mapping(self, manager: BackupManager, workspace: Path):
        manager.ensure_backup(str(workspace / "data" / "销售.xlsx"))
        manager.discard_all()
        assert manager.list_backups() == []


class TestBackupPathRedirection:
    """测试路径重定向逻辑。"""

    def test_redirect_write_tool(self, manager: BackupManager, workspace: Path):
        original = str(workspace / "data" / "销售.xlsx")
        backup = manager.ensure_backup(original)
        assert backup != original
        assert "outputs/backups" in backup

    def test_redirect_read_tool_after_write(self, manager: BackupManager, workspace: Path):
        original = str(workspace / "data" / "销售.xlsx")
        backup = manager.ensure_backup(original)
        resolved = manager.resolve_path(original)
        assert resolved == backup
