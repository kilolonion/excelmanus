"""P1：一轮任务结束行为与 finish_task 报告契约。

覆盖：纯文本直接结束、无写入不追加警告、真实工具循环 + 假 LLM、
finish_task 字段（outputs/warnings/incomplete）、主循环外无额外 LLM、
权限拒绝 / 取消 / 审批等待仍有效、旧 finish_task 参数兼容。
写入追踪与 chat_mode 工具过滤仍保留（硬边界，不是启发式猜测）。
"""

from __future__ import annotations

import asyncio
import json
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from excelmanus.config import ExcelManusConfig
from excelmanus.engine import AgentEngine, ChatResult, ToolCallResult
from excelmanus.skillpacks.models import SkillMatchResult
from excelmanus.tools.registry import ToolDef, ToolRegistry


# ── helpers ──────────────────────────────────────────────────

def _make_config(**overrides) -> ExcelManusConfig:
    defaults = {
        "api_key": "test-key",
        "base_url": "https://test.example.com/v1",
        "model": "test-model",
        "max_iterations": 20,
        "max_consecutive_failures": 3,
        "workspace_root": str(Path(__file__).resolve().parent),
        "backup_enabled": False,
    }
    defaults.update(overrides)
    return ExcelManusConfig(**defaults)


def _make_engine(**overrides) -> AgentEngine:
    """构建最小化 AgentEngine 实例用于单元测试。"""
    cfg = _make_config(
        **{k: v for k, v in overrides.items() if k in ExcelManusConfig.__dataclass_fields__}
    )
    registry = ToolRegistry()
    return AgentEngine(config=cfg, registry=registry)


def _make_route_result(**kwargs) -> SkillMatchResult:
    defaults = dict(
        skills_used=[],
        route_mode="all_tools",
        system_contexts=[],
    )
    defaults.update(kwargs)
    return SkillMatchResult(**defaults)


def _make_text_response(content: str) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=content, tool_calls=None))]
    )


def _make_tool_call_response(
    tool_calls: list[tuple[str, str, str]],
    content: str = "",
) -> types.SimpleNamespace:
    tc_objects = []
    for call_id, name, args in tool_calls:
        tc_objects.append(
            types.SimpleNamespace(
                id=call_id,
                function=types.SimpleNamespace(name=name, arguments=args),
            )
        )
    return types.SimpleNamespace(
        choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=content, tool_calls=tc_objects))]
    )


def _register_add_numbers(engine: AgentEngine) -> None:
    def add_numbers(a: int, b: int) -> int:
        return a + b

    engine._registry.register_tool(
        ToolDef(
            name="add_numbers",
            description="两数相加",
            input_schema={
                "type": "object",
                "properties": {
                    "a": {"type": "integer"},
                    "b": {"type": "integer"},
                },
                "required": ["a", "b"],
            },
            func=add_numbers,
        )
    )


# ── 纯文本结束 / 无写入警告 ──────────────────────────────────


class TestPlainTextEndsRound:
    """纯文本回复一律结束本轮，不因内容形态被拦或强制续跑。"""

    @pytest.mark.asyncio
    async def test_plain_text_ends_without_extra_llm(self) -> None:
        engine = _make_engine(max_iterations=5)
        route_result = _make_route_result()
        text = "我已经分析完数据了，结果如下：总计 100 条记录。"
        create_mock = AsyncMock(side_effect=[_make_text_response(text)])
        engine._client.chat.completions.create = create_mock

        result = await engine._tool_calling_loop(route_result, on_event=None)

        assert result.reply == text
        assert result.iterations == 1
        assert result.write_guard_triggered is False
        assert "✅ 任务完成" not in result.reply
        assert create_mock.await_count <= 2

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "text",
        [
            "请用公式 =SUM(A1:A2)",
            "好的，请直接把图片上传到当前会话里。",
            "请问你要处理哪个文件？",
            "VBA 宏可以这样写：Sub Demo()",
        ],
    )
    async def test_content_shape_does_not_block_or_rerun(self, text: str) -> None:
        engine = _make_engine(max_iterations=3)
        create_mock = AsyncMock(side_effect=[_make_text_response(text)])
        engine._client.chat.completions.create = create_mock

        result = await engine._tool_calling_loop(_make_route_result(), on_event=None)

        assert result.reply == text
        assert result.iterations == 1
        assert create_mock.await_count <= 2
        user_messages = [
            str(m.get("content", ""))
            for m in engine.memory.get_messages()
            if m.get("role") == "user"
        ]
        for kw in ("公式或代码建议", "写入工具", "尚未调用任何写入工具", "强制继续"):
            assert all(kw not in msg for msg in user_messages)

    @pytest.mark.asyncio
    async def test_no_write_ends_without_warning_or_second_call(self) -> None:
        """无写入也能正常结束，不追加警告、不二次调用。"""
        engine = _make_engine(max_iterations=3)
        engine._has_write_tool_call = False
        text = "分析完成，数据如下..."
        create_mock = AsyncMock(side_effect=[_make_text_response(text)])
        engine._client.chat.completions.create = create_mock

        result = await engine._tool_calling_loop(_make_route_result(), on_event=None)

        assert result.reply == text
        assert result.iterations == 1
        assert create_mock.await_count <= 2
        assert "警告" not in result.reply
        assert result.write_guard_triggered is False


