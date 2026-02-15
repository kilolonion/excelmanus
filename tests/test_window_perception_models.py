"""窗口感知模型测试。"""

from excelmanus.window_perception import DetailLevel, PerceptionBudget, Viewport, WindowState, WindowType


class TestWindowModels:
    """模型定义测试。"""

    def test_viewport_defaults(self) -> None:
        viewport = Viewport()
        assert viewport.range_ref == "A1:J25"
        assert viewport.visible_rows == 25
        assert viewport.visible_cols == 10

    def test_window_state_defaults(self) -> None:
        state = WindowState(id="w1", type=WindowType.SHEET, title="test")
        assert state.sheet_tabs == []
        assert state.preview_rows == []
        assert state.metadata == {}
        assert state.columns == []
        assert state.data_buffer == []
        assert state.cached_ranges == []
        assert state.viewport_range == ""
        assert state.detail_level == DetailLevel.FULL
        assert state.idle_turns == 0
        assert state.last_access_seq == 0
        assert state.dormant is False

    def test_budget_defaults(self) -> None:
        budget = PerceptionBudget()
        assert budget.system_budget_tokens == 3000
        assert budget.tool_append_tokens == 500
        assert budget.max_windows == 6
        assert budget.background_after_idle == 1
        assert budget.suspend_after_idle == 3
        assert budget.terminate_after_idle == 5
        assert budget.window_full_max_rows == 25
        assert budget.window_full_total_budget_tokens == 500
        assert budget.window_data_buffer_max_rows == 200
