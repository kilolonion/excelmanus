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
    assert "未回滚" in undo_msg
    assert "manage_spreadsheet_versions" in undo_msg
    assert target.read_text(encoding="utf-8") == "new\n"


def test_undo_rejects_human_edit_after_approved_write(tmp_path: Path) -> None:
    manager = ApprovalManager(str(tmp_path))
    target = tmp_path / "demo.txt"
    target.write_text("old\n", encoding="utf-8")
    approval_id = manager.new_approval_id()

    def execute(tool_name: str, arguments: dict, tool_scope: list[str]) -> str:
        target.write_text("new\n", encoding="utf-8")
        return '{"status":"success"}'

    manager.execute_and_audit(
        approval_id=approval_id,
        tool_name="write_text_file",
        arguments={"file_path": "demo.txt", "content": "new\n"},
        tool_scope=["write_text_file"],
        execute=execute,
        undoable=True,
        created_at_utc=manager.utc_now(),
    )
    target.write_text("human\n", encoding="utf-8")
    undo_msg = manager.undo(approval_id)
    assert "未回滚" in undo_msg
    assert "manage_spreadsheet_versions" in undo_msg
    assert target.read_text(encoding="utf-8") == "human\n"


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
    assert record.binary_snapshots == []
    assert not (manager.audit_root / approval_id / "snapshots").exists()
    assert target.read_bytes() == b"\x00NEW_BINARY"

    undo_msg = manager.undo(approval_id)
    assert "未回滚" in undo_msg
    assert target.read_bytes() == b"\x00NEW_BINARY"


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
    assert "未回滚" in undo_msg
    assert target.read_text(encoding="utf-8") == ""


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
    assert "未回滚" in undo_msg
    assert target.read_text(encoding="utf-8") == "after"


def test_tool_result_contract_error_is_returned_not_raised(tmp_path: Path) -> None:
    from excelmanus.engine_core.tool_result import error_result

    manager = ApprovalManager(str(tmp_path))
    approval_id = manager.new_approval_id()
    failed = error_result("版本冲突", code="VERSION_CONFLICT")

    def execute(tool_name: str, arguments: dict, tool_scope: list[str]):
        return failed

    payload, record = manager.execute_and_audit(
        approval_id=approval_id,
        tool_name="apply_spreadsheet_changes",
        arguments={"file_path": "book.xlsx"},
        tool_scope=["apply_spreadsheet_changes"],
        execute=execute,
        undoable=True,
        created_at_utc=manager.utc_now(),
    )
    assert payload is failed
    assert payload.success is False
    assert payload.error is not None
    assert payload.error.code == "VERSION_CONFLICT"
    assert record.execution_status == "failed"


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


def test_mcp_default_allow_without_auto_approve(tmp_path: Path) -> None:
    manager = ApprovalManager(str(tmp_path))
    tool_name = "mcp_context7_query_docs"
    assert manager.is_mcp_tool(tool_name) is True
    assert manager.is_confirm_required_tool(tool_name) is False
    assert manager.is_high_risk_tool(tool_name) is False
    manager.register_mcp_auto_approve([tool_name])
    assert manager.is_mcp_auto_approved(tool_name) is True
    assert manager.is_high_risk_tool(tool_name) is False


def test_builtin_high_risk_tools_still_confirm(tmp_path: Path) -> None:
    manager = ApprovalManager(str(tmp_path))
    assert manager.is_confirm_required_tool("run_shell") is True
    assert manager.is_confirm_required_tool("delete_file") is True
    assert manager.is_confirm_required_tool("write_text_file") is False


def test_register_mcp_auto_approve_replaces_not_accumulates(tmp_path: Path) -> None:
    """回归：重新同步 MCP 白名单时，旧的不再存在的工具应被移除。"""
    manager = ApprovalManager(str(tmp_path))
    old_tool = "mcp_old_server_tool"
    new_tool = "mcp_new_server_tool"

    manager.register_mcp_auto_approve([old_tool])
    assert manager.is_mcp_auto_approved(old_tool) is True

    # 重新同步，仅包含 new_tool
    manager.register_mcp_auto_approve([new_tool])
    assert manager.is_mcp_auto_approved(new_tool) is True
    assert manager.is_mcp_auto_approved(old_tool) is False


