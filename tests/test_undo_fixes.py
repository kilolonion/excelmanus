"""回归测试：撤销功能修复 B1/B2/B4。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from excelmanus.approval import ApprovalManager


def _execute_write(tmp_path: Path, manager: ApprovalManager, filename: str, *,
                   session_turn: int | None = None) -> str:
    """辅助方法：通过 execute_and_audit 创建文件并返回 approval_id。"""
    target = tmp_path / filename
    approval_id = manager.new_approval_id()

    def execute(tool_name: str, arguments: dict, tool_scope: list) -> str:
        target.write_text(f"content of {filename}", encoding="utf-8")
        return "ok"

    manager.execute_and_audit(
        approval_id=approval_id,
        tool_name="write_text_file",
        arguments={"file_path": filename, "content": f"content of {filename}"},
        tool_scope=["write_text_file"],
        execute=execute,
        undoable=True,
        created_at_utc=manager.utc_now(),
        session_turn=session_turn,
    )
    return approval_id


# ── B2: undo() 成功后 undoable=False，防止 double-undo ──


class TestUndoSetsUndoableFalse:
    """B2: undo 成功后 record.undoable 应为 False。"""

    def test_undo_marks_undoable_false(self, tmp_path: Path) -> None:
        manager = ApprovalManager(str(tmp_path))
        target = tmp_path / "file.txt"
        target.write_text("original", encoding="utf-8")
        aid = _execute_write(tmp_path, manager, "file.txt")

        record = manager.get_applied(aid)
        assert record is not None
        assert record.undoable is True

        msg = manager.undo(aid)
        assert "已回滚" in msg

        record = manager.get_applied(aid)
        assert record is not None
        assert record.undoable is False

    def test_double_undo_rejected(self, tmp_path: Path) -> None:
        manager = ApprovalManager(str(tmp_path))
        target = tmp_path / "file.txt"
        target.write_text("original", encoding="utf-8")
        aid = _execute_write(tmp_path, manager, "file.txt")

        msg1 = manager.undo(aid)
        assert "已回滚" in msg1

        msg2 = manager.undo(aid)
        assert "不支持自动回滚" in msg2

    def test_undo_persists_undoable_false_to_manifest(self, tmp_path: Path) -> None:
        manager = ApprovalManager(str(tmp_path))
        target = tmp_path / "file.txt"
        target.write_text("original", encoding="utf-8")
        aid = _execute_write(tmp_path, manager, "file.txt")

        manager.undo(aid)

        record = manager.get_applied(aid)
        assert record is not None
        manifest_path = tmp_path / record.manifest_file
        assert manifest_path.exists()
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert raw["approval"]["undoable"] is False

    def test_double_undo_rejected_after_restart(self, tmp_path: Path) -> None:
        """重启后也应拒绝 double-undo（因为 manifest 已更新）。"""
        manager1 = ApprovalManager(str(tmp_path))
        target = tmp_path / "file.txt"
        target.write_text("original", encoding="utf-8")
        aid = _execute_write(tmp_path, manager1, "file.txt")

        msg1 = manager1.undo(aid)
        assert "已回滚" in msg1

        # 模拟重启
        manager2 = ApprovalManager(str(tmp_path))
        msg2 = manager2.undo(aid)
        assert "不支持自动回滚" in msg2


# ── B1: session_turn 字段 + rollback 范围过滤 ──


class TestSessionTurnField:
    """B1: AppliedApprovalRecord 应记录 session_turn。"""

    def test_session_turn_stored_in_record(self, tmp_path: Path) -> None:
        manager = ApprovalManager(str(tmp_path))
        aid = _execute_write(tmp_path, manager, "a.txt", session_turn=3)
        record = manager.get_applied(aid)
        assert record is not None
        assert record.session_turn == 3

    def test_session_turn_none_by_default(self, tmp_path: Path) -> None:
        manager = ApprovalManager(str(tmp_path))
        aid = _execute_write(tmp_path, manager, "a.txt")
        record = manager.get_applied(aid)
        assert record is not None
        assert record.session_turn is None

    def test_session_turn_persisted_to_manifest(self, tmp_path: Path) -> None:
        manager = ApprovalManager(str(tmp_path))
        aid = _execute_write(tmp_path, manager, "a.txt", session_turn=5)
        record = manager.get_applied(aid)
        assert record is not None
        manifest_path = tmp_path / record.manifest_file
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert raw["approval"]["session_turn"] == 5

    def test_session_turn_loaded_from_manifest_after_restart(self, tmp_path: Path) -> None:
        manager1 = ApprovalManager(str(tmp_path))
        aid = _execute_write(tmp_path, manager1, "a.txt", session_turn=7)

        manager2 = ApprovalManager(str(tmp_path))
        record = manager2.get_applied(aid)
        assert record is not None
        assert record.session_turn == 7

    def test_session_turn_in_to_dict(self, tmp_path: Path) -> None:
        manager = ApprovalManager(str(tmp_path))
        aid = _execute_write(tmp_path, manager, "a.txt", session_turn=2)
        record = manager.get_applied(aid)
        assert record is not None
        d = record.to_dict()
        assert d["session_turn"] == 2


# ── B4: mark_non_undoable_for_paths 同步 manifest ──


class TestMarkNonUndoablePersistsManifest:
    """B4: mark_non_undoable_for_paths 应同步更新 manifest.json。"""

    def test_manifest_updated_after_mark(self, tmp_path: Path) -> None:
        manager = ApprovalManager(str(tmp_path))
        aid = _execute_write(tmp_path, manager, "target.txt")
        record = manager.get_applied(aid)
        assert record is not None

        manifest_path = tmp_path / record.manifest_file
        raw_before = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert raw_before["approval"]["undoable"] is True

        count = manager.mark_non_undoable_for_paths({"target.txt"})
        assert count == 1

        raw_after = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert raw_after["approval"]["undoable"] is False

    def test_mark_survives_restart(self, tmp_path: Path) -> None:
        manager1 = ApprovalManager(str(tmp_path))
        aid = _execute_write(tmp_path, manager1, "target.txt")
        manager1.mark_non_undoable_for_paths({"target.txt"})

        # 模拟重启
        manager2 = ApprovalManager(str(tmp_path))
        record = manager2.get_applied(aid)
        assert record is not None
        assert record.undoable is False

    def test_unrelated_paths_not_affected(self, tmp_path: Path) -> None:
        manager = ApprovalManager(str(tmp_path))
        aid = _execute_write(tmp_path, manager, "target.txt")
        count = manager.mark_non_undoable_for_paths({"other.txt"})
        assert count == 0
        record = manager.get_applied(aid)
        assert record is not None
        assert record.undoable is True
