"""提示词段预算：frontmatter max_tokens 与场景四栏。"""

from __future__ import annotations

import pytest

from excelmanus.prompt.budget import collect_report, over_budget_names


def test_declared_segments_are_within_max_tokens() -> None:
    report = collect_report()
    assert over_budget_names(report) == []


def test_write_principle_tokens_meet_target() -> None:
    report = collect_report()
    write = next(item for item in report.scenarios if item.name == "write_new")
    assert write.principle_tokens <= 1200
    assert "spreadsheet:invariants" in write.section_names
    assert "apply_spreadsheet_changes" in write.visible_tools
    existing = next(item for item in report.scenarios if item.name == "write_existing")
    assert "apply_spreadsheet_changes" in existing.visible_tools
    assert "run_code" in existing.visible_tools


def test_unified_wire_keeps_tools_and_sdk_details_are_discovered_on_demand() -> None:
    """直接工具与完整 SDK 共存，常驻 system 不注入全部签名。"""
    report = collect_report()
    write = next(item for item in report.scenarios if item.name == "write_existing")
    assert "apply_spreadsheet_changes" in write.visible_tools
    assert "run_code" in write.wire_tools
    assert "apply_spreadsheet_changes" in write.sdk_tools
    assert "observe_spreadsheet" in write.sdk_tools
    assert "def apply_spreadsheet_changes(" not in write.system_text
    assert "工具不存在于当前目录" not in write.discovery_text
    assert "workbook_spec" in write.discovery_text or "sheets" in write.discovery_text
    # 真实发现成本应显著大于十条“不可用”回复
    assert write.discovery_tokens > 1000


def test_wire_tools_include_session_level_meta_tools() -> None:
    """真实 envelope 含 task/plan/元工具；预算不能只序列化基础目录。"""
    report = collect_report()
    write = next(item for item in report.scenarios if item.name == "write_existing")
    for name in ("task_create", "task_update", "skill", "ask_user"):
        assert name in write.wire_tools, name
    assert "delegate" in write.visible_tools
    assert "delegate" in write.sdk_tools
    assert "delegate" not in write.wire_tools
    plan = next(item for item in report.scenarios if item.name == "plan")
    assert "write_plan" in plan.wire_tools
    assert "exit_plan_mode" in plan.wire_tools


@pytest.mark.parametrize("loaded", [False, True])
def test_budget_uses_actual_deferred_mcp_wire(tmp_path, monkeypatch, loaded) -> None:
    from excelmanus.prompt import budget
    from excelmanus.tools.registry import ToolDef

    make_engine = budget._make_engine

    def with_mcp(**kwargs):
        engine = make_engine(**kwargs)
        engine.registry.register_tool(ToolDef(
            name="mcp_budget_lookup", description="按需查询",
            input_schema={"type": "object", "properties": {}},
            func=lambda: {"status": "ok"}, write_effect="none",
        ))
        if loaded:
            engine._loaded_tool_names.add("mcp_budget_lookup")
        return engine

    monkeypatch.setattr(budget, "_make_engine", with_mcp)
    result = budget.measure_scenario(
        name="mcp", chat_mode="write", has_workbook=True,
        workspace=tmp_path,
    )
    assert "mcp_budget_lookup" in result.visible_tools
    assert "mcp_budget_lookup" in result.sdk_tools
    assert ("mcp_budget_lookup" in result.wire_tools) is loaded
