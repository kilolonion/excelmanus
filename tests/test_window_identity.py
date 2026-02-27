import pytest

from excelmanus.window_perception.identity import ExplorerIdentity, SheetIdentity
from excelmanus.window_perception.locator import (
    WINDOW_IDENTITY_CONFLICT,
    WINDOW_KIND_CONFLICT,
    LocatorReject,
    WindowLocator,
)


def test_locator_matches_by_sheet_identity_only() -> None:
    locator = WindowLocator()
    sid = SheetIdentity(file_path_norm="/tmp/a.xlsx", sheet_name_norm="sheet1")
    locator.register("sheet_1", sid)

    assert locator.find(sid) == "sheet_1"
    assert isinstance(ExplorerIdentity(directory_norm="/tmp"), ExplorerIdentity)


def test_locator_rejects_kind_conflict() -> None:
    locator = WindowLocator()
    sid = SheetIdentity(file_path_norm="/tmp/a.xlsx", sheet_name_norm="sheet1")

    with pytest.raises(LocatorReject) as exc:
        locator.find(sid, expected_kind="explorer")

    assert exc.value.code == WINDOW_KIND_CONFLICT


def test_locator_rejects_identity_conflict() -> None:
    locator = WindowLocator()
    sid = SheetIdentity(file_path_norm="/tmp/a.xlsx", sheet_name_norm="sheet1")
    locator.register("sheet_1", sid)

    with pytest.raises(LocatorReject) as exc:
        locator.register("sheet_2", sid)

    assert exc.value.code == WINDOW_IDENTITY_CONFLICT
