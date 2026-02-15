from dataclasses import FrozenInstanceError

import pytest

from excelmanus.window_perception.models import IntentTag, WindowState, WindowType
from excelmanus.window_perception.projection_service import project_confirmation, project_notice, project_tool_payload


def test_notice_projection_is_read_only_and_contains_identity() -> None:
    window = WindowState(
        id="sheet_1",
        type=WindowType.SHEET,
        title="sales.xlsx/Q1",
        file_path="sales.xlsx",
        sheet_name="Q1",
        viewport_range="A1:C3",
        total_rows=3,
        total_cols=3,
        intent_tag=IntentTag.AGGREGATE,
    )

    notice = project_notice(window, ctx={"identity": "sales.xlsx#Q1"})

    assert notice.window_id == "sheet_1"
    assert notice.identity == "sales.xlsx#Q1"
    assert notice.intent == "aggregate"

    with pytest.raises(FrozenInstanceError):
        notice.identity = "x"  # type: ignore[misc]


def test_projection_identity_intent_consistency_across_outputs() -> None:
    window = WindowState(
        id="sheet_1",
        type=WindowType.SHEET,
        title="sales.xlsx/Q1",
        file_path="sales.xlsx",
        sheet_name="Q1",
        viewport_range="A1:C3",
        total_rows=3,
        total_cols=3,
        intent_tag=IntentTag.AGGREGATE,
    )

    notice = project_notice(window)
    payload = project_tool_payload(window)
    confirmation = project_confirmation(window, tool_name="read_excel")

    assert payload is not None
    assert notice.identity == payload.identity
    assert notice.intent == payload.intent == confirmation.intent
