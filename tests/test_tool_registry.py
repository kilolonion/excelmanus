"""ToolRegistry 单元测试。"""

from __future__ import annotations

import json

import pytest

from excelmanus.tools import registry as registry_module
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

    def test_call_tool_missing_required_argument_returns_structured_error(self) -> None:
        registry = ToolRegistry()

        def _need_file_path(file_path: str) -> str:
            return file_path

        registry.register_tool(
            ToolDef(
                name="need_file_path",
                description="需要 file_path 参数",
                input_schema={
                    "type": "object",
                    "properties": {"file_path": {"type": "string"}},
                    "required": ["file_path"],
                    "additionalProperties": False,
                },
                func=_need_file_path,
            )
        )

        raw = registry.call_tool("need_file_path", {})
        payload = json.loads(raw)
        assert payload["status"] == "error"
        assert payload["error_code"] == "TOOL_ARGUMENT_VALIDATION_ERROR"
        assert payload["tool"] == "need_file_path"
        assert "file_path" in payload["required_fields"]

    def test_register_builtin_tools_includes_macro_and_image_tools(self, tmp_path) -> None:
        """内置注册应覆盖 macro/image 工具，避免模块清单漂移。"""
        registry = ToolRegistry()
        registry.register_builtin_tools(str(tmp_path))

        tool_names = set(registry.get_tool_names())
        assert "read_image" in tool_names
        assert "vlookup_write" in tool_names
        assert "computed_column" in tool_names

    def test_builtin_module_manifest_is_single_source(self) -> None:
        """模块清单应包含关键模块且不重复，作为注册唯一事实源。"""
        module_paths = registry_module._BUILTIN_TOOL_MODULE_PATHS
        assert module_paths == tuple(dict.fromkeys(module_paths))
        assert "excelmanus.tools.macro_tools" in module_paths
        assert "excelmanus.tools.image_tools" in module_paths
        assert "excelmanus.tools.memory_tools" in module_paths


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
