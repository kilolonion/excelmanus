"""CoW 路径映射功能测试（FileRegistry 单一数据源）。"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, PropertyMock

import pytest

from excelmanus.engine_core.session_state import SessionState


class _FakeFileRegistry:
    """最小 FileRegistry 测试桩。"""

    def __init__(
        self,
        mapping: dict[str, str] | None = None,
        *,
        panorama: str = "",
    ) -> None:
        self.has_versions = True
        self._mapping = dict(mapping or {})
        self._panorama = panorama

    def register_cow_mapping(self, src_rel: str, dst_rel: str) -> None:
        self._mapping[src_rel] = dst_rel

    def lookup_cow_redirect(self, rel_path: str) -> str | None:
        return self._mapping.get(rel_path)

    def get_cow_mappings(self) -> dict[str, str]:
        return dict(self._mapping)

    def build_panorama(self) -> str:
        return self._panorama


# ── SessionState CoW 统一入口 ───────────────────────────────


class TestCowPathRegistry:
    """SessionState 仅透传 FileRegistry，不再维护本地 dict。"""

    def test_default_empty(self):
        state = SessionState()
        assert state.get_cow_mappings() == {}
        assert state.lookup_cow_redirect("bench/external/data.xlsx") is None

    def test_register_without_registry_noop(self):
        state = SessionState()
        state.register_cow_mappings({"bench/external/data.xlsx": "outputs/data.xlsx"})
        assert state.get_cow_mappings() == {}
        assert state.lookup_cow_redirect("bench/external/data.xlsx") is None

    def test_register_empty_mapping_noop(self):
        state = SessionState()
        state.register_cow_mappings({})
        assert state.get_cow_mappings() == {}

    def test_register_none_like_noop(self):
        state = SessionState()
        state.register_cow_mappings(None)  # type: ignore
        assert state.get_cow_mappings() == {}

    def test_register_and_lookup_via_file_registry(self):
        state = SessionState()
        state._file_registry = _FakeFileRegistry()
        state.register_cow_mappings({"bench/external/data.xlsx": "outputs/data.xlsx"})
        assert state.lookup_cow_redirect("bench/external/data.xlsx") == "outputs/data.xlsx"
        assert state.get_cow_mappings() == {"bench/external/data.xlsx": "outputs/data.xlsx"}

    def test_lookup_cow_redirect_not_found(self):
        state = SessionState()
        state._file_registry = _FakeFileRegistry({"a.xlsx": "outputs/a.xlsx"})
        assert state.lookup_cow_redirect("bench/external/data.xlsx") is None

    def test_reset_session_does_not_crash_with_registry(self):
        state = SessionState()
        state._file_registry = _FakeFileRegistry()
        state.register_cow_mappings({"bench/external/data.xlsx": "outputs/data.xlsx"})
        state.reset_session()
        assert state.lookup_cow_redirect("bench/external/data.xlsx") == "outputs/data.xlsx"

    def test_reset_loop_stats_keeps_registry_mapping(self):
        state = SessionState()
        state._file_registry = _FakeFileRegistry()
        state.register_cow_mappings({"bench/external/data.xlsx": "outputs/data.xlsx"})
        state.reset_loop_stats()
        assert state.lookup_cow_redirect("bench/external/data.xlsx") == "outputs/data.xlsx"


# ── ToolDispatcher CoW 映射提取 ─────────────────────────────


class TestExtractAndRegisterCowMapping:
    """ToolDispatcher._extract_and_register_cow_mapping 测试。"""

    def _make_dispatcher(self):
        """构造最小化的 ToolDispatcher。"""
        from excelmanus.engine_core.tool_dispatcher import ToolDispatcher

        engine = MagicMock()
        file_registry = _FakeFileRegistry()
        _state = SessionState()
        _state._file_registry = file_registry
        engine._state = _state
        engine.state = _state
        engine.file_registry = file_registry
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
        assert engine._state.get_cow_mappings() == {"bench/external/data.xlsx": "outputs/data.xlsx"}

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
        assert engine._state.get_cow_mappings() == {}

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
        assert len(engine._state.get_cow_mappings()) == 2


# ── ToolDispatcher CoW 路径拦截 ──────────────────────────────


class TestRedirectCowPaths:
    """ToolDispatcher._redirect_cow_paths 测试。"""

    def _make_dispatcher(self, registry: dict[str, str] | None = None, workspace_root: str = "/workspace"):
        from excelmanus.engine_core.tool_dispatcher import ToolDispatcher

        engine = MagicMock()
        _state = SessionState()
        engine._state = _state
        engine.state = _state
        file_registry = None
        if registry is not None:
            file_registry = _FakeFileRegistry(registry)
            _state._file_registry = file_registry
        engine.file_registry = file_registry
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
    """ContextBuilder._build_file_registry_notice CoW 部分测试（统一接口）。"""

    def _make_builder(
        self,
        registry: dict[str, str] | None = None,
        *,
        panorama: str = "",
    ):
        from excelmanus.engine_core.context_builder import ContextBuilder

        engine = MagicMock()
        _state = SessionState()
        file_registry = _FakeFileRegistry(registry, panorama=panorama) if registry is not None else None
        _state._file_registry = file_registry
        engine._state = _state
        engine.state = _state
        engine.file_registry = file_registry
        builder = ContextBuilder(engine)
        return builder

    def test_empty_registry_returns_empty(self):
        builder = self._make_builder()
        assert builder._build_file_registry_notice() == ""

    def test_single_mapping_notice(self):
        builder = self._make_builder(
            registry={"bench/external/data.xlsx": "outputs/data.xlsx"},
        )
        notice = builder._build_file_registry_notice()
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
        notice = builder._build_file_registry_notice()
        assert "a.xlsx" in notice
        assert "b.xlsx" in notice
        # 应该是表格格式
        assert "|" in notice

    def test_notice_contains_table_format(self):
        builder = self._make_builder(
            registry={"src.xlsx": "outputs/src.xlsx"},
        )
        notice = builder._build_file_registry_notice()
        assert "| 原始路径（禁止访问） | 副本路径（请使用） |" in notice
        assert "| `src.xlsx` | `outputs/src.xlsx` |" in notice

    def test_panorama_and_cow_can_coexist(self):
        builder = self._make_builder(
            registry={"src.xlsx": "outputs/src.xlsx"},
            panorama="## 工作区文件全景\n- src.xlsx",
        )
        notice = builder._build_file_registry_notice()
        assert "工作区文件全景" in notice
        assert "文件保护路径映射（CoW）" in notice
