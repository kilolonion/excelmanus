"""Version-bound workbook targets shared by questions and presentation tools."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from excelmanus.workbook.refs import parse_rect
from excelmanus.workbook.snapshot import open_snapshot_at, require_default_sheet
from excelmanus.workspace.identity import resolve_canonical


def target_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "工作区内的表格路径"},
            "sheet": {"type": "string", "description": "工作表名称"},
            "ranges": {"type": "array", "maxItems": 16, "items": {"type": "string"},
                       "description": "A1 区域列表；选区提问时是建议区域，展示时必须提供"},
            "content_version": {"type": "string", "description": "读取或写入回执中的版本；已修改展示必须提供"},
        },
        "required": ["file_path", "sheet"],
        "additionalProperties": False,
    }


def normalize_ranges(raw: Any, sheet: str, *, required: bool = False) -> list[str]:
    if not isinstance(raw, list) or len(raw) > 16 or (required and not raw):
        raise ValueError("请选择 1–16 个区域" if required else "ranges 必须是最多 16 个区域的数组")
    result: list[str] = []
    for address in raw:
        if not isinstance(address, str) or len(address) > 128:
            raise ValueError("区域必须是 A1 地址")
        rect = parse_rect(address, default_sheet=sheet)
        if rect.sheet != sheet:
            raise ValueError("区域必须属于指定工作表")
        normalized = rect.to_a1(include_sheet=False).replace("$", "")
        if normalized not in result:
            result.append(normalized)
    return result


def bind_target(engine: Any, raw: Any, *, require_ranges: bool = False) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("selection/target 必须是对象")
    workspace = getattr(engine, "_workspace_ref", None)
    if workspace is None:
        raise ValueError("当前会话没有绑定工作区")
    path, sheet = raw.get("file_path"), raw.get("sheet")
    if not isinstance(path, str) or not path or len(path) > 1024:
        raise ValueError("file_path 不能为空")
    if not isinstance(sheet, str) or not sheet or len(sheet) > 100:
        raise ValueError("sheet 不能为空")
    identity = resolve_canonical(workspace.root, path)
    if not identity.relative.lower().endswith((".xlsx", ".xlsm", ".xls", ".xlsb", ".csv", ".tsv")):
        raise ValueError("只能展示表格文件")
    version = raw.get("content_version")
    if version is not None and (not isinstance(version, str) or not version or len(version) > 100):
        raise ValueError("content_version 格式无效")
    snapshot = open_snapshot_at(workspace.root / identity.relative, relative=identity.relative,
                                workspace=workspace, expected_version=version)
    if snapshot.is_csv():
        sheet = require_default_sheet(["Sheet1"], sheet)
    else:
        wb = snapshot.open_workbook(data_only=False)
        try:
            sheet = require_default_sheet(wb.sheetnames, sheet)
        finally:
            wb.close()
    return {
        "file_path": identity.public,
        "workspace_id": workspace.workspace_id,
        "sheet": sheet,
        "ranges": normalize_ranges([] if raw.get("ranges") is None else raw["ranges"], sheet, required=require_ranges),
        "content_version": snapshot.content_version,
    }


def prepare_questions(engine: Any, questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prepared = deepcopy(questions)
    for question in prepared:
        if isinstance(question, dict) and question.get("selection") is not None:
            question["selection"] = bind_target(engine, question["selection"])
    return prepared


def validate_selection_answer(engine: Any, target: dict[str, Any], raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict) or not raw.get("content_version"):
        raise ValueError("请在表格中选择区域并确认，或输入说明")
    workspace = getattr(engine, "_workspace_ref", None)
    if workspace is None or raw.get("workspace_id") != target.get("workspace_id") or raw.get("workspace_id") != workspace.workspace_id:
        raise ValueError("选区不属于当前问题的工作区")
    path = raw.get("file_path")
    if not isinstance(path, str) or resolve_canonical(workspace.root, path).relative != resolve_canonical(workspace.root, target["file_path"]).relative:
        raise ValueError("请在问题指定的文件中选择区域")
    if raw.get("sheet") != target["sheet"]:
        raise ValueError("请在问题指定的工作表中选择区域")
    # Re-read once before accepting. A later write still uses normal optimistic concurrency.
    return bind_target(engine, raw, require_ranges=True)
