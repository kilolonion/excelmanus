"""Catalog family detection requires contents, not just a filename suffix."""
from zipfile import ZipFile

import pytest
from openpyxl import Workbook

from excelmanus.tools.catalog import inspect_workspace_catalog
from excelmanus.workbook.file_format import is_workbook_file


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "uploads").mkdir()
    (tmp_path / "outputs").mkdir()
    (tmp_path / "uploads" / "data.csv").write_text("a,b\n1,2\n")
    return tmp_path


@pytest.mark.parametrize("suffix", [".xlsx", ".xlsm", ".xltx", ".xltm", ".xlsb", ".xls"])
def test_renamed_csv_does_not_unlock_tools(workspace, suffix):
    fake = workspace / "outputs" / ("fake" + suffix)
    fake.write_text("a,b\n1,2\n")
    assert not is_workbook_file(fake)
    flags = inspect_workspace_catalog(str(workspace))
    assert flags["profile"] == "csv"
    assert flags["new_workbook"] is True


@pytest.mark.parametrize("kind", ["pk_only", "zip", "fake_parts", "wrong_root", "word_package"])
def test_invalid_containers_do_not_count(workspace, kind):
    fake = workspace / "outputs" / "fake.xlsx"
    if kind == "pk_only":
        fake.write_bytes(b"PK\x03\x04")
    else:
        with ZipFile(fake, "w") as package:
            package.writestr("arbitrary.txt", "not a workbook")
            if kind in {"fake_parts", "wrong_root", "word_package"}:
                content_type = ("application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
                                if kind == "word_package" else
                                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml")
                package.writestr("[Content_Types].xml",
                    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                    f'<Override PartName="/xl/workbook.xml" ContentType="{content_type}"/></Types>')
                package.writestr("xl/workbook.xml", "not XML" if kind == "fake_parts" else
                    '<notWorkbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"/>')
    assert inspect_workspace_catalog(str(workspace))["profile"] == "csv"


def test_real_workbook_unlocks_and_replacement_invalidates_cache(workspace):
    path = workspace / "outputs" / "report.xlsx"
    path.write_bytes(b"PK\x03\x04")
    assert not is_workbook_file(path)
    wb = Workbook()
    wb.active["A1"] = 42
    wb.save(path)
    wb.close()
    assert is_workbook_file(path)
    assert inspect_workspace_catalog(str(workspace))["profile"] == "xlsx"
    assert not inspect_workspace_catalog(str(workspace))["new_workbook"]
    path.write_text("a,b\n1,2\n")
    assert not is_workbook_file(path)
    assert inspect_workspace_catalog(str(workspace))["profile"] == "csv"


def test_corrupt_file_does_not_hide_later_valid_workbook(workspace):
    (workspace / "outputs" / "a.xlsx").write_bytes(b"corrupt")
    wb = Workbook()
    wb.save(workspace / "outputs" / "z.xlsx")
    wb.close()
    assert inspect_workspace_catalog(str(workspace))["profile"] == "xlsx"


def test_csv_copy_tool_cannot_fake_workbook_evidence(workspace):
    from excelmanus.tools.context import use_workspace
    from excelmanus.tools.file_tools import copy_file

    with use_workspace(workspace):
        result = copy_file("uploads/data.csv", "outputs/renamed.xlsx")
    assert result.success is True
    assert inspect_workspace_catalog(str(workspace))["profile"] == "csv"
