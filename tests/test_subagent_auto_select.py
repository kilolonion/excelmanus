"""_auto_select_subagent 关键词路由单元测试。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from excelmanus.config import ExcelManusConfig
from excelmanus.engine import AgentEngine
from excelmanus.tools.registry import ToolRegistry


def _make_config(**overrides) -> ExcelManusConfig:
    defaults = {
        "api_key": "test-key",
        "base_url": "https://test.example.com/v1",
        "model": "test-model",
        "workspace_root": str(Path(__file__).resolve().parent),
        "backup_enabled": False,
    }
    defaults.update(overrides)
    return ExcelManusConfig(**defaults)


def _make_engine(**overrides) -> AgentEngine:
    cfg = _make_config(**{k: v for k, v in overrides.items() if k in ExcelManusConfig.__dataclass_fields__})
    registry = ToolRegistry()
    return AgentEngine(config=cfg, registry=registry)


class TestAutoSelectSubagentReadIntent:
    """只读意图应选中 explorer。"""

    @pytest.mark.asyncio
    async def test_chinese_read_keywords(self):
        engine = _make_engine()
        for task in ["查看这个文件的结构", "分析数据分布", "统计各部门人数", "读取销售数据"]:
            result = await engine._auto_select_subagent(task=task, file_paths=[])
            assert result == "explorer", f"task={task!r} should select explorer, got {result}"

    @pytest.mark.asyncio
    async def test_english_read_keywords(self):
        engine = _make_engine()
        for task in ["analyze the data", "list all sheets", "inspect files", "search for errors"]:
            result = await engine._auto_select_subagent(task=task, file_paths=[])
            assert result == "explorer", f"task={task!r} should select explorer, got {result}"

    @pytest.mark.asyncio
    async def test_mixed_read_keywords(self):
        engine = _make_engine()
        result = await engine._auto_select_subagent(task="预览这个 Excel 文件有哪些sheet", file_paths=[])
        assert result == "explorer"


class TestAutoSelectSubagentWriteIntent:
    """写入意图应选中 subagent（即使同时含只读关键词）。"""

    @pytest.mark.asyncio
    async def test_chinese_write_keywords(self):
        engine = _make_engine()
        for task in ["修改这个单元格", "写入新数据", "删除第一行", "创建图表"]:
            result = await engine._auto_select_subagent(task=task, file_paths=[])
            assert result == "subagent", f"task={task!r} should select subagent, got {result}"

    @pytest.mark.asyncio
    async def test_write_overrides_read(self):
        """同时含写入和只读关键词时，写入优先。"""
        engine = _make_engine()
        result = await engine._auto_select_subagent(task="分析数据后写入结果", file_paths=[])
        assert result == "subagent"

    @pytest.mark.asyncio
    async def test_english_write_keywords(self):
        engine = _make_engine()
        for task in ["create a chart", "delete the row", "save the result", "export to CSV"]:
            result = await engine._auto_select_subagent(task=task, file_paths=[])
            assert result == "subagent", f"task={task!r} should select subagent, got {result}"


class TestAutoSelectSubagentFallback:
    """无关键词命中时回退 subagent。"""

    @pytest.mark.asyncio
    async def test_no_keyword_match(self):
        engine = _make_engine()
        result = await engine._auto_select_subagent(task="你好", file_paths=[])
        assert result == "subagent"

    @pytest.mark.asyncio
    async def test_empty_task(self):
        engine = _make_engine()
        result = await engine._auto_select_subagent(task="", file_paths=[])
        assert result == "subagent"
