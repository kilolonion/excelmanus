"""ToolRuntime 流水线、统一调用和并行分类。"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from excelmanus.engine_core.tool_result import (
    ToolError,
    ToolResult,
    ToolUiMeta,
    error_result,
    finalize_content,
)
from excelmanus.engine_types import ToolCallResult
from excelmanus.tools.policy import is_concurrency_safe
from excelmanus.tools.runtime import ToolRuntime


def _tc(name: str, arguments: dict | None = None, *, call_id: str = "c1", parent: str | None = None):
    tc = SimpleNamespace(
        id=call_id,
        parent_call_id=parent,
        function=SimpleNamespace(name=name, arguments=arguments or {}),
    )
    return tc


def _runtime(*, execute: AsyncMock | None = None) -> ToolRuntime:
    dispatcher = SimpleNamespace(
        parse_arguments=lambda raw: (raw if isinstance(raw, dict) else {}, None),
        execute=execute or AsyncMock(),
    )
    engine = SimpleNamespace(
        _mcp_manager=None,
        config=SimpleNamespace(tool_result_hard_cap_chars=0),
    )
    return ToolRuntime(dispatcher, engine=engine)


class TestUnifiedExecution:
    @pytest.mark.asyncio
    async def test_direct_edit_reaches_dispatcher(self) -> None:
        execute = AsyncMock(return_value=ToolCallResult(
            tool_name="apply_spreadsheet_changes", arguments={"file_path": "a.xlsx"},
            result="permission denied", success=False, error="PERMISSION_DENIED",
            structured=error_result("permission denied", code="PERMISSION_DENIED"),
        ))
        runtime = _runtime(execute=execute)
        result = await runtime.execute(
            _tc("apply_spreadsheet_changes", {"file_path": "a.xlsx"}),
            None,
            None,
            1,
        )
        assert result.success is False
        assert result.error == "PERMISSION_DENIED"
        execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_sdk_subcall_carries_parent(self) -> None:
        execute = AsyncMock(
            return_value=ToolCallResult(
                tool_name="apply_spreadsheet_changes",
                arguments={"file_path": "a.xlsx"},
                result='{"status":"success"}',
                success=True,
                structured=ToolResult(
                    success=True,
                    model_text='{"status":"success"}',
                    value={
                        "status": "success",
                        "file_path": "a.xlsx",
                        "content_version": "v1",
                        "schema_version": "workbook/2", "files": [], "receipt": {}, "committed": True,
                        "applied": [],
                    },
                ),
            )
        )
        runtime = _runtime(execute=execute)
        tc = _tc("apply_spreadsheet_changes", {"file_path": "a.xlsx"}, parent="run_1")
        result = await runtime.execute(tc, None, None, 1)
        assert result.success is True
        execute.assert_awaited_once()
        passed_tc = execute.await_args.args[0]
        assert getattr(passed_tc, "parent_call_id", None) == "run_1"

    @pytest.mark.asyncio
    async def test_full_access_auto_allows_pre_execute_ask(self) -> None:
        payload = {
            "status": "success",
            "command": "curl https://example.com",
            "return_code": 0,
        }
        dispatched = ToolCallResult(
            tool_name="run_shell",
            arguments={"command": "curl https://example.com"},
            result=json.dumps(payload),
            success=True,
            structured=ToolResult(
                success=True,
                model_text=json.dumps(payload),
                value=payload,
            ),
        )
        execute = AsyncMock(return_value=dispatched)
        runtime = _runtime(execute=execute)
        runtime.engine._full_access_enabled = True
        runtime.add_pre_execute(lambda _token: "ask")
        runtime._one_shot_ask = AsyncMock(
            side_effect=AssertionError("跳过审批不应创建交互"),
        )

        result = await runtime.execute(
            _tc("run_shell", {"command": "curl https://example.com"}),
            None,
            None,
            1,
        )

        assert result.success is True
        runtime._one_shot_ask.assert_not_awaited()
        execute.assert_awaited_once()


class TestConcurrency:
    def test_mutations_are_exclusive_including_run_code(self) -> None:
        assert is_concurrency_safe("observe_spreadsheet", {"file_path": "a.xlsx"}) is True
        assert is_concurrency_safe("apply_spreadsheet_changes", {"file_path": "a.xlsx"}) is False
        assert is_concurrency_safe("apply_spreadsheet_changes", {"file_path": "a.xlsx"}) is False
        assert is_concurrency_safe("run_code", {"code": "print(1)"}) is False
        assert is_concurrency_safe("", {}) is False

    def test_unknown_or_throwing_classifier_is_exclusive(self) -> None:
        assert is_concurrency_safe("not_a_real_tool") is False
        from excelmanus.tools import policy as policy_mod

        original = policy_mod.MUTATING_ALL_TOOLS
        try:
            class _Boom:
                def __contains__(self, _key):  # noqa: ANN001
                    raise RuntimeError("boom")

            policy_mod.MUTATING_ALL_TOOLS = _Boom()  # type: ignore[assignment]
            assert is_concurrency_safe("observe_spreadsheet") is False
        finally:
            policy_mod.MUTATING_ALL_TOOLS = original

    def test_split_batches_keeps_mutations_serial(self) -> None:
        runtime = _runtime()
        calls = [
            _tc("apply_spreadsheet_changes", {"file_path": "a.xlsx"}, call_id="1"),
            _tc("apply_spreadsheet_changes", {"file_path": "a.xlsx"}, call_id="2"),
            _tc("observe_spreadsheet", {"file_path": "a.xlsx"}, call_id="3"),
            _tc("observe_spreadsheet", {"file_path": "b.xlsx"}, call_id="4"),
        ]
        batches = runtime.split_batches(calls)
        assert [([c.function.name for c in b.tool_calls], b.parallel) for b in batches] == [
            (["apply_spreadsheet_changes"], False),
            (["apply_spreadsheet_changes"], False),
            (["observe_spreadsheet", "observe_spreadsheet"], True),
        ]

    def test_execution_graph_honors_explicit_dependency(self) -> None:
        runtime = _runtime()
        calls = [
            _tc("observe_spreadsheet", {"file_path": "a.xlsx"}, call_id="read"),
            _tc("analyze_spreadsheet", {"file_path": "b.xlsx", "depends_on": ["read"]}, call_id="analyze"),
            _tc("observe_spreadsheet", {"file_path": "c.xlsx"}, call_id="independent"),
        ]

        batches = runtime.build_execution_batches(calls)

        assert [[call.id for call in batch.tool_calls] for batch in batches] == [
            ["read", "independent"], ["analyze"],
        ]
        assert batches[0].parallel is True

    def test_execution_graph_serializes_mutation_and_overlapping_read(self) -> None:
        runtime = _runtime()
        calls = [
            _tc("observe_spreadsheet", {"file_path": "a.xlsx"}, call_id="read"),
            _tc("apply_spreadsheet_changes", {"file_path": "a.xlsx"}, call_id="write"),
            _tc("observe_spreadsheet", {"file_path": "b.xlsx"}, call_id="other"),
        ]

        batches = runtime.build_execution_batches(calls)

        assert [[call.id for call in batch.tool_calls] for batch in batches] == [
            ["read"], ["write"], ["other"],
        ]
        assert all(batch.parallel is False for batch in batches)

    def test_reclassify_before_start_serializes_flipped_batch(self) -> None:
        runtime = _runtime()
        calls = [
            _tc("observe_spreadsheet", {"file_path": "a.xlsx"}, call_id="1"),
            _tc("observe_spreadsheet", {"file_path": "b.xlsx"}, call_id="2"),
        ]
        runtime.is_concurrency_safe = lambda name, args=None: False  # type: ignore[method-assign]
        batches = runtime.reclassify_batch(calls)
        assert all(not batch.parallel for batch in batches)
        assert len(batches) == 2

    def test_mcp_auto_approve_is_not_parallel(self) -> None:
        runtime = _runtime()
        runtime.engine._mcp_manager = SimpleNamespace(
            auto_approved_tools=["mcp_excel_write"],
        )
        runtime.engine.get_tool_write_effect = lambda _name: "unknown"
        assert runtime.is_concurrency_safe("mcp_excel_write", {}) is False

    def test_mcp_default_allow_is_not_parallel(self) -> None:
        runtime = _runtime()
        runtime.engine.get_tool_write_effect = lambda _name: "unknown"
        assert runtime.is_concurrency_safe("mcp_context7_query_docs", {}) is False

    def test_mcp_readonly_effect_still_not_parallel_unless_listed(self) -> None:
        runtime = _runtime()
        runtime.engine.get_tool_write_effect = lambda _name: "none"
        assert runtime.is_concurrency_safe("mcp_exa_web_search", {}) is False


class TestFinalizeContent:
    def test_magic_fields_leave_model_text(self) -> None:
        raw = {
            "status": "success",
            "__tool_result_image__": {"base64": "abc", "mime_type": "image/png"},
            "_file_download": {"file_path": "a.txt"},
            "content_version": "sha256:abc",
        }
        result = finalize_content(
            ToolResult(
                success=True,
                model_text=json.dumps(raw),
                value=dict(raw),
            )
        )
        assert "__tool_result_image__" not in result.model_text
        assert "_file_download" not in result.model_text
        assert "sha256:abc" in result.model_text

    def test_failed_script_keeps_committed_content_version(self) -> None:
        result = finalize_content(
            ToolResult(
                success=False,
                model_text=json.dumps({"status": "error", "message": "boom"}),
                value={
                    "status": "error",
                    "message": "boom",
                    "sdk_calls": {
                        "writes": [
                            {"tool": "apply_spreadsheet_changes", "content_version": "sha256:committed"}
                        ]
                    },
                },
                error=ToolError(code="SCRIPT_ERROR", message="boom"),
                ui_meta=ToolUiMeta(content_version="sha256:committed"),
            )
        )
        assert result.success is False
        assert "sha256:committed" in result.model_text
