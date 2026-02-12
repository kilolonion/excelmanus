"""Skill 注册中心测试：Property 8 属性测试 + SkillRegistry 单元测试。

**Validates: Requirements 2.1, 2.2, 2.5, 2.6, 2.7, 2.8**
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings, strategies as st

from excelmanus.skills import (
    SkillRegistry,
    SkillRegistryError,
    ToolDef,
    ToolExecutionError,
    ToolNotFoundError,
)


# ── 辅助函数 ──────────────────────────────────────────────


def _make_tool(name: str, description: str = "测试工具") -> ToolDef:
    """创建一个简单的测试用 ToolDef。"""
    return ToolDef(
        name=name,
        description=description,
        input_schema={
            "type": "object",
            "properties": {"x": {"type": "string"}},
            "required": ["x"],
        },
        func=lambda x="": f"result:{x}",
    )


# ── 策略定义 ──────────────────────────────────────────────

# 生成合法的 Skill 名称（非空 ASCII 字母数字 + 下划线）
skill_name_st = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="_"),
    min_size=1,
    max_size=30,
).filter(lambda s: s.strip() == s and len(s) > 0)

# 生成合法的工具名称
tool_name_st = st.text(
    alphabet=st.characters(whitelist_categories=("Ll",), whitelist_characters="_"),
    min_size=1,
    max_size=30,
)


# ── Property 8：Skill 注册与 Schema 生成 ─────────────────


class TestProperty8SkillRegistration:
    """Property 8：重复 Skill 名称注册必须报错；schema 数量等于工具总数；
    schema 必须含 name/description/input_schema。

    **Validates: Requirements 2.1, 2.2, 2.5**
    """

    @settings(max_examples=100)
    @given(name=skill_name_st)
    def test_duplicate_skill_name_raises(self, name: str) -> None:
        """重复注册同名 Skill 必须抛出 SkillRegistryError。"""
        registry = SkillRegistry()
        tool = _make_tool(f"tool_{name}")
        registry.register(name, "描述", [tool])
        with pytest.raises(SkillRegistryError):
            registry.register(name, "另一个描述", [_make_tool(f"tool2_{name}")])

    @settings(max_examples=100)
    @given(
        tool_count=st.integers(min_value=1, max_value=10),
    )
    def test_schema_count_equals_tool_count(self, tool_count: int) -> None:
        """get_openai_schemas() 返回的 schema 数量必须等于注册的工具总数。"""
        registry = SkillRegistry()
        tools = [_make_tool(f"tool_{i}") for i in range(tool_count)]
        registry.register("test_skill", "测试", tools)
        schemas = registry.get_openai_schemas()
        assert len(schemas) == tool_count

    @settings(max_examples=100)
    @given(
        tool_count=st.integers(min_value=1, max_value=8),
    )
    def test_schema_contains_required_fields(self, tool_count: int) -> None:
        """每个 OpenAI schema 必须包含 name、description 和 parameters（对应 input_schema）。"""
        registry = SkillRegistry()
        tools = [_make_tool(f"tool_{i}", f"描述_{i}") for i in range(tool_count)]
        registry.register("test_skill", "测试", tools)

        for schema in registry.get_openai_schemas():
            assert schema["type"] == "function"
            assert "name" in schema
            assert "description" in schema
            assert "parameters" in schema
            assert isinstance(schema["name"], str)
            assert len(schema["name"]) > 0
            assert isinstance(schema["description"], str)
            assert isinstance(schema["parameters"], dict)


# ── MVP 工具清单单元测试 ──────────────────────────────────


class TestMVPToolInventory:
    """验证 MVP 三个 Skill 的工具总数和名称。

    **Validates: Requirements 2.6, 2.7, 2.8**
    """

    def test_total_mvp_tool_count_is_8(self) -> None:
        """MVP 工具总数应为 8（data:5 + chart:1 + format:2）。"""
        from excelmanus.skills.data_skill import get_tools as data_tools
        from excelmanus.skills.chart_skill import get_tools as chart_tools
        from excelmanus.skills.format_skill import get_tools as format_tools

        all_tools = data_tools() + chart_tools() + format_tools()
        assert len(all_tools) == 8

    def test_mvp_tool_names(self) -> None:
        """MVP 工具名称应完整覆盖需求定义的 8 个工具。"""
        from excelmanus.skills.data_skill import get_tools as data_tools
        from excelmanus.skills.chart_skill import get_tools as chart_tools
        from excelmanus.skills.format_skill import get_tools as format_tools

        names = {t.name for t in data_tools() + chart_tools() + format_tools()}
        expected = {
            "read_excel", "write_excel", "analyze_data", "filter_data",
            "transform_data", "create_chart", "format_cells", "adjust_column_width",
        }
        assert names == expected

    def test_auto_discover_loads_all_skills(self) -> None:
        """auto_discover 应自动加载 data/chart/format 三个 Skill。"""
        registry = SkillRegistry()
        registry.auto_discover("excelmanus.skills")
        tools = registry.get_all_tools()
        names = {t.name for t in tools}
        assert len(tools) == 8
        assert "read_excel" in names
        assert "create_chart" in names
        assert "format_cells" in names

    def test_registry_schemas_match_tool_count(self) -> None:
        """auto_discover 后 schema 数量应等于 8。"""
        registry = SkillRegistry()
        registry.auto_discover("excelmanus.skills")
        schemas = registry.get_openai_schemas()
        assert len(schemas) == 8


# ── SkillRegistry 单元测试 ────────────────────────────────


class TestSkillRegistryUnit:
    """SkillRegistry 核心方法单元测试。

    **Validates: Requirements 2.1, 2.2, 2.5**
    """

    def test_register_and_get_tools(self) -> None:
        """注册后 get_all_tools 应返回所有工具。"""
        registry = SkillRegistry()
        tools = [_make_tool("a"), _make_tool("b")]
        registry.register("skill1", "描述", tools)
        assert len(registry.get_all_tools()) == 2

    def test_call_tool_success(self) -> None:
        """call_tool 应正确调用工具函数并返回结果。"""
        registry = SkillRegistry()
        registry.register("s", "d", [_make_tool("echo")])
        result = registry.call_tool("echo", {"x": "hello"})
        assert result == "result:hello"

    def test_call_tool_not_found(self) -> None:
        """调用不存在的工具应抛出 ToolNotFoundError。"""
        registry = SkillRegistry()
        with pytest.raises(ToolNotFoundError):
            registry.call_tool("nonexistent", {})

    def test_call_tool_execution_error(self) -> None:
        """工具执行异常应包装为 ToolExecutionError。"""
        def bad_func(**kwargs):
            raise ValueError("boom")

        tool = ToolDef(
            name="bad",
            description="会失败的工具",
            input_schema={"type": "object", "properties": {}},
            func=bad_func,
        )
        registry = SkillRegistry()
        registry.register("s", "d", [tool])
        with pytest.raises(ToolExecutionError, match="boom"):
            registry.call_tool("bad", {})

    def test_multiple_skills_register(self) -> None:
        """多个不同名 Skill 应能正常注册。"""
        registry = SkillRegistry()
        registry.register("s1", "d1", [_make_tool("t1")])
        registry.register("s2", "d2", [_make_tool("t2")])
        assert len(registry.get_all_tools()) == 2

    def test_duplicate_tool_name_in_same_skill_raises(self) -> None:
        """同一 Skill 内重复工具名应报错。"""
        registry = SkillRegistry()
        tools = [_make_tool("dup"), _make_tool("dup")]
        with pytest.raises(SkillRegistryError, match="重复工具名"):
            registry.register("skill_dup", "d", tools)

    def test_duplicate_tool_name_across_skills_raises(self) -> None:
        """跨 Skill 工具名冲突应报错。"""
        registry = SkillRegistry()
        registry.register("s1", "d1", [_make_tool("dup")])
        with pytest.raises(SkillRegistryError, match="工具名冲突"):
            registry.register("s2", "d2", [_make_tool("dup")])

    def test_empty_registry(self) -> None:
        """空注册中心应返回空列表。"""
        registry = SkillRegistry()
        assert registry.get_all_tools() == []
        assert registry.get_openai_schemas() == []

    def test_get_openai_schemas_chat_completions_mode(self) -> None:
        """可按需生成 Chat Completions 兼容的 schema。"""
        registry = SkillRegistry()
        registry.register("s", "d", [_make_tool("echo")])
        schemas = registry.get_openai_schemas(mode="chat_completions")
        assert schemas[0]["type"] == "function"
        assert schemas[0]["function"]["name"] == "echo"


# ── ToolDef 转换测试 ──────────────────────────────────────


class TestToolDefConversion:
    """ToolDef 的 to_openai_schema 转换测试。"""

    def test_to_openai_schema_structure(self) -> None:
        """to_openai_schema 默认应返回 Responses API 兼容结构。"""
        tool = _make_tool("test_tool", "测试描述")
        schema = tool.to_openai_schema()
        assert schema["type"] == "function"
        assert schema["name"] == "test_tool"
        assert schema["description"] == "测试描述"
        assert schema["parameters"] == tool.input_schema

    def test_to_openai_schema_chat_completions_structure(self) -> None:
        """to_openai_schema(chat_completions) 应返回 Chat Completions 兼容结构。"""
        tool = _make_tool("test_tool", "测试描述")
        schema = tool.to_openai_schema(mode="chat_completions")
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "test_tool"
        assert schema["function"]["description"] == "测试描述"
        assert schema["function"]["parameters"] == tool.input_schema

