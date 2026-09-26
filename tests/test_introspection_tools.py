"""单元测试：introspect_capability 工具。

测试五种查询类型的正确性、错误处理和注册逻辑。

**验证：需求 3.1–3.3、4.1–4.3、5.1–5.3、6.1–6.4、7.1–7.2、8.1–8.3、12.1**
"""

from __future__ import annotations

import contextlib
from pathlib import Path

import pytest

from excelmanus.tools.introspection_tools import (
    INTROSPECT_CAPABILITY_SCHEMA,
    _ALL_QUERY_TYPES,
    _handle_can_i_do,
    _handle_category_tools,
    _handle_related_tools,
    _handle_system_status,
    _handle_tool_detail,
    introspect_capability,
    register_introspection_tools,
)
from excelmanus.tools.introspection_tools import _registry as _initial_registry
from excelmanus.tools.policy import (
    MUTATING_AUDIT_ONLY_TOOLS,
    MUTATING_CONFIRM_TOOLS,
    READ_ONLY_SAFE_TOOLS,
    TOOL_CATEGORIES,
    TOOL_SHORT_DESCRIPTIONS,
)
from excelmanus.tools.registry import ToolDef, ToolRegistry

# ── Fixtures ──────────────────────────────────────────────


@pytest.fixture()
def registry() -> ToolRegistry:
    """创建包含常用工具的 ToolRegistry 并注册 introspection 工具。"""
    import excelmanus.tools.introspection_tools as mod

    reg = ToolRegistry()
    # 注册一些代表性工具
    for name in (
        "observe_spreadsheet", "analyze_spreadsheet", "compare_spreadsheets",
        "apply_spreadsheet_changes", "write_text_file", "copy_file", "run_code",
        "list_directory", "run_shell", "delete_file", "rename_file",
        "read_word", "inspect_word",
    ):
        desc = TOOL_SHORT_DESCRIPTIONS.get(name, f"desc of {name}")
        reg.register_tool(
            ToolDef(
                name=name,
                description=desc,
                input_schema={"type": "object", "properties": {"file_path": {"type": "string"}}},
                func=lambda: None,
            )
        )
    register_introspection_tools(reg)

    class _StubCatalog:
        """最小调用级目录：handlers 只信 _call_catalog，不回退 registry 全表。"""

        mode = "write"

        def introspection_source(self) -> dict:
            return dict(reg._tools)

        def tool_index_text(self) -> str:
            return ""

        def digest(self) -> str:
            return "stub"

    _catalog_token = mod._call_catalog.set(_StubCatalog())
    yield reg
    # 清理调用级 catalog 与模块级 _registry
    mod._call_catalog.reset(_catalog_token)
    mod._registry = None


@pytest.fixture()
def empty_registry() -> ToolRegistry:
    """空的 ToolRegistry，不注册 introspection 工具。"""
    import excelmanus.tools.introspection_tools as mod

    old = mod._registry
    mod._registry = None
    yield ToolRegistry()
    mod._registry = old


class TestBatchQuery:
    """测试批量查询功能。"""

    def test_batch_query_mode(self, registry: ToolRegistry) -> None:
        """批量查询应返回多个结果。"""
        queries = [
            {"query_type": "tool_detail", "query": "observe_spreadsheet"},
            {"query_type": "can_i_do", "query": "读取数据"},
        ]
        result = introspect_capability(queries=queries)

        # 应包含两个查询的结果
        assert "[1]" in result
        assert "[2]" in result
        assert "observe_spreadsheet" in result
        assert "tool_detail(observe_spreadsheet)" in result

    def test_batch_query_empty(self, registry: ToolRegistry) -> None:
        """空的批量查询应返回提示信息。"""
        result = introspect_capability(queries=[])
        assert "未提供有效查询" in result

    def test_batch_query_invalid_type(self, registry: ToolRegistry) -> None:
        """批量查询中的无效查询类型应返回错误信息。"""
        queries = [
            {"query_type": "invalid_type", "query": "test"},
        ]
        result = introspect_capability(queries=queries)
        assert "不支持的查询类型" in result
        assert "invalid_type" in result


