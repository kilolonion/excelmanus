"""header_row schema 指引一致性测试。"""

from __future__ import annotations

from excelmanus.tools import workbook_tools


def _get_prop_desc(tool_defs, tool_name: str, prop_name: str) -> str:
    for tool in tool_defs:
        if tool.name == tool_name:
            props = tool.input_schema.get("properties", {})
            prop = props.get(prop_name, {})
            return str(prop.get("description", ""))
    raise AssertionError(f"tool not found: {tool_name}")


def test_header_row_schema_guidance_is_consistent() -> None:
    tools = workbook_tools.get_tools()
    for tool_name in ("split_spreadsheet", "analyze_spreadsheet"):
        desc = _get_prop_desc(tools, tool_name, "header_row")
        assert "自动检测" in desc
