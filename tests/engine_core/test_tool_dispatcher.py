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


def _make_engine(tool_result: str = "ok") -> MagicMock:
    """构造一个最小化的 engine mock 供 ToolDispatcher 使用。"""
    engine = MagicMock()
    engine._registry = _make_registry(tool_result)
    engine._persistent_memory = None
    return engine


class TestParseArguments:
    """工具参数解析。"""

    def test_parse_none_args(self):
        d = ToolDispatcher(_make_engine())
        args, err = d.parse_arguments(None)
        assert args == {}
        assert err is None

    def test_parse_empty_string_args(self):
        d = ToolDispatcher(_make_engine())
        args, err = d.parse_arguments("")
        assert args == {}
        assert err is None

    def test_parse_dict_args(self):
        d = ToolDispatcher(_make_engine())
        args, err = d.parse_arguments({"key": "value"})
        assert args == {"key": "value"}
        assert err is None

    def test_parse_json_string_args(self):
        d = ToolDispatcher(_make_engine())
        args, err = d.parse_arguments('{"cell": "A1", "value": 42}')
        assert args == {"cell": "A1", "value": 42}
        assert err is None

    def test_parse_invalid_json(self):
        d = ToolDispatcher(_make_engine())
        args, err = d.parse_arguments("{bad json")
        assert args == {}
        assert err is not None
        assert "JSON" in err

    def test_parse_non_dict_json(self):
        d = ToolDispatcher(_make_engine())
        args, err = d.parse_arguments("[1, 2, 3]")
        assert args == {}
        assert err is not None
        assert "对象" in err or "dict" in err.lower() or "类型" in err

    def test_parse_invalid_type(self):
        d = ToolDispatcher(_make_engine())
        args, err = d.parse_arguments(12345)
        assert args == {}
        assert err is not None


class TestCallRegistryTool:
    """普通工具调用。"""

    async def test_call_simple_tool(self):
        engine = _make_engine(tool_result="cell A1 = hello")
        d = ToolDispatcher(engine)
        result = await d.call_registry_tool(
            tool_name="read_cell",
            arguments={"cell": "A1"},
            tool_scope=None,
        )
        assert result == "cell A1 = hello"
        engine._registry.call_tool.assert_called_once()

    async def test_call_tool_with_scope(self):
        engine = _make_engine(tool_result="ok")
        d = ToolDispatcher(engine)
        result = await d.call_registry_tool(
            tool_name="write_cell",
            arguments={"cell": "A1", "value": "test"},
            tool_scope=["write_cell", "read_cell"],
        )
        assert result == "ok"

    async def test_result_truncation(self):
        engine = _make_engine(tool_result="very long result")
        tool_def = MagicMock()
        tool_def.truncate_result = MagicMock(return_value="truncated")
        engine._registry.get_tool = MagicMock(return_value=tool_def)
        d = ToolDispatcher(engine)
        result = await d.call_registry_tool(
            tool_name="read_cell",
            arguments={},
            tool_scope=None,
        )
        assert result == "truncated"


class TestPrepareImageForVlm:
    """测试图片预处理双模式。"""

    def _make_test_image(self, w=200, h=100, color=(180, 180, 180)):
        """创建测试用灰底图片。"""
        from io import BytesIO
        from PIL import Image
        img = Image.new("RGB", (w, h), color)
        buf = BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def test_data_mode_enhances_contrast(self):
        """data 模式应增强对比度（灰底图片会被处理）。"""
        from io import BytesIO
        from PIL import Image
        import random
        random.seed(42)
        # 创建带噪声的灰色图片（JPEG 压缩后更小，确保不会回退到原始 PNG）
        img = Image.new("RGB", (800, 600), (200, 200, 200))
        pixels = img.load()
        for x in range(800):
            for y in range(600):
                v = 200 + random.randint(-15, 15)
                pixels[x, y] = (v, v, v)
        buf = BytesIO()
        img.save(buf, format="PNG")
        raw = buf.getvalue()
        result_data, _ = ToolDispatcher._prepare_image_for_vlm(
            raw, mode="data"
        )
        result_style, _ = ToolDispatcher._prepare_image_for_vlm(
            raw, mode="style"
        )
        # data 模式处理后应与 style 模式不同（data 会做对比度增强等）
        assert result_data != result_style

    def test_style_mode_preserves_colors(self):
        """style 模式应保留原始颜色信息（仅缩放）。"""
        from io import BytesIO
        from PIL import Image
        raw = self._make_test_image(color=(100, 150, 200))
        result, _ = ToolDispatcher._prepare_image_for_vlm(
            raw, mode="style"
        )
        # style 模式输出的图片应保留蓝色调
        img = Image.open(BytesIO(result))
        center = img.getpixel((100, 50))
        # R < G < B 的蓝色调应保留
        assert center[2] > center[0]  # B > R

    def test_default_mode_is_data(self):
        """默认模式应为 data（向后兼容）。"""
        raw = self._make_test_image()
        default_result, _ = ToolDispatcher._prepare_image_for_vlm(raw)
        data_result, _ = ToolDispatcher._prepare_image_for_vlm(
            raw, mode="data"
        )
        assert default_result == data_result
