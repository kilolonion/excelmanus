"""Child prompt evidence at the real request/Driver boundary; no remote model calls."""
from __future__ import annotations

import json
import shutil
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from excelmanus.agent.session import AgentEngine
from excelmanus.config import ExcelManusConfig
from excelmanus.prompt.assemble import build_stable_system_prompt
from excelmanus.prompt.load import PromptComposer
from excelmanus.request.compiler import compile_request
from excelmanus.subagent.builtin import BUILTIN_SUBAGENTS
from excelmanus.subagent.child import compose_child
from excelmanus.subagent.errors import SubagentError
from excelmanus.subagent.models import SubagentConfig, SubagentStartRequest
from excelmanus.tools.registry import ToolRegistry


@pytest.fixture
def parent(tmp_path):
    registry = ToolRegistry()
    registry.register_builtin_tools(str(tmp_path))
    engine = AgentEngine(ExcelManusConfig(
        api_key="test", base_url="https://test.invalid/v1", model="parent-model",
        protocol="openai", workspace_root=str(tmp_path), main_model_vision="false",
    ), registry)
    # Independent text fixtures make reload/failure tests safe for the working tree.
    dest = tmp_path / "prompts"
    shutil.copytree(Path(__file__).parents[1] / "excelmanus/prompts", dest)
    engine._prompt_composer = PromptComposer(dest)
    engine._prompt_composer.load_all(auto_repair=False)
    engine._bind_prompt_registry_runtime()
    engine._session_id = "prompt-parent"
    return engine


async def wire(engine):
    prepared, error = await compile_request(engine)
    assert error is None, error
    assert prepared is not None
    return prepared.create_kwargs()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["read", "plan", "write"])
async def test_child_wire_has_core_and_effective_permissions(parent, mode):
    parent._current_chat_mode = mode
    child = compose_child(parent, BUILTIN_SUBAGENTS["subagent"])
    child.memory.add_user_message("处理授权范围内的任务")
    request = await wire(child)
    text = request["messages"][0]["content"]
    names = {item["function"]["name"] for item in request.get("tools", [])}
    assert "你是 ExcelManus，工作区内的表格智能代理" in text
    assert "结论以实际读取的数据为依据" in text
    assert "区分完成、部分完成、失败、被拒绝和未验证" in text
    assert f"当前子代理模式：{mode}" in text
    assert "本次授权工具目录" in text
    assert "delegate" not in names
    assert "write_plan" not in names
    assert ("VERSION_CONFLICT 表示这次没有落盘" in text) == (mode == "write")
    assert ("edit_spreadsheet" in names) == (mode == "write")
    assert ("当前是计划模式" in text) == (mode == "plan")


@pytest.mark.asyncio
async def test_explorer_does_not_inherit_write_strategies_or_parent_callbacks(parent):
    parent._prompt_user_contexts = ["parent-private-context"]
    parent_before = build_stable_system_prompt(parent)
    child = compose_child(parent, BUILTIN_SUBAGENTS["explorer"])
    assert child._prompt_composer is not parent._prompt_composer
    assert child._prompt_composer.registry is not parent._prompt_composer.registry
    child.memory.add_user_message("只读探索")
    request = await wire(child)
    text = request["messages"][0]["content"]
    names = {item["function"]["name"] for item in request.get("tools", [])}
    assert "run_code 只做只读计算" in text
    assert "程序写工作区走 em.*" not in text
    assert "WorkbookSpec 经 edit_spreadsheet" not in text
    assert "parent-private-context" not in json.dumps(request, ensure_ascii=False)
    assert names <= set(child._registry.get_tool_names())
    assert "edit_spreadsheet" not in names
    assert build_stable_system_prompt(parent) == parent_before


@pytest.mark.asyncio
async def test_custom_same_name_role_and_child_model_are_used(parent):
    config = replace(BUILTIN_SUBAGENTS["explorer"], source="project", model="child-model",
                     system_prompt="独立自定义角色。当前模型 {{model}}，工作区 {{workspace_root}}。")
    child = compose_child(parent, config)
    child.memory.add_user_message("开始")
    request = await wire(child)
    text = request["messages"][0]["content"]
    assert "独立自定义角色。当前模型 child-model" in text
    assert "只读探索子代理 explorer" not in text
    assert "{{" not in text
    assert request["model"] == "child-model"
    assert parent.active_model == "parent-model"


def add_strategy(parent, name, conditions, marker):
    path = parent._prompt_composer._prompts_dir / "strategies" / f"{name}.md"
    path.write_text(f"---\nname: {name}\npriority: 160\nlayer: strategy\n"
                    f"conditions: {json.dumps(conditions)}\n---\n{marker}", encoding="utf-8")


