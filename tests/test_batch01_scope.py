"""第 1 批验收：工作区绑定、child 交集、versions action、context 释放、并行只读。"""

from __future__ import annotations

from pathlib import Path
import pytest

from excelmanus.config import ExcelManusConfig
from excelmanus.engine import AgentEngine
from excelmanus.mcp.manager import _adapt_mcp_call_arguments
from excelmanus.subagent.child import child_capability, compose_child
from excelmanus.subagent.guard import reject_readonly_write
from excelmanus.subagent.models import SubagentConfig
from excelmanus.subagent.parallel import ParallelTask, detect_parallel_conflict
from excelmanus.tools.context import (
    ToolContextMissing,
    bind_workspace,
    clear_call,
    current_call,
    require_call,
    reset_call,
    use_workspace,
)
from excelmanus.tools.policy import write_effect_for_call
from excelmanus.tools.registry import ToolRegistry
from excelmanus.workbook_commit import (
    export_seen_versions,
    peek_seen_content_version,
    remember_content_version,
    seed_seen_versions,
)
from excelmanus.workspace.identity import public_identity
from excelmanus.workspace.refs import WorkspaceRef


def _config(root: Path) -> ExcelManusConfig:
    return ExcelManusConfig(
        api_key="test-key",
        base_url="https://test.example.com/v1",
        model="test-model",
        workspace_root=str(root),
    )


def test_a1_native_and_mcp_and_reference_hit_bound_workspace(tmp_path: Path) -> None:
    a = tmp_path / "ws_a"
    b = tmp_path / "ws_b"
    a.mkdir()
    b.mkdir()
    (a / "report.xlsx").write_bytes(b"aaa")
    (b / "report.xlsx").write_bytes(b"bbb")

    token = bind_workspace(b)
    try:
        from excelmanus.tools.context import require_guard

        native_path = require_guard().resolve_and_validate("report.xlsx")
        assert Path(native_path).resolve() == (b / "report.xlsx").resolve()

        adapted = _adapt_mcp_call_arguments(
            server_name="excel",
            arguments={"fileAbsolutePath": "report.xlsx"},
            workspace_root=str(a),
        )
        assert Path(adapted["fileAbsolutePath"]).resolve() == (b / "report.xlsx").resolve()
    finally:
        reset_call(token)


def test_a1_missing_context_is_fail_closed() -> None:
    clear_call()
    with pytest.raises(ToolContextMissing):
        require_call()
    from excelmanus.tools.context import require_guard

    with pytest.raises(ToolContextMissing):
        require_guard()
    with pytest.raises(ToolContextMissing):
        _adapt_mcp_call_arguments(
            server_name="excel",
            arguments={"fileAbsolutePath": "report.xlsx"},
            workspace_root=".",
        )


def _mcp_fake_client(captured: dict[str, object]) -> object:
    from types import SimpleNamespace

    async def _call_tool(_name: str, arguments: dict[str, object]) -> SimpleNamespace:
        captured["arguments"] = arguments
        return SimpleNamespace(content=[SimpleNamespace(type="text", text="ok")])

    return SimpleNamespace(call_tool=_call_tool, _config=SimpleNamespace(timeout=5))


@pytest.mark.asyncio
async def test_a1_mcp_async_wrapper_uses_bound_workspace_not_closure(tmp_path: Path) -> None:
    """正式异步包装在已 bind 时改写到调用者工作区，不得 UnboundLocalError。"""
    from excelmanus.mcp.manager import _make_async_tool_func

    a = tmp_path / "ws_a"
    b = tmp_path / "ws_b"
    a.mkdir()
    b.mkdir()
    (a / "report.xlsx").write_bytes(b"aaa")
    (b / "report.xlsx").write_bytes(b"bbb")
    captured: dict[str, object] = {}
    fn = _make_async_tool_func(_mcp_fake_client(captured), "excel", "read", str(a))

    token = bind_workspace(b)
    try:
        result = await fn(fileAbsolutePath="report.xlsx")
    finally:
        reset_call(token)

    assert result.success and result.value == "ok"
    called = captured["arguments"]
    assert isinstance(called, dict)
    assert Path(str(called["fileAbsolutePath"])).resolve() == (b / "report.xlsx").resolve()


def test_a1_mcp_sync_wrapper_uses_bound_workspace_not_closure(tmp_path: Path) -> None:
    from excelmanus.mcp.manager import _make_tool_func

    a = tmp_path / "ws_a"
    b = tmp_path / "ws_b"
    a.mkdir()
    b.mkdir()
    (a / "report.xlsx").write_bytes(b"aaa")
    (b / "report.xlsx").write_bytes(b"bbb")
    captured: dict[str, object] = {}
    fn = _make_tool_func(_mcp_fake_client(captured), "excel", "read", 5, str(a))

    token = bind_workspace(b)
    try:
        result = fn(fileAbsolutePath="report.xlsx")
    finally:
        reset_call(token)

    assert result.success and result.value == "ok"
    called = captured["arguments"]
    assert isinstance(called, dict)
    assert Path(str(called["fileAbsolutePath"])).resolve() == (b / "report.xlsx").resolve()


