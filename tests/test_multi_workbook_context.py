import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from excelmanus.api_routes_chat import ChatRequest, _context_input
from excelmanus.workbook.ui_context import render_workbook_ui_context


def view(path, **kwargs):
    return {"workspace_id": "w1", "path": path, "sheet": "明细", "range": "A2:B9", "observed_version": "v1", **kwargs}


def test_multi_view_roles_and_explicit_references(tmp_path):
    engine = SimpleNamespace(_workspace_ref=SimpleNamespace(root=tmp_path, workspace_id="w1"))
    incoming = {"sheet_context": view("ref.xlsx"), "sheet_contexts": [view("main.xlsx"), view("ref.xlsx"), view("result.xlsx")]}
    rendered = render_workbook_ui_context(engine, incoming, "@file:ref.xlsx\n对照主表核对")
    data = json.loads(rendered.splitlines()[2])
    assert data["primary_path"].endswith("main.xlsx")
    assert data["focused_path"].endswith("ref.xlsx")
    assert len(data["views"]) == 3
    assert "不表示允许一起修改" in rendered
    assert render_workbook_ui_context(engine, incoming, "@file:outside.xlsx") == ""


@pytest.mark.parametrize("bad", [view("../outside.xlsx"), view("ref.xlsx", workspace_id="w2"), view("bad.py"), view("ref.xlsx", range="x" * 4097)])
def test_rejects_unsafe_group_member(tmp_path, bad):
    engine = SimpleNamespace(_workspace_ref=SimpleNamespace(root=tmp_path, workspace_id="w1"))
    assert render_workbook_ui_context(engine, {"sheet_contexts": [view("main.xlsx"), bad]}, "分析") == ""


def test_api_keeps_multi_context_even_without_optional_advice():
    request = ChatRequest(message="核对", sheet_context=view("ref.xlsx"), sheet_contexts=[view("main.xlsx"), view("ref.xlsx")])
    with patch("excelmanus.system_one.policy.live_jev_settings", return_value={}), patch("excelmanus.system_one.policy.jev_is_active", return_value=False):
        incoming = _context_input(request)
    assert incoming["sheet_contexts"][0]["path"] == "main.xlsx"
    with pytest.raises(ValidationError):
        ChatRequest(message="核对", sheet_contexts=[view(f"{i}.xlsx") for i in range(4)])


def test_validates_duplicate_members_and_canonicalizes_paths(tmp_path):
    engine = SimpleNamespace(_workspace_ref=SimpleNamespace(root=tmp_path, workspace_id="w1"))
    assert render_workbook_ui_context(engine, {
        "sheet_context": view("main.xlsx"),
        "sheet_contexts": [view("main.xlsx", workspace_id="w2")],
    }, "分析") == ""
    rendered = render_workbook_ui_context(engine, {
        "sheet_context": view("./ref.xlsx"),
        "sheet_contexts": [view("main.xlsx"), view("ref.xlsx")],
    }, "分析")
    assert len(json.loads(rendered.splitlines()[2])["views"]) == 2
    assert render_workbook_ui_context(engine, {
        "sheet_context": view("fourth.xlsx"),
        "sheet_contexts": [view(f"{i}.xlsx") for i in range(3)],
    }, "分析") == ""


def test_intent_context_sees_references_without_loading_cells(tmp_path):
    from excelmanus.system_one.intent_context import context_state
    engine = SimpleNamespace(_workspace_ref=SimpleNamespace(root=tmp_path, workspace_id="w1"))
    state = context_state(engine, "核对这些表", {"sheet_context": view("ref.xlsx"), "sheet_contexts": [view("main.xlsx"), view("ref.xlsx")]})
    assert "linked_view" in str(state) and "main.xlsx" in str(state) and "ref.xlsx" in str(state)


@pytest.mark.asyncio
async def test_agent_persists_multi_context_for_retry_without_provider_metadata(tmp_path):
    from unittest.mock import AsyncMock
    from excelmanus.engine import AgentEngine
    from excelmanus.config import ExcelManusConfig
    from excelmanus.tools.registry import ToolRegistry
    from excelmanus.workspace.refs import WorkspaceRef
    config = ExcelManusConfig(api_key="test", model="test", base_url="https://test.invalid", workspace_root=str(tmp_path), jev_enabled="off")
    engine = AgentEngine(config=config, registry=ToolRegistry(), workspace_ref=WorkspaceRef.from_root(tmp_path, workspace_id="w1"))
    engine._client.chat.completions.create = AsyncMock(return_value=SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="比较结果", tool_calls=None))]))
    incoming = {"sheet_context": view("ref.xlsx"), "sheet_contexts": [view("main.xlsx"), view("ref.xlsx")]}
    with patch("excelmanus.system_one.intent_context.suggest_context", AsyncMock(return_value="")):
        await engine.followup("比较这两份表", context_input=incoming)
    visible = [message for message in engine._memory.messages if message.get("role") == "user" and not message.get("_ui_hidden")]
    assert visible[0]["_workbook_context"] == incoming
    incoming["sheet_contexts"][0]["path"] = "changed.xlsx"
    assert visible[0]["_workbook_context"]["sheet_contexts"][0]["path"] == "main.xlsx"
    messages = engine._client.chat.completions.create.call_args.kwargs["messages"]
    assert "main.xlsx" in str(messages) and "ref.xlsx" in str(messages)
    assert all("_workbook_context" not in message for message in messages)
