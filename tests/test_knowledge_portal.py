"""Portal discovery -> references -> current contracts, without model/network calls."""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from jsonschema import validate

from excelmanus.agent.session import AgentEngine
from excelmanus.config import ExcelManusConfig
from excelmanus.engine_core.meta_tools import MetaToolBuilder
from excelmanus.knowledge.documents import TOPICS, references
from excelmanus.knowledge.portal import next_call
from excelmanus.prompt.assemble import build_stable_system_prompt
from excelmanus.security import FileAccessGuard, SecurityViolationError
from excelmanus.skillpacks import SkillpackLoader, SkillRouter
from excelmanus.tools.context import ToolCallContext, bind_call, binding_from_engine, reset_call
from excelmanus.tools.registry import ToolDef, ToolRegistry

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def make_engine(tmp_path):
    def make(name="main", **overrides):
        workspace = tmp_path / name
        workspace.mkdir(exist_ok=True)
        values = dict(api_key="DO-NOT-EXPOSE-SECRET", base_url="https://private.invalid/v1", model=name,
                      workspace_root=str(workspace), jev_enabled="off", memory_enabled=False,
                      skills_system_dir=str(ROOT / "excelmanus/skillpacks/system"),
                      skills_user_dir=str(workspace / "skills"), skills_discovery_enabled=False)
        values.update(overrides)
        config = ExcelManusConfig(**values)
        registry = ToolRegistry()
        registry.register_builtin_tools(str(workspace))
        loader = SkillpackLoader(config, registry)
        loader.load_all()
        engine = AgentEngine(config, registry, skill_router=SkillRouter(config, loader))
        engine._session_id = name
        return engine
    return make


def invoke(engine, call):
    MetaToolBuilder(engine).build_v5_tools()
    tool = engine.registry.get_tool(call["name"])
    validate(call["arguments"], tool.input_schema)
    token = bind_call(ToolCallContext(binding_from_engine(engine), tool_name=call["name"],
                                     loaded_tool_names=engine._loaded_tool_names))
    try:
        result = engine.registry.call_tool(call["name"], call["arguments"])
    finally:
        reset_call(token)
    assert result.success, result.model_text
    return json.loads(result.model_text)


def read(engine, ref):
    return invoke(engine, next_call("knowledge_read", ref))


def all_items(engine, call):
    items = []
    while call:
        result = invoke(engine, call)
        assert result["status"] == "ok", result
        items.extend(result.get("items", []))
        call = result.get("next_call")
    return items


@pytest.mark.parametrize("mode", ["write", "read", "plan"])
def test_entry_point_survives_without_self_management_and_compaction(make_engine, mode):
    engine = make_engine(agent_self_management_enabled=False)
    engine._current_chat_mode = mode
    for _ in range(2):
        schemas = MetaToolBuilder(engine).build_v5_tools()
        tool = next(s["function"] for s in schemas if s["function"]["name"] == "introspect_capability")
        assert "knowledge_read" in tool["description"]
        assert "knowledge_index" in build_stable_system_prompt(engine)
        assert not engine.config.agent_self_management_enabled
        assert read(engine, "setting:max_iterations")["data"]["value"] == engine.config.max_iterations
        assert not read(engine, "setting:max_iterations")["data"]["can_modify_now"]
        engine.memory.clear()


def test_all_document_references_and_live_directories_are_traversable(make_engine):
    engine = make_engine()
    entries = all_items(engine, next_call("knowledge_index"))
    assert {"doc:" + topic.id for topic in TOPICS} <= {e["ref"] for e in entries}
    for topic in TOPICS:
        result = read(engine, "doc:" + topic.id)
        assert result["status"] == "ok"
        assert set(references(topic.read())) == {item["ref"] for item in result["links"]}
        for target in result["links"]:
            resolved = invoke(engine, target["next_call"])
            assert resolved["status"] in {"ok", "unavailable"}, (target, resolved)
            assert "index_call" in resolved
    for directory in ("tools", "settings", "skills", "errors"):
        items = all_items(engine, next_call("knowledge_read", directory))
        assert items
        for item in items:
            resolved = invoke(engine, item["next_call"])
            assert resolved["status"] in {"ok", "unavailable"}, (item, resolved)
            assert "DO-NOT-EXPOSE" not in json.dumps(resolved)
            assert "private.invalid" not in json.dumps(resolved)