class TestHtmlEndpointStillRejected:
    """HTML 整页响应仍视为 LLM 客户端配置错误，不是行为判断。"""

    @pytest.mark.asyncio
    async def test_html_document_returns_config_error(self) -> None:
        engine = _make_engine()
        html = "<!DOCTYPE html><html><head><title>Login</title></head><body>ok</body></html>"
        engine._client.chat.completions.create = AsyncMock(side_effect=[_make_text_response(html)])

        result = await engine._tool_calling_loop(_make_route_result(), on_event=None)

        assert result.iterations == 1
        assert html not in result.reply
        assert result.reply  # 应替换为配置错误提示


# ── finish_task 报告契约 ──────────────────────────────────


class TestFinishTaskReportContract:
    @pytest.mark.asyncio
    async def test_handler_partial_fields(self) -> None:
        from excelmanus.engine_core.tool_handlers import FinishTaskHandler

        engine = _make_engine()
        handler = FinishTaskHandler(engine, engine._tool_dispatcher)
        outcome = await handler.handle(
            "finish_task",
            "tc_finish",
            {
                "status": "partial",
                "summary": "只写完汇总表",
                "outputs": [
                    {
                        "path": "report.xlsx",
                        "changed_ranges": ["客户汇总!A1:B10"],
                        "content_version": "abc123",
                    }
                ],
                "warnings": ["公式缓存值未重算"],
                "incomplete": ["Sheet1 VLOOKUP 未写入"],
            },
        )
        assert outcome.finish_accepted is True
        assert outcome.success is True
        assert "任务部分完成" in outcome.result_str
        assert "✅ 任务完成" not in outcome.result_str
        assert "report.xlsx" in outcome.result_str
        assert "客户汇总!A1:B10" in outcome.result_str
        assert "abc123" in outcome.result_str
        assert "公式缓存值未重算" in outcome.result_str
        assert "Sheet1 VLOOKUP 未写入" in outcome.result_str

    @pytest.mark.asyncio
    async def test_handler_stopped_status(self) -> None:
        from excelmanus.engine_core.tool_handlers import FinishTaskHandler

        engine = _make_engine()
        handler = FinishTaskHandler(engine, engine._tool_dispatcher)
        outcome = await handler.handle(
            "finish_task",
            "tc_stop",
            {
                "status": "stopped",
                "summary": "源文件缺失，已停止",
                "incomplete": ["未生成报表"],
            },
        )
        assert "任务已停止，尚未完成" in outcome.result_str
        assert "✅ 任务完成" not in outcome.result_str

    @pytest.mark.asyncio
    async def test_incomplete_infers_partial(self) -> None:
        from excelmanus.engine_core.tool_handlers import FinishTaskHandler

        engine = _make_engine()
        handler = FinishTaskHandler(engine, engine._tool_dispatcher)
        outcome = await handler.handle(
            "finish_task",
            "tc_infer",
            {"summary": "做了一半", "incomplete": ["第二步未做"]},
        )
        assert "任务部分完成" in outcome.result_str

    @pytest.mark.asyncio
    async def test_summary_and_outputs(self) -> None:
        """finish_task 用 outputs + report 渲染，不再套「✅ 任务完成」。"""
        from excelmanus.engine_core.tool_handlers import FinishTaskHandler

        engine = _make_engine()
        handler = FinishTaskHandler(engine, engine._tool_dispatcher)
        outcome = await handler.handle(
            "finish_task",
            "tc_outputs",
            {
                "summary": "分析完成，无需改文件",
                "outputs": [{"path": "old.xlsx"}],
                "report": {
                    "operations": "读取了 Sheet1",
                    "key_findings": "共 10 行",
                },
            },
        )
        assert outcome.finish_accepted is True
        assert "任务已完成" in outcome.result_str
        assert "✅ 任务完成" not in outcome.result_str
        assert "old.xlsx" in outcome.result_str
        assert "读取了 Sheet1" in outcome.result_str

    def test_schema_has_outputs_warnings_incomplete(self) -> None:
        engine = _make_engine()
        engine._bench_mode = False
        engine._skill_router = None
        tools = engine._meta_tool_builder.build_meta_tools()
        ft = [t for t in tools if t["function"]["name"] == "finish_task"][0]
        props = ft["function"]["parameters"]["properties"]
        assert "outputs" in props
        assert "warnings" in props
        assert "incomplete" in props
        assert "status" in props
        assert "affected_files" not in props

    def test_finish_task_always_present(self) -> None:
        engine = _make_engine()
        engine._skill_router = None
        names = [t["function"]["name"] for t in engine._meta_tool_builder.build_meta_tools()]
        assert "finish_task" in names
        names_v5 = [t["function"]["name"] for t in engine._meta_tool_builder.build_v5_tools()]
        assert "finish_task" in names_v5


