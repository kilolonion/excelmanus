"""WURM v2 ingest 语义测试。"""

from __future__ import annotations

from excelmanus.window_perception.domain import Window
from excelmanus.window_perception.ingest import deduplicated_merge, ingest_read_result, ingest_write_result
from excelmanus.window_perception.models import CachedRange, ColumnDef, WindowType
from tests.window_factories import make_window


def _build_window() -> Window:
    return make_window(
        id="sheet_1",
        type=WindowType.SHEET,
        title="sales.xlsx/Q1",
        viewport_range="A1:C3",
        columns=[ColumnDef(name="A"), ColumnDef(name="B"), ColumnDef(name="C")],
        schema=[ColumnDef(name="A"), ColumnDef(name="B"), ColumnDef(name="C")],
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
    )


def test_merge_keeps_same_value_rows_when_positions_different() -> None:
    merged = deduplicated_merge(
        [{"A": 100}],
        [{"A": 100}],
        existing_range="A1:A1",
        incoming_range="A2:A2",
    )
    assert merged == [{"A": 100}, {"A": 100}]


def test_merge_overlapping_range_uses_row_position_override() -> None:
    merged = deduplicated_merge(
        [{"A": 1}, {"A": 2}],
        [{"A": 20}, {"A": 30}],
        existing_range="A1:A2",
        incoming_range="A2:A3",
    )
    assert merged == [{"A": 1}, {"A": 20}, {"A": 30}]


def test_ingest_read_non_contiguous_range_keeps_separate_cache_blocks() -> None:
    window = _build_window()
    ingest_read_result(
        window,
        new_range="E1:G3",
        new_rows=[{"A": 10, "B": 11, "C": 12}],
        iteration=2,
    )
    assert len(window.cached_ranges) == 2
    assert {item.range_ref for item in window.cached_ranges} == {"A1:C3", "E1:G3"}


def test_ingest_write_patches_buffer_when_target_mappable() -> None:
    window = _build_window()
    affected = ingest_write_result(
        window,
        target_range="A2:B2",
        result_json={"preview_after": [[400, 500]]},
        iteration=3,
    )
    assert affected
    assert window.stale_hint is None
    assert window.data_buffer[1]["A"] == 400
    assert window.data_buffer[1]["B"] == 500


def test_ingest_write_sets_stale_when_target_not_mappable() -> None:
    window = _build_window()
    affected = ingest_write_result(
        window,
        target_range="A100:B100",
        result_json={"preview_after": [[1, 2]]},
        iteration=3,
    )
    assert affected == []
    assert window.stale_hint is not None
