"""单元格 / 区域引用规范层。"""

from __future__ import annotations

import pytest

from excelmanus.workbook.refs import (
    AreaRef,
    CellRef,
    InvalidRefError,
    NamedRef,
    RectRef,
    TableRef,
    describe_for_schema,
    parse_rect,
    parse_ref,
    reference_examples,
)


def _rect(area: AreaRef) -> RectRef:
    assert len(area.areas) == 1
    part = area.areas[0]
    assert isinstance(part, RectRef)
    return part


def test_parse_a1_cell() -> None:
    rect = _rect(parse_ref("A1"))
    assert rect.sheet is None
    assert (rect.min_row, rect.max_row, rect.min_col, rect.max_col) == (1, 1, 1, 1)
    assert parse_ref("A1").to_a1() == "A1"


def test_parse_absolute_and_mixed_dollar_flags() -> None:
    abs_cell = _rect(parse_ref("$A$1"))
    assert abs_cell.abs_min_col and abs_cell.abs_min_row
    assert parse_ref("$A$1").to_a1() == "$A$1"

    col_abs = _rect(parse_ref("$A1"))
    assert col_abs.abs_min_col and not col_abs.abs_min_row
    assert parse_ref("$A1").to_a1() == "$A1"

    row_abs = _rect(parse_ref("A$1"))
    assert row_abs.abs_min_row and not row_abs.abs_min_col
    assert parse_ref("A$1").to_a1() == "A$1"

    spanned = _rect(parse_ref("$B$2:$C$13"))
    assert spanned.abs_min_col and spanned.abs_min_row
    assert spanned.abs_max_col and spanned.abs_max_row
    assert parse_ref("$B$2:$C$13").to_a1() == "$B$2:$C$13"


def test_cell_ref_dollar_roundtrip() -> None:
    cell = CellRef(sheet=None, row=1, col=1, abs_row=True, abs_col=True)
    assert cell.to_a1() == "$A$1"
    sheet_cell = CellRef(sheet="Sheet1", row=2, col=2, abs_row=False, abs_col=True)
    assert sheet_cell.to_a1() == "Sheet1!$B2"


def test_parse_range_inclusive() -> None:
    rect = parse_rect("A1:B2")
    assert (rect.min_row, rect.max_row, rect.min_col, rect.max_col) == (1, 2, 1, 2)
    assert parse_rect("B2:A1").to_a1(include_sheet=False) == "A1:B2"


def test_parse_sheet_qualified_and_quoted() -> None:
    rect = parse_rect("Sheet1!A1:B2")
    assert rect.sheet == "Sheet1"
    assert parse_ref("Sheet1!A1:B2").to_a1() == "Sheet1!A1:B2"

    quoted = parse_rect("'My Sheet'!A1")
    assert quoted.sheet == "My Sheet"
    assert parse_ref("'My Sheet'!A1").to_a1() == "'My Sheet'!A1"

    escaped = parse_rect("'O''Brien'!B2")
    assert escaped.sheet == "O'Brien"
    assert parse_ref("'O''Brien'!B2").to_a1() == "'O''Brien'!B2"

    chinese = parse_rect("区域汇总!A5:C5")
    assert chinese.sheet == "区域汇总"
    assert (chinese.min_row, chinese.max_row, chinese.min_col, chinese.max_col) == (5, 5, 1, 3)


def test_parse_whole_column_and_row() -> None:
    col = parse_rect("A:A")
    assert col.whole_column and not col.whole_row
    assert (col.min_col, col.max_col) == (1, 1)
    assert parse_ref("A:A").is_whole_column
    assert parse_ref("A:A").to_a1() == "A:A"

    spanned = parse_rect("Sheet1!A:A")
    assert spanned.sheet == "Sheet1"
    assert spanned.whole_column
    assert parse_ref("Sheet1!A:A").to_a1() == "Sheet1!A:A"

    row = parse_rect("1:1")
    assert row.whole_row and not row.whole_column
    assert (row.min_row, row.max_row) == (1, 1)
    assert parse_ref("1:1").is_whole_row
    assert parse_ref("1:1").to_a1() == "1:1"


def test_parse_multi_area() -> None:
    area = parse_ref("A1:B2,C3:D4")
    assert len(area.areas) == 2
    assert all(isinstance(part, RectRef) for part in area.areas)
    assert area.to_a1() == "A1:B2,C3:D4"
    first, second = area.areas
    assert isinstance(first, RectRef) and isinstance(second, RectRef)
    assert (first.min_row, first.max_row, first.min_col, first.max_col) == (1, 2, 1, 2)
    assert (second.min_row, second.max_row, second.min_col, second.max_col) == (3, 4, 3, 4)


