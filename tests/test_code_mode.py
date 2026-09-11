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

        source = render_sdk_source([_read_excel_def()], docker_sandbox=False)
        assert "def read_excel(" in source
        assert "file_path" in source
        assert "sheet_name=None" in source
        assert "读取 Excel 摘要。" in source
        assert "eval(" not in source


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
        source = render_sdk_source([_read_excel_def()], docker_sandbox=False)
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
        source = render_sdk_source([_read_excel_def()], docker_sandbox=False)
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
        source = render_sdk_source([_read_excel_def()], docker_sandbox=False)
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
        source = render_sdk_source([_read_excel_def()], docker_sandbox=False)
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


class TestDockerOffWording:
    def test_sdk_docstring_omits_strong_isolation_when_docker_off(self) -> None:
        from excelmanus.code_mode import render_sdk_source

        source = render_sdk_source([_read_excel_def()], docker_sandbox=False)
        assert "强隔离" not in source
        assert "受限 builtins" in source or "SDK" in source

    def test_run_code_result_note_omits_strong_isolation(self, tmp_path: Path) -> None:
        from excelmanus.code_mode import (
            DOCKER_OFF_DISCLAIMER,
            apply_sdk_calls_summary,
        )
        from excelmanus.code_mode import CodeModeSession

        dispatcher = AsyncMock()
        session = CodeModeSession(
            dispatcher=dispatcher,
            root_call_id="call_note",
            bridge_dir=tmp_path / "bridge",
            docker_sandbox=False,
        )
        raw = json.dumps({"status": "success", "stdout_tail": "ok"})
        merged = apply_sdk_calls_summary(raw, session)
        payload = json.loads(merged)
        assert "强隔离" not in merged
        assert "强隔离" not in DOCKER_OFF_DISCLAIMER
        assert payload.get("sandbox_note") == DOCKER_OFF_DISCLAIMER


class TestWrapperSdkInject:
    def test_wrapper_exposes_em_module(self, tmp_path: Path) -> None:
        from excelmanus.code_mode import render_sdk_source

        sdk_path = tmp_path / "em.py"
        sdk_path.write_text(
            render_sdk_source([_read_excel_def()], docker_sandbox=False),
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
