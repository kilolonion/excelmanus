"""WURM v2 确认协议测试。"""

from __future__ import annotations

from excelmanus.window_perception.confirmation import (
    build_confirmation_record,
    parse_confirmation,
    serialize_confirmation,
)
from excelmanus.window_perception.models import ChangeRecord, IntentTag, WindowState, WindowType


def _build_window() -> WindowState:
    window = WindowState(
        id="sheet_1",
        type=WindowType.SHEET,
        title="sales.xlsx/Q1",
        file_path="sales.xlsx",
        sheet_name="Q1",
        viewport_range="A1:E10",
        total_rows=20,
        total_cols=5,
        intent_tag=IntentTag.AGGREGATE,
    )
    window.change_log.append(
        ChangeRecord(
            operation="read",
            tool_summary="read_excel(A1:E10)",
            affected_range="A1:E10",
            change_type="added",
            iteration=1,
            affected_row_indices=[0, 1],
        )
    )
    return window


def test_anchored_confirmation_round_trip() -> None:
    window = _build_window()
    record = build_confirmation_record(
        window=window,
        tool_name="read_excel",
        repeat_warning=True,
    )
    text = serialize_confirmation(record, mode="anchored")
    parsed = parse_confirmation(text)
    assert parsed is not None
    assert parsed.window_label == record.window_label
    assert parsed.operation == record.operation
    assert parsed.range_ref == record.range_ref
    assert parsed.rows == record.rows
    assert parsed.cols == record.cols
    assert parsed.change_summary == record.change_summary
    assert parsed.intent == record.intent
    assert "intent[aggregate]" in parsed.hint


def test_unified_confirmation_round_trip() -> None:
    window = _build_window()
    record = build_confirmation_record(
        window=window,
        tool_name="read_excel",
        repeat_warning=False,
    )
    text = serialize_confirmation(record, mode="unified")
    parsed = parse_confirmation(text)
    assert parsed is not None
    assert parsed.window_label == record.window_label
    assert parsed.operation == record.operation
    assert parsed.range_ref == record.range_ref
    assert parsed.rows == record.rows
    assert parsed.cols == record.cols
    assert parsed.change_summary == record.change_summary
    assert parsed.intent == record.intent
    assert parsed.hint == ""
