"""图表写入走 manage_spreadsheet_objects，不再把 create_excel_chart 当模型面入口。"""

from __future__ import annotations

from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook

from excelmanus.engine_core.tool_result import ToolResult
from excelmanus.security import FileAccessGuard
from excelmanus.tools._guard_ctx import set_guard
from excelmanus.workbook.charts import init_guard as init_chart_guard
from excelmanus.tools.intent_tools import init_guard, manage_spreadsheet_objects
from excelmanus.workbook_commit import content_version_of_file, seed_seen_versions


def _payload(result: ToolResult) -> dict:
    assert isinstance(result, ToolResult)
    assert isinstance(result.value, dict)
    return result.value


@pytest.fixture(autouse=True)
def _bind_workspace(tmp_path: Path) -> None:
    workspace = str(tmp_path)
    set_guard(FileAccessGuard(workspace))
    init_guard(workspace)
    init_chart_guard(workspace)
    seed_seen_versions({})


def _make_chart_data(tmp_path: Path, name: str = "chart_data.xlsx") -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = "数据"
    ws["A1"] = "月份"
    ws["B1"] = "营收"
    ws["C1"] = "成本"
    months = ["1月", "2月", "3月", "4月", "5月", "6月"]
    revenues = [100, 150, 120, 180, 200, 170]
    costs = [80, 90, 85, 110, 130, 100]
    for i, (m, r, c) in enumerate(zip(months, revenues, costs), start=2):
        ws[f"A{i}"] = m
        ws[f"B{i}"] = r
        ws[f"C{i}"] = c
    fp = tmp_path / name
    wb.save(fp)
    wb.close()
    return fp


def _chart(path: Path, **fields: object) -> ToolResult:
    payload = {"kind": "chart", **fields}
    if "sheet" not in payload and "sheet_name" not in payload:
        payload["sheet"] = "数据"
    return manage_spreadsheet_objects(
        file_path=str(path),
        operations=[payload],
        expected_version=content_version_of_file(path),
    )


class TestCreateExcelChart:
    def test_bar_chart(self, tmp_path: Path) -> None:
        fp = _make_chart_data(tmp_path)
        result = _payload(
            _chart(
                fp,
                chart_type="bar",
                data_range="B1:C7",
                categories_range="A2:A7",
                title="月度营收与成本",
            )
        )
        assert result["status"] == "success"
        assert result["chart_type"] == "bar"
        wb = load_workbook(fp)
        assert len(wb["数据"]._charts) == 1
        wb.close()

    def test_line_chart(self, tmp_path: Path) -> None:
        fp = _make_chart_data(tmp_path)
        result = _payload(
            _chart(
                fp,
                chart_type="line",
                data_range="B1:B7",
                categories_range="A2:A7",
                target_cell="E1",
            )
        )
        assert result["status"] == "success"
        assert result["target_cell"] == "E1"

    def test_pie_chart(self, tmp_path: Path) -> None:
        fp = _make_chart_data(tmp_path)
        result = _payload(
            _chart(
                fp,
                chart_type="pie",
                data_range="B1:B7",
                categories_range="A2:A7",
                title="营收占比",
            )
        )
        assert result["status"] == "success"

    def test_area_chart(self, tmp_path: Path) -> None:
        fp = _make_chart_data(tmp_path)
        result = _payload(
            _chart(fp, chart_type="area", data_range="B1:C7", categories_range="A2:A7")
        )
        assert result["status"] == "success"

    def test_scatter_chart(self, tmp_path: Path) -> None:
        fp = _make_chart_data(tmp_path)
        result = _payload(
            _chart(
                fp,
                chart_type="scatter",
                data_range="B1:C7",
                categories_range="B2:B7",
            )
        )
        assert result["status"] == "success"

    def test_chart_on_new_target_sheet(self, tmp_path: Path) -> None:
        fp = _make_chart_data(tmp_path)
        result = _payload(
            _chart(
                fp,
                chart_type="bar",
                data_range="B1:B7",
                target_sheet="图表汇总",
                target_cell="A1",
            )
        )
        assert result["status"] == "success"
        assert result["target_sheet"] == "图表汇总"
        wb = load_workbook(fp)
        assert "图表汇总" in wb.sheetnames
        assert len(wb["图表汇总"]._charts) == 1
        wb.close()

    def test_chart_with_style_and_size(self, tmp_path: Path) -> None:
        fp = _make_chart_data(tmp_path)
        result = _payload(
            _chart(
                fp,
                chart_type="line",
                data_range="B1:C7",
                style=10,
                width=20.0,
                height=12.0,
                x_title="月份",
                y_title="金额",
            )
        )
        assert result["status"] == "success"

    def test_invalid_chart_type(self, tmp_path: Path) -> None:
        fp = _make_chart_data(tmp_path)
        result = _chart(fp, chart_type="radar", data_range="B1:B7")
        assert result.success is False

    def test_from_rows_mode(self, tmp_path: Path) -> None:
        fp = _make_chart_data(tmp_path)
        result = _payload(
            _chart(fp, chart_type="bar", data_range="A1:G3", from_rows=True)
        )
        assert result["status"] == "success"

    def test_missing_sheet_is_invalid_args_not_save_failed(self, tmp_path: Path) -> None:
        fp = _make_chart_data(tmp_path)
        result = _chart(
            fp,
            chart_type="bar",
            data_range="B1:C7",
            sheet="不存在的表",
        )
        assert result.success is False
        assert result.error is not None
        assert result.error.code == "INVALID_ARGS"
        assert "SAVE_FAILED" not in (result.model_text or "")

    def test_sheet_qualified_data_range(self, tmp_path: Path) -> None:
        fp = _make_chart_data(tmp_path)
        result = _payload(
            _chart(
                fp,
                chart_type="column",
                data_range="数据!B1:C7",
                categories_range="数据!A2:A7",
                target_cell="E2",
            )
        )
        assert result["status"] == "success"
        assert result["chart_type"] == "bar"
