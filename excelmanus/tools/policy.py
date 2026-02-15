"""工具策略单一事实源（SSOT）。

集中维护：
1. 写入类工具的审批/审计分层；
2. 子代理默认工具域；
3. 审计目标路径映射与工作区扫描预算；
4. fallback 路由下的只读发现工具。
"""

from __future__ import annotations

# ── 只读安全白名单（低风险） ───────────────────────────────

# 仅显式白名单中的工具在 readOnly 模式下可直接执行。
# default 模式下的确认/审计行为由写入分层（Tier A/Tier B）决定。
READ_ONLY_SAFE_TOOLS: frozenset[str] = frozenset(
    {
        "read_excel",
        "analyze_data",
        "filter_data",
        "group_aggregate",
        "analyze_sheet_mapping",
        "list_sheets",
        "get_file_info",
        "search_files",
        "list_directory",
        "read_text_file",
        "read_cell_styles",
        "scan_excel_files",
        "list_skills",
        "memory_read_topic",
        # 任务工具仅修改会话内存态，不触达工作区文件。
        "task_create",
        "task_update",
    }
)

# ── 写入类工具分层 ──────────────────────────────────────────

# Tier A：需要进入 /accept 门禁确认后才能执行
MUTATING_CONFIRM_TOOLS: frozenset[str] = frozenset(
    {
        "write_text_file",
        "run_code",
        "run_shell",
        "delete_file",
        "rename_file",
        "write_excel",
        "transform_data",
        "create_sheet",
        "copy_sheet",
        "rename_sheet",
        "delete_sheet",
        "copy_range_between_sheets",
    }
)

# Tier B：不拦截确认，但必须纳入审计
MUTATING_AUDIT_ONLY_TOOLS: frozenset[str] = frozenset(
    {
        "copy_file",
        "create_chart",
        "create_excel_chart",
        "write_cells",
        "insert_rows",
        "insert_columns",
        "format_cells",
        "adjust_column_width",
        "adjust_row_height",
        "merge_cells",
        "unmerge_cells",
        "apply_threshold_icon_format",
        "style_card_blocks",
        "scale_range_unit",
        "apply_dashboard_dark_theme",
        "add_color_scale",
        "add_data_bar",
        "add_conditional_rule",
        "set_print_layout",
        "set_page_header_footer",
    }
)

MUTATING_ALL_TOOLS: frozenset[str] = MUTATING_CONFIRM_TOOLS | MUTATING_AUDIT_ONLY_TOOLS

if not MUTATING_CONFIRM_TOOLS.issubset(MUTATING_ALL_TOOLS):
    raise AssertionError("MUTATING_CONFIRM_TOOLS 必须是 MUTATING_ALL_TOOLS 子集")
if MUTATING_CONFIRM_TOOLS & MUTATING_AUDIT_ONLY_TOOLS:
    raise AssertionError("MUTATING_CONFIRM_TOOLS 与 MUTATING_AUDIT_ONLY_TOOLS 不允许交集")
if READ_ONLY_SAFE_TOOLS & MUTATING_ALL_TOOLS:
    raise AssertionError("READ_ONLY_SAFE_TOOLS 不允许包含写入工具")


# ── 审计目标路径映射（SSOT） ───────────────────────────────

# mode=all：提取所有非空字段作为目标文件
AUDIT_TARGET_ARG_RULES_ALL: dict[str, tuple[str, ...]] = {
    "write_text_file": ("file_path",),
    "copy_file": ("destination",),
    "rename_file": ("source", "destination"),
    "delete_file": ("file_path",),
    "write_excel": ("file_path",),
    "format_cells": ("file_path",),
    "adjust_column_width": ("file_path",),
    "adjust_row_height": ("file_path",),
    "merge_cells": ("file_path",),
    "unmerge_cells": ("file_path",),
    "create_sheet": ("file_path",),
    "copy_sheet": ("file_path",),
    "rename_sheet": ("file_path",),
    "delete_sheet": ("file_path",),
    "create_excel_chart": ("file_path",),
    "write_cells": ("file_path",),
    "insert_rows": ("file_path",),
    "insert_columns": ("file_path",),
    "apply_threshold_icon_format": ("file_path",),
    "style_card_blocks": ("file_path",),
    "scale_range_unit": ("file_path",),
    "apply_dashboard_dark_theme": ("file_path",),
    "add_color_scale": ("file_path",),
    "add_data_bar": ("file_path",),
    "add_conditional_rule": ("file_path",),
    "set_print_layout": ("file_path",),
    "set_page_header_footer": ("file_path",),
    "create_chart": ("output_path",),
}

# mode=first：按字段优先级提取第一个非空路径
AUDIT_TARGET_ARG_RULES_FIRST: dict[str, tuple[str, ...]] = {
    "transform_data": ("output_path", "file_path"),
    "copy_range_between_sheets": ("target_file", "source_file"),
}

