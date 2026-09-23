"""Formatting previews retain their provenance when conversion changes layout."""
import io
from unittest.mock import Mock

from openpyxl import Workbook, load_workbook

from excelmanus import workbook_commit
from excelmanus.security import FileAccessGuard
from excelmanus.tools import intent_tools
from excelmanus.tools._guard_ctx import set_guard


def test_recalculation_can_change_print_settings_without_invalidating_commit(tmp_path, monkeypatch):
    set_guard(FileAccessGuard(str(tmp_path)))
    intent_tools.init_guard(str(tmp_path))
    path = tmp_path / "receipt.xlsx"
    wb = Workbook()
    wb.active.title = "Receipt"
    wb.active["A1"] = "=1+1"  # Commit invokes recalculation for formula workbooks.
    wb.save(path)
    wb.close()

    def recalculate(data, *, suffix):
        converted = load_workbook(io.BytesIO(data))
        try:
            ws = converted["Receipt"]
            assert ws.page_setup.orientation == "landscape"
            assert ws.page_setup.scale == 125
            # Simulate a converter normalizing the requested print layout.
            ws.page_setup.orientation = "portrait"
            ws.page_setup.scale = 100
            output = io.BytesIO()
            converted.save(output)
            return output.getvalue(), {"status": "recalculated", "engine": "test-converter", "errors": []}
        finally:
            converted.close()

    recalc = Mock(side_effect=recalculate)
    monkeypatch.setattr(workbook_commit, "recalculate_workbook_bytes", recalc)
    result = intent_tools.format_spreadsheet(
        file_path=str(path),
        expected_version=workbook_commit.content_version_of_file(path),
        operations=[{
            "kind": "print_layout", "sheet": "Receipt",
            "print_layout": {"orientation": "landscape", "scale": 125},
        }],
    )

    assert result.success, result.model_text
    recalc.assert_called_once()
    assert result.value["content_version"] == workbook_commit.content_version_of_file(path)
    assert result.value["formula_recalculation"]["status"] == "recalculated"
    preview = result.value["appearance"]
    assert preview["source"] == "mutation_preview"
    assert preview["phase"] == "before_serialization"
    assert "不代表最终提交文件的快照" in preview["note"]
    settings = preview["sheets"][0]["print_settings"]
    assert settings["orientation"] == "landscape" and settings["scale"] == 125
    assert "mutation_preview" in result.model_text
    committed = load_workbook(path)
    try:
        ws = committed["Receipt"]
        assert ws.page_setup.orientation == "portrait"
        assert ws.page_setup.scale == 100
        assert ws["A1"].value == "=1+1"
    finally:
        committed.close()
