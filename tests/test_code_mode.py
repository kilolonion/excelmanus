"""P4 Code Mode 第一刀：SDK 生成、文件桥、run_code 摘要。"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from excelmanus.engine_core.tool_result import ToolError, ToolResult, ToolUiMeta
from excelmanus.security.sandbox_hook import generate_wrapper_script
from excelmanus.tools.registry import ToolDef


def _read_excel_def() -> ToolDef:
    return ToolDef(
        name="read_excel",
        description="读取 Excel 摘要。",
        input_schema={
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "文件路径"},
                "sheet_name": {"type": "string", "description": "工作表名"},
                "max_rows": {"type": "integer", "minimum": 1},
            },
            "required": ["file_path"],
            "additionalProperties": False,
        },
        func=lambda **kwargs: None,
    )


def _exec_sdk(source: str) -> dict[str, object]:
    ns: dict[str, object] = {}
    exec(compile(source, "<sdk>", "exec"), ns)  # noqa: S102 — 测试加载生成源
    return ns


class TestRenderSdkSource:
    def test_schema_emits_read_excel_without_eval(self) -> None:
        from excelmanus.code_mode import render_sdk_source

        source = render_sdk_source([_read_excel_def()])
        assert "def read_excel(" in source
        assert "file_path" in source
        assert "sheet_name=None" in source
        assert "读取 Excel 摘要。" in source
        assert "eval(" not in source
        assert '"""读取 Excel 摘要。"""' not in source
        assert "read_excel.__doc__ =" in source

    def test_sdk_docstring_with_regex_escape_does_not_warn(self) -> None:
        import warnings

        from excelmanus.code_mode import render_sdk_source

        tool = ToolDef(
            name="write_word",
            description=r"匹配 ^-?\d+(\.\d+)?$ 的写成数字。",
            input_schema={
                "type": "object",
                "properties": {"file_path": {"type": "string"}},
                "required": ["file_path"],
            },
            func=lambda **kwargs: None,
        )
        source = render_sdk_source([tool])
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            compile(source, "<sdk>", "exec")
        syntax = [item for item in caught if issubclass(item.category, SyntaxWarning)]
        assert syntax == []
        assert r"\d" in source

    def test_sdk_section_lists_typed_signatures_not_bridge(self) -> None:
        from excelmanus.code_mode import render_sdk_section

        section = render_sdk_section([_read_excel_def()])
        assert "read_excel(file_path: str, sheet_name: str = None" in section
        assert "max_rows: int = None" in section
        assert "-> dict" in section
        assert "- run_code" not in section
        assert "_call_host" not in section
        assert "from em import" not in section

    def test_sdk_section_operations_typed_as_list(self) -> None:
        from excelmanus.code_mode import render_sdk_section
        from excelmanus.tools.intent_tools import get_tools

        edit = next(tool for tool in get_tools() if tool.name == "edit_spreadsheet")
        section = render_sdk_section([edit])
        assert "operations: list" in section
        # 嵌套 WorkbookSpec/operations 结构不展开进 SDK（F1 冻结）。
        assert "start_cell" not in section.split("edit_spreadsheet(", 1)[1].split(")", 1)[0]
        assert "dict{status, file_path, content_version" in section or "applied" in section

    def test_inspect_sdk_keeps_sheet_name_drops_sheet(self) -> None:
        from excelmanus.code_mode import render_sdk_source
        from excelmanus.tools.intent_tools import get_tools

        inspect = next(tool for tool in get_tools() if tool.name == "inspect_spreadsheet")
        source = render_sdk_source([inspect])
        assert "def inspect_spreadsheet(" in source
        assert "sheet_name=" in source
        assert "sheet=" not in source.split("def inspect_spreadsheet(", 1)[1].split(")", 1)[0]


class TestCodeModeBridge:
    @pytest.mark.asyncio
    async def test_fake_dispatcher_called_once_with_root_call_id(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from excelmanus.code_mode import CodeModeSession, render_sdk_source

        dispatcher = AsyncMock()
        dispatcher.call_registry_tool.return_value = ToolResult(
            success=True,
            model_text="ok",
            value={"rows": 3},
        )
        session = CodeModeSession(
            dispatcher=dispatcher,
            root_call_id="call_parent_1",
            bridge_dir=tmp_path / "bridge",
            call_timeout=8.0,
        )
        source = render_sdk_source([_read_excel_def()])
        monkeypatch.setenv("EXCELMANUS_CODE_MODE_BRIDGE", str(session.bridge_dir))
        monkeypatch.setenv("EXCELMANUS_CODE_MODE_ROOT_CALL_ID", "call_parent_1")
        monkeypatch.setenv("EXCELMANUS_CODE_MODE_TIMEOUT", "8")
        session.start()
        try:
            ns = _exec_sdk(source)
            result = await asyncio.to_thread(
                ns["read_excel"], "data.xlsx", sheet_name="Sheet1",
            )
        finally:
            session.stop()

        assert result == {"rows": 3}
        dispatcher.call_registry_tool.assert_awaited_once()
        kwargs = dispatcher.call_registry_tool.await_args.kwargs
        assert kwargs["tool_name"] == "read_excel"
        assert kwargs["arguments"]["file_path"] == "data.xlsx"
        assert kwargs["arguments"]["sheet_name"] == "Sheet1"
        assert kwargs["root_call_id"] == "call_parent_1"

    @pytest.mark.asyncio
    async def test_sdk_version_conflict_recovery_chain(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """S13 直接证据：SDK 侧过期版本写入 → VERSION_CONFLICT 携带当前版本
        → 重新 inspect 核对 → 用新版本重试成功。全程真实 bridge + 真实工具。"""
        from openpyxl import Workbook, load_workbook

        from excelmanus.code_mode import CodeModeSession, render_sdk_source
        from excelmanus.security import FileAccessGuard
        from excelmanus.tools._guard_ctx import set_guard
        from excelmanus.tools import intent_tools, reference_tools
        from excelmanus.tools.intent_tools import get_tools
        from excelmanus.tools.registry import ToolRegistry
        from excelmanus.workbook_commit import content_version_of_file, seed_seen_versions

        workspace = str(tmp_path)
        set_guard(FileAccessGuard(workspace))
        intent_tools.init_guard(workspace)
        reference_tools.init_guard(workspace)

        wb = Workbook()
        ws = wb.active
        assert ws is not None
        ws.title = "Sheet1"
        ws.append(["项目", "金额"])
        ws.append(["租金", 1200])
        wb.save(tmp_path / "book.xlsx")
        wb.close()

        registry = ToolRegistry()
        registry.register_builtin_tools(workspace)
        registry.bind_catalog(mode="write")
        tools = {t.name: t for t in get_tools()}

        from excelmanus.tools.context import use_workspace

        class _SyncDispatcher:
            """与生产 execute_subcall 同语义：每个 SDK 子调用绑自己的调用上下文。"""

            def call_registry_tool(self, *, tool_name, arguments, tool_scope=None, root_call_id=""):
                with use_workspace(tmp_path):
                    return registry.call_tool(tool_name, arguments)

        session = CodeModeSession(
            dispatcher=_SyncDispatcher(),
            root_call_id="call_s13",
            bridge_dir=tmp_path / ".tmp" / "code_mode" / "s13",
            tool_defs=[tools["inspect_spreadsheet"], tools["edit_spreadsheet"]],
            call_timeout=30.0,
        )
        monkeypatch.setenv("EXCELMANUS_CODE_MODE_BRIDGE", str(session.bridge_dir))
        monkeypatch.setenv("EXCELMANUS_CODE_MODE_ROOT_CALL_ID", "call_s13")
        session.start()
        try:
            ns = _exec_sdk(render_sdk_source(session.tool_defs))

            def _run() -> dict[str, object]:
                out: dict[str, object] = {}
                # 1) 读到 v1
                first = ns["inspect_spreadsheet"](
                    file_path="book.xlsx", mode="range", range="A1:B2",
                )
                v1 = first["content_version"]
                out["v1"] = v1
                # 2) 外部改盘（模拟他人/他进程写入）
                other = load_workbook(tmp_path / "book.xlsx")
                other.active["B2"] = 9999
                other.save(tmp_path / "book.xlsx")
                other.close()
                # 3) 用 v1 写 → VERSION_CONFLICT，且错误携带恢复字段
                try:
                    ns["edit_spreadsheet"](
                        file_path="book.xlsx",
                        expected_version=v1,
                        operations=[{
                            "kind": "write", "sheet": "Sheet1",
                            "start_cell": "C1", "values": [["x"]],
                        }],
                    )
                    out["conflict"] = "未冲突"
                except ns["HostToolError"] as exc:  # type: ignore[attr-defined]
                    out["conflict_code"] = exc.code
                    out["details"] = getattr(exc, "details", None) or str(exc)
                # 4) 重新核对受影响数据 → 拿当前版本
                second = ns["inspect_spreadsheet"](
                    file_path="book.xlsx", mode="range", range="A1:B2",
                )
                v2 = second["content_version"]
                out["v2"] = v2
                out["v1_ne_v2"] = v1 != v2
                # 5) 用核对后的版本重试 → 成功
                retry = ns["edit_spreadsheet"](
                    file_path="book.xlsx",
                    expected_version=v2,
                    operations=[{
                        "kind": "write", "sheet": "Sheet1",
                        "start_cell": "C1", "values": [["ok"]],
                    }],
                )
                out["retry_ok"] = bool(retry.get("content_version"))
                return out

            out = await asyncio.to_thread(_run)
        finally:
            session.stop()
            seed_seen_versions({})

        assert out["conflict_code"] == "VERSION_CONFLICT", out
        details = out.get("details")
        assert details is not None
        details_text = details if isinstance(details, str) else json.dumps(details, ensure_ascii=False)
        assert "sha256:" in details_text  # 错误内含可重试的当前版本
        assert out["v1_ne_v2"] is True
        assert out["retry_ok"] is True
        final = load_workbook(tmp_path / "book.xlsx")
        assert final.active["B2"].value == 9999  # 外部改动未被覆盖
        assert final.active["C1"].value == "ok"
        final.close()

    @pytest.mark.asyncio
    async def test_failed_subcall_raises_and_keeps_success_in_summary(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from excelmanus.code_mode import CodeModeSession, render_sdk_source

        dispatcher = AsyncMock()
        dispatcher.call_registry_tool.side_effect = [
            ToolResult(
                success=True,
                model_text="ok",
                value={"ok": True},
                ui_meta=ToolUiMeta(content_version="sha256:abc"),
            ),
            ToolResult(
                success=False,
                model_text="missing",
                error=ToolError(code="NOT_FOUND", message="missing"),
            ),
        ]
        session = CodeModeSession(
            dispatcher=dispatcher,
            root_call_id="call_parent_2",
            bridge_dir=tmp_path / "bridge",
            call_timeout=8.0,
        )
        source = render_sdk_source([_read_excel_def()])
        monkeypatch.setenv("EXCELMANUS_CODE_MODE_BRIDGE", str(session.bridge_dir))
        monkeypatch.setenv("EXCELMANUS_CODE_MODE_ROOT_CALL_ID", "call_parent_2")
        monkeypatch.setenv("EXCELMANUS_CODE_MODE_TIMEOUT", "8")
        session.start()
        try:
            ns = _exec_sdk(source)
            first = await asyncio.to_thread(ns["read_excel"], "ok.xlsx")
            assert first == {"ok": True}
            with pytest.raises(ns["HostToolError"]) as exc_info:
                await asyncio.to_thread(ns["read_excel"], "missing.xlsx")
        finally:
            session.stop()

        assert exc_info.value.code == "NOT_FOUND"
        summary = session.summary()
        assert summary["count"] == 2
        assert summary["succeeded"] == 1
        assert summary["failed"] == 1
        assert summary["writes"] == [
            {"tool": "read_excel", "content_version": "sha256:abc"},
        ]

    @pytest.mark.asyncio
    async def test_prefers_execute_subcall_over_registry_shortcut(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from excelmanus.code_mode import CodeModeSession, render_sdk_source

        class _FullDispatcher:
            def __init__(self) -> None:
                self.registry_calls = 0
                self.subcalls = 0

            async def execute_subcall(self, **kwargs):
                self.subcalls += 1
                return ToolResult(success=True, model_text="ok", value={"via": "execute"})

            async def call_registry_tool(self, **kwargs):
                self.registry_calls += 1
                return ToolResult(success=True, model_text="ok", value={"via": "registry"})

        dispatcher = _FullDispatcher()
        session = CodeModeSession(
            dispatcher=dispatcher,
            root_call_id="call_parent_3",
            bridge_dir=tmp_path / "bridge",
            call_timeout=8.0,
        )
        source = render_sdk_source([_read_excel_def()])
        monkeypatch.setenv("EXCELMANUS_CODE_MODE_BRIDGE", str(session.bridge_dir))
        monkeypatch.setenv("EXCELMANUS_CODE_MODE_ROOT_CALL_ID", "call_parent_3")
        monkeypatch.setenv("EXCELMANUS_CODE_MODE_TIMEOUT", "8")
        session.start()
        try:
            ns = _exec_sdk(source)
            result = await asyncio.to_thread(ns["read_excel"], "data.xlsx")
        finally:
            session.stop()
        assert result == {"via": "execute"}
        assert dispatcher.subcalls == 1
        assert dispatcher.registry_calls == 0

    @pytest.mark.asyncio
    async def test_subcalls_get_unique_ids_and_parent_event(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from excelmanus.code_mode import CodeModeSession, render_sdk_source

        seen: list[dict[str, object]] = []
        events: list[object] = []

        def on_event(event) -> None:
            events.append(event)

        class _FullDispatcher:
            async def execute_subcall(self, **kwargs):
                seen.append(kwargs)
                return ToolResult(success=True, model_text="ok", value={"n": len(seen)})

            async def call_registry_tool(self, **kwargs):
                raise AssertionError("should not use registry shortcut")

        dispatcher = _FullDispatcher()
        session = CodeModeSession(
            dispatcher=dispatcher,
            root_call_id="call_parent_4",
            bridge_dir=tmp_path / "bridge",
            call_timeout=8.0,
            on_event=on_event,
        )
        source = render_sdk_source([_read_excel_def()])
        monkeypatch.setenv("EXCELMANUS_CODE_MODE_BRIDGE", str(session.bridge_dir))
        monkeypatch.setenv("EXCELMANUS_CODE_MODE_ROOT_CALL_ID", "call_parent_4")
        monkeypatch.setenv("EXCELMANUS_CODE_MODE_TIMEOUT", "8")
        session.start()
        try:
            ns = _exec_sdk(source)
            await asyncio.to_thread(ns["read_excel"], "a.xlsx")
            await asyncio.to_thread(ns["read_excel"], "b.xlsx")
        finally:
            session.stop()
        assert len(seen) == 2
        assert seen[0]["call_id"] != seen[1]["call_id"]
        assert seen[0]["call_id"].startswith("call_parent_4:read_excel:")
        assert seen[1]["call_id"].startswith("call_parent_4:read_excel:")
        assert seen[0]["on_event"] is on_event
        assert seen[1]["on_event"] is on_event


class TestBridgeBinding:
    """P0-A：桥按绑定快照成员拒绝不可调用工具；root_call_id 只信宿主。"""

    def _resp_payload(self, resp_path: Path) -> dict[str, object]:
        return json.loads(resp_path.read_text(encoding="utf-8"))

    def test_tool_outside_binding_rejected(self, tmp_path: Path) -> None:
        from excelmanus.code_mode import CodeModeSession

        session = CodeModeSession(
            dispatcher=AsyncMock(),
            root_call_id="call_bind",
            bridge_dir=tmp_path / "bridge",
            bound_names=frozenset({"inspect_spreadsheet"}),
        )
        resp = tmp_path / "bridge" / "00000001.resp.json"
        session._handle_request(
            {"tool": "delete_file", "arguments": {}, "root_call_id": "spoofed"},
            resp,
        )
        payload = self._resp_payload(resp)
        assert payload["ok"] is False
        assert payload["error"]["code"] == "TOOL_NOT_IN_SDK"

    def test_non_dict_arguments_rejected(self, tmp_path: Path) -> None:
        from excelmanus.code_mode import CodeModeSession

        session = CodeModeSession(
            dispatcher=AsyncMock(),
            root_call_id="call_bind",
            bridge_dir=tmp_path / "bridge",
            bound_names=frozenset({"inspect_spreadsheet"}),
        )
        resp = tmp_path / "bridge" / "00000002.resp.json"
        session._handle_request(
            {"tool": "inspect_spreadsheet", "arguments": "book.xlsx"},
            resp,
        )
        payload = self._resp_payload(resp)
        assert payload["ok"] is False
        assert payload["error"]["code"] == "BAD_REQUEST"

    def test_none_binding_keeps_legacy_dispatch(self, tmp_path: Path) -> None:
        """bound_names=None（旧构造）不做成员检查，交给 dispatcher/tool_scope。"""
        from excelmanus.code_mode import CodeModeSession

        dispatcher = AsyncMock()
        dispatcher.call_registry_tool.return_value = ToolResult(
            success=True, model_text="ok", value={"status": "ok"}
        )
        session = CodeModeSession(
            dispatcher=dispatcher,
            root_call_id="call_bind",
            bridge_dir=tmp_path / "bridge",
        )
        resp = tmp_path / "bridge" / "00000003.resp.json"
        session._handle_request({"tool": "inspect_spreadsheet", "arguments": {}}, resp)
        payload = self._resp_payload(resp)
        assert payload["ok"] is True

    def test_payload_root_call_id_not_trusted(self, tmp_path: Path) -> None:
        from excelmanus.code_mode import CodeModeSession

        seen: dict[str, object] = {}

        async def _capture(*, tool_name, arguments, tool_scope=None, root_call_id=""):
            seen["root_call_id"] = root_call_id
            return ToolResult(success=True, model_text="ok", value={"status": "ok"})

        dispatcher = AsyncMock()
        dispatcher.call_registry_tool.side_effect = _capture
        session = CodeModeSession(
            dispatcher=dispatcher,
            root_call_id="host_root",
            bridge_dir=tmp_path / "bridge",
            bound_names=frozenset({"inspect_spreadsheet"}),
        )
        resp = tmp_path / "bridge" / "00000004.resp.json"
        session._handle_request(
            {
                "tool": "inspect_spreadsheet",
                "arguments": {},
                "root_call_id": "spoofed_root",
            },
            resp,
        )
        payload = self._resp_payload(resp)
        assert payload["ok"] is True
        assert seen["root_call_id"] == "host_root"


class TestBuildSession:
    """P0-B：目录推导失败显式抛出；bridge 目录每次运行唯一。"""

    def test_missing_registry_raises_code_mode_unavailable(
        self, tmp_path: Path,
    ) -> None:
        from types import SimpleNamespace

        from excelmanus.code_mode import (
            CodeModeUnavailable,
            build_session_for_run_code,
        )

        engine = SimpleNamespace(
            _registry=None,
            registry=None,
            config=SimpleNamespace(workspace_root=str(tmp_path)),
            _current_chat_mode="write",
        )
        dispatcher = SimpleNamespace(_engine=engine)
        with pytest.raises(CodeModeUnavailable):
            build_session_for_run_code(dispatcher, root_call_id="call_x")

    def test_bridge_dirs_are_unique_per_build(self, tmp_path: Path) -> None:
        from types import SimpleNamespace

        from excelmanus.code_mode import build_session_for_run_code
        from excelmanus.tools.registry import ToolRegistry

        registry = ToolRegistry()
        registry.register_builtin_tools(str(tmp_path))
        engine = SimpleNamespace(
            _registry=registry,
            registry=registry,
            config=SimpleNamespace(workspace_root=str(tmp_path)),
            _current_chat_mode="write",
            _fixed_capability=None,
            _skill_router=None,
            _subagent_config=None,
        )
        dispatcher = SimpleNamespace(_engine=engine)
        a = build_session_for_run_code(dispatcher, root_call_id="call_same")
        b = build_session_for_run_code(dispatcher, root_call_id="call_same")
        assert a.bridge_dir != b.bridge_dir
        assert "inspect_spreadsheet" in a.bound_names
        assert a.call_timeout == 900.0

    def test_timeout_seconds_sets_call_timeout_and_deadline(self, tmp_path: Path) -> None:
        import time
        from types import SimpleNamespace

        from excelmanus.code_mode import (
            build_session_for_run_code,
            timeout_seconds_from_args,
        )
        from excelmanus.tools.registry import ToolRegistry

        assert timeout_seconds_from_args({"timeout_seconds": 450}) == 450
        assert timeout_seconds_from_args({"timeout_seconds": 0}) == 900
        assert timeout_seconds_from_args({"timeout_seconds": 9999}) == 1800

        registry = ToolRegistry()
        registry.register_builtin_tools(str(tmp_path))
        engine = SimpleNamespace(
            _registry=registry,
            registry=registry,
            config=SimpleNamespace(workspace_root=str(tmp_path), subagent_timeout_seconds=600),
            _current_chat_mode="write",
            _fixed_capability=None,
            _skill_router=None,
            _subagent_config=None,
        )
        dispatcher = SimpleNamespace(_engine=engine)
        before = time.monotonic()
        session = build_session_for_run_code(
            dispatcher, root_call_id="call_to", timeout_seconds=450,
        )
        assert session.call_timeout == 450.0
        assert session.deadline_mono is not None
        assert 449.0 <= session.deadline_mono - before <= 451.0
        assert session.deadline_wall is not None

    def test_script_uses_sdk_detection(self, tmp_path: Path) -> None:
        from excelmanus.code_mode import script_uses_sdk

        assert script_uses_sdk({"code": "import em\nprint(em.x)"}) is True
        assert script_uses_sdk({"code": "from em import read_excel"}) is True
        assert script_uses_sdk({"code": "print(em.inspect_spreadsheet)"}) is True
        assert script_uses_sdk({"code": "print('hello')"}) is False
        assert script_uses_sdk({"code": "em . inspect_spreadsheet()"}) is True
        script = tmp_path / "job.py"
        script.write_text("print(1)\n", encoding="utf-8")
        assert script_uses_sdk({"script_path": "job.py"}, workspace_root=tmp_path) is False
        assert script_uses_sdk({"script_path": "missing.py"}, workspace_root=tmp_path) is True
        assert script_uses_sdk({}) is True


class TestSessionStopSettlement:
    """P0-B/P0-C 前置：stop() 给未处理请求补 CANCELLED，不留悬挂。"""

    def test_stop_writes_cancelled_for_pending_requests(self, tmp_path: Path) -> None:
        from excelmanus.code_mode import CodeModeSession

        session = CodeModeSession(
            dispatcher=AsyncMock(),
            root_call_id="call_stop",
            bridge_dir=tmp_path / "bridge",
        )
        session.bridge_dir.mkdir(parents=True, exist_ok=True)
        (session.bridge_dir / "00000001.req.json").write_text(
            json.dumps({"tool": "inspect_spreadsheet", "arguments": {}}),
            encoding="utf-8",
        )
        session.stop()
        resp = session.bridge_dir / "00000001.resp.json"
        assert resp.exists()
        payload = json.loads(resp.read_text(encoding="utf-8"))
        assert payload["ok"] is False
        assert payload["error"]["code"] == "CANCELLED"


class TestDockerOffWording:
    def test_sdk_docstring_omits_strong_isolation_when_docker_off(self) -> None:
        from excelmanus.code_mode import render_sdk_source

        source = render_sdk_source([_read_excel_def()])
        assert "强隔离" not in source
        assert "本机受限子进程" in source or "SDK" in source

    def test_run_code_result_note_omits_strong_isolation(self, tmp_path: Path) -> None:
        from excelmanus.code_mode import (
            LOCAL_SANDBOX_DISCLAIMER,
            apply_sdk_calls_summary,
        )
        from excelmanus.code_mode import CodeModeSession

        dispatcher = AsyncMock()
        session = CodeModeSession(
            dispatcher=dispatcher,
            root_call_id="call_note",
            bridge_dir=tmp_path / "bridge",
        )
        raw = json.dumps({"status": "success", "stdout_tail": "ok"})
        merged = apply_sdk_calls_summary(raw, session)
        payload = json.loads(merged)
        assert "强隔离" not in merged
        assert "强隔离" not in LOCAL_SANDBOX_DISCLAIMER
        assert payload.get("sandbox_note") == LOCAL_SANDBOX_DISCLAIMER


class TestWrapperSdkInject:
    def test_wrapper_exposes_em_module(self, tmp_path: Path) -> None:
        from excelmanus.code_mode import render_sdk_source

        sdk_path = tmp_path / "em.py"
        sdk_path.write_text(
            render_sdk_source([_read_excel_def()]),
            encoding="utf-8",
        )
        script = tmp_path / "user.py"
        script.write_text(
            "from em import read_excel\nprint(read_excel.__name__)\n",
            encoding="utf-8",
        )
        wrapper_src = generate_wrapper_script("GREEN", str(tmp_path))
        wrapper_path = tmp_path / "_wrapper.py"
        wrapper_path.write_text(wrapper_src, encoding="utf-8")
        env = os.environ.copy()
        env["EXCELMANUS_CODE_MODE_SDK"] = str(sdk_path)
        completed = subprocess.run(
            [sys.executable, str(wrapper_path), str(script)],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
        )
        assert completed.returncode == 0, completed.stderr
        assert "read_excel" in completed.stdout
        assert "强隔离" not in wrapper_src


class TestBridgeTypedArgs:
    @pytest.mark.asyncio
    async def test_datetime_and_nested_values_round_trip(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """R24 复现：run_code 里构造 datetime 值不能再把 json.dump 打崩。"""
        import datetime as dt

        from excelmanus.code_mode import CodeModeSession, render_sdk_source

        seen: dict[str, object] = {}

        async def _capture(*, tool_name, arguments, **kwargs):
            seen.update(arguments)
            return ToolResult(success=True, model_text="ok", value={})

        dispatcher = AsyncMock()
        dispatcher.call_registry_tool.side_effect = _capture
        session = CodeModeSession(
            dispatcher=dispatcher,
            root_call_id="call_types",
            bridge_dir=tmp_path / "bridge",
            call_timeout=8.0,
        )
        source = render_sdk_source([_read_excel_def()])
        monkeypatch.setenv("EXCELMANUS_CODE_MODE_BRIDGE", str(session.bridge_dir))
        monkeypatch.setenv("EXCELMANUS_CODE_MODE_TIMEOUT", "8")
        session.start()
        try:
            ns = _exec_sdk(source)
            payload_args = {
                "file_path": "out.xlsx",
                "when": dt.datetime(2024, 8, 15, 10, 30),
                "rows": [[dt.date(2024, 1, 2), dt.time(9, 5), {"k": dt.datetime(2024, 3, 4)}]],
            }
            await asyncio.to_thread(ns["_call_host"], "read_excel", payload_args)
        finally:
            session.stop()

        assert seen["when"] == dt.datetime(2024, 8, 15, 10, 30)
        row = seen["rows"][0]
        assert row[0] == dt.date(2024, 1, 2)
        assert row[1] == dt.time(9, 5)
        assert row[2] == {"k": dt.datetime(2024, 3, 4)}

    def test_revive_typed_args_passthrough_and_bad_marker(self) -> None:
        import datetime as dt

        from excelmanus.code_mode import _revive_typed_args

        assert _revive_typed_args({"a": 1, "b": "x"}) == {"a": 1, "b": "x"}
        assert _revive_typed_args({"$em_type": "date", "v": "2024-02-29"}) == dt.date(2024, 2, 29)
        assert _revive_typed_args({"$em_type": "datetime", "v": "NaT"}) is None
        assert _revive_typed_args({"$em_type": "unknown", "v": "x"}) == {
            "$em_type": "unknown",
            "v": "x",
        }


class TestBridgeCallTimeout:
    """P0-C：可交互子调用给足 600s 交互窗口，普通调用保持默认。"""

    def test_interactive_tools_get_extended_timeout(self, tmp_path: Path) -> None:
        import time

        from excelmanus.code_mode import CodeModeSession, interaction_wait_window
        from excelmanus.interaction import DEFAULT_INTERACTION_TIMEOUT

        # 父预算充足时，所有子调用（含普通工具——hook 可打在任意工具上）
        # 都拿满交互窗口。
        session = CodeModeSession(
            dispatcher=AsyncMock(),
            root_call_id="call_t",
            bridge_dir=tmp_path / "bridge",
            call_timeout=120.0,
            deadline_mono=time.monotonic() + 3600.0,
        )
        for tool in ("ask_user", "run_shell", "delete_file", "inspect_spreadsheet"):
            assert session._timeout_for(tool) == interaction_wait_window()
            assert session._timeout_for(tool) >= DEFAULT_INTERACTION_TIMEOUT

        # 父剩余预算比窗口更紧时，等待以剩余预算为界。
        tight = CodeModeSession(
            dispatcher=AsyncMock(),
            root_call_id="call_t2",
            bridge_dir=tmp_path / "bridge2",
            call_timeout=120.0,
            deadline_mono=time.monotonic() + 5.0,
        )
        assert 0.0 < tight._timeout_for("ask_user") <= 5.5

    def test_sdk_source_embeds_tool_timeouts(self) -> None:
        from excelmanus.code_mode import render_sdk_source
        from excelmanus.interaction import DEFAULT_INTERACTION_TIMEOUT

        source = render_sdk_source([_read_excel_def()])
        assert "_EM_INTERACTIVE_WINDOW" in source
        assert "_EM_TOOL_TIMEOUTS" in source
        ns = _exec_sdk(source)
        # hook ASK 可打在任意工具上：所有工具默认用交互窗口，只有
        # delegate 之类长任务在 _EM_TOOL_TIMEOUTS 单列。
        assert ns["_EM_INTERACTIVE_WINDOW"] >= DEFAULT_INTERACTION_TIMEOUT
        timeouts = ns["_EM_TOOL_TIMEOUTS"]
        assert "ask_user" not in timeouts
        assert "run_shell" not in timeouts
        assert timeouts.get("delegate", 0) >= 600.0
        assert "min(max(0.0, remaining), own)" in source


class TestRunCodeTimeoutBudget:
    """P0-C：脚本墙钟默认 900s、上限 1800s，覆盖 600s 交互窗口。"""

    def test_default_is_900_and_cap_1800(self) -> None:
        import inspect as _inspect

        from excelmanus.tools.code_tools import run_code

        sig = _inspect.signature(run_code)
        assert sig.parameters["timeout_seconds"].default == 900

    def test_over_cap_rejected(self, tmp_path: Path) -> None:
        from excelmanus.tools._guard_ctx import set_guard
        from excelmanus.security import FileAccessGuard
        from excelmanus.tools.code_tools import run_code

        set_guard(FileAccessGuard(str(tmp_path)))
        with pytest.raises(ValueError, match="1800"):
            run_code(code="print(1)", timeout_seconds=1801)

    def test_schema_declares_900_default_1800_max(self, tmp_path: Path) -> None:
        from excelmanus.tools.registry import ToolRegistry

        registry = ToolRegistry()
        registry.register_builtin_tools(str(tmp_path))
        spec = registry.get_tool("run_code").input_schema["properties"]["timeout_seconds"]
        assert spec["default"] == 900
        assert spec["maximum"] == 1800


def _make_approval_engine(tmp_path: Path) -> object:
    from types import SimpleNamespace

    from excelmanus.approval import ApprovalManager
    from excelmanus.interaction import InteractionRegistry

    approval = ApprovalManager(str(tmp_path))
    return SimpleNamespace(
        approval=approval,
        _approval=approval,
        _interaction_registry=InteractionRegistry(),
        _approval_resolver=None,
        _active_code_mode_session=None,
        _inflight_approval_ids=set(),
        _last_approved_structured=None,
    )


def _make_dispatcher(engine: object) -> object:
    import threading
    from types import SimpleNamespace

    from excelmanus.engine_core.tool_dispatcher import ToolDispatcher

    dispatcher = ToolDispatcher.__new__(ToolDispatcher)
    dispatcher._engine = engine
    dispatcher._cancel_event = threading.Event()
    return dispatcher


def _pending_tcr(approval_id: str) -> object:
    from types import SimpleNamespace

    return SimpleNamespace(
        pending_approval=True,
        approval_id=approval_id,
        result="等待审批",
    )


class TestSubcallApprovalAwait:
    """P0-C：子调用审批——同一决策通道、accept 恢复一次、取消/超时收敛为终态。"""

    @pytest.mark.asyncio
    async def test_accept_resumes_original_call_once(self, tmp_path: Path) -> None:
        from types import SimpleNamespace

        engine = _make_approval_engine(tmp_path)
        dispatcher = _make_dispatcher(engine)
        pending = engine.approval.create_pending(
            tool_name="run_shell", arguments={"cmd": "ls"},
        )
        structured = ToolResult(success=True, model_text="done", value={"out": 1})

        async def _apply(decision, pend, aid, tcid, on_event, iteration, source):
            engine._last_approved_structured = structured
            engine.approval.clear_pending()
            return (dict(success=True, result="done"), True)

        engine._apply_approval_decision = AsyncMock(side_effect=_apply)

        async def _approve() -> None:
            await asyncio.sleep(0.05)
            assert engine._interaction_registry.resolve(
                pending.approval_id,
                {"decision": "accept", "approval_id": pending.approval_id},
            )

        task = asyncio.create_task(_approve())
        try:
            result = await dispatcher._await_subcall_approval(
                _pending_tcr(pending.approval_id),
                SimpleNamespace(id="root:run_shell:1"),
                None,
            )
        finally:
            task.cancel()
        assert result is structured
        engine._apply_approval_decision.assert_awaited_once()
        assert engine.approval.pending is None
        assert pending.approval_id not in engine._inflight_approval_ids

    @pytest.mark.asyncio
    async def test_late_and_duplicate_resolve_do_not_replay(self, tmp_path: Path) -> None:
        from types import SimpleNamespace

        engine = _make_approval_engine(tmp_path)
        dispatcher = _make_dispatcher(engine)
        pending = engine.approval.create_pending(
            tool_name="run_shell", arguments={"cmd": "ls"},
        )
        engine._apply_approval_decision = AsyncMock(
            return_value=(dict(success=True, result="done"), True),
        )
        fut_done = asyncio.Event()

        async def _approve_twice() -> None:
            await asyncio.sleep(0.05)
            assert engine._interaction_registry.resolve(
                pending.approval_id, {"decision": "accept"},
            )
            # 重复批准：Future 已 resolve 后不存在 → False，不会二次执行
            assert not engine._interaction_registry.resolve(
                pending.approval_id, {"decision": "accept"},
            )
            fut_done.set()

        task = asyncio.create_task(_approve_twice())
        try:
            await dispatcher._await_subcall_approval(
                _pending_tcr(pending.approval_id),
                SimpleNamespace(id="root:run_shell:1"),
                None,
            )
            await asyncio.wait_for(fut_done.wait(), timeout=1.0)
        finally:
            task.cancel()
        engine._apply_approval_decision.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_parent_cancel_aborts_wait_and_clears_pending(
        self, tmp_path: Path,
    ) -> None:
        from types import SimpleNamespace

        engine = _make_approval_engine(tmp_path)
        dispatcher = _make_dispatcher(engine)
        session = SimpleNamespace(_subcall_cancel=asyncio.Event())
        engine._active_code_mode_session = session
        pending = engine.approval.create_pending(
            tool_name="run_shell", arguments={"cmd": "rm -rf /"},
        )

        async def _cancel() -> None:
            await asyncio.sleep(0.05)
            session._subcall_cancel.set()

        task = asyncio.create_task(_cancel())
        try:
            result = await dispatcher._await_subcall_approval(
                _pending_tcr(pending.approval_id),
                SimpleNamespace(id="root:run_shell:1"),
                None,
            )
        finally:
            task.cancel()
        assert result.success is False
        assert result.error is not None and result.error.code == "CANCELLED"
        assert engine.approval.pending is None

    @pytest.mark.asyncio
    async def test_timeout_rejects_pending(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from types import SimpleNamespace

        monkeypatch.setattr(
            "excelmanus.interaction.DEFAULT_INTERACTION_TIMEOUT", 0.05,
        )
        engine = _make_approval_engine(tmp_path)
        dispatcher = _make_dispatcher(engine)
        pending = engine.approval.create_pending(
            tool_name="run_shell", arguments={"cmd": "ls"},
        )
        result = await dispatcher._await_subcall_approval(
            _pending_tcr(pending.approval_id),
            SimpleNamespace(id="root:run_shell:1"),
            None,
        )
        assert result.success is False
        assert result.error is not None and result.error.code == "APPROVAL_TIMEOUT"
        assert engine.approval.pending is None

    @pytest.mark.asyncio
    async def test_resolver_channel_accept(self, tmp_path: Path) -> None:
        from types import SimpleNamespace

        engine = _make_approval_engine(tmp_path)
        dispatcher = _make_dispatcher(engine)
        engine._approval_resolver = AsyncMock(return_value="accept")
        pending = engine.approval.create_pending(
            tool_name="delete_file", arguments={"file_path": "x.txt"},
        )
        engine._apply_approval_decision = AsyncMock(
            return_value=(dict(success=True, result="done"), True),
        )
        result = await dispatcher._await_subcall_approval(
            _pending_tcr(pending.approval_id),
            SimpleNamespace(id="root:delete_file:1"),
            None,
        )
        assert result.success is True
        engine._approval_resolver.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_inflight_guard_blocks_accept_replay(self, tmp_path: Path) -> None:
        """inflight 中的 pending 走 /accept 不得触发 _execute_approved_pending 重放。"""
        from types import SimpleNamespace

        from excelmanus.engine_core.command_handler import CommandHandler

        engine = _make_approval_engine(tmp_path)
        engine._pending_approval_tool_call_id = None
        engine._pending_approval_route_result = None
        engine._execute_approved_pending = AsyncMock()
        engine.emit = lambda *a, **k: None
        pending = engine.approval.create_pending(
            tool_name="run_shell", arguments={"cmd": "ls"},
        )
        engine._inflight_approval_ids.add(pending.approval_id)
        # inflight 等待中的 registry Future：/accept 把决策注入等待通道
        engine._interaction_registry.create(pending.approval_id)

        handler = CommandHandler(engine)
        reply = await handler._handle_accept_command(
            ["/accept", pending.approval_id], on_event=None,
        )
        assert "已批准" in reply
        engine._execute_approved_pending.assert_not_called()


class TestSessionCancelSignal:
    """P0-C：stop() 置位子调用取消事件，引擎侧等待能及时退出。"""

    @pytest.mark.asyncio
    async def test_stop_sets_subcall_cancel_on_loop(self, tmp_path: Path) -> None:
        from excelmanus.code_mode import CodeModeSession

        session = CodeModeSession(
            dispatcher=AsyncMock(),
            root_call_id="call_c",
            bridge_dir=tmp_path / "bridge",
        )
        session._loop = asyncio.get_running_loop()
        session.stop()
        assert session._subcall_cancel.is_set()


class TestRuntimeHookAsk:
    """P0-C：hook ASK 走同一决策通道；resolver / registry / 超时 / 通道缺失。"""

    def _runtime(self, engine: object, dispatcher: object | None = None) -> object:
        from types import SimpleNamespace

        from excelmanus.engine_core.tool_dispatcher import ToolDispatcher
        from excelmanus.tools.runtime import ToolRuntime

        if dispatcher is None:
            dispatcher = SimpleNamespace(
                _engine=engine,
                _wait_approval_decision=ToolDispatcher._wait_approval_decision,
            )
        return ToolRuntime(dispatcher=dispatcher, engine=engine)

    @pytest.mark.asyncio
    async def test_hook_ask_resolver_accept_allows(self, tmp_path: Path) -> None:
        engine = _make_approval_engine(tmp_path)
        engine._approval_resolver = AsyncMock(return_value="accept")
        runtime = self._runtime(engine)
        token = runtime.allocate_token("edit_spreadsheet", {"file_path": "a.xlsx"})
        assert await runtime._one_shot_ask(token) is True
        engine._approval_resolver.assert_awaited_once()
        assert engine.approval.pending is None

    @pytest.mark.asyncio
    async def test_hook_ask_registry_accept_allows(self, tmp_path: Path) -> None:
        engine = _make_approval_engine(tmp_path)
        runtime = self._runtime(engine)
        token = runtime.allocate_token("edit_spreadsheet", {"file_path": "a.xlsx"})

        async def _approve() -> None:
            await asyncio.sleep(0.05)
            pending = engine.approval.pending
            assert pending is not None
            engine._interaction_registry.resolve(
                pending.approval_id, {"decision": "accept"},
            )

        task = asyncio.create_task(_approve())
        try:
            assert await runtime._one_shot_ask(token) is True
        finally:
            task.cancel()
        assert engine.approval.pending is None

    @pytest.mark.asyncio
    async def test_hook_ask_reject_denies(self, tmp_path: Path) -> None:
        engine = _make_approval_engine(tmp_path)
        engine._approval_resolver = AsyncMock(return_value="reject")
        runtime = self._runtime(engine)
        token = runtime.allocate_token("run_shell", {"cmd": "ls"})
        assert await runtime._one_shot_ask(token) is False
        assert engine.approval.pending is None

    @pytest.mark.asyncio
    async def test_hook_ask_timeout_denies_and_rejects(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "excelmanus.interaction.DEFAULT_INTERACTION_TIMEOUT", 0.05,
        )
        engine = _make_approval_engine(tmp_path)
        runtime = self._runtime(engine)
        token = runtime.allocate_token("run_shell", {"cmd": "ls"})
        assert await runtime._one_shot_ask(token) is False
        assert engine.approval.pending is None

    @pytest.mark.asyncio
    async def test_hook_ask_no_channel_denies(self, tmp_path: Path) -> None:
        from types import SimpleNamespace

        engine = _make_approval_engine(tmp_path)
        engine._interaction_registry = None
        runtime = self._runtime(engine)
        token = runtime.allocate_token("run_shell", {"cmd": "ls"})
        assert await runtime._one_shot_ask(token) is False
        assert engine.approval.pending is None


class TestOutputContracts:
    """P1-A：OUTPUT_CONTRACTS 与真实工具输出一致；违约不静默放过。"""

    def test_contract_violation_becomes_structured_error(self, tmp_path: Path) -> None:
        from excelmanus.code_mode import CodeModeSession

        dispatcher = AsyncMock()
        dispatcher.call_registry_tool.return_value = ToolResult(
            success=True, model_text="ok", value={"unexpected": 1},
        )
        session = CodeModeSession(
            dispatcher=dispatcher,
            root_call_id="call_cv",
            bridge_dir=tmp_path / "bridge",
        )
        resp = tmp_path / "bridge" / "00000001.resp.json"
        session._handle_request(
            {"tool": "edit_spreadsheet", "arguments": {}}, resp,
        )
        payload = json.loads(resp.read_text(encoding="utf-8"))
        assert payload["ok"] is False
        assert payload["error"]["code"] == "SDK_CONTRACT_VIOLATION"
        assert "content_version" in json.dumps(payload["error"]["details"])

    def test_unregistered_tool_passes_through(self, tmp_path: Path) -> None:
        from excelmanus.code_mode import _payload_from_tool_result

        result = ToolResult(success=True, model_text="ok", value={"anything": 1})
        payload = _payload_from_tool_result(result, tool_name="some_other_tool")
        assert payload["ok"] is True
        assert payload["value"] == {"anything": 1}

    def test_real_tool_outputs_satisfy_contracts(self, tmp_path: Path) -> None:
        """夹具断言：核心工具真实返回值满足各自声明的必有键。"""
        from openpyxl import Workbook

        from excelmanus.security import FileAccessGuard
        from excelmanus.tools import intent_tools
        from excelmanus.tools._guard_ctx import set_guard
        from excelmanus.tools.context import use_workspace
        from excelmanus.tools.output_contracts import validate_output

        wb = Workbook()
        ws = wb.active
        assert ws is not None
        ws.title = "S1"
        ws.append(["项目", "金额"])
        ws.append(["租金", 1200])
        wb.save(tmp_path / "book.xlsx")
        wb.close()
        set_guard(FileAccessGuard(str(tmp_path)))
        intent_tools.init_guard(str(tmp_path))

        calls = [
            ("inspect_spreadsheet", {"file_path": "book.xlsx", "mode": "overview"}),
            ("inspect_spreadsheet", {
                "file_path": "book.xlsx", "mode": "range", "range": "A1:B2",
            }),
            ("analyze_spreadsheet", {"file_path": "book.xlsx", "mode": "files"}),
            ("analyze_spreadsheet", {
                "file_path": "book.xlsx", "mode": "filter",
                "sheet_name": "S1", "column": "金额",
                "operator": ">", "value": 500,
            }),
            ("edit_spreadsheet", {
                "file_path": "book.xlsx",
                "operations": [{
                    "kind": "write", "sheet": "S1",
                    "start_cell": "C1", "values": [["x"]],
                }],
            }),
            ("split_spreadsheet", {
                "file_path": "book.xlsx", "sheet_name": "S1",
                "by_column": "项目", "output_dir": "outputs",
            }),
            ("manage_spreadsheet_versions", {
                "file_path": "book.xlsx", "action": "list",
            }),
        ]
        with use_workspace(tmp_path):
            for name, args in calls:
                result = getattr(intent_tools, name)(**args)
                assert result.success, f"{name} 执行失败: {result.model_text}"
                violations = validate_output(name, result.value, arguments=args)
                assert violations == [], f"{name} 合同违约: {violations}"

    def test_edit_spreadsheet_arg_branch(self) -> None:
        """workbook_spec 创建分支不带 applied 不违约；operations 路径必须带。"""
        from excelmanus.tools.output_contracts import validate_output

        spec_value = {
            "status": "success", "file_path": "out.xlsx",
            "content_version": "v1", "build_summary": {"sheets": 1},
        }
        assert validate_output(
            "edit_spreadsheet", spec_value,
            arguments={"workbook_spec": {"sheets": []}},
        ) == []
        # 无 spec 的 operations 编辑必须带 applied。
        ops_args = {"operations": [{"kind": "write"}]}
        missing = [v for v in validate_output(
            "edit_spreadsheet", spec_value, arguments=ops_args,
        ) if "applied" in v]
        assert missing
        assert validate_output(
            "edit_spreadsheet",
            {**spec_value, "applied": []},
            arguments=ops_args,
        ) == []
        # 不传 arguments 时参数分支跳过，只看共有必有键。
        assert validate_output("edit_spreadsheet", spec_value) == []

    def test_spill_retrieve_skips_contract(self) -> None:
        """spill 句柄读取是宿主投影：字符串 value 不触发对象合同误报。"""
        from excelmanus.code_mode import _payload_from_tool_result

        result = ToolResult(
            success=True,
            model_text="raw text",
            value="非 JSON 原始文本",
            coverage={"spill_retrieve": True},
        )
        payload = _payload_from_tool_result(result, tool_name="read_text_file")
        assert payload == {"ok": True, "value": "非 JSON 原始文本"}

    def test_return_hint_marks_optional_keys(self) -> None:
        from excelmanus.tools.output_contracts import contract_for

        hint = contract_for("read_text_file").render_return_hint()
        assert "status" in hint and "truncated?" in hint
        edit = contract_for("edit_spreadsheet").render_return_hint()
        assert "applied?" in edit

    def test_sdk_nested_array_field_hint(self) -> None:
        """对象数组参数提示嵌套字段名；operations/workbook_spec 仍冻结。"""
        from excelmanus.code_mode import _py_type_of

        questions = {
            "type": "array",
            "items": {"type": "object", "properties": {
                "text": {"type": "string"}, "options": {"type": "array"},
            }},
        }
        assert _py_type_of(questions, prop_name="questions") == (
            "list[dict{text, options}]"
        )
        ops = {
            "type": "array",
            "items": {"type": "object", "properties": {
                "kind": {"type": "string"}, "start_cell": {"type": "string"},
            }},
        }
        assert _py_type_of(ops, prop_name="operations") == "list"


class TestCodeModeWrapUp:
    """v3 收尾：超时注入、结算、合同类型、审计 call_id、空 SDK。"""

    def test_nested_budget_adds_child_usage_to_parent(self) -> None:
        from excelmanus.engine_core.tool_dispatcher import ToolDispatcher

        dispatcher = ToolDispatcher.__new__(ToolDispatcher)
        dispatcher._call_budget = 10
        dispatcher._call_count = 3
        dispatcher._call_budget_reason = "parent"
        dispatcher._readonly_replay_cache = {}
        snapshot = dispatcher.begin_nested_call_budget()
        dispatcher._call_count = 5
        dispatcher.restore_parent_call_budget(snapshot)
        assert dispatcher._call_count == 8
        assert dispatcher._call_budget == 10
        assert dispatcher._call_budget_reason == "parent"

    def test_sdk_calls_attached_after_stop(self, tmp_path: Path) -> None:
        from excelmanus.code_mode import (
            CodeModeSession,
            SdkCallRecord,
            attach_sdk_calls,
        )

        session = CodeModeSession(
            dispatcher=AsyncMock(),
            root_call_id="call_sdk",
            bridge_dir=tmp_path / "bridge",
        )
        session._record(SdkCallRecord(tool="inspect_spreadsheet", success=True, content_version="v1"))
        session.stop()
        result = ToolResult(success=True, model_text="{}", value={"status": "ok"})
        attached = attach_sdk_calls(result, session)
        assert attached.value["sdk_calls"]["count"] == 1
        assert attached.value["sdk_calls"]["writes"][0]["content_version"] == "v1"

    @pytest.mark.asyncio
    async def test_stop_does_not_join_on_running_loop(self, tmp_path: Path) -> None:
        from excelmanus.code_mode import CodeModeSession

        session = CodeModeSession(
            dispatcher=AsyncMock(),
            root_call_id="call_join",
            bridge_dir=tmp_path / "bridge",
        )
        session.start()
        session.stop()
        assert session.dispatch_closed
        await session.wait_settlement(timeout=2.0)

    def test_reject_new_foreground_commit_after_parent_close(self, tmp_path: Path) -> None:
        from excelmanus.code_mode import (
            CodeModeSession,
            reject_new_foreground_commit,
            reset_code_mode_session,
            set_code_mode_session,
        )

        session = CodeModeSession(
            dispatcher=AsyncMock(),
            root_call_id="call_fg",
            bridge_dir=tmp_path / "bridge",
        )
        token = set_code_mode_session(session)
        try:
            assert reject_new_foreground_commit() is None
            session.stop()
            assert reject_new_foreground_commit()
        finally:
            reset_code_mode_session(token)

    def test_timeout_env_injection(self, tmp_path: Path) -> None:
        from excelmanus.code_mode import (
            CodeModeSession,
            reset_code_mode_session,
            set_code_mode_session,
        )
        from excelmanus.tools.code_tools import _apply_code_mode_env

        session = CodeModeSession(
            dispatcher=AsyncMock(),
            root_call_id="call_env",
            bridge_dir=tmp_path / "bridge",
            call_timeout=450.0,
            deadline_wall=1_700_000_000.0,
            tool_defs=[_read_excel_def()],
        )
        token = set_code_mode_session(session)
        try:
            env: dict[str, str] = {}
            _apply_code_mode_env(env, workspace_root=tmp_path)
            assert env["EXCELMANUS_CODE_MODE_TIMEOUT"] == "450"
            assert env["EXCELMANUS_CODE_MODE_DEADLINE"] == "1700000000.0"
        finally:
            reset_code_mode_session(token)

    def test_output_contracts_cover_sdk_tools_and_type_check(self) -> None:
        from excelmanus.tools.output_contracts import OUTPUT_CONTRACTS, validate_output

        assert "create_spreadsheet" not in OUTPUT_CONTRACTS
        required = {
            "inspect_spreadsheet", "analyze_spreadsheet", "edit_spreadsheet",
            "format_spreadsheet", "split_spreadsheet",
            "compare_spreadsheets", "trace_spreadsheet_formulas",
            "manage_spreadsheet_objects", "manage_spreadsheet_versions",
            "list_directory", "read_text_file", "write_text_file", "edit_text_file",
            "copy_file", "rename_file", "delete_file", "offer_download",
            "run_shell", "skill", "ask_user", "delegate", "list_subagents",
        }
        assert required <= set(OUTPUT_CONTRACTS)
        violations = validate_output("run_shell", {
            "status": "ok",
            "command": "ls",
            "return_code": "0",
        })
        assert any("return_code" in item and "int" in item for item in violations)
        # 元工具（ask_user/skill 等）按 any 登记不校验——其返回形状由
        # 交互层决定（ask_user 实为回答 payload dict）。
        assert validate_output("skill", {"not": "a string"}) == []
        assert validate_output("ask_user", {"question_id": "q", "raw_input": "1"}) == []

    def test_empty_sdk_fails_loud(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from types import SimpleNamespace

        from excelmanus.prompt.assemble import build_stable_system_prompt

        monkeypatch.setattr("excelmanus.tools.runtime.present_as_of", lambda engine: "code")
        monkeypatch.setattr("excelmanus.tools.catalog.catalog_from_engine", lambda engine: None)
        engine = SimpleNamespace(
            _child_system_prompt=None,
            _prompt_composer=object(),
            _tool_runtime=SimpleNamespace(render_sdk_section=lambda: "   "),
            _current_chat_mode="write",
        )
        with pytest.raises(RuntimeError, match="SDK 段为空"):
            build_stable_system_prompt(engine)

    def test_tool_call_log_persists_call_id(self, tmp_path: Path) -> None:
        from excelmanus.database import Database
        from excelmanus.stores.tool_call_store import ToolCallStore

        db = Database(str(tmp_path / "audit.db"))
        cols = {
            row[1]
            for row in db.conn.execute("PRAGMA table_info(tool_call_log)").fetchall()
        }
        assert "call_id" in cols
        assert "parent_call_id" in cols
        store = ToolCallStore(db)
        store.log(
            session_id="s1",
            tool_name="inspect_spreadsheet",
            success=True,
            call_id="child-1",
            parent_call_id="run-1",
        )
        rows = store.query(session_id="s1")
        assert rows[0]["call_id"] == "child-1"
        assert rows[0]["parent_call_id"] == "run-1"

    def test_create_pending_stores_parent_call_id(self, tmp_path: Path) -> None:
        from excelmanus.approval import ApprovalManager
        from excelmanus.tools.context import (
            ToolCallContext,
            bind_call,
            bind_workspace,
            reset_call,
            require_call,
        )

        manager = ApprovalManager(str(tmp_path))
        pending = manager.create_pending(
            "run_shell", {"cmd": "ls"}, parent_call_id="run-parent",
        )
        assert pending.parent_call_id == "run-parent"
        manager.reject_pending(pending.approval_id)

        ws_token = bind_workspace(tmp_path)
        try:
            current = require_call()
            ctx_token = bind_call(ToolCallContext(
                binding=current.binding, call_id="child", parent_call_id="from-ctx",
            ))
            try:
                inherited = manager.create_pending("run_shell", {"cmd": "ls"})
                assert inherited.parent_call_id == "from-ctx"
            finally:
                reset_call(ctx_token)
        finally:
            reset_call(ws_token)


class TestSseSubcallEvents:
    """P1-B：SDK 子调用 SSE 开始/结束、parent 关联、session_events 回放、模型面隔离。"""

    def test_audit_payload_truncates_matrix_and_keeps_parent(self) -> None:
        from excelmanus.events import EventType, ToolCallEvent
        from excelmanus.session_log import tool_call_audit_payload

        huge = "CELL-" + ("x" * 4000)
        event = ToolCallEvent(
            event_type=EventType.TOOL_CALL_END,
            tool_call_id="child-1",
            tool_name="inspect_spreadsheet",
            arguments={"file_path": "book.xlsx", "grid": huge},
            result=huge,
            parent_call_id="run-1",
            success=True,
        )
        payload = tool_call_audit_payload(event)
        assert payload["parent_call_id"] == "run-1"
        assert payload["tool_call_id"] == "child-1"
        assert huge not in json.dumps(payload, ensure_ascii=False)
        assert len(str(payload.get("result") or "")) <= 500

    def test_sse_carries_parent_call_id(self) -> None:
        from excelmanus.api_sse import sse_event_to_sse
        from excelmanus.events import EventType, ToolCallEvent

        start = sse_event_to_sse(ToolCallEvent(
            event_type=EventType.TOOL_CALL_START,
            tool_call_id="child-1",
            tool_name="inspect_spreadsheet",
            arguments={"file_path": "book.xlsx"},
            parent_call_id="run-1",
        ))
        end = sse_event_to_sse(ToolCallEvent(
            event_type=EventType.TOOL_CALL_END,
            tool_call_id="child-1",
            tool_name="inspect_spreadsheet",
            success=True,
            result="ok",
            parent_call_id="run-1",
        ))
        assert start is not None and end is not None
        start_data = json.loads(start.split("data:", 1)[1].strip())
        end_data = json.loads(end.split("data:", 1)[1].strip())
        assert start_data["parent_call_id"] == "run-1"
        assert end_data["parent_call_id"] == "run-1"

    def test_save_events_replay_rebuilds_children_by_parent(self, tmp_path: Path) -> None:
        from excelmanus.chat_history import ChatHistoryStore
        from excelmanus.database import Database
        from excelmanus.events import EventType, ToolCallEvent
        from excelmanus.session_log import (
            SessionEventLog,
            append_tool_call_event,
            reconstruct_tool_call_timeline,
        )

        log = SessionEventLog("s-sse")
        log.append(
            "assistant/message",
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "run-1", "type": "function",
                                "function": {"name": "run_code", "arguments": "{}"}}],
                "message_id": "m-a",
            },
        )
        append_tool_call_event(log, ToolCallEvent(
            event_type=EventType.TOOL_CALL_START,
            tool_call_id="run-1",
            tool_name="run_code",
        ))
        append_tool_call_event(log, ToolCallEvent(
            event_type=EventType.TOOL_CALL_START,
            tool_call_id="run-1:inspect_spreadsheet:1",
            tool_name="inspect_spreadsheet",
            parent_call_id="run-1",
        ))
        append_tool_call_event(log, ToolCallEvent(
            event_type=EventType.TOOL_CALL_END,
            tool_call_id="run-1:inspect_spreadsheet:1",
            tool_name="inspect_spreadsheet",
            parent_call_id="run-1",
            success=True,
        ))
        append_tool_call_event(log, ToolCallEvent(
            event_type=EventType.TOOL_CALL_END,
            tool_call_id="run-1",
            tool_name="run_code",
            success=True,
        ))
        log.append(
            "tool/result",
            {"role": "tool", "tool_call_id": "run-1", "content": "summary", "message_id": "m-t"},
        )
        surface_roles = [m["role"] for m in log.surface_messages()]
        assert surface_roles == ["assistant", "tool"]
        assert all(m.get("tool_call_id") != "run-1:inspect_spreadsheet:1" for m in log.surface_messages())

        store = ChatHistoryStore(Database(str(tmp_path / "sse.db")))
        store.create_session("s-sse")
        store.save_events("s-sse", [ev.to_row() for ev in log.events])
        restored = SessionEventLog("s-sse", events=store.iter_events("s-sse"))
        timeline = reconstruct_tool_call_timeline(restored.events)
        assert len(timeline) == 1
        parent = timeline[0]
        assert parent["tool_call_id"] == "run-1"
        assert parent["started"] and parent["ended"]
        assert len(parent["children"]) == 1
        child = parent["children"][0]
        assert child["parent_call_id"] == "run-1"
        assert child["started"] and child["ended"]
        assert child["seq_end"] is not None and parent["seq_end"] is not None
        assert child["seq_end"] < parent["seq_end"]

    @pytest.mark.asyncio
    async def test_run_code_subprocess_emits_nested_sse_and_isolates_history(
        self, tmp_path: Path,
    ) -> None:
        import sys

        from openpyxl import Workbook

        from excelmanus.api_sse import sse_event_to_sse
        from excelmanus.config import ExcelManusConfig
        from excelmanus.engine import AgentEngine
        from excelmanus.engine_core.tool_dispatcher import _SyntheticToolCall
        from excelmanus.events import EventType, ToolCallEvent
        from excelmanus.session_log import (
            SessionEventLog,
            reconstruct_tool_call_timeline,
        )
        from excelmanus.tools import code_tools, intent_tools
        from excelmanus.tools.registry import ToolRegistry
        from excelmanus.workbook import data as data_tools
        from excelmanus.workbook_commit import seed_seen_versions

        intent_tools.init_guard(str(tmp_path))
        data_tools.init_guard(str(tmp_path))
        code_tools.init_guard(str(tmp_path))
        (tmp_path / "scripts" / "temp").mkdir(parents=True, exist_ok=True)
        seed_seen_versions({})

        book = tmp_path / "book.xlsx"
        wb = Workbook()
        ws = wb.active
        assert ws is not None
        ws.title = "Sheet1"
        for i in range(20):
            ws[f"A{i + 1}"] = f"row-{i}-secret-matrix"
        wb.save(book)
        wb.close()

        registry = ToolRegistry()
        registry.register_tools(intent_tools.get_tools())
        registry.register_tools(code_tools.get_tools())
        engine = AgentEngine(
            ExcelManusConfig(
                api_key="test-key",
                base_url="https://test.example.com/v1",
                model="test-model",
                workspace_root=str(tmp_path),
                max_iterations=8,
            ),
            registry,
        )
        engine._full_access_enabled = True
        engine._session_id = "sse-sub"
        log = SessionEventLog("sse-sub")
        engine.memory.attach_event_log(log)

        parent_id = "call_run_code_sse"
        live: list[ToolCallEvent] = []

        def on_event(event: object) -> None:
            if isinstance(event, ToolCallEvent):
                live.append(event)

        engine.memory.add_assistant_tool_message({
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": parent_id,
                "type": "function",
                "function": {
                    "name": "run_code",
                    "arguments": json.dumps({"code": "import em"}),
                },
            }],
        })
        script = (
            "import em\n"
            "r = em.inspect_spreadsheet("
            "file_path='book.xlsx', mode='range', sheet_name='Sheet1', max_rows=20)\n"
            "print(r.get('content_version') or 'ok')\n"
        )
        tc = _SyntheticToolCall(
            call_id=parent_id,
            name="run_code",
            arguments={
                "code": script,
                "python_command": sys.executable,
                "timeout_seconds": 60,
                "require_excel_deps": True,
            },
        )
        result = await engine._execute_tool_call(tc, None, on_event, 0)
        assert result.success, result.result
        engine.memory.add_tool_result(parent_id, result.result)

        starts = [e for e in live if e.event_type == EventType.TOOL_CALL_START]
        ends = [e for e in live if e.event_type == EventType.TOOL_CALL_END]
        parent_starts = [e for e in starts if e.tool_call_id == parent_id]
        parent_ends = [e for e in ends if e.tool_call_id == parent_id]
        child_starts = [
            e for e in starts
            if e.parent_call_id == parent_id and e.tool_name == "inspect_spreadsheet"
        ]
        child_ends = [
            e for e in ends
            if e.parent_call_id == parent_id and e.tool_name == "inspect_spreadsheet"
        ]
        assert parent_starts and parent_ends
        assert child_starts and child_ends
        assert all(e.parent_call_id == parent_id for e in child_starts)
        assert all(e.parent_call_id == parent_id for e in child_ends)
        child_end_idx = live.index(child_ends[0])
        parent_end_idx = live.index(parent_ends[0])
        assert child_end_idx < parent_end_idx

        sse_children = []
        for event in child_starts + child_ends:
            text = sse_event_to_sse(event)
            assert text is not None
            payload = json.loads(text.split("data:", 1)[1].strip())
            sse_children.append(payload)
            assert payload["parent_call_id"] == parent_id

        timeline = reconstruct_tool_call_timeline(log.events)
        roots = [n for n in timeline if n["tool_call_id"] == parent_id]
        assert len(roots) == 1
        nested = roots[0]["children"]
        assert any(c["tool_name"] == "inspect_spreadsheet" for c in nested)
        assert all(c["parent_call_id"] == parent_id for c in nested)
        assert all(c["started"] and c["ended"] for c in nested)
        assert all(
            c["seq_end"] is not None
            and roots[0]["seq_end"] is not None
            and c["seq_end"] < roots[0]["seq_end"]
            for c in nested
        )
        # UI 投影：子调用挂在父下，不是独立根卡片。
        root_names = [n["tool_name"] for n in timeline]
        assert "inspect_spreadsheet" not in root_names

        msgs = engine.memory.get_messages(["sys"])
        tool_msgs = [m for m in msgs if m.get("role") == "tool"]
        assert all(m.get("tool_call_id") == parent_id for m in tool_msgs)
        assistant_tcs = []
        for m in msgs:
            if m.get("role") == "assistant":
                assistant_tcs.extend(m.get("tool_calls") or [])
        assert all(
            (tc.get("function") or {}).get("name") == "run_code"
            for tc in assistant_tcs
        )
        wire = engine.memory.project_for_request(["sys"])
        blob = json.dumps(wire, ensure_ascii=False)
        assert "row-15-secret-matrix" not in blob
        assert "inspect_spreadsheet" not in [
            (tc.get("function") or {}).get("name")
            for m in wire if m.get("role") == "assistant"
            for tc in (m.get("tool_calls") or [])
        ]