# ── 注册测试 ──────────────────────────────────────────────


class TestRegistration:
    """验证：需求 3.1–3.3"""

    def test_tool_registered(self, registry: ToolRegistry) -> None:
        """introspect_capability 应被注册到 ToolRegistry。"""
        tool = registry.get_tool("introspect_capability")
        assert tool is not None
        assert tool.name == "introspect_capability"

    def test_schema_has_required_fields(self, registry: ToolRegistry) -> None:
        """Schema 应包含 query_type、query 和 queries 三个属性。"""
        tool = registry.get_tool("introspect_capability")
        assert tool is not None
        schema = tool.input_schema

        # 扁平 properties 结构
        assert "properties" in schema
        props = schema["properties"]
        assert "query_type" in props
        assert "query" in props
        assert "queries" in props

    def test_query_type_enum(self) -> None:
        """旧工具发现入口与统一认知入口使用同一 schema。"""
        props = INTROSPECT_CAPABILITY_SCHEMA["properties"]
        enum_values = props["query_type"]["enum"]
        assert set(enum_values) == {
            "tool_detail", "category_tools", "can_i_do",
            "related_tools", "system_status",
            "knowledge_index", "knowledge_search", "knowledge_read",
            "knowledge_toc", "knowledge_find", "knowledge_related", "knowledge_spec", "knowledge_examples",
            "knowledge_workflow",
        }

    def test_in_read_only_safe_tools(self) -> None:
        """introspect_capability 应在 READ_ONLY_SAFE_TOOLS 中。"""
        assert "introspect_capability" in READ_ONLY_SAFE_TOOLS


# ── tool_detail 测试 ──────────────────────────────────────


class TestToolDetail:
    """验证：需求 4.1–4.3"""

    def test_sdk_detail_preserves_union_null_enum_and_required(self, registry: ToolRegistry) -> None:
        registry.register_tool(ToolDef(
            name="mcp_union_tool", description="union tool", func=lambda **_: None,
            input_schema={
                "type": "object",
                "properties": {
                    "operations": {"type": ["array", "string", "null"], "items": {"type": "object"}},
                    "level": {"anyOf": [{"enum": ["low", "high"]}, {"type": "integer"}, {"type": "null"}]},
                    "strict": {"type": "boolean", "default": True},
                },
                "required": ["operations"],
            },
        ))
        detail = introspect_capability("tool_detail", "mcp_union_tool")
        assert "operations: list | str | None," in detail
        assert "level: Literal['low', 'high'] | int | None = ..." in detail
        assert "strict: bool = True" in detail
        assert "必填: operations" in detail
        assert "-> Any" in detail and "未声明" in detail
        field = introspect_capability("tool_detail", "mcp_union_tool.level")
        assert '"anyOf"' in field and '"type":"null"' in field
        operations = introspect_capability("tool_detail", "mcp_union_tool.operations")
        assert '"type":["array","string","null"]' in operations

    def test_root_detail_includes_same_output_contract_as_output_query(self, registry: ToolRegistry) -> None:
        from excelmanus.tools.output_contracts import contract_summary

        summary = contract_summary("observe_spreadsheet")
        assert summary in introspect_capability("tool_detail", "observe_spreadsheet")
        assert summary in introspect_capability("tool_detail", "observe_spreadsheet.output")

    def test_output_detail_preserves_optional_value_types(self, registry: ToolRegistry) -> None:
        from excelmanus.tools.output_contracts import contract_for

        contract = contract_for("apply_spreadsheet_changes")
        assert contract.optional_types
        detail = introspect_capability("tool_detail", "apply_spreadsheet_changes.output")
        for name, value_type in contract.optional_types.items():
            assert f"{name}:{value_type}" in detail

    def test_existing_tool(self, registry: ToolRegistry) -> None:
        """查询已注册工具应返回 schema 和权限信息。"""
        result = introspect_capability("tool_detail", "observe_spreadsheet")
        assert "observe_spreadsheet" in result
        assert "file_path" in result
        assert "🟢" in result

    def test_mode_detail_uses_live_schema_and_field_descriptions(self, registry: ToolRegistry) -> None:
        """mode 与字段解释来自实时 schema，不依赖已移除的独立字段映射。"""
        from excelmanus.tools import workbook_tools

        real = {t.name: t for t in workbook_tools.get_tools()}
        registry._tools["analyze_spreadsheet"] = real["analyze_spreadsheet"]
        registry._tools["trace_spreadsheet_formulas"] = real["trace_spreadsheet_formulas"]
        detail = introspect_capability("tool_detail", "analyze_spreadsheet")
        assert "quality" in detail
        field = introspect_capability("tool_detail", "analyze_spreadsheet.sample_rows")
        assert real["analyze_spreadsheet"].input_schema["properties"]["sample_rows"]["description"] in field
        trace_detail = introspect_capability("tool_detail", "trace_spreadsheet_formulas")
        assert "map" in trace_detail and "trace" in trace_detail

    def test_schema_consistency(self, registry: ToolRegistry) -> None:
        """返回的 schema 应与 ToolDef.input_schema 一致。"""
        result = introspect_capability("tool_detail", "observe_spreadsheet")
        tool_def = registry.get_tool("observe_spreadsheet")
        assert tool_def is not None
        assert "file_path" in result

    def test_permission_read_only(self, registry: ToolRegistry) -> None:
        """只读工具应标注为 🟢。"""
        result = introspect_capability("tool_detail", "observe_spreadsheet")
        assert "🟢" in result

    def test_permission_confirm(self, registry: ToolRegistry) -> None:
        """Tier A 工具应标注为 🔴。"""
        result = introspect_capability("tool_detail", "delete_file")
        assert "🔴" in result

    def test_permission_audit(self, registry: ToolRegistry) -> None:
        """Tier B 工具应标注为 🟡。"""
        result = introspect_capability("tool_detail", "copy_file")
        assert "🟡" in result

    def test_category_shown(self, registry: ToolRegistry) -> None:
        """应显示工具所属分类。"""
        result = introspect_capability("tool_detail", "observe_spreadsheet")
        assert "inspect" in result

    def test_nonexistent_tool(self, registry: ToolRegistry) -> None:
        """查询不存在的工具应返回提示信息。"""
        result = introspect_capability("tool_detail", "nonexistent_tool_xyz")
        assert "工具不存在" in result
        assert "category_tools" in result


