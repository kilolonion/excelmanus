"""审批与审计模块测试。"""

from __future__ import annotations

import json
from pathlib import Path

from excelmanus.approval import ApprovalManager


def test_text_file_audit_and_undo(tmp_path: Path) -> None:
    manager = ApprovalManager(str(tmp_path))
    target = tmp_path / "demo.txt"
    target.write_text("old\n", encoding="utf-8")

    approval_id = manager.new_approval_id()

    def execute(tool_name: str, arguments: dict, tool_scope: list[str]) -> str:
        assert tool_name == "write_text_file"
        target.write_text("new\n", encoding="utf-8")
        return '{"status":"success"}'

    _, record = manager.execute_and_audit(
        approval_id=approval_id,
        tool_name="write_text_file",
        arguments={"file_path": "demo.txt", "content": "new\n"},
        tool_scope=["write_text_file"],
        execute=execute,
        undoable=True,
        created_at_utc=manager.utc_now(),
    )

    manifest_path = tmp_path / record.manifest_file
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["approval_id"] == approval_id
    applied = manager.get_applied(approval_id)
    assert applied is not None
    assert applied.approval_id == approval_id
    assert target.read_text(encoding="utf-8") == "new\n"

    undo_msg = manager.undo(approval_id)
    assert "已回滚" in undo_msg
    assert target.read_text(encoding="utf-8") == "old\n"


def test_binary_snapshot_and_undo(tmp_path: Path) -> None:
    manager = ApprovalManager(str(tmp_path))
    target = tmp_path / "demo.xlsx"
    target.write_bytes(b"\x00OLD_BINARY")

    approval_id = manager.new_approval_id()

    def execute(tool_name: str, arguments: dict, tool_scope: list[str]) -> str:
        assert tool_name == "write_excel"
        target.write_bytes(b"\x00NEW_BINARY")
        return '{"status":"success"}'

    _, record = manager.execute_and_audit(
        approval_id=approval_id,
        tool_name="write_excel",
        arguments={"file_path": "demo.xlsx", "data": []},
        tool_scope=["write_excel"],
        execute=execute,
        undoable=True,
        created_at_utc=manager.utc_now(),
    )

    assert len(record.changes) == 1
    assert record.changes[0].is_binary is True
    assert record.binary_snapshots
    assert target.read_bytes() == b"\x00NEW_BINARY"

    undo_msg = manager.undo(approval_id)
    assert "已回滚" in undo_msg
    assert target.read_bytes() == b"\x00OLD_BINARY"


def test_non_undoable_record_returns_message(tmp_path: Path) -> None:
    manager = ApprovalManager(str(tmp_path))
    approval_id = manager.new_approval_id()

    def execute(tool_name: str, arguments: dict, tool_scope: list[str]) -> str:
        return '{"status":"success"}'

    manager.execute_and_audit(
        approval_id=approval_id,
        tool_name="run_code",
        arguments={"code": "print('hello')"},
        tool_scope=["run_code"],
        execute=execute,
        undoable=False,
        created_at_utc=manager.utc_now(),
    )

    msg = manager.undo(approval_id)
    assert "不支持自动回滚" in msg


def test_pending_queue_single_item(tmp_path: Path) -> None:
    manager = ApprovalManager(str(tmp_path))
    first = manager.create_pending(
        tool_name="write_text_file",
        arguments={"file_path": "a.py", "content": "x"},
        tool_scope=["write_text_file"],
    )
    assert manager.has_pending() is True
    assert manager.pending is not None
    assert manager.pending.approval_id == first.approval_id

    try:
        manager.create_pending(
            tool_name="write_excel",
            arguments={"file_path": "a.xlsx", "data": []},
            tool_scope=["write_excel"],
        )
        raise AssertionError("应当抛出 ValueError")
    except ValueError as exc:
        assert "存在待确认操作" in str(exc)


def test_reject_pending_clears_queue(tmp_path: Path) -> None:
    manager = ApprovalManager(str(tmp_path))
    pending = manager.create_pending(
        tool_name="write_text_file",
        arguments={"file_path": "a.py", "content": "x"},
        tool_scope=["write_text_file"],
    )

    msg = manager.reject_pending(pending.approval_id)
    assert "已拒绝" in msg
    assert manager.has_pending() is False
