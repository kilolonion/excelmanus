"""compose_child + InProcessDriver：组合窗口、只读拒绝、run_code 写入拒绝。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from excelmanus.config import ExcelManusConfig
from excelmanus.engine import AgentEngine
from excelmanus.subagent.child import compose_child, compose_child_prompt, resolve_child_runtime
from excelmanus.subagent.driver import InProcessDriver
from excelmanus.subagent.errors import SubagentError
from excelmanus.subagent.guard import reject_readonly_write
from excelmanus.subagent.models import SubagentConfig, SubagentDescriptor, SubagentStartRequest
from excelmanus.tools.registry import ToolDef, ToolRegistry


def _make_config(**overrides) -> ExcelManusConfig:
    defaults = {
        "api_key": "test-key",
        "base_url": "https://test.example.com/v1",
        "model": "test-model",
        "max_iterations": 8,
        "max_consecutive_failures": 3,
        "workspace_root": str(Path(__file__).resolve().parent),
    }
    defaults.update(overrides)
    return ExcelManusConfig(**defaults)


def _text_response(content: str):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=None))],
    )


def _tool_response(call_id: str, name: str, arguments: str = "{}"):
    tc = SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=arguments))
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="", tool_calls=[tc]))],
    )


def test_resolve_child_runtime_uses_parent_active() -> None:
    parent = SimpleNamespace(
        _active_model="main-b",
        _active_api_key="k",
        _active_base_url="https://alt.example.com/v1",
    )
    cfg = SubagentConfig(name="explorer", description="x")
    model, key, url = resolve_child_runtime(parent, cfg)
    assert (model, key, url) == ("main-b", "k", "https://alt.example.com/v1")


def test_compose_child_prompt_has_delegation_lock() -> None:
    parent = SimpleNamespace(_prompt_composer=None, _runtime_vars={})
    text = compose_child_prompt(parent, SubagentConfig(name="x", description="探查"))
    assert "探查" in text
    assert "不能扩大" in text
    assert "最多重试" not in text


def test_explorer_write_guard_rejects_mutating_and_nested_writes() -> None:
    cfg = SubagentConfig(name="explorer", description="x", permission_mode="readOnly")
    assert reject_readonly_write(cfg, "edit_spreadsheet")
    assert reject_readonly_write(cfg, "copy_file")
    assert reject_readonly_write(cfg, "run_code") is None
    assert reject_readonly_write(cfg, "inspect_spreadsheet") is None
    assert reject_readonly_write(
        cfg, "manage_spreadsheet_versions", arguments={"action": "list"}
    ) is None
    assert reject_readonly_write(
        cfg, "manage_spreadsheet_versions", arguments={"action": "restore"}
    )
    assert reject_readonly_write(cfg, "edit_spreadsheet", parent_call="run_code")
    assert reject_readonly_write(cfg, "run_code", parent_call="run_code")


def test_compose_child_plan_parent_cannot_escalate_to_write(tmp_path: Path) -> None:
    registry = ToolRegistry()
    registry.register_builtin_tools(str(tmp_path))
    parent = AgentEngine(_make_config(workspace_root=str(tmp_path)), registry)
    parent._current_chat_mode = "plan"
    cfg = SubagentConfig(name="subagent", description="x", permission_mode="acceptEdits")
    child = compose_child(parent, cfg)
    assert child._current_chat_mode == "plan"
    assert child._fixed_capability.catalog_mode == "plan"
    assert "edit_spreadsheet" not in child.registry._tools
    assert "inspect_spreadsheet" in child.registry._tools


def test_compose_child_uses_child_role() -> None:
    parent = AgentEngine(_make_config(), ToolRegistry())
    cfg = SubagentConfig(
        name="explorer",
        description="x",
        allowed_tools=["inspect_spreadsheet"],
        permission_mode="readOnly",
    )
    child = compose_child(parent, cfg)
    assert child._session_role == "child"
    assert child._is_host_session is False
    assert child._delegation_depth == 1
    assert child._subagent_enabled is False
    assert child._approval is parent._approval
    assert child._driver.inbox is not parent._driver.inbox
    assert "delegate" not in child.registry._tools
    assert "task_create" not in child.registry._tools


def test_compose_child_depth_exceeded() -> None:
    parent = AgentEngine(_make_config(), ToolRegistry())
    parent._delegation_depth = 1
    cfg = SubagentConfig(name="explorer", description="x", permission_mode="readOnly")
    with pytest.raises(SubagentError) as exc:
        compose_child(parent, cfg)
    assert exc.value.code == "DEPTH_EXCEEDED"


@pytest.mark.asyncio
async def test_missing_agent_raises_not_found() -> None:
    parent = AgentEngine(_make_config(), ToolRegistry())
    with pytest.raises(SubagentError) as exc:
        await parent._subagent_runtime.start(
            SubagentStartRequest(task="x", agent_name="nope")
        )
    assert exc.value.code == "NOT_FOUND"


@pytest.mark.asyncio
async def test_readonly_child_rejects_write(tmp_path: Path) -> None:
    config = _make_config(workspace_root=str(tmp_path))
    registry = ToolRegistry()

    def copy_file(source: str, destination: str) -> str:
        return f"copied {source} -> {destination}"

    registry.register_tool(
        ToolDef(
            name="copy_file",
            description="copy",
            input_schema={
                "type": "object",
                "properties": {
                    "source": {"type": "string"},
                    "destination": {"type": "string"},
                },
                "required": ["source", "destination"],
            },
            func=copy_file,
        )
    )
    parent = AgentEngine(config, registry)
    parent._subagent_registry.get = lambda name: SubagentConfig(  # type: ignore[method-assign]
        name="explorer",
        description="x",
        allowed_tools=["copy_file"],
        permission_mode="readOnly",
        max_iterations=2,
        max_consecutive_failures=1,
    )
    fake_create = AsyncMock(
        side_effect=[
            _tool_response(
                "c1",
                "copy_file",
                '{"source":"a.xlsx","destination":"b.xlsx"}',
            ),
            _text_response("无法复制"),
        ]
    )
    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create))
    )
    with patch(
        "excelmanus.engine_core.llm_client_manager.create_client",
        return_value=fake_client,
    ):
        run = await parent._subagent_runtime.start(
            SubagentStartRequest(task="复制文件", agent_name="explorer")
        )
        result = await run.result
    assert result.success is False
    assert result.stop_reason == "refusal"
    assert result.error is not None
    assert "拒绝" in result.error


@pytest.mark.asyncio
async def test_driver_timeout_maps_aborted() -> None:
    cfg = SubagentConfig(name="explorer", description="x", permission_mode="readOnly")
    descriptor = SubagentDescriptor(run_id="run-1", agent_name="explorer")

    class _Hang:
        async def kick(self) -> None:
            await asyncio.sleep(10)

        def enqueue_followup(self, *_args, **_kwargs):
            return SimpleNamespace(result=None)

    child = SimpleNamespace(_driver=_Hang())
    driver = InProcessDriver()
    result = await driver.run(
        SimpleNamespace(),
        cfg,
        prompt="x",
        descriptor=descriptor,
        timeout=0.05,
        child=child,
    )
    assert result.stop_reason == "aborted"
    assert "超时" in (result.diagnostic or "")