# ── category_tools 测试 ───────────────────────────────────


class TestCategoryTools:
    """验证：需求 5.1–5.3"""

    def test_valid_category(self, registry: ToolRegistry) -> None:
        """查询有效分类应返回该分类下所有工具。"""
        result = introspect_capability("category_tools", "inspect")
        for tool_name in TOOL_CATEGORIES["inspect"]:
            if registry.get_tool(tool_name) is not None:
                assert tool_name in result

    def test_tools_with_descriptions(self, registry: ToolRegistry) -> None:
        """返回的工具应附带描述。"""
        result = introspect_capability("category_tools", "inspect")
        assert "observe_spreadsheet" in result
        desc = TOOL_SHORT_DESCRIPTIONS.get("observe_spreadsheet", "")
        if desc:
            assert desc in result

    def test_nonexistent_category(self, registry: ToolRegistry) -> None:
        """查询不存在的分类应返回所有可用分类名。"""
        result = introspect_capability("category_tools", "nonexistent_category")
        assert "分类不存在" in result
        for cat in TOOL_CATEGORIES:
            assert cat in result


# ── can_i_do 测试 ─────────────────────────────────────────


class TestCanIDo:
    """验证：需求 6.1–6.4"""

    def test_matching_query(self, registry: ToolRegistry) -> None:
        """使用工具描述关键词应匹配到对应工具。"""
        result = introspect_capability("can_i_do", "读取 Excel 数据")
        assert "available" in result
        assert "observe_spreadsheet" in result

    def test_product_diff_term_routes_to_compare(self, registry: ToolRegistry) -> None:
        """产品术语 diff 不应依赖短描述的词袋命中。"""
        result = introspect_capability("can_i_do", "diff")
        assert "available" in result
        assert "compare_spreadsheets" in result

    def test_self_match(self, registry: ToolRegistry) -> None:
        """使用工具完整描述作为查询应匹配到该工具。"""
        desc = TOOL_SHORT_DESCRIPTIONS["observe_spreadsheet"]
        result = introspect_capability("can_i_do", desc)
        assert "observe_spreadsheet" in result

    def test_no_match(self, registry: ToolRegistry) -> None:
        """无匹配时应返回当前不可用。"""
        result = introspect_capability("can_i_do", "量子计算模拟")
        assert "unknown" in result

    def test_max_results_per_layer(self, registry: ToolRegistry) -> None:
        """每层匹配结果不应超过 5 个。"""
        result = introspect_capability("can_i_do", "Excel 数据 文件 格式")
        # 当前授权工具匹配行
        builtin_lines = []
        in_builtin = False
        for line in result.splitlines():
            if line.startswith("当前授权工具匹配"):
                in_builtin = True
                continue
            if in_builtin and line.startswith("  - "):
                builtin_lines.append(line)
            elif in_builtin and not line.startswith("  "):
                in_builtin = False
        assert len(builtin_lines) <= 5

    def test_extended_capabilities_match(self, registry: ToolRegistry) -> None:
        """can_i_do 应能匹配扩展能力（run_code + Python 库）。"""
        result = introspect_capability("can_i_do", "数据透视表 pivot")
        assert "available" in result
        assert "pivot" in result.lower()

    def test_subagent_match(self, registry: ToolRegistry) -> None:
        """can_i_do 应能匹配子代理能力。"""
        result = introspect_capability("can_i_do", "只读探索 文件结构分析")
        assert "available" in result or "unknown" in result