# ── 真实工具循环 + 假 LLM ──────────────────────────────────


class TestRealToolLoopFakeLlm:
    """不 mock finish_accepted；走真实 dispatcher + FinishTaskHandler。"""

    @pytest.mark.asyncio
    async def test_add_numbers_then_finish_partial(self) -> None:
        engine = _make_engine(max_iterations=5)
        engine._current_chat_mode = "write"
        _register_add_numbers(engine)
        finish_args = {
            "status": "partial",
            "summary": "只算了加法",
            "outputs": [{"path": "calc.txt", "changed_ranges": []}],
            "warnings": ["未写回工作簿"],
            "incomplete": ["未生成图表"],
        }
        create_mock = AsyncMock(
            side_effect=[
                _make_tool_call_response(
                    [("call_add", "add_numbers", '{"a": 2, "b": 3}')],
                ),
                _make_tool_call_response(
                    [("call_finish", "finish_task", json.dumps(finish_args, ensure_ascii=False))],
                ),
            ]
        )
        engine._client.chat.completions.create = create_mock

        result = await engine._tool_calling_loop(_make_route_result(), on_event=None)

        assert result.iterations == 2
        assert "任务部分完成" in result.reply
        assert "calc.txt" in result.reply
        assert "未写回工作簿" in result.reply
        assert "未生成图表" in result.reply
        assert "✅ 任务完成" not in result.reply
        assert any(tc.tool_name == "add_numbers" and tc.success for tc in result.tool_calls)
        # 主循环外不应再调 LLM（流式失败回退最多 2 次/轮）
        assert create_mock.await_count <= 4
        assert create_mock.await_count >= 2

    @pytest.mark.asyncio
    async def test_no_extra_llm_after_text_round(self) -> None:
        engine = _make_engine(max_iterations=8)
        create_mock = AsyncMock(side_effect=[_make_text_response("本轮结束")])
        engine._client.chat.completions.create = create_mock

        result = await engine._tool_calling_loop(_make_route_result(), on_event=None)

        assert result.iterations == 1
        assert create_mock.await_count <= 2


# ── 硬边界仍然有效 ──────────────────────────────────


