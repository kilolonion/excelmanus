"""Exercise self-management boundaries through real engines, catalogs and tool calls."""

from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request

from excelmanus.config import ExcelManusConfig, load_config
from excelmanus.engine import AgentEngine
from excelmanus.self_management import SKILL_NAME, TOOL_NAMES, set_enabled
from excelmanus.skillpacks import SkillpackLoader, SkillRouter
from excelmanus.tools.catalog import catalog_from_engine, execution_catalog_from_engine
from excelmanus.tools.context import ToolCallContext, bind_call, binding_from_engine, reset_call
from excelmanus.tools.registry import ToolDef, ToolRegistry

SYSTEM_SKILLS = Path(__file__).resolve().parents[1] / "excelmanus/skillpacks/system"


@pytest.fixture
def make_engine(tmp_path):
    def make(*, enabled=True, config=None, tools=()):
        config = config or ExcelManusConfig(
            api_key="DO-NOT-EXPOSE-PRIMARY-SECRET", base_url="https://example.test/v1", model="test-model",
            workspace_root=str(tmp_path), memory_enabled=False,
            skills_system_dir=str(SYSTEM_SKILLS), skills_user_dir=str(tmp_path / "user-skills"),
            skills_project_dir=".excelmanus/skillpacks", skills_discovery_enabled=False,
            subagent_user_dir=str(tmp_path / "agents"),
            agent_self_management_enabled=enabled, exa_api_key="DO-NOT-EXPOSE-SEARCH-SECRET",
        )
        registry = ToolRegistry()
        registry.register_tool(ToolDef(
            name="list_directory", description="test reader", func=lambda: "ok",
            input_schema={"type": "object", "properties": {}}, write_effect="none",
        ))
        for extra in tools:
            registry.register_tool(extra)
        loader = SkillpackLoader(config, registry)
        loader.load_all()
        engine = AgentEngine(config, registry, skill_router=SkillRouter(config, loader))
        engine._session_id = "self-test"
        skill = loader.get_skillpack(SKILL_NAME)
        if skill:
            engine._active_skills = [skill]
        return engine
    return make


# CSV-only 目录里真正被文件族门控的工具已变为 trace_spreadsheet_formulas：
# 工作簿写工具不再被门控（新建工作簿不依赖已有 xlsx，见 tests/test_effective_catalog.py）。
PROFILE_GATED_TOOL = ToolDef(
    name="trace_spreadsheet_formulas", description="test formula tracer",
    func=lambda **kwargs: "ok", input_schema={"type": "object", "properties": {"file_path": {"type": "string"}}},
    write_effect="none",
)


def call(engine, name="inspect_agent", *, actor="host", mode=None, session_id=None, **arguments):
    binding = binding_from_engine(engine)
    cap = binding.capability
    if mode is not None:
        cap = replace(cap, catalog_mode=mode)
    binding = replace(binding, actor=actor, capability=cap,
                      session_id=binding.session_id if session_id is None else session_id)
    token = bind_call(ToolCallContext(binding, call_id="test", tool_name=name))
    try:
        return engine._registry.call_tool(name, arguments)
    finally:
        reset_call(token)


def test_default_is_on_in_config_loader():
    values = {"EXCELMANUS_API_KEY": "test", "EXCELMANUS_BASE_URL": "https://example.test/v1",
              "EXCELMANUS_MODEL": "test"}
    assert ExcelManusConfig(api_key="test", base_url="https://example.test/v1", model="test").agent_self_management_enabled
    assert load_config(values=values).agent_self_management_enabled
    values["EXCELMANUS_AGENT_SELF_MANAGEMENT_ENABLED"] = "false"
    assert not load_config(values=values).agent_self_management_enabled
    values["EXCELMANUS_AGENT_SELF_MANAGEMENT_ENABLED"] = "true"
    assert load_config(values=values).agent_self_management_enabled


def test_disabled_hides_skill_tools_and_rejects_direct_calls(make_engine):
    engine = make_engine(enabled=False)
    assert not TOOL_NAMES & set(catalog_from_engine(engine).names())
    assert not TOOL_NAMES & set(execution_catalog_from_engine(engine).names())
    assert SKILL_NAME not in engine._skill_router.list_skill_names()
    assert SKILL_NAME not in engine._skill_resolver.list_manual_invocable_skill_names()
    assert not call(engine).success
    assert not call(engine, "configure_agent", changes={"parallel_tool_max": 2}, reason="test").success


