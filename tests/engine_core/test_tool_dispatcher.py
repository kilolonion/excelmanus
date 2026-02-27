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
    reg = _make_registry(tool_result)
    engine._registry = reg
    engine.registry = reg
    engine._persistent_memory = None
    engine._database = None
    engine.config = MagicMock()
    engine.config.code_policy_enabled = False
    engine.state = MagicMock()
    engine.approval = MagicMock()
    engine.approval.is_audit_only_tool = MagicMock(return_value=False)
    engine.approval.is_high_risk_tool = MagicMock(return_value=False)
    engine.full_access_enabled = False
    engine._plan_intercept_task_create = False
    engine._suspend_task_create_plan_once = False
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


class TestCallVlmResponseFormat:
    """测试 _call_vlm_with_retry 的 response_format 透传。"""

    async def test_response_format_passed_to_client(self):
        """response_format 参数应透传给 VLM client。"""
        from unittest.mock import MagicMock

        dispatcher = MagicMock()
        dispatcher._sanitize_vlm_error = ToolDispatcher._sanitize_vlm_error

        captured_kwargs = {}
        async def mock_create(**kwargs):
            captured_kwargs.update(kwargs)
            resp = MagicMock()
            resp.choices = [MagicMock()]
            resp.choices[0].message.content = '{"test": true}'
            return resp

        mock_client = MagicMock()
        mock_client.chat.completions.create = mock_create

        rf = {"type": "json_object"}
        raw, err, fr = await ToolDispatcher._call_vlm_with_retry(
            dispatcher,
            messages=[{"role": "user", "content": "test"}],
            vlm_client=mock_client,
            vlm_model="test-model",
            vlm_timeout=30,
            vlm_max_retries=0,
            vlm_base_delay=1.0,
            response_format=rf,
        )
        assert raw == '{"test": true}'
        assert captured_kwargs.get("response_format") == rf

    async def test_no_response_format_by_default(self):
        """默认不传 response_format。"""
        from unittest.mock import MagicMock

        dispatcher = MagicMock()
        dispatcher._sanitize_vlm_error = ToolDispatcher._sanitize_vlm_error

        captured_kwargs = {}
        async def mock_create(**kwargs):
            captured_kwargs.update(kwargs)
            resp = MagicMock()
            resp.choices = [MagicMock()]
            resp.choices[0].message.content = "hello"
            return resp

        mock_client = MagicMock()
        mock_client.chat.completions.create = mock_create

        raw, err, fr = await ToolDispatcher._call_vlm_with_retry(
            dispatcher,
            messages=[{"role": "user", "content": "test"}],
            vlm_client=mock_client,
            vlm_model="test-model",
            vlm_timeout=30,
            vlm_max_retries=0,
            vlm_base_delay=1.0,
        )
        assert "response_format" not in captured_kwargs


# ════════════════════════════════════════════════════════════════
# _parse_vlm_json
# ════════════════════════════════════════════════════════════════


class TestParseVlmJson:
    def test_plain_json(self):
        from excelmanus.engine_core.tool_dispatcher import _parse_vlm_json
        result = _parse_vlm_json('{"tables": []}')
        assert result == {"tables": []}

    def test_fenced_json(self):
        from excelmanus.engine_core.tool_dispatcher import _parse_vlm_json
        text = '```json\n{"tables": [{"name": "S1"}]}\n```'
        result = _parse_vlm_json(text)
        assert result is not None
        assert result["tables"][0]["name"] == "S1"

    def test_prefix_suffix_pollution(self):
        from excelmanus.engine_core.tool_dispatcher import _parse_vlm_json
        text = 'Here is the result:\n{"tables": []}\nDone.'
        result = _parse_vlm_json(text)
        assert result == {"tables": []}

    def test_invalid_returns_none(self):
        from excelmanus.engine_core.tool_dispatcher import _parse_vlm_json
        assert _parse_vlm_json("not json at all") is None

    def test_empty_returns_none(self):
        from excelmanus.engine_core.tool_dispatcher import _parse_vlm_json
        assert _parse_vlm_json("") is None

    def test_truncated_json_without_repair_returns_none(self):
        from excelmanus.engine_core.tool_dispatcher import _parse_vlm_json
        truncated = '{"tables": [{"name": "Sheet1", "cells": [{"addr": "A1"'
        assert _parse_vlm_json(truncated) is None

    def test_truncated_json_with_repair_succeeds(self):
        from excelmanus.engine_core.tool_dispatcher import _parse_vlm_json
        truncated = '{"tables": [{"name": "Sheet1", "cells": [{"addr": "A1", "val": "hello"},'
        result = _parse_vlm_json(truncated, try_repair=True)
        assert result is not None
        assert result["tables"][0]["name"] == "Sheet1"
        assert result["tables"][0]["cells"][0]["addr"] == "A1"

    def test_truncated_json_repair_preserves_complete_cells(self):
        """修复截断 JSON 时保留所有已完成的单元格数据（含部分有效的末尾元素）。"""
        from excelmanus.engine_core.tool_dispatcher import _parse_vlm_json
        truncated = (
            '{"tables": [{"name": "S1", "cells": ['
            '{"addr": "A1", "val": "x"}, '
            '{"addr": "A2", "val": "y"}, '
            '{"addr": "A3", "val":'  # 截断在值位置
        )
        result = _parse_vlm_json(truncated, try_repair=True)
        assert result is not None
        cells = result["tables"][0]["cells"]
        # A3 的 addr 仍然有效（val 被截断丢弃），所以保留 3 个 cell
        assert len(cells) == 3
        assert cells[0]["addr"] == "A1"
        assert cells[1]["addr"] == "A2"
        assert cells[2]["addr"] == "A3"
        assert "val" not in cells[2]  # val 被截断


class TestRepairTruncatedJson:
    def test_repair_nested_arrays(self):
        from excelmanus.engine_core.tool_dispatcher import _repair_truncated_json
        # 截断在 4 处，修复回退到最后一个 , 之前，保留 [3]
        fragment = '{"a": [1, 2, [3, 4'
        result = _repair_truncated_json(fragment)
        assert result is not None
        assert result["a"][:2] == [1, 2]
        assert 3 in result["a"][2]

    def test_repair_with_strings(self):
        from excelmanus.engine_core.tool_dispatcher import _repair_truncated_json
        fragment = '{"key": "value", "arr": [{"n": "test"}'
        result = _repair_truncated_json(fragment)
        assert result is not None
        assert result["key"] == "value"

    def test_unrepairable_returns_none(self):
        from excelmanus.engine_core.tool_dispatcher import _repair_truncated_json
        assert _repair_truncated_json('just text') is None
