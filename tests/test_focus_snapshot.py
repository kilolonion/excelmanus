"""focus_window 方案 B 回归测试：预览快照 + enrich 管道对齐。"""

from __future__ import annotations

import json
from typing import Any

from excelmanus.window_perception.domain import Window
from excelmanus.window_perception.focus import FocusService
from excelmanus.window_perception.manager import WindowPerceptionManager
from excelmanus.window_perception.models import (
    CachedRange,
    DetailLevel,
    PerceptionBudget,
    WindowType,
)
from tests.window_factories import make_window


def _build_manager() -> WindowPerceptionManager:
    return WindowPerceptionManager(
        enabled=True,
        budget=PerceptionBudget(system_budget_tokens=3000),
    )


def _build_sheet_window(
    window_id: str,
    data_buffer: list[dict[str, Any]] | None = None,
    columns: list | None = None,
    total_rows: int = 50,
    total_cols: int = 3,
    viewport_range: str = "A1:C10",
    cached_ranges: list[CachedRange] | None = None,
) -> Window:
    buf = data_buffer or [{"A": i, "B": i * 10, "C": f"row{i}"} for i in range(1, 11)]
    cols = columns or [{"name": "A"}, {"name": "B"}, {"name": "C"}]
    return make_window(
        id=window_id,
        type=WindowType.SHEET,
        title=f"{window_id}.xlsx/Sheet1",
        file_path=f"/tmp/{window_id}.xlsx",
        sheet_name="Sheet1",
        viewport_range=viewport_range,
        data_buffer=buf,
        columns=cols,
        total_rows=total_rows,
        total_cols=total_cols,
        cached_ranges=cached_ranges or [
            CachedRange(
                range_ref=viewport_range,
                rows=buf,
                is_current_viewport=True,
                added_at_iteration=1,
            )
        ],
    )


# ── build_focus_snapshot 单元测试 ──


def test_build_focus_snapshot_returns_preview_and_columns() -> None:
    """build_focus_snapshot 应返回 preview/columns/total_rows 等字段。"""
    manager = _build_manager()
    window = _build_sheet_window("sheet_1")
    manager._windows[window.id] = window

    snapshot = manager.build_focus_snapshot("sheet_1")

    assert isinstance(snapshot, dict)
    assert "preview" in snapshot
    assert "columns" in snapshot
    assert snapshot["total_rows"] == 50
    assert snapshot["total_cols"] == 3
    assert snapshot["file_path"] == "/tmp/sheet_1.xlsx"
    assert snapshot["sheet_name"] == "Sheet1"
    assert len(snapshot["preview"]) == 10  # data_buffer 有 10 行


def test_build_focus_snapshot_caps_preview_rows() -> None:
    """preview 最多返回 default_rows 行（budget 默认 20）。"""
    manager = _build_manager()
    big_buf = [{"A": i} for i in range(100)]
    window = _build_sheet_window("sheet_1", data_buffer=big_buf, total_rows=100)
    manager._windows[window.id] = window

    snapshot = manager.build_focus_snapshot("sheet_1")

    assert len(snapshot["preview"]) <= 20


def test_build_focus_snapshot_invalid_window_returns_empty() -> None:
    manager = _build_manager()
    assert manager.build_focus_snapshot("nonexistent") == {}


# ── FocusService 返回快照测试 ──


def test_focus_service_cache_hit_returns_snapshot() -> None:
    """缓存命中时 focus_window 返回应包含 snapshot 字段。"""
    manager = _build_manager()
    window = _build_sheet_window("sheet_1")
    manager._windows[window.id] = window
    manager._active_window_id = window.id

    service = FocusService(manager=manager, refill_reader=None)
    result = service.focus_window(
        window_id="sheet_1",
        action="scroll",
        range_ref="A1:C10",
    )

    assert result["status"] == "ok"
    assert "snapshot" in result
    snap = result["snapshot"]
    assert isinstance(snap["preview"], list)
    assert len(snap["preview"]) > 0
    assert snap["total_rows"] == 50


def test_focus_service_restore_returns_snapshot() -> None:
    """restore 操作返回应包含 snapshot。"""
    manager = _build_manager()
    window = _build_sheet_window("sheet_1")
    window.detail_level = DetailLevel.ICON
    manager._windows[window.id] = window

    service = FocusService(manager=manager, refill_reader=None)
    result = service.focus_window(window_id="sheet_1", action="restore")

    assert result["status"] == "ok"
    assert "snapshot" in result


def test_focus_service_clear_filter_returns_snapshot() -> None:
    """clear_filter 操作返回应包含 snapshot。"""
    manager = _build_manager()
    window = _build_sheet_window("sheet_1")
    window.unfiltered_buffer = [{"A": i} for i in range(5)]
    window.filter_state = {"column": "A", "operator": "gt", "value": 3}
    window.data_buffer = [{"A": 4}, {"A": 5}]
    manager._windows[window.id] = window
    manager._active_window_id = window.id

    service = FocusService(manager=manager, refill_reader=None)
    result = service.focus_window(window_id="sheet_1", action="clear_filter")

    assert result["status"] == "ok"
    assert "snapshot" in result
    assert len(result["snapshot"]["preview"]) > 0