def test_parse_named_and_table_refs() -> None:
    named = parse_ref("MyName")
    assert isinstance(named.areas[0], NamedRef)
    assert named.areas[0].name == "MyName"  # type: ignore[union-attr]
    assert named.to_a1() == "MyName"

    sheet_named = parse_ref("Sheet1!MyName")
    part = sheet_named.areas[0]
    assert isinstance(part, NamedRef)
    assert part.sheet == "Sheet1" and part.name == "MyName"

    table = parse_ref("Table1[列]")
    tpart = table.areas[0]
    assert isinstance(tpart, TableRef)
    assert tpart.table == "Table1" and tpart.column == "列"
    assert table.to_a1() == "Table1[列]"

    all_spec = parse_ref("Table1[#All]")
    apart = all_spec.areas[0]
    assert isinstance(apart, TableRef)
    assert apart.column == "#All"
    assert all_spec.to_a1() == "Table1[#All]"


def test_parse_rect_rejects_multi_named_table() -> None:
    with pytest.raises(InvalidRefError, match="多区域") as multi:
        parse_rect("A1:B2,C3:D4")
    assert "错误点" in str(multi.value)
    assert "A1:B2" in str(multi.value)

    with pytest.raises(InvalidRefError, match="命名区域") as named:
        parse_rect("MyName")
    assert "正确写法" in str(named.value)

    with pytest.raises(InvalidRefError, match="表引用"):
        parse_rect("Table1[列]")


def test_invalid_input_messages_are_actionable() -> None:
    cases = [
        "",
        "   ",
        "=A1",
        "R1C1",
        "R[-1]C[2]",
        "A1:B2,",
        "A",
        "1",
        "A1:B2；C3",
        "A1:B2，C3:D4",
        "Sheet1:Sheet2!A1",
        "''!A1",
    ]
    for raw in cases:
        with pytest.raises(InvalidRefError) as caught:
            parse_ref(raw)
        message = str(caught.value)
        assert "错误点" in message, raw
        assert "正确写法" in message, raw
        assert message  # 不得静默空结果


def test_to_zero_based_inclusive_edges() -> None:
    zero = parse_rect("A1:B2").to_zero_based()
    assert (zero.start_row, zero.end_row, zero.start_col, zero.end_col) == (0, 1, 0, 1)
    assert zero.sheet is None

    sheet_zero = parse_rect("Sheet1!C4").to_zero_based()
    assert (sheet_zero.start_row, sheet_zero.start_col) == (3, 2)
    assert (sheet_zero.end_row, sheet_zero.end_col) == (3, 2)
    assert sheet_zero.sheet == "Sheet1"

    whole = parse_ref("A:A").to_zero_based()
    assert len(whole) == 1
    assert whole[0].start_row == 0
    assert whole[0].end_row == 1_048_576 - 1
    assert (whole[0].start_col, whole[0].end_col) == (0, 0)

    with pytest.raises(InvalidRefError, match="0-based"):
        parse_ref("MyName").to_zero_based()


def test_expand_clips_whole_axis_not_bounded() -> None:
    used = RectRef(sheet="Sheet1", min_row=1, max_row=5, min_col=1, max_col=3)

    clipped_col = parse_ref("A:A").expand(used)
    rect = _rect(clipped_col)
    assert (rect.min_row, rect.max_row, rect.min_col, rect.max_col) == (1, 5, 1, 1)
    assert not rect.whole_column
    assert clipped_col.to_a1() == "A1:A5"

    clipped_row = parse_ref("1:1").expand(used)
    row = _rect(clipped_row)
    assert (row.min_row, row.max_row, row.min_col, row.max_col) == (1, 1, 1, 3)
    assert clipped_row.to_a1() == "A1:C1"

    bounded = parse_ref("A1:A10").expand(used)
    kept = _rect(bounded)
    assert (kept.min_row, kept.max_row) == (1, 10)

    with pytest.raises(InvalidRefError, match="工作簿元数据"):
        parse_ref("MyName").expand(used)


def test_default_sheet_and_sheets() -> None:
    area = parse_ref("A1:B2", default_sheet="Sheet1")
    assert _rect(area).sheet == "Sheet1"
    assert area.sheets() == ("Sheet1",)

    kept = parse_ref("Other!A1", default_sheet="Sheet1")
    assert _rect(kept).sheet == "Other"
    mixed = parse_ref("Sheet1!A1,Sheet2!B2")
    assert mixed.sheets() == ("Sheet1", "Sheet2")


def test_describe_for_schema_and_examples_stable() -> None:
    examples = reference_examples()
    assert examples == (
        "A1",
        "$A$1",
        "Sheet1!A1:B2",
        "'My Sheet'!A1",
        "A:A",
        "1:1",
        "A1:B2,C3:D4",
        "Sheet1!A1,Sheet2!B1",
        "MyName",
        "Table1[列]",
    )
    assert examples == reference_examples()
    for sample in examples:
        parse_ref(sample)

    schema = describe_for_schema()
    assert schema == describe_for_schema()
    assert schema["syntax"]
    assert schema["examples"]
    assert schema["common_errors"]
    assert "、".join(examples) == schema["examples"]
    assert "format.range：单元格/矩形/同表并集" in schema["execution"]
