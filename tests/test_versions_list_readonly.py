"""manage_spreadsheet_versions：list 在只读会话放行，restore/checkpoint 拒绝。"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from excelmanus.config import ExcelManusConfig
from excelmanus.engine import AgentEngine
from excelmanus.tools import ToolRegistry
from excelmanus.tools.policy import write_effect_for_call
from excelmanus.tools.registry import ToolDef


def test_write_effect_list_is_none_restore_stays_write() -> None:
    assert write_effect_for_call(
        "manage_spreadsheet_versions",
        {"action": "list"},
        declared="workspace_write",
    ) == "none"
    assert write_effect_for_call(
        "manage_spreadsheet_versions",
        {"action": "restore"},
        declared="workspace_write",
    ) == "workspace_write"
    assert write_effect_for_call(
        "manage_spreadsheet_versions",
        {"action": "checkpoint"},
        declared="workspace_write",
    ) == "workspace_write"
    assert write_effect_for_call(
        "inspect_spreadsheet",
        {"mode": "range"},
        declared="none",
    ) == "none"


def _make_engine() -> AgentEngine:
    config = ExcelManusConfig(
        api_key="test-key",
        base_url="https://test.example.com/v1",
        model="test-model",
        max_iterations=20,
        max_consecutive_failures=3,
        workspace_root=str(Path(__file__).resolve().parent),
    )
    return AgentEngine(config, ToolRegistry())


@pytest.mark.asyncio
async def test_read_session_can_list_versions_but_not_restore() -> None:
    engine = _make_engine()
    called: list[str] = []

    def versions(action: str = "", **_kwargs: object) -> str:
        called.append(str(action))
        return json.dumps({"status": "success", "file_path": "book.xlsx", "revisions": []})

    engine._registry.register_tool(
        ToolDef(
            name="manage_spreadsheet_versions",
            description="versions",
            input_schema={"type": "object", "properties": {"action": {"type": "string"}}},
            func=versions,
            write_effect="workspace_write",
        )
    )
    engine._current_chat_mode = "read"

    list_tc = SimpleNamespace(
        id="call_list",
        function=SimpleNamespace(
            name="manage_spreadsheet_versions",
            arguments=json.dumps({"file_path": "book.xlsx", "action": "list"}),
        ),
    )
    listed = await engine._tool_dispatcher.execute(
        tc=list_tc,
        tool_scope=None,
        on_event=None,
        iteration=1,
        route_result=None,
    )
    assert listed.success is True, listed.result
    assert called == ["list"]

    restore_tc = SimpleNamespace(
        id="call_restore",
        function=SimpleNamespace(
            name="manage_spreadsheet_versions",
            arguments=json.dumps({
                "file_path": "book.xlsx",
                "action": "restore",
                "revision_id": "rev1",
            }),
        ),
    )
    restored = await engine._tool_dispatcher.execute(
        tc=restore_tc,
        tool_scope=None,
        on_event=None,
        iteration=1,
        route_result=None,
    )
    assert restored.success is False
    assert restored.error == "PERMISSION_DENIED"
    assert called == ["list"]
