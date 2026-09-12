"""Wave C：ToolRuntime 流水线、presentAs 坍缩、并行分类。"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from excelmanus.engine_core.tool_result import (
    ToolError,
    ToolResult,
    ToolUiMeta,
    finalize_content,
)
from excelmanus.engine_types import ToolCallResult
from excelmanus.tools.policy import is_concurrency_safe
from excelmanus.tools.runtime import (
    UNKNOWN_TOOL,
    ToolRuntime,
    collapse_schemas,
    is_direct_call_allowed,
    normalize_present_as,
    present_as_of,
)


def _tc(name: str, arguments: dict | None = None, *, call_id: str = "c1", parent: str | None = None):
    tc = SimpleNamespace(
        id=call_id,
        parent_call_id=parent,
        function=SimpleNamespace(name=name, arguments=arguments or {}),
    )
    return tc


def _runtime(*, present_as: str = "native", execute: AsyncMock | None = None) -> ToolRuntime:
    dispatcher = SimpleNamespace(
        parse_arguments=lambda raw: (raw if isinstance(raw, dict) else {}, None),
        execute=execute or AsyncMock(),
    )
    engine = SimpleNamespace(
        _present_as=present_as,
        _mcp_manager=None,
        config=SimpleNamespace(tool_result_hard_cap_chars=0),
    )
    return ToolRuntime(dispatcher, engine=engine)


class TestPresentAsCollapse:
    def test_catalog_and_executor_share_predicate(self) -> None:
        schemas = [
            {"function": {"name": "edit_spreadsheet"}},
            {"function": {"name": "run_code"}},
            {"function": {"name": "ask_user"}},
        ]
        native = collapse_schemas(schemas, "native")
        code = collapse_schemas(schemas, "code")
        assert {s["function"]["name"] for s in native} == {
            "edit_spreadsheet",
            "run_code",
            "ask_user",
        }
        assert {s["function"]["name"] for s in code} == {"run_code"}
        for name in ("edit_spreadsheet", "run_code", "ask_user"):
            assert is_direct_call_allowed(name, present_as="native", parent=None) == (
                name in {s["function"]["name"] for s in native}
            )
            assert is_direct_call_allowed(name, present_as="code", parent=None) == (
                name in {s["function"]["name"] for s in code}
            )

    @pytest.mark.asyncio
    async def test_code_mode_direct_edit_is_unknown_tool(self) -> None:
        execute = AsyncMock(side_effect=AssertionError("dispatcher must not run"))
        runtime = _runtime(present_as="code", execute=execute)
        result = await runtime.execute(
            _tc("edit_spreadsheet", {"file_path": "a.xlsx"}),
            None,
            None,
            1,
        )
        assert result.success is False
        assert result.error == UNKNOWN_TOOL
        assert result.structured is not None
        assert result.structured.error is not None
        assert result.structured.error.code == UNKNOWN_TOOL
        assert "run_code" in result.result
        assert "edit_spreadsheet" in result.result
        execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_code_mode_subcall_carries_parent(self) -> None:
        execute = AsyncMock(
            return_value=ToolCallResult(
                tool_name="edit_spreadsheet",
                arguments={"file_path": "a.xlsx"},
                result='{"status":"success"}',
                success=True,
                structured=ToolResult(
                    success=True,
                    model_text='{"status":"success"}',
                    value={"status": "success"},
                ),
            )
        )
        runtime = _runtime(present_as="code", execute=execute)
        tc = _tc("edit_spreadsheet", {"file_path": "a.xlsx"}, parent="run_1")
        result = await runtime.execute(tc, None, None, 1)
        assert result.success is True
        execute.assert_awaited_once()
        passed_tc = execute.await_args.args[0]
        assert getattr(passed_tc, "parent_call_id", None) == "run_1"


class TestPresentAsNormalize:
    def test_plan_and_read_force_native(self) -> None:
        assert normalize_present_as("code", chat_mode="write") == "code"
        assert normalize_present_as("both", chat_mode="write") == "code"
        assert normalize_present_as("code", chat_mode="plan") == "native"
        assert normalize_present_as("code", chat_mode="read") == "native"
        assert normalize_present_as("both", chat_mode="plan") == "native"

    def test_present_as_of_respects_chat_mode(self) -> None:
        engine = SimpleNamespace(_present_as="code", _current_chat_mode="plan")
        assert present_as_of(engine) == "native"
        engine._current_chat_mode = "write"
        assert present_as_of(engine) == "code"

    def test_set_present_as_preference_keeps_code_in_plan(self) -> None:
        from excelmanus.tools.runtime import set_present_as_preference

        engine = SimpleNamespace(_present_as="native", _current_chat_mode="plan", _tools_cache=["x"])
        assert set_present_as_preference(engine, "code") == "code"
        assert engine._present_as == "code"
        assert present_as_of(engine) == "native"
        assert engine._tools_cache is None

    def test_present_rejects_both(self) -> None:
        runtime = _runtime(present_as="native")
        with pytest.raises(ValueError, match="unknown present_as"):
            runtime.present("both")
        runtime.present("code")
        assert runtime.engine._present_as == "code"


class TestConcurrency:
    def test_mutations_are_exclusive_including_run_code(self) -> None:
        assert is_concurrency_safe("inspect_spreadsheet", {"file_path": "a.xlsx"}) is True
        assert is_concurrency_safe("edit_spreadsheet", {"file_path": "a.xlsx"}) is False
        assert is_concurrency_safe("format_spreadsheet", {"file_path": "a.xlsx"}) is False
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
            assert is_concurrency_safe("inspect_spreadsheet") is False
        finally:
            policy_mod.MUTATING_ALL_TOOLS = original

    def test_split_batches_keeps_mutations_serial(self) -> None:
        runtime = _runtime()
        calls = [
            _tc("edit_spreadsheet", {"file_path": "a.xlsx"}, call_id="1"),
            _tc("format_spreadsheet", {"file_path": "a.xlsx"}, call_id="2"),
            _tc("inspect_spreadsheet", {"file_path": "a.xlsx"}, call_id="3"),
            _tc("inspect_spreadsheet", {"file_path": "b.xlsx"}, call_id="4"),
        ]
        batches = runtime.split_batches(calls)
        assert [([c.function.name for c in b.tool_calls], b.parallel) for b in batches] == [
            (["edit_spreadsheet"], False),
            (["format_spreadsheet"], False),
            (["inspect_spreadsheet", "inspect_spreadsheet"], True),
        ]

    def test_reclassify_before_start_serializes_flipped_batch(self) -> None:
        runtime = _runtime()
        calls = [
            _tc("inspect_spreadsheet", {"file_path": "a.xlsx"}, call_id="1"),
            _tc("inspect_spreadsheet", {"file_path": "b.xlsx"}, call_id="2"),
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
                            {"tool": "edit_spreadsheet", "content_version": "sha256:committed"}
                        ]
                    },
                },
                error=ToolError(code="SCRIPT_ERROR", message="boom"),
                ui_meta=ToolUiMeta(content_version="sha256:committed"),
            )
        )
        assert result.success is False
        assert "sha256:committed" in result.model_text
