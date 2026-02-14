"""sheet_tools 工具测试：重点覆盖 list_sheets 分页行为。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from openpyxl import Workbook

from excelmanus.tools import sheet_tools


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


class TestListSheets:
    def test_basic(self, workspace: Path) -> None:
        result = json.loads(sheet_tools.list_sheets("multi.xlsx"))
        assert result["file"] == "multi.xlsx"
        assert result["sheet_count"] == 7
        assert result["returned"] == 7
        assert len(result["sheets"]) == 7

    def test_pagination(self, workspace: Path) -> None:
        full = json.loads(sheet_tools.list_sheets("multi.xlsx"))
        page = json.loads(sheet_tools.list_sheets("multi.xlsx", offset=2, limit=2))
        assert page["sheet_count"] == full["sheet_count"]
        assert page["offset"] == 2
        assert page["limit"] == 2
        assert page["returned"] == 2
        assert page["sheets"] == full["sheets"][2:4]
        assert page["has_more"] is True

    def test_invalid_paging(self, workspace: Path) -> None:
        result = json.loads(sheet_tools.list_sheets("multi.xlsx", offset=-1, limit=10))
        assert "error" in result
        result = json.loads(sheet_tools.list_sheets("multi.xlsx", offset=0, limit=0))
        assert "error" in result

    def test_tool_def_disables_global_truncation(self, workspace: Path) -> None:
        tools = {tool.name: tool for tool in sheet_tools.get_tools()}
        assert tools["list_sheets"].max_result_chars == 0
