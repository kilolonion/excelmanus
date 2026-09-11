"""写入日志与摘要回归测试。

覆盖：
- SessionState.record_write_operation 记录结构化写入日志
- SessionState.render_write_operations_log 渲染可读文本
- ToolDispatcher._extract_write_summary 提取写入摘要
- ToolDispatcher._extract_run_code_write_summary 提取 run_code 摘要
"""

from __future__ import annotations



from excelmanus.engine_core.session_state import SessionState


# ── SessionState write_operations_log 测试 ──────────────────


class TestSessionStateWriteOperationsLog:
    def test_record_single_write(self):
        state = SessionState()
        state.record_write_operation(
            tool_name="write_cells",
            file_path="data.xlsx",
            sheet="Sheet1",
            cell_range="A1:C100",
            summary="写入 100 行 × 3 列",
        )
        assert len(state.write_operations_log) == 1
        entry = state.write_operations_log[0]
        assert entry["tool_name"] == "write_cells"
        assert entry["file_path"] == "data.xlsx"
        assert entry["sheet"] == "Sheet1"
        assert entry["range"] == "A1:C100"
        assert entry["summary"] == "写入 100 行 × 3 列"

    def test_record_multiple_writes(self):
        state = SessionState()
        state.record_write_operation(tool_name="write_cells", file_path="a.xlsx")
        state.record_write_operation(tool_name="create_sheet", file_path="b.xlsx", summary="创建 sheet「汇总」")
        state.record_write_operation(tool_name="run_code", file_path="c.xlsx")
        assert len(state.write_operations_log) == 3

    def test_record_minimal_entry(self):
        state = SessionState()
        state.record_write_operation(tool_name="write_cells")
        entry = state.write_operations_log[0]
        assert entry == {"tool_name": "write_cells"}
        assert "file_path" not in entry

    def test_reset_loop_stats_clears_log(self):
        state = SessionState()
        state.record_write_operation(tool_name="write_cells", file_path="x.xlsx")
        assert len(state.write_operations_log) == 1
        state.reset_loop_stats()
        assert len(state.write_operations_log) == 0

    def test_reset_session_clears_log(self):
        state = SessionState()
        state.record_write_operation(tool_name="write_cells", file_path="x.xlsx")
        state.reset_session()
        assert len(state.write_operations_log) == 0


class TestRenderWriteOperationsLog:
    def test_empty_log_returns_empty(self):
        state = SessionState()
        assert state.render_write_operations_log() == ""

    def test_single_entry_rendered(self):
        state = SessionState()
        state.record_write_operation(
            tool_name="write_cells",
            file_path="data.xlsx",
            sheet="Sheet1",
            cell_range="A1:C10",
            summary="写入 10 行数据",
        )
        text = state.render_write_operations_log()
        assert "本轮写入操作记录" in text
        assert "write_cells" in text
        assert "data.xlsx / Sheet1 / A1:C10" in text
        assert "写入 10 行数据" in text

    def test_multiple_entries_numbered(self):
        state = SessionState()
        state.record_write_operation(tool_name="write_cells", file_path="a.xlsx", sheet="S1")
        state.record_write_operation(tool_name="create_sheet", file_path="b.xlsx", summary="创建 sheet")
        state.record_write_operation(tool_name="run_code", file_path="c.xlsx")
        text = state.render_write_operations_log()
        assert "1. write_cells" in text
        assert "2. create_sheet" in text
        assert "3. run_code" in text

    def test_entry_without_optional_fields(self):
        state = SessionState()
        state.record_write_operation(tool_name="write_cells", file_path="data.xlsx")
        text = state.render_write_operations_log()
        assert "write_cells → data.xlsx" in text


# ── ToolDispatcher 写入摘要提取测试 ──────────────────────


class TestExtractWriteSummary:
    def _call(self, tool_name, arguments, result_str=""):
        from excelmanus.engine_core.tool_dispatcher import ToolDispatcher
        return ToolDispatcher._extract_write_summary(tool_name, arguments, result_str)

    def test_write_cells_with_values(self):
        args = {"values": [[1, 2, 3], [4, 5, 6], [7, 8, 9]]}
        result = self._call("write_cells", args)
        assert "3 行" in result
        assert "3 列" in result

    def test_write_cells_without_values(self):
        result = self._call("write_cells", {})
        assert result == "写入数据"

    def test_create_sheet(self):
        result = self._call("create_sheet", {"sheet_name": "汇总"})
        assert "汇总" in result

    def test_delete_sheet(self):
        result = self._call("delete_sheet", {"sheet_name": "临时"})
        assert "删除" in result
        assert "临时" in result

    def test_insert_rows(self):
        result = self._call("insert_rows", {"count": 5})
        assert "5 行" in result

    def test_unknown_tool(self):
        result = self._call("some_other_tool", {})
        assert result == ""


class TestExtractRunCodeWriteSummary:
    def _call(self, result_str):
        from excelmanus.engine_core.tool_dispatcher import ToolDispatcher
        return ToolDispatcher._extract_run_code_write_summary(result_str)

    def test_extracts_first_line(self):
        result = self._call("已写入 500 行数据\n完成")
        assert result == "已写入 500 行数据"

    def test_skips_json_lines(self):
        result = self._call('{"status": "ok"}\n实际写入 100 行')
        assert result == "实际写入 100 行"

    def test_empty_result(self):
        result = self._call("")
        assert "run_code" in result

    def test_truncates_long_line(self):
        long_line = "x" * 200
        result = self._call(long_line)
        assert len(result) <= 120


# ── Verifier prompt 含 delta 注入测试 ──────────────────────
