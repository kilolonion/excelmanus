"""Window intent 层规则测试。"""

from __future__ import annotations

from excelmanus.window_perception.domain import Window
from excelmanus.window_perception.manager import WindowPerceptionManager
from excelmanus.window_perception.models import IntentTag, PerceptionBudget, WindowType
from tests.window_factories import make_window


def _build_manager() -> WindowPerceptionManager:
    return WindowPerceptionManager(
        enabled=True,
        budget=PerceptionBudget(),
    )


def _build_window() -> Window:
    return make_window(
        id="sheet_1",
        type=WindowType.SHEET,
        title="sales.xlsx/Q1",
        file_path="sales.xlsx",
        sheet_name="Q1",
    )


def test_resolve_intent_from_user_keywords() -> None:
    manager = _build_manager()
    manager.set_turn_hints(
        is_new_task=False,
        user_intent_summary="帮我汇总Q1总销量并给出占比",
        turn_intent_hint="汇总销量占比",
    )
    decision = manager._resolve_window_intent(
        window=_build_window(),
        canonical_tool_name="read_excel",
        arguments={"range": "A1:C20"},
        result_json=None,
    )
    assert decision["tag"] == IntentTag.AGGREGATE
    assert decision["source"] == "user_rule"


def test_user_intent_priority_over_tool_intent() -> None:
    manager = _build_manager()
    manager.set_turn_hints(
        is_new_task=False,
        user_intent_summary="把表头改成粗体蓝色样式",
        turn_intent_hint="粗体蓝色样式",
    )
    decision = manager._resolve_window_intent(
        window=_build_window(),
        canonical_tool_name="analyze_data",
        arguments={},
        result_json=None,
    )
    assert decision["tag"] == IntentTag.FORMAT
    assert decision["source"] == "user_rule"


def test_sticky_lock_keeps_current_intent() -> None:
    manager = _build_manager()
    window = _build_window()
    window.intent_tag = IntentTag.FORMAT
    window.intent_confidence = 0.9
    window.intent_lock_until_turn = 5
    manager.set_turn_hints(
        is_new_task=False,
        user_intent_summary="",
        turn_intent_hint="",
    )
    decision = manager._resolve_window_intent(
        window=window,
        canonical_tool_name="analyze_data",
        arguments={},
        result_json=None,
    )
    assert decision["tag"] == IntentTag.FORMAT
    assert decision["source"] == "carry"


def test_formula_intent_from_write_arguments() -> None:
    manager = _build_manager()
    manager.set_turn_hints(
        is_new_task=False,
        user_intent_summary="",
        turn_intent_hint="",
    )
    decision = manager._resolve_window_intent(
        window=_build_window(),
        canonical_tool_name="write_cells",
        arguments={"values": [["=SUMIFS(C:C,A:A,\"苹果\")"]]},
        result_json=None,
    )
    assert decision["tag"] == IntentTag.FORMULA
    assert decision["source"] == "tool_rule"
