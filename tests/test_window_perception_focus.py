"""focus_window 服务测试。"""

from __future__ import annotations

import json
from typing import Any

from excelmanus.window_perception.domain import Window
from excelmanus.window_perception.focus import FocusService
from excelmanus.window_perception.manager import WindowPerceptionManager
from excelmanus.window_perception.models import (
    CachedRange,
    ColumnDef,
    DetailLevel,
    IntentTag,
    PerceptionBudget,
    WindowType,
)
from tests.window_factories import make_window


def _build_manager_and_window() -> tuple[WindowPerceptionManager, Window]:
    manager = WindowPerceptionManager(
        enabled=True,
        budget=PerceptionBudget(),
    )
    window = make_window(
        id="sheet_1",
        type=WindowType.SHEET,
        title="sales.xlsx/Q1",
        file_path="sales.xlsx",
        sheet_name="Q1",
        viewport_range="A1:C3",
        columns=[ColumnDef(name="A"), ColumnDef(name="B"), ColumnDef(name="C")],
        data_buffer=[
            {"A": 1, "B": 2, "C": 3},
            {"A": 4, "B": 5, "C": 6},
            {"A": 7, "B": 8, "C": 9},
        ],
        cached_ranges=[
            CachedRange(
                range_ref="A1:C3",
                rows=[
                    {"A": 1, "B": 2, "C": 3},
                    {"A": 4, "B": 5, "C": 6},
                    {"A": 7, "B": 8, "C": 9},
                ],
                is_current_viewport=True,
                added_at_iteration=1,
            )
        ],
        detail_level=DetailLevel.FULL,
    )
    manager._windows[window.id] = window
    manager._sheet_index[(window.file_path or "", window.sheet_name or "")] = window.id
    manager._active_window_id = window.id
    return manager, window


def test_focus_scroll_cache_hit_without_refill() -> None:
    manager, window = _build_manager_and_window()
    called = {"count": 0}

    def _refill(**_kwargs: Any) -> dict[str, Any]:
        called["count"] += 1
        return {"success": False, "error": "should not be called"}

    service = FocusService(manager=manager, refill_reader=_refill)
    result = service.focus_window(window_id=window.id, action="scroll", range_ref="A2:C3")

    assert result["status"] == "ok"
    assert result["cache_hit"] is True
    assert called["count"] == 0
    assert manager._windows[window.id].viewport_range == "A2:C3"


def test_focus_scroll_cache_miss_triggers_refill() -> None:
    manager, window = _build_manager_and_window()
    called: list[str] = []

    def _refill(*, file_path: str, sheet_name: str, range_ref: str) -> dict[str, Any]:
        called.append(f"{file_path}|{sheet_name}|{range_ref}")
        return {
            "success": True,
            "tool_name": "read_excel",
            "arguments": {"file_path": file_path, "sheet_name": sheet_name, "range": range_ref},
            "result_text": json.dumps(
                {
                    "columns": ["A", "B", "C"],
                    "data": [{"A": 20, "B": 21, "C": 22}],
                },
                ensure_ascii=False,
            ),
        }

    service = FocusService(manager=manager, refill_reader=_refill)
    result = service.focus_window(window_id=window.id, action="scroll", range_ref="A20:C20")

    assert result["status"] == "ok"
    assert result["refilled"] is True
    assert called == ["sales.xlsx|Q1|A20:C20"]
    assert manager._windows[window.id].viewport_range == "A20:C20"
    assert any(row.get("A") == 20 for row in manager._windows[window.id].data_buffer)


def test_focus_clear_filter_restores_unfiltered_buffer() -> None:
    manager, window = _build_manager_and_window()
    window.unfiltered_buffer = [
        {"A": 1, "B": 2, "C": 3},
        {"A": 4, "B": 5, "C": 6},
    ]
    window.filter_state = {"column": "A", "operator": "gt", "value": 3}
    window.data_buffer = [{"A": 4, "B": 5, "C": 6}]

    service = FocusService(manager=manager, refill_reader=None)
    result = service.focus_window(window_id=window.id, action="clear_filter")

    assert result["status"] == "ok"
    assert result["restored"] is True
    assert window.filter_state is None
    assert window.unfiltered_buffer is None
    assert len(window.data_buffer) == 2
    assert window.intent_tag == IntentTag.VALIDATE


def test_focus_expand_triggers_refill_with_expanded_range() -> None:
    manager, window = _build_manager_and_window()
    called: list[str] = []

    def _refill(*, file_path: str, sheet_name: str, range_ref: str) -> dict[str, Any]:
        called.append(range_ref)
        return {
            "success": True,
            "tool_name": "read_excel",
            "arguments": {"file_path": file_path, "sheet_name": sheet_name, "range": range_ref},
            "result_text": json.dumps({"columns": ["A", "B", "C"], "data": []}, ensure_ascii=False),
        }

    service = FocusService(manager=manager, refill_reader=_refill)
    result = service.focus_window(window_id=window.id, action="expand", rows=5)

    assert result["status"] == "ok"
    assert called == ["A1:C8"]
    assert manager._windows[window.id].viewport_range == "A1:C8"


def test_focus_restore_wakes_dormant_window() -> None:
    manager, window = _build_manager_and_window()
    window.dormant = True
    window.detail_level = DetailLevel.NONE

    service = FocusService(manager=manager, refill_reader=None)
    result = service.focus_window(window_id=window.id, action="restore")

    assert result["status"] == "ok"
    assert window.dormant is False
    assert window.detail_level == DetailLevel.FULL


def test_manager_routes_tool_result_via_classify_locate_apply(monkeypatch) -> None:
    manager = WindowPerceptionManager(
        enabled=True,
        budget=PerceptionBudget(),
    )
    payload = json.dumps(
        {
            "file": "sales.xlsx",
            "sheet": "Q1",
            "shape": {"rows": 20, "columns": 5},
            "columns": ["日期", "产品", "数量", "单价", "金额"],
            "preview": [{"日期": "2024-01-01", "产品": "A", "数量": 1, "单价": 100, "金额": 100}],
        },
        ensure_ascii=False,
    )
    arguments = {"file_path": "sales.xlsx", "sheet_name": "Q1", "range": "A1:E10"}
    calls: list[str] = []

    original_classify = manager._classify_tool
    original_locate = manager._locate_window_by_identity
    original_apply = manager._apply_delta_pipeline

    def _spy_classify(tool_name: str):
        calls.append("classify")
        return original_classify(tool_name)

    def _spy_locate(*, window_type, arguments, result_json):
        calls.append("locate")
        return original_locate(window_type=window_type, arguments=arguments, result_json=result_json)

    def _spy_apply(*, window, canonical_tool_name, arguments, result_json):
        calls.append("apply")
        return original_apply(
            window=window,
            canonical_tool_name=canonical_tool_name,
            arguments=arguments,
            result_json=result_json,
        )

    monkeypatch.setattr(manager, "_classify_tool", _spy_classify)
    monkeypatch.setattr(manager, "_locate_window_by_identity", _spy_locate)
    monkeypatch.setattr(manager, "_apply_delta_pipeline", _spy_apply)

    _ = manager.enrich_tool_result(
        tool_name="read_excel",
        arguments=arguments,
        result_text=payload,
        success=True,
        mode="anchored",
        model_id="gpt-5.3",
    )

    assert "classify" in calls
    assert "locate" in calls
    assert "apply" in calls
    assert calls.index("classify") < calls.index("locate") < calls.index("apply")
