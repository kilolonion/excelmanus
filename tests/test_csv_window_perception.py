"""CSV 文件窗口感知层支持 — 回归测试。

覆盖范围：
- Fix 1: is_excel_path / is_csv_path 扩展
- Fix 2: 目录列表 [CSV] 标记
- Fix 3: CSV 窗口 sheet_name 自动填充
- Fix 4: CSV 确认文本 Sheet1(CSV) 标签
- Fix 5: 渲染器 CSV 适配（列截断提示无 Excel 行引用）
- 回归: observe_subagent / observe_code_execution 处理 CSV
"""

from __future__ import annotations

import json

import pytest

from excelmanus.window_perception.extractor import (
    extract_explorer_entries,
    extract_file_path,
    is_csv_path,
    is_excel_path,
)
from excelmanus.window_perception.manager import WindowPerceptionManager
from excelmanus.window_perception.models import PerceptionBudget
from excelmanus.window_perception.projection_service import project_confirmation
from excelmanus.window_perception.renderer import render_tool_perception_block

_DEFAULT_BUDGET = PerceptionBudget()


def _make_manager():
    return WindowPerceptionManager(enabled=True, budget=_DEFAULT_BUDGET)


# ── Fix 1: is_excel_path / is_csv_path ────────────────────────


class TestIsExcelPathCsv:
    @pytest.mark.parametrize("path", [
        "data.csv", "data.CSV", "report.tsv", "log.txt",
        "/home/user/data.csv", "C:\\Users\\test.tsv",
    ])
    def test_csv_recognized_as_excel_path(self, path):
        assert is_excel_path(path) is True

    @pytest.mark.parametrize("path", [
        "data.xlsx", "data.xlsm", "data.xls", "data.xlsb",
    ])
    def test_real_excel_still_recognized(self, path):
        assert is_excel_path(path) is True

    @pytest.mark.parametrize("path", [
        "readme.md", "script.py", "image.png", "",
    ])
    def test_non_table_files_rejected(self, path):
        assert is_excel_path(path) is False


class TestIsCsvPath:
    @pytest.mark.parametrize("path,expected", [
        ("data.csv", True),
        ("data.CSV", True),
        ("report.tsv", True),
        ("log.txt", True),
        ("data.xlsx", False),
        ("data.xlsm", False),
        ("readme.md", False),
        ("", False),
    ])
    def test_csv_detection(self, path, expected):
        assert is_csv_path(path) is expected


# ── Fix 2: 目录列表 [CSV] 标记 ───────────────────────────────


class TestExplorerEntriesCsvTag:
    def test_csv_file_gets_csv_tag(self):
        result_json = {
            "entries": [
                {"name": "data.csv", "type": "file", "size": "1.2MB"},
                {"name": "report.xlsx", "type": "file", "size": "3MB"},
                {"name": "notes.txt", "type": "file", "size": "500B"},
                {"name": "subdir", "type": "directory"},
            ]
        }
        entries = extract_explorer_entries(result_json)
        assert any("[CSV]" in e and "data.csv" in e for e in entries)
        assert any("[XLS]" in e and "report.xlsx" in e for e in entries)
        assert any("[CSV]" in e and "notes.txt" in e for e in entries)
        assert any("[DIR]" in e and "subdir" in e for e in entries)

    def test_matches_section_csv_tag(self):
        result_json = {
            "matches": [
                {"path": "output.tsv", "type": "file"},
                {"path": "results.xlsx", "type": "file"},
            ]
        }
        entries = extract_explorer_entries(result_json)
        assert any("[CSV]" in e and "output.tsv" in e for e in entries)
        assert any("[XLS]" in e and "results.xlsx" in e for e in entries)


# ── Fix 1 回归: extract_file_path 识别 CSV ────────────────────


class TestExtractFilePathCsv:
    def test_csv_from_arguments(self):
        args = {"file_path": "data.csv"}
        assert extract_file_path(args, None) == "data.csv"

    def test_csv_from_result_json_file_key(self):
        args = {}
        result = {"file": "output.csv"}
        assert extract_file_path(args, result) == "output.csv"

    def test_csv_from_result_json_path_key(self):
        args = {}
        result = {"path": "report.tsv"}
        assert extract_file_path(args, result) == "report.tsv"


# ── Fix 3: CSV 窗口 sheet_name 自动填充 ──────────────────────


class TestCsvWindowSheetName:
    def test_csv_window_auto_fills_sheet1(self):
        mgr = _make_manager()
        result = json.dumps({
            "shape": {"rows": 100, "columns": 5},
            "columns": ["A", "B", "C", "D", "E"],
            "data": [{"A": 1, "B": 2}],
        })
        payload = mgr.update_from_tool_call(
            tool_name="read_excel",
            arguments={"file_path": "data.csv"},
            result_text=result,
        )
        assert payload is not None
        assert payload.get("sheet") == "Sheet1"

    def test_xlsx_window_does_not_force_sheet1(self):
        mgr = _make_manager()
        result = json.dumps({
            "shape": {"rows": 50, "columns": 3},
            "columns": ["X", "Y", "Z"],
            "data": [{"X": 1}],
            "sheet": "MySheet",
        })
        payload = mgr.update_from_tool_call(
            tool_name="read_excel",
            arguments={"file_path": "data.xlsx", "sheet_name": "MySheet"},
            result_text=result,
        )
        assert payload is not None
        assert payload.get("sheet") == "MySheet"


# ── Fix 4: CSV 确认文本 Sheet1(CSV) 标签 ─────────────────────


