from excelmanus.window_perception.domain import BaseWindow, ExplorerWindow, SheetWindow


def test_explorer_and_sheet_have_separate_typed_data() -> None:
    explorer = ExplorerWindow.new(id="explorer_1", title="resource", directory=".")
    sheet = SheetWindow.new(id="sheet_1", title="t", file_path="a.xlsx", sheet_name="Sheet1")

    assert isinstance(explorer, BaseWindow)
    assert isinstance(sheet, BaseWindow)
    assert explorer.kind == "explorer"
    assert sheet.kind == "sheet"
    assert hasattr(explorer.data, "directory")
    assert hasattr(sheet.data, "file_path")
