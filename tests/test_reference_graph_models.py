"""reference_graph.models 数据模型测试。"""
from __future__ import annotations

import pytest


class TestRefType:
    def test_all_types_exist(self) -> None:
        from excelmanus.reference_graph.models import RefType

        expected = {"formula", "data_link", "validation", "cond_format", "named_range", "external"}
        assert {t.value for t in RefType} == expected

    def test_is_string_enum(self) -> None:
        from excelmanus.reference_graph.models import RefType

        assert isinstance(RefType.FORMULA, str)
        assert RefType.FORMULA == "formula"


class TestCellRef:
    def test_same_sheet_ref(self) -> None:
        from excelmanus.reference_graph.models import CellRef

        ref = CellRef(cell_or_range="A1")
        assert ref.file_path is None
        assert ref.sheet_name is None
        assert ref.cell_or_range == "A1"
        assert ref.is_absolute_row is False
        assert ref.is_absolute_col is False

    def test_cross_sheet_ref(self) -> None:
        from excelmanus.reference_graph.models import CellRef

        ref = CellRef(sheet_name="Sheet2", cell_or_range="A1:C10")
        assert ref.sheet_name == "Sheet2"

    def test_external_ref(self) -> None:
        from excelmanus.reference_graph.models import CellRef

        ref = CellRef(file_path="other.xlsx", sheet_name="Data", cell_or_range="B2")
        assert ref.file_path == "other.xlsx"

    def test_display(self) -> None:
        from excelmanus.reference_graph.models import CellRef

        ref = CellRef(sheet_name="Sheet2", cell_or_range="A1:C10")
        assert ref.display() == "Sheet2!A1:C10"

        ref2 = CellRef(cell_or_range="B5")
        assert ref2.display() == "B5"

        ref3 = CellRef(file_path="book.xlsx", sheet_name="S1", cell_or_range="D1")
        assert ref3.display() == "[book.xlsx]S1!D1"


class TestSheetRefEdge:
    def test_basic_fields(self) -> None:
        from excelmanus.reference_graph.models import RefType, SheetRefEdge

        edge = SheetRefEdge(
            source_sheet="订单表",
            target_sheet="产品表",
            ref_type=RefType.FORMULA,
            ref_count=50,
            sample_formulas=["=VLOOKUP(A2,产品表!A:C,2,0)"],
            column_pairs=[("A", "A")],
        )
        assert edge.source_sheet == "订单表"
        assert edge.ref_count == 50


class TestSheetRefSummary:
    def test_basic(self) -> None:
        from excelmanus.reference_graph.models import SheetRefSummary

        s = SheetRefSummary(
            sheet_name="Sheet1",
            formula_count=100,
            outgoing_refs=[],
            incoming_refs=[],
            self_refs=20,
            formula_patterns=["VLOOKUP", "SUM"],
        )
        assert s.formula_count == 100
        assert s.formula_patterns == ["VLOOKUP", "SUM"]


class TestWorkbookRefIndex:
    def test_render_summary(self) -> None:
        from excelmanus.reference_graph.models import (
            RefType,
            SheetRefEdge,
            SheetRefSummary,
            WorkbookRefIndex,
        )

        edge = SheetRefEdge(
            source_sheet="订单表",
            target_sheet="产品表",
            ref_type=RefType.FORMULA,
            ref_count=50,
            sample_formulas=["=VLOOKUP(...)"],
            column_pairs=[],
        )
        idx = WorkbookRefIndex(
            file_path="test.xlsx",
            sheets={
                "订单表": SheetRefSummary("订单表", 50, [edge], [], 10, ["VLOOKUP"]),
                "产品表": SheetRefSummary("产品表", 0, [], [edge], 0, []),
            },
            cross_sheet_edges=[edge],
            external_refs=[],
            named_ranges={},
            built_at=0.0,
        )
        summary = idx.render_summary()
        assert "订单表" in summary
        assert "产品表" in summary

    def test_empty_workbook(self) -> None:
        from excelmanus.reference_graph.models import WorkbookRefIndex

        idx = WorkbookRefIndex(
            file_path="empty.xlsx",
            sheets={},
            cross_sheet_edges=[],
            external_refs=[],
            named_ranges={},
            built_at=0.0,
        )
        summary = idx.render_summary()
        assert summary == ""


class TestCellNode:
    def test_basic(self) -> None:
        from excelmanus.reference_graph.models import CellNode, CellRef

        node = CellNode(
            sheet="Sheet1",
            address="D5",
            formula="=A5*B5+C5",
            precedents=[
                CellRef(cell_or_range="A5"),
                CellRef(cell_or_range="B5"),
                CellRef(cell_or_range="C5"),
            ],
            dependents=[],
        )
        assert len(node.precedents) == 3
        assert node.formula == "=A5*B5+C5"
