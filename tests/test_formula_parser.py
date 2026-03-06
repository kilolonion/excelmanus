"""FormulaRefExtractor 测试。"""
from __future__ import annotations

import pytest

from excelmanus.reference_graph.formula_parser import FormulaRefExtractor


@pytest.fixture()
def extractor() -> FormulaRefExtractor:
    return FormulaRefExtractor()


class TestSimpleRefs:
    def test_single_cell(self, extractor: FormulaRefExtractor) -> None:
        refs = extractor.extract("=A1+B2")
        displays = sorted(r.display() for r in refs)
        assert "A1" in displays
        assert "B2" in displays

    def test_range(self, extractor: FormulaRefExtractor) -> None:
        refs = extractor.extract("=SUM(A1:A10)")
        assert any(r.cell_or_range == "A1:A10" for r in refs)

    def test_absolute_ref(self, extractor: FormulaRefExtractor) -> None:
        refs = extractor.extract("=$A$1+$B2+C$3")
        displays = sorted(r.display() for r in refs)
        assert len(refs) == 3

    def test_no_formula(self, extractor: FormulaRefExtractor) -> None:
        refs = extractor.extract("hello world")
        assert refs == []

    def test_empty(self, extractor: FormulaRefExtractor) -> None:
        refs = extractor.extract("")
        assert refs == []


class TestCrossSheetRefs:
    def test_simple_cross_sheet(self, extractor: FormulaRefExtractor) -> None:
        refs = extractor.extract("=Sheet2!A1")
        assert len(refs) == 1
        assert refs[0].sheet_name == "Sheet2"
        assert refs[0].cell_or_range == "A1"

    def test_cross_sheet_range(self, extractor: FormulaRefExtractor) -> None:
        refs = extractor.extract("=VLOOKUP(A1,Sheet2!A1:C100,2,0)")
        sheets = {r.sheet_name for r in refs if r.sheet_name}
        assert "Sheet2" in sheets

    def test_quoted_sheet_name(self, extractor: FormulaRefExtractor) -> None:
        refs = extractor.extract("='My Sheet'!A1:B10")
        assert any(r.sheet_name == "My Sheet" for r in refs)

    def test_sheet_with_chinese(self, extractor: FormulaRefExtractor) -> None:
        refs = extractor.extract("=产品表!A1")
        assert any(r.sheet_name == "产品表" for r in refs)

    def test_quoted_chinese_sheet(self, extractor: FormulaRefExtractor) -> None:
        refs = extractor.extract("='价格 表'!C2:C100")
        assert any(r.sheet_name == "价格 表" for r in refs)


class TestExternalRefs:
    def test_external_book(self, extractor: FormulaRefExtractor) -> None:
        refs = extractor.extract("=[Book1.xlsx]Sheet1!A1")
        assert len(refs) >= 1
        ext = [r for r in refs if r.file_path]
        assert len(ext) == 1
        assert ext[0].file_path == "Book1.xlsx"
        assert ext[0].sheet_name == "Sheet1"


class TestWholeColumnRow:
    def test_whole_column(self, extractor: FormulaRefExtractor) -> None:
        refs = extractor.extract("=SUM(A:A)")
        assert any(r.cell_or_range == "A:A" for r in refs)

    def test_cross_sheet_whole_column(self, extractor: FormulaRefExtractor) -> None:
        refs = extractor.extract("=VLOOKUP(A1,Sheet2!A:C,2,0)")
        cross = [r for r in refs if r.sheet_name == "Sheet2"]
        assert len(cross) >= 1


class TestFunctionExtraction:
    def test_extract_functions(self, extractor: FormulaRefExtractor) -> None:
        funcs = extractor.extract_functions("=VLOOKUP(A1,Sheet2!A:C,2,0)")
        assert "VLOOKUP" in funcs

    def test_nested_functions(self, extractor: FormulaRefExtractor) -> None:
        funcs = extractor.extract_functions("=IF(A1>0,VLOOKUP(A1,B:C,2,0),SUM(D:D))")
        assert {"IF", "VLOOKUP", "SUM"} <= set(funcs)

    def test_no_functions(self, extractor: FormulaRefExtractor) -> None:
        funcs = extractor.extract_functions("=A1+B1*C1")
        assert funcs == []


class TestDedup:
    def test_no_duplicate_refs(self, extractor: FormulaRefExtractor) -> None:
        refs = extractor.extract("=A1+A1+A1")
        assert len(refs) == 1

    def test_range_and_cell_kept_separate(self, extractor: FormulaRefExtractor) -> None:
        refs = extractor.extract("=A1+SUM(A1:A10)")
        assert len(refs) == 2


class TestComplexFormulas:
    def test_sumifs(self, extractor: FormulaRefExtractor) -> None:
        refs = extractor.extract('=SUMIFS(订单表!D:D,订单表!A:A,A2,订单表!B:B,">=2024-01-01")')
        cross = [r for r in refs if r.sheet_name == "订单表"]
        assert len(cross) >= 2

    def test_index_match(self, extractor: FormulaRefExtractor) -> None:
        refs = extractor.extract("=INDEX(Sheet2!B:B,MATCH(A1,Sheet2!A:A,0))")
        cross = [r for r in refs if r.sheet_name == "Sheet2"]
        assert len(cross) >= 2


class TestAddressInRef:
    def test_exact_match(self) -> None:
        from excelmanus.reference_graph.formula_parser import address_in_ref
        assert address_in_ref("A1", "A1") is True
        assert address_in_ref("A1", "A2") is False

    def test_no_false_positive_on_prefix(self) -> None:
        from excelmanus.reference_graph.formula_parser import address_in_ref
        assert address_in_ref("A1", "A10") is False
        assert address_in_ref("B2", "B20") is False

    def test_whole_column_range(self) -> None:
        from excelmanus.reference_graph.formula_parser import address_in_ref
        assert address_in_ref("B5", "A:C") is True
        assert address_in_ref("D5", "A:C") is False
        assert address_in_ref("A1", "A:A") is True

    def test_cell_range(self) -> None:
        from excelmanus.reference_graph.formula_parser import address_in_ref
        assert address_in_ref("B5", "A1:C10") is True
        assert address_in_ref("D5", "A1:C10") is False
        assert address_in_ref("B15", "A1:C10") is False
        assert address_in_ref("A1", "A1:C10") is True
        assert address_in_ref("C10", "A1:C10") is True

    def test_no_range(self) -> None:
        from excelmanus.reference_graph.formula_parser import address_in_ref
        assert address_in_ref("A1", "B2") is False