@pytest.mark.asyncio
async def test_a1_mcp_wrapper_missing_context_is_fail_closed(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from excelmanus.mcp.manager import _make_async_tool_func

    clear_call()
    client = SimpleNamespace(
        call_tool=lambda *_a, **_k: None,
        _config=SimpleNamespace(timeout=5),
    )
    fn = _make_async_tool_func(client, "excel", "read", str(tmp_path))
    result = await fn(fileAbsolutePath="report.xlsx")
    payload = getattr(result, "value", result)
    if isinstance(payload, dict):
        assert payload.get("error_code") == "TOOL_CONTEXT_MISSING"
    else:
        error = getattr(result, "error", None)
        assert error is not None
        assert getattr(error, "code", "") == "TOOL_CONTEXT_MISSING"


def test_a2_plan_parent_default_child_is_plan_not_write(tmp_path: Path) -> None:
    registry = ToolRegistry()
    registry.register_builtin_tools(str(tmp_path))
    parent = AgentEngine(_config(tmp_path), registry, workspace_ref=WorkspaceRef.from_root(tmp_path))
    parent._current_chat_mode = "plan"
    cfg = SubagentConfig(name="subagent", description="x", permission_mode="default")
    cap = child_capability(parent, cfg)
    assert cap.catalog_mode == "plan"
    child = compose_child(parent, cfg)
    assert child._current_chat_mode == "plan"
    assert "edit_spreadsheet" not in child.registry._tools
    assert "inspect_spreadsheet" in child.registry._tools


def test_a3_versions_list_not_rejected_by_readonly_guard() -> None:
    cfg = SubagentConfig(name="explorer", description="x", permission_mode="readOnly")
    assert reject_readonly_write(cfg, "manage_spreadsheet_versions", arguments={"action": "list"}) is None
    assert reject_readonly_write(cfg, "manage_spreadsheet_versions", arguments={"action": "restore"})
    assert write_effect_for_call(
        "manage_spreadsheet_versions",
        {"action": "list"},
        declared="workspace_write",
        actions={"list": {"write_effect": "none"}},
    ) == "none"


def test_a4_reset_clears_call_and_register_does_not_stick(tmp_path: Path) -> None:
    token = bind_workspace(tmp_path)
    assert current_call() is not None
    reset_call(token)
    assert current_call() is None
    registry = ToolRegistry()
    registry.register_builtin_tools(str(tmp_path))
    assert current_call() is None


def test_a5_parallel_rejects_any_writable_child() -> None:
    write_cfg = SubagentConfig(name="subagent", description="x", permission_mode="acceptEdits")
    read_cfg = SubagentConfig(name="explorer", description="x", permission_mode="readOnly")
    conflict = detect_parallel_conflict(
        [
            (ParallelTask(task="写", agent_name="subagent", file_paths=["a.xlsx"]), write_cfg),
            (ParallelTask(task="读", agent_name="explorer", file_paths=["b.xlsx"]), read_cfg),
        ]
    )
    assert conflict
    assert detect_parallel_conflict(
        [
            (ParallelTask(task="读1", agent_name="explorer"), read_cfg),
            (ParallelTask(task="读2", agent_name="explorer"), read_cfg),
        ]
    ) is None


def test_seen_versions_qualified_by_workspace(tmp_path: Path) -> None:
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    seed_seen_versions({})
    with use_workspace(a, workspace_id="wa"):
        remember_content_version("report.xlsx", "sha256:aaa")
        assert peek_seen_content_version("report.xlsx") == "sha256:aaa"
        exported = export_seen_versions()
        assert exported.get("report.xlsx") == "sha256:aaa"
        assert not any(k.startswith("id:") for k in exported)
    with use_workspace(b, workspace_id="wb"):
        assert peek_seen_content_version("report.xlsx") is None
        remember_content_version("report.xlsx", "sha256:bbb")
        assert peek_seen_content_version("report.xlsx") == "sha256:bbb"


def test_live_identity_does_not_guess_backup(tmp_path: Path) -> None:
    (tmp_path / "report.xlsx").write_bytes(b"live")
    backup = tmp_path / "outputs" / "backups"
    backup.mkdir(parents=True)
    (backup / "report.xlsx").write_bytes(b"copy")
    assert public_identity("outputs/backups/report.xlsx", tmp_path) == ""
    assert public_identity("report.xlsx", tmp_path) == "./report.xlsx"


def test_cli_style_workspace_ref_has_path_identity(tmp_path: Path) -> None:
    ref = WorkspaceRef.from_root(tmp_path)
    assert ref.workspace_id is None
    assert ref.identity_key().startswith("path:")


def test_engine_holds_workspace_ref(tmp_path: Path) -> None:
    ref = WorkspaceRef.from_root(tmp_path, workspace_id="ws-1")
    engine = AgentEngine(_config(tmp_path), ToolRegistry(), workspace_ref=ref)
    assert engine.workspace_ref.equals(ref)
    binding = engine.session_binding
    assert binding.workspace.equals(ref)
    assert binding.capability.catalog_mode == "write"
