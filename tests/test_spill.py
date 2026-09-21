"""D1：spill 外置与写后语义校验。"""

from __future__ import annotations

import json
import re
from pathlib import Path

import openpyxl
from openpyxl.utils import get_column_letter

from excelmanus.engine_core.spill import (
    DEFAULT_SPILL_BYTE_THRESHOLD,
    DEFAULT_SPILL_CHAR_THRESHOLD,
    DEFAULT_SPILL_TOKEN_THRESHOLD,
    MAX_WRITE_VERIFY_ENTRIES,
    SpillStore,
    apply_spill,
    extract_spill_locator,
    is_spill_locator,
    project_for_wire,
    retrieve_spill,
    retrieve_spill_result,
    should_spill,
    verify_write,
)
from excelmanus.engine_core.tool_result import ToolResult

_HOST_DRIVE = re.compile(r"^[A-Za-z]:\\")
_HOST_USERS = "/Users/"
_HOST_HOME = "/home/"


def _assert_no_host_path(handle: str) -> None:
    assert not _HOST_DRIVE.search(handle)
    assert _HOST_USERS not in handle
    assert _HOST_HOME not in handle
    assert "\\" not in handle


def _book(path: Path, cells: dict[str, object], *, sheet: str = "Sheet1") -> Path:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet
    for addr, value in cells.items():
        ws[addr] = value
    wb.save(path)
    wb.close()
    return path


class TestSpillThresholds:
    def test_small_result_not_spilled_at_boundary(self, tmp_path: Path) -> None:
        store = SpillStore(tmp_path)
        text = "x" * 10
        assert should_spill(text, char_threshold=10, byte_threshold=10_000, token_threshold=10_000) is False
        proj = project_for_wire(
            text,
            store=store,
            char_threshold=10,
            byte_threshold=10_000,
            token_threshold=10_000,
        )
        assert proj.spilled is False
        assert proj.locator is None
        assert proj.model_text == text
        assert list(tmp_path.joinpath(".excelmanus", "spill").glob("*")) == []

    def test_just_over_char_threshold_spills_with_opaque_handle(self, tmp_path: Path) -> None:
        store = SpillStore(tmp_path)
        text = "y" * 11
        assert should_spill(text, char_threshold=10, byte_threshold=10_000, token_threshold=10_000) is True
        proj = project_for_wire(
            text,
            store=store,
            char_threshold=10,
            byte_threshold=10_000,
            token_threshold=10_000,
        )
        assert proj.spilled is True
        assert proj.locator is not None
        assert is_spill_locator(proj.locator)
        _assert_no_host_path(str(proj.locator))
        _assert_no_host_path(proj.model_text)
        payload = json.loads(proj.model_text)
        assert payload["spilled"] is True
        assert payload["spill"] == proj.locator
        assert "preview" in payload
        assert str(tmp_path) not in proj.model_text
        # 磁盘文件名只有 hex
        files = list(store.directory.iterdir())
        assert len(files) == 1
        assert re.fullmatch(r"[0-9a-f]{64}", files[0].name)
        _assert_no_host_path(files[0].name)


class TestSpillRetrieve:
    def test_retrieve_matches_original(self, tmp_path: Path) -> None:
        store = SpillStore(tmp_path)
        original = "hello-spill\n第二行 " + ("z" * 50)
        locator = store.put(original)
        _assert_no_host_path(str(locator))
        assert retrieve_spill(locator, workspace_root=tmp_path) == original
        assert store.get(locator) == original

    def test_retrieve_tool_result_preserves_text(self, tmp_path: Path) -> None:
        store = SpillStore(tmp_path)
        original = json.dumps({"status": "success", "rows": list(range(8))}, ensure_ascii=False)
        locator = store.put(original)
        result = retrieve_spill_result(locator, workspace_root=tmp_path)
        assert result.success is True
        assert result.model_text == original
        assert result.coverage and result.coverage.get("spill_retrieve") is True

    def test_apply_spill_keeps_value_contract(self, tmp_path: Path) -> None:
        store = SpillStore(tmp_path)
        original = "w" * 200
        result = ToolResult(
            success=True,
            model_text=original,
            value={"status": "success", "file_path": "book.xlsx", "applied": ["A1"]},
        )
        spilled = apply_spill(
            result,
            store=store,
            char_threshold=50,
            byte_threshold=10_000,
            token_threshold=10_000,
        )
        assert spilled.model_text != original
        assert "spill:" in spilled.model_text
        assert spilled.value["status"] == "success"
        assert spilled.value["file_path"] == "book.xlsx"
        assert spilled.value["applied"] == ["A1"]
        assert spilled.value["meta"]["spill"]["locator"]
        locator = spilled.value["meta"]["spill"]["locator"]
        _assert_no_host_path(str(locator))
        assert store.get(locator) == original

    def test_extract_locator_from_file_path(self) -> None:
        locator = "spill:" + "ab" * 32
        assert is_spill_locator(locator)
        assert extract_spill_locator({"file_path": locator, "sheet": "Sheet1"}) == locator
        assert extract_spill_locator({"file_path": "book.xlsx"}) is None


