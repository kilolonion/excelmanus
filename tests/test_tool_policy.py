"""工具策略 SSOT 一致性测试。"""

from __future__ import annotations

from pathlib import Path

from excelmanus.subagent.builtin import BUILTIN_SUBAGENTS
from excelmanus.tools import ToolRegistry
from excelmanus.tools.policy import (
    AUDIT_TARGET_ARG_RULES_ALL,
    AUDIT_TARGET_ARG_RULES_FIRST,
    FALLBACK_DISCOVERY_TOOLS,
    MUTATING_ALL_TOOLS,
    MUTATING_AUDIT_ONLY_TOOLS,
    MUTATING_CONFIRM_TOOLS,
    SUBAGENT_ANALYSIS_EXTRA_TOOLS,
    SUBAGENT_READ_ONLY_TOOLS,
    SUBAGENT_WRITE_EXTRA_TOOLS,
    WORKSPACE_SCAN_EXCLUDE_PREFIXES,
    WORKSPACE_SCAN_MAX_FILES,
    WORKSPACE_SCAN_MAX_HASH_BYTES,
)


EXPECTED_MUTATING_CONFIRM_TOOLS = {
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

EXPECTED_MUTATING_AUDIT_ONLY_TOOLS = {
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


def test_mutating_tool_tiers_match_expected_contract() -> None:
    assert set(MUTATING_CONFIRM_TOOLS) == EXPECTED_MUTATING_CONFIRM_TOOLS
    assert set(MUTATING_AUDIT_ONLY_TOOLS) == EXPECTED_MUTATING_AUDIT_ONLY_TOOLS
    assert set(MUTATING_ALL_TOOLS) == (
        EXPECTED_MUTATING_CONFIRM_TOOLS | EXPECTED_MUTATING_AUDIT_ONLY_TOOLS
    )


def test_mutating_policy_covers_registered_mutating_like_tools(tmp_path: Path) -> None:
    registry = ToolRegistry()
    registry.register_builtin_tools(str(tmp_path))
    registered = set(registry.get_tool_names())

    missing = set(MUTATING_ALL_TOOLS) - registered
    assert not missing, f"策略中存在未注册工具: {sorted(missing)}"

    prefixes = (
        "write_",
        "copy_",
        "rename_",
        "delete_",
        "transform_",
        "create_",
        "format_",
        "adjust_",
        "merge_",
        "unmerge_",
        "insert_",
        "apply_",
        "add_",
        "style_",
        "scale_",
        "set_",
    )
    mutating_like = {
        name
        for name in registered
        if name in {"run_code", "run_shell"} or name.startswith(prefixes)
    }
    assert mutating_like == set(MUTATING_ALL_TOOLS), (
        "存在未分层的写入类工具或误分层工具，"
        f"diff={sorted(mutating_like ^ set(MUTATING_ALL_TOOLS))}"
    )


def test_audit_path_rules_cover_all_mutating_tools_except_workspace_scan_tools() -> None:
    path_ruled = set(AUDIT_TARGET_ARG_RULES_ALL) | set(AUDIT_TARGET_ARG_RULES_FIRST)
    expected = set(MUTATING_ALL_TOOLS) - {"run_code", "run_shell"}
    assert path_ruled == expected


def test_workspace_scan_budget_constants_contract() -> None:
    assert WORKSPACE_SCAN_MAX_FILES == 20000
    assert WORKSPACE_SCAN_MAX_HASH_BYTES == 256 * 1024 * 1024
    assert tuple(WORKSPACE_SCAN_EXCLUDE_PREFIXES) == (
        ".git",
        ".venv",
        "__pycache__",
        "outputs/approvals",
    )


def test_subagent_tool_scope_is_synced_with_policy() -> None:
    explorer = BUILTIN_SUBAGENTS["explorer"]
    analyst = BUILTIN_SUBAGENTS["analyst"]
    writer = BUILTIN_SUBAGENTS["writer"]

    assert set(explorer.allowed_tools) == set(SUBAGENT_READ_ONLY_TOOLS)
    assert set(analyst.allowed_tools) == (
        set(SUBAGENT_READ_ONLY_TOOLS) | set(SUBAGENT_ANALYSIS_EXTRA_TOOLS)
    )
    assert set(writer.allowed_tools) == (
        set(SUBAGENT_READ_ONLY_TOOLS) | set(SUBAGENT_WRITE_EXTRA_TOOLS)
    )


def test_fallback_discovery_excludes_memory_tool() -> None:
    assert "memory_read_topic" not in FALLBACK_DISCOVERY_TOOLS


def test_discovery_tools_are_read_only_or_focus() -> None:
    """基础发现工具集必须全部是只读安全工具或 focus_window。"""
    from excelmanus.tools.policy import DISCOVERY_TOOLS, READ_ONLY_SAFE_TOOLS
    non_readonly = DISCOVERY_TOOLS - READ_ONLY_SAFE_TOOLS - {"focus_window"}
    assert not non_readonly, f"非只读工具混入基础集: {sorted(non_readonly)}"


def test_discovery_tools_expected_members() -> None:
    from excelmanus.tools.policy import DISCOVERY_TOOLS
    expected = {
        "read_excel", "scan_excel_files", "analyze_data", "filter_data",
        "group_aggregate", "list_sheets", "list_directory", "get_file_info",
        "search_files", "read_text_file", "read_cell_styles", "focus_window",
    }
    assert DISCOVERY_TOOLS == expected


def test_tool_categories_cover_all_registered_tools(tmp_path: Path) -> None:
    """分类映射必须覆盖所有内置工具（不含 memory/task/skill/focus 元工具）。"""
    from excelmanus.tools.policy import TOOL_CATEGORIES
    categorized = set()
    for tools in TOOL_CATEGORIES.values():
        categorized.update(tools)
    registry = ToolRegistry()
    registry.register_builtin_tools(str(tmp_path))
    registered = set(registry.get_tool_names())
    meta_tools = {"memory_save", "memory_read_topic", "task_create", "task_update",
                  "list_skills", "focus_window", "scan_excel_files"}
    uncategorized = registered - categorized - meta_tools
    assert not uncategorized, f"未分类工具: {sorted(uncategorized)}"


def test_tool_categories_no_duplicates() -> None:
    from excelmanus.tools.policy import TOOL_CATEGORIES
    seen: set[str] = set()
    for cat, tools in TOOL_CATEGORIES.items():
        for tool in tools:
            assert tool not in seen, f"工具 '{tool}' 在多个分类中重复"
            seen.add(tool)
