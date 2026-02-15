import pytest

from excelmanus.window_perception.apply import DeltaReject, apply_delta
from excelmanus.window_perception.delta import ExplorerDelta, SheetReadDelta
from excelmanus.window_perception.domain import ExplorerWindow


def test_apply_delta_rejects_kind_mismatch() -> None:
    window = ExplorerWindow.new(id="explorer_1", title="resource", directory=".")
    delta = SheetReadDelta(range_ref="A1:E10", rows=20, cols=5, change_summary="added@A1:E10")

    with pytest.raises(DeltaReject):
        apply_delta(window, delta)

    # Keep import coverage for both delta contracts in this first task.
    assert ExplorerDelta(directory="/tmp").kind == "explorer"
