"""V2 workbook tool surface. All workbook clients use WorkbookService."""
from __future__ import annotations

from typing import Any
import jsonschema
from zipfile import BadZipFile
from xml.etree.ElementTree import ParseError
from openpyxl.utils.exceptions import InvalidFileException
try:
    from lxml.etree import XMLSyntaxError
except ImportError:
    XMLSyntaxError = ParseError

from excelmanus.engine_core.tool_result import ToolResult, from_payload, error_result
from excelmanus.tools.registry import ToolDef
from excelmanus.tools.context import bind_workspace, ToolContextMissing
from excelmanus.tools._helpers import MutationAborted
from excelmanus.workbook.service import WorkbookService
from excelmanus.workbook.contracts import operation_schema
from excelmanus.workbook.observation import FACETS
from excelmanus.workbook.snapshot import SnapshotError
from excelmanus.workbook_commit import CommitError
from excelmanus.security import SecurityViolationError
from excelmanus.tools.workbook_query_schemas import QUERY_SCHEMAS


def init_guard(workspace_root: str) -> None:
    bind_workspace(workspace_root)


def _v2_model_text(payload: dict[str, Any]) -> str:
    """Bounded model projection; SDK/UI keep the complete structured value."""
    parts = [str(payload.get("status") or "success")]
    if payload.get("schema_version"):
        parts.append(str(payload["schema_version"]))
    for key in ("file_path", "content_version", "observation_id", "snapshot_id", "attachment_id", "render_id"):
        value = payload.get(key)
        if value:
            parts.append(f"{key}={value}")
    if isinstance(payload.get("files"), list):
        parts.append(f"files={len(payload['files'])}")
        for item in payload["files"][:4]:
            if isinstance(item, dict):
                parts.append(f"{item.get('file_path', '')}@{item.get('content_version', '')}")
                observation = item.get("observation")
                if isinstance(observation, dict):
                    changes = observation.get("geometry_changes") or []
                    if changes:
                        directions = []
                        for change in changes[:4]:
                            before, after = change.get("before"), change.get("persisted")
                            if isinstance(before, dict) and isinstance(after, dict):
                                directions.append(f"W:{before.get('width_px')}→{after.get('width_px')},H:{before.get('height_px')}→{after.get('height_px')}")
                        if directions:
                            parts.append("geometry=" + ";".join(directions))
                    parts.append(f"visual_observed={observation.get('visual_observed', False)}")
    if isinstance(payload.get("regions"), list):
        parts.append(f"regions={len(payload['regions'])}")
        coverage = payload.get("coverage")
        if isinstance(coverage, dict):
            parts.append(f"coverage={coverage.get('scope', 'requested')}")
    if isinstance(payload.get("applied"), list):
        parts.append("applied=" + ",".join(str(item) for item in payload["applied"][:12]))
    document = payload.get("document")
    if isinstance(document, dict):
        parts.append(f"purpose={document.get('purpose', 'data')}")
        parts.append(f"uncertainties={len(document.get('uncertainties') or [])}")
    return " ".join(parts)


def _invoke(function, *args, **kwargs) -> ToolResult:
    try:
        result = function(*args, **kwargs)
        if isinstance(result, ToolResult):
            return result
        if isinstance(result, dict) and result.get("schema_version") == "workbook/2":
            return from_payload(result, model_text=_v2_model_text(result))
        return from_payload(result)
    except ToolContextMissing as exc:
        return error_result(str(exc), code="TOOL_CONTEXT_MISSING")
    except MutationAborted as exc:
        return exc.result
    except SecurityViolationError as exc:
        return error_result(str(exc), code="PATH_INVALID")
    except (BadZipFile, ParseError, XMLSyntaxError, InvalidFileException, ValueError, TypeError, KeyError, OSError, SnapshotError, CommitError, SecurityViolationError, jsonschema.ValidationError, ToolContextMissing) as exc:
        from excelmanus.workbook.spec import SpecValidationError
        if isinstance(exc, SpecValidationError):
            return from_payload(exc.to_payload())
        return error_result(str(exc), code=getattr(exc, "code", "INVALID_ARGS"), fields=getattr(exc, "fields", None))


def observe_spreadsheet(file_path: str, mode: str = "overview", sheet: str | None = None,
                        range: str | None = None, facets: list[str] | None = None,
                        expected_version: str | None = None, query: str = "", offset: int = 0, limit: int = 50) -> ToolResult:
    from excelmanus.engine_core.spill import is_spill_reference, retrieve_spill_result
    from excelmanus.tools.context import require_guard
    if is_spill_reference(file_path):
        return retrieve_spill_result(file_path, workspace_root=require_guard().workspace_root)
    return _invoke(WorkbookService().observe, file_path, mode=mode, sheet=sheet, range=range, facets=facets,
                   expected_version=expected_version, query=query, offset=offset, limit=limit)


def apply_spreadsheet_changes(file_path: str = "", operations: list[dict] | None = None,
                               expected_version: str | None = None, create: bool = False,
                               workbook_spec: dict | None = None, workbooks: list[dict] | None = None,
                               read_dependencies: list[dict] | None = None, dry_run: bool = False) -> ToolResult:
    return _invoke(WorkbookService().apply, file_path, operations=operations, expected_version=expected_version,
                   create=create, workbook_spec=workbook_spec, workbooks=workbooks, read_dependencies=read_dependencies, dry_run=dry_run)


def preview_spreadsheet(file_path: str, sheet: str, range: str, expected_version: str | None = None,
                        surface: str = "workbench", page: int = 1) -> ToolResult:
    return _invoke(WorkbookService().preview, file_path, sheet=sheet, range=range,
                   expected_version=expected_version, surface=surface, page=page)


