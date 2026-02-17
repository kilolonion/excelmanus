"""测试 format_cells schema 中包含颜色名列表。"""

from excelmanus.tools.format_tools import get_tools


class TestFormatCellsColorSchema:
    """format_cells 的 schema description 应包含常用颜色名列表。"""

    def test_top_description_contains_color_names(self) -> None:
        tools = get_tools()
        format_cells_tool = next(t for t in tools if t.name == "format_cells")
        desc = format_cells_tool.description
        for color in ["深蓝", "浅蓝", "深红", "天蓝"]:
            assert color in desc, f"format_cells description 缺少颜色名 '{color}'"

    def test_font_color_description_mentions_names(self) -> None:
        tools = get_tools()
        format_cells_tool = next(t for t in tools if t.name == "format_cells")
        font_color_desc = format_cells_tool.input_schema["properties"]["font"]["properties"]["color"]["description"]
        assert "深蓝" in font_color_desc or "颜色名" in font_color_desc

    def test_fill_color_description_mentions_names(self) -> None:
        tools = get_tools()
        format_cells_tool = next(t for t in tools if t.name == "format_cells")
        fill_color_desc = format_cells_tool.input_schema["properties"]["fill"]["properties"]["color"]["description"]
        assert "深蓝" in fill_color_desc or "颜色名" in fill_color_desc
