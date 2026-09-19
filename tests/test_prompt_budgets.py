"""提示词段预算：frontmatter max_tokens 与场景四栏。"""

from __future__ import annotations

from excelmanus.prompt.budget import collect_report, over_budget_names


def test_declared_segments_are_within_max_tokens() -> None:
    report = collect_report()
    assert over_budget_names(report) == []


def test_native_write_principle_tokens_meet_target() -> None:
    report = collect_report()
    write = next(item for item in report.scenarios if item.name == "native_write_new")
    assert write.principle_tokens <= 1200
    assert "spreadsheet:invariants" in write.section_names
    assert "edit_spreadsheet" in write.visible_tools
    existing = next(item for item in report.scenarios if item.name == "native_write_existing")
    assert "edit_spreadsheet" in existing.visible_tools
    code = next(item for item in report.scenarios if item.name == "code_write_existing")
    assert "run_code" in code.visible_tools


def test_code_mode_wire_collapses_but_sdk_discovery_sees_execution_catalog() -> None:
    """code 模式：wire 只留 run_code，但发现必须绑执行目录。

    回归：曾把 run_code-only 外层目录绑给 introspect，十次查询
    只返回“工具不存在于当前目录”，discovery_tokens≈280 是假成本。
    """
    report = collect_report()
    code = next(item for item in report.scenarios if item.name == "code_write_existing")
    assert code.wire_tools == ("run_code",)
    assert "edit_spreadsheet" in code.sdk_tools
    assert "inspect_spreadsheet" in code.sdk_tools
    assert "工具不存在于当前目录" not in code.discovery_text
    assert "workbook_spec" in code.discovery_text or "sheets" in code.discovery_text
    # 真实发现成本应显著大于十条“不可用”回复
    assert code.discovery_tokens > 1000


def test_wire_tools_include_session_level_meta_tools() -> None:
    """真实 envelope 含 task/plan/元工具；预算不能只序列化基础目录。"""
    report = collect_report()
    write = next(item for item in report.scenarios if item.name == "native_write_existing")
    for name in ("task_create", "task_update", "skill", "delegate", "ask_user"):
        assert name in write.wire_tools, name
    plan = next(item for item in report.scenarios if item.name == "native_plan")
    assert "write_plan" in plan.wire_tools
    assert "exit_plan_mode" in plan.wire_tools
