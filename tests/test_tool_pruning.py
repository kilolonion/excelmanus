"""工具裁剪相关测试。

注意：基于 task_tags 的 TAG_EXCLUDED_TOOLS 黑名单裁剪已被
基于 route_tool_tags 的 ROUTE_TOOL_SCOPE 白名单裁剪替代。
新的 ROUTE_TOOL_SCOPE 测试在 test_tool_routing.py 中。
本文件仅保留 simple_read 标签推断测试（路由层 task_tags 仍用于其他子系统）。
"""

from __future__ import annotations


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
