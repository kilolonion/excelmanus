"""工具呈现层（ToolProfile）：控制 LLM 看到的 schema 详细度。

v5 三层正交架构之 Layer 1。
与 Skill 层完全解耦（Skill 只负责知识注入）。
与 ToolPolicy 完全解耦（ToolPolicy 只负责安全拦截）。
"""
from __future__ import annotations

from typing import Literal

ToolTier = Literal["core", "extended"]

# ── core: 始终展示完整 OpenAI tool schema ──────────────────
CORE_TOOLS: frozenset[str] = frozenset({
    # 数据读取
    "read_excel", "analyze_data", "filter_data",
    "group_aggregate", "inspect_excel_files",
    "analyze_sheet_mapping",
    # 结构发现
    "list_sheets", "list_directory", "get_file_info",
    "find_files", "read_text_file", "read_cell_styles",
    # 元工具（由 engine 注册，此处仅声明 tier）
    "activate_skill", "expand_tools",
    "focus_window", "task_create", "task_update",
    "finish_task", "ask_user",
    "delegate_to_subagent", "list_subagents",
    "memory_save", "memory_read_topic",
})

# ── extended 工具类别 ──────────────────────────────────────
EXTENDED_CATEGORIES: frozenset[str] = frozenset({
    "data_write", "format", "advanced_format",
    "chart", "sheet", "code", "file_ops",
})

CATEGORY_DESCRIPTIONS: dict[str, str] = {
    "data_write": "数据写入（write_excel, write_cells, transform_data, insert_rows, insert_columns）",
    "format": "基础格式化（format_cells, 列宽行高, 合并/取消合并单元格）",
    "advanced_format": "高级格式化（条件格式, 仪表盘主题, 色阶, 数据条, 打印布局等）",
    "chart": "图表生成（create_chart 生成 PNG, create_excel_chart 嵌入原生图表）",
    "sheet": "工作表管理（create/copy/rename/delete_sheet, 跨表复制）",
    "code": "代码执行（write_text_file, run_code, run_shell）",
    "file_ops": "文件操作（copy_file, rename_file, delete_file）",
}

# ── 全量 ToolProfile 定义 ─────────────────────────────────
TOOL_PROFILES: dict[str, dict] = {}

# 自动填充 core
for _name in CORE_TOOLS:
    TOOL_PROFILES[_name] = {"tier": "core", "category": "meta"}

# extended 工具按类别
_EXTENDED_TOOL_MAP: dict[str, tuple[str, ...]] = {
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

for _category, _tools in _EXTENDED_TOOL_MAP.items():
    for _tool_name in _tools:
        TOOL_PROFILES[_tool_name] = {"tier": "extended", "category": _category}


def get_tier(tool_name: str) -> ToolTier | None:
    """返回工具的 tier，不存在返回 None。"""
    profile = TOOL_PROFILES.get(tool_name)
    return profile["tier"] if profile else None


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
