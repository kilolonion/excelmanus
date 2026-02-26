"""Tests for /undo UI enhancements: list_applied, command handler, API endpoints."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from excelmanus.approval import ApprovalManager


# ── ApprovalManager.list_applied ─────────────────────────


def _execute_write(tmp_path: Path, manager: ApprovalManager, filename: str) -> str:
    """Helper: create a file via execute_and_audit and return the approval_id."""
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
    )
    return approval_id


def test_list_applied_empty(tmp_path: Path) -> None:
    manager = ApprovalManager(str(tmp_path))
    result = manager.list_applied()
    assert result == []


def test_list_applied_returns_records_newest_first(tmp_path: Path) -> None:
    manager = ApprovalManager(str(tmp_path))
    id1 = _execute_write(tmp_path, manager, "a.txt")
    id2 = _execute_write(tmp_path, manager, "b.txt")

    records = manager.list_applied()
    assert len(records) >= 2
    ids = [r.approval_id for r in records]
    # Most recent first
    assert ids.index(id2) < ids.index(id1)


def test_list_applied_undoable_only(tmp_path: Path) -> None:
    manager = ApprovalManager(str(tmp_path))
    _execute_write(tmp_path, manager, "undoable.txt")

    # Create a non-undoable record
    non_undo_id = manager.new_approval_id()

    def execute_noop(tool_name: str, arguments: dict, tool_scope: list) -> str:
        return "ok"

    manager.execute_and_audit(
        approval_id=non_undo_id,
        tool_name="run_code",
        arguments={"code": "print('hello')"},
        tool_scope=["run_code"],
        execute=execute_noop,
        undoable=False,
        created_at_utc=manager.utc_now(),
    )

    all_records = manager.list_applied()
    undoable_records = manager.list_applied(undoable_only=True)

    assert len(all_records) >= 2
    assert len(undoable_records) >= 1
    assert all(r.undoable for r in undoable_records)


def test_list_applied_respects_limit(tmp_path: Path) -> None:
    manager = ApprovalManager(str(tmp_path))
    for i in range(5):
        _execute_write(tmp_path, manager, f"file_{i}.txt")

    records = manager.list_applied(limit=3)
    assert len(records) == 3


def test_list_applied_survives_restart(tmp_path: Path) -> None:
    """重启后 list_applied 应从文件系统找到记录。"""
    manager1 = ApprovalManager(str(tmp_path))
    aid = _execute_write(tmp_path, manager1, "persist.txt")

    # 模拟重启：使用新的 manager 实例
    manager2 = ApprovalManager(str(tmp_path))
    records = manager2.list_applied()
    ids = [r.approval_id for r in records]
    assert aid in ids


# ── CommandHandler /undo enhancements ────────────────────


def test_undo_command_no_args_shows_list(tmp_path: Path) -> None:
    """_handle_undo_command with no args should return a formatted list."""
    from unittest.mock import MagicMock

    from excelmanus.engine_core.command_handler import CommandHandler

    manager = ApprovalManager(str(tmp_path))
    _execute_write(tmp_path, manager, "test.txt")

    engine = MagicMock()
    engine._approval = manager
    engine.approval = manager

    handler = CommandHandler(engine)
    result = handler._handle_undo_command(["/undo"])
    assert "操作历史" in result
    assert "write_text_file" in result


def test_undo_command_list_arg(tmp_path: Path) -> None:
    from unittest.mock import MagicMock

    from excelmanus.engine_core.command_handler import CommandHandler

    manager = ApprovalManager(str(tmp_path))
    _execute_write(tmp_path, manager, "test.txt")

    engine = MagicMock()
    engine._approval = manager
    engine.approval = manager

    handler = CommandHandler(engine)
    result = handler._handle_undo_command(["/undo", "list"])
    assert "操作历史" in result


def test_undo_command_with_id_performs_undo(tmp_path: Path) -> None:
    from unittest.mock import MagicMock

    from excelmanus.engine_core.command_handler import CommandHandler

    manager = ApprovalManager(str(tmp_path))
    aid = _execute_write(tmp_path, manager, "undo_me.txt")

    engine = MagicMock()
    engine._approval = manager
    engine.approval = manager

    handler = CommandHandler(engine)
    result = handler._handle_undo_command(["/undo", aid])
    assert "已回滚" in result


def test_undo_command_empty_history(tmp_path: Path) -> None:
    from unittest.mock import MagicMock

    from excelmanus.engine_core.command_handler import CommandHandler

    manager = ApprovalManager(str(tmp_path))
    engine = MagicMock()
    engine._approval = manager
    engine.approval = manager

    handler = CommandHandler(engine)
    result = handler._handle_undo_command(["/undo"])
    assert "没有已执行的操作记录" in result


# ── Events: approval_undoable field ──────────────────────


def test_approval_undoable_field_in_event() -> None:
    from excelmanus.events import EventType, ToolCallEvent

    event = ToolCallEvent(
        event_type=EventType.APPROVAL_RESOLVED,
        approval_id="test_id",
        approval_tool_name="write_text_file",
        success=True,
        approval_undoable=True,
    )
    d = event.to_dict()
    assert d["approval_undoable"] is True

    event2 = ToolCallEvent(
        event_type=EventType.APPROVAL_RESOLVED,
        approval_id="test_id2",
        success=False,
    )
    assert event2.approval_undoable is False
