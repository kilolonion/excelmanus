"""工具呈现层（ToolProfile）：工具元数据与分类。

v5 三层正交架构之 Layer 1。
与 Skill 层完全解耦（Skill 只负责知识注入）。
与 ToolPolicy 完全解耦（ToolPolicy 只负责安全拦截）。

v5.1: 废弃 core/extended 分层，所有工具始终暴露完整 schema。
"""
from __future__ import annotations

# ── 所有工具（v5.1: 统一暴露完整 schema） ──────────────────
CORE_TOOLS: frozenset[str] = frozenset({
    # 数据读取
    "read_excel", "analyze_data", "filter_data",
    "group_aggregate", "inspect_excel_files",
    "analyze_sheet_mapping",
    # 结构发现
    "list_sheets", "list_directory", "get_file_info",
    "find_files", "read_text_file", "read_cell_styles",
    # 元工具（由 engine 注册，此处仅声明 tier）
    "activate_skill",
    "focus_window", "task_create", "task_update",
    "finish_task", "ask_user",
    "delegate_to_subagent", "list_subagents",
    "memory_save", "memory_read_topic",
    # 数据写入
    "write_excel", "write_cells", "transform_data",
    "insert_rows", "insert_columns",
    # 格式化
    "format_cells", "adjust_column_width", "adjust_row_height",
    "merge_cells", "unmerge_cells",
    # 高级格式化
    "apply_threshold_icon_format", "style_card_blocks",
    "scale_range_unit", "apply_dashboard_dark_theme",
    "add_color_scale", "add_data_bar", "add_conditional_rule",
    "set_print_layout", "set_page_header_footer",
    # 图表
    "create_chart", "create_excel_chart",
    # 工作表管理
    "create_sheet", "copy_sheet", "rename_sheet",
    "delete_sheet", "copy_range_between_sheets",
    # 代码执行
    "write_text_file", "run_code", "run_shell",
    # 文件操作
    "copy_file", "rename_file", "delete_file",
})

# ── 全量 ToolProfile 定义 ─────────────────────────────────
TOOL_PROFILES: dict[str, dict] = {}

# v5.1: 所有工具统一为 core tier
for _name in CORE_TOOLS:
    TOOL_PROFILES[_name] = {"tier": "core", "category": "meta"}

# 按类别设置 category（tier 统一为 core）
_TOOL_CATEGORY_MAP: dict[str, tuple[str, ...]] = {
    "data_write": (
        "write_excel", "write_cells", "transform_data",
        "insert_rows", "insert_columns",
    ),
    "format": (
        "format_cells", "adjust_column_width", "adjust_row_height",
        "merge_cells", "unmerge_cells",
    ),
    "advanced_format": (
        "apply_threshold_icon_format", "style_card_blocks",
        "scale_range_unit", "apply_dashboard_dark_theme",
        "add_color_scale", "add_data_bar", "add_conditional_rule",
        "set_print_layout", "set_page_header_footer",
    ),
    "chart": ("create_chart", "create_excel_chart"),
    "sheet": (
        "create_sheet", "copy_sheet", "rename_sheet",
        "delete_sheet", "copy_range_between_sheets",
    ),
    "code": ("write_text_file", "run_code", "run_shell"),
    "file_ops": ("copy_file", "rename_file", "delete_file"),
}

for _category, _tools in _TOOL_CATEGORY_MAP.items():
    for _tool_name in _tools:
        TOOL_PROFILES[_tool_name] = {"tier": "core", "category": _category}


def get_category(tool_name: str) -> str | None:
    """返回工具的 category。"""
    profile = TOOL_PROFILES.get(tool_name)
    return profile["category"] if profile else None


def get_tools_in_category(category: str) -> list[str]:
    """返回指定 category 中的所有工具名。"""
    return [
        name for name, profile in TOOL_PROFILES.items()
        if profile.get("category") == category
    ]