def test_tool_fields_and_outputs_are_reachable_from_live_schema(make_engine):
    engine = make_engine()
    for name in ("observe_spreadsheet", "analyze_spreadsheet", "convert_spreadsheet",
                 "list_directory", "trace_spreadsheet_formulas", "apply_spreadsheet_changes"):
        detail = read(engine, "tool:" + name)
        assert detail["status"] == "ok", detail
        assert "TOOL_EXECUTION_ERROR" not in detail.get("content", "")
        fields = all_items(engine, next_call("knowledge_read", "fields:" + name))
        expected = set(engine.registry.get_tool(name).input_schema["properties"])
        assert expected <= {item["title"] for item in fields}
        for item in fields:
            assert invoke(engine, item["next_call"])["status"] == "ok", item
        assert read(engine, "tool:" + name + ".output")["status"] == "ok"
    nested = all_items(engine, next_call("knowledge_read", "fields:apply_spreadsheet_changes.workbook_spec.sheets"))
    assert any(item["title"] == "styles" for item in nested)
    for item in nested:
        assert invoke(engine, item["next_call"])["status"] == "ok"
    kinds = all_items(engine, next_call("knowledge_read", "fields:apply_spreadsheet_changes.operations"))
    assert {"kind", "write", "geometry.scale"} <= {item["title"] for item in kinds}
    for item in kinds:
        resolved = invoke(engine, item["next_call"])
        assert resolved["status"] == "ok", resolved
    geometry = all_items(engine, next_call("knowledge_read", "fields:apply_spreadsheet_changes.operations.geometry.scale"))
    assert "x" in {item["title"] for item in geometry}
    for item in geometry:
        assert invoke(engine, item["next_call"])["status"] == "ok"


def test_full_schema_pages_reconstruct_exact_contract(make_engine):
    from excelmanus.tools.reference_contract import augment_reference_schema

    engine = make_engine()
    name = "apply_spreadsheet_changes"
    call = next_call("knowledge_read", "schema:" + name)
    pieces = []
    while call:
        result = invoke(engine, call)
        assert result["status"] == "ok", result
        pieces.append(result["content"])
        call = result.get("next_call")
    assert len(pieces) > 1
    assert json.loads("".join(pieces)) == augment_reference_schema(engine.registry.get_tool(name).input_schema)


@pytest.mark.parametrize("query,expected", [
    ("系统架构", "doc:architecture"), ("读取 Excel 数据", "tool:observe_spreadsheet"),
    ("配置", "settings"), ("审批", "doc:execution"),
    ("VERSION_CONFLICT", "error:VERSION_CONFLICT"), ("memory", "doc:context"),
])
def test_bilingual_search_leads_to_resolvable_resources(make_engine, query, expected):
    engine = make_engine()
    hits = all_items(engine, next_call("knowledge_search", query))
    assert expected in {hit["ref"].partition("#")[0] for hit in hits}
    assert read(engine, expected)["status"] == "ok"


def test_empty_unknown_and_invalid_references_have_recovery_paths(make_engine):
    engine = make_engine()
    for ref in ("doc:missing", "../../config.env", "/etc/passwd", "setting:api_key", "error:NOT_REAL"):
        result = read(engine, ref)
        assert result["status"] == "not_found"
        assert invoke(engine, result["index_call"])["status"] == "ok"
    assert invoke(engine, next_call("knowledge_search", "xyz_no_such_capability"))["items"] == []
    assert invoke(engine, next_call("knowledge_search"))["items"]
    assert invoke(engine, next_call("knowledge_read", "tools", page=999))["restart_call"]


