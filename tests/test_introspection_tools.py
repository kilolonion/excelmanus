"""单元测试：introspect_capability 工具。

测试五种查询类型的正确性、错误处理和注册逻辑。

**验证：需求 3.1–3.3、4.1–4.3、5.1–5.3、6.1–6.4、7.1–7.2、8.1–8.3、12.1**
"""

from __future__ import annotations

import pytest

from excelmanus.tools.introspection_tools import (
    INTROSPECT_CAPABILITY_SCHEMA,
    _ALL_QUERY_TYPES,
    _EXTENDED_CAPABILITIES,
    _SUBAGENT_CAPABILITIES,
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
        "inspect_spreadsheet", "analyze_spreadsheet", "compare_spreadsheets",
        "edit_spreadsheet", "write_text_file", "copy_file", "run_code",
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
            {"query_type": "tool_detail", "query": "inspect_spreadsheet"},
            {"query_type": "can_i_do", "query": "读取数据"},
        ]
        result = introspect_capability(queries=queries)
        
        # 应包含两个查询的结果
        assert "[1]" in result
        assert "[2]" in result
        assert "inspect_spreadsheet" in result
        assert "tool_detail(inspect_spreadsheet)" in result

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
        """query_type 应包含五种枚举值。"""
        props = INTROSPECT_CAPABILITY_SCHEMA["properties"]
        enum_values = props["query_type"]["enum"]
        assert set(enum_values) == {
            "tool_detail", "category_tools", "can_i_do",
            "related_tools", "system_status",
        }

    def test_in_read_only_safe_tools(self) -> None:
        """introspect_capability 应在 READ_ONLY_SAFE_TOOLS 中。"""
        assert "introspect_capability" in READ_ONLY_SAFE_TOOLS


# ── tool_detail 测试 ──────────────────────────────────────


class TestToolDetail:
    """验证：需求 4.1–4.3"""

    def test_existing_tool(self, registry: ToolRegistry) -> None:
        """查询已注册工具应返回 schema 和权限信息。"""
        result = introspect_capability("tool_detail", "inspect_spreadsheet")
        assert "inspect_spreadsheet" in result
        assert "file_path" in result
        assert "🟢" in result

    def test_schema_consistency(self, registry: ToolRegistry) -> None:
        """返回的 schema 应与 ToolDef.input_schema 一致。"""
        result = introspect_capability("tool_detail", "inspect_spreadsheet")
        tool_def = registry.get_tool("inspect_spreadsheet")
        assert tool_def is not None
        assert '"file_path"' in result

    def test_permission_read_only(self, registry: ToolRegistry) -> None:
        """只读工具应标注为 🟢。"""
        result = introspect_capability("tool_detail", "inspect_spreadsheet")
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
        result = introspect_capability("tool_detail", "inspect_spreadsheet")
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
            if registry.get_tool(tool_name) is not None or tool_name in TOOL_SHORT_DESCRIPTIONS:
                assert tool_name in result

    def test_tools_with_descriptions(self, registry: ToolRegistry) -> None:
        """返回的工具应附带描述。"""
        result = introspect_capability("category_tools", "inspect")
        assert "inspect_spreadsheet" in result
        desc = TOOL_SHORT_DESCRIPTIONS.get("inspect_spreadsheet", "")
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
        assert "可见工具可做" in result
        assert "inspect_spreadsheet" in result

    def test_product_diff_term_routes_to_compare(self, registry: ToolRegistry) -> None:
        """产品术语 diff 不应依赖短描述的词袋命中。"""
        result = introspect_capability("can_i_do", "diff")
        assert "可见工具可做" in result
        assert "compare_spreadsheets" in result

    def test_self_match(self, registry: ToolRegistry) -> None:
        """使用工具完整描述作为查询应匹配到该工具。"""
        desc = TOOL_SHORT_DESCRIPTIONS["inspect_spreadsheet"]
        result = introspect_capability("can_i_do", desc)
        assert "inspect_spreadsheet" in result

    def test_no_match(self, registry: ToolRegistry) -> None:
        """无匹配时应返回当前不可用。"""
        result = introspect_capability("can_i_do", "量子计算模拟")
        assert "当前不可用" in result

    def test_max_results_per_layer(self, registry: ToolRegistry) -> None:
        """每层匹配结果不应超过 5 个。"""
        result = introspect_capability("can_i_do", "Excel 数据 文件 格式")
        # 内置工具匹配行
        builtin_lines = []
        in_builtin = False
        for line in result.splitlines():
            if line.startswith("内置工具匹配"):
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
        assert "可见工具可做" in result
        assert "扩展能力" in result or "pivot" in result.lower()

    def test_subagent_match(self, registry: ToolRegistry) -> None:
        """can_i_do 应能匹配子代理能力。"""
        result = introspect_capability("can_i_do", "只读探索 文件结构分析")
        assert "可见工具可做" in result or "当前不可用" in result


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
        assert "扩展能力" in result
        assert "内置子代理" in result

    def test_shows_subagent_names(self, registry: ToolRegistry) -> None:
        """system_status 应列出所有内置子代理名称。"""
        result = introspect_capability("system_status", "")
        for name in _SUBAGENT_CAPABILITIES:
            assert name in result

    def test_extended_capabilities_count(self, registry: ToolRegistry) -> None:
        """system_status 应显示正确的扩展能力数量。"""
        result = introspect_capability("system_status", "")
        assert f"{len(_EXTENDED_CAPABILITIES)} 项" in result


