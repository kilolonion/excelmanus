"""L2 巡检修复回归测试。

覆盖范围：
- B1: tool_dispatcher._EXCEL_WRITE_TOOLS 更新为实际 MCP 工具名
- B2: _drop_window 清理 WindowLocator 身份映射
- B3: verification_gate._WRITE_TOOLS 更新为实际工具名
- I1: ConfirmationRecord 包含 sheet_dimensions 字段
- R1: _latest_change_summary 重复定义一致性
"""

from __future__ import annotations

import json

import pytest

from excelmanus.window_perception.locator import (
    WindowLocator,
    LocatorReject,
    WINDOW_IDENTITY_CONFLICT,
)
from excelmanus.window_perception.identity import SheetIdentity, ExplorerIdentity
from excelmanus.window_perception.manager import WindowPerceptionManager
from excelmanus.window_perception.models import PerceptionBudget
from excelmanus.window_perception.confirmation import (
    ConfirmationRecord,
    build_confirmation_record,
    serialize_confirmation,
)
from excelmanus.window_perception.projection_service import project_confirmation


_DEFAULT_BUDGET = PerceptionBudget()


def _make_manager():
    return WindowPerceptionManager(enabled=True, budget=_DEFAULT_BUDGET)


# ── B1: _EXCEL_WRITE_TOOLS 更新 ──────────────────────────────


class TestExcelWriteToolsUpdated:
    def test_write_to_sheet_in_excel_write_tools(self):
        from excelmanus.engine_core.tool_dispatcher import ToolDispatcher
        assert "write_to_sheet" in ToolDispatcher._EXCEL_WRITE_TOOLS

    def test_format_range_in_excel_write_tools(self):
        from excelmanus.engine_core.tool_dispatcher import ToolDispatcher
        assert "format_range" in ToolDispatcher._EXCEL_WRITE_TOOLS

    def test_pruned_tools_not_in_excel_write_tools(self):
        from excelmanus.engine_core.tool_dispatcher import ToolDispatcher
        pruned = {"write_cells", "insert_rows", "insert_columns", "create_sheet", "delete_sheet"}
        overlap = pruned & ToolDispatcher._EXCEL_WRITE_TOOLS
        assert not overlap, f"Pruned tools still present: {overlap}"


# ── B2: _drop_window 清理 locator ─────────────────────────────


class TestLocatorUnregister:
    def test_unregister_removes_identity(self):
        loc = WindowLocator()
        identity = SheetIdentity(file_path_norm="test.xlsx", sheet_name_norm="sheet1")
        loc.register("win_1", identity)
        assert loc.find(identity) == "win_1"
        loc.unregister("win_1")
        assert loc.find(identity) is None

    def test_unregister_nonexistent_is_noop(self):
        loc = WindowLocator()
        loc.unregister("nonexistent")  # should not raise

    def test_unregister_allows_re_registration(self):
        loc = WindowLocator()
        identity = SheetIdentity(file_path_norm="test.xlsx", sheet_name_norm="sheet1")
        loc.register("win_1", identity)
        loc.unregister("win_1")
        loc.register("win_2", identity)
        assert loc.find(identity) == "win_2"

    def test_unregister_does_not_affect_other_windows(self):
        loc = WindowLocator()
        id1 = SheetIdentity(file_path_norm="a.xlsx", sheet_name_norm="s1")
        id2 = SheetIdentity(file_path_norm="b.xlsx", sheet_name_norm="s2")
        loc.register("win_1", id1)
        loc.register("win_2", id2)
        loc.unregister("win_1")
        assert loc.find(id1) is None
        assert loc.find(id2) == "win_2"

    def test_without_unregister_re_registration_would_conflict(self):
        """Demonstrates the bug: without unregister, re-registration raises LocatorReject."""
        loc = WindowLocator()
        identity = SheetIdentity(file_path_norm="test.xlsx", sheet_name_norm="sheet1")
        loc.register("win_1", identity)
        # Without unregister, trying to register a different window for the same identity fails
        with pytest.raises(LocatorReject):
            loc.register("win_2", identity)


class TestDropWindowCleansLocator:
    def test_evicted_window_identity_cleaned(self):
        """After _drop_window, the locator should allow re-registration of same identity."""
        mgr = _make_manager()
        result = json.dumps({
            "shape": {"rows": 10, "columns": 3},
            "columns": ["A", "B", "C"],
            "data": [{"A": 1}],
        })
        mgr.update_from_tool_call(
            tool_name="read_excel",
            arguments={"file_path": "data.xlsx", "sheet_name": "Sheet1"},
            result_text=result,
        )
        windows = list(mgr._windows.values())
        assert len(windows) == 1
        win_id = windows[0].id

        # Drop the window
        mgr._drop_window(win_id)
        assert win_id not in mgr._windows

        # Re-create a window for the same file+sheet should work (no LocatorReject)
        payload = mgr.update_from_tool_call(
            tool_name="read_excel",
            arguments={"file_path": "data.xlsx", "sheet_name": "Sheet1"},
            result_text=result,
        )
        assert payload is not None
        new_windows = list(mgr._windows.values())
        assert len(new_windows) == 1
        assert new_windows[0].id != win_id