def test_pagination_detects_catalog_changes_and_never_drops_items(make_engine):
    engine = make_engine()
    first = read(engine, "tools")
    assert first["next_call"]
    expected = set(engine.registry.effective_catalog().names())
    items = all_items(engine, next_call("knowledge_read", "tools"))
    assert len(items) == len(expected)
    assert {item["ref"][5:] for item in items} == expected
    engine.registry.register_tool(ToolDef(name="mcp_new_search", description="New search",
        input_schema={"type": "object", "properties": {}}, func=lambda: "ok", write_effect="none"))
    stale = invoke(engine, first["next_call"])
    assert stale["status"] == "stale"
    assert invoke(engine, stale["restart_call"])["status"] == "ok"


def test_package_docs_work_while_workspace_source_remains_forbidden(make_engine, tmp_path):
    engine = make_engine()
    docs = Path(engine.config.workspace_root) / "docs"
    docs.mkdir()
    (docs / "architecture.md").write_text("MALICIOUS_WORKSPACE_OVERRIDE", encoding="utf-8")
    with pytest.raises(SecurityViolationError):
        FileAccessGuard(engine.config.workspace_root).resolve_and_validate("docs/architecture.md")
    assert "MALICIOUS_WORKSPACE_OVERRIDE" not in json.dumps(read(engine, "doc:architecture"))


def test_session_settings_are_live_and_do_not_cross_between_engines(make_engine):
    first, second = make_engine("first", max_iterations=31), make_engine("second", max_iterations=72)
    assert read(first, "setting:max_iterations")["data"]["value"] == 31
    assert read(second, "setting:max_iterations")["data"]["value"] == 72
    first._config = replace(first.config, max_iterations=99)
    assert read(first, "setting:max_iterations")["data"]["value"] == 99
    assert read(first, "runtime")["data"]["session"]["model"] == "first"


def test_child_uses_own_runtime_and_permissions(make_engine):
    from excelmanus.subagent.child import compose_child
    from excelmanus.subagent.builtin import BUILTIN_SUBAGENTS

    parent = make_engine("parent")
    child = compose_child(parent, replace(BUILTIN_SUBAGENTS["explorer"], model="child-model"))
    state = read(child, "runtime")["data"]["session"]
    assert state["actor"] == "child" and state["model"] == "child-model"
    assert state["mode"] == "read"
    assert read(child, "tool:apply_spreadsheet_changes")["status"] == "unavailable"
    assert not read(child, "setting:max_iterations")["data"]["can_modify_now"]
    assert "parent" not in json.dumps(state)


@pytest.mark.asyncio
async def test_real_dispatch_read_loads_deferred_tool_for_next_model_request(make_engine):
    engine = make_engine()
    schemas = MetaToolBuilder(engine).build_v5_tools()
    assert "compare_spreadsheets" not in {s["function"]["name"] for s in schemas}
    call = SimpleNamespace(id="knowledge-read", function=SimpleNamespace(
        name="introspect_capability", arguments=json.dumps({"query_type": "knowledge_read", "query": "tool:compare_spreadsheets"})))
    result = await engine._tool_runtime.execute(call, None, None, 1)
    assert result.success, result.result
    assert "compare_spreadsheets" in {s["function"]["name"] for s in MetaToolBuilder(engine).build_v5_tools()}


def test_skill_read_returns_load_call_without_activating(make_engine):
    engine = make_engine(agent_self_management_enabled=False)
    state = read(engine, "skill:data_basic")
    assert state["load_call"] == {"name": "skill", "arguments": {"name": "data_basic"}}
    assert not engine._active_skills
    assert not engine.config.agent_self_management_enabled