@pytest.mark.asyncio
async def test_enabled_skill_activation_and_discovery(make_engine):
    engine = make_engine()
    engine._active_skills = []
    assert not call(engine).success
    assert TOOL_NAMES <= set(catalog_from_engine(engine).names())
    text = await engine._handle_activate_skill(SKILL_NAME)
    assert "inspect_agent" in text
    assert call(engine).success
    from excelmanus.engine_core.meta_tools import MetaToolBuilder
    from excelmanus.tools.runtime import schema_tool_name
    assert TOOL_NAMES <= {schema_tool_name(s) for s in MetaToolBuilder(engine).build_v5_tools()}


def test_snapshot_is_real_and_does_not_disclose_secrets(make_engine):
    engine = make_engine()
    result = call(engine)
    assert result.success
    assert "DO-NOT-EXPOSE" not in result.model_text
    assert "https://example.test" not in result.model_text
    assert engine._config.workspace_root not in result.model_text
    assert set(result.value["capabilities"]["tools"]) == set(execution_catalog_from_engine(engine).names())
    assert result.value["settings"]["agent_self_management_enabled"]["writable"] is False
    assert result.value["settings"]["thinking_effort"]["writable"] is True


@pytest.mark.parametrize("arguments", [
    {"changes": {"agent_self_management_enabled": False}},
    {"changes": {"api_key": "DO-NOT-ECHO-SECRET"}},
    {"changes": {"code_policy_enabled": False}},
    {"changes": {"parallel_tool_max": 0}},
    {"changes": {"parallel_tool_max": True}},
    {"changes": {"subagent_enabled": "false"}},
    {"changes": {"thinking_effort": "bogus"}},
    {"changes": {"parallel_tool_max": 2}, "disable_tools": ["skill"]},
    {"changes": {"parallel_tool_max": 2}, "enable_tools": ["nonexistent"]},
    {"enable_tools": ["list_directory"], "disable_tools": ["list_directory"]},
    {"changes": {"parallel_tool_max": 2}, "reason": " "},
])
def test_validation_rejects_entire_request_without_mutation(make_engine, arguments):
    engine = make_engine()
    original = engine.config
    result = call(engine, "configure_agent", **{"reason": "test", **arguments})
    assert not result.success
    assert engine.config is original
    assert "DO-NOT-ECHO" not in result.model_text
    assert engine._config.parallel_tool_max == 4


@pytest.mark.parametrize("mode", ["read", "plan"])
def test_read_and_plan_only_allow_inspection(make_engine, mode):
    engine = make_engine()
    engine._current_chat_mode = mode
    names = set(catalog_from_engine(engine).names())
    assert "inspect_agent" in names
    assert "configure_agent" not in names
    assert call(engine, mode=mode).success
    assert not call(engine, "configure_agent", mode=mode, changes={"thinking_effort": "high"}, reason="test").success


def test_no_context_child_and_wrong_session_are_denied(make_engine):
    engine = make_engine()
    assert not engine._registry.call_tool("inspect_agent", {}).success
    assert not call(engine, actor="child").success
    assert not call(engine, session_id="another-session").success
    engine._is_host_session = False
    engine._fixed_capability = binding_from_engine(engine).capability
    assert not TOOL_NAMES & set(execution_catalog_from_engine(engine).names())


def test_explicit_host_restrictions_also_apply_to_direct_registry_calls(make_engine):
    engine = make_engine()
    engine._fixed_capability = replace(binding_from_engine(engine).capability,
                                       disallowed_tools=TOOL_NAMES)
    assert not call(engine).success
    assert not call(engine, "configure_agent", changes={"parallel_tool_max": 2}, reason="test").success


def test_changes_apply_to_real_consumers_and_are_session_local(make_engine):
    engine = make_engine()
    sibling = make_engine(config=engine.config)
    original = engine.config
    result = call(engine, "configure_agent", reason="复杂计算", changes={
        "thinking_effort": "high", "thinking_budget": 8000,
        "subagent_enabled": False, "parallel_tool_max": 2, "parallel_readonly_tools": False,
        "compaction_enabled": False, "max_context_tokens": 64000,
        "max_iterations": 33, "skills_context_char_budget": 9000,
    })
    assert result.success
    assert engine.thinking_config.effort == "high"
    assert engine.thinking_config.budget_tokens == 8000
    assert not engine.subagent_enabled
    assert not engine._compaction_manager.enabled
    assert engine.max_context_tokens == 64000
    assert engine._skill_router._config.skills_context_char_budget == 9000
    assert engine.config.parallel_tool_max == 2
    assert result.value["changes"]["max_iterations"]["effective"] == "next_turn"
    assert sibling.config is original
    assert sibling.config.parallel_tool_max == 4
    assert sibling.subagent_enabled
    assert sibling.thinking_config.effort == "medium"


