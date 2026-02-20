"""Dashboard 状态模型单元测试。

覆盖：
- DashboardTurnState 默认值与更新
- DashboardTimelineEntry 时间线裁剪（上限 200 条）
- DashboardMetrics 统计累积
- DashboardSessionBadges 徽章生成
- subagent 统计累积与增量计算
"""

from __future__ import annotations

import time

import pytest

from excelmanus.cli_dashboard import (
    DashboardMetrics,
    DashboardSessionBadges,
    DashboardTimelineEntry,
    DashboardTurnState,
    UiLayoutMode,
)


# ══════════════════════════════════════════════════════════
# UiLayoutMode 枚举
# ══════════════════════════════════════════════════════════


class TestUiLayoutMode:
    def test_dashboard_value(self) -> None:
        assert UiLayoutMode.DASHBOARD.value == "dashboard"

    def test_classic_value(self) -> None:
        assert UiLayoutMode.CLASSIC.value == "classic"


# ══════════════════════════════════════════════════════════
# DashboardTimelineEntry
# ══════════════════════════════════════════════════════════


class TestDashboardTimelineEntry:
    def test_basic_creation(self) -> None:
        entry = DashboardTimelineEntry(
            icon="🔧",
            label="read_excel",
            detail="file=test.xlsx",
        )
        assert entry.icon == "🔧"
        assert entry.label == "read_excel"
        assert entry.detail == "file=test.xlsx"
        assert entry.elapsed_ms is None
        assert entry.category == "tool"

    def test_categories(self) -> None:
        for cat in ("tool", "subagent", "approval", "question", "system"):
            entry = DashboardTimelineEntry(icon="", label="", category=cat)
            assert entry.category == cat


# ══════════════════════════════════════════════════════════
# DashboardTurnState
# ══════════════════════════════════════════════════════════


class TestDashboardTurnState:
    def test_defaults(self) -> None:
        state = DashboardTurnState()
        assert state.turn_number == 0
        assert state.model_name == ""
        assert state.route_mode == ""
        assert state.skills_used == []
        assert state.timeline == []
        assert state.status == "idle"
        assert state.subagent_active is False
        assert state.subagent_name == ""
        assert state.subagent_turns == 0
        assert state.subagent_tool_calls == 0

    def test_add_timeline_entry(self) -> None:
        state = DashboardTurnState()
        entry = DashboardTimelineEntry(icon="🔧", label="read_excel")
        state.add_timeline_entry(entry)
        assert len(state.timeline) == 1
        assert state.timeline[0].label == "read_excel"

    def test_timeline_capped_at_200(self) -> None:
        state = DashboardTurnState()
        for i in range(250):
            state.add_timeline_entry(
                DashboardTimelineEntry(icon="🔧", label=f"tool_{i}")
            )
        assert len(state.timeline) == 200
        assert state.folded_count == 50

    def test_timeline_folded_count_accumulates(self) -> None:
        state = DashboardTurnState()
        for i in range(210):
            state.add_timeline_entry(
                DashboardTimelineEntry(icon="🔧", label=f"tool_{i}")
            )
        assert state.folded_count == 10
        # Add 10 more (total 220, should fold 20)
        for i in range(10):
            state.add_timeline_entry(
                DashboardTimelineEntry(icon="🔧", label=f"extra_{i}")
            )
        assert state.folded_count == 20

    def test_reset_for_new_turn(self) -> None:
        state = DashboardTurnState()
        state.turn_number = 5
        state.status = "thinking"
        state.add_timeline_entry(
            DashboardTimelineEntry(icon="🔧", label="test")
        )
        state.subagent_active = True
        state.reset_for_new_turn(turn_number=6, model_name="gpt4")
        assert state.turn_number == 6
        assert state.model_name == "gpt4"
        assert state.timeline == []
        assert state.folded_count == 0
        assert state.status == "thinking"
        assert state.subagent_active is False

    def test_subagent_stats_accumulate(self) -> None:
        state = DashboardTurnState()
        state.subagent_active = True
        state.subagent_name = "writer"
        # First iteration: establishes baseline
        state.update_subagent_iteration(turn=3, total_calls=10)
        assert state.subagent_turns == 3
        assert state.subagent_tool_calls == 10
        assert state.subagent_delta_calls == 10  # first call: delta = total
        # Second iteration: delta should be 5
        state.update_subagent_iteration(turn=4, total_calls=15)
        assert state.subagent_turns == 4
        assert state.subagent_tool_calls == 15
        assert state.subagent_delta_calls == 5

    def test_subagent_delta_calls_first_iteration(self) -> None:
        state = DashboardTurnState()
        state.update_subagent_iteration(turn=1, total_calls=3)
        assert state.subagent_delta_calls == 3


# ══════════════════════════════════════════════════════════
# DashboardMetrics
# ══════════════════════════════════════════════════════════


class TestDashboardMetrics:
    def test_defaults(self) -> None:
        m = DashboardMetrics()
        assert m.total_tool_calls == 0
        assert m.success_count == 0
        assert m.failure_count == 0
        assert m.total_tokens == 0

    def test_record_tool_result(self) -> None:
        m = DashboardMetrics()
        m.record_tool_result(success=True)
        m.record_tool_result(success=True)
        m.record_tool_result(success=False)
        assert m.total_tool_calls == 3
        assert m.success_count == 2
        assert m.failure_count == 1

    def test_record_tokens(self) -> None:
        m = DashboardMetrics()
        m.record_tokens(prompt=100, completion=50)
        m.record_tokens(prompt=200, completion=100)
        assert m.prompt_tokens == 300
        assert m.completion_tokens == 150
        assert m.total_tokens == 450


# ══════════════════════════════════════════════════════════
# DashboardSessionBadges
# ══════════════════════════════════════════════════════════


class TestDashboardSessionBadges:
    def test_defaults(self) -> None:
        b = DashboardSessionBadges()
        assert b.plan_mode is False
        assert b.full_access is False
        assert b.backup_enabled is True
        assert b.layout_mode == "dashboard"

    def test_to_badges_string(self) -> None:
        b = DashboardSessionBadges(
            plan_mode=True,
            full_access=True,
            backup_enabled=False,
            layout_mode="classic",
        )
        badges = b.to_badges_list()
        # Should contain plan, fullaccess indicators
        assert any("plan" in badge.lower() for badge in badges)
        assert any("full" in badge.lower() or "access" in badge.lower() for badge in badges)
