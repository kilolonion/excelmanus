"""单元格写入走 edit_spreadsheet，不再经过 write_cells 第二扇门。"""

from __future__ import annotations

from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook

from excelmanus.engine_core.tool_result import ToolResult
from excelmanus.security import FileAccessGuard
from excelmanus.tools._guard_ctx import set_guard
from excelmanus.tools.intent_tools import edit_spreadsheet, init_guard
from excelmanus.workbook_commit import content_version_of_file, seed_seen_versions


@pytest.fixture(autouse=True)
def _bind_workspace(tmp_path: Path) -> None:
    workspace = str(tmp_path)
    set_guard(FileAccessGuard(workspace))
    init_guard(workspace)
    seed_seen_versions({})


def _make_sample(tmp_path: Path, name: str = "sample.xlsx") -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws["A1"] = "姓名"
    ws["B1"] = "年龄"
    ws["C1"] = "城市"
    ws["A2"] = "张三"
    ws["B2"] = 28
    ws["C2"] = "北京"
    ws["A3"] = "李四"
    ws["B3"] = 35
    ws["C3"] = "上海"
    fp = tmp_path / name
    wb.save(fp)
    wb.close()
    return fp


def _edit(path: Path, operations: list[dict]) -> ToolResult:
    filled = []
    for op in operations:
        item = dict(op)
        if item.get("kind") in {"write", "insert", "copy"} and "sheet" not in item and "sheet_name" not in item:
            if not str(item.get("start_cell") or "").count("!"):
                item["sheet"] = "Sheet1"
        filled.append(item)
    return edit_spreadsheet(
        file_path=str(path),
        operations=filled,
        expected_version=content_version_of_file(path),
    )


class TestWriteCells:
    def test_single_cell_value(self, tmp_path: Path) -> None:
        fp = _make_sample(tmp_path)
        result = _edit(fp, [{"kind": "write", "start_cell": "D1", "values": [["得分"]]}])
        assert result.success
        wb = load_workbook(fp)
        assert wb.active["D1"].value == "得分"
        assert wb.active["A1"].value == "姓名"
        assert wb.active["B2"].value == 28
        wb.close()

    def test_single_cell_formula(self, tmp_path: Path) -> None:
        fp = _make_sample(tmp_path)
        result = _edit(fp, [{"kind": "write", "start_cell": "D2", "values": [["=B2*2"]]}])
        assert result.success
        wb = load_workbook(fp)
        assert wb.active["D2"].value == "=B2*2"
        wb.close()

    def test_range_mode_batch_write(self, tmp_path: Path) -> None:
        fp = _make_sample(tmp_path)
        result = _edit(
            fp,
            [{"kind": "write", "start_cell": "A4", "values": [["王五", 42, "广州"], ["赵六", 29, "深圳"]]}],
        )
        assert result.success
        wb = load_workbook(fp)
        assert wb.active["A4"].value == "王五"
        assert wb.active["C5"].value == "深圳"
        wb.close()

    def test_range_mode_with_full_range(self, tmp_path: Path) -> None:
        fp = _make_sample(tmp_path)
        result = _edit(
            fp,
            [{"kind": "write", "start_cell": "E1", "values": [[100, 200], [300, 400]]}],
        )
        assert result.success
        wb = load_workbook(fp)
        assert wb.active["E1"].value == 100
        assert wb.active["F2"].value == 400
        wb.close()

    def test_specific_sheet(self, tmp_path: Path) -> None:
        fp = _make_sample(tmp_path)
        created = _edit(
            fp,
            [{"kind": "sheet", "action": "create", "new_name": "目标表"}],
        )
        assert created.success
        result = _edit(
            fp,
            [{"kind": "write", "sheet": "目标表", "start_cell": "A1", "values": [["测试"]]}],
        )
        assert result.success
        wb = load_workbook(fp)
        assert wb["目标表"]["A1"].value == "测试"
        assert wb["Sheet1"]["A1"].value == "姓名"
        wb.close()

    def test_numeric_string_preserved(self, tmp_path: Path) -> None:
        fp = _make_sample(tmp_path)
        result = _edit(fp, [{"kind": "write", "start_cell": "D2", "values": [["42.5"]]}])
        assert result.success
        wb = load_workbook(fp)
        assert wb.active["D2"].value == "42.5"
        wb.close()

    def test_write_requires_values(self, tmp_path: Path) -> None:
        fp = _make_sample(tmp_path)
        result = _edit(fp, [{"kind": "write", "start_cell": "A1"}])
        assert not result.success


class TestInsertRows:
    def test_insert_single_row(self, tmp_path: Path) -> None:
        fp = _make_sample(tmp_path)
        result = _edit(fp, [{"kind": "insert", "axis": "row", "at": 2, "count": 1}])
        assert result.success
        wb = load_workbook(fp)
        ws = wb.active
        assert ws["A1"].value == "姓名"
        assert ws["A2"].value is None
        assert ws["A3"].value == "张三"
        wb.close()

    def test_insert_multiple_rows(self, tmp_path: Path) -> None:
        fp = _make_sample(tmp_path)
        result = _edit(fp, [{"kind": "insert", "axis": "row", "at": 1, "count": 3}])
        assert result.success
        wb = load_workbook(fp)
        assert wb.active["A4"].value == "姓名"
        wb.close()

    def test_insert_row_invalid_params(self, tmp_path: Path) -> None:
        fp = _make_sample(tmp_path)
        result = _edit(fp, [{"kind": "insert", "axis": "row", "at": 0, "count": 1}])
        assert not result.success
        result = _edit(fp, [{"kind": "insert", "axis": "row", "at": 1, "count": 0}])
        assert not result.success


class TestInsertColumns:
    def test_insert_single_column_by_letter(self, tmp_path: Path) -> None:
        fp = _make_sample(tmp_path)
        result = _edit(fp, [{"kind": "insert", "axis": "column", "at": "B", "count": 1}])
        assert result.success
        wb = load_workbook(fp)
        ws = wb.active
        assert ws["A1"].value == "姓名"
        assert ws["B1"].value is None
        assert ws["C1"].value == "年龄"
        wb.close()

    def test_insert_column_by_number(self, tmp_path: Path) -> None:
        fp = _make_sample(tmp_path)
        result = _edit(fp, [{"kind": "insert", "axis": "column", "at": 1, "count": 2}])
        assert result.success
        wb = load_workbook(fp)
        assert wb.active["C1"].value == "姓名"
        wb.close()

    def test_insert_column_invalid_letter(self, tmp_path: Path) -> None:
        fp = _make_sample(tmp_path)
        result = _edit(fp, [{"kind": "insert", "axis": "column", "at": "1A", "count": 1}])
        assert not result.success
