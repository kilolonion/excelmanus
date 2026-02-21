"""WURM ingest 语义测试：read-replace + write-through。"""

from __future__ import annotations

from excelmanus.window_perception.domain import Window
from excelmanus.window_perception.ingest import ingest_read_result, ingest_write_result
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


def test_read_replace_non_contiguous_range_replaces_cache() -> None:
    """任意读操作替换整个缓存，即使是非连续区域。"""
    window = _build_window()
    ingest_read_result(
        window,
        new_range="E1:G3",
        new_rows=[{"A": 10, "B": 11, "C": 12}],
        iteration=2,
    )
    # 单视口替换全部，不保留独立缓存块
    assert len(window.cached_ranges) == 1
    assert window.cached_ranges[0].range_ref == "E1:G3"
    assert len(window.data_buffer) == 1


def test_write_always_wipes_cache_regardless_of_target() -> None:
    """写操作始终清空缓存，不做内存补丁。"""
    window = _build_window()
    affected = ingest_write_result(
        window,
        target_range="A2:B2",
        result_json={"preview_after": [[400, 500]]},
        iteration=3,
    )
    assert affected == []
    assert window.data_buffer == []
    assert window.cached_ranges == []
    assert window.stale_hint is not None


def test_write_unmappable_target_also_wipes() -> None:
    """即使目标区域无法映射，写操作也会清空缓存。"""
    window = _build_window()
    affected = ingest_write_result(
        window,
        target_range="A100:B100",
        result_json={"preview_after": [[1, 2]]},
        iteration=3,
    )
    assert affected == []
    assert window.stale_hint is not None
    assert window.data_buffer == []
