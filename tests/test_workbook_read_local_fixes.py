"""B1/B2/B3/B4 局部切片：读路径不臆算、header 1-based、版本绑定、筛选源行。"""

from __future__ import annotations

import csv
from pathlib import Path

from openpyxl import Workbook

from excelmanus.engine_core.tool_result import ToolResult
from excelmanus.workbook import data as data_tools
from excelmanus.workbook_commit import content_version_of_file


def _assert_uncached_formula_col(values: list[object]) -> None:
    """无缓存时只能是缺失/空，或公式原文；不能是臆算的 [4, 6]。"""
    assert values != [4, 6]
    assert values != [4.0, 6.0]
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and value.startswith("="):
            continue
        raise AssertionError(f"B 列出现臆算或缓存值: {values!r}")


def _payload(result: ToolResult) -> dict:
    assert isinstance(result.value, dict), getattr(result, "error", None) or result.model_text
    return result.value


def _write_xlsx(path: Path, rows: list[list[object]], sheet: str = "Sheet1") -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = sheet
    for row in rows:
        ws.append(row)
    wb.save(path)
    wb.close()
    return path


def _write_csv(path: Path, rows: list[list[object]]) -> Path:
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        for row in rows:
            writer.writerow(row)
    return path


class TestB1ReadDoesNotGuessFormulas:
    def test_read_excel_does_not_broadcast_first_formula(self, tmp_path: Path) -> None:
        data_tools.init_guard(str(tmp_path))
        fp = _write_xlsx(
            tmp_path / "formulas.xlsx",
            [["A", "B"], [2, "=A2*2"], [3, "=A3*3"]],
        )
        parsed = _payload(data_tools.read_excel(str(fp)))
        assert "B" in parsed["columns"]
        b_values = [row.get("B") for row in parsed.get("preview") or []]
        _assert_uncached_formula_col(b_values)
        resolved = (parsed.get("formula_resolution") or {}).get("resolved_columns") or []
        assert "B" not in resolved

    def test_filter_data_does_not_broadcast_first_formula(self, tmp_path: Path) -> None:
        data_tools.init_guard(str(tmp_path))
        fp = _write_xlsx(
            tmp_path / "formulas_filter.xlsx",
            [["A", "B"], [2, "=A2*2"], [3, "=A3*3"]],
        )
        parsed = _payload(
            data_tools.filter_data(str(fp), column="A", operator="ge", value=0),
        )
        assert parsed.get("status") != "error"
        assert "B" in parsed["columns"]
        _assert_uncached_formula_col([row.get("B") for row in parsed.get("data") or []])


class TestB3PublicHeaderRowOneBased:
    def test_header_row_1_same_on_xlsx_and_csv(self, tmp_path: Path) -> None:
        data_tools.init_guard(str(tmp_path))
        rows = [["Name", "Amount"], ["Alice", 10], ["Bob", 20]]
        xlsx = _write_xlsx(tmp_path / "same.xlsx", rows)
        csv_fp = _write_csv(tmp_path / "same.csv", rows)
        xlsx_parsed = _payload(data_tools.read_excel(str(xlsx), header_row=1))
        csv_parsed = _payload(data_tools.read_excel(str(csv_fp), header_row=1))
        assert xlsx_parsed["columns"][:2] == ["Name", "Amount"]
        assert csv_parsed["columns"][:2] == ["Name", "Amount"]
        assert "Alice" not in csv_parsed["columns"]

    def test_auto_detect_always_emits_first_row(self, tmp_path: Path) -> None:
        data_tools.init_guard(str(tmp_path))
        fp = _write_xlsx(tmp_path / "plain.xlsx", [["Name", "Amount"], ["Alice", 10]])
        parsed = _payload(data_tools.read_excel(str(fp)))
        assert parsed.get("detected_header_row") == 1
        assert (parsed.get("meta") or {}).get("header_row") == 1

    def test_auto_detect_roundtrip_xlsx_and_csv(self, tmp_path: Path) -> None:
        data_tools.init_guard(str(tmp_path))
        rows = [
            ["Report", "", "", ""],
            ["Name", "Amount", "Dept", "Note"],
            ["Alice", 10, "Sales", "x"],
            ["Bob", 20, "Ops", "y"],
        ]
        xlsx = _write_xlsx(tmp_path / "title.xlsx", rows)
        csv_fp = _write_csv(tmp_path / "title.csv", rows)
        for path in (xlsx, csv_fp):
            first = _payload(data_tools.read_excel(str(path)))
            detected = first.get("detected_header_row")
            assert detected == 2
            again = _payload(data_tools.read_excel(str(path), header_row=detected))
            assert again["columns"][:2] == ["Name", "Amount"]
            assert "Alice" not in again["columns"]


class TestB2FilterBindsObservedVersion:
    def test_filter_does_not_pair_old_data_with_new_version(
        self, tmp_path: Path, monkeypatch: object,
    ) -> None:
        data_tools.init_guard(str(tmp_path))
        fp = _write_xlsx(
            tmp_path / "amount.xlsx",
            [["Name", "Amount"], ["Alice", 10]],
        )
        old_version = content_version_of_file(fp)
        real_read = data_tools._read_df

        def _read_then_mutate(*args: object, **kwargs: object):
            df, header = real_read(*args, **kwargs)
            _write_xlsx(fp, [["Name", "Amount"], ["Alice", 999]])
            return df, header

        monkeypatch.setattr(data_tools, "_read_df", _read_then_mutate)
        result = data_tools.filter_data(
            str(fp), column="Name", operator="eq", value="Alice",
        )
        new_version = content_version_of_file(fp)
        assert new_version != old_version
        if result.success:
            parsed = _payload(result)
            assert (parsed.get("data") or [{}])[0].get("Amount") in (10, "10")
            assert parsed.get("content_version") == old_version
            assert parsed.get("content_version") != new_version
        else:
            assert result.error is not None
            assert result.error.code == "STALE_READ"


class TestB4FilterSourceRowsAndShape:
    def test_filter_source_rows_match_excel_and_kind_is_filter(self, tmp_path: Path) -> None:
        data_tools.init_guard(str(tmp_path))
        fp = _write_xlsx(
            tmp_path / "people.xlsx",
            [["Name", "Amount"], ["Alice", 10], ["Bob", 20], ["Carol", 30]],
        )
        parsed = _payload(
            data_tools.filter_data(str(fp), column="Amount", operator="gt", value=10),
        )
        assert parsed.get("status") != "error"
        names = [row.get("Name") for row in parsed.get("data") or []]
        assert names == ["Bob", "Carol"]
        assert parsed.get("source_rows") == [3, 4]
        meta = parsed.get("meta") or {}
        assert meta.get("kind") == "filter"
        assert meta.get("sampled") is False
        assert meta.get("formulas_uncached") == "unknown"
        assert meta.get("header_row") == 1

    def test_filter_truncated_marks_sampled(self, tmp_path: Path) -> None:
        data_tools.init_guard(str(tmp_path))
        fp = _write_xlsx(
            tmp_path / "many.xlsx",
            [["Name", "Amount"], ["A", 1], ["B", 2], ["C", 3]],
        )
        parsed = _payload(
            data_tools.filter_data(
                str(fp), column="Amount", operator="ge", value=1, max_rows=1,
            ),
        )
        meta = parsed.get("meta") or {}
        assert meta.get("kind") == "filter"
        assert parsed.get("truncated") is True
        assert meta.get("sampled") is False
        assert (parsed.get("coverage") or {}).get("kind") == "truncated"
        assert parsed.get("source_rows") == [2]