class TestHardBoundariesRemain:
    @pytest.mark.asyncio
    async def test_permission_denied_still_blocks(self) -> None:
        engine = _make_engine()
        _register_add_numbers(engine)
        tc = types.SimpleNamespace(
            id="call_denied",
            function=types.SimpleNamespace(
                name="add_numbers",
                arguments='{"a":1,"b":2}',
            ),
        )
        result = await engine._execute_tool_call(
            tc, tool_scope=["read_excel"], on_event=None, iteration=1,
        )
        assert result.success is False
        assert "TOOL_NOT_ALLOWED" in (result.error or result.result or "")

    @pytest.mark.asyncio
    async def test_cancelled_error_propagates(self) -> None:
        engine = _make_engine()

        async def _raise_cancel(*_a, **_k):
            raise asyncio.CancelledError()

        engine._client.chat.completions.create = _raise_cancel
        with pytest.raises(asyncio.CancelledError):
            await engine._tool_calling_loop(_make_route_result(), on_event=None)

    @pytest.mark.asyncio
    async def test_pending_approval_does_not_block_chat(self) -> None:
        engine = _make_engine()
        engine._approval.create_pending(
            tool_name="run_code",
            arguments={"code": "print(1)"},
            tool_scope=[],
        )
        mocked = AsyncMock(return_value=_make_text_response("不应被调用"))
        engine._client.chat.completions.create = mocked

        result = await engine.chat("继续处理")

        assert isinstance(result, ChatResult)
        assert result.reply
        mocked.assert_called()


# ── 旧会话兼容 ──────────────────────────────────


class TestLegacySessionCompat:
    def test_from_dict_ignores_current_write_hint(self) -> None:
        from excelmanus.engine_core.session_state import SessionState

        state = SessionState.from_dict({
            "session_turn": 2,
            "has_write_tool_call": True,
            "current_write_hint": "may_write",
            "stuck_warning_fired": True,
        })
        assert state.has_write_tool_call is True
        assert state.session_turn == 2
        assert not hasattr(state, "current_write_hint") or getattr(
            state, "current_write_hint", None
        ) in (None, "unknown")


# ── 写入追踪（真实副作用，不是猜测） ──────────────────────────


class TestDelegateSubagentWritePropagation:
    @staticmethod
    def _delegate_tc(task: str = "test", agent: str = "writer") -> types.SimpleNamespace:
        return types.SimpleNamespace(
            id="call_delegate",
            function=types.SimpleNamespace(
                name="delegate_to_subagent",
                arguments=f'{{"task":"{task}","agent_name":"{agent}"}}',
            ),
        )

    @staticmethod
    def _make_outcome(*, success: bool, file_changes: list[str]):
        from excelmanus.engine import DelegateSubagentOutcome
        from excelmanus.subagent.models import SubagentFileChange, SubagentResult

        structured = [
            SubagentFileChange(path=p, tool_name="write_excel")
            for p in file_changes
        ]
        sub = SubagentResult(
            success=success,
            summary="test summary",
            subagent_name="subagent",
            permission_mode="default",
            conversation_id="conv_test",
            structured_changes=structured,
        )
        return DelegateSubagentOutcome(
            reply="test reply",
            success=success,
            picked_agent="subagent",
            task_text="test task",
            subagent_result=sub,
        )

    @pytest.mark.asyncio
    async def test_subagent_with_file_changes_propagates_write_state(self) -> None:
        engine = _make_engine()
        engine._has_write_tool_call = False
        outcome = self._make_outcome(success=True, file_changes=["outputs/backups/test.xlsx"])
        with patch.object(engine, "_delegate_to_subagent", return_value=outcome):
            result = await engine._execute_tool_call(
                self._delegate_tc(), tool_scope=None, on_event=None, iteration=1,
            )
        assert result.success is True
        assert engine._has_write_tool_call is True

    @pytest.mark.asyncio
    async def test_subagent_without_file_changes_does_not_propagate(self) -> None:
        engine = _make_engine()
        engine._has_write_tool_call = False
        outcome = self._make_outcome(success=True, file_changes=[])
        with patch.object(engine, "_delegate_to_subagent", return_value=outcome):
            await engine._execute_tool_call(
                self._delegate_tc(agent="analyst"), tool_scope=None, on_event=None, iteration=1,
            )
        assert engine._has_write_tool_call is False

    @pytest.mark.asyncio
    async def test_failed_subagent_does_not_propagate(self) -> None:
        engine = _make_engine()
        engine._has_write_tool_call = False
        outcome = self._make_outcome(success=False, file_changes=["outputs/backups/partial.xlsx"])
        with patch.object(engine, "_delegate_to_subagent", return_value=outcome):
            await engine._execute_tool_call(
                self._delegate_tc(), tool_scope=None, on_event=None, iteration=1,
            )
        assert engine._has_write_tool_call is False


