"""工具注册中心测试：ToolRegistry 属性测试 + 废弃模块清理测试。

**Validates: Requirements 2.1, 2.2, 2.5, 4.1, 4.2, 4.3**
"""

from __future__ import annotations

import pytest
from hypothesis import given, strategies as st

from excelmanus.tools import ToolRegistry
from excelmanus.tools.registry import (
    ToolDef,
    ToolExecutionError,
    ToolNotFoundError,
    ToolRegistryError,
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

# 生成合法的工具名称
tool_name_st = st.text(
    alphabet=st.characters(whitelist_categories=("Ll",), whitelist_characters="_"),
    min_size=1,
    max_size=30,
)


# ── Property 8：工具注册与 Schema 生成 ─────────────────


class TestProperty8ToolRegistration:
    """Property 8：重复工具名注册必须报错；schema 数量等于工具总数；
    schema 必须含 name/description/parameters。

    **Validates: Requirements 2.1, 2.2, 2.5**
    """

    @given(
        tool_count=st.integers(min_value=1, max_value=10),
    )
    def test_schema_count_equals_tool_count(self, tool_count: int) -> None:
        """get_openai_schemas() 返回的 schema 数量必须等于注册的工具总数。"""
        registry = ToolRegistry()
        tools = [_make_tool(f"tool_{i}") for i in range(tool_count)]
        registry.register_tools(tools)
        schemas = registry.get_openai_schemas()
        assert len(schemas) == tool_count

    @given(
        tool_count=st.integers(min_value=1, max_value=8),
    )
    def test_schema_contains_required_fields(self, tool_count: int) -> None:
        """每个 OpenAI schema 必须包含 name、description 和 parameters。"""
        registry = ToolRegistry()
        tools = [_make_tool(f"tool_{i}", f"描述_{i}") for i in range(tool_count)]
        registry.register_tools(tools)

        for schema in registry.get_openai_schemas():
            assert schema["type"] == "function"
            assert "name" in schema
            assert "description" in schema
            assert "parameters" in schema
            assert isinstance(schema["name"], str)
            assert len(schema["name"]) > 0
            assert isinstance(schema["description"], str)
            assert isinstance(schema["parameters"], dict)


# ── 废弃模块清理测试 ──────────────────────────────────────


class TestDeprecatedSkillsModuleRemoved:
    """验证废弃的 excelmanus.skills 模块已被彻底移除。"""

    def test_skills_module_not_found(self) -> None:
        import importlib

        with pytest.raises(ModuleNotFoundError):
            importlib.import_module("excelmanus.skills")


# ── ToolRegistry 单元测试 ────────────────────────────────


class TestToolRegistryUnit:
    """ToolRegistry 核心方法单元测试。

    **Validates: Requirements 2.1, 2.2, 2.5**
    """

    def test_register_and_get_tools(self) -> None:
        """注册后 get_all_tools 应返回所有工具。"""
        registry = ToolRegistry()
        tools = [_make_tool("a"), _make_tool("b")]
        registry.register_tools(tools)
        assert len(registry.get_all_tools()) == 2

    def test_call_tool_success(self) -> None:
        """call_tool 应正确调用工具函数并返回结果。"""
        registry = ToolRegistry()
        registry.register_tools([_make_tool("echo")])
        result = registry.call_tool("echo", {"x": "hello"})
        assert result == "result:hello"

    def test_call_tool_not_found(self) -> None:
        """调用不存在的工具应抛出 ToolNotFoundError。"""
        registry = ToolRegistry()
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
        registry = ToolRegistry()
        registry.register_tools([tool])
        with pytest.raises(ToolExecutionError, match="boom"):
            registry.call_tool("bad", {})

    def test_duplicate_tool_name_raises(self) -> None:
        """重复工具名应报错。"""
        registry = ToolRegistry()
        tools = [_make_tool("dup"), _make_tool("dup")]
        with pytest.raises(ToolRegistryError, match="重复"):
            registry.register_tools(tools)

    def test_duplicate_tool_name_across_batches_raises(self) -> None:
        """跨批次工具名冲突应报错。"""
        registry = ToolRegistry()
        registry.register_tools([_make_tool("dup")])
        with pytest.raises(ToolRegistryError, match="冲突"):
            registry.register_tools([_make_tool("dup")])

    def test_empty_registry(self) -> None:
        """空注册中心应返回空列表。"""
        registry = ToolRegistry()
        assert registry.get_all_tools() == []
        assert registry.get_openai_schemas() == []

    def test_get_openai_schemas_chat_completions_mode(self) -> None:
        """可按需生成 Chat Completions 兼容的 schema。"""
        registry = ToolRegistry()
        registry.register_tools([_make_tool("echo")])
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