def test_mcp_auto_approve_skips_confirm_not_readonly(tmp_path: Path) -> None:
    manager = ApprovalManager(str(tmp_path))
    tool_name = "mcp_excel_write"
    manager.register_mcp_auto_approve([tool_name])
    assert manager.is_mcp_auto_approved(tool_name) is True
    assert manager.is_high_risk_tool(tool_name) is False
    assert manager.is_read_only_safe_tool(tool_name) is False


def test_registry_write_effects_drive_side_effect_policy_without_mcp_fields(tmp_path: Path) -> None:
    """宿主从现有 ToolDef 推导副作用；MCP 不需要新增 provider 字段。"""
    from excelmanus.tools.registry import ToolDef

    manager = ApprovalManager(str(tmp_path))
    manager.bind_tool_definitions([
        ToolDef(
            name="memory_save",
            description="save memory",
            input_schema={"type": "object"},
            func=lambda **_: "ok",
            write_effect="external_write",
        ),
        ToolDef(
            name="manage_skills",
            description="manage skills",
            input_schema={"type": "object"},
            func=lambda **_: "ok",
            write_effect="workspace_write",
        ),
        ToolDef(
            name="manage_spreadsheet_versions",
            description="versions",
            input_schema={"type": "object"},
            func=lambda **_: "ok",
            write_effect="workspace_write",
        ),
        ToolDef(
            name="mcp_demo_mutate",
            description="provider operation",
            input_schema={"type": "object"},
            func=lambda **_: "ok",
            write_effect="unknown",
            consistency="external_unverified",
        ),
    ])

    assert manager.is_mutating_tool("memory_save")
    assert manager.is_audit_only_tool("memory_save")
    assert manager.is_undoable_tool("memory_save") is False
    assert manager.is_audit_only_tool("manage_skills", {"action": "install"})
    assert manager.is_audit_only_tool("manage_skills", {"action": "list"}) is False
    assert manager.is_mutating_tool(
        "manage_spreadsheet_versions", {"action": "list"}
    ) is False
    assert manager.is_mutating_tool("mcp_demo_mutate")
    # 绑定到 registry 后，unknown MCP 使用宿主 fail-closed confirm；不要求
    # provider 增加任何字段。未绑定的纯名称兼容行为仍由上方回归覆盖。
    assert manager.is_high_risk_tool("mcp_demo_mutate") is True
    assert manager.is_undoable_tool("mcp_demo_mutate") is False


