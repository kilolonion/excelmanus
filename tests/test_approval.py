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


def test_unknown_tool_not_high_risk_by_default(tmp_path: Path) -> None:
    manager = ApprovalManager(str(tmp_path))
    assert manager.is_high_risk_tool("custom_tool") is False


def test_audit_only_tool_not_high_risk_but_mutating(tmp_path: Path) -> None:
    manager = ApprovalManager(str(tmp_path))
    assert manager.is_audit_only_tool("create_chart") is True
    assert manager.is_high_risk_tool("create_chart") is False
    assert manager.is_mutating_tool("create_chart") is True


def test_read_only_safe_tool_not_high_risk(tmp_path: Path) -> None:
    manager = ApprovalManager(str(tmp_path))
    assert manager.is_high_risk_tool("read_excel") is False


def test_non_whitelisted_mcp_high_risk_until_auto_approve(tmp_path: Path) -> None:
    manager = ApprovalManager(str(tmp_path))
    tool_name = "mcp_context7_query_docs"
    assert manager.is_high_risk_tool(tool_name) is True
    manager.register_mcp_auto_approve([tool_name])
    assert manager.is_high_risk_tool(tool_name) is False


def test_resolve_target_paths_covers_new_mutating_tools(tmp_path: Path) -> None:
    manager = ApprovalManager(str(tmp_path))
    cases = [
        ("create_excel_chart", {"file_path": "book.xlsx"}, ["book.xlsx"]),
        ("write_cells", {"file_path": "book.xlsx"}, ["book.xlsx"]),
        ("insert_rows", {"file_path": "book.xlsx"}, ["book.xlsx"]),
        ("insert_columns", {"file_path": "book.xlsx"}, ["book.xlsx"]),
        ("apply_threshold_icon_format", {"file_path": "book.xlsx"}, ["book.xlsx"]),
        ("style_card_blocks", {"file_path": "book.xlsx"}, ["book.xlsx"]),
        ("scale_range_unit", {"file_path": "book.xlsx"}, ["book.xlsx"]),
        ("apply_dashboard_dark_theme", {"file_path": "book.xlsx"}, ["book.xlsx"]),
        ("add_color_scale", {"file_path": "book.xlsx"}, ["book.xlsx"]),
        ("add_data_bar", {"file_path": "book.xlsx"}, ["book.xlsx"]),
        ("add_conditional_rule", {"file_path": "book.xlsx"}, ["book.xlsx"]),
        ("set_print_layout", {"file_path": "book.xlsx"}, ["book.xlsx"]),
        ("set_page_header_footer", {"file_path": "book.xlsx"}, ["book.xlsx"]),
        ("create_chart", {"output_path": "charts/out.png"}, ["charts/out.png"]),
        (
            "copy_range_between_sheets",
            {"source_file": "src.xlsx", "target_file": "dst.xlsx"},
            ["dst.xlsx"],
        ),
        (
            "transform_data",
            {"file_path": "src.xlsx", "output_path": "out.xlsx"},
            ["out.xlsx"],
        ),
    ]

    for tool_name, arguments, expected in cases:
        resolved = manager._resolve_target_paths(tool_name, arguments)
        relative = [str(path.relative_to(tmp_path)) for path in resolved]
        assert relative == expected