def test_skill_body_and_resources_have_complete_paginated_read_paths(make_engine):
    engine = make_engine()
    loader = engine._skill_router._loader
    original = loader.get_skillpack("data_basic")
    body = "完整技能正文与说明。" * 1200
    resource = "完整资源内容。" * 1300
    loader._skillpacks["data_basic"] = replace(original, instructions=body,
        resources=["references/full.md"], resource_contents={"references/full.md": resource})
    for ref, expected in (("skill-text:data_basic", body), ("resource:data_basic/references/full.md", resource)):
        call = next_call("knowledge_read", ref)
        chunks = []
        while call:
            result = invoke(engine, call)
            assert result["status"] == "ok"
            chunks.append(result["content"])
            call = result.get("next_call")
        assert "".join(chunks) == expected
    links = all_items(engine, next_call("knowledge_read", "resources:data_basic"))
    assert links[0]["ref"] == "resource:data_basic/references/full.md"
    assert read(engine, "resource:data_basic/../../config.env")["status"] == "not_found"
    assert not engine._active_skills


def test_missing_package_resource_has_explicit_repair_and_index(make_engine, monkeypatch):
    from excelmanus.knowledge.documents import Topic

    engine = make_engine()
    def missing(self):
        raise FileNotFoundError("package resource")
    monkeypatch.setattr(Topic, "read", missing)
    result = read(engine, "doc:architecture")
    assert result["status"] == "unavailable" and "修复安装" in result["reason"]
    assert invoke(engine, result["index_call"])["items"]


def test_mismatched_context_cannot_read_bound_engine_settings(make_engine):
    engine = make_engine()
    MetaToolBuilder(engine).build_v5_tools()
    binding = replace(binding_from_engine(engine), session_id="different-session")
    token = bind_call(ToolCallContext(binding))
    try:
        result = engine.registry.call_tool("introspect_capability", {"query_type": "knowledge_read", "query": "settings"})
    finally:
        reset_call(token)
    assert json.loads(result.model_text)["status"] == "unavailable"


def test_read_only_setting_can_modify_changes_only_when_all_gates_pass(make_engine):
    from excelmanus.self_management import set_enabled

    engine = make_engine()
    assert not read(engine, "setting:thinking_effort")["data"]["can_modify_now"]
    set_enabled(engine, True)
    assert not read(engine, "setting:thinking_effort")["data"]["can_modify_now"]
    engine._active_skills = [engine._skill_router._loader.get_skillpack("agent_self_management")]
    assert read(engine, "setting:thinking_effort")["data"]["can_modify_now"]
    engine._current_chat_mode = "plan"
    assert not read(engine, "setting:thinking_effort")["data"]["can_modify_now"]


def test_live_mcp_names_with_dots_and_long_descriptions_are_navigable(make_engine):
    engine = make_engine()
    name = "mcp_service.lookup"
    engine.registry.register_tool(ToolDef(name=name, description="A" * 300 + " 探索星图",
        input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
        func=lambda query: "ok", write_effect="none"))
    assert read(engine, "tool:" + name)["status"] == "ok"
    fields = all_items(engine, next_call("knowledge_read", "fields:" + name))
    assert fields and invoke(engine, fields[0]["next_call"])["status"] == "ok"
    hits = all_items(engine, next_call("knowledge_search", "探索星图"))
    assert "tool:" + name in {item["ref"] for item in hits}


@pytest.mark.asyncio
async def test_python_sdk_queries_same_session_portal(make_engine):
    import sys
    from excelmanus.engine_core.tool_dispatcher import _SyntheticToolCall

    engine = make_engine(max_iterations=37)
    call = _SyntheticToolCall(call_id="portal-sdk", name="run_code", arguments={
        "code": "import em\nr = em.introspect_capability(query_type='knowledge_read', query='setting:max_iterations')\nprint('portal_iterations=' + str(r['data']['value']))",
        "python_command": sys.executable, "timeout_seconds": 30,
    })
    result = await engine._execute_tool_call(call, None, None, 0)
    assert result.success, result.result
    assert "portal_iterations=37" in result.result
