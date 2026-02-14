"""窗口感知渲染测试。"""

from excelmanus.window_perception.models import Viewport, WindowRenderAction, WindowSnapshot, WindowState, WindowType
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
            metadata={"entries": ["📊 sales.xlsx", "📁 data"]},
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
        assert "挂起" in text
        assert "200×15" in text

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
        assert "后台" in text
        assert "5000行 × 12列" in text
        assert "列: 订单编号, 日期, 金额" in text
        assert "视口: A1:C25" in text

    def test_render_system_notice(self) -> None:
        snapshots = [
            WindowSnapshot(
                window_id="w1",
                action=WindowRenderAction.KEEP,
                rendered_text="【窗口 · sales.xlsx / Q1】",
                estimated_tokens=100,
            )
        ]
        text = render_system_notice(snapshots)
        assert "窗口感知上下文" in text

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
        assert "环境感知" in block
        assert "当前Sheet: Q1 | 其他: [Q2] [Q3]" in block
        assert "滚动条位置: 纵向 0.0% | 横向 10.0%" in block
        assert "状态栏: SUM=371,200 | COUNT=24 | AVERAGE=15,466.60" in block
        assert "列宽: A=12, B=15" in block
        assert "行高: 1=24, 2=18" in block
        assert "合并单元格: F1:H1" in block
        assert "条件格式效果: D2:D7: 条件着色（cellIs/greaterThan）" in block
