"""审批与审计模块测试。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from excelmanus.approval import ApprovalManager


def test_policy_defaults_not_exposed_as_public_class_constants() -> None:
    assert hasattr(ApprovalManager, "_READ_ONLY_SAFE_TOOLS")
    assert hasattr(ApprovalManager, "_CONFIRM_TOOLS")
    assert hasattr(ApprovalManager, "_AUDIT_ONLY_TOOLS")
    assert hasattr(ApprovalManager, "_MUTATING_TOOLS")

    assert not hasattr(ApprovalManager, "READ_ONLY_SAFE_TOOLS")
    assert not hasattr(ApprovalManager, "CONFIRM_TOOLS")
    assert not hasattr(ApprovalManager, "AUDIT_ONLY_TOOLS")
    assert not hasattr(ApprovalManager, "HIGH_RISK_TOOLS")
    assert not hasattr(ApprovalManager, "MUTATING_TOOLS")


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
    assert manifest["version"] == 2
    assert manifest["approval"]["approval_id"] == approval_id
    assert manifest["execution"]["status"] == "success"

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
        assert tool_name == "copy_file"
        target.write_bytes(b"\x00NEW_BINARY")
        return '{"status":"success"}'

    _, record = manager.execute_and_audit(
        approval_id=approval_id,
        tool_name="copy_file",
        arguments={"source": "src.xlsx", "destination": "demo.xlsx"},
        tool_scope=["copy_file"],
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


def test_empty_file_hash_recorded_and_undo(tmp_path: Path) -> None:
    manager = ApprovalManager(str(tmp_path))
    target = tmp_path / "empty.txt"
    target.write_text("before", encoding="utf-8")
    approval_id = manager.new_approval_id()

    def execute(tool_name: str, arguments: dict, tool_scope: list[str]) -> str:
        assert tool_name == "write_text_file"
        target.write_text("", encoding="utf-8")
        return "ok"

    _, record = manager.execute_and_audit(
        approval_id=approval_id,
        tool_name="write_text_file",
        arguments={"file_path": "empty.txt", "content": ""},
        tool_scope=["write_text_file"],
        execute=execute,
        undoable=True,
        created_at_utc=manager.utc_now(),
    )

    assert record.changes
    expected_empty_hash = hashlib.sha256(b"").hexdigest()
    assert record.changes[0].after_hash == expected_empty_hash

    undo_msg = manager.undo(approval_id)
    assert "已回滚" in undo_msg
    assert target.read_text(encoding="utf-8") == "before"


def test_failed_execution_still_writes_manifest_and_supports_undo(tmp_path: Path) -> None:
    manager = ApprovalManager(str(tmp_path))
    target = tmp_path / "failed.txt"
    target.write_text("before", encoding="utf-8")
    approval_id = manager.new_approval_id()

    def execute(tool_name: str, arguments: dict, tool_scope: list[str]) -> str:
        assert tool_name == "write_text_file"
        target.write_text("after", encoding="utf-8")
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        manager.execute_and_audit(
            approval_id=approval_id,
            tool_name="write_text_file",
            arguments={"file_path": "failed.txt", "content": "after"},
            tool_scope=["write_text_file"],
            execute=execute,
            undoable=True,
            created_at_utc=manager.utc_now(),
        )

    manifest_path = tmp_path / "outputs" / "approvals" / approval_id / "manifest.json"
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["execution"]["status"] == "failed"
    assert manifest["execution"]["error_type"] == "RuntimeError"
    assert target.read_text(encoding="utf-8") == "after"

    undo_msg = manager.undo(approval_id)
    assert "已回滚" in undo_msg
    assert target.read_text(encoding="utf-8") == "before"


def test_undo_can_load_record_from_manifest_after_restart(tmp_path: Path) -> None:
    manager = ApprovalManager(str(tmp_path))
    target = tmp_path / "restart.txt"
    approval_id = manager.new_approval_id()

    def execute(tool_name: str, arguments: dict, tool_scope: list[str]) -> str:
        assert tool_name == "write_text_file"
        target.write_text("content", encoding="utf-8")
        return "ok"

    manager.execute_and_audit(
        approval_id=approval_id,
        tool_name="write_text_file",
        arguments={"file_path": "restart.txt", "content": "content"},
        tool_scope=["write_text_file"],
        execute=execute,
        undoable=True,
        created_at_utc=manager.utc_now(),
    )
    assert target.exists()

    # 模拟重启：使用全新 manager，从 manifest 重建记录。
    manager2 = ApprovalManager(str(tmp_path))
    msg = manager2.undo(approval_id)
    assert "已回滚" in msg
    assert not target.exists()


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

    with pytest.raises(ValueError, match="存在待确认操作"):
        manager.create_pending(
            tool_name="create_sheet",
            arguments={"file_path": "a.xlsx"},
            tool_scope=["create_sheet"],
        )


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


def test_unknown_tool_not_high_risk_by_default(tmp_path: Path) -> None:
    manager = ApprovalManager(str(tmp_path))
    assert manager.is_high_risk_tool("custom_tool") is False


def test_audit_only_tool_not_high_risk_but_mutating(tmp_path: Path) -> None:
    manager = ApprovalManager(str(tmp_path))
    assert manager.is_audit_only_tool("copy_file") is True
    assert manager.is_high_risk_tool("copy_file") is False
    assert manager.is_mutating_tool("copy_file") is True


def test_read_only_safe_tool_not_high_risk(tmp_path: Path) -> None:
    manager = ApprovalManager(str(tmp_path))
    assert manager.is_high_risk_tool("read_excel") is False


def test_non_whitelisted_mcp_high_risk_until_auto_approve(tmp_path: Path) -> None:
    manager = ApprovalManager(str(tmp_path))
    tool_name = "mcp_context7_query_docs"
    assert manager.is_high_risk_tool(tool_name) is True
    manager.register_mcp_auto_approve([tool_name])
    assert manager.is_high_risk_tool(tool_name) is False


def test_resolve_target_paths_covers_mutating_tools_with_path_rules(tmp_path: Path) -> None:
    manager = ApprovalManager(str(tmp_path))
    cases = [
        # Batch 1/2/3 精简：大部分专有工具已删除
        ("copy_file", {"source": "a.xlsx", "destination": "b.xlsx"}, ["b.xlsx"]),
        ("run_code", {"code": "print('hi')"}, []),
        ("run_shell", {"command": "ls"}, []),
    ]

    for tool_name, arguments, expected in cases:
        resolved = manager._resolve_target_paths(tool_name, arguments)
        relative = [str(path.relative_to(tmp_path)) for path in resolved]
        assert relative == expected
