"""WURM v2 focus 语义测试。"""

from __future__ import annotations

from excelmanus.window_perception.manager import WindowPerceptionManager
from excelmanus.window_perception.models import (
    CachedRange,
    DetailLevel,
    IntentTag,
    PerceptionBudget,
    WindowState,
    WindowType,
)


def _build_manager() -> WindowPerceptionManager:
    return WindowPerceptionManager(
        enabled=True,
        budget=PerceptionBudget(system_budget_tokens=3000),
    )


def _build_sheet_window(window_id: str) -> WindowState:
    return WindowState(
        id=window_id,
        type=WindowType.SHEET,
        title=f"{window_id}.xlsx/Q1",
        file_path=f"{window_id}.xlsx",
        sheet_name="Q1",
        viewport_range="A1:C3",
        data_buffer=[{"A": 1, "B": 2, "C": 3}],
    )


def test_focus_switch_downgrades_previous_active_window() -> None:
    manager = _build_manager()
    old = _build_sheet_window("sheet_1")
    new = _build_sheet_window("sheet_2")
    manager._windows[old.id] = old
    manager._windows[new.id] = new
    manager._active_window_id = old.id

    result = manager.focus_window_action(
        window_id=new.id,
        action="restore",
    )
    assert result["status"] == "ok"
    assert manager._active_window_id == new.id
    assert old.detail_level == DetailLevel.SUMMARY


def test_focus_invalid_window_returns_available_windows() -> None:
    manager = _build_manager()
    w1 = _build_sheet_window("sheet_1")
    w2 = _build_sheet_window("sheet_2")
    manager._windows[w1.id] = w1
    manager._windows[w2.id] = w2

    result = manager.focus_window_action(
        window_id="sheet_x",
        action="restore",
    )
    assert result["status"] == "error"
    assert result["available_windows"] == ["sheet_1", "sheet_2"]


def test_clear_filter_sets_validate_intent_and_refreshes_lock() -> None:
    manager = _build_manager()
    window = _build_sheet_window("sheet_1")
    window.unfiltered_buffer = [{"A": 1}, {"A": 2}]
    window.filter_state = {"column": "A", "operator": "gt", "value": 1}
    window.data_buffer = [{"A": 2}]
    manager._windows[window.id] = window
    manager._active_window_id = window.id

    result = manager.focus_window_action(
        window_id=window.id,
        action="clear_filter",
    )
    assert result["status"] == "ok"
    assert window.intent_tag == IntentTag.VALIDATE
    assert window.intent_updated_turn >= 1
    assert window.intent_lock_until_turn >= window.intent_updated_turn


def test_scroll_and_expand_keep_original_intent() -> None:
    manager = _build_manager()
    window = _build_sheet_window("sheet_1")
    window.intent_tag = IntentTag.FORMAT
    window.intent_confidence = 0.8
    manager._windows[window.id] = window
    manager._active_window_id = window.id

    scroll_result = manager.focus_window_action(
        window_id=window.id,
        action="scroll",
        range_ref="A2:C3",
    )
    expand_result = manager.focus_window_action(
        window_id=window.id,
        action="expand",
        rows=2,
    )
    assert scroll_result["status"] in {"ok", "needs_refill"}
    assert expand_result["status"] in {"ok", "needs_refill"}
    assert window.intent_tag == IntentTag.FORMAT


def test_focus_hit_promotes_window_to_active() -> None:
    manager = _build_manager()
    old = _build_sheet_window("sheet_1")
    new = _build_sheet_window("sheet_2")
    new.cached_ranges = [
        CachedRange(
            range_ref="A1:C3",
            rows=[{"A": 1, "B": 2, "C": 3}],
            is_current_viewport=True,
            added_at_iteration=1,
        )
    ]
    manager._windows[old.id] = old
    manager._windows[new.id] = new
    manager._active_window_id = old.id

    result = manager.focus_window_action(
        window_id=new.id,
        action="scroll",
        range_ref="A1:C3",
    )

    assert result["status"] == "ok"
    assert result["active_window_id"] == new.id
    assert manager._active_window_id == new.id
    assert old.detail_level == DetailLevel.SUMMARY
