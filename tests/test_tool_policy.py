"""工具策略 SSOT 一致性测试。"""

from __future__ import annotations

from pathlib import Path

from excelmanus.subagent.builtin import BUILTIN_SUBAGENTS
from excelmanus.tools import ToolRegistry
from excelmanus.tools.policy import (
    FALLBACK_DISCOVERY_TOOLS,
    MUTATING_ALL_TOOLS,
    MUTATING_AUDIT_ONLY_TOOLS,
    MUTATING_CONFIRM_TOOLS,
    SUBAGENT_ANALYSIS_EXTRA_TOOLS,
    SUBAGENT_READ_ONLY_TOOLS,
    SUBAGENT_WRITE_EXTRA_TOOLS,
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
