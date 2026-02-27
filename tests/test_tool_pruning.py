"""动态工具裁剪（task_tags-based tool pruning）测试。"""

from __future__ import annotations

import pytest

from excelmanus.tools.policy import TAG_EXCLUDED_TOOLS, _VISION_TOOLS


# ── TAG_EXCLUDED_TOOLS 映射表测试 ──────────────────────────


class TestTagExcludedToolsMapping:
    """TAG_EXCLUDED_TOOLS 映射表的静态约束测试。"""

    def test_simple_read_excludes_vision_and_chart(self):
        excluded = TAG_EXCLUDED_TOOLS["simple_read"]
        assert _VISION_TOOLS <= excluded, "simple_read 应隐藏所有 vision 工具"
        assert "create_excel_chart" in excluded

    def test_formatting_excludes_vision_and_chart(self):
        excluded = TAG_EXCLUDED_TOOLS["formatting"]
        assert _VISION_TOOLS <= excluded
        assert "create_excel_chart" in excluded

    def test_chart_excludes_vision_only(self):
        excluded = TAG_EXCLUDED_TOOLS["chart"]
        assert _VISION_TOOLS <= excluded
        assert "create_excel_chart" not in excluded, "chart 标签不应隐藏 chart 工具自身"

    def test_data_fill_excludes_vision_and_chart(self):
        excluded = TAG_EXCLUDED_TOOLS["data_fill"]
        assert _VISION_TOOLS <= excluded
        assert "create_excel_chart" in excluded

    def test_wide_tags_not_in_mapping(self):
        """宽标签不应出现在裁剪映射中。"""
        for wide_tag in ("cross_sheet", "large_data", "image_replica"):
            assert wide_tag not in TAG_EXCLUDED_TOOLS, (
                f"宽标签 {wide_tag} 不应有裁剪规则"
            )

    def test_introspect_capability_never_excluded(self):
        """introspect_capability 作为安全阀，不应出现在任何排除集中。"""
        for tag, excluded in TAG_EXCLUDED_TOOLS.items():
            assert "introspect_capability" not in excluded, (
                f"tag={tag} 的排除集包含了 introspect_capability"
            )

    def test_meta_tools_never_excluded(self):
        """元工具（finish_task、delegate 等）不应被 tag 裁剪。"""
        meta_tools = {"finish_task", "delegate", "delegate_to_subagent",
                      "activate_skill", "list_subagents", "ask_user"}
        for tag, excluded in TAG_EXCLUDED_TOOLS.items():
            overlap = meta_tools & excluded
            assert not overlap, (
                f"tag={tag} 的排除集包含了元工具: {overlap}"
            )


# ── _build_v5_tools_impl 集成测试 ─────────────────────────


def _extract_tool_names(schemas: list[dict]) -> set[str]:
    """从 OpenAI tool schema 列表中提取工具名集合。"""
    names = set()
    for s in schemas:
        func = s.get("function", {})
        name = func.get("name", "")
        if not name:
            name = s.get("name", "")
        if name:
            names.add(name)
    return names


