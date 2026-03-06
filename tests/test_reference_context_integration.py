"""引用关系图注入系统提示词 panorama 的集成测试。"""
from __future__ import annotations

from excelmanus.reference_graph.cache import RefCache
from excelmanus.reference_graph.models import (
    RefType,
    SheetRefEdge,
    SheetRefSummary,
    WorkbookRefIndex,
)


class TestBuildRefGraphNotice:
    def test_renders_summary(self) -> None:
        from excelmanus.engine_core.context_builder import build_ref_graph_notice

        cache = RefCache()
        edge = SheetRefEdge("订单表", "产品表", RefType.FORMULA, 10, ["=VLOOKUP(...)"], [])
        idx = WorkbookRefIndex(
            file_path="test.xlsx",
            sheets={
                "订单表": SheetRefSummary("订单表", 50, [edge], [], 5, ["VLOOKUP"]),
                "产品表": SheetRefSummary("产品表", 0, [], [edge], 0, []),
            },
            cross_sheet_edges=[edge],
            external_refs=[],
            named_ranges={},
            built_at=0,
        )
        cache.put_tier1("test.xlsx", idx)
        result = build_ref_graph_notice(cache)
        assert "引用关系图" in result
        assert "订单表" in result
        assert "产品表" in result

    def test_empty_cache_returns_empty(self) -> None:
        from excelmanus.engine_core.context_builder import build_ref_graph_notice

        cache = RefCache()
        result = build_ref_graph_notice(cache)
        assert result == ""

    def test_multiple_files(self) -> None:
        from excelmanus.engine_core.context_builder import build_ref_graph_notice

        cache = RefCache()
        edge1 = SheetRefEdge("A表", "B表", RefType.FORMULA, 5, [], [])
        idx1 = WorkbookRefIndex(
            file_path="f1.xlsx",
            sheets={
                "A表": SheetRefSummary("A表", 10, [edge1], [], 0, []),
                "B表": SheetRefSummary("B表", 0, [], [edge1], 0, []),
            },
            cross_sheet_edges=[edge1],
            external_refs=[],
            named_ranges={},
            built_at=0,
        )
        idx2 = WorkbookRefIndex(
            file_path="f2.xlsx",
            sheets={"Sheet1": SheetRefSummary("Sheet1", 0, [], [], 0, [])},
            cross_sheet_edges=[],
            external_refs=[],
            named_ranges={},
            built_at=0,
        )
        cache.put_tier1("f1.xlsx", idx1)
        cache.put_tier1("f2.xlsx", idx2)
        result = build_ref_graph_notice(cache)
        assert "A表" in result
