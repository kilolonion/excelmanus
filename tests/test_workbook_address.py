"""Excel A1 / Sheet!A1 解析。"""

from excelmanus.workbook.address import (
    column_map_to_list,
    parse_sheet_address,
    strip_sheet_qualifier,
    top_left_cell,
)


def test_parse_bare_range() -> None:
    parsed = parse_sheet_address("A5:C5")
    assert parsed.sheet is None
    assert parsed.address == "A5:C5"


def test_parse_chinese_sheet() -> None:
    parsed = parse_sheet_address("区域汇总!A5:C5")
    assert parsed.sheet == "区域汇总"
    assert parsed.address == "A5:C5"


def test_parse_quoted_sheet_with_space() -> None:
    parsed = parse_sheet_address("'Sheet 2'!A1:B2")
    assert parsed.sheet == "Sheet 2"
    assert parsed.address == "A1:B2"


def test_parse_escaped_quotes() -> None:
    parsed = parse_sheet_address("'O''Brien'!B2")
    assert parsed.sheet == "O'Brien"
    assert parsed.address == "B2"


def test_strip_absolute_refs() -> None:
    parsed = parse_sheet_address("销售明细!$B$2:$C$13")
    assert parsed.sheet == "销售明细"
    assert parsed.address == "B2:C13"


def test_top_left_from_range() -> None:
    assert top_left_cell("C3:E10") == "C3"
    assert top_left_cell("区域汇总!A5:C5") == "A5"


def test_column_map_to_list_sparse() -> None:
    assert column_map_to_list({"A": 9, "C": 14}) == [9.0, 0.0, 14.0]


def test_strip_sheet_qualifier() -> None:
    assert strip_sheet_qualifier("'区域汇总'!A5:C5") == "A5:C5"


def test_combine_sheet_names_conflict() -> None:
    from excelmanus.workbook.address import combine_sheet_names
    import pytest

    assert combine_sheet_names("区域汇总", None) == "区域汇总"
    assert combine_sheet_names(None, "区域汇总") == "区域汇总"
    assert combine_sheet_names("区域汇总", "区域汇总") == "区域汇总"
    with pytest.raises(ValueError, match="不一致"):
        combine_sheet_names("销售明细", "区域汇总")
