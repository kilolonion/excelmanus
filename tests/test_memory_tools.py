"""memory_tools 单元测试：验证 memory_read_topic 工具函数和注册逻辑。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from excelmanus.memory_models import MemoryCategory, MemoryEntry
from excelmanus.persistent_memory import PersistentMemory
from excelmanus.tools import memory_tools
from excelmanus.tools.memory_tools import (
    bind_memory_context,
    get_tools,
    init_memory,
    memory_read_topic,
)


class TestInitMemory:
    """init_memory 初始化函数测试。"""

    def test_init_with_instance(self, tmp_path: Path) -> None:
        """传入 PersistentMemory 实例后，工具函数可正常使用。"""
        pm = PersistentMemory(str(tmp_path))
        init_memory(pm)
        # 验证模块级变量已设置
        assert memory_tools._persistent_memory is pm

    def test_init_with_none(self) -> None:
        """传入 None 表示功能未启用。"""
        init_memory(None)
        assert memory_tools._persistent_memory is None

    def teardown_method(self) -> None:
        """每个测试后重置模块级变量。"""
        init_memory(None)


class TestMemoryReadTopic:
    """memory_read_topic 工具函数测试。"""

    def setup_method(self) -> None:
        """每个测试前重置模块级变量。"""
        init_memory(None)

    def teardown_method(self) -> None:
        """每个测试后重置模块级变量。"""
        init_memory(None)

    def test_not_initialized_returns_hint(self) -> None:
        """PersistentMemory 未初始化时返回提示信息。"""
        result = memory_read_topic(topic="file_patterns")
        assert "持久记忆功能未启用" in result

    def test_unsupported_topic_returns_error(self, tmp_path: Path) -> None:
        """不支持的主题名返回错误提示。"""
        pm = PersistentMemory(str(tmp_path))
        init_memory(pm)
        result = memory_read_topic(topic="unknown_topic")
        assert "不支持的主题" in result

    def test_empty_topic_returns_hint(self, tmp_path: Path) -> None:
        """主题文件不存在时返回暂无内容提示。"""
        pm = PersistentMemory(str(tmp_path))
        init_memory(pm)
        result = memory_read_topic(topic="file_patterns")
        assert "暂无记忆内容" in result

    def test_reads_file_patterns(self, tmp_path: Path) -> None:
        """正确读取 file_patterns.md 内容。"""
        pm = PersistentMemory(str(tmp_path))
        topic_file = tmp_path / "file_patterns.md"
        topic_file.write_text(
            "### [2025-01-15 14:30] file_pattern\n\n文件结构: 列名: 日期, 产品, 数量\n\n---",
            encoding="utf-8",
        )
        init_memory(pm)

        result = memory_read_topic(topic="file_patterns")
        assert "文件结构" in result
        assert "日期" in result

    def test_reads_user_prefs(self, tmp_path: Path) -> None:
        """正确读取 user_prefs.md 内容。"""
        pm = PersistentMemory(str(tmp_path))
        topic_file = tmp_path / "user_prefs.md"
        topic_file.write_text(
            "### [2025-01-15 14:30] user_pref\n\n偏好柱状图，蓝色主题\n\n---",
            encoding="utf-8",
        )
        init_memory(pm)

        result = memory_read_topic(topic="user_prefs")
        assert "柱状图" in result

    def test_reads_error_solutions(self, tmp_path: Path) -> None:
        """正确读取 error_solutions.md 内容。"""
        pm = PersistentMemory(str(tmp_path))
        topic_file = tmp_path / "error_solutions.md"
        topic_file.write_text(
            "### [2025-01-15 14:30] error_solution\n\nopenpyxl 版本冲突时固定到 3.1.x\n\n---",
            encoding="utf-8",
        )
        init_memory(pm)

        result = memory_read_topic(topic="error_solutions")
        assert "openpyxl" in result

    def test_reads_general_topic(self, tmp_path: Path) -> None:
        """正确读取 general.md 内容。"""
        pm = PersistentMemory(str(tmp_path))
        topic_file = tmp_path / "general.md"
        topic_file.write_text(
            "### [2025-01-15 14:30] general\n\n项目默认使用中文回复\n\n---",
            encoding="utf-8",
        )
        init_memory(pm)

        result = memory_read_topic(topic="general")
        assert "中文回复" in result

    def test_alias_topic_is_compatible(self, tmp_path: Path) -> None:
        """旧别名 error_solution 仍可读取新主题文件。"""
        pm = PersistentMemory(str(tmp_path))
        topic_file = tmp_path / "error_solutions.md"
        topic_file.write_text(
            "### [2025-01-15 14:30] error_solution\n\n别名兼容测试\n\n---",
            encoding="utf-8",
        )
        init_memory(pm)

        result = memory_read_topic(topic="error_solution")
        assert "别名兼容测试" in result

    def test_bind_memory_context_overrides_global(self, tmp_path: Path) -> None:
        """上下文绑定应覆盖全局引用，退出后恢复。"""
        pm_global = PersistentMemory(str(tmp_path / "global"))
        (pm_global.memory_dir / "user_prefs.md").write_text(
            "### [2025-01-15 14:30] user_pref\n\n全局\n\n---", encoding="utf-8"
        )
        pm_local = PersistentMemory(str(tmp_path / "local"))
        (pm_local.memory_dir / "user_prefs.md").write_text(
            "### [2025-01-15 14:30] user_pref\n\n局部\n\n---", encoding="utf-8"
        )
        init_memory(pm_global)

        with bind_memory_context(pm_local):
            assert "局部" in memory_read_topic(topic="user_prefs")

        assert "全局" in memory_read_topic(topic="user_prefs")

    def test_bind_memory_context_none_blocks_global_fallback(self, tmp_path: Path) -> None:
        """上下文显式绑定 None 时应禁用全局回退。"""
        pm_global = PersistentMemory(str(tmp_path / "global"))
        (pm_global.memory_dir / "user_prefs.md").write_text(
            "### [2025-01-15 14:30] user_pref\n\n全局\n\n---", encoding="utf-8"
        )
        init_memory(pm_global)

        with bind_memory_context(None):
            result = memory_read_topic(topic="user_prefs")
            assert "持久记忆功能未启用" in result

    def test_reads_content_written_by_save_entries(self, tmp_path: Path) -> None:
        """验证通过 save_entries 写入的内容可以被 memory_read_topic 读取。"""
        pm = PersistentMemory(str(tmp_path))
        init_memory(pm)

        entries = [
            MemoryEntry(
                content="用户偏好深色主题",
                category=MemoryCategory.USER_PREF,
                timestamp=datetime(2025, 1, 15, 14, 30),
            ),
        ]
        pm.save_entries(entries)

        result = memory_read_topic(topic="user_prefs")
        assert "深色主题" in result


class TestGetTools:
    """get_tools 导出函数测试。"""

    def test_returns_list(self) -> None:
        """返回工具定义列表。"""
        tools = get_tools()
        assert isinstance(tools, list)
        assert len(tools) == 2

    def test_tool_name(self) -> None:
        """工具名称正确。"""
        tool = get_tools()[0]
        assert tool.name == "memory_read_topic"

    def test_tool_schema_has_topic_enum(self) -> None:
        """工具 schema 中 topic 参数包含 enum 约束。"""
        tool = get_tools()[0]
        topic_prop = tool.input_schema["properties"]["topic"]
        assert "enum" in topic_prop
        assert "file_patterns" in topic_prop["enum"]
        assert "user_prefs" in topic_prop["enum"]
        assert "error_solutions" in topic_prop["enum"]
        assert "general" in topic_prop["enum"]

    def test_tool_func_is_callable(self) -> None:
        """工具函数可调用。"""
        tool = get_tools()[0]
        assert callable(tool.func)

    def test_tool_schema_requires_topic(self) -> None:
        """topic 是必填参数。"""
        tool = get_tools()[0]
        assert "topic" in tool.input_schema["required"]


class TestRegistration:
    """验证 memory_tools 在 ToolRegistry 中的注册。"""

    def test_register_builtin_includes_memory_tool(self, tmp_path: Path) -> None:
        """register_builtin_tools 后 memory_read_topic 工具已注册。"""
        from excelmanus.tools.registry import ToolRegistry

        registry = ToolRegistry()
        registry.register_builtin_tools(str(tmp_path))
        assert registry.get_tool("memory_read_topic") is not None