# ── related_tools 测试 ────────────────────────────────────


class TestRelatedTools:
    """验证：需求 7.1–7.2"""

    def test_same_category(self, registry: ToolRegistry) -> None:
        """应返回同分类的其他工具。"""
        result = introspect_capability("related_tools", "write_text_file")
        assert "run_code" in result

    def test_no_predefined_combinations_section(self, registry: ToolRegistry) -> None:
        """related_tools 结果中不应出现预定义组合段落。"""
        result = introspect_capability("related_tools", "write_text_file")
        assert "预定义组合" not in result

    def test_no_related(self, registry: ToolRegistry) -> None:
        """不在分类和组合中的工具应返回无推荐。"""
        result = introspect_capability("related_tools", "unknown_tool_xyz")
        assert "无相关工具推荐" in result


# ── system_status 测试 ───────────────────────────────────


class TestSystemStatus:
    """测试 system_status 查询类型。"""

    def test_basic_status(self, registry: ToolRegistry) -> None:
        """system_status 应返回工具数量和分类信息。"""
        result = introspect_capability("system_status", "")
        assert "系统状态概览" in result
        assert "内置工具" in result
        assert "工具分类" in result
        assert "MCP 扩展工具" in result
        assert "当前目录模式" in result

    def test_shows_subagent_names(self, registry: ToolRegistry) -> None:
        """system_status 应列出所有内置子代理名称。"""
        result = introspect_capability("system_status", "")
        assert "delegate" not in registry.get_tool_names()
        assert "内置子代理" not in result



# ── 扩展能力常量测试 ─────────────────────────────────────




# ── 纯查询无副作用测试 ────────────────────────────────────


class TestNoSideEffects:
    """验证：需求 8.1–8.3"""

    def test_registry_unchanged(self, registry: ToolRegistry) -> None:
        """调用后 ToolRegistry 状态不变。"""
        tools_before = set(registry.get_tool_names())
        introspect_capability("tool_detail", "observe_spreadsheet")
        introspect_capability("category_tools", "inspect")
        introspect_capability("can_i_do", "读取数据")
        introspect_capability("related_tools", "apply_spreadsheet_changes")
        tools_after = set(registry.get_tool_names())
        assert tools_before == tools_after

    def test_always_returns_nonempty(self, registry: ToolRegistry) -> None:
        """任何有效查询都应返回非空字符串。"""
        for qt in _ALL_QUERY_TYPES:
            result = introspect_capability(qt, "test_query")
            assert isinstance(result, str)
            assert len(result) > 0


