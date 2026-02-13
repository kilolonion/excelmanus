"""ToolRegistry 单元测试。"""

from __future__ import annotations

import pytest

from excelmanus.tools import (
    ToolDef,
    ToolNotAllowedError,
    ToolRegistry,
    ToolRegistryError,
)


def _tool(name: str) -> ToolDef:
    return ToolDef(
        name=name,
        description=f"工具 {name}",
        input_schema={
            "type": "object",
            "properties": {"x": {"type": "integer"}},
            "required": ["x"],
        },
        func=lambda x=0: x + 1,
    )


class TestToolRegistry:
    def test_register_conflict_raises(self) -> None:
        registry = ToolRegistry()
        registry.register_tool(_tool("dup"))
        with pytest.raises(ToolRegistryError):
            registry.register_tool(_tool("dup"))

    def test_get_schema_with_tool_scope(self) -> None:
        registry = ToolRegistry()
        registry.register_tools([_tool("a"), _tool("b")])
        schemas = registry.get_openai_schemas(
            mode="chat_completions",
            tool_scope=["b"],
        )
        assert len(schemas) == 1
        assert schemas[0]["function"]["name"] == "b"

    def test_call_tool_not_allowed(self) -> None:
        registry = ToolRegistry()
        registry.register_tool(_tool("safe"))
        with pytest.raises(ToolNotAllowedError):
            registry.call_tool("safe", {"x": 1}, tool_scope=["other"])

    def test_get_tool_found(self) -> None:
        registry = ToolRegistry()
        registry.register_tool(_tool("alpha"))
        tool = registry.get_tool("alpha")
        assert tool is not None
        assert tool.name == "alpha"

    def test_get_tool_not_found(self) -> None:
        registry = ToolRegistry()
        assert registry.get_tool("nonexistent") is None


class TestToolDefTruncate:
    """ToolDef.truncate_result 截断逻辑测试。"""

    def _make_tool(self, max_chars: int = 3000) -> ToolDef:
        return ToolDef(
            name="t",
            description="test",
            input_schema={"type": "object", "properties": {}},
            func=lambda: None,
            max_result_chars=max_chars,
        )

    def test_short_text_unchanged(self) -> None:
        tool = self._make_tool(100)
        text = "a" * 100
        assert tool.truncate_result(text) == text

    def test_long_text_truncated(self) -> None:
        tool = self._make_tool(50)
        text = "x" * 200
        result = tool.truncate_result(text)
        assert result.startswith("x" * 50)
        assert "[结果已截断，原始长度: 200 字符]" in result
        assert len(result) < len(text)

    def test_zero_limit_no_truncation(self) -> None:
        tool = self._make_tool(0)
        text = "a" * 5000
        assert tool.truncate_result(text) == text

    def test_negative_limit_no_truncation(self) -> None:
        tool = self._make_tool(-1)
        text = "a" * 5000
        assert tool.truncate_result(text) == text

    def test_default_limit(self) -> None:
        tool = ToolDef(
            name="t",
            description="test",
            input_schema={},
            func=lambda: None,
        )
        assert tool.max_result_chars == 3000
