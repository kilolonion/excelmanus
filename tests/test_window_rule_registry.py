"""WURM v2 规则注册表测试。"""

from __future__ import annotations

from excelmanus.window_perception.models import IntentTag, WindowType
from excelmanus.window_perception.rule_registry import (
    classify_tool_meta,
    repeat_threshold,
    resolve_intent_decision,
)


def test_classify_tool_meta_for_sheet_read_tool() -> None:
    meta = classify_tool_meta("read_excel")
    assert meta.window_type == WindowType.SHEET
    assert meta.read_like is True
    assert meta.write_like is False


def test_classify_tool_meta_for_unknown_tool() -> None:
    meta = classify_tool_meta("run_shell")
    assert meta.window_type is None
    assert meta.read_like is False
    assert meta.write_like is False


def test_resolve_intent_decision_user_rule_has_priority() -> None:
    decision = resolve_intent_decision(
        current_tag=IntentTag.GENERAL,
        current_confidence=0.0,
        current_lock_until_turn=0,
        current_turn=2,
        intent_enabled=True,
        sticky_turns=3,
        user_intent_text="请帮我把表头改成粗体蓝色样式",
        canonical_tool_name="analyze_data",
        arguments={},
        result_json=None,
    )
    assert decision.tag == IntentTag.FORMAT
    assert decision.source == "user_rule"
    assert decision.rule_id.startswith("user_")


def test_resolve_intent_decision_sticky_lock_keeps_current_tag() -> None:
    decision = resolve_intent_decision(
        current_tag=IntentTag.FORMAT,
        current_confidence=0.9,
        current_lock_until_turn=5,
        current_turn=3,
        intent_enabled=True,
        sticky_turns=3,
        user_intent_text="",
        canonical_tool_name="filter_data",
        arguments={},
        result_json=None,
    )
    assert decision.tag == IntentTag.FORMAT
    assert decision.source == "carry"
    assert decision.rule_id == "sticky_lock"


def test_repeat_threshold_by_intent() -> None:
    base_warn, base_trip = 2, 3
    aggregate = repeat_threshold(IntentTag.AGGREGATE, base_warn=base_warn, base_trip=base_trip)
    entry = repeat_threshold(IntentTag.ENTRY, base_warn=base_warn, base_trip=base_trip)
    assert aggregate == (2, 3)
    assert entry[0] >= aggregate[0]
    assert entry[1] > entry[0]