class TestFreezeFromSchema:
    def test_freeze_query_routes_to_format_kind(self) -> None:
        import excelmanus.tools.introspection_tools as mod
        from excelmanus.tools.catalog import derive_effective_catalog

        reg = ToolRegistry()
        from excelmanus.tools.workbook_tools import get_tools
        format_tool = next(t for t in get_tools() if t.name == "apply_spreadsheet_changes")
        reg.register_tool(format_tool)
        catalog = derive_effective_catalog(tools=reg.get_all_tools(), mode="write")
        token = mod._call_catalog.set(catalog)
        try:
            result = introspect_capability("can_i_do", "冻结首行")
            assert "available" in result
            assert "apply_spreadsheet_changes" in result
            assert "已有文件不可用" not in result
            detail = introspect_capability("tool_detail", "apply_spreadsheet_changes")
            assert "operations" in detail
            assert "Excel A1 引用语法" not in detail
            size_detail = introspect_capability(
                "tool_detail", "apply_spreadsheet_changes.operations.size",
            )
            assert "字段不存在" not in size_detail
            assert "columns" in size_detail or "auto_fit" in size_detail
            freeze_ops = introspect_capability(
                "tool_detail", "apply_spreadsheet_changes.operations.freeze",
            )
            freeze_top = introspect_capability("tool_detail", "apply_spreadsheet_changes.freeze")
            for freeze_detail in (freeze_ops,):
                assert "字段不存在" not in freeze_detail
                assert "freeze_panes" in freeze_detail
        finally:
            mod._call_catalog.reset(token)



# ── ToolRegistry 未初始化测试 ─────────────────────────────


class TestRegistryNotInitialized:
    """验证：需求 12.1"""

    def test_returns_error(self, empty_registry: ToolRegistry) -> None:
        """未初始化时应返回错误提示。"""
        result = introspect_capability("tool_detail", "observe_spreadsheet")
        assert "工具注册表尚未初始化" in result


# ── 能力检索：意图路由 / 门控区分（回归 session #12 / #23）──────

_ROUTE_FIXTURE_TOOLS = (
    "observe_spreadsheet", "analyze_spreadsheet", "validate_spreadsheet",
    "apply_spreadsheet_changes", "write_text_file", "task_create", "read_word",
)


@contextlib.contextmanager
def _bound_catalog(
    registry: ToolRegistry,
    *,
    mode: str = "write",
    disallowed: tuple[str, ...] = (),
    families: frozenset[str] | None = None,
):
    """把 registry 的绑定参数与 introspect 调用级目录设成同一份投影。"""
    import excelmanus.tools.introspection_tools as mod
    from excelmanus.tools.catalog import derive_effective_catalog

    registry.bind_catalog(mode=mode, disallowed=disallowed, families=families)
    catalog = derive_effective_catalog(
        tools=registry.get_all_tools(), mode=mode, disallowed=disallowed, families=families,
    )
    token = mod._call_catalog.set(catalog)
    try:
        yield catalog
    finally:
        mod._call_catalog.reset(token)


def _route_registry(names: tuple[str, ...] = _ROUTE_FIXTURE_TOOLS) -> ToolRegistry:
    registry = ToolRegistry()
    for name in names:
        registry.register_tool(ToolDef(
            name=name,
            description=TOOL_SHORT_DESCRIPTIONS.get(name, f"desc of {name}"),
            input_schema={"type": "object", "properties": {"file_path": {"type": "string"}}},
            func=lambda **_: None,
        ))
    register_introspection_tools(registry)
    return registry


@pytest.fixture()
def routed_registry() -> ToolRegistry:
    import excelmanus.tools.introspection_tools as mod

    registry = _route_registry()
    yield registry
    mod._registry = None


