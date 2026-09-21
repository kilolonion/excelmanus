"""Post-Write Inline Checkpoint 测试。

覆盖当前写入意图：
- edit_spreadsheet / format_spreadsheet / manage_spreadsheet_objects / write_word
- 文件不存在、路径为空时静默返回空串
"""

from __future__ import annotations

from pathlib import Path

import openpyxl


def _create_test_xlsx(tmp_path: Path, *, sheets: dict[str, list[list]] | None = None) -> Path:
    fp = tmp_path / "test.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    if sheets:
        for name, data in sheets.items():
            if name == "Sheet1":
                target = ws
            else:
                target = wb.create_sheet(name)
            for row_idx, row_data in enumerate(data, 1):
                for col_idx, val in enumerate(row_data, 1):
                    target.cell(row=row_idx, column=col_idx, value=val)
    wb.save(str(fp))
    wb.close()
    return fp


class TestPostWriteCheckpointEditSpreadsheet:
    def test_reports_sheet_dimensions(self, tmp_path):
        fp = _create_test_xlsx(tmp_path, sheets={"Sheet1": [["hello"]]})
        from excelmanus.engine_core.tool_dispatcher import ToolDispatcher
        result = ToolDispatcher._post_write_checkpoint(
            "edit_spreadsheet",
            {"file_path": str(fp), "sheet": "Sheet1"},
            str(tmp_path),
        )
        assert "回读确认" in result
        assert "Sheet1" in result

    def test_with_operations_sheet(self, tmp_path):
        fp = _create_test_xlsx(tmp_path, sheets={"数据": [["a", "b"]]})
        from excelmanus.engine_core.tool_dispatcher import ToolDispatcher
        result = ToolDispatcher._post_write_checkpoint(
            "edit_spreadsheet",
            {
                "file_path": str(fp),
                "operations": [{"sheet": "数据", "kind": "values"}],
            },
            str(tmp_path),
        )
        assert "数据" in result


class TestPostWriteCheckpointFormat:
    def test_format_spreadsheet_reports_style_readback(self, tmp_path):
        # format_spreadsheet 现在做样式回读，不再走「未核验」的跳过分支
        fp = _create_test_xlsx(tmp_path, sheets={"Sheet1": [[1], [2], [3]]})
        from excelmanus.engine_core.tool_dispatcher import ToolDispatcher
        result = ToolDispatcher._post_write_checkpoint(
            "format_spreadsheet",
            {"file_path": str(fp), "sheet": "Sheet1"},
            str(tmp_path),
        )
        assert "样式回读" in result
        assert "Sheet1" in result


class TestPostWriteCheckpointEdgeCases:
    def test_empty_file_path(self, tmp_path):
        from excelmanus.engine_core.tool_dispatcher import ToolDispatcher
        result = ToolDispatcher._post_write_checkpoint(
            "edit_spreadsheet", {"file_path": ""}, str(tmp_path),
        )
        assert result == ""

    def test_nonexistent_file(self, tmp_path):
        from excelmanus.engine_core.tool_dispatcher import ToolDispatcher
        result = ToolDispatcher._post_write_checkpoint(
            "edit_spreadsheet",
            {"file_path": str(tmp_path / "nonexistent.xlsx")},
            str(tmp_path),
        )
        assert result == ""

    def test_unknown_tool(self, tmp_path):
        fp = _create_test_xlsx(tmp_path)
        from excelmanus.engine_core.tool_dispatcher import ToolDispatcher
        result = ToolDispatcher._post_write_checkpoint(
            "some_other_tool", {"file_path": str(fp)}, str(tmp_path),
        )
        assert result == ""

    def test_relative_path_resolved(self, tmp_path):
        _create_test_xlsx(tmp_path, sheets={"Sheet1": [["val"]]})
        from excelmanus.engine_core.tool_dispatcher import ToolDispatcher
        result = ToolDispatcher._post_write_checkpoint(
            "edit_spreadsheet",
            {"file_path": "test.xlsx", "sheet": "Sheet1"},
            str(tmp_path),
        )
        assert "回读确认" in result
