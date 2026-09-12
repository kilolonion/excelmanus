"""八类意图工具：模型面唯一入口，内部复用旧实现。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from openpyxl import Workbook

from excelmanus.engine_core.tool_result import ToolResult
from excelmanus.security import FileAccessGuard
from excelmanus.tools import (
    ToolRegistry,
    intent_tools,
    reference_tools,
)
from excelmanus.tools._guard_ctx import set_guard
from excelmanus.tools.intent_tools import (
    analyze_spreadsheet,
    compare_spreadsheets,
    edit_spreadsheet,
    format_spreadsheet,
    inspect_spreadsheet,
    manage_spreadsheet_objects,
    manage_spreadsheet_versions,
    trace_spreadsheet_formulas,
)


def _bind_workspace(root: Path) -> None:
    workspace = str(root)
    set_guard(FileAccessGuard(workspace))
    intent_tools.init_guard(workspace)
    reference_tools.init_guard(workspace)


_MODEL_SPREADSHEET_TOOLS = {
    "inspect_spreadsheet",
    "analyze_spreadsheet",
    "compare_spreadsheets",
    "edit_spreadsheet",
    "format_spreadsheet",
    "manage_spreadsheet_objects",
    "trace_spreadsheet_formulas",
    "manage_spreadsheet_versions",
}

_REMOVED_MODEL_TOOLS = {
    "read_excel",
    "filter_data",
    "compare_excel",
    "list_sheets",
    "create_excel_chart",
    "scan_excel_snapshot",
    "search_excel_values",
    "extract_table_spec",
    "rebuild_excel_from_spec",
    "verify_excel_replica",
}


def _book(path: Path, rows: list[list[object]] | None = None) -> Path:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "Sheet1"
    for r_idx, row in enumerate(rows or [["部门", "金额"], ["销售", 10], ["研发", 20]], start=1):
        for c_idx, value in enumerate(row, start=1):
            ws.cell(row=r_idx, column=c_idx, value=value)
    wb.save(path)
    return path


def _payload(result: ToolResult) -> dict[str, Any]:
    assert isinstance(result, ToolResult)
    assert isinstance(result.value, dict)
    return result.value


def test_intent_tools_are_the_only_spreadsheet_catalog(tmp_path: Path) -> None:
    registry = ToolRegistry()
    registry.register_builtin_tools(str(tmp_path))
    names = set(registry.get_tool_names())
    assert _MODEL_SPREADSHEET_TOOLS <= names
    assert names.isdisjoint(_REMOVED_MODEL_TOOLS)


def test_inspect_overview_and_range(tmp_path: Path) -> None:
    _bind_workspace(tmp_path)
    path = _book(tmp_path / "book.xlsx")
    overview = inspect_spreadsheet(mode="overview", file_path=str(path))
    assert isinstance(overview, ToolResult)
    assert overview.value
    assert overview.ui_meta.content_version
    ranged = inspect_spreadsheet(mode="range", file_path=str(path), sheet_name="Sheet1", max_rows=5)
    assert isinstance(ranged, ToolResult)
    assert ranged.success
    assert ranged.ui_meta.content_version


def _assert_model_text_not_full_dump(result: ToolResult) -> None:
    assert isinstance(result, ToolResult)
    assert result.success
    dumped = json.dumps(result.value, ensure_ascii=False, default=str)
    assert result.model_text != dumped
    assert len(result.model_text) < 4000


def test_inspect_search_and_analyze_probes_are_bounded(tmp_path: Path) -> None:
    _bind_workspace(tmp_path)
    a = _book(tmp_path / "a.xlsx")
    b = _book(tmp_path / "b.xlsx", [["部门", "金额"], ["销售", 11], ["研发", 20]])

    searched = inspect_spreadsheet(mode="search", file_path=str(a), query="销售")
    _assert_model_text_not_full_dump(searched)
    assert '"matches"' not in searched.model_text

    profiled = analyze_spreadsheet(mode="profile", file_path=str(a))
    _assert_model_text_not_full_dump(profiled)
    assert '"quality_signals"' not in profiled.model_text

    files = analyze_spreadsheet(mode="files", directory=str(tmp_path))
    _assert_model_text_not_full_dump(files)
    assert '"file_list"' not in files.model_text

    rels = analyze_spreadsheet(mode="relationships", file_paths=[str(a), str(b)])
    _assert_model_text_not_full_dump(rels)
    assert '"file_pairs"' not in rels.model_text


def test_analyze_filter_and_compare(tmp_path: Path) -> None:
    _bind_workspace(tmp_path)
    a = _book(tmp_path / "a.xlsx")
    b = _book(tmp_path / "b.xlsx", [["部门", "金额"], ["销售", 11], ["研发", 20]])
    filtered = analyze_spreadsheet(
        mode="filter",
        file_path=str(a),
        column="部门",
        operator="eq",
        value="销售",
    )
    assert isinstance(filtered, ToolResult)
    assert "销售" in filtered.model_text or "销售" in json.dumps(filtered.value, ensure_ascii=False)
    compared = compare_spreadsheets(file_a=str(a), file_b=str(b), alignment="position")
    assert isinstance(compared, ToolResult)


def test_edit_format_objects_versions(tmp_path: Path) -> None:
    _bind_workspace(tmp_path)
    path = _book(tmp_path / "book.xlsx")
    from excelmanus.workbook_commit import content_version_of_file

    edited = edit_spreadsheet(
        file_path=str(path),
        operations=[{"kind": "write", "sheet": "Sheet1", "start_cell": "B2", "values": [[99]]}],
        expected_version=content_version_of_file(path),
    )
    edited_payload = _payload(edited)
    assert edited.success
    assert edited_payload.get("status") == "success"
    assert edited.ui_meta.content_version == edited_payload.get("content_version")
    assert edited.ui_meta.files
    formatted = format_spreadsheet(
        file_path=str(path),
        operations=[{"kind": "format", "sheet": "Sheet1", "range": "A1:B1", "font": {"bold": True}}],
        expected_version=edited_payload.get("content_version"),
    )
    formatted_payload = _payload(formatted)
    assert formatted.success
    assert formatted_payload.get("status") == "success"
    charted = manage_spreadsheet_objects(
        file_path=str(path),
        operations=[{
            "kind": "chart",
            "chart_type": "bar",
            "data_range": "B1:B3",
            "categories_range": "A2:A3",
            "sheet": "Sheet1",
            "target_cell": "D1",
        }],
        expected_version=formatted_payload.get("content_version"),
    )
    assert isinstance(charted, ToolResult)
    listed = manage_spreadsheet_versions(file_path=str(path), action="list")
    listed_payload = _payload(listed)
    assert listed.success
    assert listed_payload.get("status") == "success"


def test_edit_spreadsheet_sheet_and_copy_ops(tmp_path: Path) -> None:
    _bind_workspace(tmp_path)
    path = _book(tmp_path / "sheets.xlsx")
    from excelmanus.workbook_commit import content_version_of_file
    from openpyxl import load_workbook

    created = edit_spreadsheet(
        file_path=str(path),
        operations=[{"kind": "sheet", "action": "create", "new_name": "Sheet2"}],
        expected_version=content_version_of_file(path),
    )
    created_payload = _payload(created)
    assert created.success
    copied = edit_spreadsheet(
        file_path=str(path),
        operations=[{
            "kind": "copy",
            "source_sheet": "Sheet1",
            "source_range": "A1:B2",
            "target_sheet": "Sheet2",
            "target_start": "A1",
        }],
        expected_version=created_payload.get("content_version"),
    )
    assert copied.success
    wb = load_workbook(path)
    try:
        assert "Sheet2" in wb.sheetnames
        assert wb["Sheet2"]["A1"].value == "部门"
    finally:
        wb.close()


def test_versions_checkpoint_revision_on_all_three_lanes(tmp_path: Path) -> None:
    _bind_workspace(tmp_path)
    path = _book(tmp_path / "book.xlsx")
    checkpoint = manage_spreadsheet_versions(file_path=str(path), action="checkpoint", label="before")
    payload = _payload(checkpoint)
    assert checkpoint.success
    revision = payload.get("revision")
    assert isinstance(revision, dict)
    assert revision.get("revision_id")
    assert checkpoint.ui_meta.revision == revision
    assert revision["revision_id"] in checkpoint.model_text
    assert checkpoint.ui_meta.content_version == payload.get("content_version")
    assert revision.get("reason") == "checkpoint"
    assert not (tmp_path / "outputs" / ".versions").exists()
    assert not (tmp_path / "outputs" / "backups").exists()
    listed = manage_spreadsheet_versions(file_path=str(path), action="list")
    listed_ids = [item.get("revision_id") for item in _payload(listed).get("revisions") or []]
    assert revision["revision_id"] in listed_ids


def test_versions_restore_from_revision_store(tmp_path: Path) -> None:
    from excelmanus.workbook_commit import content_version_of_file
    from openpyxl import load_workbook

    _bind_workspace(tmp_path)
    path = _book(tmp_path / "book.xlsx")
    before = manage_spreadsheet_versions(file_path=str(path), action="checkpoint", label="before")
    revision_id = _payload(before)["revision"]["revision_id"]
    ver = content_version_of_file(path)
    edited = edit_spreadsheet(
        file_path=str(path),
        operations=[{"kind": "write", "sheet": "Sheet1", "start_cell": "A1", "values": [["changed"]]}],
        expected_version=ver,
    )
    assert edited.success
    restored = manage_spreadsheet_versions(
        file_path=str(path),
        action="restore",
        revision_id=revision_id,
        expected_version=_payload(edited).get("content_version"),
    )
    assert restored.success
    wb = load_workbook(path)
    try:
        assert wb["Sheet1"]["A1"].value == "部门"
    finally:
        wb.close()
    assert not (tmp_path / "outputs" / ".versions").exists()
    listed = _payload(manage_spreadsheet_versions(file_path=str(path), action="list"))
    reasons = [item.get("reason") for item in listed.get("revisions") or []]
    assert "beforeRestore" in reasons
    assert "afterEdit" in reasons


def test_versions_restore_requires_expected_version(tmp_path: Path) -> None:
    _bind_workspace(tmp_path)
    path = _book(tmp_path / "book.xlsx")
    before = manage_spreadsheet_versions(file_path=str(path), action="checkpoint", label="before")
    revision_id = _payload(before)["revision"]["revision_id"]
    restored = manage_spreadsheet_versions(
        file_path=str(path),
        action="restore",
        revision_id=revision_id,
    )
    assert not restored.success
    assert restored.error is not None
    assert restored.error.code == "VERSION_CONFLICT"


def test_trace_map(tmp_path: Path) -> None:
    _bind_workspace(tmp_path)
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws["A1"] = 1
    ws["B1"] = "=A1+1"
    path = tmp_path / "f.xlsx"
    wb.save(path)
    result = trace_spreadsheet_formulas(mode="map", file_path=str(path))
    payload = _payload(result)
    assert "sheets" in payload or payload.get("status") == "error"


def test_prompt_sections_follow_segment_order() -> None:
    from pathlib import Path as P

    import excelmanus
    from excelmanus.prompt.load import PromptComposer, PromptContext

    composer = PromptComposer(P(excelmanus.__file__).resolve().parent / "prompts")
    composer.load_all()
    names = {seg.name: seg.order for seg in composer.core_segments + composer.strategy_segments}
    assert names["harness:identity"] == -100
    assert names["deployment:persona"] == 0
    assert names["plan:policy"] == 50
    assert names["tool:inspect"] == 100
    assert names["spreadsheet:workbook_spec"] == 110
    assert "spreadsheet:invariants" not in names
    assert names["tool:run_code"] == 150
    text = composer.compose_system_text(PromptContext())
    assert "VERSION_CONFLICT" in text
    assert "read_excel" not in text
    assert "当前是计划模式" not in text
