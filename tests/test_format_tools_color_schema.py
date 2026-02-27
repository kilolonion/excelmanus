"""测试 format_cells schema 中包含颜色名列表。

Batch 2 精简：format_cells 已删除，测试降级为验证 get_tools 返回空列表。
"""

from excelmanus.tools.format_tools import get_tools


class TestFormatCellsColorSchema:
    """Batch 2 精简：format_cells 已删除。"""

    def test_top_description_contains_color_names(self) -> None:
        assert len(get_tools()) == 0

    def test_font_color_description_mentions_names(self) -> None:
        """Batch 2 精简：跳过。"""

    def test_fill_color_description_mentions_names(self) -> None:
        """Batch 2 精简：跳过。"""
