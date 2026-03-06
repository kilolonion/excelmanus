"""引用提示注入窗口感知的集成测试。"""
from __future__ import annotations

from excelmanus.reference_graph.models import RefType, SheetRefEdge, SheetRefSummary, WorkbookRefIndex


class TestBuildReferenceHints:
    def test_outgoing_hint(self) -> None:
        from excelmanus.window_perception.renderer import _build_reference_hints

        edge = SheetRefEdge("订单表", "产品表", RefType.FORMULA, 10, ["=VLOOKUP(...)"], [])
        index = WorkbookRefIndex(
            file_path="t.xlsx",
            sheets={
                "订单表": SheetRefSummary("订单表", 50, [edge], [], 5, ["VLOOKUP"]),
            },
            cross_sheet_edges=[edge],
            external_refs=[],
            named_ranges={},
            built_at=0,
        )
        hints = _build_reference_hints("订单表", index)
        assert "产品表" in hints

    def test_incoming_hint(self) -> None:
        from excelmanus.window_perception.renderer import _build_reference_hints

        edge = SheetRefEdge("汇总表", "订单表", RefType.FORMULA, 5, [], [])
        index = WorkbookRefIndex(
            file_path="t.xlsx",
            sheets={
                "订单表": SheetRefSummary("订单表", 0, [], [edge], 0, []),
            },
            cross_sheet_edges=[edge],
            external_refs=[],
            named_ranges={},
            built_at=0,
        )
        hints = _build_reference_hints("订单表", index)
        assert "汇总表" in hints

    def test_no_refs_returns_empty(self) -> None:
        from excelmanus.window_perception.renderer import _build_reference_hints

        index = WorkbookRefIndex(
            file_path="t.xlsx",
            sheets={"Sheet1": SheetRefSummary("Sheet1", 0, [], [], 0, [])},
            cross_sheet_edges=[],
            external_refs=[],
            named_ranges={},
            built_at=0,
        )
        hints = _build_reference_hints("Sheet1", index)
        assert hints == ""


class TestRefHintsInRender:
    def test_render_sheet_includes_hints(self) -> None:
        from excelmanus.window_perception.domain import SheetWindow
        from excelmanus.window_perception.renderer import _render_sheet

        w = SheetWindow.new(
            id="sheet_1", title="test", file_path="f.xlsx", sheet_name="S1",
        )
        w.ref_hints = "refs-to: S2\nrefs-from: S3"
        output = _render_sheet(w)
        assert "refs-to: S2" in output
        assert "refs-from: S3" in output

    def test_render_sheet_no_hints(self) -> None:
        from excelmanus.window_perception.domain import SheetWindow
        from excelmanus.window_perception.renderer import _render_sheet

        w = SheetWindow.new(
            id="sheet_1", title="test", file_path="f.xlsx", sheet_name="S1",
        )
        output = _render_sheet(w)
        assert "refs-to" not in output
