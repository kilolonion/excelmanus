"""WURM ingest 模块测试。"""

from excelmanus.window_perception.domain import Window
from excelmanus.window_perception.ingest import (
    deduplicated_merge,
    extract_columns,
    extract_data_rows,
    ingest_filter_result,
    ingest_read_result,
    ingest_write_result,
    is_adjacent_or_overlapping,
    union_range,
)
from excelmanus.window_perception.models import CachedRange, ColumnDef, DetailLevel, WindowType
from tests.window_factories import make_window


def _build_window() -> Window:
    return make_window(
        id="W1",
        type=WindowType.SHEET,
        title="sheet",
        viewport_range="A1:C10",
        columns=[ColumnDef(name="A"), ColumnDef(name="B"), ColumnDef(name="C")],
        max_cached_rows=12,
        data_buffer=[
            {"A": 1, "B": 2, "C": 3},
            {"A": 4, "B": 5, "C": 6},
        ],
        cached_ranges=[
            CachedRange(
                range_ref="A1:C2",
                rows=[{"A": 1, "B": 2, "C": 3}, {"A": 4, "B": 5, "C": 6}],
                is_current_viewport=True,
                added_at_iteration=1,
            )
        ],
    )


def test_extract_data_rows_and_columns() -> None:
    payload = {
        "columns": ["日期", "产品", "金额"],
        "data": [{"日期": "2024-01-01", "产品": "A", "金额": 100}],
    }
    rows = extract_data_rows(payload, "read_excel")
    assert len(rows) == 1
    columns = extract_columns(payload, rows)
    assert [c.name for c in columns] == ["日期", "产品", "金额"]


def test_range_union_and_adjacency() -> None:
    assert is_adjacent_or_overlapping("A1:C10", "A11:C20")
    assert union_range("A1:C10", "A11:C20") == "A1:C20"


def test_deduplicated_merge() -> None:
    merged = deduplicated_merge(
        [{"A": 1}, {"A": 2}],
        [{"A": 2}, {"A": 3}],
    )
    # v2 语义：无几何/主键信息时不按“整行内容签名”去重。
    assert merged == [{"A": 1}, {"A": 2}, {"A": 2}, {"A": 3}]


def test_ingest_read_result_merges_and_limits_by_cached_range() -> None:
    window = _build_window()
    new_rows = [{"A": i, "B": i + 1, "C": i + 2} for i in range(10, 20)]
    affected = ingest_read_result(
        window,
        new_range="A3:C12",
        new_rows=new_rows,
        iteration=2,
    )
    assert window.detail_level == DetailLevel.FULL
    assert window.viewport_range == "A3:C12"
    assert affected


def test_ingest_write_result_clears_stale_when_patchable() -> None:
    window = _build_window()
    affected = ingest_write_result(
        window,
        target_range="A1:A1",
        result_json={"preview_after": [[999]]},
        iteration=3,
    )
    assert affected
    assert window.stale_hint is None
    assert window.data_buffer[0]["A"] == 999


def test_ingest_filter_result_snapshots_unfiltered_buffer() -> None:
    window = _build_window()
    affected = ingest_filter_result(
        window,
        filter_condition={"column": "A", "operator": "gt", "value": 2},
        filtered_rows=[{"A": 4, "B": 5, "C": 6}],
        iteration=4,
    )
    assert affected == [0]
    assert window.filter_state is not None
    assert window.unfiltered_buffer is not None
    assert len(window.data_buffer) == 1