class TestSessionIdIsolation:
    """回归测试：验证 list_applied 按 session_id 隔离，防止回退时跨会话污染。"""

    @staticmethod
    def _make_record(
        manager: ApprovalManager,
        tmp_path: Path,
        filename: str,
        session_id: str | None,
        session_turn: int | None = None,
    ) -> str:
        target = tmp_path / filename
        target.write_text("before\n", encoding="utf-8")
        aid = manager.new_approval_id()

        def execute(tool_name: str, arguments: dict, tool_scope: list[str]) -> str:
            target.write_text("after\n", encoding="utf-8")
            return '{"status":"success"}'

        manager.execute_and_audit(
            approval_id=aid,
            tool_name="write_text_file",
            arguments={"file_path": filename, "content": "after\n"},
            tool_scope=["write_text_file"],
            execute=execute,
            undoable=True,
            created_at_utc=manager.utc_now(),
            session_turn=session_turn,
            session_id=session_id,
        )
        return aid

    def test_list_applied_filters_by_session_id(self, tmp_path: Path) -> None:
        manager = ApprovalManager(str(tmp_path))
        aid_a = self._make_record(manager, tmp_path, "a.txt", "sess-A", session_turn=1)
        aid_b = self._make_record(manager, tmp_path, "b.txt", "sess-B", session_turn=1)

        # 不过滤 → 两条都返回
        all_records = manager.list_applied(limit=100)
        all_ids = {r.approval_id for r in all_records}
        assert aid_a in all_ids
        assert aid_b in all_ids

        # 按 session_id 过滤 → 只返回对应会话
        a_records = manager.list_applied(limit=100, session_id="sess-A")
        a_ids = {r.approval_id for r in a_records}
        assert aid_a in a_ids
        assert aid_b not in a_ids

        b_records = manager.list_applied(limit=100, session_id="sess-B")
        b_ids = {r.approval_id for r in b_records}
        assert aid_b in b_ids
        assert aid_a not in b_ids

    def test_manifest_persists_session_id(self, tmp_path: Path) -> None:
        manager = ApprovalManager(str(tmp_path))
        aid = self._make_record(manager, tmp_path, "c.txt", "sess-C", session_turn=0)
        record = manager.get_applied(aid)
        assert record is not None
        assert record.session_id == "sess-C"

        # 从 manifest.json 重新加载，session_id 仍在
        manager2 = ApprovalManager(str(tmp_path))
        loaded = manager2.list_applied(limit=100, session_id="sess-C")
        assert any(r.approval_id == aid for r in loaded)

    def test_list_applied_with_nonexistent_session_returns_empty(self, tmp_path: Path) -> None:
        manager = ApprovalManager(str(tmp_path))
        self._make_record(manager, tmp_path, "d.txt", "sess-D", session_turn=0)
        result = manager.list_applied(limit=100, session_id="sess-NONEXIST")
        assert result == []

    def test_set_session_id_updates_instance(self, tmp_path: Path) -> None:
        manager = ApprovalManager(str(tmp_path))
        assert manager._session_id is None
        manager.set_session_id("sess-X")
        assert manager._session_id == "sess-X"

        # execute_and_audit 不显式传 session_id 时，使用实例级 _session_id
        aid = self._make_record(manager, tmp_path, "e.txt", session_id=None, session_turn=0)
        record = manager.get_applied(aid)
        assert record is not None
        assert record.session_id == "sess-X"


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


def test_manifest_survives_non_json_typed_arguments(tmp_path: Path) -> None:
    """arguments 携带 datetime 时 manifest/DB 不得因裸 json.dumps 崩溃。

    Code Mode 桥会把 {"$em_type":"datetime"} 还原成真 datetime 再下发；
    审计落盘用同一标记编码，读回经 revive_typed_args 还原，保持类型保真。
    """
    import datetime as dt

    manager = ApprovalManager(str(tmp_path))
    target = tmp_path / "book.xlsx"
    approval_id = manager.new_approval_id()

    def execute(tool_name: str, arguments: dict, tool_scope: list[str]) -> str:
        target.write_bytes(b"\x00XLSX")
        return '{"status":"success"}'

    args = {
        "file_path": "book.xlsx",
        "workbook_spec": {
            "sheets": [
                {
                    "name": "订单",
                    "value_blocks": [
                        {"start": "A1", "values": [["日期"], [dt.datetime(2024, 1, 15)]]}
                    ],
                }
            ]
        },
    }
    _, record = manager.execute_and_audit(
        approval_id=approval_id,
        tool_name="apply_spreadsheet_changes",
        arguments=args,
        tool_scope=["apply_spreadsheet_changes"],
        execute=execute,
        undoable=True,
        created_at_utc=manager.utc_now(),
    )
    assert target.exists()

    manifest = json.loads((tmp_path / record.manifest_file).read_text(encoding="utf-8"))
    stored = manifest["approval"]["arguments"]["workbook_spec"]["sheets"][0]["value_blocks"][0]["values"][1][0]
    assert stored == {"$em_type": "datetime", "v": "2024-01-15T00:00:00"}

    # 重新从 manifest 加载：arguments 还原回真实 datetime，与传入值类型一致
    fresh = ApprovalManager(str(tmp_path))
    loaded = fresh.get_applied(approval_id)
    assert loaded is not None
    revived = loaded.arguments["workbook_spec"]["sheets"][0]["value_blocks"][0]["values"][1][0]
    assert revived == dt.datetime(2024, 1, 15)
