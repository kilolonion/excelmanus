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