class TestCsvConfirmationLabel:
    def test_csv_confirmation_uses_csv_label(self):
        mgr = _make_manager()
        result = json.dumps({
            "shape": {"rows": 10, "columns": 3},
            "columns": ["A", "B", "C"],
            "data": [{"A": 1}],
        })
        mgr.update_from_tool_call(
            tool_name="read_excel",
            arguments={"file_path": "sales.csv"},
            result_text=result,
        )
        windows = list(mgr._windows.values())
        assert len(windows) == 1
        window = windows[0]
        projection = project_confirmation(window, tool_name="read_excel")
        assert "Sheet1(CSV)" in projection.window_label

    def test_xlsx_confirmation_no_csv_label(self):
        mgr = _make_manager()
        result = json.dumps({
            "shape": {"rows": 10, "columns": 3},
            "columns": ["A", "B", "C"],
            "data": [{"A": 1}],
        })
        mgr.update_from_tool_call(
            tool_name="read_excel",
            arguments={"file_path": "sales.xlsx", "sheet_name": "Sheet1"},
            result_text=result,
        )
        windows = list(mgr._windows.values())
        assert len(windows) == 1
        window = windows[0]
        projection = project_confirmation(window, tool_name="read_excel")
        assert "(CSV)" not in projection.window_label


# ── Fix 5: 渲染器 CSV 列截断提示 ─────────────────────────────


class TestRendererCsvColumnTruncation:
    def test_csv_column_truncation_no_range_hint(self):
        payload = {
            "window_type": "sheet",
            "identity": "data.csv#Sheet1",
            "file": "data.csv",
            "sheet": "Sheet1",
            "intent": "general",
            "sheet_tabs": [],
            "viewport": {
                "range": "A1:E100",
                "total_rows": 100,
                "total_cols": 20,
                "visible_rows": 100,
                "visible_cols": 5,
            },
        }
        rendered = render_tool_perception_block(payload)
        assert "列截断" in rendered
        assert "1:1" not in rendered

    def test_xlsx_column_truncation_has_range_hint(self):
        payload = {
            "window_type": "sheet",
            "identity": "data.xlsx#Sheet1",
            "file": "data.xlsx",
            "sheet": "Sheet1",
            "intent": "general",
            "sheet_tabs": ["Sheet1"],
            "viewport": {
                "range": "A1:E100",
                "total_rows": 100,
                "total_cols": 20,
                "visible_rows": 100,
                "visible_cols": 5,
            },
        }
        rendered = render_tool_perception_block(payload)
        assert "列截断" in rendered
        assert "1:1" in rendered


# ── 回归: observe_subagent_context CSV ────────────────────────


class _FakeChange:
    """Duck-typed SubagentFileChange for testing."""
    def __init__(self, path, tool_name="write", change_type="write", sheets_affected=None):
        self.path = path
        self.tool_name = tool_name
        self.change_type = change_type
        self.sheets_affected = sheets_affected or []


class TestObserveSubagentCsv:
    def test_observe_subagent_context_creates_csv_window(self):
        mgr = _make_manager()
        mgr.observe_subagent_context(
            candidate_paths=["report.csv"],
            subagent_name="test_agent",
            task="process csv",
        )
        windows = [w for w in mgr._windows.values() if not w.dormant]
        assert len(windows) >= 1
        found = any(
            getattr(w, "file_path", "") == "report.csv"
            for w in windows
        )
        assert found, "CSV file should create a window via observe_subagent_context"

    def test_observe_subagent_writes_marks_csv_stale(self):
        mgr = _make_manager()
        result = json.dumps({
            "shape": {"rows": 10, "columns": 3},
            "columns": ["A", "B", "C"],
            "data": [{"A": 1}],
        })
        mgr.update_from_tool_call(
            tool_name="read_excel",
            arguments={"file_path": "report.csv"},
            result_text=result,
        )
        mgr.observe_subagent_writes(
            structured_changes=[_FakeChange("report.csv")],
            subagent_name="test_agent",
            task="update csv",
        )
        windows = list(mgr._windows.values())
        assert len(windows) == 1
        assert windows[0].stale_hint is not None


# ── 回归: observe_code_execution CSV ─────────────────────────


class _FakeAuditChange:
    """Duck-typed audit change for testing."""
    def __init__(self, path):
        self.path = path


class TestObserveCodeExecutionCsv:
    def test_code_execution_marks_csv_stale(self):
        mgr = _make_manager()
        result = json.dumps({
            "shape": {"rows": 10, "columns": 3},
            "columns": ["A", "B", "C"],
            "data": [{"A": 1}],
        })
        mgr.update_from_tool_call(
            tool_name="read_excel",
            arguments={"file_path": "data.csv"},
            result_text=result,
        )
        mgr.observe_code_execution(
            code="import pandas as pd; df = pd.read_csv('data.csv'); df.to_csv('data.csv')",
            audit_changes=[_FakeAuditChange("data.csv")],
            stdout_tail="",
            iteration=1,
        )
        windows = list(mgr._windows.values())
        assert len(windows) == 1
        assert windows[0].stale_hint is not None


# ── 回归: CSV + 错误 payload 透传 ────────────────────────────


class TestCsvErrorPassthrough:
    def test_csv_error_not_swallowed(self):
        mgr = _make_manager()
        error_result = json.dumps({
            "status": "error",
            "message": "CSV 文件不支持 range 参数",
        })
        payload = mgr.update_from_tool_call(
            tool_name="read_excel",
            arguments={"file_path": "data.csv", "range": "A1:C10"},
            result_text=error_result,
        )
        assert payload is None, "Error payload should return None (passthrough)"