class TestWriteVerification:
    def test_value_change(self, tmp_path: Path) -> None:
        path = _book(tmp_path / "values.xlsx", {"A1": "hello", "B1": 3})
        payload = verify_write(
            "edit_spreadsheet",
            {
                "file_path": str(path),
                "operations": [{
                    "kind": "write",
                    "sheet": "Sheet1",
                    "start_cell": "A1",
                    "values": [["hello", 3]],
                }],
            },
            workspace_root=str(tmp_path),
        )
        assert payload["status"] == "success"
        cells = {item["cell"]: item["value"] for item in payload["value_changes"]}
        assert cells["A1"] == "hello"
        assert cells["B1"] == 3
        assert payload["formula_changes"] == []

    def test_formula_change(self, tmp_path: Path) -> None:
        path = _book(tmp_path / "formula.xlsx", {"A1": "=B1+1", "B1": 4})
        payload = verify_write(
            "edit_spreadsheet",
            {
                "file_path": str(path),
                "operations": [{
                    "kind": "write",
                    "sheet": "Sheet1",
                    "start_cell": "A1",
                    "values": [["=B1+1"]],
                }],
            },
            workspace_root=str(tmp_path),
        )
        assert payload["status"] == "success"
        assert payload["formula_changes"]
        assert payload["formula_changes"][0]["cell"] == "A1"
        assert payload["formula_changes"][0]["formula"] == "=B1+1"
        assert payload["value_changes"] == []

    def test_partial_failure_uses_c1_shape(self, tmp_path: Path) -> None:
        path = _book(tmp_path / "mismatch.xlsx", {"A1": "on-disk"})
        payload = verify_write(
            "edit_spreadsheet",
            {
                "file_path": str(path),
                "operations": [{
                    "kind": "write",
                    "sheet": "Sheet1",
                    "start_cell": "A1",
                    "values": [["intended"]],
                }],
            },
            workspace_root=str(tmp_path),
        )
        assert payload["status"] == "error"
        assert payload["error_code"] == "RESULT_UNCERTAIN"
        assert payload["failure_class"]
        assert payload["remediation"]
        assert payload["message"]
        assert payload["mismatches"]
        assert payload["mismatches"][0]["expected"] == "intended"
        assert payload["mismatches"][0]["actual"] == "on-disk"

    def test_iso_date_string_matches_readback_datetime(self, tmp_path: Path) -> None:
        """SDK 边界把 date 意图序列化为 'YYYY-MM-DD'，读回是 datetime(...,0:00)——不得误报。"""
        import datetime as dt

        path = _book(tmp_path / "dates.xlsx", {"A1": dt.date(2024, 3, 1), "A2": dt.date(2023, 12, 15)})
        payload = verify_write(
            "edit_spreadsheet",
            {
                "file_path": str(path),
                "operations": [{
                    "kind": "write",
                    "sheet": "Sheet1",
                    "start_cell": "A1",
                    "values": [["2024-03-01"], ["2023-12-15"]],
                }],
            },
            workspace_root=str(tmp_path),
        )
        assert payload["status"] == "success"
        assert not payload.get("mismatches")

    def test_date_object_matches_readback_datetime(self, tmp_path: Path) -> None:
        import datetime as dt

        path = _book(tmp_path / "dates2.xlsx", {"A1": dt.datetime(2024, 3, 1, 0, 0)})
        payload = verify_write(
            "edit_spreadsheet",
            {
                "file_path": str(path),
                "operations": [{
                    "kind": "write",
                    "sheet": "Sheet1",
                    "start_cell": "A1",
                    "values": [[dt.date(2024, 3, 1)]],
                }],
            },
            workspace_root=str(tmp_path),
        )
        assert payload["status"] == "success"

    def test_wrong_date_still_flagged(self, tmp_path: Path) -> None:
        import datetime as dt

        path = _book(tmp_path / "wrong.xlsx", {"A1": dt.date(2024, 3, 2)})
        payload = verify_write(
            "edit_spreadsheet",
            {
                "file_path": str(path),
                "operations": [{
                    "kind": "write",
                    "sheet": "Sheet1",
                    "start_cell": "A1",
                    "values": [["2024-03-01"]],
                }],
            },
            workspace_root=str(tmp_path),
        )
        assert payload["status"] == "error"
        assert payload["mismatches"]

    def test_time_component_loss_still_flagged(self, tmp_path: Path) -> None:
        """意图带时间而单元格只剩日期——是真实丢失，不能被日期等价吞掉。"""
        import datetime as dt

        path = _book(tmp_path / "time.xlsx", {"A1": dt.datetime(2024, 3, 1, 0, 0)})
        payload = verify_write(
            "edit_spreadsheet",
            {
                "file_path": str(path),
                "operations": [{
                    "kind": "write",
                    "sheet": "Sheet1",
                    "start_cell": "A1",
                    "values": [["2024-03-01 10:30:00"]],
                }],
            },
            workspace_root=str(tmp_path),
        )
        assert payload["status"] == "error"
        assert payload["mismatches"]

    def test_entry_cap(self, tmp_path: Path) -> None:
        values = list(range(MAX_WRITE_VERIFY_ENTRIES + 5))
        cells = {f"{get_column_letter(i + 1)}1": values[i] for i in range(len(values))}
        path = _book(tmp_path / "cap.xlsx", cells)
        payload = verify_write(
            "edit_spreadsheet",
            {
                "file_path": str(path),
                "operations": [{
                    "kind": "write",
                    "sheet": "Sheet1",
                    "start_cell": "A1",
                    "values": [values],
                }],
            },
            workspace_root=str(tmp_path),
            entry_limit=MAX_WRITE_VERIFY_ENTRIES,
        )
        assert payload["status"] == "success"
        listed = len(payload["value_changes"]) + len(payload["formula_changes"])
        assert listed <= MAX_WRITE_VERIFY_ENTRIES
        assert payload["total_changes"] == len(values)
        assert payload["truncated"] is True
        assert payload["shown"] <= MAX_WRITE_VERIFY_ENTRIES


