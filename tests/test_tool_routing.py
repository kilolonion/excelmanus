"""工具可见性测试：只读硬边界，不再按路由标签裁剪。"""

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
    """build_v5_tools_impl 只按 chat_mode 硬边界过滤。"""

    @pytest.fixture(autouse=True)
    def _setup_engine(self):
        from excelmanus.tools.registry import ToolDef, ToolRegistry

        registry = ToolRegistry()
        for name, effect in (
            ("inspect_spreadsheet", "none"),
            ("edit_spreadsheet", "workspace_write"),
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
        engine._bench_mode = False
        engine._tools_cache = None
        engine._tools_cache_key = None
        engine._current_chat_mode = "write"
        engine.active_model = "test-model"

        from excelmanus.engine_core.meta_tools import MetaToolBuilder
        self.builder = MetaToolBuilder(engine)
        self.builder.build_meta_tools = MagicMock(return_value=[])
        self.engine = engine

    def test_write_mode_exposes_all_domain_tools(self):
        self.engine._current_chat_mode = "write"
        names = _extract_tool_names(self.builder.build_v5_tools_impl())
        assert "inspect_spreadsheet" in names
        assert "edit_spreadsheet" in names
        assert "run_code" in names
        assert "write_text_file" in names

    def test_read_only_hides_write_tools(self):
        self.engine._current_chat_mode = "read"
        names = _extract_tool_names(
            self.builder.build_v5_tools_impl(tool_access="read_only"),
        )
        assert "inspect_spreadsheet" in names
        assert "run_code" in names
        assert "edit_spreadsheet" not in names
        assert "write_text_file" not in names
        assert "inspect_spreadsheet" in READ_ONLY_SAFE_TOOLS