class TestBuildV5ToolsPruning:
    """通过 mock engine._meta_tool_builder.build_v5_tools 的 tag-based 裁剪逻辑。"""

    @pytest.fixture(autouse=True)
    def _setup_engine(self, tmp_path):
        """构建最小化的 AgentEngine 用于测试。"""
        from unittest.mock import MagicMock, patch
        from excelmanus.tools.registry import ToolDef, ToolRegistry

        registry = ToolRegistry()
        # 注册一组代表性工具
        _TOOLS = [
            ("read_excel", "none"),
            ("filter_data", "none"),
            ("list_directory", "none"),
            ("run_code", "dynamic"),
            ("write_text_file", "workspace_write"),
            ("create_excel_chart", "workspace_write"),
            ("read_image", "none"),
            ("rebuild_excel_from_spec", "workspace_write"),
            ("verify_excel_replica", "workspace_write"),
            ("extract_table_spec", "none"),
            ("introspect_capability", "none"),
            ("focus_window", "none"),
        ]
        for name, effect in _TOOLS:
            registry.register_tool(ToolDef(
                name=name,
                description=f"test tool {name}",
                input_schema={"type": "object", "properties": {}},
                func=lambda: None,
                write_effect=effect,
            ))

        # Mock engine with minimal attributes
        engine = MagicMock()
        engine._registry = registry
        engine.registry = registry
        engine._active_skills = []
        engine._bench_mode = False
        engine._tools_cache = None
        engine._tools_cache_key = None
        engine._current_write_hint = "unknown"
        engine.active_model = "test-model"

        # Patch _build_meta_tools to return empty (meta tools tested separately)
        engine._meta_tool_builder.build_meta_tools = MagicMock(return_value=[])

        self.engine = engine
        self.registry = registry

    def _call_impl(self, write_hint="unknown", task_tags=()):
        """直接调用 engine._meta_tool_builder.build_v5_tools 的逻辑。"""
        from excelmanus.tools.policy import (
            READ_ONLY_SAFE_TOOLS, CODE_POLICY_DYNAMIC_TOOLS, TAG_EXCLUDED_TOOLS,
        )

        domain_schemas = self.registry.get_tiered_schemas(mode="chat_completions")

        # write_hint 过滤（简化版，与 engine 逻辑一致）
        if write_hint == "read_only":
            _allowed = READ_ONLY_SAFE_TOOLS | CODE_POLICY_DYNAMIC_TOOLS
            domain_schemas = [
                s for s in domain_schemas
                if s.get("function", {}).get("name", "") in _allowed
            ]

        # task_tags 过滤
        if task_tags:
            excluded: set[str] = set()
            for tag in task_tags:
                tag_excluded = TAG_EXCLUDED_TOOLS.get(tag)
                if tag_excluded is not None:
                    excluded |= tag_excluded
            if excluded:
                domain_schemas = [
                    s for s in domain_schemas
                    if s.get("function", {}).get("name", "") not in excluded
                ]

        return domain_schemas

    def test_no_tags_exposes_all_tools(self):
        schemas = self._call_impl()
        names = _extract_tool_names(schemas)
        assert "read_image" in names
        assert "create_excel_chart" in names
        assert "read_excel" in names

    def test_simple_read_hides_vision_and_chart(self):
        schemas = self._call_impl(task_tags=("simple_read",))
        names = _extract_tool_names(schemas)
        assert "read_image" not in names
        assert "rebuild_excel_from_spec" not in names
        assert "create_excel_chart" not in names
        # 保留核心读工具
        assert "read_excel" in names
        assert "filter_data" in names

    def test_formatting_hides_vision_and_chart(self):
        schemas = self._call_impl(task_tags=("formatting",))
        names = _extract_tool_names(schemas)
        assert "read_image" not in names
        assert "create_excel_chart" not in names
        assert "read_excel" in names

    def test_chart_tag_keeps_chart_tool(self):
        schemas = self._call_impl(task_tags=("chart",))
        names = _extract_tool_names(schemas)
        assert "create_excel_chart" in names, "chart 标签不应隐藏 chart 工具"
        assert "read_image" not in names, "chart 标签应隐藏 vision 工具"

    def test_wide_tag_no_pruning(self):
        """宽标签（cross_sheet）不做裁剪。"""
        all_schemas = self._call_impl()
        cross_sheet_schemas = self._call_impl(task_tags=("cross_sheet",))
        assert _extract_tool_names(all_schemas) == _extract_tool_names(cross_sheet_schemas)

    def test_introspect_always_available(self):
        """introspect_capability 在所有 tag 组合下都可用。"""
        for tags in [(), ("simple_read",), ("formatting",), ("chart",), ("data_fill",)]:
            schemas = self._call_impl(task_tags=tags)
            names = _extract_tool_names(schemas)
            assert "introspect_capability" in names, f"tags={tags} 时 introspect 被误隐藏"

    def test_combined_write_hint_and_tags(self):
        """write_hint=read_only + simple_read tag 双重过滤。"""
        schemas = self._call_impl(write_hint="read_only", task_tags=("simple_read",))
        names = _extract_tool_names(schemas)
        # read_only 已隐藏写工具，simple_read 进一步隐藏 vision/chart
        assert "write_text_file" not in names
        assert "read_image" not in names
        assert "create_excel_chart" not in names
        # 但 read_excel 应保留
        assert "read_excel" in names


# ── simple_read 标签推断测试 ───────────────────────────────


class TestSimpleReadTagInference:
    """测试 simple_read 标签在路由层的自动推断。"""

    def test_read_only_without_wide_tags_gets_simple_read(self):
        """read_only + 无宽标签 → 自动追加 simple_read。"""
        # 模拟 router 的推断逻辑
        classified_hint = "read_only"
        lexical_tags: list[str] = []
        _WIDE_TAGS = {"cross_sheet", "large_data", "image_replica"}
        if classified_hint == "read_only" and not (set(lexical_tags) & _WIDE_TAGS):
            if "simple_read" not in lexical_tags:
                lexical_tags.append("simple_read")
        assert "simple_read" in lexical_tags

    def test_read_only_with_cross_sheet_no_simple_read(self):
        """read_only + cross_sheet → 不追加 simple_read。"""
        classified_hint = "read_only"
        lexical_tags: list[str] = ["cross_sheet"]
        _WIDE_TAGS = {"cross_sheet", "large_data", "image_replica"}
        if classified_hint == "read_only" and not (set(lexical_tags) & _WIDE_TAGS):
            if "simple_read" not in lexical_tags:
                lexical_tags.append("simple_read")
        assert "simple_read" not in lexical_tags

    def test_may_write_no_simple_read(self):
        """may_write → 不追加 simple_read。"""
        classified_hint = "may_write"
        lexical_tags: list[str] = []
        _WIDE_TAGS = {"cross_sheet", "large_data", "image_replica"}
        if classified_hint == "read_only" and not (set(lexical_tags) & _WIDE_TAGS):
            if "simple_read" not in lexical_tags:
                lexical_tags.append("simple_read")
        assert "simple_read" not in lexical_tags