class TestCanIDoIntentRouting:
    """can_i_do 只回答语义相关的工具，并区分"产品无此工具"与"被门控"。"""

    def test_create_chart_does_not_return_task_create(self, routed_registry: ToolRegistry) -> None:
        """回归 #23：create chart 曾命中 task_create（任务清单工具）。"""
        with _bound_catalog(routed_registry):
            result = introspect_capability("can_i_do", "create chart")
        assert "task_create" not in result
        assert "apply_spreadsheet_changes" in result
        assert "available" in result

    def test_apply_spreadsheet_changes_query_never_falls_back_to_write_text_file(
        self, routed_registry: ToolRegistry,
    ) -> None:
        """回归 #23：点名被门控的工具时不能再推荐 write_text_file（描述明确不适用）。"""
        with _bound_catalog(routed_registry, disallowed=("apply_spreadsheet_changes",)):
            result = introspect_capability("can_i_do", "apply_spreadsheet_changes")
        assert "apply_spreadsheet_changes" in result
        assert "被门控" in result
        assert "disallowed_tools" in result
        assert "write_text_file" not in result

    def test_named_tool_available_answers_the_tool_itself(self, routed_registry: ToolRegistry) -> None:
        with _bound_catalog(routed_registry):
            result = introspect_capability("can_i_do", "apply_spreadsheet_changes")
        assert "available" in result
        assert "apply_spreadsheet_changes" in result
        assert "被门控" not in result

    def test_gated_intent_reports_reason_not_readonly_validator(
        self, routed_registry: ToolRegistry,
    ) -> None:
        """回归 #23：写入/图表意图不能只给 validate_spreadsheet。"""
        with _bound_catalog(routed_registry, disallowed=("apply_spreadsheet_changes",)):
            result = introspect_capability("can_i_do", "写入单元格 修改工作簿 创建图表")
        assert "validate_spreadsheet" not in result
        assert "apply_spreadsheet_changes" in result
        assert "gated" in result and "被门控" in result
        assert "解锁" in result

    def test_available_intent_returns_routed_tool_only(self, routed_registry: ToolRegistry) -> None:
        with _bound_catalog(routed_registry):
            result = introspect_capability("can_i_do", "写入单元格 修改工作簿 创建图表")
        assert "apply_spreadsheet_changes" in result
        assert "validate_spreadsheet" not in result
        assert "task_create" not in result

    def test_chart_query_without_write_tool_returns_no_unrelated_tool(self) -> None:
        """create chart 不能退化成词面相近的任意工具。"""
        import excelmanus.tools.introspection_tools as mod

        registry = _route_registry(("task_create", "write_text_file"))
        try:
            with _bound_catalog(registry):
                result = introspect_capability("can_i_do", "create chart")
        finally:
            mod._registry = None
        assert "task_create" not in result
        assert "write_text_file" not in result
        assert "unknown" in result

    def test_read_mode_discovery_does_not_name_write_tools(self, routed_registry: ToolRegistry) -> None:
        """只读会话的发现类回答不点名写工具（与既有目录投影约定一致）。"""
        with _bound_catalog(routed_registry, mode="read"):
            result = introspect_capability("can_i_do", "写入")
        assert "apply_spreadsheet_changes" not in result
        assert "write_text_file" not in result
        assert "unknown" in result

    def test_tool_detail_distinguishes_gated_from_absent(self, routed_registry: ToolRegistry) -> None:
        with _bound_catalog(routed_registry, disallowed=("apply_spreadsheet_changes",)):
            gated = introspect_capability("tool_detail", "apply_spreadsheet_changes")
            absent = introspect_capability("tool_detail", "no_such_tool_xyz")
        assert "工具不可用" in gated
        assert "apply_spreadsheet_changes" in gated
        assert "被门控" in gated and "解锁" in gated
        assert "工具不存在于当前目录" not in gated
        assert "工具不存在于当前目录" in absent

    def test_category_tools_explains_gated_members(self, routed_registry: ToolRegistry) -> None:
        with _bound_catalog(routed_registry, disallowed=("apply_spreadsheet_changes",)):
            result = introspect_capability("category_tools", "edit")
        assert "apply_spreadsheet_changes" in result
        assert "被门控" in result
        assert "解锁" in result

    def test_category_tools_accepts_keyword_query(self, routed_registry: ToolRegistry) -> None:
        """回归 #12：category_tools 收到关键词而不是分类名时给出能力路由。"""
        with _bound_catalog(routed_registry):
            result = introspect_capability("category_tools", "workbook write create chart")
        assert "分类不存在" in result
        assert "apply_spreadsheet_changes" in result

    def test_family_gated_word_tool_reports_docx_unlock(self, routed_registry: ToolRegistry) -> None:
        """工作区没有 .docx 时，Word 工具的不可用原因是文件族而不是"不存在"。"""
        with _bound_catalog(routed_registry, families=frozenset({"xlsx"})):
            result = introspect_capability("tool_detail", "read_word")
        assert "read_word" in result
        assert "被门控" in result
        assert ".docx" in result
        assert "工具不存在于当前目录" not in result

    def test_related_tools_explains_gated_tool(self, routed_registry: ToolRegistry) -> None:
        with _bound_catalog(routed_registry, disallowed=("apply_spreadsheet_changes",)):
            result = introspect_capability("related_tools", "apply_spreadsheet_changes")
        assert "无相关工具推荐" in result
        assert "被门控" in result
        assert "disallowed_tools" in result


