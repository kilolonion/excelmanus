"""sheet_tools 工具测试：重点覆盖 list_sheets 分页行为。"""

from __future__ import annotations

from pathlib import Path

import pytest
from openpyxl import Workbook

from excelmanus.engine_core.tool_result import ToolResult
from excelmanus.tools import workbook_tools as sheet_tools


def _payload(result: ToolResult) -> dict:
    assert isinstance(result, ToolResult)
    assert isinstance(result.value, dict)
    return result.value


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    """创建多工作表测试文件并初始化 guard。"""
    wb = Workbook()
    ws = wb.active
    ws.title = "S01"
    ws.append(["id", "name"])
    ws.append([1, "A"])
    for i in range(2, 8):
        ws_new = wb.create_sheet(f"S{i:02d}")
        ws_new.append(["id", "name"])
        ws_new.append([i, f"N{i}"])
    wb.save(tmp_path / "multi.xlsx")
    sheet_tools.init_guard(str(tmp_path))
    return tmp_path


class TestObservationSheets:
    def test_basic(self,workspace):
        result=sheet_tools.observe_spreadsheet("multi.xlsx")
        assert result.success and result.value["file_path"]=="multi.xlsx"
        assert len(result.value["sheets"])==7

    def test_pagination(self,workspace):
        page=sheet_tools.observe_spreadsheet("multi.xlsx",offset=2,limit=2).value
        assert len(page["sheets"])==7
        assert [r["sheet"] for r in page["regions"]]==["S03","S04"]
        assert page["coverage"]["next_offset"]==4

    @pytest.mark.parametrize("offset,limit",[(-1,10),(0,0)])
    def test_invalid_paging(self,workspace,offset,limit):
        assert not sheet_tools.observe_spreadsheet("multi.xlsx",offset=offset,limit=limit).success

    def test_file_not_found_preserves_candidates(self,workspace):
        result=sheet_tools.observe_spreadsheet("nonexistent.xlsx")
        assert not result.success
        assert "multi.xlsx" in result.value["available_excel_files"]
        assert "不要擅自替换" in result.value["hint"]

    def test_tool_def_disables_global_truncation(self,workspace):
        tool=next(t for t in sheet_tools.get_tools() if t.name=="observe_spreadsheet")
        assert tool.max_result_chars==0
