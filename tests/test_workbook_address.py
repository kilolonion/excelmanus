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


def test_absolute_refs_preserved() -> None:
    """旧实现会剥掉 ``$``；规范层保留绝对引用标记。"""
    parsed = parse_sheet_address("销售明细!$B$2:$C$13")
    assert parsed.sheet == "销售明细"
    assert parsed.address == "$B$2:$C$13"


def test_top_left_strips_absolute_for_openpyxl() -> None:
    assert top_left_cell("$C$3:$E$10") == "C3"


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


def test_whole_column_clips_to_used_range() -> None:
    from excelmanus.workbook.address import resolve_range_to_bounds

    resolved = resolve_range_to_bounds("A:A", used_max_row=5, used_max_col=3)
    assert (resolved.min_col, resolved.min_row, resolved.max_col, resolved.max_row) == (1, 1, 1, 5)
    assert resolved.resolved == "A1:A5"
    assert resolved.clipped is True
    assert resolved.truncated is False


def test_whole_row_clips_to_used_range() -> None:
    from excelmanus.workbook.address import resolve_range_to_bounds

    resolved = resolve_range_to_bounds("1:1", used_max_row=8, used_max_col=4)
    assert (resolved.min_col, resolved.min_row, resolved.max_col, resolved.max_row) == (1, 1, 4, 1)
    assert resolved.resolved == "A1:D1"
    assert resolved.clipped is True


def test_whole_column_span_clips_rows() -> None:
    from excelmanus.workbook.address import resolve_range_to_bounds

    resolved = resolve_range_to_bounds("A:C", used_max_row=2, used_max_col=10)
    assert resolved.resolved == "A1:C2"


def test_bounded_range_not_clipped_to_used() -> None:
    from excelmanus.workbook.address import resolve_range_to_bounds

    resolved = resolve_range_to_bounds("A1:A10", used_max_row=3, used_max_col=1)
    assert resolved.resolved == "A1:A10"
    assert resolved.clipped is False
    assert resolved.truncated is False


def test_whole_column_truncates_over_cell_cap(monkeypatch) -> None:
    from excelmanus.workbook import address as address_mod

    monkeypatch.setattr(address_mod, "MAX_WHOLE_RANGE_CELLS", 3)
    resolved = address_mod.resolve_range_to_bounds("A:A", used_max_row=20, used_max_col=1)
    assert resolved.resolved == "A1:A3"
    assert resolved.truncated is True
    assert resolved.clipped is True


def test_multi_area_range_rejected_by_single_rect_resolver() -> None:
    """parse_ref 接受并集；resolve_range_to_bounds 仍只服务单矩形读路径。"""
    import pytest
    from excelmanus.workbook.address import resolve_range_to_bounds
    from excelmanus.workbook.refs import parse_ref

    parsed = parse_sheet_address("A1:B2,C1:D2")
    assert parsed.address == "A1:B2,C1:D2"
    assert parse_ref("A1:B2,C1:D2").to_a1() == "A1:B2,C1:D2"
    with pytest.raises(ValueError, match="多区域"):
        resolve_range_to_bounds("A1:B2,C1:D2", used_max_row=10, used_max_col=10)
