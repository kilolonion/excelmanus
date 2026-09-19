from io import BytesIO

import pytest
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font

from excelmanus.workbook.view_mutate import apply_workbook_operations
from excelmanus.workspace.file_service import WorkspaceFileService


def test_ui_rejects_unmaintained_structure_without_publication(tmp_path):
    wb = Workbook()
    wb.active.title = "Data"
    wb.active["A2"] = 3
    wb.active["B2"] = "=A2*2"
    out = BytesIO(); wb.save(out)
    svc = WorkspaceFileService(tmp_path)
    created = svc.create("book.xlsx", out.getvalue())
    def builder(before):
        book = load_workbook(BytesIO(before))
        apply_workbook_operations(book, [{"op":"insert_axis","sheet":"Data","axis":"row","index":2}])
        target = BytesIO(); book.save(target); return target.getvalue()
    with pytest.raises(ValueError, match="拒绝结构修改"):
        svc.update_with_builder("book.xlsx", builder, expected_version=created.primary_version())
    assert (tmp_path / "book.xlsx").read_bytes() == out.getvalue()


def test_style_patch_can_disable_without_erasing_unrelated_fields():
    wb = Workbook(); ws = wb.active
    ws["A1"].font = Font(underline="single", strike=True, scheme="minor", bold=True)
    ws["A1"].alignment = Alignment(wrap_text=True, horizontal="left")
    apply_workbook_operations(wb, [{"op":"set_styles", "sheet":ws.title,
        "cells":[{"cell":"A1","style":{"ul":{"s":0},"st":{"s":0},"ht":2}}]}])
    assert ws["A1"].font.underline is None and ws["A1"].font.strike is False
    assert ws["A1"].font.bold and ws["A1"].font.scheme == "minor"
    assert ws["A1"].alignment.wrap_text and ws["A1"].alignment.horizontal == "center"


def test_unknown_operation_or_fields_are_rejected():
    with pytest.raises(ValueError):
        apply_workbook_operations(Workbook(), [{"op":"unknown"}])
    with pytest.raises(ValueError):
        apply_workbook_operations(Workbook(), [{"op":"set_values","celss":[]}])
