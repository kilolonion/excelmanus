"""WorkbookSpec 校验与私有编译辅助。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from excelmanus.replica_spec import SpecValidationError, validate_workbook_spec


def _minimal_workbook_spec(**overrides: object) -> dict:
    spec: dict = {
        "name": "replica",
        "sheets": [{
            "name": "Sheet1",
            "dimensions": {"rows": 2, "cols": 2},
            "value_blocks": [{
                "start": "A1",
                "values": [["Name", "Age"], ["Alice", 30]],
            }],
            "formula_blocks": [],
            "styles": {
                "header": {"font": {"bold": True}},
            },
            "style_regions": [{"range": "A1:B1", "style_id": "header"}],
            "merged_ranges": [],
            "column_widths": [12, 8],
            "row_heights": {"1": 20},
        }],
        "uncertainties": [],
    }
    spec.update(overrides)
    return spec


class TestWorkbookSpecValidation:
    def test_missing_uncertainties_reports_path(self) -> None:
        data = _minimal_workbook_spec()
        del data["uncertainties"]
        with pytest.raises(SpecValidationError) as excinfo:
            validate_workbook_spec(data)
        paths = [e["path"] for e in excinfo.value.errors]
        assert "uncertainties" in paths
        payload = excinfo.value.to_payload()
        assert payload["status"] == "error"
        assert payload["error_code"] == "SPEC_VALIDATION_FAILED"
        assert any(e["path"] == "uncertainties" for e in payload["errors"])

    def test_empty_uncertainties_ok(self) -> None:
        spec = validate_workbook_spec(_minimal_workbook_spec())
        assert spec.uncertainties == []
        assert spec.sheets[0].value_blocks[0].start == "A1"

    def test_out_of_bounds_value_block(self) -> None:
        data = _minimal_workbook_spec()
        data["sheets"][0]["value_blocks"] = [{
            "start": "A1",
            "values": [["a", "b", "c"], ["d", "e", "f"]],
        }]
        with pytest.raises(SpecValidationError) as excinfo:
            validate_workbook_spec(data)
        err = excinfo.value.errors[0]
        assert err["path"] == "sheets.0.value_blocks.0"
        assert "超出" in err["message"]

    def test_out_of_bounds_formula_block(self) -> None:
        data = _minimal_workbook_spec()
        data["sheets"][0]["formula_blocks"] = [{
            "start": "A3",
            "formulas": [["=A1+A2"]],
        }]
        with pytest.raises(SpecValidationError) as excinfo:
            validate_workbook_spec(data)
        assert any("formula_blocks" in e["path"] for e in excinfo.value.errors)

    def test_bad_style_region_ref(self) -> None:
        data = _minimal_workbook_spec()
        data["sheets"][0]["style_regions"] = [
            {"range": "A1:B1", "style_id": "missing_style"},
        ]
        with pytest.raises(SpecValidationError) as excinfo:
            validate_workbook_spec(data)
        err = next(e for e in excinfo.value.errors if e["path"].endswith("style_id"))
        assert err["path"] == "sheets.0.style_regions.0.style_id"
        assert "missing_style" in err["message"]

    def test_does_not_silently_patch(self) -> None:
        data = _minimal_workbook_spec()
        del data["uncertainties"]
        with pytest.raises(SpecValidationError):
            validate_workbook_spec(data)


class TestCompileFromWorkbookSpec:
    def test_compile_writes_xlsx_from_blocks(self, tmp_path: Path) -> None:
        from openpyxl import load_workbook

        from excelmanus.replica_spec import compile_workbook_spec_to_bytes, validate_workbook_spec

        spec = validate_workbook_spec(_minimal_workbook_spec())
        data, summary = compile_workbook_spec_to_bytes(spec)
        xlsx_path = tmp_path / "out.xlsx"
        xlsx_path.write_bytes(data)
        assert xlsx_path.is_file()
        assert summary["cells_written"] >= 4
        wb = load_workbook(str(xlsx_path))
        ws = wb["Sheet1"]
        assert ws["A1"].value == "Name"
        assert ws["B2"].value == 30
        assert ws["A1"].font.bold is True


class TestValidateWorkbookSpec:
    def test_accepts_minimal_spec(self) -> None:
        workbook = validate_workbook_spec(_minimal_workbook_spec())
        assert workbook.uncertainties == []
        assert len(workbook.sheets) == 1

    def test_validation_errors_include_field_paths(self) -> None:
        data = _minimal_workbook_spec()
        del data["uncertainties"]
        with pytest.raises(SpecValidationError) as ei:
            validate_workbook_spec(data)
        payload = ei.value.to_payload()
        assert payload["error_code"] == "SPEC_VALIDATION_FAILED"
        assert any(e["path"] == "uncertainties" for e in payload["errors"])

    def test_image_tools_only_register_read_image(self) -> None:
        from excelmanus.tools.image_tools import get_tools

        names = {t.name for t in get_tools()}
        assert names == {"read_image"}
        assert "extract_table_spec" not in names
