"""V2 evidence belongs to the atomic receipt, never to a second live-file read."""
from pathlib import Path

import openpyxl
import pytest

from excelmanus.tools.context import use_workspace
from excelmanus.tools.workbook_tools import apply_spreadsheet_changes
from excelmanus.workbook_commit import content_version_of_file


@pytest.fixture
def workbook(tmp_path):
    path=tmp_path/'test.xlsx'
    wb=openpyxl.Workbook(); wb.active.title='Data'; wb.active['A1']='old'; wb.save(path); wb.close()
    with use_workspace(tmp_path):
        yield path


def test_receipt_binds_serialized_cells_to_committed_version(workbook):
    result=apply_spreadsheet_changes(file_path=workbook.name,expected_version=content_version_of_file(workbook),operations=[{'kind':'write','sheet':'Data','start_cell':'A1','values':[['new']]}])
    assert result.success, result.model_text
    observation=result.value['observation']
    assert observation['content_version']==content_version_of_file(workbook)
    assert observation['cell_checks']==[{'sheet':'Data','cell':'A1','value':'new','formula':None,'verified':True}]
    assert observation['visual_observed'] is False


def test_receipt_verifies_style_without_claiming_visual_check(workbook):
    result=apply_spreadsheet_changes(file_path=workbook.name,expected_version=content_version_of_file(workbook),operations=[{'kind':'format','sheet':'Data','range':'A1','font':{'bold':True}}])
    assert result.success
    assert result.value['observation']['cell_checks'][0]['verified']
    assert result.value['observation']['coverage']['kind']=='sampled'
    wb=openpyxl.load_workbook(workbook)
    try:
        assert wb['Data']['A1'].font.bold
    finally:
        wb.close()


@pytest.mark.parametrize('file_path',['test.xlsx','', 'nonexistent.xlsx'])
def test_dispatcher_does_not_create_a_second_workbook_checkpoint(workbook, file_path, monkeypatch):
    from excelmanus.engine_core.tool_dispatcher import ToolDispatcher
    monkeypatch.setattr(openpyxl,'load_workbook',lambda *a,**kw:pytest.fail('V2 must use the receipt, not reopen a live file'))
    assert ToolDispatcher._post_write_checkpoint('apply_spreadsheet_changes',{'file_path':file_path},str(workbook.parent))==''


def test_unknown_tool_does_not_create_checkpoint(workbook):
    from excelmanus.engine_core.tool_dispatcher import ToolDispatcher
    assert ToolDispatcher._post_write_checkpoint('some_other_tool',{'file_path':str(workbook)},str(workbook.parent))==''
