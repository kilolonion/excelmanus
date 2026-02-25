"""CoW 路径注册表功能测试。

覆盖：
- SessionState 的 cow_path_registry 累积/查找/重置
- ToolDispatcher 的 cow_mapping 提取与注册
- ToolDispatcher 的 CoW 路径拦截重定向
- ContextBuilder 的 CoW 路径清单系统提示词注入
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, PropertyMock

import pytest

from excelmanus.engine_core.session_state import SessionState


# ── SessionState CoW 注册表 ─────────────────────────────────


class TestCowPathRegistry:
    """cow_path_registry 累积、查找、重置。"""

    def test_default_empty(self):
        state = SessionState()
        assert state.cow_path_registry == {}

    def test_register_single_mapping(self):
        state = SessionState()
        state.register_cow_mappings({"bench/external/data.xlsx": "outputs/data.xlsx"})
        assert state.cow_path_registry == {"bench/external/data.xlsx": "outputs/data.xlsx"}

    def test_register_multiple_mappings(self):
        state = SessionState()
        state.register_cow_mappings({
            "bench/external/a.xlsx": "outputs/a.xlsx",
            "bench/external/b.xlsx": "outputs/b.xlsx",
        })
        assert len(state.cow_path_registry) == 2

    def test_register_accumulates_across_calls(self):
        state = SessionState()
        state.register_cow_mappings({"bench/external/a.xlsx": "outputs/a.xlsx"})
        state.register_cow_mappings({"bench/external/b.xlsx": "outputs/b.xlsx"})
        assert len(state.cow_path_registry) == 2
        assert state.cow_path_registry["bench/external/a.xlsx"] == "outputs/a.xlsx"
        assert state.cow_path_registry["bench/external/b.xlsx"] == "outputs/b.xlsx"

    def test_register_overwrites_existing(self):
        state = SessionState()
        state.register_cow_mappings({"bench/external/a.xlsx": "outputs/a.xlsx"})
        state.register_cow_mappings({"bench/external/a.xlsx": "outputs/a_1.xlsx"})
        assert state.cow_path_registry["bench/external/a.xlsx"] == "outputs/a_1.xlsx"

    def test_register_empty_mapping_noop(self):
        state = SessionState()
        state.register_cow_mappings({})
        assert state.cow_path_registry == {}

    def test_register_none_like_noop(self):
        state = SessionState()
        state.register_cow_mappings(None)  # type: ignore
        assert state.cow_path_registry == {}

    def test_lookup_cow_redirect_found(self):
        state = SessionState()
        state.register_cow_mappings({"bench/external/data.xlsx": "outputs/data.xlsx"})
        assert state.lookup_cow_redirect("bench/external/data.xlsx") == "outputs/data.xlsx"

    def test_lookup_cow_redirect_not_found(self):
        state = SessionState()
        assert state.lookup_cow_redirect("bench/external/data.xlsx") is None

    def test_reset_session_clears_registry(self):
        state = SessionState()
        state.register_cow_mappings({"bench/external/data.xlsx": "outputs/data.xlsx"})
        state.reset_session()
        assert state.cow_path_registry == {}

    def test_reset_loop_stats_preserves_registry(self):
        """cow_path_registry 是会话级的，reset_loop_stats 不应清除。"""
        state = SessionState()
        state.register_cow_mappings({"bench/external/data.xlsx": "outputs/data.xlsx"})
        state.reset_loop_stats()
        assert len(state.cow_path_registry) == 1


# ── ToolDispatcher CoW 映射提取 ─────────────────────────────


class TestExtractAndRegisterCowMapping:
    """ToolDispatcher._extract_and_register_cow_mapping 测试。"""

    def _make_dispatcher(self):
        """构造最小化的 ToolDispatcher。"""
        from excelmanus.engine_core.tool_dispatcher import ToolDispatcher

        engine = MagicMock()
        _state = SessionState()
        engine._state = _state
        engine.state = _state
        engine.transaction = None
        dispatcher = ToolDispatcher(engine)
        return dispatcher, engine

    def test_extract_from_run_code_result(self):
        dispatcher, engine = self._make_dispatcher()
        result = json.dumps({
            "status": "success",
            "cow_mapping": {"bench/external/data.xlsx": "outputs/data.xlsx"},
        })
        extracted = dispatcher._extract_and_register_cow_mapping(result)
        assert extracted == {"bench/external/data.xlsx": "outputs/data.xlsx"}
        assert engine._state.cow_path_registry == {"bench/external/data.xlsx": "outputs/data.xlsx"}

    def test_extract_from_macro_tool_result(self):
        dispatcher, engine = self._make_dispatcher()
        result = json.dumps({
            "status": "success",
            "message": "写入完成",
            "cow_mapping": {"bench/external/src.xlsx": "outputs/src.xlsx"},
        })
        extracted = dispatcher._extract_and_register_cow_mapping(result)
        assert extracted == {"bench/external/src.xlsx": "outputs/src.xlsx"}

    def test_no_cow_mapping_returns_none(self):
        dispatcher, engine = self._make_dispatcher()
        result = json.dumps({"status": "success"})
        extracted = dispatcher._extract_and_register_cow_mapping(result)
        assert extracted is None
        assert engine._state.cow_path_registry == {}

    def test_empty_cow_mapping_returns_none(self):
        dispatcher, engine = self._make_dispatcher()
        result = json.dumps({"status": "success", "cow_mapping": {}})
        extracted = dispatcher._extract_and_register_cow_mapping(result)
        assert extracted is None

    def test_non_json_result_returns_none(self):
        dispatcher, _ = self._make_dispatcher()
        assert dispatcher._extract_and_register_cow_mapping("plain text") is None

    def test_non_dict_json_returns_none(self):
        dispatcher, _ = self._make_dispatcher()
        assert dispatcher._extract_and_register_cow_mapping(json.dumps([1, 2, 3])) is None

    def test_accumulates_across_multiple_calls(self):
        dispatcher, engine = self._make_dispatcher()
        r1 = json.dumps({"cow_mapping": {"a.xlsx": "outputs/a.xlsx"}})
        r2 = json.dumps({"cow_mapping": {"b.xlsx": "outputs/b.xlsx"}})
        dispatcher._extract_and_register_cow_mapping(r1)
        dispatcher._extract_and_register_cow_mapping(r2)
        assert len(engine._state.cow_path_registry) == 2


# ── ToolDispatcher CoW 路径拦截 ──────────────────────────────


class TestRedirectCowPaths:
    """ToolDispatcher._redirect_cow_paths 测试。"""

    def _make_dispatcher(self, registry: dict[str, str] | None = None, workspace_root: str = "/workspace"):
        from excelmanus.engine_core.tool_dispatcher import ToolDispatcher

        engine = MagicMock()
        _state = SessionState()
        engine._state = _state
        engine.state = _state
        if registry:
            _state.register_cow_mappings(registry)
        engine.config.workspace_root = workspace_root
        dispatcher = ToolDispatcher(engine)
        return dispatcher

    def test_no_registry_noop(self):
        dispatcher = self._make_dispatcher()
        args = {"file_path": "bench/external/data.xlsx"}
        new_args, reminders = dispatcher._redirect_cow_paths("read_excel", args)
        assert new_args == args
        assert reminders == []

    def test_redirect_read_excel_relative_path(self):
        dispatcher = self._make_dispatcher(
            registry={"bench/external/data.xlsx": "outputs/data.xlsx"},
        )
        args = {"file_path": "bench/external/data.xlsx"}
        new_args, reminders = dispatcher._redirect_cow_paths("read_excel", args)
        assert new_args["file_path"] == "outputs/data.xlsx"
        assert len(reminders) == 1
        assert "重定向" in reminders[0]

    def test_redirect_read_excel_absolute_path(self):
        dispatcher = self._make_dispatcher(
            registry={"bench/external/data.xlsx": "outputs/data.xlsx"},
            workspace_root="/workspace",
        )
        args = {"file_path": "/workspace/bench/external/data.xlsx"}
        new_args, reminders = dispatcher._redirect_cow_paths("read_excel", args)
        assert new_args["file_path"] == "/workspace/outputs/data.xlsx"
        assert len(reminders) == 1

    def test_no_match_no_redirect(self):
        dispatcher = self._make_dispatcher(
            registry={"bench/external/data.xlsx": "outputs/data.xlsx"},
        )
        args = {"file_path": "outputs/result.xlsx"}
        new_args, reminders = dispatcher._redirect_cow_paths("read_excel", args)
        assert new_args == args
        assert reminders == []

    def test_redirect_write_text_file(self):
        dispatcher = self._make_dispatcher(
            registry={"bench/external/report.txt": "outputs/report.txt"},
        )
        args = {"file_path": "bench/external/report.txt", "content": "hello"}
        new_args, reminders = dispatcher._redirect_cow_paths("write_text_file", args)
        assert new_args["file_path"] == "outputs/report.txt"
        assert new_args["content"] == "hello"  # 非路径字段不变

    def test_no_path_fields_tool_skipped(self):
        """没有路径字段映射的工具（如 ask_user）不做拦截。"""
        dispatcher = self._make_dispatcher(
            registry={"bench/external/data.xlsx": "outputs/data.xlsx"},
        )
        args = {"question": "which file?"}
        new_args, reminders = dispatcher._redirect_cow_paths("ask_user", args)
        assert new_args == args
        assert reminders == []


# ── ContextBuilder CoW 路径清单注入 ──────────────────────────


class TestBuildCowPathNotice:
    """ContextBuilder._build_cow_path_notice 测试。"""

    def _make_builder(self, registry: dict[str, str] | None = None):
        from excelmanus.engine_core.context_builder import ContextBuilder

        engine = MagicMock()
        _state = SessionState()
        engine._state = _state
        engine.state = _state
        if registry:
            _state.register_cow_mappings(registry)
        builder = ContextBuilder(engine)
        return builder

    def test_empty_registry_returns_empty(self):
        builder = self._make_builder()
        assert builder._build_cow_path_notice() == ""

    def test_single_mapping_notice(self):
        builder = self._make_builder(
            registry={"bench/external/data.xlsx": "outputs/data.xlsx"},
        )
        notice = builder._build_cow_path_notice()
        assert "⚠️ 文件保护路径映射（CoW）" in notice
        assert "bench/external/data.xlsx" in notice
        assert "outputs/data.xlsx" in notice
        assert "严禁访问原始路径" in notice

    def test_multiple_mappings_notice(self):
        builder = self._make_builder(
            registry={
                "bench/external/a.xlsx": "outputs/a.xlsx",
                "bench/external/b.xlsx": "outputs/b.xlsx",
            },
        )
        notice = builder._build_cow_path_notice()
        assert "a.xlsx" in notice
        assert "b.xlsx" in notice
        # 应该是表格格式
        assert "|" in notice

    def test_notice_contains_table_format(self):
        builder = self._make_builder(
            registry={"src.xlsx": "outputs/src.xlsx"},
        )
        notice = builder._build_cow_path_notice()
        assert "| 原始路径（禁止访问） | 副本路径（请使用） |" in notice
        assert "| `src.xlsx` | `outputs/src.xlsx` |" in notice
