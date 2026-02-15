"""窗口感知渲染测试。"""

from excelmanus.window_perception.models import (
    ColumnDef,
    DetailLevel,
    IntentTag,
    Viewport,
    WindowRenderAction,
    WindowSnapshot,
    WindowState,
    WindowType,
)
from excelmanus.window_perception.renderer import (
    build_tool_perception_payload,
    render_system_notice,
    render_window_background,
    render_tool_perception_block,
    render_window_keep,
    render_window_minimized,
)


class TestWindowRenderer:
    """渲染器测试。"""

    def test_render_explorer_window(self) -> None:
        window = WindowState(
            id="explorer_1",
            type=WindowType.EXPLORER,
            title="资源管理器",
            directory=".",
            metadata={"entries": ["[XLS] sales.xlsx", "[DIR] data"]},
        )
        text = render_window_keep(window)
        assert "资源管理器" in text
        assert "sales.xlsx" in text

    def test_render_sheet_window(self) -> None:
        window = WindowState(
            id="sheet_1",
            type=WindowType.SHEET,
            title="sheet",
            file_path="sales.xlsx",
            sheet_name="Q1",
            sheet_tabs=["Q1", "Q2"],
            viewport=Viewport(range_ref="A1:J25", total_rows=500, total_cols=30),
            preview_rows=[{"产品": "A", "金额": 100}],
            style_summary="样式类2种",
        )
        text = render_window_keep(window)
        assert "sales.xlsx / Q1" in text
        assert "A1:J25" in text
        assert "样式类2种" in text

    def test_render_minimized(self) -> None:
        window = WindowState(
            id="sheet_1",
            type=WindowType.SHEET,
            title="sheet",
            file_path="sales.xlsx",
            sheet_name="Q1",
            viewport=Viewport(range_ref="A1:B2", total_rows=200, total_cols=15),
            summary="最近修改区域: A1:B2",
        )
        text = render_window_minimized(window)
        assert "IDLE" in text
        assert "200x15" in text

    def test_render_background_contains_columns(self) -> None:
        window = WindowState(
            id="sheet_2",
            type=WindowType.SHEET,
            title="sheet",
            file_path="sales.xlsx",
            sheet_name="Q2",
            sheet_tabs=["Q1", "Q2", "Q3"],
            viewport=Viewport(range_ref="A1:C25", total_rows=5000, total_cols=12),
            preview_rows=[{"订单编号": "ORD-1", "日期": "2025-01-01", "金额": 100}],
        )
        text = render_window_background(window)
        assert "BG" in text
        assert "5000" in text
        assert "12" in text
        assert "cols:" in text or "订単編号" in text or "订单编号" in text
        assert "viewport:" in text or "A1:C25" in text

    def test_render_system_notice(self) -> None:
        snapshots = [
            WindowSnapshot(
                window_id="w1",
                action=WindowRenderAction.KEEP,
                rendered_text="[ACTIVE -- sales.xlsx / Q1]",
                estimated_tokens=100,
            )
        ]
        text = render_system_notice(snapshots)
        assert "窗口感知上下文" in text

    def test_render_system_notice_anchored_mode(self) -> None:
        snapshots = [
            WindowSnapshot(
                window_id="w1",
                action=WindowRenderAction.KEEP,
                rendered_text="[W1 · sales.xlsx / Q1]",
                estimated_tokens=100,
            )
        ]
        text = render_system_notice(snapshots, mode="anchored")
        assert "数据窗口" in text

    def test_render_window_keep_anchored_full(self) -> None:
        window = WindowState(
            id="W3",
            type=WindowType.SHEET,
            title="sheet",
            file_path="sales.xlsx",
            sheet_name="Q1",
            viewport=Viewport(range_ref="A1:C10", total_rows=100, total_cols=3),
            viewport_range="A1:C10",
            detail_level=DetailLevel.FULL,
            columns=[ColumnDef(name="日期"), ColumnDef(name="产品"), ColumnDef(name="金额")],
            data_buffer=[
                {"日期": "2024-01-01", "产品": "A", "金额": 100},
                {"日期": "2024-01-02", "产品": "B", "金额": 120},
            ],
        )
        text = render_window_keep(window, mode="anchored", max_rows=10, current_iteration=1)
        assert "W3 · sales.xlsx / Q1" in text
        assert "列: [日期, 产品, 金额]" in text or "cols: [日期, 产品, 金额]" in text

    def test_tool_payload_and_block(self) -> None:
        window = WindowState(
            id="sheet_1",
            type=WindowType.SHEET,
            title="sheet",
            file_path="sales.xlsx",
            sheet_name="Q1",
            sheet_tabs=["Q1", "Q2", "Q3"],
            viewport=Viewport(range_ref="A1:J25", total_rows=50, total_cols=10),
        )
        window.metadata["scroll_position"] = {
            "vertical_pct": 0.0,
            "horizontal_pct": 10.0,
            "remaining_rows_pct": 50.0,
            "remaining_cols_pct": 20.0,
        }
        window.metadata["status_bar"] = {"sum": 371200, "count": 24, "average": 15466.6}
        window.metadata["column_widths"] = {"A": 12.0, "B": 15.0}
        window.metadata["row_heights"] = {"1": 24.0, "2": 18.0}
        window.metadata["merged_ranges"] = ["F1:H1"]
        window.metadata["conditional_effects"] = ["D2:D7: 条件着色（cellIs/greaterThan）"]
        payload = build_tool_perception_payload(window)
        assert payload is not None
        block = render_tool_perception_block(payload)
        assert "--- perception ---" in block
        assert "sheet: Q1 | others: [Q2] [Q3]" in block
        assert "scroll: v=0.0% | h=10.0%" in block
        assert "stats: SUM=371,200 | COUNT=24 | AVG=15,466.60" in block
        assert "col-width: A=12, B=15" in block
        assert "row-height: 1=24, 2=18" in block
        assert "merged: F1:H1" in block
        assert "cond-fmt: D2:D7:" in block

    def test_render_wurm_full_format_intent_prefers_style(self) -> None:
        window = WindowState(
            id="W4",
            type=WindowType.SHEET,
            title="sheet",
            file_path="sales.xlsx",
            sheet_name="Style",
            viewport=Viewport(range_ref="A1:C10", total_rows=100, total_cols=3),
            viewport_range="A1:C10",
            detail_level=DetailLevel.FULL,
            intent_tag=IntentTag.FORMAT,
            style_summary="字体+填充",
            columns=[ColumnDef(name="A"), ColumnDef(name="B"), ColumnDef(name="C")],
            data_buffer=[{"A": 1, "B": 2, "C": 3}],
        )
        text = render_window_keep(
            window,
            mode="anchored",
            max_rows=10,
            current_iteration=1,
            intent_profile={"intent": "format", "show_style": True, "max_rows": 2, "focus_text": "样式优先"},
        )
        assert "intent: format" in text
        assert "style: 字体+填充" in text

    def test_render_wurm_full_validate_intent_prefers_quality(self) -> None:
        window = WindowState(
            id="W5",
            type=WindowType.SHEET,
            title="sheet",
            file_path="sales.xlsx",
            sheet_name="Check",
            viewport=Viewport(range_ref="A1:C10", total_rows=100, total_cols=3),
            viewport_range="A1:C10",
            detail_level=DetailLevel.FULL,
            intent_tag=IntentTag.VALIDATE,
            columns=[ColumnDef(name="A"), ColumnDef(name="B"), ColumnDef(name="C")],
            data_buffer=[
                {"A": 1, "B": "", "C": 3},
                {"A": 1, "B": "", "C": 3},
                {"A": 4, "B": 5, "C": 6},
            ],
        )
        text = render_window_keep(
            window,
            mode="anchored",
            max_rows=10,
            current_iteration=1,
            intent_profile={"intent": "validate", "show_quality": True, "max_rows": 3, "focus_text": "质量校验优先"},
        )
        assert "intent: validate" in text
        assert "quality: empty_cells" in text
