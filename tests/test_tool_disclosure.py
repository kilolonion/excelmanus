"""按需工具披露：发现 → 可信详情 → 下一请求 schema，SDK 保留完整授权目录。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from excelmanus.code_mode import build_session_for_run_code, render_sdk_source
from excelmanus.engine_core.meta_tools import MetaToolBuilder
from excelmanus.engine_core.tool_result import ToolResult
from excelmanus.tools.context import (
    CallerCapability,
    ToolCallContext,
    bind_call,
    binding_from_engine,
    reset_call,
)
from excelmanus.tools.registry import ToolDef, ToolRegistry
from excelmanus.tools.policy import DEFAULT_DISCLOSURE_CORE_TOOLS


@pytest.fixture
def engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    from excelmanus.tools import introspection_tools

    monkeypatch.setattr(introspection_tools, "_registry", None)
    registry = ToolRegistry()
    names = DEFAULT_DISCLOSURE_CORE_TOOLS - {"introspect_capability"} | {
        "compare_spreadsheets", "trace_spreadsheet_formulas", "split_spreadsheet",
        "manage_spreadsheet_objects", "manage_spreadsheet_versions",
        "read_text_file", "copy_file", "run_shell", "read_word", "write_word",
        "delegate", "list_subagents", "manage_skills", "memory_read_topic",
        "mcp_docs_search", "mcp_private_search",
    }
    (tmp_path / "notes.docx").write_bytes(b"")  # 只为激活 docx 工作区能力族。
    for name in sorted(names):
        registry.register_tool(ToolDef(
            name=name,
            description="Search documents" if name.startswith("mcp_") else name,
            input_schema={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
                "additionalProperties": False,
            },
            func=lambda query: "found: " + query,
            write_effect="none",
        ))
    introspection_tools.register_introspection_tools(registry)
    return SimpleNamespace(
        registry=registry, _registry=registry,
        config=SimpleNamespace(workspace_root=str(tmp_path)),
        _current_chat_mode="write",
        _active_skills=[], _skill_router=None, _skill_resolver=None,
        _subagent_config=None, _fixed_capability=None,
        _tools_cache=None, _tools_cache_key=None, _loaded_tool_names=set(),
        _is_host_session=True,
    )


def _names(schemas: list[dict]) -> set[str]:
    return {row["function"]["name"] for row in schemas}


def _detail(engine: SimpleNamespace, query: str = "", **kwargs: object) -> ToolResult:
    token = bind_call(ToolCallContext(
        binding=binding_from_engine(engine),
        tool_name="introspect_capability",
        loaded_tool_names=engine._loaded_tool_names,
    ))
    try:
        arguments = dict(kwargs) if kwargs else {"query_type": "tool_detail"}
        arguments.setdefault("query", query)
        return engine.registry.call_tool(
            "introspect_capability",
            arguments,
        )
    finally:
        reset_call(token)


def test_discover_load_compile_and_call_keeps_sdk_complete(engine: SimpleNamespace) -> None:
    builder = MetaToolBuilder(engine)
    initial = builder.build_v5_tools()
    assert "mcp_docs_search" not in _names(initial)
    assert {"inspect_spreadsheet", "run_code", "introspect_capability"} <= _names(initial)
    description = next(row["function"]["description"] for row in initial if row["function"]["name"] == "introspect_capability")
    assert "MCP" in description and "can_i_do" in description and "tool_detail" in description
    assert builder.build_v5_tools() is initial
    discovery = _detail(engine, query_type="can_i_do", query="Search documents")
    assert "mcp_docs_search" in discovery.model_text
    assert not engine._loaded_tool_names

    session = build_session_for_run_code(SimpleNamespace(_engine=engine), root_call_id="run")
    assert {"mcp_docs_search", "mcp_private_search"} <= session.bound_names
    assert "run_code" not in session.bound_names
    result = _detail(engine, "mcp_docs_search.query")
    assert result.success
    assert engine._loaded_tool_names == {"mcp_docs_search"}
    loaded = builder.build_v5_tools()
    assert loaded is not initial
    assert "mcp_docs_search" in _names(loaded)
    assert "mcp_private_search" not in _names(loaded)
    assert builder.build_v5_tools() is loaded
    assert engine.registry.call_tool("mcp_docs_search", {"query": "hello"}).model_text == "found: hello"


def test_default_core_is_explicit_and_every_deferred_tool_is_discoverable(engine: SimpleNamespace) -> None:
    from excelmanus.tools.catalog import catalog_from_engine

    schemas = MetaToolBuilder(engine).build_v5_tools()
    catalog = catalog_from_engine(engine)
    assert _names(schemas) == DEFAULT_DISCLOSURE_CORE_TOOLS & catalog.name_set()
    deferred = catalog.name_set() - _names(schemas)
    assert {
        "compare_spreadsheets", "trace_spreadsheet_formulas", "split_spreadsheet",
        "manage_spreadsheet_objects", "manage_spreadsheet_versions",
        "read_text_file", "copy_file", "run_shell", "read_word", "write_word",
        "delegate", "list_subagents", "manage_skills", "memory_read_topic",
        "mcp_docs_search", "mcp_private_search",
    } <= deferred
    navigation = catalog.capability_map_text()
    assert "普通内置工具与 MCP" in navigation
    status = _detail(engine, query_type="system_status").model_text
    for name in deferred:
        assert name in navigation
        assert name in status
        assert name in _detail(engine, query_type="can_i_do", query=name).model_text
    for category, expected in {
        "objects": "manage_spreadsheet_objects", "versions": "manage_spreadsheet_versions",
        "word": "read_word", "file": "copy_file", "agents": "delegate",
        "skills": "manage_skills", "mcp": "mcp_docs_search",
    }.items():
        assert expected in _detail(engine, query_type="category_tools", query=category).model_text
    assert not engine._loaded_tool_names  # 导航不隐式加载整份目录。
    assert '"properties"' not in navigation and "em." not in navigation


def test_builtin_batch_detail_loads_schemas_without_narrowing_sdk(engine: SimpleNamespace) -> None:
    builder = MetaToolBuilder(engine)
    initial = _names(builder.build_v5_tools())
    assert "compare_spreadsheets" not in initial and "manage_spreadsheet_versions" not in initial
    _detail(engine, queries=[
        {"query_type": "tool_detail", "query": "compare_spreadsheets"},
        "malformed query",
        {"query_type": "tool_detail", "query": "manage_spreadsheet_versions.query"},
        {"query_type": "tool_detail", "query": "manage_spreadsheet_objects.missing"},
    ])
    loaded = _names(builder.build_v5_tools())
    assert loaded - initial == {"compare_spreadsheets", "manage_spreadsheet_versions"}
    session = build_session_for_run_code(SimpleNamespace(_engine=engine), root_call_id="builtins")
    assert {"manage_spreadsheet_objects", "split_spreadsheet", "read_word", "run_shell"} <= session.bound_names


def test_registration_schema_change_and_removal_refresh_disclosure(engine: SimpleNamespace) -> None:
    builder = MetaToolBuilder(engine)
    builder.build_v5_tools()
    _detail(engine, "custom_report")
    assert not engine._loaded_tool_names
    engine.registry.register_tool(ToolDef(
        name="custom_report", description="Generate custom reports",
        input_schema={"type": "object", "properties": {"title": {"type": "string"}}},
        func=lambda title="": title, write_effect="none",
    ))
    assert "custom_report" not in _names(builder.build_v5_tools())
    assert "custom_report" in _detail(engine, query_type="category_tools", query="other").model_text
    _detail(engine, "custom_report.title")
    loaded = builder.build_v5_tools()
    assert "custom_report" in _names(loaded)
    engine.registry.get_tool("custom_report").input_schema["properties"]["title"]["enum"] = ["updated"]
    refreshed = builder.build_v5_tools()
    assert refreshed is not loaded
    schema = next(item["function"]["parameters"] for item in refreshed if item["function"]["name"] == "custom_report")
    assert schema["properties"]["title"]["enum"] == ["updated"]
    # 模拟连接器卸载注册项；旧 loaded 名不保留执行或披露权。
    engine.registry._tools.pop("custom_report")
    assert "custom_report" not in _names(builder.build_v5_tools())
    assert "工具不可用" in _detail(engine, "custom_report").model_text


def test_workspace_family_removal_revokes_loaded_word_schema(engine: SimpleNamespace) -> None:
    builder = MetaToolBuilder(engine)
    builder.build_v5_tools()
    _detail(engine, "read_word")
    assert "read_word" in _names(builder.build_v5_tools())
    (Path(engine.config.workspace_root) / "notes.docx").unlink()
    assert "read_word" not in _names(builder.build_v5_tools())
    assert "工具不可用" in _detail(engine, "read_word").model_text
    session = build_session_for_run_code(SimpleNamespace(_engine=engine), root_call_id="after-family-removal")
    assert "read_word" not in session.bound_names


def test_unknown_or_failed_detail_does_not_load_and_batch_uses_exact_ids(engine: SimpleNamespace) -> None:
    MetaToolBuilder(engine).build_v5_tools()
    for query in ("mcp_missing", "mcp_docs_search.missing", "mcp_docs_search.output"):
        _detail(engine, query)
    assert not engine._loaded_tool_names
    _detail(engine, queries=[
        {"query_type": "tool_detail", "query": "mcp_docs_search.query"},
        {"query_type": "tool_detail", "query": "mcp_private_search"},
        {"query_type": "can_i_do", "query": "mcp_missing"},
    ])
    assert engine._loaded_tool_names == {"mcp_docs_search", "mcp_private_search"}


def test_loaded_survives_profile_changes_but_not_new_turn(engine: SimpleNamespace, monkeypatch) -> None:
    from excelmanus.system_one.host import clear_turn_exposure

    builder = MetaToolBuilder(engine)
    builder.build_v5_tools()
    _detail(engine, "mcp_docs_search")
    monkeypatch.setattr("excelmanus.system_one.host.turn_wire_profile", lambda _: "minimal")
    assert {"mcp_docs_search", "run_code"} <= _names(builder.build_v5_tools())
    clear_turn_exposure(engine)
    assert not engine._loaded_tool_names
    assert "mcp_docs_search" not in _names(builder.build_v5_tools())


def test_web_initial_profile_can_include_mcp(engine: SimpleNamespace, monkeypatch) -> None:
    monkeypatch.setattr("excelmanus.system_one.host.turn_wire_profile", lambda _: "web")
    assert "mcp_docs_search" in _names(MetaToolBuilder(engine).build_v5_tools())


def test_child_permission_intersection_and_state_isolation(engine: SimpleNamespace) -> None:
    parent_builder = MetaToolBuilder(engine)
    parent_builder.build_v5_tools()
    _detail(engine, "mcp_private_search")
    child = SimpleNamespace(**vars(engine))
    child.registry = child._registry = engine.registry.fork()
    child._loaded_tool_names = set()
    child._tools_cache = None
    child._is_host_session = False
    child._fixed_capability = CallerCapability(
        allowed_tools=frozenset({"introspect_capability", "mcp_docs_search"}),
    )
    builder = MetaToolBuilder(child)
    builder.build_v5_tools()
    denied = _detail(child, "mcp_private_search")
    assert "不可用" in denied.model_text
    assert not child._loaded_tool_names
    _detail(child, "mcp_docs_search")
    assert child._loaded_tool_names == {"mcp_docs_search"}
    assert engine._loaded_tool_names == {"mcp_private_search"}
    assert _names(builder.build_v5_tools()) == {"introspect_capability", "mcp_docs_search"}
    child._fixed_capability = CallerCapability(allowed_tools=frozenset({"introspect_capability"}))
    assert _names(builder.build_v5_tools()) == {"introspect_capability"}
    session = build_session_for_run_code(SimpleNamespace(_engine=child), root_call_id="child")
    assert session.bound_names == frozenset({"introspect_capability"})


def test_no_discovery_entry_keeps_authorized_mcp_visible(engine: SimpleNamespace) -> None:
    engine._fixed_capability = CallerCapability(allowed_tools=frozenset({"mcp_docs_search"}))
    assert _names(MetaToolBuilder(engine).build_v5_tools()) == {"mcp_docs_search"}


def test_no_discovery_entry_does_not_lose_builtins_to_profile(engine: SimpleNamespace, monkeypatch) -> None:
    engine._fixed_capability = CallerCapability(allowed_tools=frozenset({"compare_spreadsheets", "mcp_docs_search"}))
    monkeypatch.setattr("excelmanus.system_one.host.turn_wire_profile", lambda _: "minimal")
    assert _names(MetaToolBuilder(engine).build_v5_tools()) == {"compare_spreadsheets", "mcp_docs_search"}


@pytest.mark.asyncio
async def test_dispatcher_injects_session_local_load_state(engine: SimpleNamespace, monkeypatch) -> None:
    from excelmanus.engine_core.tool_dispatcher import ToolDispatcher

    builder = MetaToolBuilder(engine)
    builder.build_v5_tools()
    engine.sandbox_env = {}
    dispatcher = ToolDispatcher(engine)
    monkeypatch.setattr(dispatcher, "is_cancelled", lambda: False)
    monkeypatch.setattr(dispatcher, "consume_call_budget", lambda: True)

    async def inner(*args):
        return engine.registry.call_tool("introspect_capability", {
            "query_type": "tool_detail", "query": "mcp_docs_search",
        })

    monkeypatch.setattr(dispatcher, "_execute_inner", inner)
    result = await dispatcher.execute(SimpleNamespace(
        id="detail", function=SimpleNamespace(name="introspect_capability"),
    ), None, None, 0)
    assert result.success
    assert "mcp_docs_search" in _names(builder.build_v5_tools())


@pytest.mark.asyncio
async def test_sdk_bridge_preserves_required_null_and_optional_omission(tmp_path: Path, monkeypatch) -> None:
    from excelmanus.code_mode import CodeModeSession, _py_type_of

    schema = {
        "type": "object",
        "$defs": {"nullable": {"anyOf": [{"type": "integer"}, {"type": "null"}]}},
        "properties": {
            "required_null": {"$ref": "#/$defs/nullable"},
            "optional_null": {"type": ["string", "null"]},
            "optional_text": {"type": "string"},
        },
        "required": ["required_null"],
    }
    tool = ToolDef(name="nullable_tool", description="nullable", input_schema=schema, func=lambda **_: None)
    assert _py_type_of(schema["properties"]["required_null"], root=schema) == "int | None"
    dispatcher = AsyncMock()
    dispatcher.call_registry_tool.return_value = ToolResult(success=True, model_text="ok", value={})
    session = CodeModeSession(dispatcher=dispatcher, root_call_id="null", bridge_dir=tmp_path / "bridge", call_timeout=3)
    namespace: dict = {}
    exec(compile(render_sdk_source([tool]), "<sdk>", "exec"), namespace)
    monkeypatch.setenv("EXCELMANUS_CODE_MODE_BRIDGE", str(session.bridge_dir))
    monkeypatch.setenv("EXCELMANUS_CODE_MODE_TIMEOUT", "3")
    session.start()
    try:
        await asyncio.to_thread(namespace["nullable_tool"], None)
        assert dispatcher.call_registry_tool.await_args.kwargs["arguments"] == {"required_null": None}
        await asyncio.to_thread(namespace["nullable_tool"], None, optional_null=None, optional_text=None)
        assert dispatcher.call_registry_tool.await_args.kwargs["arguments"] == {
            "required_null": None, "optional_null": None,
        }
    finally:
        session.stop()


@pytest.mark.parametrize("spec", [
    {"$ref": "https://invalid.example/schema.json"},
    {"$ref": "#/missing"},
    {"$ref": "#/$defs/cycle"},
    {"not": {"type": "string"}},
])
def test_unknown_reference_does_not_fetch_or_break_sdk(spec: dict, monkeypatch) -> None:
    import socket
    import urllib.request

    def no_network(*args, **kwargs):
        raise AssertionError("SDK generation must not resolve remote schemas")

    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(urllib.request, "urlopen", no_network)
    tool = ToolDef(
        name="mcp_unknown", description="unknown schema", func=lambda **_: None,
        input_schema={
            "type": "object", "$defs": {"cycle": {"$ref": "#/$defs/cycle"}},
            "properties": {"value": spec},
        },
    )
    namespace: dict = {}
    exec(compile(render_sdk_source([tool]), "<sdk>", "exec"), namespace)
    namespace["_call_host"] = lambda name, args: args
    assert namespace["mcp_unknown"](value=None) == {"value": None}


@pytest.mark.parametrize("nullable", [False, True])
def test_sdk_alias_null_matches_canonical_and_conflicting_values_are_rejected(nullable: bool) -> None:
    value_type = ["string", "null"] if nullable else "string"
    tool = ToolDef(
        name="alias_tool", description="alias", func=lambda **_: None,
        input_schema={"type": "object", "properties": {
            "file_path": {"type": value_type}, "path": {"type": value_type},
        }},
    )
    namespace: dict = {}
    exec(compile(render_sdk_source([tool]), "<sdk>", "exec"), namespace)
    unset = namespace["_EM_UNSET"]
    namespace["_call_host"] = lambda name, args: {k: v for k, v in args.items() if v is not unset}
    expected = {"file_path": None} if nullable else {}
    assert namespace["alias_tool"](path=None) == expected
    assert namespace["alias_tool"](file_path=None) == expected
    assert namespace["alias_tool"](file_path="a.xlsx", path="a.xlsx") == {"file_path": "a.xlsx"}
    with pytest.raises(TypeError, match="同时给出"):
        namespace["alias_tool"](file_path="a.xlsx", path="b.xlsx")
    if nullable:
        with pytest.raises(TypeError, match="同时给出"):
            namespace["alias_tool"](file_path=None, path="b.xlsx")
