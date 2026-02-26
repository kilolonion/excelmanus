"""ToolDispatcher.execute 行为锁定测试。"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from excelmanus.config import ExcelManusConfig
from excelmanus.engine import AgentEngine, DelegateSubagentOutcome
from excelmanus.events import EventType
from excelmanus.skillpacks import Skillpack
from excelmanus.subagent.models import SubagentFileChange, SubagentResult
from excelmanus.tools import ToolRegistry
from excelmanus.tools.registry import ToolDef


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


def _make_registry() -> ToolRegistry:
    registry = ToolRegistry()

    def add_numbers(a: int, b: int) -> int:
        return a + b

    registry.register_tool(
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
    return registry


def _make_engine(**config_overrides) -> AgentEngine:
    return AgentEngine(_make_config(**config_overrides), _make_registry())


class TestToolDispatcherExecute:
    @pytest.mark.asyncio
    async def test_execute_parse_error_emits_start_and_end_events(self) -> None:
        engine = _make_engine()
        dispatcher = engine._tool_dispatcher
        events = []

        tc = SimpleNamespace(
            id="call_bad_json",
            function=SimpleNamespace(name="add_numbers", arguments="{bad json"),
        )

        result = await dispatcher.execute(
            tc=tc,
            tool_scope=["add_numbers"],
            on_event=events.append,
            iteration=1,
            route_result=None,
        )

        assert result.success is False
        assert "工具参数解析错误" in result.result
        assert len(events) >= 2
        assert events[0].event_type == EventType.TOOL_CALL_START
        assert events[-1].event_type == EventType.TOOL_CALL_END
        assert events[-1].success is False

    @pytest.mark.asyncio
    async def test_execute_dispatches_via_handlers_instead_of_legacy_if_chain(self) -> None:
        from excelmanus.engine_core.tool_dispatcher import _ToolExecOutcome

        engine = _make_engine()
        dispatcher = engine._tool_dispatcher
        dispatcher._dispatch_via_handlers = AsyncMock(
            return_value=_ToolExecOutcome(result_str="handler ok", success=True)
        )
        dispatcher._dispatch_tool_execution = AsyncMock(
            side_effect=AssertionError("legacy dispatch path should not be called")
        )

        tc = SimpleNamespace(
            id="call_dispatch_via_handlers",
            function=SimpleNamespace(
                name="add_numbers",
                arguments=json.dumps({"a": 1, "b": 2}),
            ),
        )

        result = await dispatcher.execute(
            tc=tc,
            tool_scope=["add_numbers"],
            on_event=None,
            iteration=1,
            route_result=None,
        )

        assert result.success is True
        assert result.result == "handler ok"
        dispatcher._dispatch_via_handlers.assert_awaited_once()
        dispatcher._dispatch_tool_execution.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_execute_post_tool_hook_deny_turns_success_to_failure(self) -> None:
        engine = _make_engine()
        dispatcher = engine._tool_dispatcher
        engine._active_skills = [
            Skillpack(
                name="hook/post_deny",
                description="post deny",
                instructions="",
                source="project",
                root_dir="/tmp/hook",
                hooks={
                    "PostToolUse": [
                        {
                            "matcher": "add_numbers",
                            "hooks": [
                                {
                                    "type": "prompt",
                                    "decision": "deny",
                                    "reason": "post blocked",
                                }
                            ],
                        }
                    ]
                },
            )
        ]

        tc = SimpleNamespace(
            id="call_post_hook_deny",
            function=SimpleNamespace(name="add_numbers", arguments=json.dumps({"a": 2, "b": 3})),
        )

        result = await dispatcher.execute(
            tc=tc,
            tool_scope=["add_numbers"],
            on_event=None,
            iteration=1,
            route_result=None,
        )

        assert result.success is False
        assert "[Hook 拒绝] post blocked" in result.result

    @pytest.mark.asyncio
    async def test_execute_run_code_red_creates_pending_approval_and_event(self) -> None:
        engine = _make_engine(code_policy_enabled=True)
        dispatcher = engine._tool_dispatcher
        events = []

        tc = SimpleNamespace(
            id="call_run_code_red",
            function=SimpleNamespace(
                name="run_code",
                arguments=json.dumps({"code": "import subprocess\nsubprocess.run(['echo', 'x'])"}),
            ),
        )

        result = await dispatcher.execute(
            tc=tc,
            tool_scope=None,
            on_event=events.append,
            iteration=1,
            route_result=None,
        )

        assert result.success is True
        assert result.pending_approval is True
        assert isinstance(result.approval_id, str) and result.approval_id
        assert engine._approval.pending is not None
        assert engine._approval.pending.approval_id == result.approval_id
        assert any(event.event_type == EventType.PENDING_APPROVAL for event in events)

    @pytest.mark.asyncio
    async def test_execute_delegate_to_subagent_write_propagation(self) -> None:
        engine = _make_engine()
        dispatcher = engine._tool_dispatcher
        engine._has_write_tool_call = False

        sub_result = SubagentResult(
            success=True,
            summary="ok",
            subagent_name="writer",
            permission_mode="default",
            conversation_id="sub_1",
            structured_changes=[
                SubagentFileChange(path="outputs/test.xlsx", tool_name="write_excel")
            ],
        )
        outcome = DelegateSubagentOutcome(
            reply="子代理已写入",
            success=True,
            picked_agent="writer",
            task_text="写入测试",
            subagent_result=sub_result,
        )
        engine._delegate_to_subagent = AsyncMock(return_value=outcome)

        tc = SimpleNamespace(
            id="call_delegate",
            function=SimpleNamespace(
                name="delegate_to_subagent",
                arguments=json.dumps({"task": "写入测试", "agent_name": "writer"}),
            ),
        )

        result = await dispatcher.execute(
            tc=tc,
            tool_scope=None,
            on_event=None,
            iteration=1,
            route_result=None,
        )

        assert result.success is True
        assert result.result == "子代理已写入"
        assert engine._has_write_tool_call is True

    @pytest.mark.asyncio
    async def test_execute_unknown_tool_workspace_probe_marks_write_state(self, tmp_path: Path) -> None:
        engine = _make_engine(workspace_root=str(tmp_path))
        dispatcher = engine._tool_dispatcher
        engine._has_write_tool_call = False
        engine._registry_refresh_needed = False

        def unknown_writer(path: str) -> str:
            target = tmp_path / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("probe", encoding="utf-8")
            return "ok"

        engine._registry.register_tool(
            ToolDef(
                name="unknown_writer",
                description="unknown write tool",
                input_schema={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                    "additionalProperties": False,
                },
                func=unknown_writer,
                write_effect="unknown",
            )
        )

        tc = SimpleNamespace(
            id="call_unknown_writer",
            function=SimpleNamespace(
                name="unknown_writer",
                arguments=json.dumps({"path": "out/probe.txt"}),
            ),
        )

        result = await dispatcher.execute(
            tc=tc,
            tool_scope=["unknown_writer"],
            on_event=None,
            iteration=1,
            route_result=None,
        )

        assert result.success is True
        assert engine._has_write_tool_call is True
        assert engine._registry_refresh_needed is True
