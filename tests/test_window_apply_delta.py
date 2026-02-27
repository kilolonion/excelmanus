import pytest

from excelmanus.window_perception.apply import DeltaReject, apply_delta
from excelmanus.window_perception.delta import ExplorerDelta, SheetReadDelta, SheetStyleDelta
from excelmanus.window_perception.domain import ExplorerWindow, SheetWindow


def test_apply_delta_rejects_kind_mismatch() -> None:
    window = ExplorerWindow.new(id="explorer_1", title="resource", directory=".")
    delta = SheetReadDelta(range_ref="A1:E10", rows=20, cols=5, change_summary="added@A1:E10")

    with pytest.raises(DeltaReject):
        apply_delta(window, delta)

    # 在本任务中保留两种 delta 契约的导入覆盖。
    assert ExplorerDelta(directory="/tmp").kind == "explorer"


def test_apply_delta_updates_sheet_style_fields() -> None:
    window = SheetWindow.new(
        id="sheet_1",
        title="s",
        file_path="a.xlsx",
        sheet_name="Sheet1",
    )
    delta = SheetStyleDelta(
        style_summary="bold+fill",
        column_widths={"A": 12.0},
        row_heights={"1": 24.0},
        merged_ranges=["A1:B1"],
        conditional_effects=["A1:A10 color-scale"],
    )

    apply_delta(window, delta)

    assert window.style_summary == "bold+fill"
    assert window.column_widths == {"A": 12.0}
    assert window.row_heights == {"1": 24.0}
    assert window.merged_ranges == ["A1:B1"]
    assert window.conditional_effects == ["A1:A10 color-scale"]