# ── B3: verification_gate._WRITE_TOOLS 更新 ───────────────────


class TestVerificationGateWriteTools:
    def test_mcp_write_tools_in_gate(self):
        from excelmanus.engine_core.verification_gate import _WRITE_TOOLS
        assert "write_to_sheet" in _WRITE_TOOLS
        assert "format_range" in _WRITE_TOOLS

    def test_text_tools_still_in_gate(self):
        from excelmanus.engine_core.verification_gate import _WRITE_TOOLS
        assert "write_text_file" in _WRITE_TOOLS
        assert "edit_text_file" in _WRITE_TOOLS

    def test_pruned_tools_not_in_gate(self):
        from excelmanus.engine_core.verification_gate import _WRITE_TOOLS
        pruned = {"write_cells", "write_excel", "advanced_format",
                  "create_sheet", "delete_sheet", "insert_rows", "insert_columns"}
        overlap = pruned & _WRITE_TOOLS
        assert not overlap, f"Pruned tools still present: {overlap}"


# ── I1: ConfirmationRecord sheet_dimensions ────────────────────


class TestConfirmationRecordSheetDimensions:
    def test_field_exists(self):
        record = ConfirmationRecord(
            window_label="test",
            operation="read_excel",
            range_ref="A1:C10",
            rows=10,
            cols=3,
            change_summary="test",
            intent="general",
            sheet_dimensions=(("Sheet1", 100, 5), ("Sheet2", 50, 3)),
        )
        assert record.sheet_dimensions == (("Sheet1", 100, 5), ("Sheet2", 50, 3))

    def test_default_is_empty_tuple(self):
        record = ConfirmationRecord(
            window_label="test",
            operation="read_excel",
            range_ref="A1:C10",
            rows=10,
            cols=3,
            change_summary="test",
            intent="general",
        )
        assert record.sheet_dimensions == ()

    def test_build_confirmation_record_passes_sheet_dimensions(self):
        mgr = _make_manager()
        result = json.dumps({
            "shape": {"rows": 10, "columns": 3},
            "columns": ["A", "B", "C"],
            "data": [{"A": 1}],
        })
        mgr.update_from_tool_call(
            tool_name="read_excel",
            arguments={"file_path": "data.xlsx", "sheet_name": "Sheet1"},
            result_text=result,
        )
        windows = list(mgr._windows.values())
        assert len(windows) == 1
        window = windows[0]

        projection = project_confirmation(window, tool_name="read_excel")
        record = build_confirmation_record(projection=projection)
        # sheet_dimensions should be passed through (may be empty if no dims in result)
        assert hasattr(record, "sheet_dimensions")
        assert isinstance(record.sheet_dimensions, tuple)

    def test_serialize_unified_includes_sheet_dimensions(self):
        record = ConfirmationRecord(
            window_label="win_1: data.xlsx / Sheet1",
            operation="read_excel",
            range_ref="A1:C10",
            rows=100,
            cols=5,
            change_summary="状态同步",
            intent="general",
            sheet_dimensions=(("Sheet1", 100, 5), ("Sheet2", 50, 3)),
        )
        text = serialize_confirmation(record, mode="unified")
        assert "Sheet1(100r" in text
        assert "Sheet2(50r" in text

    def test_serialize_anchored_includes_sheet_dimensions(self):
        record = ConfirmationRecord(
            window_label="win_1: data.xlsx / Sheet1",
            operation="read_excel",
            range_ref="A1:C10",
            rows=100,
            cols=5,
            change_summary="状态同步",
            intent="general",
            sheet_dimensions=(("Sheet1", 100, 5),),
        )
        text = serialize_confirmation(record, mode="anchored")
        assert "Sheet1(100r" in text


# ── R1: _latest_change_summary 一致性 ─────────────────────────


class TestLatestChangeSummaryConsistency:
    def test_both_modules_produce_same_result(self):
        from excelmanus.window_perception.confirmation import _latest_change_summary as confirm_fn
        from excelmanus.window_perception.projection_service import _latest_change_summary as proj_fn
        from excelmanus.window_perception.models import ChangeRecord

        log = [
            ChangeRecord(
                operation="write",
                tool_summary="write_to_sheet",
                affected_range="A1:C10",
                change_type="written",
                iteration=1,
                affected_row_indices=[],
            ),
        ]
        assert confirm_fn(log) == proj_fn(log)

    def test_empty_log(self):
        from excelmanus.window_perception.confirmation import _latest_change_summary as confirm_fn
        from excelmanus.window_perception.projection_service import _latest_change_summary as proj_fn
        assert confirm_fn([]) == proj_fn([])