class TestStyleVerification:
    """A1.1：format_spreadsheet 的样式/结构回读。"""

    def _styled_book(self, path: Path) -> Path:
        from openpyxl.styles import Font, PatternFill

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Sheet1"
        for row in ws["A1:B2"]:
            for cell in row:
                cell.fill = PatternFill(start_color="FFFF0000", end_color="FFFF0000", fill_type="solid")
                cell.font = Font(bold=True, size=14)
                cell.number_format = "0.00"
        wb.save(path)
        wb.close()
        return path

    def test_style_all_match(self, tmp_path: Path) -> None:
        path = self._styled_book(tmp_path / "style.xlsx")
        payload = verify_write(
            "format_spreadsheet",
            {
                "file_path": str(path),
                "operations": [{
                    "kind": "format",
                    "sheet": "Sheet1",
                    "range": "A1:B2",
                    "fill": {"color": "FF0000"},
                    "font": {"bold": True, "size": 14},
                    "number_format": "0.00",
                }],
            },
            workspace_root=str(tmp_path),
        )
        assert payload["status"] == "success"
        assert payload["verification_kind"] == "style"
        assert payload["style_changes"]
        assert payload["mismatch_count"] == 0
        assert payload["total_changes"] >= 4

    def test_fill_color_mismatch(self, tmp_path: Path) -> None:
        path = self._styled_book(tmp_path / "style_bad.xlsx")
        payload = verify_write(
            "format_spreadsheet",
            {
                "file_path": str(path),
                "operations": [{
                    "kind": "format",
                    "sheet": "Sheet1",
                    "range": "A1:B2",
                    "fill": {"color": "0000FF"},
                }],
            },
            workspace_root=str(tmp_path),
        )
        assert payload["status"] == "error"
        assert payload["error_code"] == "RESULT_UNCERTAIN"
        assert payload["style_mismatches"]
        assert payload["style_mismatches"][0]["prop"] == "fill"

    def test_merge_and_conditional_format_verified(self, tmp_path: Path) -> None:
        from openpyxl.formatting.rule import CellIsRule

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Sheet1"
        ws["A1"] = "x"
        ws.merge_cells("A1:B1")
        ws.conditional_formatting.add(
            "A1:A5", CellIsRule(operator="greaterThan", formula=["0"])
        )
        path = tmp_path / "struct.xlsx"
        wb.save(path)
        wb.close()
        payload = verify_write(
            "format_spreadsheet",
            {
                "file_path": str(path),
                "operations": [
                    {"kind": "merge", "sheet": "Sheet1", "range": "A1:B1"},
                    {"kind": "conditional_format", "sheet": "Sheet1", "range": "A1:A5"},
                ],
            },
            workspace_root=str(tmp_path),
        )
        assert payload["status"] == "success"
        by_kind = {item["kind"]: item for item in payload["structure_changes"]}
        assert by_kind["merge"]["verified"] is True
        assert by_kind["conditional_format"]["verified"] is True
        assert by_kind["conditional_format"]["rule_count"] >= 1

    def test_formula_overwritten_count(self, tmp_path: Path) -> None:
        path = _book(tmp_path / "overwritten.xlsx", {"A1": 42})
        payload = verify_write(
            "edit_spreadsheet",
            {
                "file_path": str(path),
                "operations": [{
                    "kind": "write",
                    "sheet": "Sheet1",
                    "start_cell": "A1",
                    "values": [["=B1+1"]],
                }],
            },
            workspace_root=str(tmp_path),
        )
        assert payload["status"] == "error"
        assert payload["formula_intended_count"] == 1
        assert payload["formula_verified_count"] == 0
        assert payload["formula_overwritten_count"] == 1


def test_default_thresholds_documented() -> None:
    assert DEFAULT_SPILL_CHAR_THRESHOLD == 8000
    assert DEFAULT_SPILL_BYTE_THRESHOLD == 24_000
    assert DEFAULT_SPILL_TOKEN_THRESHOLD == 2000
    assert DEFAULT_SPILL_CHAR_THRESHOLD < 12000