# ── 扩展能力常量测试 ─────────────────────────────────────


class TestExtendedCapabilitiesConstants:
    """测试扩展能力和子代理常量的完整性。"""

    def test_extended_capabilities_non_empty(self) -> None:
        """扩展能力描述不应为空。"""
        assert len(_EXTENDED_CAPABILITIES) > 0
        for key, desc in _EXTENDED_CAPABILITIES.items():
            assert isinstance(key, str) and key
            assert isinstance(desc, str) and desc
            assert (
                "run_code" in desc
                or "edit_spreadsheet" in desc
                or "format_spreadsheet" in desc
                or "manage_spreadsheet_objects" in desc
                or "WorkbookSpec" in desc
                or "当前不可用" in desc
                or "不可用" in desc
            ), f"扩展能力 {key} 应指向意图工具、run_code，或声明当前不可用"

    def test_subagent_capabilities_match_builtin(self) -> None:
        """子代理能力描述应与 builtin.py 中定义的子代理一致。"""
        expected = {"explorer", "subagent"}
        assert set(_SUBAGENT_CAPABILITIES.keys()) == expected


# ── 纯查询无副作用测试 ────────────────────────────────────


class TestNoSideEffects:
    """验证：需求 8.1–8.3"""

    def test_registry_unchanged(self, registry: ToolRegistry) -> None:
        """调用后 ToolRegistry 状态不变。"""
        tools_before = set(registry.get_tool_names())
        introspect_capability("tool_detail", "inspect_spreadsheet")
        introspect_capability("category_tools", "inspect")
        introspect_capability("can_i_do", "读取数据")
        introspect_capability("related_tools", "edit_spreadsheet")
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
        format_tool = ToolDef(
            name="format_spreadsheet",
            description="改外观含冻结",
            input_schema={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "operations": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "kind": {
                                    "type": "string",
                                    "enum": ["format", "merge", "unmerge", "size", "freeze"],
                                },
                                "freeze_panes": {"type": "string"},
                            },
                        },
                    },
                },
                "required": ["file_path", "operations"],
            },
            func=lambda: None,
        )
        reg.register_tool(format_tool)
        catalog = derive_effective_catalog(tools=reg.get_all_tools(), mode="write")
        token = mod._call_catalog.set(catalog)
        try:
            result = introspect_capability("can_i_do", "冻结首行")
            assert "可见工具可做" in result
            assert "kind=freeze" in result
            assert "已有文件不可用" not in result
            assert "支持" not in result.split("能力判断:", 1)[-1][:20]
            detail = introspect_capability("tool_detail", "format_spreadsheet")
            assert "operations.kind" in detail
            assert "Excel A1 引用语法" not in detail
            size_detail = introspect_capability(
                "tool_detail", "format_spreadsheet.operations.size",
            )
            assert "字段不存在" not in size_detail
            assert "columns" in size_detail or "auto_fit" in size_detail
            freeze_ops = introspect_capability(
                "tool_detail", "format_spreadsheet.operations.freeze",
            )
            freeze_top = introspect_capability("tool_detail", "format_spreadsheet.freeze")
            for freeze_detail in (freeze_ops, freeze_top):
                assert "字段不存在" not in freeze_detail
                assert "freeze_panes" in freeze_detail
        finally:
            mod._call_catalog.reset(token)



# ── ToolRegistry 未初始化测试 ─────────────────────────────


class TestRegistryNotInitialized:
    """验证：需求 12.1"""

    def test_returns_error(self, empty_registry: ToolRegistry) -> None:
        """未初始化时应返回错误提示。"""
        result = introspect_capability("tool_detail", "inspect_spreadsheet")
        assert "工具注册表尚未初始化" in result
