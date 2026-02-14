"""窗口感知规则引擎测试。"""

from excelmanus.mcp.manager import add_tool_prefix
from excelmanus.window_perception.models import WindowType
from excelmanus.window_perception.rules import classify_tool, is_window_relevant_tool


class TestWindowRules:
    """工具分类测试。"""

    def test_classify_explorer_tool(self) -> None:
        result = classify_tool("scan_excel_files")
        assert result.window_type == WindowType.EXPLORER

    def test_classify_sheet_tool(self) -> None:
        result = classify_tool("read_excel")
        assert result.window_type == WindowType.SHEET

    def test_classify_sheet_style_tool(self) -> None:
        result = classify_tool("adjust_row_height")
        assert result.window_type == WindowType.SHEET

    def test_classify_mcp_tool(self) -> None:
        prefixed = add_tool_prefix("excel", "read_sheet")
        result = classify_tool(prefixed)
        assert result.window_type == WindowType.SHEET

    def test_unknown_tool_not_relevant(self) -> None:
        assert is_window_relevant_tool("run_shell") is False