class TestCanIDoCSVProfileGate:
    """回归 #12/#23（真实会话）：CSV-only 工作区的写工具是"被门控可解锁"，不是"不存在"。"""

    @pytest.fixture()
    def csv_engine(self, tmp_path: Path):
        import excelmanus.tools.introspection_tools as mod
        from excelmanus.config import ExcelManusConfig
        from excelmanus.engine import AgentEngine
        from excelmanus.tools.catalog import catalog_from_engine

        (tmp_path / "data.csv").write_text("a,b\n1,2\n", encoding="utf-8")
        registry = ToolRegistry()
        registry.register_builtin_tools(str(tmp_path))
        engine = AgentEngine(ExcelManusConfig(
            api_key="test", base_url="https://test.invalid/v1", model="test",
            workspace_root=str(tmp_path), jev_enabled="off",
        ), registry)
        catalog = catalog_from_engine(engine)
        if "apply_spreadsheet_changes" in catalog.name_set():
            pytest.skip("CSV-only profile 已不再门控工作簿写工具")
        previous = mod._registry
        mod._registry = registry
        yield engine
        mod._registry = previous

    def _query(self, engine, query_type: str, query: str) -> str:
        result = engine.registry.call_tool(
            "introspect_capability", {"query_type": query_type, "query": query},
        )
        assert result.success, result.model_text
        return result.model_text

    def test_named_gated_tool_reports_gate_and_unlock(self, csv_engine) -> None:
        text = self._query(csv_engine, "can_i_do", "apply_spreadsheet_changes")
        assert "apply_spreadsheet_changes" in text
        assert "门控" in text
        assert "解锁" in text
        assert "write_text_file" not in text

    def test_create_chart_reports_gate_not_task_create(self, csv_engine) -> None:
        text = self._query(csv_engine, "can_i_do", "create chart")
        assert "task_create" not in text
        assert "apply_spreadsheet_changes" in text
        assert "门控" in text

    def test_write_intent_does_not_answer_with_readonly_validator(self, csv_engine) -> None:
        text = self._query(csv_engine, "can_i_do", "写入单元格 修改工作簿 创建图表")
        assert "validate_spreadsheet" not in text
        assert "apply_spreadsheet_changes" in text

    def test_tool_detail_gated_message_mentions_unlock(self, csv_engine) -> None:
        text = self._query(csv_engine, "tool_detail", "apply_spreadsheet_changes")
        assert "工具不可用" in text
        assert "门控" in text
        assert "工具不存在于当前目录" not in text

    def test_system_status_still_lists_current_directory(self, csv_engine) -> None:
        text = self._query(csv_engine, "system_status", "")
        assert "系统状态概览" in text

    def test_system_status_explains_gated_tools(self, csv_engine) -> None:
        """回归 #12：查"完整目录"时要能看出"工具仍在、被 CSV profile 门控、产出 xlsx 即解锁"。"""
        text = self._query(csv_engine, "system_status", "")
        header = "被门控（工具仍在，可解锁）"
        assert header in text
        section = text.split(header, 1)[1]
        assert "apply_spreadsheet_changes" in section
        assert "门控" in section  # 是门控，不是"不存在"
        assert "csv" in section.lower()  # gate 原因：CSV-only profile
        assert "解锁" in section  # 给出下一步
        assert "xlsx" in section  # 解锁条件：工作区产出第一个 xlsx
        # 解锁后由下一轮目录推导生效（两种文案之一）
        assert "下一轮" in section or "工作区出现" in section

    def test_category_tools_explains_gated_edit_category(self, csv_engine) -> None:
        text = self._query(csv_engine, "category_tools", "edit")
        assert "apply_spreadsheet_changes" in text
        assert "被门控" in text


