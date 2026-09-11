"""子 Driver：共享 loop + restrict + 只读守卫。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from excelmanus.agent.inbox import Inbox
from excelmanus.config import ExcelManusConfig
from excelmanus.engine import AgentEngine, ChatResult
from excelmanus.subagent.child import (
    ChildDriver,
    _compose_child_prompt,
    resolve_child_runtime,
    spawn_child_engine,
    start_child_driver,
)
from excelmanus.subagent.guard import reject_readonly_write
from excelmanus.subagent.models import SubagentConfig, SubagentResult
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
        _config=SimpleNamespace(),
    )
    cfg = SubagentConfig(name="explorer", description="x")
    model, key, url = resolve_child_runtime(parent, cfg)
    assert model == "main-b"
    assert key == "k"
    assert url == "https://alt.example.com/v1"


def test_resolve_child_runtime_uses_explicit_config_model() -> None:
    parent = SimpleNamespace(
        _active_model="main-b",
        _active_api_key="k",
        _active_base_url="https://alt.example.com/v1",
        _config=SimpleNamespace(),
    )
    cfg = SubagentConfig(
        name="explorer",
        description="x",
        model="sub-fixed",
        api_key="sub-k",
        base_url="https://sub.example.com/v1",
    )
    model, key, url = resolve_child_runtime(parent, cfg)
    assert model == "sub-fixed"
    assert key == "sub-k"
    assert url == "https://sub.example.com/v1"


def test_child_driver_has_own_inbox() -> None:
    parent = AgentEngine(_make_config(), ToolRegistry())
    child = ChildDriver(parent, agent_name="explorer")
    assert isinstance(child.inbox, Inbox)


@pytest.mark.asyncio
async def test_missing_agent_fails_loud() -> None:
    parent = AgentEngine(_make_config(), ToolRegistry())
    result = await start_child_driver(parent, task="x", agent_name="nope")
    assert result.success is False
    assert "SubagentNotFound" in (result.error or "")


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
        result = await start_child_driver(parent, task="复制文件", agent_name="explorer")
    assert result.success is False
    assert result.error is not None
    assert "拒绝写入" in result.error


def test_child_fallback_prompt_has_no_retry_ritual() -> None:
    parent = SimpleNamespace(_prompt_composer=None, _runtime_vars={})
    text = _compose_child_prompt(parent, SubagentConfig(name="x", description="探查"))
    assert "最多重试" not in text
    assert "探查" in text


def test_explorer_write_guard_rejects_mutating_tools() -> None:
    cfg = SubagentConfig(name="explorer", description="x", permission_mode="readOnly")
    assert reject_readonly_write(cfg, "edit_spreadsheet")
    assert reject_readonly_write(cfg, "copy_file")
    assert reject_readonly_write(cfg, "run_code") is None
    assert reject_readonly_write(cfg, "inspect_spreadsheet") is None


def test_child_spawn_uses_child_role() -> None:
    parent = AgentEngine(_make_config(), ToolRegistry())
    cfg = SubagentConfig(
        name="explorer",
        description="x",
        allowed_tools=["inspect_spreadsheet"],
        permission_mode="readOnly",
    )
    child = spawn_child_engine(parent, cfg)
    assert child._session_role == "child"
    assert child._is_host_session is False
    assert child._embedding_client is None
    assert child._playbook_store is None
    assert child._prompt_composer is parent._prompt_composer
    assert child._approval is parent._approval
    assert "task_create" not in child.registry._tools
    assert "write_plan" not in child.registry._tools
    assert "introspect_capability" not in child.registry._tools

