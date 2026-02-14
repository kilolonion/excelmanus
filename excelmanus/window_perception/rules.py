"""窗口感知层规则引擎。"""

from __future__ import annotations

from dataclasses import dataclass

from excelmanus.mcp.manager import parse_tool_prefix

from .models import WindowType

_EXPLORER_TOOLS = {
    "list_directory",
    "search_files",
    "scan_excel_files",
}

_SHEET_TOOLS = {
    "read_excel",
    "list_sheets",
    "write_excel",
    "write_cells",
    "format_cells",
    "adjust_column_width",
    "adjust_row_height",
    "merge_cells",
    "unmerge_cells",
    "read_cell_styles",
    "add_color_scale",
    "add_data_bar",
    "add_conditional_rule",
    "create_sheet",
    "copy_sheet",
    "rename_sheet",
    "delete_sheet",
    "copy_range_between_sheets",
}

_MCP_EXPLORER_SUFFIXES = {
    "list_dir",
    "find_by_name",
}

_MCP_SHEET_SUFFIXES = {
    "read_sheet",
    "write_to_sheet",
    "format_range",
    "describe_sheets",
    "copy_sheet",
}


@dataclass(frozen=True)
class ToolClassification:
    """工具分类结果。"""

    canonical_name: str
    window_type: WindowType | None



def classify_tool(tool_name: str) -> ToolClassification:
    """将工具归类到窗口类型。"""
    name = (tool_name or "").strip()
    if not name:
        return ToolClassification(canonical_name="", window_type=None)

    if name in _EXPLORER_TOOLS:
        return ToolClassification(canonical_name=name, window_type=WindowType.EXPLORER)
    if name in _SHEET_TOOLS:
        return ToolClassification(canonical_name=name, window_type=WindowType.SHEET)

    if name.startswith("mcp_"):
        canonical = _canonicalize_mcp_tool_name(name)
        if canonical in _MCP_EXPLORER_SUFFIXES:
            return ToolClassification(canonical_name=canonical, window_type=WindowType.EXPLORER)
        if canonical in _MCP_SHEET_SUFFIXES:
            return ToolClassification(canonical_name=canonical, window_type=WindowType.SHEET)

    return ToolClassification(canonical_name=name, window_type=None)



def is_window_relevant_tool(tool_name: str) -> bool:
    """判断工具是否属于窗口感知范围。"""
    return classify_tool(tool_name).window_type is not None



def _canonicalize_mcp_tool_name(tool_name: str) -> str:
    """提取 MCP 工具原始名称。"""
    try:
        _, original = parse_tool_prefix(tool_name)
    except ValueError:
        original = tool_name.removeprefix("mcp_")
        if "_" in original:
            original = original.split("_", 1)[1]
    return (original or "").strip().lower()
