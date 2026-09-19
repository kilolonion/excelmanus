"""Word tool real-I/O regression tests."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from docx import Document
from docx.enum.section import WD_ORIENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor

from excelmanus.database import Database
from excelmanus.file_registry import FileRegistry
from excelmanus.security.guard import FileAccessGuard
from excelmanus.tools._guard_ctx import reset_guard, set_guard
from excelmanus.engine_core.tool_result import ToolResult
from excelmanus.workbook_commit import content_version_of_file
from excelmanus.tools.word_tools import (
    _ensure_docx,
    _heading_level,
    _resolve_path,
    _run_to_dict,
    apply_word_operations,
    get_tools,
    inspect_word,
    read_word,
    search_word,
    write_word,
)



def _payload(result: ToolResult) -> dict:
    assert isinstance(result, ToolResult)
    assert isinstance(result.value, dict)
    return result.value

@pytest.fixture(autouse=True)
def _set_guard(tmp_path: Path):
    token = set_guard(FileAccessGuard(str(tmp_path)))
    try:
        yield
    finally:
        reset_guard(token)


def _make_test_doc(path, paragraphs=None):
    doc = Document()
    for text in (paragraphs or ["Hello", "World"]):
        doc.add_paragraph(text)
    doc.save(str(path))
    return path


def _paragraph_texts(path: Path) -> list[str]:
    return [paragraph.text for paragraph in Document(path).paragraphs]


def _write(path: Path, operations: list[dict]) -> dict:
    return _payload(
        write_word(
            path.name,
            operations=operations,
            expected_version=content_version_of_file(path),
        )
    )


def _make_xlsx(path: Path, rows: list[list], sheet: str | None = None) -> Path:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    if sheet:
        ws.title = sheet
    for r_i, row in enumerate(rows, start=1):
        for c_i, value in enumerate(row, start=1):
            ws.cell(row=r_i, column=c_i, value=value)
    wb.save(path)
    return path


def _table_data(path: Path, index: int = 0) -> list[list[str]]:
    table = Document(path).tables[index]
    return [[cell.text for cell in row.cells] for row in table.rows]


def _fill_table(table, rows: list[list[str]]) -> None:
    for r_i, row in enumerate(rows):
        for c_i, value in enumerate(row):
            table.cell(r_i, c_i).text = value


class TestReadWord:
    def test_basic_read(self, tmp_path: Path) -> None:
        path = _make_test_doc(tmp_path / "report.docx", ["Intro", "Summary"])
        doc = Document(path)
        table = doc.add_table(rows=2, cols=2)
        table.cell(0, 0).text = "Metric"
        table.cell(0, 1).text = "Value"
        table.cell(1, 0).text = "Revenue"
        table.cell(1, 1).text = "42"
        doc.save(path)

        result = _payload(read_word(path.name))

        assert result["file_path"] == path.name
        assert result["total_paragraphs"] == 2
        assert result["returned"] == 2
        assert result["paragraphs"][0]["text"] == "Intro"
        assert result["total_tables"] == 1
        assert result["tables"][0]["data"][1] == ["Revenue", "42"]

    def test_offset_pagination(self, tmp_path: Path) -> None:
        path = _make_test_doc(tmp_path / "paged.docx", ["A", "B", "C"])

        result = _payload(read_word(path.name, offset=1, max_paragraphs=1))

        assert result["offset"] == 1
        assert result["returned"] == 1
        assert result["truncated"] is True
        assert result["paragraphs"] == [{"index": 1, "text": "B", "style": "Normal"}]

    def test_include_format(self, tmp_path: Path) -> None:
        path = tmp_path / "format.docx"
        doc = Document()
        para = doc.add_paragraph()
        run = para.add_run("Styled")
        run.bold = True
        run.italic = True
        run.font.size = Pt(12)
        run.font.name = "Calibri"
        run.font.color.rgb = RGBColor(0x11, 0x22, 0x33)
        doc.save(path)

        result = _payload(read_word(path.name, include_format=True))

        run_data = result["paragraphs"][0]["runs"][0]
        assert run_data["text"] == "Styled"
        assert run_data["format"]["bold"] is True
        assert run_data["format"]["italic"] is True
        assert run_data["format"]["size_pt"] == 12.0
        assert run_data["format"]["font"] == "Calibri"
        assert run_data["format"]["color"] == "112233"

    def test_include_tables_false_omits_tables(self, tmp_path: Path) -> None:
        path = _make_test_doc(tmp_path / "no_tables.docx")
        doc = Document(path)
        doc.add_table(rows=1, cols=1).cell(0, 0).text = "hidden"
        doc.save(path)

        result = _payload(read_word(path.name, include_tables=False))

        assert "tables" not in result
        assert "total_tables" not in result

    def test_empty_document(self, tmp_path: Path) -> None:
        path = tmp_path / "empty.docx"
        Document().save(path)

        result = _payload(read_word(path.name))

        assert result["total_paragraphs"] == 0
        assert result["returned"] == 0
        assert result["paragraphs"] == []
        assert result["truncated"] is False


class TestWriteWord:
    def test_replace(self, tmp_path: Path) -> None:
        path = _make_test_doc(tmp_path / "replace.docx")

        result = _write(path, [{"action": "replace", "index": 0, "text": "Updated"}])

        assert result["applied"] == ["replace paragraph 0"]
        assert _paragraph_texts(path) == ["Updated", "World"]

    def test_insert_after_regression(self, tmp_path: Path) -> None:
        path = _make_test_doc(tmp_path / "insert.docx")

        result = _write(path, [{"action": "insert_after", "index": 0, "text": "Inserted"}])

        assert result["applied_count"] == 1
        assert "errors" not in result
        assert _paragraph_texts(path) == ["Hello", "Inserted", "World"]

    def test_append(self, tmp_path: Path) -> None:
        path = _make_test_doc(tmp_path / "append.docx")

        result = _write(path, [{"action": "append", "text": "Tail"}])

        assert result["applied"] == ["append paragraph"]
        assert _paragraph_texts(path) == ["Hello", "World", "Tail"]

    def test_delete(self, tmp_path: Path) -> None:
        path = _make_test_doc(tmp_path / "delete.docx")

        result = _write(path, [{"action": "delete", "index": 0}])

        assert result["applied"] == ["delete paragraph 0"]
        assert _paragraph_texts(path) == ["World"]

    def test_out_of_range_index_returns_error(self, tmp_path: Path) -> None:
        path = _make_test_doc(tmp_path / "bounds.docx")

        result = _write(path, [{"action": "replace", "index": 9, "text": "Never"}])

        assert result["applied_count"] == 0
        assert "超出范围" in result["errors"][0]
        assert _paragraph_texts(path) == ["Hello", "World"]

    def test_multiple_operations_batch(self, tmp_path: Path) -> None:
        path = _make_test_doc(tmp_path / "batch.docx", ["A", "B"])

        result = _write(
            path,
            [
                {"action": "replace", "index": 0, "text": "A1"},
                {"action": "insert_after", "index": 0, "text": "A2"},
                {"action": "delete", "index": 2},
                {"action": "append", "text": "C"},
            ],
        )

        assert result["applied_count"] == 4
        assert _paragraph_texts(path) == ["A1", "A2", "C"]

    def test_write_requires_expected_version(self, tmp_path: Path) -> None:
        path = _make_test_doc(tmp_path / "need_ver.docx")
        result = _payload(
            write_word(path.name, operations=[{"action": "append", "text": "Nope"}])
        )
        assert result.get("error_code") == "VERSION_CONFLICT"
        assert _paragraph_texts(path) == ["Hello", "World"]

    def test_partial_errors_do_not_save(self, tmp_path: Path) -> None:
        path = _make_test_doc(tmp_path / "partial.docx")
        result = _write(
            path,
            [
                {"action": "replace", "index": 0, "text": "Changed"},
                {"action": "replace", "index": 9, "text": "Never"},
            ],
        )
        assert "errors" in result
        assert _paragraph_texts(path) == ["Hello", "World"]

    def test_replace_preserves_first_run_bold(self, tmp_path: Path) -> None:
        path = tmp_path / "bold_replace.docx"
        doc = Document()
        para = doc.add_paragraph()
        run = para.add_run("Old bold")
        run.bold = True
        doc.save(path)

        _write(path, [{"action": "replace", "index": 0, "text": "New bold"}])

        result = _payload(read_word(path.name, include_format=True))
        paragraph = result["paragraphs"][0]
        assert paragraph["text"] == "New bold"
        assert paragraph["runs"][0]["format"]["bold"] is True

    def test_replace_keeps_heading_style_when_style_omitted(self, tmp_path: Path) -> None:
        path = tmp_path / "heading_keep.docx"
        doc = Document()
        doc.add_heading("Old heading", level=1)
        doc.save(path)

        _write(path, [{"action": "replace", "index": 0, "text": "New heading"}])

        result = _payload(read_word(path.name))
        paragraph = result["paragraphs"][0]
        assert paragraph["text"] == "New heading"
        assert paragraph["style"] == "Heading 1"
        assert paragraph["heading_level"] == 1

    def test_replace_with_style_changes_paragraph_style(self, tmp_path: Path) -> None:
        path = tmp_path / "heading_to_normal.docx"
        doc = Document()
        doc.add_heading("Old heading", level=1)
        doc.save(path)

        _write(
            path,
            [{"action": "replace", "index": 0, "text": "Plain", "style": "Normal"}],
        )

        result = _payload(read_word(path.name))
        paragraph = result["paragraphs"][0]
        assert paragraph["text"] == "Plain"
        assert paragraph["style"] == "Normal"
        assert "heading_level" not in paragraph

    def test_replace_collapses_multiple_runs(self, tmp_path: Path) -> None:
        path = tmp_path / "multi_run.docx"
        doc = Document()
        para = doc.add_paragraph()
        para.add_run("Hello ")
        para.add_run("World")
        doc.save(path)

        _write(path, [{"action": "replace", "index": 0, "text": "Hi"}])

        loaded = Document(path)
        paragraph = loaded.paragraphs[0]
        assert paragraph.text == "Hi"
        nonempty = [run for run in paragraph.runs if run.text]
        assert len(nonempty) == 1


class TestWriteWordReplaceTable:
    def test_expands_rows_and_preserves_style(self, tmp_path: Path) -> None:
        path = tmp_path / "expand.docx"
        doc = Document()
        table = doc.add_table(rows=2, cols=2)
        table.style = "Table Grid"
        _fill_table(table, [["a", "b"], ["c", "d"]])
        style_name = table.style.name
        doc.save(path)
        _make_xlsx(
            tmp_path / "src.xlsx",
            [["H1", "H2"], ["1", "2"], ["3", "4"]],
        )

        result = _write(
            path,
            [{
                "action": "replace_table",
                "table_index": 0,
                "source_file": "src.xlsx",
                "source_range": "A1:B3",
            }],
        )

        assert result["status"] == "success"
        applied = result["applied"][0]
        assert applied.startswith("replace_table table=0 3x2 from=")
        assert applied.endswith("!A1:B3")
        loaded = Document(path)
        assert loaded.tables[0].style.name == style_name
        assert _table_data(path) == [["H1", "H2"], ["1", "2"], ["3", "4"]]

    def test_shrinks_columns_without_leftover_text(self, tmp_path: Path) -> None:
        path = tmp_path / "shrink.docx"
        doc = Document()
        table = doc.add_table(rows=3, cols=3)
        _fill_table(
            table,
            [["A", "B", "C"], ["D", "E", "F"], ["G", "H", "I"]],
        )
        doc.save(path)
        _make_xlsx(tmp_path / "src.xlsx", [["1", "2"], ["3", "4"]])

        result = _write(
            path,
            [{
                "action": "replace_table",
                "table_index": 0,
                "source_file": "src.xlsx",
                "source_range": "A1:B2",
            }],
        )

        assert "errors" not in result
        loaded = Document(path).tables[0]
        assert len(loaded.rows) == 2
        assert len(loaded.columns) == 2
        data = _table_data(path)
        assert data == [["1", "2"], ["3", "4"]]
        flat = [cell for row in data for cell in row]
        assert "C" not in flat
        assert "F" not in flat
        assert "I" not in flat
        xml = loaded._tbl.xml
        assert ">C<" not in xml
        assert ">F<" not in xml
        assert ">I<" not in xml

    def test_locate_by_caption(self, tmp_path: Path) -> None:
        path = tmp_path / "caption.docx"
        doc = Document()
        doc.add_paragraph("表1：销售")
        table = doc.add_table(rows=1, cols=1)
        table.cell(0, 0).text = "old"
        doc.save(path)
        _make_xlsx(tmp_path / "src.xlsx", [["new"]])

        result = _write(
            path,
            [{
                "action": "replace_table",
                "caption": "表1：销售",
                "source_file": "src.xlsx",
            }],
        )

        assert result["status"] == "success"
        assert "table=0" in result["applied"][0]
        assert _table_data(path) == [["new"]]

    def test_caption_ambiguous_lists_candidates(self, tmp_path: Path) -> None:
        path = tmp_path / "caption_ambiguous.docx"
        doc = Document()
        doc.add_paragraph("表1：销售")
        doc.add_table(rows=1, cols=1).cell(0, 0).text = "t0"
        doc.add_paragraph("表2：销售汇总")
        doc.add_table(rows=1, cols=1).cell(0, 0).text = "t1"
        doc.save(path)
        _make_xlsx(tmp_path / "src.xlsx", [["x"]])

        result = _write(
            path,
            [{
                "action": "replace_table",
                "caption": "销售",
                "source_file": "src.xlsx",
            }],
        )

        assert "errors" in result
        error = result["errors"][0]
        assert "命中多个" in error
        assert "0=" in error
        assert "1=" in error
        assert "表1：销售" in error
        assert "表2：销售汇总" in error
        assert _table_data(path, 0) == [["t0"]]
        assert _table_data(path, 1) == [["t1"]]

    def test_caption_not_found_lists_available(self, tmp_path: Path) -> None:
        path = tmp_path / "caption_miss.docx"
        doc = Document()
        doc.add_paragraph("表1：销售")
        doc.add_table(rows=1, cols=1).cell(0, 0).text = "t0"
        doc.save(path)
        _make_xlsx(tmp_path / "src.xlsx", [["x"]])

        result = _write(
            path,
            [{
                "action": "replace_table",
                "caption": "利润",
                "source_file": "src.xlsx",
            }],
        )

        assert "errors" in result
        error = result["errors"][0]
        assert "未命中" in error
        assert "表1：销售" in error
        assert _table_data(path) == [["t0"]]

    def test_source_range_defaults_to_used_range(self, tmp_path: Path) -> None:
        path = tmp_path / "used_range.docx"
        doc = Document()
        doc.add_table(rows=1, cols=1).cell(0, 0).text = "old"
        doc.save(path)
        _make_xlsx(
            tmp_path / "src.xlsx",
            [["A1", "B1"], ["A2", "B2"]],
            sheet="Data",
        )

        result = _write(
            path,
            [{
                "action": "replace_table",
                "table_index": 0,
                "source_file": "src.xlsx",
                "source_sheet": "Data",
            }],
        )

        assert result["status"] == "success"
        assert "from=Data!A1:B2" in result["applied"][0]
        assert _table_data(path) == [["A1", "B1"], ["A2", "B2"]]

    def test_uncached_formulas_reported(self, tmp_path: Path) -> None:
        path = tmp_path / "formula.docx"
        doc = Document()
        table = doc.add_table(rows=1, cols=3)
        _fill_table(table, [["", "", ""]])
        doc.save(path)
        _make_xlsx(tmp_path / "src.xlsx", [[1, 2, "=A1+B1"]])

        result = _write(
            path,
            [{
                "action": "replace_table",
                "table_index": 0,
                "source_file": "src.xlsx",
                "source_range": "A1:C1",
            }],
        )

        assert result["status"] == "success"
        assert result["formulas_uncached"] >= 1
        assert "uncertainties" in result
        assert any("formulas_uncached" in item for item in result["uncertainties"])
        assert _table_data(path) == [["1", "2", ""]]

    def test_merged_cells_rejected_without_write(self, tmp_path: Path) -> None:
        path = tmp_path / "merged.docx"
        doc = Document()
        table = doc.add_table(rows=2, cols=2)
        table.cell(0, 0).text = "KEEP-A"
        table.cell(0, 1).text = "KEEP-B"
        table.cell(1, 0).text = "KEEP-C"
        table.cell(1, 1).text = "KEEP-D"
        table.cell(0, 0).merge(table.cell(0, 1))
        doc.save(path)
        before = Document(path).tables[0]._tbl.xml
        _make_xlsx(tmp_path / "src.xlsx", [["x", "y"], ["z", "w"]])

        result = _write(
            path,
            [{
                "action": "replace_table",
                "table_index": 0,
                "source_file": "src.xlsx",
            }],
        )

        assert "errors" in result
        assert "含合并单元格" in result["errors"][0]
        assert "run_code" in result["errors"][0]
        after = Document(path)
        assert "KEEP-C" in after.tables[0].cell(1, 0).text
        assert after.tables[0]._tbl.xml == before

    def test_number_format(self, tmp_path: Path) -> None:
        path = tmp_path / "numbers.docx"
        doc = Document()
        doc.add_table(rows=1, cols=3)
        doc.save(path)
        _make_xlsx(tmp_path / "src.xlsx", [[42, 42.5, 42.0]])

        result = _write(
            path,
            [{
                "action": "replace_table",
                "table_index": 0,
                "source_file": "src.xlsx",
                "source_range": "A1:C1",
            }],
        )

        assert result["status"] == "success"
        assert _table_data(path) == [["42", "42.5", "42"]]


def _insert_bookmark(paragraph, name: str, text: str, bookmark_id: int) -> None:
    start = OxmlElement("w:bookmarkStart")
    start.set(qn("w:id"), str(bookmark_id))
    start.set(qn("w:name"), name)
    run = paragraph.add_run(text)
    end = OxmlElement("w:bookmarkEnd")
    end.set(qn("w:id"), str(bookmark_id))
    run._element.addprevious(start)
    run._element.addnext(end)


class TestWriteWordFillTemplate:
    def test_placeholder_basic(self, tmp_path: Path) -> None:
        path = _make_test_doc(tmp_path / "tpl.docx", ["尊敬的{{客户}}，您好"])

        result = _write(
            path,
            [{"action": "fill_template", "values": {"客户": "张三"}}],
        )

        assert result["status"] == "success"
        assert result["applied"][0].startswith("fill_template filled=1")
        assert result["filled_keys"] == ["客户"]
        assert result["unfilled_keys"] == []
        assert result["bookmarks_filled"] == 0
        assert _paragraph_texts(path)[0] == "尊敬的张三，您好"

    def test_placeholder_cross_run_preserves_surrounding_bold(self, tmp_path: Path) -> None:
        path = tmp_path / "cross_run.docx"
        doc = Document()
        para = doc.add_paragraph()
        prefix = para.add_run("前缀")
        prefix.bold = True
        para.add_run("{{客")
        para.add_run("户}}")
        suffix = para.add_run("后缀")
        suffix.bold = True
        doc.save(path)

        result = _write(
            path,
            [{"action": "fill_template", "values": {"客户": "李四"}}],
        )

        assert result["status"] == "success"
        loaded = Document(path).paragraphs[0]
        assert loaded.text == "前缀李四后缀"
        runs = list(loaded.runs)
        assert runs[0].text == "前缀"
        assert runs[0].bold is True
        assert runs[-1].text == "后缀"
        assert runs[-1].bold is True
        assert "李四" in "".join(run.text or "" for run in runs)

    def test_placeholder_in_table_cell_including_extra_paragraph(self, tmp_path: Path) -> None:
        path = tmp_path / "table_tpl.docx"
        doc = Document()
        table = doc.add_table(rows=1, cols=1)
        cell = table.cell(0, 0)
        cell.paragraphs[0].add_run("客户：{{客户}}")
        cell.add_paragraph("金额：{{金额}}")
        doc.save(path)

        result = _write(
            path,
            [{"action": "fill_template", "values": {"客户": "王五", "金额": "42"}}],
        )

        assert result["status"] == "success"
        assert set(result["filled_keys"]) == {"客户", "金额"}
        data = _table_data(path)
        assert "王五" in data[0][0]
        assert "42" in data[0][0]
        paras = Document(path).tables[0].cell(0, 0).paragraphs
        assert paras[0].text == "客户：王五"
        assert paras[1].text == "金额：42"

    def test_bookmark_fill(self, tmp_path: Path) -> None:
        path = tmp_path / "bookmark.docx"
        doc = Document()
        _insert_bookmark(doc.add_paragraph(), "客户", "旧名", 1)
        _insert_bookmark(doc.add_paragraph(), "客户", "另一处", 2)
        doc.save(path)

        result = _write(
            path,
            [{"action": "fill_template", "values": {"客户": "赵六"}}],
        )

        assert result["status"] == "success"
        assert result["bookmarks_filled"] == 2
        assert result["filled_keys"] == ["客户"]
        texts = [t for t in _paragraph_texts(path) if t]
        assert texts == ["赵六", "赵六"]

    def test_unfilled_keys_reported_and_left_in_place(self, tmp_path: Path) -> None:
        path = _make_test_doc(
            tmp_path / "unfilled.docx",
            ["{{客户}} / {{备注}}"],
        )

        result = _write(
            path,
            [{"action": "fill_template", "values": {"客户": "周七"}}],
        )

        assert result["status"] == "success"
        assert result["filled_keys"] == ["客户"]
        assert result["unfilled_keys"] == ["备注"]
        assert _paragraph_texts(path)[0] == "周七 / {{备注}}"

    def test_row_source_from_xlsx(self, tmp_path: Path) -> None:
        path = _make_test_doc(
            tmp_path / "row_tpl.docx",
            ["{{客户}}|{{金额}}|{{日期}}"],
        )
        when = datetime(2024, 1, 15, 10, 30, 0)
        _make_xlsx(
            tmp_path / "roster.xlsx",
            [["客户", "金额", "日期"], [None, 42.0, when]],
        )

        result = _write(
            path,
            [{
                "action": "fill_template",
                "source_file": "roster.xlsx",
                "source_row": 2,
            }],
        )

        assert result["status"] == "success"
        assert result["filled_keys"] == ["客户", "金额", "日期"]
        assert _paragraph_texts(path)[0] == f"|42|{when.isoformat()}"

    def test_duplicate_header_rejected(self, tmp_path: Path) -> None:
        path = _make_test_doc(tmp_path / "dup.docx", ["{{客户}}"])
        _make_xlsx(
            tmp_path / "dup.xlsx",
            [["客户", "客户"], ["a", "b"]],
        )
        before = path.read_bytes()

        result = _write(
            path,
            [{
                "action": "fill_template",
                "source_file": "dup.xlsx",
                "source_row": 2,
            }],
        )

        assert "errors" in result
        assert "表头重复" in result["errors"][0]
        assert path.read_bytes() == before

    def test_values_and_source_are_mutex(self, tmp_path: Path) -> None:
        path = _make_test_doc(tmp_path / "mutex.docx", ["{{客户}}"])
        _make_xlsx(tmp_path / "src.xlsx", [["客户"], ["张三"]])

        both = _write(
            path,
            [{
                "action": "fill_template",
                "values": {"客户": "A"},
                "source_file": "src.xlsx",
                "source_row": 2,
            }],
        )
        neither = _write(path, [{"action": "fill_template"}])

        assert "恰好" in both["errors"][0]
        assert "恰好" in neither["errors"][0]
        assert _paragraph_texts(path)[0] == "{{客户}}"

    def test_output_file_creates_new_without_touching_source(self, tmp_path: Path) -> None:
        path = _make_test_doc(tmp_path / "template.docx", ["{{客户}}"])
        before = path.read_bytes()

        result = _payload(
            write_word(
                path.name,
                operations=[{"action": "fill_template", "values": {"客户": "钱八"}}],
                output_file="out/row1.docx",
            )
        )

        assert result["status"] == "success"
        assert result["output_file"] == "out/row1.docx"
        assert path.read_bytes() == before
        assert _paragraph_texts(path) == ["{{客户}}"]
        out = tmp_path / "out" / "row1.docx"
        assert out.is_file()
        assert _paragraph_texts(out) == ["钱八"]

    def test_output_file_exists_without_version_conflicts(self, tmp_path: Path) -> None:
        path = _make_test_doc(tmp_path / "template.docx", ["{{客户}}"])
        out = tmp_path / "exists.docx"
        Document().save(out)
        before_src = path.read_bytes()
        before_out = out.read_bytes()

        result = _payload(
            write_word(
                path.name,
                operations=[{"action": "fill_template", "values": {"客户": "孙九"}}],
                output_file="exists.docx",
            )
        )

        assert result.get("error_code") == "VERSION_CONFLICT"
        assert path.read_bytes() == before_src
        assert out.read_bytes() == before_out

    def test_output_file_overwrite_with_version(self, tmp_path: Path) -> None:
        path = _make_test_doc(tmp_path / "template.docx", ["{{客户}}"])
        out = tmp_path / "exists.docx"
        Document().save(out)
        out_ver = content_version_of_file(out)
        src_before = path.read_bytes()

        result = _payload(
            write_word(
                path.name,
                operations=[{"action": "fill_template", "values": {"客户": "吴十"}}],
                output_file="exists.docx",
                output_expected_version=out_ver,
            )
        )

        assert result["status"] == "success"
        assert result["output_file"] == "exists.docx"
        assert path.read_bytes() == src_before
        assert _paragraph_texts(out) == ["吴十"]

    def test_inplace_still_requires_expected_version(self, tmp_path: Path) -> None:
        path = _make_test_doc(tmp_path / "need_ver.docx", ["{{客户}}"])
        result = _payload(
            write_word(
                path.name,
                operations=[{"action": "fill_template", "values": {"客户": "郑"}}],
            )
        )
        assert result.get("error_code") == "VERSION_CONFLICT"
        assert _paragraph_texts(path) == ["{{客户}}"]

    def test_apply_word_operations_two_arg_compatible(self, tmp_path: Path) -> None:
        path = _make_test_doc(tmp_path / "two_arg.docx", ["{{客户}}"])
        doc = Document(path)
        applied, errors = apply_word_operations(
            doc, [{"action": "fill_template", "values": {"客户": "两参"}}]
        )
        assert errors == []
        assert applied[0].startswith("fill_template filled=1")
        assert any(para.text == "两参" for para in doc.paragraphs)


class TestWriteWordExtractTable:
    def test_new_xlsx_parses_numbers_keeps_leading_zeros_and_blanks(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "src.docx"
        doc = Document()
        table = doc.add_table(rows=2, cols=4)
        _fill_table(
            table,
            [["H", "42", "007", ""], ["x", "3.5", "0", "   "]],
        )
        doc.save(path)
        before = path.read_bytes()

        result = _payload(
            write_word(
                path.name,
                operations=[{
                    "action": "extract_table",
                    "table_index": 0,
                    "target_file": "out.xlsx",
                }],
            )
        )

        from openpyxl import load_workbook

        assert result["status"] == "success"
        assert result["applied"][0] == "extract_table table=0 → out.xlsx!Sheet1!A1 2x4"
        assert result["target_file"] == "out.xlsx"
        assert result["target_sheet"] == "Sheet1"
        assert result["created_sheet"] is False
        assert path.read_bytes() == before
        out = tmp_path / "out.xlsx"
        assert result["target_content_version"] == content_version_of_file(out)
        from excelmanus.workbook_commit import peek_seen_content_version
        assert peek_seen_content_version("out.xlsx") == result["target_content_version"]
        wb = load_workbook(out)
        try:
            ws = wb.active
            assert ws["A1"].value == "H"
            assert ws["B1"].value == 42
            assert isinstance(ws["B1"].value, int)
            assert ws["C1"].value == "007"
            assert isinstance(ws["C1"].value, str)
            assert ws["D1"].value is None
            assert ws["A2"].value == "x"
            assert ws["B2"].value == 3.5
            assert isinstance(ws["B2"].value, float)
            assert ws["C2"].value == 0
            assert ws["D2"].value is None
        finally:
            wb.close()

    def test_existing_sheet_overwrites_block_with_version(self, tmp_path: Path) -> None:
        path = tmp_path / "src.docx"
        doc = Document()
        table = doc.add_table(rows=1, cols=2)
        _fill_table(table, [["N1", "N2"]])
        doc.save(path)
        xlsx = _make_xlsx(
            tmp_path / "exists.xlsx",
            [["keep", "old"], ["stay", "old2"]],
            sheet="Data",
        )
        ver = content_version_of_file(xlsx)

        result = _payload(
            write_word(
                path.name,
                operations=[{
                    "action": "extract_table",
                    "table_index": 0,
                    "target_file": "exists.xlsx",
                    "target_sheet": "Data",
                    "start_cell": "B2",
                    "target_expected_version": ver,
                }],
            )
        )

        from openpyxl import load_workbook

        assert result["status"] == "success"
        assert result["created_sheet"] is False
        assert result["target_sheet"] == "Data"
        assert "exists.xlsx!Data!B2 1x2" in result["applied"][0]
        wb = load_workbook(xlsx)
        try:
            ws = wb["Data"]
            assert ws["A1"].value == "keep"
            assert ws["B1"].value == "old"
            assert ws["A2"].value == "stay"
            assert ws["B2"].value == "N1"
            assert ws["C2"].value == "N2"
        finally:
            wb.close()

    def test_existing_xlsx_without_version_conflicts(self, tmp_path: Path) -> None:
        path = tmp_path / "src.docx"
        doc = Document()
        doc.add_table(rows=1, cols=1).cell(0, 0).text = "v"
        doc.save(path)
        xlsx = _make_xlsx(tmp_path / "exists.xlsx", [["old"]])
        before_src = path.read_bytes()
        before_xlsx = xlsx.read_bytes()

        result = _payload(
            write_word(
                path.name,
                operations=[{
                    "action": "extract_table",
                    "table_index": 0,
                    "target_file": "exists.xlsx",
                }],
            )
        )

        assert result.get("error_code") == "VERSION_CONFLICT"
        assert path.read_bytes() == before_src
        assert xlsx.read_bytes() == before_xlsx

    def test_extract_plus_stale_docx_does_not_create_xlsx(self, tmp_path: Path) -> None:
        path = tmp_path / "src.docx"
        doc = Document()
        doc.add_paragraph("Hello")
        table = doc.add_table(rows=1, cols=1)
        table.cell(0, 0).text = "cell"
        doc.save(path)
        result = _payload(
            write_word(
                path.name,
                operations=[
                    {
                        "action": "extract_table",
                        "table_index": 0,
                        "target_file": "pulled.xlsx",
                    },
                    {"action": "replace", "index": 0, "text": "X"},
                ],
                expected_version="sha256:" + ("0" * 64),
            )
        )
        assert result.get("error_code") == "VERSION_CONFLICT"
        assert not (tmp_path / "pulled.xlsx").exists()

    def test_missing_target_sheet_creates_sheet(self, tmp_path: Path) -> None:
        path = tmp_path / "src.docx"
        doc = Document()
        table = doc.add_table(rows=1, cols=1)
        table.cell(0, 0).text = "pulled"
        doc.save(path)
        xlsx = _make_xlsx(tmp_path / "exists.xlsx", [["keep"]], sheet="Data")
        ver = content_version_of_file(xlsx)

        result = _payload(
            write_word(
                path.name,
                operations=[{
                    "action": "extract_table",
                    "table_index": 0,
                    "target_file": "exists.xlsx",
                    "target_sheet": "Pulled",
                    "target_expected_version": ver,
                }],
            )
        )

        from openpyxl import load_workbook

        assert result["status"] == "success"
        assert result["created_sheet"] is True
        assert result["target_sheet"] == "Pulled"
        wb = load_workbook(xlsx)
        try:
            assert "Pulled" in wb.sheetnames
            assert wb["Pulled"]["A1"].value == "pulled"
            assert wb["Data"]["A1"].value == "keep"
        finally:
            wb.close()

    def test_merged_cells_extract_with_uncertainty(self, tmp_path: Path) -> None:
        path = tmp_path / "merged.docx"
        doc = Document()
        table = doc.add_table(rows=2, cols=2)
        table.cell(0, 0).text = "A"
        table.cell(0, 1).text = "B"
        table.cell(1, 0).text = "C"
        table.cell(1, 1).text = "D"
        table.cell(0, 0).merge(table.cell(0, 1))
        doc.save(path)

        result = _payload(
            write_word(
                path.name,
                operations=[{
                    "action": "extract_table",
                    "table_index": 0,
                    "target_file": "merged.xlsx",
                }],
            )
        )

        from openpyxl import load_workbook

        assert result["status"] == "success"
        assert "uncertainties" in result
        assert any("合并单元格" in item for item in result["uncertainties"])
        wb = load_workbook(tmp_path / "merged.xlsx")
        try:
            ws = wb.active
            assert ws["A1"].value is not None
            assert ws["A2"].value == "C"
        finally:
            wb.close()

    def test_extract_only_does_not_change_docx_bytes(self, tmp_path: Path) -> None:
        path = _make_test_doc(tmp_path / "readonly.docx", ["Keep"])
        doc = Document(path)
        table = doc.add_table(rows=1, cols=1)
        table.cell(0, 0).text = "42"
        doc.save(path)
        before = path.read_bytes()

        result = _payload(
            write_word(
                path.name,
                operations=[{
                    "action": "extract_table",
                    "table_index": 0,
                    "target_file": "out.xlsx",
                }],
            )
        )

        assert result["status"] == "success"
        assert path.read_bytes() == before
        assert "Keep" in _paragraph_texts(path)

    def test_mixed_extract_and_replace_both_apply(self, tmp_path: Path) -> None:
        path = _make_test_doc(tmp_path / "mixed.docx", ["Hello"])
        doc = Document(path)
        table = doc.add_table(rows=1, cols=1)
        table.cell(0, 0).text = "99"
        doc.save(path)

        result = _write(
            path,
            [
                {
                    "action": "extract_table",
                    "table_index": 0,
                    "target_file": "out.xlsx",
                },
                {"action": "replace", "index": 0, "text": "Updated"},
            ],
        )

        from openpyxl import load_workbook

        assert result["status"] == "success"
        assert _paragraph_texts(path)[0] == "Updated"
        wb = load_workbook(tmp_path / "out.xlsx")
        try:
            assert wb.active["A1"].value == 99
        finally:
            wb.close()

    def test_extract_and_output_file_are_mutex(self, tmp_path: Path) -> None:
        path = tmp_path / "src.docx"
        doc = Document()
        doc.add_table(rows=1, cols=1).cell(0, 0).text = "v"
        doc.save(path)
        before = path.read_bytes()

        result = _payload(
            write_word(
                path.name,
                operations=[{
                    "action": "extract_table",
                    "table_index": 0,
                    "target_file": "out.xlsx",
                }],
                output_file="other.docx",
            )
        )

        assert result.get("error_code") == "INVALID_ARGS"
        assert "互斥" in (result.get("message") or "")
        assert path.read_bytes() == before
        assert not (tmp_path / "out.xlsx").exists()
        assert not (tmp_path / "other.docx").exists()

    def test_extract_table_in_schema(self) -> None:
        tool = next(item for item in get_tools() if item.name == "write_word")
        schema = tool.input_schema
        actions = schema["properties"]["operations"]["items"]["properties"]["action"]["enum"]
        assert "extract_table" in actions
        item_props = schema["properties"]["operations"]["items"]["properties"]
        for key in ("target_file", "target_sheet", "start_cell", "target_expected_version"):
            assert key in item_props
        assert "extract_table" in tool.description
        assert "output_file" in tool.description
        assert "互斥" in tool.description


class TestWriteWordSchema:
    def test_fill_template_and_output_file_in_schema(self) -> None:
        tool = next(item for item in get_tools() if item.name == "write_word")
        schema = tool.input_schema
        actions = schema["properties"]["operations"]["items"]["properties"]["action"]["enum"]
        assert "fill_template" in actions
        item_props = schema["properties"]["operations"]["items"]["properties"]
        for key in ("values", "source_file", "source_sheet", "source_row", "header_row"):
            assert key in item_props
        assert "output_file" in schema["properties"]
        assert "output_expected_version" in schema["properties"]
        assert "{{列名}}" in tool.description
        assert "output_file" in tool.description
        assert "页眉" in tool.description


class TestInspectWord:
    def test_single_file(self, tmp_path: Path) -> None:
        path = tmp_path / "single.docx"
        doc = Document()
        doc.core_properties.title = "Quarterly Review"
        doc.add_heading("Overview", level=1)
        doc.add_paragraph("Body")
        doc.add_table(rows=1, cols=2)
        doc.save(path)

        result = _payload(inspect_word(file_path=path.name))

        assert result["file_path"] == path.name
        assert result["title"] == "Quarterly Review"
        assert result["total_paragraphs"] == 2
        assert result["total_tables"] == 1
        assert result["headings"][0]["text"] == "Overview"

    def test_multiple_files(self, tmp_path: Path) -> None:
        first = _make_test_doc(tmp_path / "first.docx", ["One"])
        second = _make_test_doc(tmp_path / "second.docx", ["Two"])

        result = _payload(inspect_word(file_paths=[first.name, second.name]))

        files = {entry["file_path"] for entry in result["files"]}
        assert files == {first.name, second.name}

    def test_directory_scan(self, tmp_path: Path) -> None:
        _make_test_doc(tmp_path / "a.docx")
        _make_test_doc(tmp_path / "b.docx")
        (tmp_path / "notes.txt").write_text("skip me", encoding="utf-8")

        result = _payload(inspect_word(directory="."))

        files = {entry["file_path"] for entry in result["files"]}
        assert files == {"a.docx", "b.docx"}

    def test_directory_scan_skips_doc_files(self, tmp_path: Path) -> None:
        _make_test_doc(tmp_path / "current.docx", ["Current"])
        (tmp_path / "legacy.doc").write_bytes(b"legacy-doc")

        result = _payload(inspect_word(directory="."))

        assert result["file_path"] == "current.docx"

    def test_non_docx_file_returns_error(self, tmp_path: Path) -> None:
        (tmp_path / "notes.txt").write_text("plain text", encoding="utf-8")

        result = _payload(inspect_word(file_paths=["notes.txt"]))

        assert result["file_path"] == "notes.txt"
        assert "docx" in result["message"].lower()

    def test_default_section_orientation_is_portrait(self, tmp_path: Path) -> None:
        path = _make_test_doc(tmp_path / "portrait.docx")

        result = _payload(inspect_word(file_path=path.name))

        assert result["sections"][0]["orientation"] == "portrait"

    def test_landscape_section_orientation(self, tmp_path: Path) -> None:
        path = tmp_path / "landscape.docx"
        doc = Document()
        doc.add_paragraph("Wide")
        section = doc.sections[0]
        new_width, new_height = section.page_height, section.page_width
        section.orientation = WD_ORIENT.LANDSCAPE
        section.page_width = new_width
        section.page_height = new_height
        doc.save(path)

        result = _payload(inspect_word(file_path=path.name))

        assert result["sections"][0]["orientation"] == "landscape"


class TestSearchWord:
    def test_contains(self, tmp_path: Path) -> None:
        path = _make_test_doc(tmp_path / "contains.docx", ["Hello world", "Bye"])

        result = _payload(search_word("world", file_path=path.name))

        assert result["match_mode"] == "contains"
        assert result["total_matches"] == 1
        assert result["matches"][0]["paragraph_index"] == 0

    def test_exact(self, tmp_path: Path) -> None:
        path = _make_test_doc(tmp_path / "exact.docx", ["world", "worldwide"])

        result = _payload(search_word("world", file_path=path.name, match_mode="exact"))

        assert result["total_matches"] == 1
        assert result["matches"][0]["text"] == "world"

    def test_regex(self, tmp_path: Path) -> None:
        path = _make_test_doc(tmp_path / "regex.docx", ["Item 42", "Other"])

        result = _payload(search_word(r"Item \d+", file_path=path.name, match_mode="regex"))

        assert result["total_matches"] == 1
        assert result["matches"][0]["text"] == "Item 42"

    def test_startswith(self, tmp_path: Path) -> None:
        path = _make_test_doc(tmp_path / "startswith.docx", ["Intro line", "Tail"])

        result = _payload(search_word("Intro", file_path=path.name, match_mode="startswith"))

        assert result["total_matches"] == 1
        assert result["matches"][0]["text"] == "Intro line"

    def test_multiple_files(self, tmp_path: Path) -> None:
        first = _make_test_doc(tmp_path / "first.docx", ["alpha"])
        second = _make_test_doc(tmp_path / "second.docx", ["beta alpha"])

        result = _payload(search_word("alpha", file_paths=[first.name, second.name]))

        assert result["total_matches"] == 2
        files = {match["file_path"] for match in result["matches"]}
        assert files == {first.name, second.name}

    def test_doc_file_returns_error(self, tmp_path: Path) -> None:
        (tmp_path / "legacy.doc").write_bytes(b"legacy-doc")

        result = _payload(search_word("legacy", file_path="legacy.doc"))

        assert result["file_path"] == "legacy.doc"
        assert "docx" in result["message"].lower()

    def test_table_cell_hit_when_absent_from_paragraphs(self, tmp_path: Path) -> None:
        path = tmp_path / "table_only.docx"
        doc = Document()
        doc.add_paragraph("Intro only")
        table = doc.add_table(rows=2, cols=2)
        table.cell(0, 0).text = "Metric"
        table.cell(0, 1).text = "Value"
        table.cell(1, 0).text = "Revenue"
        table.cell(1, 1).text = "42"
        doc.save(path)

        result = _payload(search_word("42", file_path=path.name))

        assert result["total_matches"] == 1
        hit = result["matches"][0]
        assert hit["kind"] == "table"
        assert hit["file_path"] == path.name
        assert hit["table_index"] == 0
        assert hit["row"] == 1
        assert hit["col"] == 1
        assert hit["text"] == "42"
        assert "paragraph_index" not in hit

    def test_table_regex(self, tmp_path: Path) -> None:
        path = tmp_path / "table_regex.docx"
        doc = Document()
        doc.add_paragraph("No numbers here")
        table = doc.add_table(rows=1, cols=2)
        table.cell(0, 0).text = "SKU-99"
        table.cell(0, 1).text = "plain"
        doc.save(path)

        result = _payload(
            search_word(r"SKU-\d+", file_path=path.name, match_mode="regex")
        )

        assert result["total_matches"] == 1
        hit = result["matches"][0]
        assert hit["kind"] == "table"
        assert hit["row"] == 0
        assert hit["col"] == 0
        assert hit["text"] == "SKU-99"

    def test_max_results_spans_paragraphs_and_tables(self, tmp_path: Path) -> None:
        path = tmp_path / "cap.docx"
        doc = Document()
        doc.add_paragraph("hit one")
        doc.add_paragraph("hit two")
        table = doc.add_table(rows=2, cols=2)
        table.cell(0, 0).text = "hit A"
        table.cell(0, 1).text = "hit B"
        table.cell(1, 0).text = "hit C"
        table.cell(1, 1).text = "other"
        doc.save(path)

        result = _payload(search_word("hit", file_path=path.name, max_results=3))

        assert result["total_matches"] == 3
        kinds = [match["kind"] for match in result["matches"]]
        assert kinds == ["paragraph", "paragraph", "table"]
        assert result["matches"][0]["paragraph_index"] == 0
        assert result["matches"][1]["paragraph_index"] == 1
        assert result["matches"][2]["table_index"] == 0
        assert result["matches"][2]["row"] == 0
        assert result["matches"][2]["col"] == 0

    def test_multiple_files_include_table_hits(self, tmp_path: Path) -> None:
        first = _make_test_doc(tmp_path / "first.docx", ["alpha"])
        second_path = tmp_path / "second.docx"
        doc = Document()
        doc.add_paragraph("beta")
        table = doc.add_table(rows=1, cols=1)
        table.cell(0, 0).text = "alpha in cell"
        doc.save(second_path)

        result = _payload(
            search_word("alpha", file_paths=[first.name, second_path.name])
        )

        assert result["total_matches"] == 2
        files = {match["file_path"] for match in result["matches"]}
        assert files == {first.name, second_path.name}
        kinds = {match["kind"] for match in result["matches"]}
        assert kinds == {"paragraph", "table"}

    def test_paragraph_hit_keeps_original_fields(self, tmp_path: Path) -> None:
        path = _make_test_doc(tmp_path / "fields.docx", ["Hello world"])

        result = _payload(search_word("world", file_path=path.name))

        hit = result["matches"][0]
        assert hit["file_path"] == path.name
        assert hit["paragraph_index"] == 0
        assert hit["style"] == "Normal"
        assert hit["text"] == "Hello world"
        assert hit["kind"] == "paragraph"
        assert set(hit) == {
            "file_path",
            "kind",
            "paragraph_index",
            "style",
            "text",
        }

    def test_description_mentions_paragraphs_and_tables(self) -> None:
        tool = next(item for item in get_tools() if item.name == "search_word")
        assert "表格" in tool.description
        assert "paragraph_index" in tool.description
        assert "table_index" in tool.description


class TestFileRegistryWordPolicy:
    def test_scan_workspace_does_not_classify_doc_as_word(self, tmp_path: Path) -> None:
        registry = FileRegistry(Database(str(tmp_path / "registry.db")), tmp_path)
        _make_test_doc(tmp_path / "report.docx", ["Intro"])
        (tmp_path / "legacy.doc").write_bytes(b"legacy-doc")

        registry.scan_workspace()
        report = registry.get_by_path("report.docx")
        legacy = registry.get_by_path("legacy.doc")
        assert report is not None
        assert report.file_type == "word"
        assert legacy is not None
        assert legacy.file_type == "other"


class TestHelpers:
    def test_ensure_docx_validation(self) -> None:
        assert _ensure_docx("report.docx") is None
        error = _payload(_ensure_docx("report.txt"))
        assert error["file_path"] == "report.txt"

    def test_heading_level_parsing(self) -> None:
        doc = Document()
        title = doc.add_paragraph("Main Title")
        title.style = "Title"
        heading = doc.add_paragraph("Section")
        heading.style = "Heading 2"

        assert _heading_level(title) == 0
        assert _heading_level(heading) == 2

    def test_run_to_dict_format(self) -> None:
        para = Document().add_paragraph()
        run = para.add_run("Styled")
        run.bold = True
        run.italic = True
        run.underline = True
        run.font.name = "Calibri"
        run.font.size = Pt(14)
        run.font.color.rgb = RGBColor(0xAA, 0xBB, 0xCC)

        result = _run_to_dict(run)

        assert result == {
            "text": "Styled",
            "format": {
                "bold": True,
                "italic": True,
                "underline": True,
                "size_pt": 14.0,
                "font": "Calibri",
                "color": "AABBCC",
            },
        }


class TestGuardRequired:
    """未初始化 FileAccessGuard 时不得读写任意路径。"""

    def test_resolve_path_without_guard_returns_error_json(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from excelmanus.tools.context import clear_call

        clear_call()

        _safe, err = _resolve_path("/tmp/outside.docx")

        assert err is not None
        payload = _payload(err)
        assert payload.get("status") == "error"
        assert payload.get("file_path") == "/tmp/outside.docx"

    def test_write_word_without_guard_cannot_write_outside_workspace(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        outside = tmp_path.parent / "excelmanus_unguarded_escape.docx"
        _make_test_doc(outside, ["original"])
        from excelmanus.tools.context import clear_call

        clear_call()

        try:
            result = _payload(
                write_word(
                    str(outside),
                    operations=[{"action": "append", "text": "pwned"}],
                )
            )

            assert result.get("status") == "error"
            assert _paragraph_texts(outside) == ["original"]
        finally:
            if outside.exists():
                outside.unlink()
