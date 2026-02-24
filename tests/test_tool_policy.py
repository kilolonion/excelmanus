"""工具策略 SSOT 一致性测试。"""

from __future__ import annotations

from pathlib import Path

from excelmanus.subagent.builtin import BUILTIN_SUBAGENTS
from excelmanus.tools import ToolRegistry
from excelmanus.tools.policy import (
    AUDIT_TARGET_ARG_RULES_ALL,
    AUDIT_TARGET_ARG_RULES_FIRST,
    MUTATING_ALL_TOOLS,
    MUTATING_AUDIT_ONLY_TOOLS,
    MUTATING_CONFIRM_TOOLS,
    WORKSPACE_SCAN_EXCLUDE_PREFIXES,
    WORKSPACE_SCAN_MAX_FILES,
    WORKSPACE_SCAN_MAX_HASH_BYTES,
)


EXPECTED_MUTATING_CONFIRM_TOOLS = {
    "write_text_file",
    "run_shell",
    "delete_file",
    "rename_file",
    # Batch 1: write_excel, transform_data
    # Batch 3: create_sheet, copy_sheet, rename_sheet, delete_sheet, copy_range_between_sheets
}

EXPECTED_MUTATING_AUDIT_ONLY_TOOLS = {
    "copy_file",
    # Macro 工具
    "vlookup_write",
    "computed_column",
    # Vision 工具
    "rebuild_excel_from_spec",
    "verify_excel_replica",
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
        if name in {"run_code", "run_shell", "vlookup_write", "computed_column", "rebuild_excel_from_spec", "verify_excel_replica"}
        or name.startswith(prefixes)
    }
    from excelmanus.tools.policy import CODE_POLICY_DYNAMIC_TOOLS
    expected_all = set(MUTATING_ALL_TOOLS) | set(CODE_POLICY_DYNAMIC_TOOLS)
    assert mutating_like == expected_all, (
        "存在未分层的写入类工具或误分层工具，"
        f"diff={sorted(mutating_like ^ expected_all)}"
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
        "venv",
        "__pycache__",
        "node_modules",
        ".tox",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "dist",
        "build",
        ".worktrees",
        "outputs/approvals",
    )


def test_subagent_tool_scope_is_synced_with_policy() -> None:
    # v6: 唯一的内置 subagent 使用 full capability，allowed_tools 为空（继承全部）
    subagent = BUILTIN_SUBAGENTS["subagent"]
    assert subagent.allowed_tools == []
    assert subagent.capability_mode == "full"
    assert subagent.permission_mode == "acceptEdits"


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
                  "focus_window", "inspect_excel_files"}
    uncategorized = registered - categorized - meta_tools
    assert not uncategorized, f"未分类工具: {sorted(uncategorized)}"


def test_tool_categories_no_duplicates() -> None:
    from excelmanus.tools.policy import TOOL_CATEGORIES
    seen: set[str] = set()
    for cat, tools in TOOL_CATEGORIES.items():
        for tool in tools:
            assert tool not in seen, f"工具 '{tool}' 在多个分类中重复"
            seen.add(tool)
