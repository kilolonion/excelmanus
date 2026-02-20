"""ToolDispatcher 组件单元测试。"""

from __future__ import annotations

import json
import pytest
from unittest.mock import MagicMock, AsyncMock

from excelmanus.engine_core.tool_dispatcher import ToolDispatcher


def _make_registry(tool_result: str = "ok") -> MagicMock:
    """构造一个最小化的 ToolRegistry mock。"""
    registry = MagicMock()
    registry.call_tool = MagicMock(return_value=tool_result)
    registry.is_error_result = MagicMock(return_value=False)
    tool_def = MagicMock()
    tool_def.truncate_result = MagicMock(side_effect=lambda x: x)
    registry.get_tool = MagicMock(return_value=tool_def)
    return registry


class TestParseArguments:
    """工具参数解析。"""

    def test_parse_none_args(self):
        d = ToolDispatcher(registry=_make_registry())
        args, err = d.parse_arguments(None)
        assert args == {}
        assert err is None

    def test_parse_empty_string_args(self):
        d = ToolDispatcher(registry=_make_registry())
        args, err = d.parse_arguments("")
        assert args == {}
        assert err is None

    def test_parse_dict_args(self):
        d = ToolDispatcher(registry=_make_registry())
        args, err = d.parse_arguments({"key": "value"})
        assert args == {"key": "value"}
        assert err is None

    def test_parse_json_string_args(self):
        d = ToolDispatcher(registry=_make_registry())
        args, err = d.parse_arguments('{"cell": "A1", "value": 42}')
        assert args == {"cell": "A1", "value": 42}
        assert err is None

    def test_parse_invalid_json(self):
        d = ToolDispatcher(registry=_make_registry())
        args, err = d.parse_arguments("{bad json")
        assert args == {}
        assert err is not None
        assert "JSON" in err

    def test_parse_non_dict_json(self):
        d = ToolDispatcher(registry=_make_registry())
        args, err = d.parse_arguments("[1, 2, 3]")
        assert args == {}
        assert err is not None
        assert "对象" in err or "dict" in err.lower() or "类型" in err

    def test_parse_invalid_type(self):
        d = ToolDispatcher(registry=_make_registry())
        args, err = d.parse_arguments(12345)
        assert args == {}
        assert err is not None


class TestCallRegistryTool:
    """普通工具调用。"""

    async def test_call_simple_tool(self):
        registry = _make_registry(tool_result="cell A1 = hello")
        d = ToolDispatcher(registry=registry)
        result = await d.call_registry_tool(
            tool_name="read_cell",
            arguments={"cell": "A1"},
            tool_scope=None,
        )
        assert result == "cell A1 = hello"
        registry.call_tool.assert_called_once()

    async def test_call_tool_with_scope(self):
        registry = _make_registry(tool_result="ok")
        d = ToolDispatcher(registry=registry)
        result = await d.call_registry_tool(
            tool_name="write_cell",
            arguments={"cell": "A1", "value": "test"},
            tool_scope=["write_cell", "read_cell"],
        )
        assert result == "ok"

    async def test_result_truncation(self):
        registry = _make_registry(tool_result="very long result")
        tool_def = MagicMock()
        tool_def.truncate_result = MagicMock(return_value="truncated")
        registry.get_tool = MagicMock(return_value=tool_def)
        d = ToolDispatcher(registry=registry)
        result = await d.call_registry_tool(
            tool_name="read_cell",
            arguments={},
            tool_scope=None,
        )
        assert result == "truncated"