def _query(name: str, arguments: dict) -> ToolResult:
    return _invoke(WorkbookService().query, name, arguments)


def analyze_spreadsheet(**request) -> ToolResult:
    return _query("analyze_spreadsheet", request)


def compare_spreadsheets(**request) -> ToolResult:
    return _query("compare_spreadsheets", request)


def trace_spreadsheet_formulas(**request) -> ToolResult:
    return _query("trace_spreadsheet_formulas", request)


def split_spreadsheet(**request) -> ToolResult:
    return _query("split_spreadsheet", request)


def manage_spreadsheet_versions(**request) -> ToolResult:
    return _query("manage_spreadsheet_versions", request)


def get_tools() -> list[ToolDef]:
    from excelmanus.workbook.spec import workbook_spec_json_schema
    spec = workbook_spec_json_schema()
    defs = spec.pop("$defs", {})
    changes = {"dry_run": {"type":"boolean", "description":"Compile and verify without publishing files; revalidate versions on real commit"}, "file_path": {"type": "string"}, "operations": {"type": "array", "minItems": 1, "items": operation_schema()},
               "expected_version": {"type": "string", "description": "修改已有文件必须提供观察到的 content_version"},
               "create": {"type": "boolean", "default": False}, "workbook_spec": spec,
               "read_dependencies": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                   "properties": {"path": {"type": "string"}, "version": {"type": "string"}}, "required": ["path", "version"]}}}
    mutation_schema = {"type": "object", "additionalProperties": False, "$defs": defs,
        "anyOf": [{"required": ["file_path", "operations"]}, {"required": ["file_path", "workbook_spec"]}, {"required": ["workbooks"]}],
        "properties": {**changes, "workbooks": {"type": "array", "minItems": 1, "items": {
            "type": "object", "additionalProperties": False, "properties": {key:value for key,value in changes.items() if key != "dry_run"}, "required": ["file_path"]}}}}
    tools = [
        ToolDef(name="observe_spreadsheet", description="读取同一版本的工作簿事实。overview 总览，range 定点观察；facets=data/presentation/geometry/objects/dependencies 可组合。geometry 包含有效尺寸、隐藏状态和宽高比例。空与未查询有不同 coverage。", func=observe_spreadsheet, write_effect="none", max_result_chars=0,
            input_schema={"type": "object", "additionalProperties": False, "required": ["file_path"], "properties": {
                "file_path": {"type": "string"}, "mode": {"type": "string", "enum": ["overview", "range", "search", "objects", "dependencies"]},
                "sheet": {"type": "string"}, "range": {"type": "string", "description": "Excel 1-based A1 区域；可用逗号并集，整轴按已用范围有界裁剪"},
                "facets": {"type": "array", "items": {"type": "string", "enum": list(FACETS)}},
                "expected_version": {"type": "string"}, "query": {"type": "string"},
                "offset": {"type": "integer", "minimum": 0}, "limit": {"type": "integer", "minimum": 1, "maximum": 500}}}),
        ToolDef(name="preview_spreadsheet", description="观察指定工作表区域的图像与几何，图像直接进入视觉上下文。workbench 与当前工作台共用渲染模块；print 为 LibreOffice 打印页。仅写派生缓存，不改工作区文件，read/plan 可用。", func=preview_spreadsheet, write_effect="none", max_result_chars=0,
            input_schema={"type": "object", "additionalProperties": False, "required": ["file_path", "sheet", "range"], "properties": {
                "file_path": {"type": "string"}, "sheet": {"type": "string"}, "range": {"type": "string"},
                "expected_version": {"type": "string"}, "surface": {"type": "string", "enum": ["workbench", "print"]},
                "page": {"type": "integer", "minimum": 1}}}),
        ToolDef(name="apply_spreadsheet_changes", description="一次事务完成新建、值、公式、格式、合并、列宽行高、对象和打印设置。operations 按 kind 查字段。geometry.scale 的 x 控制横向、y 控制纵向；size 使用 column_widths(字符) 与 row_heights(pt)。返回最终版本和实际几何变动，未看图不声称视觉完成。", func=apply_spreadsheet_changes, write_effect="workspace_write", max_result_chars=0, input_schema=mutation_schema),
    ]
    descriptions = {
        "analyze_spreadsheet": "分析版本绑定的数据：profile/quality/filter/aggregate/distinct/pivot/relationships/files。版式观察用 observe_spreadsheet。",
        "compare_spreadsheets": "比较两个工作簿或工作表；ignore_style=false 同时对比样式、尺寸、合并、规则和对象 XML。像素相似需要独立图像观察。",
        "trace_spreadsheet_formulas": "追踪公式先例、依赖和修改影响，报告覆盖与不支持项。",
        "split_spreadsheet": "按列分组生成多个工作簿，保留原始文件并报告对象复制边界。",
        "manage_spreadsheet_versions": "列出、创建或删除检查点，按版本恢复工作簿。恢复必须提供 expected_version。",
    }
    for name, schema in QUERY_SCHEMAS.items():
        tools.append(ToolDef(name=name, description=descriptions[name], input_schema=schema, func=globals()[name],
            write_effect="workspace_write" if name in {"split_spreadsheet", "manage_spreadsheet_versions"} else "none", max_result_chars=0))
    for tool in tools:
        tool.input_schema["x-protocol"] = "workbook/2"
        if tool.name == "observe_spreadsheet":
            from excelmanus.workbook.observation import observation_schema
            tool.output_schema = observation_schema()
    return tools