class TestRunCodeWritePropagation:
    @staticmethod
    def _run_code_tc(code: str = "print(1)") -> types.SimpleNamespace:
        return types.SimpleNamespace(
            id="call_run_code",
            function=types.SimpleNamespace(
                name="run_code",
                arguments=json.dumps({"code": code}),
            ),
        )

    @staticmethod
    def _make_audit_record(*, has_changes: bool = True):
        from excelmanus.approval import AppliedApprovalRecord, FileChangeRecord

        changes = []
        if has_changes:
            changes.append(FileChangeRecord(
                path="outputs/test.xlsx",
                before_exists=False, after_exists=True,
                before_hash=None, after_hash="abc123",
                before_size=None, after_size=1024,
                is_binary=True,
            ))
        return AppliedApprovalRecord(
            approval_id="test-001",
            tool_name="run_code",
            arguments={"code": "print(1)"},
            tool_scope=[],
            created_at_utc="2026-01-01T00:00:00Z",
            applied_at_utc="2026-01-01T00:00:01Z",
            undoable=False,
            manifest_file="outputs/approvals/test/manifest.json",
            audit_dir="outputs/approvals/test",
            result_preview='{"status":"success","cow_mapping":{}}',
            changes=changes,
        )

    @pytest.mark.asyncio
    async def test_run_code_with_file_changes(self) -> None:
        engine = _make_engine(code_policy_enabled=True)
        engine._has_write_tool_call = False
        audit_record = self._make_audit_record(has_changes=True)
        with patch.object(
            engine, "_execute_tool_with_audit", new_callable=AsyncMock,
            return_value=('{"status":"success","stdout_tail":"ok","cow_mapping":{}}', audit_record),
        ):
            result = await engine._execute_tool_call(
                self._run_code_tc(), tool_scope=None, on_event=None, iteration=1,
            )
        assert result.success is True
        assert engine._has_write_tool_call is True

    @pytest.mark.asyncio
    async def test_run_code_cow_mapping(self) -> None:
        engine = _make_engine(code_policy_enabled=True)
        engine._has_write_tool_call = False
        audit_record = self._make_audit_record(has_changes=False)
        result_json = (
            '{"status":"success","stdout_tail":"ok",'
            '"cow_mapping":{"bench/external/test.xlsx":"outputs/test.xlsx"}}'
        )
        with patch.object(
            engine, "_execute_tool_with_audit", new_callable=AsyncMock,
            return_value=(result_json, audit_record),
        ):
            result = await engine._execute_tool_call(
                self._run_code_tc(), tool_scope=None, on_event=None, iteration=1,
            )
        assert result.success is True
        assert engine._has_write_tool_call is True

    @pytest.mark.asyncio
    async def test_run_code_without_changes(self) -> None:
        engine = _make_engine(code_policy_enabled=True)
        engine._has_write_tool_call = False
        audit_record = self._make_audit_record(has_changes=False)
        with patch.object(
            engine, "_execute_tool_with_audit", new_callable=AsyncMock,
            return_value=('{"status":"success","stdout_tail":"ok","cow_mapping":{}}', audit_record),
        ):
            result = await engine._execute_tool_call(
                self._run_code_tc(), tool_scope=None, on_event=None, iteration=1,
            )
        assert result.success is True
        assert engine._has_write_tool_call is False

    @pytest.mark.asyncio
    async def test_run_code_ast_wb_save(self) -> None:
        engine = _make_engine(code_policy_enabled=True)
        engine._has_write_tool_call = False
        code_with_save = (
            'from openpyxl import load_workbook\n'
            'wb = load_workbook("output.xlsx")\n'
            'ws = wb.active\n'
            'ws["A1"] = 42\n'
            'wb.save("output.xlsx")\n'
        )
        audit_record = self._make_audit_record(has_changes=False)
        with patch.object(
            engine, "_execute_tool_with_audit", new_callable=AsyncMock,
            return_value=('{"status":"success","stdout_tail":"ok","cow_mapping":{}}', audit_record),
        ):
            result = await engine._execute_tool_call(
                self._run_code_tc(code=code_with_save),
                tool_scope=None, on_event=None, iteration=1,
            )
        assert result.success is True
        assert engine._has_write_tool_call is True


