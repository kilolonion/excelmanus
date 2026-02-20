"""单元测试：introspect_capability 工具。

测试四种查询类型的正确性、错误处理和注册逻辑。

**Validates: Requirements 3.1–3.3, 4.1–4.3, 5.1–5.3, 6.1–6.4, 7.1–7.2, 8.1–8.3, 12.1**
"""

from __future__ import annotations

import pytest

from excelmanus.tools.introspection_tools import (
    INTROSPECT_CAPABILITY_SCHEMA,
    TOOL_COMBINATIONS,
    _handle_can_i_do,
    _handle_category_tools,
    _handle_related_tools,
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
        "read_excel", "write_excel", "format_cells", "analyze_data",
        "filter_data", "create_excel_chart", "list_sheets",
        "add_conditional_rule", "read_cell_styles", "merge_cells",
        "adjust_column_width", "transform_data",
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
    yield reg
    # 清理模块级 _registry
    mod._registry = None


@pytest.fixture()
def empty_registry() -> ToolRegistry:
    """空的 ToolRegistry，不注册 introspection 工具。"""
    import excelmanus.tools.introspection_tools as mod

    old = mod._registry
    mod._registry = None
    yield ToolRegistry()
    mod._registry = old


# ── 注册测试 ──────────────────────────────────────────────


class TestRegistration:
    """Validates: Requirements 3.1–3.3"""

    def test_tool_registered(self, registry: ToolRegistry) -> None:
        """introspect_capability 应被注册到 ToolRegistry。"""
        tool = registry.get_tool("introspect_capability")
        assert tool is not None
        assert tool.name == "introspect_capability"

    def test_schema_has_required_fields(self, registry: ToolRegistry) -> None:
        """Schema 应包含 query_type 和 query 两个必填参数。"""
        tool = registry.get_tool("introspect_capability")
        assert tool is not None
        schema = tool.input_schema
        assert "query_type" in schema["properties"]
        assert "query" in schema["properties"]
        assert set(schema["required"]) == {"query_type", "query"}

    def test_query_type_enum(self) -> None:
        """query_type 应包含四种枚举值。"""
        enum_values = INTROSPECT_CAPABILITY_SCHEMA["properties"]["query_type"]["enum"]
        assert set(enum_values) == {"tool_detail", "category_tools", "can_i_do", "related_tools"}

    def test_in_read_only_safe_tools(self) -> None:
        """introspect_capability 应在 READ_ONLY_SAFE_TOOLS 中。"""
        assert "introspect_capability" in READ_ONLY_SAFE_TOOLS


# ── tool_detail 测试 ──────────────────────────────────────


class TestToolDetail:
    """Validates: Requirements 4.1–4.3"""

    def test_existing_tool(self, registry: ToolRegistry) -> None:
        """查询已注册工具应返回 schema 和权限信息。"""
        result = introspect_capability("tool_detail", "read_excel")
        assert "read_excel" in result
        assert "file_path" in result  # schema 中的参数
        assert "🟢" in result  # read_excel 是只读安全

    def test_schema_consistency(self, registry: ToolRegistry) -> None:
        """返回的 schema 应与 ToolDef.input_schema 一致。"""
        result = introspect_capability("tool_detail", "read_excel")
        tool_def = registry.get_tool("read_excel")
        assert tool_def is not None
        # 验证 schema 内容出现在结果中
        assert '"file_path"' in result

    def test_permission_read_only(self, registry: ToolRegistry) -> None:
        """只读工具应标注为 🟢。"""
        result = introspect_capability("tool_detail", "read_excel")
        assert "🟢" in result

    def test_permission_confirm(self, registry: ToolRegistry) -> None:
        """Tier A 工具应标注为 🔴。"""
        result = introspect_capability("tool_detail", "write_excel")
        assert "🔴" in result

    def test_permission_audit(self, registry: ToolRegistry) -> None:
        """Tier B 工具应标注为 🟡。"""
        result = introspect_capability("tool_detail", "add_conditional_rule")
        assert "🟡" in result

    def test_category_shown(self, registry: ToolRegistry) -> None:
        """应显示工具所属分类。"""
        result = introspect_capability("tool_detail", "read_excel")
        assert "data_read" in result

    def test_nonexistent_tool(self, registry: ToolRegistry) -> None:
        """查询不存在的工具应返回提示信息。"""
        result = introspect_capability("tool_detail", "nonexistent_tool_xyz")
        assert "工具不存在" in result
        assert "category_tools" in result


# ── category_tools 测试 ───────────────────────────────────


class TestCategoryTools:
    """Validates: Requirements 5.1–5.3"""

    def test_valid_category(self, registry: ToolRegistry) -> None:
        """查询有效分类应返回该分类下所有工具。"""
        result = introspect_capability("category_tools", "data_read")
        for tool_name in TOOL_CATEGORIES["data_read"]:
            if registry.get_tool(tool_name) is not None or tool_name in TOOL_SHORT_DESCRIPTIONS:
                assert tool_name in result

    def test_tools_with_descriptions(self, registry: ToolRegistry) -> None:
        """返回的工具应附带描述。"""
        result = introspect_capability("category_tools", "data_read")
        assert "read_excel" in result
        # 描述应出现
        desc = TOOL_SHORT_DESCRIPTIONS.get("read_excel", "")
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
    """Validates: Requirements 6.1–6.4"""

    def test_matching_query(self, registry: ToolRegistry) -> None:
        """使用工具描述关键词应匹配到对应工具。"""
        result = introspect_capability("can_i_do", "读取 Excel 数据")
        assert "支持" in result
        assert "read_excel" in result

    def test_self_match(self, registry: ToolRegistry) -> None:
        """使用工具完整描述作为查询应匹配到该工具。"""
        desc = TOOL_SHORT_DESCRIPTIONS["read_excel"]
        result = introspect_capability("can_i_do", desc)
        assert "read_excel" in result

    def test_no_match(self, registry: ToolRegistry) -> None:
        """无匹配时应返回"无直接工具支持"。"""
        result = introspect_capability("can_i_do", "量子计算模拟")
        assert "无直接工具支持" in result
        assert "introspector" in result

    def test_max_results(self, registry: ToolRegistry) -> None:
        """匹配结果不应超过 5 个。"""
        # 使用一个广泛的查询词
        result = introspect_capability("can_i_do", "Excel 数据 文件 格式")
        # 计算匹配工具数（以 "  - " 开头的行）
        tool_lines = [l for l in result.splitlines() if l.startswith("  - ")]
        assert len(tool_lines) <= 5


# ── related_tools 测试 ────────────────────────────────────


class TestRelatedTools:
    """Validates: Requirements 7.1–7.2"""

    def test_same_category(self, registry: ToolRegistry) -> None:
        """应返回同分类的其他工具。"""
        result = introspect_capability("related_tools", "read_excel")
        # read_excel 在 data_read 分类，应包含 analyze_data
        assert "analyze_data" in result

    def test_predefined_combinations(self, registry: ToolRegistry) -> None:
        """应返回预定义组合工具。"""
        result = introspect_capability("related_tools", "write_excel")
        assert "预定义组合" in result
        for combo_tool in TOOL_COMBINATIONS["write_excel"]:
            assert combo_tool in result

    def test_no_related(self, registry: ToolRegistry) -> None:
        """不在分类和组合中的工具应返回无推荐。"""
        result = introspect_capability("related_tools", "unknown_tool_xyz")
        assert "无相关工具推荐" in result


# ── 纯查询无副作用测试 ────────────────────────────────────


class TestNoSideEffects:
    """Validates: Requirements 8.1–8.3"""

    def test_registry_unchanged(self, registry: ToolRegistry) -> None:
        """调用后 ToolRegistry 状态不变。"""
        tools_before = set(registry.get_tool_names())
        introspect_capability("tool_detail", "read_excel")
        introspect_capability("category_tools", "data_read")
        introspect_capability("can_i_do", "读取数据")
        introspect_capability("related_tools", "write_excel")
        tools_after = set(registry.get_tool_names())
        assert tools_before == tools_after

    def test_always_returns_nonempty(self, registry: ToolRegistry) -> None:
        """任何有效查询都应返回非空字符串。"""
        for qt in ("tool_detail", "category_tools", "can_i_do", "related_tools"):
            result = introspect_capability(qt, "test_query")
            assert isinstance(result, str)
            assert len(result) > 0


# ── ToolRegistry 未初始化测试 ─────────────────────────────


class TestRegistryNotInitialized:
    """Validates: Requirement 12.1"""

    def test_returns_error(self, empty_registry: ToolRegistry) -> None:
        """未初始化时应返回错误提示。"""
        result = introspect_capability("tool_detail", "read_excel")
        assert "工具注册表尚未初始化" in result
