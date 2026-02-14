"""工具策略单一事实源（SSOT）。

集中维护：
1. 写入类工具的审批/审计分层；
2. 子代理默认工具域；
3. fallback 路由下的只读发现工具。
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


# ── fallback 路由只读发现工具 ───────────────────────────────

FALLBACK_DISCOVERY_TOOLS: tuple[str, ...] = (
    "scan_excel_files",
    "list_directory",
    "search_files",
    "list_sheets",
    "get_file_info",
    "read_excel",
    "filter_data",
    "analyze_data",
    "group_aggregate",
)