def test_focus_service_refill_returns_snapshot() -> None:
    """补读成功后返回应包含 snapshot。"""
    manager = _build_manager()
    window = _build_sheet_window(
        "sheet_1",
        cached_ranges=[],  # 空缓存，触发 needs_refill
    )
    manager._windows[window.id] = window
    manager._active_window_id = window.id

    refill_data = {
        "success": True,
        "result_text": json.dumps({
            "preview": [{"A": 20, "B": 200, "C": "row20"}],
            "columns": [{"name": "A"}, {"name": "B"}, {"name": "C"}],
            "total_rows": 50,
            "total_cols": 3,
        }),
        "tool_name": "read_excel",
        "arguments": {"file_path": "/tmp/sheet_1.xlsx", "sheet_name": "Sheet1"},
    }

    def mock_refill(**kwargs: Any) -> dict[str, Any]:
        return refill_data

    service = FocusService(manager=manager, refill_reader=mock_refill)
    result = service.focus_window(
        window_id="sheet_1",
        action="scroll",
        range_ref="A20:C30",
    )

    assert result["status"] == "ok"
    assert result.get("refilled") is True
    assert "snapshot" in result
    snap = result["snapshot"]
    assert isinstance(snap["preview"], list)


def test_focus_service_error_no_snapshot() -> None:
    """错误返回不应包含 snapshot。"""
    manager = _build_manager()
    service = FocusService(manager=manager, refill_reader=None)
    result = service.focus_window(window_id="nonexistent", action="scroll")

    assert result["status"] == "error"
    assert "snapshot" not in result


# ── enrich 管道对齐测试 ──


def test_update_from_tool_call_skips_reingest_for_focus_window() -> None:
    """focus_window 在 update_from_tool_call 中应走快速路径，不执行 _update_sheet_window。"""
    manager = _build_manager()
    window = _build_sheet_window("sheet_1")
    manager._windows[window.id] = window
    manager._active_window_id = window.id

    initial_seq = manager._operation_seq

    payload = manager.update_from_tool_call(
        tool_name="focus_window",
        arguments={"window_id": "sheet_1", "action": "scroll", "range": "A1:C10"},
        result_text=json.dumps({"status": "ok", "window_id": "sheet_1"}),
    )

    # 快速路径不应递增 _operation_seq
    assert manager._operation_seq == initial_seq
    # 应返回有效 payload
    assert payload is not None
    assert payload.get("window_type") == "sheet"
    assert "viewport" in payload


def test_update_from_tool_call_focus_window_refill_also_fast_path() -> None:
    """focus_window_refill 同样走快速路径。"""
    manager = _build_manager()
    window = _build_sheet_window("sheet_1")
    manager._windows[window.id] = window
    manager._active_window_id = window.id

    payload = manager.update_from_tool_call(
        tool_name="focus_window_refill",
        arguments={"window_id": "sheet_1"},
        result_text=json.dumps({"status": "ok", "window_id": "sheet_1"}),
    )

    assert payload is not None
    assert payload.get("window_type") == "sheet"


def test_enrich_tool_result_focus_window_produces_perception_block() -> None:
    """focus_window 结果经过 enrich_tool_result 后应附加 perception block。"""
    manager = _build_manager()
    window = _build_sheet_window("sheet_1")
    manager._windows[window.id] = window
    manager._active_window_id = window.id

    raw_result = json.dumps({
        "status": "ok",
        "action": "scroll",
        "window_id": "sheet_1",
        "range": "A1:C10",
        "snapshot": {"preview": [{"A": 1}], "columns": [{"name": "A"}]},
    })

    enriched = manager.enrich_tool_result(
        tool_name="focus_window",
        arguments={"window_id": "sheet_1", "action": "scroll"},
        result_text=raw_result,
        success=True,
        mode="enriched",
    )

    # 应附加 perception block
    assert "--- perception ---" in enriched
    assert "--- end ---" in enriched


def test_enrich_tool_result_focus_window_wurm_mode() -> None:
    """focus_window 在 WURM(anchored) 模式下也应正确处理。"""
    manager = _build_manager()
    window = _build_sheet_window("sheet_1")
    manager._windows[window.id] = window
    manager._active_window_id = window.id

    raw_result = json.dumps({
        "status": "ok",
        "action": "scroll",
        "window_id": "sheet_1",
    })

    enriched = manager.enrich_tool_result(
        tool_name="focus_window",
        arguments={"window_id": "sheet_1", "action": "scroll"},
        result_text=raw_result,
        success=True,
        mode="anchored",
    )

    # WURM 模式下应返回非空结果（确认文本或原始+perception）
    assert enriched is not None
    assert len(enriched) > 0