def test_tool_disable_updates_all_catalogs_and_can_be_reversed(make_engine):
    engine = make_engine()
    disabled = call(engine, "configure_agent", disable_tools=["list_directory"], reason="暂停")
    assert disabled.success
    assert disabled.value["name_reasons"]["list_directory"]["reason"] == "disabled"
    assert "list_directory" not in execution_catalog_from_engine(engine).names()
    assert "list_directory" not in catalog_from_engine(engine).names()
    assert "list_directory" not in engine._registry.effective_catalog().names()
    assert "list_directory" in call(engine).value["capabilities"]["disabled_tools"]
    result = call(engine, "configure_agent", enable_tools=["list_directory"], reason="恢复")
    assert result.success
    assert result.value["name_reasons"]["list_directory"]["reason"] == "enabled"
    assert "list_directory" in result.value["enabled_tools"]
    assert "list_directory" in execution_catalog_from_engine(engine).names()


def test_reenable_cannot_expand_host_scope(make_engine):
    engine = make_engine()
    engine._fixed_capability = replace(binding_from_engine(engine).capability,
                                       disallowed_tools=frozenset({"list_directory"}))
    result = call(engine, "configure_agent", enable_tools=["list_directory"], reason="恢复")
    # 宿主授权排除该工具时"启用"不可能生效：必须是非成功回执 + 逐名原因，
    # 而不是 enabled_tools 为空的 success 误导性回执。
    assert not result.success
    assert result.value["status"] != "success"
    assert result.value["outcome"] == "no_op"
    row = result.value["name_reasons"]["list_directory"]
    assert row["reason"] == "unauthorized" and row["effective"] is False
    assert "list_directory" not in execution_catalog_from_engine(engine).names()
    assert not getattr(engine, "_self_disabled_tools", set())


def test_enable_profile_gated_tool_is_no_op_with_actionable_reason(make_engine, tmp_path):
    """真实会话复现：CSV-only 目录里 enable 被门控工具曾返回空 changes 的 success。

    门控对象现为 trace_spreadsheet_formulas（写工具不再按 CSV profile 门控）。
    """
    (tmp_path / "广告与销售数据.csv").write_text("投入,销售额\n2.5,18.2\n", encoding="utf-8")
    engine = make_engine(tools=[PROFILE_GATED_TOOL])
    original = engine.config
    assert "trace_spreadsheet_formulas" not in execution_catalog_from_engine(engine).names()
    result = call(engine, "configure_agent", enable_tools=["trace_spreadsheet_formulas"],
                  reason="任务需要追踪公式依赖")
    assert not result.success
    assert result.value["status"] != "success"
    assert result.value["outcome"] == "no_op" and result.value["error_code"] == "NOOP"
    assert result.value["changes"] == {}
    row = result.value["name_reasons"]["trace_spreadsheet_formulas"]
    assert row["reason"] == "gated_by_profile" and row["effective"] is False
    assert "xlsx" in row["alternative"]
    # 请求没有生效，也没有留下任何状态变化
    assert engine.config is original
    assert not getattr(engine, "_self_disabled_tools", set())
    assert "trace_spreadsheet_formulas" not in execution_catalog_from_engine(engine).names()


def test_unknown_and_not_paused_names_never_return_empty_success(make_engine):
    engine = make_engine()
    original = engine.config
    unknown = call(engine, "configure_agent", changes={"parallel_tool_max": 2}, reason="test",
                   enable_tools=["apply_spreadsheet_changes"])
    assert not unknown.success
    assert unknown.value["status"] != "success"
    assert unknown.value["error_code"] == "INVALID_ARGS"
    assert unknown.value["unknown_tools"] == ["apply_spreadsheet_changes"]
    assert unknown.value["name_reasons"]["apply_spreadsheet_changes"]["reason"] == "unknown"
    not_paused = call(engine, "configure_agent", changes={"parallel_tool_max": 2}, reason="并发+恢复",
                      enable_tools=["list_directory"])
    assert not not_paused.success
    assert not_paused.value["outcome"] == "no_op"
    row = not_paused.value["name_reasons"]["list_directory"]
    assert row["reason"] == "not_paused" and row["effective"] is False
    # 只要有一个工具名不生效，整条请求都不生效（不做部分生效）
    assert engine.config is original
    assert engine._config.parallel_tool_max == 4
    assert "list_directory" in execution_catalog_from_engine(engine).names()


def test_disable_already_unavailable_tool_is_no_op(make_engine, tmp_path):
    (tmp_path / "data.csv").write_text("a\n1\n", encoding="utf-8")
    engine = make_engine(tools=[PROFILE_GATED_TOOL])
    result = call(engine, "configure_agent", disable_tools=["trace_spreadsheet_formulas"], reason="确保关闭")
    assert not result.success
    assert result.value["outcome"] == "no_op"
    row = result.value["name_reasons"]["trace_spreadsheet_formulas"]
    assert row["reason"] == "already_unavailable" and row["effective"] is False
    assert not getattr(engine, "_self_disabled_tools", set())


