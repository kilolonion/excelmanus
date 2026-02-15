from excelmanus.window_perception.identity import ExplorerIdentity, SheetIdentity
from excelmanus.window_perception.locator import WindowLocator


def test_locator_matches_by_sheet_identity_only() -> None:
    locator = WindowLocator()
    sid = SheetIdentity(file_path_norm="/tmp/a.xlsx", sheet_name_norm="sheet1")
    locator.register("sheet_1", sid)

    assert locator.find(sid) == "sheet_1"
    assert isinstance(ExplorerIdentity(directory_norm="/tmp"), ExplorerIdentity)