@pytest.mark.asyncio
async def test_runtime_conditions_update_without_recreating_child(parent):
    add_strategy(parent, "only_new", {"new_workbook": True}, "NEW_WORKBOOK_ONLY")
    add_strategy(parent, "only_full", {"full_access": True}, "FULL_ACCESS_ONLY")
    add_strategy(parent, "only_write_tool", {"tool": "edit_spreadsheet"}, "WRITE_TOOL_ONLY")
    child = compose_child(parent, SubagentConfig(
        name="探查.团队", description="custom", source="user", permission_mode="readOnly",
        system_prompt="受托处理数据。", allowed_tools=["inspect_spreadsheet", "run_code"],
    ))
    text = build_stable_system_prompt(child)
    assert "NEW_WORKBOOK_ONLY" in text
    assert "FULL_ACCESS_ONLY" not in text
    assert "WRITE_TOOL_ONLY" not in text
    child._full_access_enabled = True
    # Catalog inspects names/extensions only; this fixture never opens a workbook.
    (Path(child.config.workspace_root) / "existing.xlsx").write_bytes(b"catalog fixture")
    updated = build_stable_system_prompt(child)
    assert "NEW_WORKBOOK_ONLY" not in updated
    assert "FULL_ACCESS_ONLY" in updated
    assert "WRITE_TOOL_ONLY" not in updated


@pytest.mark.asyncio
async def test_existing_child_reloads_role_and_core_without_parent_rebinding(parent):
    child = compose_child(parent, BUILTIN_SUBAGENTS["explorer"])
    child.memory.add_user_message("继续探索")
    await wire(child)
    base = parent._prompt_composer._prompts_dir
    core = base / "core/10_core_principles.md"
    role = base / "subagent/explorer.md"
    core.write_text(core.read_text() + "\n共享核心更新。", encoding="utf-8")
    role.write_text(role.read_text() + "\n探索角色更新。", encoding="utf-8")
    request = await wire(child)
    text = json.dumps(request, ensure_ascii=False)
    assert "共享核心更新" in text and "探索角色更新" in text
    assert "edit_spreadsheet" not in {t["function"]["name"] for t in request.get("tools", [])}
    assert child._prompt_composer.registry is not parent._prompt_composer.registry


@pytest.mark.asyncio
@pytest.mark.parametrize("relative,damage", [
    ("subagent/explorer.md", "delete"),
    ("subagent/_base.md", "delete"),
    ("subagent/explorer.md", "broken"),
    ("subagent/_base.md", "yaml"),
    ("core/10_core_principles.md", "delete"),
    ("core/10_core_principles.md", "variable"),
])
async def test_invalid_prompt_fails_before_client_creation_or_run_publication(parent, monkeypatch, relative, damage):
    path = parent._prompt_composer._prompts_dir / relative
    if damage == "delete":
        path.unlink()
    elif damage == "variable":
        path.write_text(path.read_text() + "\n{{missing_runtime}}", encoding="utf-8")
    else:
        path.write_text("broken" if damage == "broken" else "---\nname: [\n---\ntext", encoding="utf-8")
    created = []
    monkeypatch.setattr("excelmanus.engine_core.llm_client_manager.create_client",
                        lambda **kw: created.append(kw))
    events = []
    with pytest.raises(SubagentError) as exc:
        await parent._subagent_runtime.start(SubagentStartRequest(
            task="探索", agent_name="explorer", on_event=events.append,
        ))
    assert exc.value.code == "PROMPT_INVALID"
    assert created == []
    assert events == []
    assert not parent._subagent_runtime._records


@pytest.mark.asyncio
async def test_missing_composer_cannot_use_legacy_child_prompt(parent):
    child = compose_child(parent, BUILTIN_SUBAGENTS["explorer"])
    child._child_system_prompt = "旧缓存不能继续使用"
    child._prompt_composer = None
    child.memory.add_user_message("继续")
    request, error = await compile_request(child)
    assert request is None
    assert "组装器未初始化" in error


@pytest.mark.asyncio
async def test_damage_after_start_prevents_model_request(parent):
    child = compose_child(parent, BUILTIN_SUBAGENTS["explorer"])
    role = parent._prompt_composer._prompts_dir / "subagent/explorer.md"
    role.write_text("broken", encoding="utf-8")
    child._client.chat.completions.create = AsyncMock()
    result = await child.followup("继续")
    assert "系统提示词组装失败" in result.reply
    child._client.chat.completions.create.assert_not_awaited()


@pytest.mark.parametrize("overrides", [
    {"system_prompt": "变量 {{not_defined}}"},
    {"system_prompt": ""},
    {"inherit_strategies": ["missing:strategy"]},
])
def test_custom_prompt_errors_are_explicit(parent, overrides):
    config = SubagentConfig(name="worker", source="project", description="test", system_prompt="执行受托任务。")
    with pytest.raises(SubagentError) as exc:
        compose_child(parent, replace(config, **overrides))
    assert exc.value.code == "PROMPT_INVALID"


@pytest.mark.asyncio
async def test_real_child_driver_sends_unified_prompt_and_returns_normal_result(parent, monkeypatch):
    received = []
    async def create(**kwargs):
        received.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
            content="未读取数据，未修改文件。", tool_calls=None,
        ))])
    monkeypatch.setattr("excelmanus.engine_core.llm_client_manager.create_client", lambda **_: SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
    ))
    run = await parent._subagent_runtime.start(SubagentStartRequest(task="报告权限范围", agent_name="explorer"))
    result = await run.result
    assert result.stop_reason == "completed"
    assert result.output == "未读取数据，未修改文件。"
    text = received[0]["messages"][0]["content"]
    assert "结论以实际读取的数据为依据" in text
    assert "区分完成、部分完成、失败、被拒绝和未验证" in text
    assert "当前子代理模式：read" in text