class TestWriteTrackingApis:
    def test_record_workspace_write_action_marks_registry_refresh(self) -> None:
        engine = _make_engine()
        engine._registry_refresh_needed = False
        engine._record_workspace_write_action()
        assert engine._has_write_tool_call is True
        assert engine._registry_refresh_needed is True

    def test_record_external_write_action_does_not_mark_registry_refresh(self) -> None:
        engine = _make_engine()
        engine._registry_refresh_needed = False
        engine._record_external_write_action()
        assert engine._has_write_tool_call is True
        assert engine._registry_refresh_needed is False


class TestRegistryRefreshOnRecordedWrite:
    @pytest.mark.asyncio
    async def test_registry_refresh_triggered_by_record_write_action(self) -> None:
        engine = _make_engine(max_iterations=1)
        first = _make_tool_call_response(
            [("call_1", "delegate_to_subagent", '{"task":"sync files"}')],
        )
        engine._client.chat.completions.create = AsyncMock(side_effect=[first])

        async def _execute_and_record_write(*_a, **_k):
            engine._record_write_action()
            return ToolCallResult(
                tool_name="delegate_to_subagent",
                arguments={"task": "sync files"},
                result="ok",
                success=True,
            )

        engine._execute_tool_call = AsyncMock(side_effect=_execute_and_record_write)
        if engine._file_registry is not None:
            with patch.object(engine._file_registry, "scan_workspace") as scan_mock:
                result = await engine._tool_calling_loop(_make_route_result(), on_event=None)
            assert "已达到最大迭代次数" in result.reply
            scan_mock.assert_called_once()
        else:
            result = await engine._tool_calling_loop(_make_route_result(), on_event=None)
            assert "已达到最大迭代次数" in result.reply


# ── chat_mode 硬边界工具过滤 ──────────────────────────────────


class TestChatModeToolFiltering:
    """read/plan 是用户选定的硬边界，不是对写入意图的猜测。"""

    def _tool_names(self, engine: AgentEngine, chat_mode: str) -> set[str]:
        from excelmanus.engine import _tool_access_from_chat_mode

        engine._current_chat_mode = chat_mode
        access = _tool_access_from_chat_mode(chat_mode)
        tools = engine._meta_tool_builder.build_v5_tools(tool_access=access)
        return {t["function"]["name"] for t in tools}

    def test_write_tools_excluded_in_read(self) -> None:
        engine = _make_engine()
        names = self._tool_names(engine, "read")
        leaked = {"edit_text_file", "write_plan"} & names
        assert not leaked, f"写工具泄漏到只读模式: {leaked}"

    def test_delegate_excluded_in_read(self) -> None:
        engine = _make_engine()
        names = self._tool_names(engine, "read")
        leaked = {"delegate", "delegate_to_subagent", "parallel_delegate"} & names
        assert not leaked, f"delegate 工具泄漏到只读模式: {leaked}"

    def test_read_safe_tools_present(self) -> None:
        engine = _make_engine()
        names = self._tool_names(engine, "read")
        missing = {"ask_user", "finish_task", "suggest_mode_switch"} - names
        assert not missing, f"只读模式缺少必要工具: {missing}"

    def test_delegate_present_in_write(self) -> None:
        engine = _make_engine()
        names = self._tool_names(engine, "write")
        assert "delegate" in names

    def test_plan_mode_allows_write_plan(self) -> None:
        engine = _make_engine()
        names = self._tool_names(engine, "plan")
        assert "write_plan" in names

    def test_plan_mode_blocks_edit_and_delegate(self) -> None:
        engine = _make_engine()
        names = self._tool_names(engine, "plan")
        assert "edit_text_file" not in names
        leaked = {"delegate", "delegate_to_subagent", "parallel_delegate"} & names
        assert not leaked

    def test_read_mode_blocks_write_plan(self) -> None:
        engine = _make_engine()
        names = self._tool_names(engine, "read")
        assert "write_plan" not in names
