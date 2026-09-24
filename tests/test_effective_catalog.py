"""EffectiveToolCatalog：可见集 / 索引 / introspect / digest 单一推导。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from excelmanus.tools.catalog import (
    derive_effective_catalog,
    resolve_catalog_mode,
)
from excelmanus.tools.introspection_tools import (
    bind_introspection_catalog,
    introspect_capability,
    register_introspection_tools,
)
from excelmanus.tools.policy import is_mutating_write_effect
from excelmanus.tools.registry import ToolDef, ToolRegistry


def _tool(name: str, *, effect: str = "none", description: str | None = None) -> ToolDef:
    return ToolDef(
        name=name,
        description=description or f"desc {name}",
        input_schema={"type": "object", "properties": {"x": {"type": "string"}}},
        func=lambda: None,
        write_effect=effect,  # type: ignore[arg-type]
    )


def _schema_names(schemas: list[dict]) -> list[str]:
    names: list[str] = []
    for schema in schemas:
        func = schema.get("function") if isinstance(schema, dict) else None
        if isinstance(func, dict) and func.get("name"):
            names.append(str(func["name"]))
        elif isinstance(schema, dict) and schema.get("name"):
            names.append(str(schema["name"]))
    return names


def _index_names(text: str) -> set[str]:
    names: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("- "):
            continue
        body = stripped[2:]
        name = body.split(" —", 1)[0].strip()
        if name:
            names.add(name)
    return names


def _domain_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register_tools(
        [
            _tool("observe_spreadsheet", effect="none", description="只读探查 Excel"),
            _tool("analyze_spreadsheet", effect="none"),
            _tool("apply_spreadsheet_changes", effect="workspace_write", description="原子编辑写值"),
            _tool("write_text_file", effect="workspace_write"),
            _tool("run_code", effect="dynamic"),
            _tool("run_shell", effect="dynamic"),
            _tool("write_plan", effect="none"),
        ]
    )
    return registry


class TestModeMapping:
    def test_read_plan_and_write(self) -> None:
        assert resolve_catalog_mode(chat_mode="read") == "read"
        assert resolve_catalog_mode(chat_mode="plan") == "plan"
        assert resolve_catalog_mode(chat_mode="write", tool_access="read_only") == "read"
        assert resolve_catalog_mode(chat_mode="plan", tool_access="read_only") == "plan"
        assert resolve_catalog_mode(chat_mode="write") == "write"


class TestVisibleEqualsExecutable:
    def test_read_schemas_have_no_write_effect_tools(self) -> None:
        catalog = derive_effective_catalog(
            tools=_domain_registry().get_all_tools(),
            mode="read",
        )
        names = set(catalog.names())
        assert "observe_spreadsheet" in names
        assert "analyze_spreadsheet" in names
        assert "write_plan" not in names
        assert "apply_spreadsheet_changes" not in names
        assert "write_text_file" not in names
        assert "run_code" not in names
        assert "run_shell" not in names
        for tool in catalog.tools:
            assert not is_mutating_write_effect(tool.write_effect)

    def test_plan_schemas_have_no_write_effect_tools_but_keep_reads(self) -> None:
        catalog = derive_effective_catalog(
            tools=_domain_registry().get_all_tools(),
            mode="plan",
        )
        names = set(catalog.names())
        assert "observe_spreadsheet" in names
        assert "write_plan" in names
        assert "apply_spreadsheet_changes" not in names
        assert "write_text_file" not in names
        assert "run_code" not in names
        for tool in catalog.tools:
            assert not is_mutating_write_effect(tool.write_effect)

    def test_write_keeps_mutating_tools(self) -> None:
        catalog = derive_effective_catalog(
            tools=_domain_registry().get_all_tools(),
            mode="write",
        )
        names = set(catalog.names())
        assert "apply_spreadsheet_changes" in names
        assert "run_code" in names
        assert "observe_spreadsheet" in names
        assert "write_plan" not in names

    def test_write_xlsx_family_hides_word_mcp_plan(self) -> None:
        registry = _domain_registry()
        registry.register_tools(
            [
                _tool("read_word"),
                _tool("write_word"),
                _tool("inspect_word"),
                _tool("search_word"),
                _tool("mcp_exa_search"),
                _tool("exit_plan_mode"),
            ]
        )
        catalog = derive_effective_catalog(
            tools=registry.get_all_tools(),
            mode="write",
            families=frozenset({"xlsx"}),
        )
        names = set(catalog.names())
        assert "observe_spreadsheet" in names
        assert "read_word" not in names
        assert "write_word" not in names
        assert "mcp_exa_search" not in names
        assert "write_plan" not in names
        assert "exit_plan_mode" not in names

    def test_csv_only_hides_trace_and_objects(self) -> None:
        registry = _domain_registry()
        registry.register_tools(
            [
                _tool("trace_spreadsheet_formulas"),
            ]
        )
        catalog = derive_effective_catalog(
            tools=registry.get_all_tools(),
            mode="write",
            families=frozenset({"csv"}),
            disallowed=("trace_spreadsheet_formulas", "apply_spreadsheet_changes"),
        )
        names = set(catalog.names())
        assert "observe_spreadsheet" in names
        assert "trace_spreadsheet_formulas" not in names
        assert "apply_spreadsheet_changes" not in names

    @pytest.mark.parametrize("mode", ["code", "both", "unknown", "", None])
    def test_invalid_catalog_mode_does_not_expand_permissions(self, mode) -> None:
        registry = _domain_registry()
        registry.bind_catalog(mode="read")
        for operation in (
            lambda: derive_effective_catalog(tools=registry.get_all_tools(), mode=mode),
            lambda: resolve_catalog_mode(chat_mode=mode),
            lambda: registry.bind_catalog(mode=mode),
        ):
            with pytest.raises(ValueError, match="unknown catalog mode"):
                operation()
        assert "apply_spreadsheet_changes" not in registry.effective_catalog().name_set()

    def test_readonly_subagent_keeps_run_code(self) -> None:
        catalog = derive_effective_catalog(
            tools=_domain_registry().get_all_tools(),
            mode="read",
            allow_run_code=True,
        )
        names = set(catalog.names())
        assert "run_code" in names
        assert "apply_spreadsheet_changes" not in names


class TestIndexSubsetOfSchemas:
    @pytest.mark.parametrize("mode", ["read", "plan", "write"])
    def test_index_names_subset_of_schema_names(self, mode: str) -> None:
        catalog = derive_effective_catalog(
            tools=_domain_registry().get_all_tools(),
            mode=mode,  # type: ignore[arg-type]
        )
        schema_names = set(_schema_names(catalog.tool_schemas()))
        index_names = _index_names(catalog.tool_index_text())
        assert index_names <= schema_names
        assert schema_names == set(catalog.names())

    def test_read_index_does_not_name_write_tools(self) -> None:
        catalog = derive_effective_catalog(
            tools=_domain_registry().get_all_tools(),
            mode="read",
        )
        text = catalog.tool_index_text()
        assert "observe_spreadsheet" in text
        assert "apply_spreadsheet_changes" not in text
        assert "write_text_file" not in text


class TestIntrospectionBoundToCatalog:
    def test_hidden_tool_detail_is_unavailable(self) -> None:
        registry = _domain_registry()
        register_introspection_tools(registry)
        catalog = derive_effective_catalog(tools=registry.get_all_tools(), mode="read")
        bind_introspection_catalog(catalog)
        result = introspect_capability("tool_detail", "apply_spreadsheet_changes")
        assert "不可用" in result
        assert "apply_spreadsheet_changes" in result

    def test_can_i_do_does_not_recommend_hidden_or_missing_tools(self) -> None:
        registry = ToolRegistry()
        registry.register_tools(
            [
                _tool("observe_spreadsheet", effect="none", description="只读探查 Excel 数据"),
                _tool("list_directory", effect="none", description="列出目录"),
            ]
        )
        register_introspection_tools(registry)
        catalog = derive_effective_catalog(tools=registry.get_all_tools(), mode="read")
        bind_introspection_catalog(catalog)
        hidden = introspect_capability("can_i_do", "原子编辑写值公式")
        assert "apply_spreadsheet_changes" not in hidden
        assert "write_text_file" not in hidden
        visible = introspect_capability("can_i_do", "读取 Excel 数据")
        assert "observe_spreadsheet" in visible

    def test_can_i_do_does_not_recommend_unregistered_global_table_tools(self) -> None:
        registry = ToolRegistry()
        registry.register_tool(
            _tool("observe_spreadsheet", effect="none", description="只读探查 Excel 数据")
        )
        register_introspection_tools(registry)
        result = introspect_capability("can_i_do", "原子编辑写值公式")
        assert "apply_spreadsheet_changes" not in result


class TestDigestStability:
    def test_same_input_stable(self) -> None:
        tools = _domain_registry().get_all_tools()
        first = derive_effective_catalog(tools=tools, mode="write").digest()
        second = derive_effective_catalog(tools=list(reversed(tools)), mode="write").digest()
        assert first == second
        assert first == derive_effective_catalog(tools=tools, mode="write").digest()

    def test_mcp_add_changes_digest(self) -> None:
        base = list(_domain_registry().get_all_tools())
        before = derive_effective_catalog(tools=base, mode="write").digest()
        mcp = _tool("mcp_search_docs", effect="none", description="MCP search")
        after = derive_effective_catalog(
            tools=base,
            extra_tools=[mcp],
            mode="write",
            families=frozenset({"xlsx", "mcp"}),
        ).digest()
        assert after != before
        removed = derive_effective_catalog(tools=base, mode="write").digest()
        assert removed == before

    def test_skill_add_changes_digest(self) -> None:
        tools = _domain_registry().get_all_tools()
        before = derive_effective_catalog(tools=tools, mode="write", skill_names=()).digest()
        after = derive_effective_catalog(
            tools=tools,
            mode="write",
            skill_names=("data_basic",),
        ).digest()
        assert after != before
        reordered = derive_effective_catalog(
            tools=tools,
            mode="write",
            skill_names=("zzz", "aaa"),
        ).digest()
        same = derive_effective_catalog(
            tools=tools,
            mode="write",
            skill_names=("aaa", "zzz"),
        ).digest()
        assert reordered == same

    def test_mode_change_changes_digest(self) -> None:
        tools = _domain_registry().get_all_tools()
        write_digest = derive_effective_catalog(tools=tools, mode="write").digest()
        read_digest = derive_effective_catalog(tools=tools, mode="read").digest()
        assert write_digest != read_digest


class TestSchemaSortStable:
    def test_mixed_mcp_and_domain_sorted_by_name(self) -> None:
        tools = [
            _tool("mcp_zzz_write", effect="none"),
            _tool("observe_spreadsheet", effect="none"),
            _tool("mcp_aaa_search", effect="none"),
            _tool("analyze_spreadsheet", effect="none"),
        ]
        catalog = derive_effective_catalog(
            tools=list(reversed(tools)),
            mode="write",
            families=frozenset({"xlsx", "mcp"}),
        )
        names = _schema_names(catalog.tool_schemas())
        assert names == sorted(names)
        assert names == [
            "analyze_spreadsheet",
            "mcp_aaa_search",
            "mcp_zzz_write",
            "observe_spreadsheet",
        ]

    def test_extra_tools_order_does_not_affect_schemas_or_digest(self) -> None:
        domain = [_tool("observe_spreadsheet", effect="none")]
        mcp_a = _tool("mcp_alpha", effect="none")
        mcp_b = _tool("mcp_beta", effect="none")
        left = derive_effective_catalog(
            tools=domain, extra_tools=[mcp_b, mcp_a], mode="write",
            families=frozenset({"xlsx", "mcp"}),
        )
        right = derive_effective_catalog(
            tools=domain, extra_tools=[mcp_a, mcp_b], mode="write",
            families=frozenset({"xlsx", "mcp"}),
        )
        assert _schema_names(left.tool_schemas()) == _schema_names(right.tool_schemas())
        assert left.digest() == right.digest()


class TestRegistryDigestDelegates:
    def test_catalog_digest_matches_effective_catalog(self) -> None:
        registry = _domain_registry()
        catalog = derive_effective_catalog(tools=registry.get_all_tools(), mode="write")
        assert registry.catalog_digest() == catalog.digest()

    def test_bind_read_changes_registry_digest(self) -> None:
        registry = _domain_registry()
        write_digest = registry.catalog_digest()
        registry.bind_catalog(mode="read")
        read_digest = registry.catalog_digest()
        assert read_digest != write_digest
        names = _schema_names(registry.get_tiered_schemas(mode="chat_completions"))
        assert "apply_spreadsheet_changes" not in names
        assert "observe_spreadsheet" in names


class TestMetaToolBuilderProjection:
    def test_read_mode_builder_hides_writes(self) -> None:
        from excelmanus.engine_core.meta_tools import MetaToolBuilder

        registry = _domain_registry()
        engine = MagicMock()
        engine._registry = registry
        engine.registry = registry
        engine._active_skills = []
        engine._tools_cache = None
        engine._tools_cache_key = None
        engine._current_chat_mode = "read"
        engine._fixed_capability = None
        engine._skill_router = None
        engine._skill_resolver = None
        engine._subagent_config = None
        builder = MetaToolBuilder(engine)
        builder.build_meta_tools = MagicMock(return_value=[])
        names = set(_schema_names(builder.build_v5_tools_impl()))
        assert "observe_spreadsheet" in names
        assert "apply_spreadsheet_changes" not in names
        assert "run_code" not in names


def test_versions_tool_visible_in_read_plan_but_write_action_still_blocked() -> None:
    """回归锁：只读 action 面在 read/plan 可见，但写 action 仍被判定为写效应。"""
    from excelmanus.tools.policy import write_effect_for_call

    registry = ToolRegistry()
    registry.register_tools(
        [
            _tool("manage_spreadsheet_versions", effect="workspace_write"),
            _tool("apply_spreadsheet_changes", effect="workspace_write"),
        ]
    )
    for mode in ("read", "plan"):
        catalog = derive_effective_catalog(tools=registry.get_all_tools(), mode=mode)
        assert "manage_spreadsheet_versions" in catalog.names()
        assert "apply_spreadsheet_changes" not in catalog.names()
    assert (
        write_effect_for_call(
            "manage_spreadsheet_versions", {"action": "restore"}, declared="workspace_write"
        )
        == "workspace_write"
    )
    assert (
        write_effect_for_call(
            "manage_spreadsheet_versions", {"action": "list"}, declared="workspace_write"
        )
        == "none"
    )


def test_inspect_workspace_csv_profile(tmp_path) -> None:
    from excelmanus.tools.catalog import inspect_workspace_catalog

    (tmp_path / "only.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    flags = inspect_workspace_catalog(str(tmp_path))
    assert flags["profile"] == "csv"
    assert "csv" in flags["families"]
    assert "xlsx" not in flags["families"]
    assert flags["new_workbook"] is True


def test_builtin_versions_tool_declares_workspace_write(tmp_path) -> None:
    """真实内置注册表：声明为写效应，且 read 目录仍可见（因含只读 action）。"""
    registry = ToolRegistry()
    registry.register_builtin_tools(str(tmp_path))
    tool = registry.get_tool("manage_spreadsheet_versions")
    assert tool is not None
    assert tool.write_effect == "workspace_write"
    assert is_mutating_write_effect(tool.write_effect) is True
    read_catalog = derive_effective_catalog(tools=registry.get_all_tools(), mode="read")
    assert "manage_spreadsheet_versions" in read_catalog.names()


def test_wire_projection_prunes_nested_schema(tmp_path) -> None:
    """出网投影：深层 description 剥离、$defs 引用坍缩；input_schema 与 tool_detail 不动。"""
    registry = ToolRegistry()
    registry.register_builtin_tools(str(tmp_path))
    catalog = derive_effective_catalog(tools=registry.get_all_tools(), mode="write")
    tool = registry.get_tool("apply_spreadsheet_changes")
    assert tool is not None
    original = tool.input_schema
    schemas = catalog.tool_schemas()
    wire = next(
        s for s in schemas
        if (s.get("function") or s).get("name") == "apply_spreadsheet_changes"
    )
    params = (wire.get("function") or wire)["parameters"]
    # 字段树按需披露，但外层 object|string 合同不能缩窄成 object。
    spec = params["properties"]["workbook_spec"]
    assert spec["type"] == "object"
    assert "tool_detail" in spec["description"]
    # 无剩余 $ref 时 $defs 整体移除
    assert "$defs" not in params
    # operations 深层字段描述剥离，但字段名/枚举保留
    branches = params["properties"]["operations"]["items"]["oneOf"]
    assert all(b["properties"]["kind"].get("enum") or b["properties"]["kind"].get("const") for b in branches)
    assert all("description" not in p for b in branches for p in b["properties"].values())
    assert "tool_detail" in params["properties"]["operations"]["description"]
    # 运行时合同不受影响：input_schema 原样
    assert tool.input_schema is original
    assert "$defs" in original


def test_error_next_step_only_on_capability_gaps(tmp_path) -> None:
    """next_step 只出现在命名算子真表达不了的错误上。"""
    from excelmanus.tools.workbook_tools import (
        compare_spreadsheets,
        apply_spreadsheet_changes,
    )
    from excelmanus.workbook_commit import content_version_of_file

    registry = ToolRegistry()
    registry.register_builtin_tools(str(tmp_path))
    book = tmp_path / "book.xlsx"
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws["A1"], ws["B1"] = "a", "b"
    wb.save(book)
    styled = compare_spreadsheets(
        file_a=str(book), file_b=str(book), ignore_style=False,
    )
    assert styled.error is not None
    assert styled.error.fields.get("next_step") == "run_code"
    from excelmanus.tools.context import use_workspace
    with use_workspace(tmp_path):
        bad_obj = apply_spreadsheet_changes(file_path=str(book), operations=[{"kind":"image", "sheet":"Sheet"}], expected_version=content_version_of_file(book))
    assert bad_obj.error is not None
    assert "image_path" in (bad_obj.error.message or "")
    assert "next_step" not in (bad_obj.error.fields or {})


def test_exposure_does_not_change_catalog_digest(tmp_path) -> None:
    """披露状态不改变执行目录或其 digest。"""
    from excelmanus.prompt.envelope import digest_tools, epoch_changed, compute_epoch_identity
    from excelmanus.tools.catalog import catalog_from_engine, execution_catalog_from_engine

    registry = _domain_registry()
    engine = SimpleNamespace(
        _registry=registry,
        registry=registry,
        _current_chat_mode="write",
        _turn_exposure=None,
        _active_skills=[],
        _tools_cache=None,
        _skill_router=None,
        _skill_resolver=None,
        _subagent_config=None,
        _fixed_capability=None,
        config=SimpleNamespace(workspace_root=str(tmp_path)),
    )
    before = catalog_from_engine(engine)
    assert before is not None
    before_digest = before.digest()
    before_schemas = before.tool_schemas()
    engine._turn_exposure = {"profile": "inspect", "applied": False, "wire_narrow": False}
    after = catalog_from_engine(engine)
    assert after is not None
    assert after.mode == "write"
    assert after.digest() == before_digest
    assert registry.catalog_digest() == before_digest
    assert "observe_spreadsheet" in after.name_set()
    assert "run_code" in after.name_set()
    after_schemas = after.tool_schemas()
    after_names = {
        (s.get("function") or {}).get("name") or s.get("name")
        for s in after_schemas
    }
    assert after_names == after.name_set()
    execution = execution_catalog_from_engine(engine)
    assert execution is not None
    assert execution.mode == "write"
    assert execution.digest() == before_digest
    before_id = compute_epoch_identity(
        session_id="s1",
        model="m",
        protocol="openai|https://x",
        call_config={"temperature": 0.2},
        tools=before_schemas,
        system="SYS",
        catalog_digest=before_digest,
        wire_payload=[{"role": "user", "content": "a"}],
    )
    after_id = compute_epoch_identity(
        session_id="s1",
        model="m",
        protocol="openai|https://x",
        call_config={"temperature": 0.2},
        tools=after_schemas,
        system="SYS",
        catalog_digest=after.digest(),
        wire_payload=[{"role": "user", "content": "a"}],
    )
    assert epoch_changed(before_id, after_id) is False
    assert before_id.tools_digest == after_id.tools_digest
    assert digest_tools(before_schemas) == digest_tools(after_schemas)