def test_unchanged_settings_are_reported_as_no_op(make_engine):
    engine = make_engine()
    original = engine.config
    result = call(engine, "configure_agent", changes={"parallel_tool_max": 4}, reason="确保并发上限")
    assert not result.success
    assert result.value["outcome"] == "no_op" and result.value["unchanged_settings"] == ["parallel_tool_max"]
    assert result.value["changed_settings"] == []
    assert engine.config is original  # 无变更时连 config 对象都不替换


def test_hot_disable_revokes_stale_tool_and_skill(make_engine):
    engine = make_engine()
    stale = engine._registry.get_tool("configure_agent")
    set_enabled(engine, False)
    assert not TOOL_NAMES & set(engine._registry.effective_catalog().names())
    assert SKILL_NAME not in engine._skill_router.list_skill_names()
    assert not stale.func(changes={"parallel_tool_max": 2}, reason="old call").success
    assert not engine._active_skills
    set_enabled(engine, True)
    assert SKILL_NAME in engine._skill_router.list_skill_names()
    assert TOOL_NAMES <= set(execution_catalog_from_engine(engine).names())
    assert not call(engine).success  # explicit reactivation required


@pytest.mark.asyncio
async def test_runtime_api_persists_and_broadcasts_gate(monkeypatch, make_engine):
    from excelmanus import api_routes_config as routes
    engine = make_engine(enabled=False)
    store_updates = []
    manager = SimpleNamespace(broadcast_self_management=AsyncMock())
    monkeypatch.setattr(routes, "get_config", lambda: engine.config)
    monkeypatch.setattr(routes, "get_session_manager", lambda: manager)
    monkeypatch.setattr(routes, "_persist_settings", lambda values: store_updates.append(values))
    request = Request({"type": "http", "method": "PUT", "path": "/api/v1/config/runtime", "headers": []})
    result = await routes.update_runtime_config(routes.RuntimeConfigUpdate(agent_self_management_enabled=True), request)
    assert result.status_code == 200
    assert store_updates == [{"EXCELMANUS_AGENT_SELF_MANAGEMENT_ENABLED": "true"}]
    manager.broadcast_self_management.assert_awaited_once_with(True)
    snapshot = await routes.get_runtime_config(request)
    assert json.loads(snapshot.body)["agent_self_management_enabled"] is True


@pytest.mark.asyncio
async def test_full_dispatch_refreshes_settings_and_rejects_disabled_tools(make_engine):
    engine = make_engine()

    async def execute(name, **arguments):
        tc = SimpleNamespace(id=f"call-{name}", function=SimpleNamespace(
            name=name, arguments=json.dumps(arguments)))
        return await engine._execute_tool_call(tc, None, None, 1)

    before = await execute("inspect_agent", section="settings")
    assert before.success, before.result
    # External UI changes must not replay an earlier read-only tool result.
    engine.set_thinking_config(effort="high")
    refreshed = await execute("inspect_agent", section="settings")
    assert refreshed.structured.value["settings"]["thinking_effort"]["value"] == "high"
    changed = await execute("configure_agent", changes={"parallel_tool_max": 2},
                            disable_tools=["list_directory"], reason="减少并发")
    assert changed.success, changed.result
    after = await execute("inspect_agent", section="settings")
    assert after.success, after.result
    assert after.structured.value["settings"]["parallel_tool_max"]["value"] == 2
    blocked = await execute("list_directory")
    assert not blocked.success
    set_enabled(engine, False)
    assert not (await execute("inspect_agent", section="settings")).success


@pytest.mark.asyncio
async def test_session_manager_broadcasts_to_existing_engines(make_engine):
    import asyncio
    from excelmanus.session import SessionManager
    first, second = make_engine(enabled=False), make_engine(enabled=False)
    manager = SimpleNamespace(_lock=asyncio.Lock(), _sessions={
        "first": SimpleNamespace(engine=first), "second": SimpleNamespace(engine=second),
    })
    await SessionManager.broadcast_self_management(manager, True)
    for engine in (first, second):
        assert engine.config.agent_self_management_enabled
        assert SKILL_NAME in engine._skill_router.list_skill_names()
        assert TOOL_NAMES <= set(execution_catalog_from_engine(engine).names())
    await SessionManager.broadcast_self_management(manager, False)
    assert not first.config.agent_self_management_enabled
    assert not second.config.agent_self_management_enabled
