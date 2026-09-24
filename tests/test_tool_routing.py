"""工具可见性测试：read/plan 的有效目录不含写效应工具。"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from excelmanus.tools.policy import READ_ONLY_SAFE_TOOLS


def _extract_tool_names(schemas: list[dict]) -> set[str]:
    names = set()
    for s in schemas:
        func = s.get("function", {})
        name = func.get("name", "") or s.get("name", "")
        if name:
            names.add(name)
    return names


class TestBuildV5ToolsReadOnly:
    """build_v5_tools_impl 按 chat_mode 投影有效目录；read/plan 不暴露写工具。"""

    @pytest.fixture(autouse=True)
    def _setup_engine(self):
        from excelmanus.tools.registry import ToolDef, ToolRegistry

        registry = ToolRegistry()
        for name, effect in (
            ("observe_spreadsheet", "none"),
            ("apply_spreadsheet_changes", "workspace_write"),
            ("run_code", "dynamic"),
            ("write_text_file", "workspace_write"),
            ("introspect_capability", "none"),
        ):
            registry.register_tool(ToolDef(
                name=name,
                description=f"test tool {name}",
                input_schema={"type": "object", "properties": {}},
                func=lambda: None,
                write_effect=effect,
            ))

        engine = MagicMock()
        engine._registry = registry
        engine.registry = registry
        engine._active_skills = []
        engine._tools_cache = None
        engine._tools_cache_key = None
        engine._current_chat_mode = "write"
        engine._fixed_capability = None
        engine._loaded_tool_names = set()
        engine._skill_router = None
        engine._skill_resolver = None
        engine._subagent_config = None
        engine.active_model = "test-model"

        from excelmanus.engine_core.meta_tools import MetaToolBuilder
        self.builder = MetaToolBuilder(engine)
        self.builder.build_meta_tools = MagicMock(return_value=[])
        self.engine = engine

    def test_write_mode_exposes_core_tools_and_defers_file_tools(self):
        self.engine._current_chat_mode = "write"
        names = _extract_tool_names(self.builder.build_v5_tools_impl())
        assert "observe_spreadsheet" in names
        assert "apply_spreadsheet_changes" in names
        assert "run_code" in names
        assert "write_text_file" not in names
        self.engine._loaded_tool_names.add("write_text_file")
        loaded_names = _extract_tool_names(self.builder.build_v5_tools_impl())
        assert "write_text_file" in loaded_names

    def test_read_only_restrict_hides_write_tools(self):
        self.engine._current_chat_mode = "read"
        names = _extract_tool_names(
            self.builder.build_v5_tools_impl(tool_access="read_only"),
        )
        assert "observe_spreadsheet" in names
        assert "run_code" not in names
        assert "apply_spreadsheet_changes" not in names
        assert "write_text_file" not in names
        assert "observe_spreadsheet" in READ_ONLY_SAFE_TOOLS

    def test_read_mode_catalog_hides_write_tools(self):
        """推翻旧语义：read 不再把写工具 schema 交给模型。"""
        self.engine._current_chat_mode = "read"
        names = _extract_tool_names(self.builder.build_v5_tools_impl())
        assert "observe_spreadsheet" in names
        assert "apply_spreadsheet_changes" not in names
        assert "write_text_file" not in names
        assert "run_code" not in names

    def test_direct_and_programmatic_tools_coexist(self):
        names = _extract_tool_names(self.builder.build_v5_tools_impl())
        assert {"run_code", "observe_spreadsheet", "apply_spreadsheet_changes"} <= names

    def test_fixed_child_scope_intersects_loaded_tools(self):
        from excelmanus.tools.context import CallerCapability

        self.engine._fixed_capability = CallerCapability(
            catalog_mode="write",
            allowed_tools=frozenset({"observe_spreadsheet", "apply_spreadsheet_changes", "run_code"}),
            disallowed_tools=frozenset({"apply_spreadsheet_changes"}),
        )
        self.engine._loaded_tool_names = {"apply_spreadsheet_changes", "write_text_file"}
        names = _extract_tool_names(self.builder.build_v5_tools_impl())
        assert names == {"observe_spreadsheet", "run_code"}

    def test_fixed_readonly_scope_hides_writes(self):
        from excelmanus.tools.context import CallerCapability

        self.engine._fixed_capability = CallerCapability(tool_access="read_only")
        names = _extract_tool_names(self.builder.build_v5_tools_impl())
        assert "observe_spreadsheet" in names
        assert "apply_spreadsheet_changes" not in names
        assert "run_code" not in names

    def test_plan_mode_hides_write_tools_and_does_not_reuse_write_cache(self):
        """推翻旧语义：plan 与 write 不再共用同一份写工具目录。"""
        write = self.builder.build_v5_tools()
        key_write = self.engine._tools_cache_key
        write_names = _extract_tool_names(write)
        assert "apply_spreadsheet_changes" in write_names
        self.engine._current_chat_mode = "plan"
        plan = self.builder.build_v5_tools()
        plan_names = _extract_tool_names(plan)
        assert "observe_spreadsheet" in plan_names
        assert "apply_spreadsheet_changes" not in plan_names
        assert "write_text_file" not in plan_names
        assert plan != write
        assert self.engine._tools_cache_key != key_write