_PATH_RULED_TOOLS = set(AUDIT_TARGET_ARG_RULES_ALL) | set(AUDIT_TARGET_ARG_RULES_FIRST)
_EXPECTED_PATH_RULED_TOOLS = set(MUTATING_ALL_TOOLS) - {"run_code", "run_shell"}
if _PATH_RULED_TOOLS != _EXPECTED_PATH_RULED_TOOLS:
    missing = sorted(_EXPECTED_PATH_RULED_TOOLS - _PATH_RULED_TOOLS)
    extra = sorted(_PATH_RULED_TOOLS - _EXPECTED_PATH_RULED_TOOLS)
    raise AssertionError(
        f"审计路径映射不完整或存在冗余：missing={missing}, extra={extra}"
    )


# ── 工作区补偿审计预算（run_code/run_shell） ───────────────

WORKSPACE_SCAN_MAX_FILES: int = 20000
WORKSPACE_SCAN_MAX_HASH_BYTES: int = 256 * 1024 * 1024
WORKSPACE_SCAN_EXCLUDE_PREFIXES: tuple[str, ...] = (
    ".git",
    ".venv",
    "__pycache__",
    "outputs/approvals",
)


# ── Subagent 工具域 ───────────────────────────────────────

SUBAGENT_READ_ONLY_TOOLS: tuple[str, ...] = (
    "read_excel",
    "analyze_data",
    "filter_data",
    "group_aggregate",
    "analyze_sheet_mapping",
    "list_sheets",
    "get_file_info",
    "search_files",
    "list_directory",
    "read_text_file",
    "read_cell_styles",
    "scan_excel_files",
)

SUBAGENT_ANALYSIS_EXTRA_TOOLS: tuple[str, ...] = (
    "run_code",
    "run_shell",
    "write_text_file",
)

SUBAGENT_WRITE_EXTRA_TOOLS: tuple[str, ...] = (
    "write_excel",
    "transform_data",
    "format_cells",
    "adjust_column_width",
    "adjust_row_height",
    "merge_cells",
    "unmerge_cells",
    "create_chart",
    "create_excel_chart",
    "create_sheet",
    "copy_sheet",
    "rename_sheet",
    "delete_sheet",
    "copy_range_between_sheets",
    "copy_file",
    "rename_file",
    "delete_file",
    "write_text_file",
    "run_code",
    "write_cells",
    "insert_rows",
    "insert_columns",
    "apply_threshold_icon_format",
    "style_card_blocks",
    "scale_range_unit",
    "apply_dashboard_dark_theme",
    "add_color_scale",
    "add_data_bar",
    "add_conditional_rule",
    "set_print_layout",
    "set_page_header_footer",
)


# ── 基础发现工具集（无 skill 激活时的默认 scope） ──────────

DISCOVERY_TOOLS: frozenset[str] = frozenset({
    # 数据探查
    "read_excel",
    "scan_excel_files",
    "analyze_data",
    "filter_data",
    "group_aggregate",
    # 结构探查
    "list_sheets",
    "list_directory",
    "get_file_info",
    "search_files",
    "read_text_file",
    # 样式感知
    "read_cell_styles",
    # 窗口
    "focus_window",
})


# ── 工具分类映射（用于 discover_tools 元工具和工具索引） ────

TOOL_CATEGORIES: dict[str, tuple[str, ...]] = {
    "data_read": (
        "read_excel", "scan_excel_files", "analyze_data",
        "filter_data", "group_aggregate", "analyze_sheet_mapping",
    ),
    "data_write": (
        "write_excel", "write_cells", "transform_data",
        "insert_rows", "insert_columns",
    ),
    "format": (
        "format_cells", "adjust_column_width", "adjust_row_height",
        "read_cell_styles", "merge_cells", "unmerge_cells",
    ),
    "advanced_format": (
        "apply_threshold_icon_format", "style_card_blocks",
        "scale_range_unit", "apply_dashboard_dark_theme",
        "add_color_scale", "add_data_bar", "add_conditional_rule",
        "set_print_layout", "set_page_header_footer",
    ),
    "chart": ("create_chart", "create_excel_chart"),
    "sheet": (
        "list_sheets", "create_sheet", "copy_sheet",
        "rename_sheet", "delete_sheet", "copy_range_between_sheets",
    ),
    "file": (
        "list_directory", "get_file_info", "search_files",
        "read_text_file", "copy_file", "rename_file", "delete_file",
    ),
    "code": ("write_text_file", "run_code", "run_shell"),
}


# ── fallback 路由只读发现工具（已废弃，使用 DISCOVERY_TOOLS 替代） ──
# 保留以兼容外部引用，内容与 DISCOVERY_TOOLS 同步
FALLBACK_DISCOVERY_TOOLS: tuple[str, ...] = tuple(sorted(DISCOVERY_TOOLS))