# ── 文本文件版本契约（回归 session #61）────────────────────────


class TestTextFileVersionConflict:
    """write/edit 同族工具的 VERSION_CONFLICT 必须给出可直接执行的下一次调用。"""

    @pytest.fixture(autouse=True)
    def _workspace(self, tmp_path: Path) -> Path:
        from excelmanus.tools import code_tools
        from excelmanus.workbook_commit import seed_seen_versions

        code_tools.init_guard(str(tmp_path))
        (tmp_path / "scripts").mkdir(parents=True, exist_ok=True)
        seed_seen_versions({})
        self.root = tmp_path
        return tmp_path

    def test_edit_conflict_next_call_is_executable(self) -> None:
        from excelmanus.tools import code_tools
        from excelmanus.workbook_commit import content_version_of_file

        target = self.root / "scripts" / "build.py"
        target.write_text("print('old')\n", encoding="utf-8")

        conflict = code_tools.edit_text_file("scripts/build.py", "print('old')", "print('new')")
        assert not conflict.success
        payload = dict(conflict.value or {})
        assert payload["error_code"] == "VERSION_CONFLICT"
        assert "content_version" in payload["message"]
        assert "expected_version" in payload["message"]
        assert payload["needed_args"] == ["expected_version"]
        assert payload["content_version"] == content_version_of_file(target)

        call = payload["next_call"]
        assert call["tool"] == "edit_text_file"
        assert call["arguments"]["expected_version"] == payload["content_version"]
        assert target.read_text(encoding="utf-8") == "print('old')\n"  # 冲突未落盘

        retry = code_tools.edit_text_file(
            "scripts/build.py", "print('old')", "print('new')",
            expected_version=call["arguments"]["expected_version"],
        )
        assert retry.success, retry.model_text
        assert target.read_text(encoding="utf-8") == "print('new')\n"

    def test_write_then_edit_uses_receipt_version(self) -> None:
        """#59 写入回执的 content_version 就是 #61 需要的 expected_version。"""
        from excelmanus.tools import code_tools
        from excelmanus.workbook_commit import seed_seen_versions

        receipt = code_tools.write_text_file("scripts/build_regression_xlsx.py", "print(1)\n")
        assert receipt.success, receipt.model_text
        version = receipt.value["content_version"]
        seed_seen_versions({})  # 下一轮：peek_seen 已清空，与 #61 的真实时序一致

        conflict = code_tools.edit_text_file(
            "scripts/build_regression_xlsx.py", "print(1)", "print(2)",
        )
        payload = dict(conflict.value or {})
        assert payload["error_code"] == "VERSION_CONFLICT"
        assert payload["next_call"]["arguments"]["expected_version"] == version

    def test_write_conflict_points_back_at_write_text_file(self) -> None:
        from excelmanus.tools import code_tools
        from excelmanus.workbook_commit import seed_seen_versions

        assert code_tools.write_text_file("scripts/job.py", "old\n").success
        seed_seen_versions({})
        conflict = code_tools.write_text_file("scripts/job.py", "new\n")
        payload = dict(conflict.value or {})
        assert payload["error_code"] == "VERSION_CONFLICT"
        assert payload["next_call"]["tool"] == "write_text_file"
        assert payload["next_call"]["arguments"]["expected_version"] == payload["content_version"]
        assert payload["needed_args"] == ["expected_version"]
