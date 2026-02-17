"""header_row schema 指引一致性测试。"""

from __future__ import annotations

from excelmanus.tools import chart_tools, data_tools


def _get_prop_desc(tool_defs, tool_name: str, prop_name: str) -> str:
    for tool in tool_defs:
        if tool.name == tool_name:
            props = tool.input_schema.get("properties", {})
            prop = props.get(prop_name, {})
            return str(prop.get("description", ""))
    raise AssertionError(f"tool not found: {tool_name}")


def test_header_row_schema_guidance_is_consistent() -> None:
    checks = [
        (data_tools.get_tools(), "read_excel", "header_row"),
        (data_tools.get_tools(), "analyze_data", "header_row"),
        (data_tools.get_tools(), "filter_data", "header_row"),
        (data_tools.get_tools(), "transform_data", "header_row"),
        (data_tools.get_tools(), "group_aggregate", "header_row"),
        (data_tools.get_tools(), "analyze_sheet_mapping", "left_header_row"),
        (data_tools.get_tools(), "analyze_sheet_mapping", "right_header_row"),
        (chart_tools.get_tools(), "create_chart", "header_row"),
    ]

    for tool_defs, tool_name, prop_name in checks:
        desc = _get_prop_desc(tool_defs, tool_name, prop_name)
        assert "自动检测" in desc
        assert "建议不传" in desc
        assert "read_excel" in desc
