"""模型面表格意图工具：九类职责，复用本仓库提交路径与 openpyxl 实现。

模型与 Code Mode SDK 只看到这九个名字。旧微工具（read_excel / filter_data /
create_excel_chart 等）只作为本模块的内部实现，不再注册、也不作为回退入口。
Host 仍拥有锁、版本校验和宏字节保留。
"""

from __future__ import annotations

import json
import re
from typing import Any, NoReturn

from openpyxl.utils import column_index_from_string, get_column_letter
from openpyxl.utils.cell import coordinate_to_tuple

from excelmanus.engine_core.tool_result import ToolResult, ToolUiMeta, error_result, from_payload
from excelmanus.logger import get_logger
from excelmanus.prompt.canonical import TOOL_DESCRIPTIONS
from excelmanus.security import FileAccessGuard, SecurityViolationError
from excelmanus.tools.context import bind_workspace, require_guard
from excelmanus.tools._helpers import (
    MutationAborted,
    OUTSIDE_WORKSPACE_MESSAGE,
    commit_error_result,
    commit_workbook_tool,
    get_worksheet,
    prepare_excel_commit_path,
    resolve_sheet_name,
    unwrap_mutation_abort,
    workspace_relpath,
)
from excelmanus.workbook.address import (
    column_map_to_list,
    combine_sheet_names,
    looks_like_coordinate_error,
    parse_sheet_address,
    resolve_range_to_bounds,
    top_left_cell,
    worksheet_used_shape,
)
from excelmanus.workbook.refs import (
    CellRef,
    InvalidRefError,
    NamedRef,
    RectRef,
    TableRef,
    describe_for_schema,
    parse_ref,
)
from excelmanus.workbook.snapshot import parse_bound_selection
from excelmanus.workbook.cells import assign_cell_value, _resolve_merged_cell
from excelmanus.workbook.styles import (
    _build_border,
    _build_fill,
    _patch_alignment,
    _patch_font,
    apply_column_sizes,
    apply_freeze_panes,
    apply_row_sizes,
)
from excelmanus.tools.registry import ToolDef
from excelmanus.workbook_commit import (
    CommitError,
    commit_bytes,
    content_version_of_file,
    peek_seen_content_version,
)

def _range_schema_description(*, extra: str = "", alias_note: str = "") -> str:
    """常用字段短说明；完整语法由 introspect 按需查询。"""
    hint = describe_for_schema()
    parts = [
        "Excel A1（1-based 闭区间）。",
        hint["common_errors"],
        extra,
        alias_note,
    ]
    return " ".join(part for part in parts if part)


def _sheet_schema_description(*, alias_note: str = "") -> str:
    hint = describe_for_schema()
    text = (
        "工作表名。含空格的表名必须在 range 里用单引号，例如 'My Sheet'!A1。"
        f"{hint['common_errors']}"
    )
    if alias_note:
        text = f"{text} {alias_note}"
    return text


def _brief_range_description(*, extra: str = "", alias_note: str = "") -> str:
    return _range_schema_description(extra=extra, alias_note=alias_note)


def _version_param_schema() -> dict[str, Any]:
    return {
        "type": "string",
        "description": (
            "已有文件写入必须带最近一次成功读/写返回的 content_version，"
            "对应本次计算所依据的那份数据，不是事后新读到的版本。"
            "restore 必须显式传入，不能靠回退。"
        ),
    }


def _join_param_schema() -> dict[str, Any]:
    return {
        "type": ["object", "string"],
        "description": (
            "aggregate/pivot 跨表左连接（VLOOKUP 语义：左连接、右表按键去重首值）；"
            "连接键为单列名，暂不支持多列键数组。"
            "连接列可参与 group_by/aggregations/conditions。也接受 JSON 字符串。"
        ),
        "additionalProperties": False,
        "properties": {
            "sheet": {"type": "string", "description": "同簿右表名"},
            "sheet_name": {"type": "string", "description": "sheet 的别名"},
            "file_path": {"type": "string", "description": "另一文件的工作区相对路径"},
            "path": {"type": "string", "description": "file_path 的别名"},
            "on": {"type": "string", "description": "同名连接键：单列名"},
            "left_on": {"type": "string", "description": "左表键：单列名"},
            "right_on": {"type": "string", "description": "右表键：单列名"},
            "leftOn": {"type": "string", "description": "left_on 的别名"},
            "rightOn": {"type": "string", "description": "right_on 的别名"},
            "columns": {
                "type": ["array", "string"],
                "items": {"type": "string"},
                "description": "从右表带来的列；缺省为右表全部非键列",
            },
            "header_row": {
                "type": "integer",
                "description": "右表表头行（Excel 1-based）；右表表头不在第 1 行时传",
            },
            "how": {
                "type": "string",
                "enum": ["left"],
                "description": "仅支持 left（VLOOKUP 语义）",
            },
        },
    }


def _format_rule_schema() -> dict[str, Any]:
    """format_spreadsheet 的 rule：条件格式与数据验证共用字段（op.kind 决定语义）。"""
    return {
        "type": ["object", "string"],
        "description": (
            "kind=conditional_format 用条件格式字段；kind=data_validation 用数据验证字段。"
            "rule 也可传 JSON 字符串。"
        ),
        "properties": {
            "type": {
                "type": "string",
                "enum": [
                    # 条件格式
                    "cell_value", "text", "formula", "duplicate", "unique",
                    "top_n", "bottom_n", "color_scale", "data_bar", "icon_set",
                    # 数据验证（含处理器支持的别名）
                    "list", "dropdown", "whole", "int", "integer",
                    "decimal", "float", "number", "date", "time",
                    "textlength", "text_length", "length", "custom",
                ],
                "description": (
                    "条件格式: cell_value/text/formula/duplicate/unique/top_n/bottom_n/"
                    "color_scale/data_bar/icon_set；"
                    "数据验证: list/whole/decimal/date/time/textLength/custom 及别名"
                ),
            },
            "operator": {
                "type": "string",
                "description": (
                    "cell_value 条件或数据验证数值类比较符："
                    "greaterThan/greaterThanOrEqual/lessThan/lessThanOrEqual/"
                    "equal/notEqual/between/notBetween；"
                    "也收符号与别名（>= > <= < == != ge gt le lt eq ne 及 snake_case 写法）"
                ),
            },
            "value": {},
            "value2": {},
            "min": {"description": "数据验证下界（等价 value/formula1）"},
            "max": {"description": "数据验证上界（等价 value2/formula2）"},
            "values": {
                "type": ["array", "string"],
                "description": "数据验证 type=list 的候选数组；也可传公式字符串",
            },
            "formula1": {"description": "数据验证主公式/值"},
            "formula2": {"description": "数据验证第二公式/值（between 上界）"},
            "source": {
                "type": "string",
                "description": "数据验证 type=list 的引用源（等价 formula1），如 Sheet!A1:A5",
            },
            "allow_blank": {"type": "boolean"},
            "show_dropdown": {
                "type": "boolean",
                "description": "True=显示下拉箭头（模型面直觉语义，保存时自动转为 OOXML）",
            },
            "error_style": {
                "type": "string",
                "description": "数据验证错误样式：stop/warning/information",
            },
            "prompt_title": {"type": "string"},
            "prompt": {"type": "string"},
            "error_title": {"type": "string"},
            "error": {"type": "string"},
            "show_input_message": {"type": "boolean"},
            "show_error_message": {"type": "boolean"},
            "text": {"type": "string"},
            "formula": {"type": "string"},
            "n": {"type": "integer"},
            "font": {"type": "object"},
            "fill": {"type": "object"},
            "border": {"type": "object"},
            "min_color": {"type": "string"},
            "mid_color": {"type": "string"},
            "max_color": {"type": "string"},
            "bar_color": {"type": "string"},
            "icon_style": {"type": "string"},
        },
    }


def _workbook_spec_param_schema() -> dict[str, Any]:
    from excelmanus.replica_spec import workbook_spec_json_schema

    return workbook_spec_json_schema()

logger = get_logger("tools.intent")

def _get_guard() -> FileAccessGuard:
    return require_guard()


def init_guard(workspace_root: str) -> None:
    bind_workspace(workspace_root)


def _op_get(op: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in op and op[key] is not None:
            return op[key]
    return default


def _ui_from_value(payload: dict[str, Any]) -> ToolUiMeta:
    ui = ToolUiMeta()
    version = payload.get("content_version")
    if isinstance(version, str) and version:
        ui.content_version = version
    file_path = payload.get("file_path") or payload.get("path")
    if isinstance(file_path, str) and file_path.strip():
        ui.files = [file_path]
    revision = payload.get("revision")
    if isinstance(revision, dict):
        ui.revision = dict(revision)
    restored = payload.get("restored_revision")
    if isinstance(restored, str) and restored:
        extra = dict(ui.revision or {})
        extra.setdefault("revision_id", restored)
        ui.revision = extra
    return ui


def _summarize_payload(payload: dict[str, Any]) -> str:
    """给模型的有界摘要：能决策即可，禁止整包 JSON。"""
    if payload.get("mode") == "capabilities":
        tools = payload.get("model_facing") or []
        return "表格意图: " + ", ".join(str(t) for t in tools)
    parts: list[str] = []
    path = payload.get("file_path") or payload.get("path") or ""
    version = payload.get("content_version") or ""
    if path:
        parts.append(str(path))
    if version:
        parts.append(str(version))
    applied = payload.get("applied")
    if isinstance(applied, list) and applied:
        shown = ", ".join(str(item) for item in applied[:12])
        extra = f" (+{len(applied) - 12})" if len(applied) > 12 else ""
        parts.append(f"applied: {shown}{extra}")
    revision = payload.get("revision")
    if isinstance(revision, dict) and revision.get("revision_id"):
        parts.append(f"revision={revision['revision_id']}")
    revisions = payload.get("revisions")
    if isinstance(revisions, list):
        parts.append(f"revisions={len(revisions)}（本次返回）")
        parts.extend(
            f"revision={item.get('revision_id')} {item.get('reason')} {item.get('label', '')} {item.get('content_version')}"
            for item in revisions[-5:] if isinstance(item, dict)
        )
    if payload.get("restored_revision"):
        parts.append(f"restored_revision={payload['restored_revision']}")
    uncertainties = payload.get("uncertainties")
    if isinstance(uncertainties, list):
        parts.append(f"uncertainties={len(uncertainties)}")
    if parts:
        return " ".join(parts)
    keys = [str(key) for key in payload if key != "status"]
    return "ok " + " ".join(keys[:8]) if keys else "ok"

def _sheet_missing_message(name: str, available: list[str]) -> str:
    """工作表不存在时的统一错误文案：附最接近候选，便于一轮纠正。"""
    from difflib import SequenceMatcher

    best, ratio = None, 0.0
    target = name.lower()
    for candidate in available:
        r = SequenceMatcher(None, target, candidate.lower()).ratio()
        if r > ratio:
            best, ratio = candidate, r
    base = f"工作表 '{name}' 不存在。该文件包含: {list(available)}"
    if best is not None and ratio >= 0.5:
        return f"{base}。最接近的是 '{best}'（相似度 {ratio:.0%}），请确认名称后重试。"
    return f"{base}。"


def _sheet_not_found(name: str, available: list[str]) -> ToolResult:
    return _invalid(
        _sheet_missing_message(name, available),
        code="SHEET_NOT_FOUND",
        available_sheets=list(available),
    )


def _invalid(message: str, *, code: str = "INVALID_ARGS", **extra: Any) -> ToolResult:
    return error_result(message, code=code, fields=extra or None)


_OPS_MUST_BE_ARRAY = (
    "operations 必须是对象数组，每一项是 {kind,...}。"
    "不要把数组序列化成 JSON 字符串再传入。"
)

# 模型常在完整 JSON 后多写几个闭合符（} ] ,）：尾部仅含这类字符时容忍取回完整值
_JSON_TRAILING_JUNK_RE = re.compile(r"^[\s}\],]*$")
_JSON_RECOVER_MISSING = object()


def _recover_json_trailing_junk(text: str) -> Any:
    """JSON 本体完整、尾部仅杂散闭合符/空白时取回完整值，否则返回哨兵。"""
    stripped = text.lstrip()
    try:
        value, end = json.JSONDecoder().raw_decode(stripped)
    except (json.JSONDecodeError, ValueError):
        return _JSON_RECOVER_MISSING
    if _JSON_TRAILING_JUNK_RE.match(stripped[end:]):
        return value
    return _JSON_RECOVER_MISSING


def _coerce_operations(operations: Any) -> list[Any] | ToolResult:
    """模型常把 operations 序列化成 JSON 字符串，或只传一个对象。"""
    if operations in (None, ""):
        return []
    if isinstance(operations, str):
        try:
            operations = json.loads(operations)
        except json.JSONDecodeError as exc:
            recovered = _recover_json_trailing_junk(operations)
            if recovered is not _JSON_RECOVER_MISSING:
                operations = recovered
            elif exc.msg == "Extra data":
                return _invalid(
                    f"operations 的 JSON 字符串在完整值后还有多余内容（{exc}）："
                    "数组本身已完整，删掉尾部多余字符后原样重试即可，不要拆分成多次调用。"
                )
            elif exc.pos >= len(operations.rstrip()) - 2:
                return _invalid(
                    f"operations 的 JSON 字符串在末尾被截断（{exc}）："
                    "数据量大时拆成多个 op 分次调用，不要一次传超长数组。"
                )
            else:
                return _invalid(_OPS_MUST_BE_ARRAY)
    if isinstance(operations, dict):
        operations = [operations]
    if not isinstance(operations, list):
        return _invalid(_OPS_MUST_BE_ARRAY)
    coerced: list[Any] = []
    for index, item in enumerate(operations):
        if isinstance(item, str):
            try:
                item = json.loads(item)
            except json.JSONDecodeError:
                recovered = _recover_json_trailing_junk(item)
                if recovered is _JSON_RECOVER_MISSING:
                    return _invalid(f"operations[{index}] 必须是对象，不要传 JSON 字符串。")
                item = recovered
        coerced.append(item)
    return coerced


_INSPECT_MODE_FIELDS: dict[str, frozenset[str]] = {
    "overview": frozenset({
        "request", "mode", "file_path", "path", "sheet", "sheet_name",
        "include", "max_rows", "directory", "query", "header_row",
    }),
    "range": frozenset({
        "request", "mode", "file_path", "path", "sheet", "sheet_name",
        "range", "cell_range", "include", "header_row",
        "max_rows", "offset", "sample_rows",
        "expected_version", "content_version",
    }),
    "search": frozenset({
        "request", "mode", "file_path", "path", "sheet", "sheet_name",
        "query", "match_mode", "max_results", "directory",
    }),
    "capabilities": frozenset({"request", "mode"}),
}
_INSPECT_DEFAULTS = {
    "match_mode": "contains",
    "directory": ".",
    "max_results": 50,
}
_ANALYZE_MODE_FIELDS: dict[str, frozenset[str]] = {
    "profile": frozenset({
        "request", "mode", "file_path", "path", "sheet", "sheet_name", "max_rows",
    }),
    "quality": frozenset({
        "request", "mode", "file_path", "path", "sheet", "sheet_name", "max_rows",
    }),
    "filter": frozenset({
        "request", "mode", "file_path", "path", "sheet", "sheet_name", "header_row",
        "column", "operator", "value", "conditions", "logic", "columns",
        "max_rows", "sort_by", "ascending", "limit",
    }),
    "aggregate": frozenset({
        "request", "mode", "file_path", "path", "sheet", "sheet_name", "header_row",
        "group_by", "aggregations", "conditions", "logic", "join",
        "column", "operator", "value", "sort_by", "ascending", "limit", "max_rows",
    }),
    "distinct": frozenset({
        "request", "mode", "file_path", "path", "sheet", "sheet_name", "header_row",
        "column", "conditions", "logic", "limit", "max_rows", "dup_only",
    }),
    "relationships": frozenset({
        "request", "mode", "file_path", "path", "file_paths", "paths",
        "directory", "max_files", "sample_rows",
    }),
    "pivot": frozenset({
        "request", "mode", "file_path", "path", "sheet", "sheet_name", "header_row",
        "index", "columns", "values", "aggfunc", "group_by",
        "conditions", "logic", "join", "column", "operator", "value", "limit", "max_rows",
        "margins", "margins_name", "totals", "totals_name", "grand_total",
    }),
    "files": frozenset({
        "request", "mode", "directory", "include", "query", "max_files", "file_path", "path",
    }),
}
_ANALYZE_DEFAULTS = {
    "logic": "and",
    "ascending": True,
    "directory": ".",
}
_TRACE_MODE_FIELDS: dict[str, frozenset[str]] = {
    "map": frozenset({"request", "mode", "file_path", "path", "detail"}),
    "trace": frozenset({
        "request", "mode", "file_path", "path", "target", "direction", "depth",
    }),
    "impact": frozenset({
        "request", "mode", "file_path", "path", "target", "scope",
    }),
}
_TRACE_DEFAULTS = {
    "direction": "both",
    "depth": 2,
    "detail": "summary",
}


def _reject_mode_fields(
    args: dict[str, Any],
    mode: str,
    allowed: frozenset[str],
    defaults: dict[str, Any],
    *,
    required_for_mode: list[str] | None = None,
    missing_required: list[str] | None = None,
) -> ToolResult | None:
    extras: list[str] = []
    for key, value in args.items():
        if key in allowed:
            continue
        if key in defaults and value == defaults[key]:
            continue
        if value in (None, "", [], {}):
            continue
        extras.append(str(key))
    missing = [str(item) for item in (missing_required or []) if str(item)]
    if not extras and not missing:
        return None
    bits: list[str] = []
    if extras:
        bits.append(
            f"mode={mode} 不接受字段：{', '.join(extras)}；"
            f"可用字段: {', '.join(sorted(allowed))}"
        )
    if missing:
        bits.append(f"mode={mode} 需要 {', '.join(missing)}")
    payload: dict[str, Any] = {
        "accepted_fields": sorted(allowed),
        "required_for_mode": list(required_for_mode or []),
    }
    if extras:
        payload["ignored_fields"] = extras
    if missing:
        payload["missing_fields"] = missing
    return _invalid("；".join(bits), **payload)


def _arg_provided(args: dict[str, Any], key: str) -> bool:
    if key not in args:
        return False
    value = args[key]
    if value is None or value == "":
        return False
    if isinstance(value, dict) and not value:
        return False
    if isinstance(value, list) and not value and key != "conditions":
        return False
    return True


_ANALYZE_REQUIRED_FOR_MODE: dict[str, list[str]] = {
    "pivot": ["index", "columns", "values"],
    "filter": ["column+operator+value 或 conditions"],
}
_INSPECT_ONLY_ANALYZE_MODES = frozenset({"overview", "range", "search", "capabilities"})


def _analyze_missing_required(mode: str, args: dict[str, Any]) -> list[str]:
    if mode == "pivot":
        missing: list[str] = []
        if not (_arg_provided(args, "index") or _arg_provided(args, "group_by")):
            missing.append("index")
        if not _arg_provided(args, "columns"):
            missing.append("columns")
        if not _arg_provided(args, "values"):
            missing.append("values")
        return missing
    if mode == "filter":
        has_conditions = _arg_provided(args, "conditions")
        has_triple = all(_arg_provided(args, key) for key in ("column", "operator", "value"))
        if has_conditions or has_triple:
            return []
        return ["column+operator+value 或 conditions"]
    return []


def _address_has_sheet(raw: Any) -> bool:
    if raw in (None, ""):
        return False
    try:
        area = parse_ref(str(raw))
    except InvalidRefError:
        return False
    return any(bool(getattr(part, "sheet", None)) for part in area.areas)


def _workbook_sheetnames(wb: Any) -> list[str]:
    return [str(n) for n in list(getattr(wb, "sheetnames", []) or []) if str(n)]


def _op_has_sheet(
    op: dict[str, Any],
    *address_keys: str,
    sheet_keys: tuple[str, ...] = ("sheet", "sheet_name"),
) -> bool:
    if _op_get(op, *sheet_keys):
        return True
    return any(_address_has_sheet(_op_get(op, key)) for key in address_keys)


def _maybe_bind_unique_sheet(
    wb: Any,
    op: dict[str, Any],
    *address_keys: str,
    sheet_keys: tuple[str, ...] = ("sheet", "sheet_name"),
) -> None:
    """I8：恰好一张表且未指定 sheet 时自动绑定。多表绝不挑选。"""
    if wb is None or _op_has_sheet(op, *address_keys, sheet_keys=sheet_keys):
        return
    names = _workbook_sheetnames(wb)
    if len(names) == 1:
        op.setdefault("sheet", names[0])


def _require_explicit_sheet(
    op: dict[str, Any],
    action: str,
    *address_keys: str,
    sheet_keys: tuple[str, ...] = ("sheet", "sheet_name"),
    wb: Any = None,
) -> None:
    _maybe_bind_unique_sheet(wb, op, *address_keys, sheet_keys=sheet_keys)
    if _op_has_sheet(op, *address_keys, sheet_keys=sheet_keys):
        return
    names = _workbook_sheetnames(wb) if wb is not None else []
    extra: dict[str, Any] = {}
    if names:
        extra["available_sheets"] = names
    code = "SHEET_REQUIRED" if len(names) > 1 else "INVALID_ARGS"
    raise MutationAborted(
        _invalid(f"{action} 必须提供 sheet，或在地址里写 表!A1", code=code, **extra)
    )


def _with_resolved_sheet(
    result: ToolResult,
    sheet: str | None = None,
    sheets: list[str] | None = None,
) -> ToolResult:
    if not result.success or not isinstance(result.value, dict):
        return result
    if sheet:
        result.value.setdefault("resolved_sheet", sheet)
    if sheets:
        result.value.setdefault("resolved_sheets", sheets)
    return result


def _worksheet(wb: Any, sheet: str | None) -> Any:
    try:
        return get_worksheet(wb, sheet)
    except ValueError as exc:
        names = list(getattr(wb, "sheetnames", []) or [])
        raise MutationAborted(_sheet_not_found(str(sheet or ""), names)) from exc


def _split_op_address(op: dict[str, Any], raw: str) -> tuple[str | None, str]:
    try:
        parsed = parse_sheet_address(raw)
    except InvalidRefError as exc:
        raise MutationAborted(_invalid(str(exc), code="RANGE_INVALID")) from exc
    explicit = _op_get(op, "sheet", "sheet_name")
    try:
        sheet = combine_sheet_names(
            str(explicit) if explicit not in (None, "") else None,
            parsed.sheet,
        )
    except ValueError as exc:
        raise MutationAborted(_invalid(str(exc))) from exc
    return sheet, parsed.address


def _abort_bad_address(field: str, raw: str, exc: BaseException | None = None) -> NoReturn:
    if isinstance(exc, InvalidRefError):
        raise MutationAborted(_invalid(str(exc), code="RANGE_INVALID"))
    extra = f"：{exc}" if exc is not None else ""
    raise MutationAborted(
        _invalid(
            f"{field}={raw!r} 不是合法坐标{extra}。"
            "请写 A1、A1:C5，或同表并集 A4,A11。",
            code="RANGE_INVALID",
        )
    )


def _format_rects(
    op: dict[str, Any],
    raw_range: str,
    *,
    allow_union: bool,
) -> tuple[str | None, list[str]]:
    try:
        area = parse_ref(raw_range)
    except InvalidRefError as exc:
        raise MutationAborted(_invalid(str(exc), code="RANGE_INVALID")) from exc
    if not allow_union and len(area.areas) != 1:
        raise MutationAborted(
            _invalid(
                f"merge/unmerge 只要一个矩形，不要写并集。当前 range={raw_range!r}。",
                code="REF_UNSUPPORTED",
            )
        )
    explicit = _op_get(op, "sheet", "sheet_name")
    explicit_text = str(explicit) if explicit not in (None, "") else None
    sheets: list[str | None] = []
    locals_: list[str] = []
    for part in area.areas:
        if isinstance(part, (NamedRef, TableRef)):
            raise MutationAborted(
                _invalid(
                    "format 不接受命名区域或表引用。请写成 A1:C5 或同表并集 A4,A11。",
                    code="REF_UNSUPPORTED",
                )
            )
        if not isinstance(part, (CellRef, RectRef)):
            raise MutationAborted(
                _invalid(f"format 不支持该引用：{raw_range!r}。", code="REF_UNSUPPORTED")
            )
        try:
            sheet = combine_sheet_names(explicit_text, part.sheet)
        except ValueError as exc:
            raise MutationAborted(_invalid(str(exc))) from exc
        sheets.append(sheet)
        locals_.append(part.to_a1(include_sheet=False).replace("$", ""))
    unique_sheets = {name for name in sheets}
    if len(unique_sheets) > 1:
        raise MutationAborted(
            _invalid(
                "format 并集必须在同一张表。跨表请拆成多次 format。",
                code="REF_UNSUPPORTED",
            )
        )
    return sheets[0], locals_


def _clip_format_local_range(ws: Any, local_a1: str) -> str:
    """整列/整行裁到已用范围；有界矩形原样返回。"""
    used_rows, used_cols = worksheet_used_shape(ws)
    bounds = resolve_range_to_bounds(
        local_a1,
        used_max_row=used_rows,
        used_max_col=used_cols,
        max_cells=max(used_rows * used_cols, 1),
    )
    return bounds.resolved


def _freeze_cell_from_op(op: dict[str, Any]) -> str:
    """kind=freeze 的目标格。A2=冻结首行；空串取消。"""
    raw = _op_get(op, "freeze_panes", "panes")
    if raw is not None and str(raw).strip() != "":
        text = str(raw).strip()
        lowered = text.lower()
        if lowered in {"first_row", "首行", "row1"}:
            return "A2"
        if lowered in {"first_col", "first_column", "首列", "col1"}:
            return "B1"
        return text
    rows_raw = _op_get(op, "rows")
    cols_raw = _op_get(op, "cols")
    freeze_rows = 0
    freeze_cols = 0
    if isinstance(rows_raw, (int, float)) and not isinstance(rows_raw, bool):
        freeze_rows = int(rows_raw)
    if isinstance(cols_raw, (int, float)) and not isinstance(cols_raw, bool):
        freeze_cols = int(cols_raw)
    if freeze_rows <= 0 and freeze_cols <= 0:
        return ""
    col_letter = get_column_letter(freeze_cols + 1)
    return f"{col_letter}{freeze_rows + 1}"


def _paint_format_cells(
    ws: Any,
    cell_range: str,
    raw_range: str,
    *,
    font_cfg: Any,
    fill: Any,
    border: Any,
    align_cfg: Any,
    number_format: Any,
) -> list[str]:
    try:
        data = ws[cell_range]
    except Exception as exc:
        _abort_bad_address("range", raw_range or cell_range, exc)
    if not isinstance(data, tuple):
        rows_data = ((data,),)
    elif data and not isinstance(data[0], tuple):
        rows_data = (data,)
    else:
        rows_data = data
    written: set[str] = set()
    skipped: list[str] = []
    from openpyxl.cell.cell import MergedCell

    for row in rows_data:
        for cell in row if isinstance(row, tuple) else (row,):
            coord = getattr(cell, "coordinate", "")
            if isinstance(cell, MergedCell):
                skipped.append(coord)
                cell = _merged_anchor_cell(ws, cell)
                if cell is None:
                    continue
                coord = getattr(cell, "coordinate", "")
            if coord in written:
                continue
            if font_cfg:
                cell.font = _patch_font(cell.font, font_cfg)
            if fill is not None:
                cell.fill = fill
            if border is not None:
                cell.border = border
            if align_cfg:
                cell.alignment = _patch_alignment(cell.alignment, align_cfg)
            if number_format:
                cell.number_format = str(number_format)
            if coord:
                written.add(coord)
    return skipped


def _success(payload: dict[str, Any]) -> ToolResult:
    payload.setdefault("status", "success")
    return ToolResult(
        success=True,
        model_text=_summarize_payload(payload),
        value=payload,
        ui_meta=_ui_from_value(payload),
    )


def _merge_request(request: dict[str, Any] | None, **kwargs: Any) -> dict[str, Any]:
    merged: dict[str, Any] = dict(request or {})
    for key, value in kwargs.items():
        if value is None:
            continue
        if value == "" and key not in merged:
            continue
        if key not in merged or merged[key] in (None, ""):
            merged[key] = value
    if "file_path" not in merged or not merged.get("file_path"):
        merged["file_path"] = merged.get("path") or merged.get("file_a") or ""
    if "sheet_name" not in merged or not merged.get("sheet_name"):
        merged["sheet_name"] = merged.get("sheet")
    return merged


def _normalize_source_rows_ops(operations: list[Any], file_path: str) -> None:
    """把 op 级 source_rows（schema 声明等价 selection.rows）归一为 selection 形态，
    让 validate_selection_target / 版本绑定 / write/delete_rows 执行分支复用同一契约。"""
    for op in operations:
        if not isinstance(op, dict) or _op_get(op, "selection") is not None:
            continue
        rows = _op_get(op, "source_rows")
        if isinstance(rows, str):
            try:
                rows = json.loads(rows)
            except (ValueError, TypeError):
                continue
        if not isinstance(rows, list) or not rows:
            continue
        sheet = _op_get(op, "sheet", "sheet_name")
        version = _op_get(op, "content_version")
        if not sheet or not version:
            continue
        op["selection"] = {
            "file": file_path,
            "sheet": sheet,
            "rows": rows,
            "content_version": version,
        }


def _ops_bound_selection_version(operations: list[Any]) -> tuple[bool, str | None]:
    """若操作带 selection/source_rows，返回必须使用的选择版本。"""
    versions: list[str] = []
    bound = False
    for raw in operations:
        if not isinstance(raw, dict):
            continue
        sel = parse_bound_selection(_op_get(raw, "selection"))
        rows = _op_get(raw, "source_rows")
        has_rows = isinstance(rows, list) and bool(rows)
        if sel is None and not has_rows:
            continue
        bound = True
        ver = None
        if sel is not None:
            ver = sel.snapshot.content_version
        if not ver:
            ver = _op_get(raw, "content_version")
        if ver:
            versions.append(str(ver).strip())
    uniq = {v for v in versions if v}
    if not bound:
        return False, None
    if len(uniq) != 1:
        return True, None
    return True, next(iter(uniq))


def _bind_write_origin(
    wb: Any,
    raw_start: str,
    explicit_sheet: Any,
    *,
    create_sheet_if_missing: bool = False,
) -> tuple[str, int, int]:
    from excelmanus.workbook.data import WorkbookRefBindError, _bind_area_in_workbook
    from excelmanus.workbook.refs import parse_ref
    from excelmanus.workbook.snapshot import SnapshotError

    default = str(explicit_sheet).strip() if explicit_sheet not in (None, "") else None
    try:
        area = parse_ref(raw_start, default_sheet=default)
    except InvalidRefError as exc:
        _abort_bad_address("start_cell", raw_start, exc)
    if len(area.areas) != 1:
        raise MutationAborted(
            _invalid(
                "write.start_cell 不支持并集。一次 write 只写一个矩形。",
                code="REF_UNSUPPORTED",
            )
        )
    try:
        rects = _bind_area_in_workbook(wb, area, default_sheet=default)
    except WorkbookRefBindError as exc:
        # create_workbook 场景下目标表尚不存在是正常用法：按 write 指定的表名建表后重绑一次
        target_sheets = area.sheets()
        if (
            create_sheet_if_missing
            and getattr(exc, "code", None) == "SHEET_NOT_FOUND"
            and target_sheets
        ):
            wb.create_sheet(title=target_sheets[0])
            try:
                rects = _bind_area_in_workbook(wb, area, default_sheet=default)
            except (WorkbookRefBindError, SnapshotError) as exc2:
                raise MutationAborted(
                    _invalid(str(exc2), code=getattr(exc2, "code", None))
                ) from exc2
        else:
            raise MutationAborted(_invalid(str(exc), code=exc.code)) from exc
    except SnapshotError as exc:
        raise MutationAborted(_invalid(str(exc), code=exc.code)) from exc
    if len(rects) != 1:
        raise MutationAborted(
            _invalid(
                "write.start_cell 命名/表引用解析为多区域，不能一次写入。",
                code="REF_UNSUPPORTED",
            )
        )
    rect = rects[0]
    if rect.whole_column or rect.whole_row:
        raise MutationAborted(
            _invalid(
                "write 不支持整轴引用。请写有限矩形，例如 A1:A100。",
                code="REF_UNSUPPORTED",
            )
        )
    return str(rect.sheet), int(rect.min_row), int(rect.min_col)


def _write_matrix(
    wb: Any,
    *,
    sheet: str,
    row0: int,
    col0: int,
    values: list[Any],
) -> str:
    ws = _worksheet(wb, sheet)
    width = None
    placements: list[tuple[int, int, int, int, Any, bool]] = []
    for r_idx, row in enumerate(values):
        cells = row if isinstance(row, list) else [row]
        if width is None:
            width = len(cells)
        elif len(cells) != width:
            raise MutationAborted(
                _invalid(
                    "values 必须是矩形；不同宽度请拆成多次 write。"
                    "短于目标区的格子请显式传 null（null 表示清空该格）。"
                )
            )
        for c_idx, raw in enumerate(cells):
            src_row = row0 + r_idx
            src_col = col0 + c_idx
            actual_row, actual_col, redirected = _resolve_merged_cell(ws, src_row, src_col)
            placements.append((src_row, src_col, actual_row, actual_col, raw, redirected))

    collisions: dict[tuple[int, int], list[str]] = {}
    for src_row, src_col, actual_row, actual_col, raw, _redirected in placements:
        if raw is None:
            continue
        key = (actual_row, actual_col)
        collisions.setdefault(key, []).append(f"{get_column_letter(src_col)}{src_row}")
    conflicted = {anchor: sources for anchor, sources in collisions.items() if len(sources) > 1}
    if conflicted:
        details = []
        for (ar, ac), sources in conflicted.items():
            details.append(f"{get_column_letter(ac)}{ar} <- {', '.join(sources)}")
        raise MutationAborted(
            _invalid(
                "合并区多个非空值指向同一锚点："
                + "; ".join(details)
                + "。请只写锚点格，或先 unmerge。"
            )
        )

    for src_row, src_col, actual_row, actual_col, raw, redirected in placements:
        if redirected and raw is None:
            continue
        assign_cell_value(ws, actual_row, actual_col, raw)
    return f"{get_column_letter(col0)}{row0}:{get_column_letter(col0 + width - 1)}{row0 + len(values) - 1}"


def _write_from_selection(wb: Any, op: dict[str, Any], values: list[Any]) -> str:
    sel = parse_bound_selection(_op_get(op, "selection"))
    if sel is None:
        raise MutationAborted(_invalid("selection 无法解析", code="SELECTION_STALE"))
    rows = [int(r) for r in sel.rows]
    if len(values) != len(rows):
        raise MutationAborted(
            _invalid(
                f"selection 有 {len(rows)} 行，values 有 {len(values)} 行，必须对齐。",
                code="COORD_CONTRACT",
            )
        )
    if sel.cols:
        cols = [int(c) for c in sel.cols]
        width = None
        for row in values:
            cells = row if isinstance(row, list) else [row]
            if width is None:
                width = len(cells)
            elif len(cells) != width:
                raise MutationAborted(_invalid("values 必须是矩形", code="COORD_CONTRACT"))
        if width != len(cols):
            raise MutationAborted(
                _invalid(
                    f"selection 有 {len(cols)} 列，values 宽度 {width}，必须对齐。",
                    code="COORD_CONTRACT",
                )
            )
        ws = _worksheet(wb, sel.sheet)
        for r_idx, excel_row in enumerate(rows):
            cells = values[r_idx] if isinstance(values[r_idx], list) else [values[r_idx]]
            for c_idx, raw in enumerate(cells):
                actual_row, actual_col, redirected = _resolve_merged_cell(ws, excel_row, cols[c_idx])
                if redirected and raw is None:
                    continue
                assign_cell_value(ws, actual_row, actual_col, raw)
        return f"selection:{sel.sheet}:{len(rows)}x{len(cols)}"
    start_col = 1
    raw_start = str(_op_get(op, "start_cell", "startCell", "cell", "start") or "")
    if raw_start:
        _sheet, _row0, start_col = _bind_write_origin(wb, raw_start, sel.sheet)
    ws = _worksheet(wb, sel.sheet)
    width = None
    for r_idx, excel_row in enumerate(rows):
        cells = values[r_idx] if isinstance(values[r_idx], list) else [values[r_idx]]
        if width is None:
            width = len(cells)
        elif len(cells) != width:
            raise MutationAborted(_invalid("values 必须是矩形", code="COORD_CONTRACT"))
        for c_idx, raw in enumerate(cells):
            actual_row, actual_col, redirected = _resolve_merged_cell(
                ws, excel_row, start_col + c_idx
            )
            if redirected and raw is None:
                continue
            assign_cell_value(ws, actual_row, actual_col, raw)
    return f"selection:{sel.sheet}:{len(rows)}x{width}"


def _commit(
    *,
    file_path: str,
    mutate_fn: Any,
    create: bool = False,
    expected_version: str | None = None,
    selection_bound: bool = False,
) -> ToolResult | tuple[str, Any, Any]:
    guard = _get_guard()
    try:
        safe_path, rel = prepare_excel_commit_path(guard, file_path)
    except SecurityViolationError:
        return commit_error_result(CommitError("PATH_INVALID", OUTSIDE_WORKSPACE_MESSAGE))
    except CommitError as exc:
        return commit_error_result(exc)

    try:
        cr = commit_workbook_tool(
            guard=guard,
            file_path=rel,
            mutate_fn=mutate_fn,
            expected_version=expected_version,
            create=create,
            selection_bound=selection_bound,
        )
    except CommitError as exc:
        aborted = unwrap_mutation_abort(exc)
        if aborted is not None:
            return aborted.result
        if looks_like_coordinate_error(exc):
            return _invalid(
                f"{getattr(exc, 'message', exc)}。"
                "请用 A1 或 A1:C5；工作表名放在 sheet，或写成 区域汇总!A5:C5。",
                code="RANGE_INVALID",
            )
        return commit_error_result(exc)

    return rel, safe_path, cr


def _count_sheet_formulas_and_charts(ws: Any) -> tuple[int, int]:
    formulas = 0
    for row in ws.iter_rows():
        for cell in row:
            value = cell.value
            if isinstance(value, str) and value.startswith("="):
                formulas += 1
    charts = len(getattr(ws, "_charts", None) or [])
    return formulas, charts


def _refuse_unmaintained_structure(ws: Any, action: str, target_sheet: str | None = None) -> None:
    from excelmanus.workbook.structure import assert_structure_supported

    try:
        assert_structure_supported(ws.parent, action, target_sheet=target_sheet)
    except ValueError as exc:
        raise MutationAborted(_invalid(str(exc))) from exc


def _refuse_rename_if_workbook_has_refs(wb: Any) -> None:
    _refuse_unmaintained_structure(wb.active, "sheet.rename")


def _resolve_write_values(raw: Any) -> Any:
    """write.values 归一：矩形数组、其 JSON 字符串，或 spill 句柄（内容为矩阵 JSON）。

    spill 是读侧大结果的外置句柄；写侧直接消费可免去模型把大矩阵
    逐字重序列化进参数——这是 analyze/spill → 落盘的免重排通道。
    """
    if not isinstance(raw, str):
        return raw
    text = raw.strip()
    from excelmanus.engine_core.spill import SpillNotFound, SpillStore, is_spill_locator

    if is_spill_locator(text):
        try:
            resolved = SpillStore(_get_guard().workspace_root).get(text)
        except (SpillNotFound, ValueError) as exc:
            raise MutationAborted(_invalid(f"values 的 spill 句柄无法取回：{exc}"))
        try:
            payload = json.loads(resolved)
        except json.JSONDecodeError:
            raise MutationAborted(
                _invalid("spill 句柄内容不是 JSON；write.values 只接受二维矩阵（或其 JSON / spill 句柄）。")
            )
        if isinstance(payload, dict):
            for key in ("values", "matrix"):
                if isinstance(payload.get(key), list):
                    return payload[key]
            raise MutationAborted(
                _invalid(
                    "spill 内容是对象载荷而非二维矩阵；write.values 需要二维数组。"
                    "records 类结果请消费其 selection，或用 analyze/pivot 产出矩阵。"
                )
            )
        return payload
    parsed = _recover_json_trailing_junk(text)
    if parsed is not _JSON_RECOVER_MISSING:
        return parsed
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return raw


def _apply_write(
    wb: Any, op: dict[str, Any], *, create_sheet_if_missing: bool = False
) -> str:
    values = _resolve_write_values(_op_get(op, "values"))
    if not isinstance(values, list) or not values:
        raise MutationAborted(
            _invalid("write 需要非空矩形 values（二维数组、其 JSON 字符串或 spill 句柄）")
        )
    if all(isinstance(row, dict) for row in values):
        raise MutationAborted(
            _invalid(
                "write.values 必须是二维矩阵，不能把 filter/search 的 records 对象数组当写入值。"
                "请使用返回的 selection，并提供与选择同行同列的矩阵。",
                code="COORD_CONTRACT",
            )
        )
    if parse_bound_selection(_op_get(op, "selection")) is not None:
        return _write_from_selection(wb, op, values)
    raw_start = str(_op_get(op, "start_cell", "startCell", "cell", "start") or "")
    if not raw_start:
        raise MutationAborted(_invalid("write 需要 start_cell 与非空矩形 values"))
    explicit_sheet = _op_get(op, "sheet", "sheet_name")
    sheet, row0, col0 = _bind_write_origin(
        wb, raw_start, explicit_sheet,
        create_sheet_if_missing=create_sheet_if_missing,
    )
    return _write_matrix(wb, sheet=sheet, row0=row0, col0=col0, values=values)


def _parse_at_count(op: dict[str, Any], action: str) -> tuple[int, int]:
    at_raw = _op_get(op, "at", "row", "column")
    if isinstance(at_raw, str) and at_raw.strip().isalpha():
        try:
            at = column_index_from_string(at_raw.strip().upper())
        except ValueError as exc:
            raise MutationAborted(_invalid(f"{action} 列字母无效：{at_raw}")) from exc
    else:
        try:
            at = int(at_raw or 0)
        except (TypeError, ValueError) as exc:
            raise MutationAborted(_invalid(f"{action} 的 at 必须是列字母或正整数")) from exc
    count_raw = _op_get(op, "count")
    try:
        count = 1 if count_raw is None else int(count_raw)
    except (TypeError, ValueError) as exc:
        raise MutationAborted(_invalid(f"{action} 的 count 必须是正整数")) from exc
    if at < 1 or count < 1:
        raise MutationAborted(_invalid(f"{action} 的 at/count 必须 >= 1"))
    return at, count


def _apply_insert(wb: Any, op: dict[str, Any]) -> str:
    _require_explicit_sheet(op, "insert", wb=wb)
    sheet = _op_get(op, "sheet", "sheet_name")
    axis = str(_op_get(op, "axis") or "")
    at, count = _parse_at_count(op, "insert")
    ws = _worksheet(wb, sheet)
    _refuse_unmaintained_structure(ws, "insert")
    axis_norm = axis.lower()
    if axis_norm in {"row", "rows"}:
        ws.insert_rows(at, amount=count)
        return f"rows@{at}+{count}"
    if axis_norm in {"column", "columns", "col", "cols"}:
        ws.insert_cols(at, amount=count)
        return f"cols@{at}+{count}"
    raise MutationAborted(_invalid("insert.axis 必须是 row 或 column"))


def _apply_delete_rows(wb: Any, op: dict[str, Any]) -> str:
    sel = parse_bound_selection(_op_get(op, "selection"))
    if sel is not None:
        ws = _worksheet(wb, sel.sheet)
        _refuse_unmaintained_structure(ws, "delete_rows")
        rows = sorted({int(r) for r in sel.rows}, reverse=True)
        for row in rows:
            ws.delete_rows(row, amount=1)
        return f"delete_rows:selection:{len(rows)}"
    _require_explicit_sheet(op, "delete_rows", wb=wb)
    sheet = _op_get(op, "sheet", "sheet_name")
    at, count = _parse_at_count(op, "delete_rows")
    ws = _worksheet(wb, sheet)
    _refuse_unmaintained_structure(ws, "delete_rows")
    ws.delete_rows(at, amount=count)
    return f"delete_rows@{at}x{count}"


def _apply_delete_columns(wb: Any, op: dict[str, Any]) -> str:
    _require_explicit_sheet(op, "delete_columns", wb=wb)
    sheet = _op_get(op, "sheet", "sheet_name")
    at, count = _parse_at_count(op, "delete_columns")
    ws = _worksheet(wb, sheet)
    _refuse_unmaintained_structure(ws, "delete_columns")
    ws.delete_cols(at, amount=count)
    return f"delete_columns@{at}x{count}"


def _apply_pivot(wb: Any, op: dict[str, Any], file_path: str) -> str:
    from excelmanus.workbook.data import (
        _apply_join,
        _build_condition_mask,
        _materialize_derived_keys,
        _normalize_conditions,
        _normalize_group_keys,
        _normalize_join,
        _pivot_frame,
        dataframe_from_worksheet,
        write_dataframe_to_worksheet,
    )

    _require_explicit_sheet(op, "pivot", wb=wb)
    source = str(_op_get(op, "sheet", "sheet_name") or "")
    target = str(_op_get(op, "target_sheet", "new_name") or source)
    header_row = _op_get(op, "header_row") or 1
    src = _worksheet(wb, source)
    df = dataframe_from_worksheet(src, header_row=int(header_row))
    if df.empty and source == target:
        raise MutationAborted(
            _invalid(
                f"pivot 的 sheet '{source}' 是空表；sheet 要指向含数据的源表，"
                "写入目标表由 target_sheet 指定（不存在会自动创建）"
            )
        )
    join_spec, join_err = _normalize_join(_op_get(op, "join"), df)
    if join_err is not None:
        raise MutationAborted(join_err)
    if join_spec is not None:
        merged, _unmatched, merge_err = _apply_join(df, join_spec, file_path)
        if merge_err is not None:
            raise MutationAborted(merge_err)
        assert merged is not None
        df = merged
    keys, derived_specs, keys_err = _normalize_group_keys(_op_get(op, "group_by"), df.columns)
    if keys_err is not None:
        raise MutationAborted(keys_err)
    idx_keys, idx_derived, idx_err = _normalize_group_keys(_op_get(op, "index"), df.columns)
    if idx_err is not None:
        raise MutationAborted(idx_err)
    col_keys, col_derived, col_err = _normalize_group_keys(
        _op_get(op, "columns", "pivot_columns"), df.columns
    )
    if col_err is not None:
        raise MutationAborted(col_err)
    assert derived_specs is not None and idx_derived is not None and col_derived is not None
    work, mat_err = _materialize_derived_keys(df, [*derived_specs, *idx_derived, *col_derived])
    if mat_err is not None:
        raise MutationAborted(mat_err)
    assert work is not None
    cond_list, cond_err = _normalize_conditions(
        work,
        column=_op_get(op, "column"),
        operator=_op_get(op, "operator"),
        value=_op_get(op, "value"),
        conditions=_op_get(op, "conditions"),
        logic=str(_op_get(op, "logic") or "and"),
        require=False,
    )
    if cond_err is not None:
        raise MutationAborted(cond_err)
    assert cond_list is not None
    mask, mask_err = _build_condition_mask(work, cond_list, str(_op_get(op, "logic") or "and"))
    if mask_err is not None:
        raise MutationAborted(mask_err)
    filtered = work[mask] if mask is not None else work
    index = idx_keys or keys
    table, pivot_err = _pivot_frame(
        filtered,
        index=index,
        columns=col_keys,
        values=_op_get(op, "values", "pivot_values"),
        aggfunc=str(_op_get(op, "aggfunc") or "sum"),
        margins=_op_get(op, "margins", "totals", "grand_total"),
        margins_name=_op_get(op, "margins_name", "totals_name") or "合计",
    )
    if pivot_err is not None:
        raise MutationAborted(_invalid(pivot_err))
    assert table is not None
    if resolve_sheet_name(target, wb.sheetnames) is None:
        wb.create_sheet(title=target)
    dest = _worksheet(wb, target)
    write_dataframe_to_worksheet(dest, table)
    return f"pivot:{target}:{len(table)}"


def _apply_transform(wb: Any, op: dict[str, Any]) -> str:
    from excelmanus.workbook.data import apply_transform_frame, dataframe_from_worksheet, write_dataframe_to_worksheet

    _require_explicit_sheet(op, "transform", wb=wb)
    sheet = str(_op_get(op, "sheet", "sheet_name") or "")
    header_row = _op_get(op, "header_row") or 1
    action = str(_op_get(op, "action", "transform") or "").strip().lower()
    if not action:
        # 模型常省略 action 只给特征字段——按字段推断，避免无意义重试。
        if _op_get(op, "key_columns") is not None:
            action = "dedupe"
        elif (
            _op_get(op, "delimiter", "sep") is not None
            or _op_get(op, "new_columns") is not None
            or _op_get(op, "into") is not None
        ):
            action = "split"
    ws = _worksheet(wb, sheet)
    df = dataframe_from_worksheet(ws, header_row=int(header_row))
    # into 在别处语义是目标锚点（{"start_cell": "A1"}），只有列表/字符串才视作新列名。
    _into = _op_get(op, "into")
    _names = _op_get(op, "new_columns")
    if _names is None and isinstance(_into, (list, str)):
        _names = _into
    out, err = apply_transform_frame(
        df,
        action,
        key_columns=_op_get(op, "key_columns"),
        key_normalizers=_op_get(op, "key_normalizers"),
        keep=str(_op_get(op, "keep") or "first"),
        order_by=_op_get(op, "order_by"),
        column=_op_get(op, "column"),
        delimiter=str(_op_get(op, "delimiter", "sep") or ","),
        into=_names,
    )
    if err is not None:
        raise MutationAborted(_invalid(err))
    assert out is not None
    write_dataframe_to_worksheet(ws, out)
    if action == "dedupe":
        keys = _op_get(op, "key_columns") or []
        normalizers = _op_get(op, "key_normalizers") or {}
        keep = str(_op_get(op, "keep") or "first")
        order_by = _op_get(op, "order_by")
        rule = f"keep={keep}" + (f" order_by={order_by}" if order_by else "")
        return (
            f"transform:dedupe rows={len(df)}→{len(out)} removed={len(df) - len(out)} "
            f"keys={keys} key_normalizers={normalizers} {rule} "
            f"output_range=A1:{get_column_letter(max(1, len(out.columns)))}{len(out) + 1}"
        )
    return f"transform:{action}:{len(out)}"


def _apply_sheet(wb: Any, op: dict[str, Any]) -> str:
    action = str(_op_get(op, "action") or "")
    name = _op_get(op, "sheet", "sheet_name")
    new_name = _op_get(op, "new_name", "newName")
    if action == "create":
        title = new_name or name
        if not title:
            raise MutationAborted(
                _invalid("sheet.create 需要新表名：new_name，或把新表名写在 sheet / sheet_name")
            )
        if resolve_sheet_name(title, wb.sheetnames) is not None:
            raise MutationAborted(_invalid(f"工作表已存在：{title}"))
        wb.create_sheet(title=str(title))
        return f"create:{title}"
    if action == "rename":
        if not name or not new_name:
            raise MutationAborted(_invalid("sheet.rename 需要 sheet 与 new_name"))
        resolved = resolve_sheet_name(name, wb.sheetnames)
        if resolved is None:
            raise MutationAborted(_sheet_not_found(name, wb.sheetnames))
        if resolve_sheet_name(new_name, wb.sheetnames) is not None:
            raise MutationAborted(_invalid(f"工作表已存在：{new_name}"))
        _refuse_rename_if_workbook_has_refs(wb)
        wb[resolved].title = str(new_name)
        return f"rename:{resolved}->{new_name}"
    if action == "delete":
        if not name:
            raise MutationAborted(_invalid("sheet.delete 需要 sheet"))
        resolved = resolve_sheet_name(name, wb.sheetnames)
        if resolved is None:
            raise MutationAborted(_sheet_not_found(name, wb.sheetnames))
        if len(wb.sheetnames) <= 1:
            raise MutationAborted(_invalid("不能删除唯一的工作表"))
        _refuse_unmaintained_structure(wb[resolved], "sheet.delete", target_sheet=resolved)
        del wb[resolved]
        return f"delete:{resolved}"
    if action == "copy":
        if not name or not new_name:
            raise MutationAborted(_invalid("sheet.copy 需要 sheet 与 new_name"))
        resolved = resolve_sheet_name(name, wb.sheetnames)
        if resolved is None:
            raise MutationAborted(_sheet_not_found(name, wb.sheetnames))
        if resolve_sheet_name(new_name, wb.sheetnames) is not None:
            raise MutationAborted(_invalid(f"工作表已存在：{new_name}"))
        copied = wb.copy_worksheet(wb[name])
        copied.title = str(new_name)
        return f"copy:{name}->{new_name}"
    raise MutationAborted(_invalid("sheet.action 必须是 create/copy/rename/delete"))


def _apply_copy(wb: Any, op: dict[str, Any]) -> str:
    src_ok = bool(
        _op_get(op, "source_sheet", "sourceSheet")
        or _address_has_sheet(_op_get(op, "source_range", "sourceRange"))
    )
    dst_ok = bool(
        _op_get(op, "target_sheet", "targetSheet")
        or _address_has_sheet(_op_get(op, "target_start", "targetStart"))
    )
    if not src_ok or not dst_ok:
        raise MutationAborted(
            _invalid("copy 必须提供 source_sheet 与 target_sheet，或在地址里写 表!A1")
        )
    src_sheet = _op_get(op, "source_sheet", "sourceSheet")
    raw_src_range = str(_op_get(op, "source_range", "sourceRange") or "")
    dst_sheet = _op_get(op, "target_sheet", "targetSheet")
    raw_dst_start = str(_op_get(op, "target_start", "targetStart") or "A1")
    src_from_range, src_range = parse_sheet_address(raw_src_range)
    dst_from_start, dst_start = parse_sheet_address(raw_dst_start)
    try:
        src_sheet = combine_sheet_names(
            str(src_sheet) if src_sheet not in (None, "") else None,
            src_from_range,
        )
        dst_sheet = combine_sheet_names(
            str(dst_sheet) if dst_sheet not in (None, "") else None,
            dst_from_start,
        )
    except ValueError as exc:
        raise MutationAborted(_invalid(str(exc))) from exc
    if not src_sheet or not src_range or not dst_sheet:
        raise MutationAborted(_invalid("copy 需要 source_sheet/source_range/target_sheet"))
    try:
        dst_start = top_left_cell(dst_start) or "A1"
        start_row, start_col = coordinate_to_tuple(dst_start.upper())
    except Exception as exc:
        _abort_bad_address("target_start", raw_dst_start, exc)
    src = _worksheet(wb, src_sheet)
    dst = _worksheet(wb, dst_sheet)
    used_max_row, used_max_col = worksheet_used_shape(src)
    try:
        bounds = resolve_range_to_bounds(
            src_range,
            used_max_row=used_max_row,
            used_max_col=used_max_col,
        )
    except ValueError as exc:
        raise MutationAborted(_invalid(str(exc), code="RANGE_INVALID")) from exc
    min_col, min_row, max_col, max_row = (
        bounds.min_col, bounds.min_row, bounds.max_col, bounds.max_row,
    )
    snapshot = [
        (row, col, src.cell(row=row, column=col).value)
        for row in range(min_row, max_row + 1)
        for col in range(min_col, max_col + 1)
    ]
    for row, col, value in snapshot:
        assign_cell_value(
            dst,
            start_row + row - min_row,
            start_col + col - min_col,
            value,
        )
    applied = f"{src_sheet}!{bounds.resolved}->{dst_sheet}!{dst_start}"
    if bounds.requested != bounds.resolved:
        applied = f"{applied} (from {bounds.requested})"
    return applied


def _merged_anchor_cell(ws: Any, cell: Any) -> Any | None:
    from openpyxl.cell.cell import MergedCell

    if not isinstance(cell, MergedCell):
        return cell
    coord = getattr(cell, "coordinate", "")
    for merged in ws.merged_cells.ranges:
        if coord in merged:
            return ws.cell(row=merged.min_row, column=merged.min_col)
    return None


def _canonical_format_kind(kind: str) -> str:
    aliases = {
        "conditionalformat": "conditional_format",
        "cf": "conditional_format",
        "datavalidation": "data_validation",
        "dv": "data_validation",
    }
    return aliases.get(kind, kind)


def _truthy_op_field(op: dict[str, Any], *keys: str) -> bool:
    value = _op_get(op, *keys)
    if value in (None, "", False):
        return False
    if value in ([], {}):
        return False
    return True


def _format_range_optional(kind: str, op: dict[str, Any]) -> bool:
    if kind == "size":
        return (
            _truthy_op_field(op, "auto_fit", "autoFit")
            or _truthy_op_field(op, "columns", "column_widths")
            or _truthy_op_field(op, "rows", "row_heights")
        )
    if kind == "freeze":
        return True
    return False


def _format_example_op(kind: str, sheet: str) -> dict[str, Any]:
    canon = _canonical_format_kind(kind)
    if canon == "size":
        return {"kind": "size", "sheet": sheet, "columns": {"A": 18}}
    if canon == "freeze":
        return {"kind": "freeze", "sheet": sheet, "freeze_panes": "A2"}
    if canon in {"merge", "unmerge"}:
        return {"kind": canon, "sheet": sheet, "range": "A1:B1"}
    if canon == "conditional_format":
        return {
            "kind": "conditional_format",
            "sheet": sheet,
            "range": "A1:K1",
            "rule": {"type": "cell_value", "operator": "greaterThan", "value": 0},
        }
    if canon == "data_validation":
        return {
            "kind": "data_validation",
            "sheet": sheet,
            "range": "A2:A10",
            "rule": {"type": "list", "values": ["是", "否"]},
        }
    return {
        "kind": "format",
        "sheet": sheet,
        "range": "A1:K1",
        "font": {"bold": True},
    }


def _format_contract_abort(
    *,
    kind: str,
    kind_omitted: bool,
    missing: list[str],
    wb: Any,
) -> None:
    names = _workbook_sheetnames(wb)
    sheet_for_example = names[0] if names else "Sheet1"
    example = _format_example_op(kind, sheet_for_example)
    labels = {
        "sheet": "sheet（或在地址里写 表!A1）",
        "range": "range",
        "columns/rows 或 auto_fit=true": "columns/rows 或 auto_fit=true",
    }
    parts: list[str] = []
    if kind_omitted:
        parts.append("kind 未提供（缺省为 format）")
    if missing:
        listed = "、".join(labels.get(item, item) for item in missing)
        parts.append(f"{kind} 必须提供 {listed}")
    parts.append(f"最小合法示例：{json.dumps(example, ensure_ascii=False)}")
    only_sheet = missing == ["sheet"]
    code = "SHEET_REQUIRED" if only_sheet and len(names) > 1 else "INVALID_ARGS"
    extra: dict[str, Any] = {
        "example": example,
        "missing_fields": list(missing),
    }
    if names:
        extra["available_sheets"] = names
    raise MutationAborted(_invalid("。".join(parts), code=code, **extra))


def _format_bound_sheet(op: dict[str, Any], raw_range: str) -> str | None:
    """size/freeze 在已绑定 sheet 时不要把表名当 range 再解析。"""
    explicit = _op_get(op, "sheet", "sheet_name")
    if explicit not in (None, "") and (
        not str(raw_range or "").strip() or not _address_has_sheet(raw_range)
    ):
        return str(explicit)
    sheet, _local = _split_op_address(op, raw_range)
    return sheet


def _apply_format(wb: Any, op: dict[str, Any]) -> tuple[str, list[str]]:
    kind_raw = _op_get(op, "kind")
    kind_omitted = kind_raw in (None, "")
    kind = str(kind_raw or "format")
    _maybe_bind_unique_sheet(wb, op, "range", "cell_range")
    raw_range = str(_op_get(op, "range", "cell_range") or "")
    missing: list[str] = []
    if not _op_has_sheet(op, "range", "cell_range"):
        missing.append("sheet")
    if kind == "size":
        if not _format_range_optional(kind, op) and not raw_range.strip():
            missing.append("columns/rows 或 auto_fit=true")
    elif not _format_range_optional(kind, op) and not raw_range.strip():
        missing.append("range")
    if missing:
        _format_contract_abort(
            kind=kind, kind_omitted=kind_omitted, missing=missing, wb=wb,
        )
    if kind not in {"format", "merge", "unmerge", "size", "freeze", "conditional_format", "conditionalformat", "cf", "data_validation", "datavalidation", "dv"}:
        raise MutationAborted(
            _invalid(
                f"不支持的 format.kind={kind}。可用：format / merge / unmerge / size / freeze / conditional_format / data_validation"
            )
        )
    if kind == "freeze":
        sheet = _format_bound_sheet(op, raw_range)
        ws = _worksheet(wb, sheet)
        cell = _freeze_cell_from_op(op)
        applied = apply_freeze_panes(ws, cell or None)
        return f"freeze:{applied or 'off'}", []
    if kind == "size":
        from excelmanus.workbook.data import _maybe_json

        sheet = _format_bound_sheet(op, raw_range)
        ws = _worksheet(wb, sheet)
        columns = _maybe_json(_op_get(op, "columns", "column_widths")) or {}
        rows = _maybe_json(_op_get(op, "rows", "row_heights")) or {}
        auto_fit = bool(_op_get(op, "auto_fit", "autoFit"))
        axis = str(_op_get(op, "axis") or "").lower()
        if auto_fit:
            if isinstance(columns, list) and columns and all(
                isinstance(item, str) and str(item).strip().isalpha() for item in columns
            ):
                apply_column_sizes(ws, auto_fit=True, letters={str(c).upper() for c in columns})
                return "size:auto_fit:cols", []
            if axis in {"", "column", "columns", "col", "cols"}:
                apply_column_sizes(ws, auto_fit=True)
            if axis in {"", "row", "rows"}:
                apply_row_sizes(ws, auto_fit=True)
            if axis and axis not in {"column", "columns", "col", "cols", "row", "rows"}:
                raise MutationAborted(_invalid("size.axis 必须是 row 或 column"))
            return "size:auto_fit", []
        if isinstance(columns, list):
            if columns and all(isinstance(item, str) and str(item).strip().isalpha() for item in columns):
                # 列名列表（["A","B"]）只能理解为"这些列自适应"——列宽必须是数值。
                apply_column_sizes(ws, auto_fit=True, letters={str(c).upper() for c in columns})
                return "size:auto_fit:cols", []
            columns = {
                get_column_letter(index + 1): width
                for index, width in enumerate(columns)
                if isinstance(width, (int, float)) and width > 0
            }
        elif isinstance(columns, dict):
            if columns and not any(str(key).isalpha() for key in columns):
                columns = {
                    get_column_letter(index): width
                    for index, width in enumerate(column_map_to_list(columns), start=1)
                    if isinstance(width, (int, float)) and width > 0
                }
        if isinstance(rows, list):
            rows = {
                str(index + 1): height
                for index, height in enumerate(rows)
                if isinstance(height, (int, float)) and height > 0
            }
        elif isinstance(rows, dict):
            rows = {str(key): value for key, value in rows.items()}
        if not (isinstance(columns, dict) and columns) and not (isinstance(rows, dict) and rows):
            raise MutationAborted(
                _invalid('kind=size 需要 columns/rows（如 {"A":18} 或 [18,12]）或 auto_fit=true')
            )
        if isinstance(columns, dict) and columns:
            apply_column_sizes(ws, columns)
        if isinstance(rows, dict) and rows:
            apply_row_sizes(ws, rows)
        return "size", []
    if not raw_range.strip():
        raise MutationAborted(_invalid(f"{kind} 需要 range"))
    sheet, rects = _format_rects(
        op, raw_range, allow_union=(kind in {"format", "conditional_format", "conditionalformat", "cf", "data_validation", "datavalidation", "dv"})
    )
    ws = _worksheet(wb, sheet)
    rects = [_clip_format_local_range(ws, rect) for rect in rects]
    cell_range = rects[0]
    if kind in {"data_validation", "datavalidation", "dv"}:
        from openpyxl.worksheet.cell_range import CellRange

        from excelmanus.workbook.data import _maybe_json
        from excelmanus.workbook.styles import build_data_validation

        target_ranges = [CellRange(rect) for rect in rects]

        def _intersects(a: CellRange, b: CellRange) -> bool:
            return not (
                a.max_col < b.min_col or a.min_col > b.max_col
                or a.max_row < b.min_row or a.min_row > b.max_row
            )

        if _op_get(op, "remove", "delete", "clear"):
            dv_list = getattr(ws.data_validations, "dataValidation", []) or []
            kept: list[Any] = []
            removed = 0
            for existing in dv_list:
                sqref = getattr(existing, "sqref", None)
                hit = False
                if sqref is not None:
                    for rng in getattr(sqref, "ranges", []):
                        if any(_intersects(rng, target) for target in target_ranges):
                            hit = True
                            break
                if hit:
                    removed += 1
                else:
                    kept.append(existing)
            ws.data_validations.dataValidation = kept
            return f"data_validation:removed:{removed}", []

        rule_spec = _maybe_json(
            _op_get(op, "rule", "validation", "data_validation_rule")
        )
        if not isinstance(rule_spec, dict):
            rule_spec = {k: v for k, v in op.items() if k not in {"kind", "op", "range", "cell_range", "sheet"}}
        try:
            dv = build_data_validation(rule_spec)
        except (ValueError, TypeError) as exc:
            raise MutationAborted(_invalid(str(exc))) from exc
        sqref = " ".join(rects)
        try:
            ws.add_data_validation(dv)
            dv.add(sqref)
        except Exception as exc:
            _abort_bad_address("range", raw_range, exc)
        return f"data_validation:{sqref}", []
    if kind in {"conditional_format", "conditionalformat", "cf"}:
        from openpyxl.worksheet.cell_range import CellRange

        from excelmanus.workbook.data import _maybe_json
        from excelmanus.workbook.styles import build_conditional_format_rule

        if _op_get(op, "remove", "delete", "clear"):
            targets = [CellRange(rect) for rect in rects]
            cf_list = ws.conditional_formatting
            removed = 0
            for cf in list(cf_list):
                hit = any(
                    not (
                        r.max_col < t.min_col or r.min_col > t.max_col
                        or r.max_row < t.min_row or r.min_row > t.max_row
                    )
                    for r in cf.sqref.ranges
                    for t in targets
                )
                if hit:
                    removed += len(cf.rules)
                    del cf_list[cf.sqref]
            return f"conditional_format:removed:{removed}", []

        rule_spec = _maybe_json(_op_get(op, "rule", "cf_rule", "conditional_format_rule"))
        if not isinstance(rule_spec, dict):
            rule_spec = op
        try:
            anchor = cell_range.split(":")[0]
            rule = build_conditional_format_rule(rule_spec, anchor=anchor)
        except (ValueError, TypeError) as exc:
            raise MutationAborted(_invalid(str(exc))) from exc
        sqref = " ".join(rects)
        try:
            ws.conditional_formatting.add(sqref, rule)
        except Exception as exc:
            _abort_bad_address("range", raw_range, exc)
        return f"conditional_format:{sqref}", []
    if kind == "merge":
        try:
            ws.merge_cells(cell_range)
        except Exception as exc:
            _abort_bad_address("range", raw_range, exc)
        return f"merge:{cell_range}", []
    if kind == "unmerge":
        try:
            ws.unmerge_cells(cell_range)
        except Exception as exc:
            _abort_bad_address("range", raw_range, exc)
        return f"unmerge:{cell_range}", []
    font_cfg = _op_get(op, "font")
    fill_cfg = _op_get(op, "fill")
    border_cfg = _op_get(op, "border")
    align_cfg = _op_get(op, "alignment")
    try:
        fill = _build_fill(fill_cfg) if fill_cfg else None
        border = _build_border(border_cfg) if border_cfg else None
    except ValueError as exc:
        raise MutationAborted(_invalid(str(exc))) from exc
    number_format = _op_get(op, "number_format", "numberFormat", "numFmt")
    skipped: list[str] = []
    for rect in rects:
        skipped.extend(
            _paint_format_cells(
                ws,
                rect,
                raw_range,
                font_cfg=font_cfg,
                fill=fill,
                border=border,
                align_cfg=align_cfg,
                number_format=number_format,
            )
        )
    return f"format:{','.join(rects)}", skipped


def _csv_cell_value(value: Any) -> Any:
    """CSV 单元格按需转数值；"007" 类前导零编号保持字符串。"""
    if not isinstance(value, str):
        return value
    s = value.strip()
    if not s:
        return value
    if s[0] == "0" and len(s) > 1 and s.isdigit():
        return value
    try:
        f = float(s)
    except ValueError:
        return value
    if f.is_integer() and "." not in s and "e" not in s.lower():
        return int(f)
    return f


def _expand_source_csv_sheets(spec: Any, guard: Any) -> ToolResult | None:
    """把 sheets[].source_csv 展开为 A1 锚点 value_block 与 dimensions（就地修改）。"""
    from pathlib import Path

    import pandas as pd

    from excelmanus.replica_spec import SpecValidationError, ValueBlock, collect_layout_errors, _parse_a1
    from excelmanus.workbook.data import _detect_csv_encoding
    from excelmanus.workbook.snapshot import csv_separator_for

    for i, sheet in enumerate(spec.sheets):
        src = sheet.source_csv
        if src is None:
            continue
        if not isinstance(src, dict):
            return _invalid(f"sheets.{i}.source_csv 必须是对象", code="SPEC_INVALID")
        csv_path = src.get("file_path") or src.get("path")
        if not isinstance(csv_path, str) or not csv_path:
            return _invalid(f"sheets.{i}.source_csv 需要 file_path", code="SPEC_INVALID")
        try:
            live = guard.resolve_and_validate(csv_path)
        except SecurityViolationError:
            return _invalid(OUTSIDE_WORKSPACE_MESSAGE, code="PATH_INVALID")
        live = Path(live)
        if not live.exists() or not live.is_file():
            return _invalid(f"CSV 不存在: {csv_path}", code="NOT_FOUND")
        if live.suffix.lower() not in {".csv", ".tsv", ".txt"}:
            return _invalid(
                f"source_csv 只接受 CSV/TSV/TXT 文本文件，{csv_path} 是 {live.suffix or '无扩展名'}。"
                "xlsx 数据源请在 spec 里用 value_blocks 写数据，或用 analyze/inspect 先读。",
                code="INVALID_ARGS",
            )
        encoding = src.get("encoding")
        if not isinstance(encoding, str) or not encoding:
            encoding = _detect_csv_encoding(live)
        start = src.get("start") or "A1"
        skip_rows = int(src.get("skip_rows") or 0)
        try:
            df = pd.read_csv(
                str(live), sep=csv_separator_for(live),
                encoding=encoding, header=None, skiprows=skip_rows,
            )
        except Exception as exc:
            return _invalid(f"CSV 读取失败: {csv_path}: {exc}", code="CSV_READ_FAILED")
        df = df.where(pd.notna(df), None)
        values = [[_csv_cell_value(v) for v in row] for row in df.values.tolist()]
        if not values:
            return _invalid(f"CSV 为空: {csv_path}", code="CSV_EMPTY")
        try:
            s_row, s_col = _parse_a1(start)
        except Exception:
            return _invalid(f"source_csv.start 锚点无效: {start!r}", code="SPEC_INVALID")
        sheet.value_blocks.append(ValueBlock(start=start, values=values))
        if not sheet.dimensions:
            sheet.dimensions = {
                "rows": s_row - 1 + len(values),
                "cols": s_col - 1 + max(len(r) for r in values),
            }

    layout_errors = collect_layout_errors(spec)
    if layout_errors:
        exc = SpecValidationError(layout_errors)
        return from_payload(exc.to_payload())
    return None


def _verify_compiled_workbook(dest: Any, spec: Any) -> dict[str, Any]:
    """写后核验：sheet 名、used_shape、source_csv 行数。失败只记 mismatches。"""
    from openpyxl import load_workbook

    mismatches: list[dict[str, Any]] = []
    try:
        wb = load_workbook(dest, read_only=True, data_only=False)
    except Exception as exc:
        return {"ok": False, "mismatches": [{"kind": "open", "message": str(exc)}]}
    try:
        expected_names = [str(sheet.name) for sheet in spec.sheets]
        actual_names = list(wb.sheetnames)
        if set(expected_names) != set(actual_names):
            mismatches.append(
                {
                    "kind": "sheets",
                    "expected": expected_names,
                    "actual": actual_names,
                }
            )
        for sheet in spec.sheets:
            name = str(sheet.name)
            if name not in wb.sheetnames:
                mismatches.append({"kind": "missing_sheet", "sheet": name})
                continue
            rows, cols = worksheet_used_shape(wb[name])
            dims = sheet.dimensions or {}
            exp_rows = dims.get("rows")
            exp_cols = dims.get("cols")
            if isinstance(exp_rows, int) and rows != exp_rows:
                mismatches.append(
                    {
                        "kind": "rows",
                        "sheet": name,
                        "expected": exp_rows,
                        "actual": rows,
                    }
                )
            if isinstance(exp_cols, int) and cols != exp_cols:
                mismatches.append(
                    {
                        "kind": "cols",
                        "sheet": name,
                        "expected": exp_cols,
                        "actual": cols,
                    }
                )
            if sheet.source_csv and sheet.value_blocks:
                expected_csv_rows = len(sheet.value_blocks[-1].values)
                if rows < expected_csv_rows:
                    mismatches.append(
                        {
                            "kind": "source_csv_rows",
                            "sheet": name,
                            "expected": expected_csv_rows,
                            "actual": rows,
                        }
                    )
    finally:
        wb.close()
    return {"ok": not mismatches, "mismatches": mismatches}


def edit_spreadsheet(
    file_path: str = "",
    operations: list[dict[str, Any]] | None = None,
    workbook_spec: dict[str, Any] | str | None = None,
    create_workbook: bool = False,
    expected_version: str | None = None,
    path: str = "",
    content_version: str | None = None,
) -> ToolResult:
    """一次原子请求：写值/插入行列/改表结构，或编译 WorkbookSpec。"""
    file_path = file_path or path
    expected_version = expected_version or content_version
    operations = _coerce_operations(operations)
    if isinstance(operations, ToolResult):
        return operations
    if workbook_spec not in (None, ""):
        if operations:
            return _invalid("operations 与 workbook_spec 互斥")
        from excelmanus.replica_spec import SpecValidationError, compile_workbook_spec_to_bytes, validate_workbook_spec
        from excelmanus.security.source_isolation import (
            PROBE_FILE_FORBIDDEN,
            is_probe_path,
            probe_error_message,
        )

        if not file_path:
            return _invalid("workbook_spec 需要用户可见的 file_path", code="PATH_REQUIRED")
        if is_probe_path(file_path):
            return _invalid(probe_error_message(file_path), code=PROBE_FILE_FORBIDDEN)

        try:
            spec = validate_workbook_spec(workbook_spec)
        except SpecValidationError as exc:
            return from_payload(exc.to_payload())

        guard = _get_guard()
        expand_err = _expand_source_csv_sheets(spec, guard)
        if expand_err is not None:
            return expand_err
        target = file_path
        try:
            dest = guard.resolve_and_validate(target)
        except SecurityViolationError:
            return _invalid(OUTSIDE_WORKSPACE_MESSAGE, code="PATH_INVALID")
        if dest.exists():
            return _invalid(
                "规格只用于创建新簿。已有文件请改输出路径，或用 operations 更新。",
                code="SPEC_NOT_PATCH",
            )
        try:
            data, summary = compile_workbook_spec_to_bytes(spec)
        except Exception as exc:
            return _invalid(f"规格编译失败: {exc}", code="COMPILE_FAILED")
        rel = workspace_relpath(guard, dest)
        try:
            cr = commit_bytes(
                guard=guard,
                file_path=rel,
                data=data,
                expected_version=None,
            )
        except CommitError as exc:
            return commit_error_result(exc)
        from excelmanus.workbook_commit import remember_content_version

        remember_content_version(rel, cr.content_version)
        remember_content_version(cr.path, cr.content_version)
        verification = _verify_compiled_workbook(dest, spec)
        return _success(
            {
                "file_path": cr.path or rel,
                "content_version": cr.content_version,
                "uncertainties": [item.model_dump() for item in spec.uncertainties],
                "build_summary": summary,
                "verification": verification,
            }
        )
    if not operations:
        return _invalid("请提供 operations，或传入 workbook_spec 编译新表")

    from excelmanus.security.source_isolation import (
        PROBE_FILE_FORBIDDEN,
        is_probe_path,
        probe_error_message,
    )

    if is_probe_path(file_path):
        return _invalid(probe_error_message(file_path), code=PROBE_FILE_FORBIDDEN)

    from excelmanus.workbook.snapshot import SnapshotError, validate_selection_target

    _normalize_source_rows_ops(operations, file_path)
    try:
        for op in operations:
            if isinstance(op, dict) and "selection" in op:
                validate_selection_target(op["selection"], file_path, _op_get(op, "sheet", "sheet_name"))
    except (SnapshotError, ValueError, TypeError) as exc:
        return _invalid(str(exc), code="SELECTION_STALE")
    has_sel, sel_ver = _ops_bound_selection_version(operations)
    selection_bound = has_sel
    if has_sel:
        if not sel_ver:
            return _invalid(
                "带 selection/source_rows 的写入必须携带该选择的 content_version，不能省略或混用多个版本。",
                code="SELECTION_STALE",
            )
        if expected_version and expected_version != sel_ver:
            return _invalid(
                f"expected_version 与选择版本不一致：选择 {sel_ver}，请求 {expected_version}",
                code="SELECTION_STALE",
            )
        expected_version = sel_ver

    applied: list[str] = []

    def mutate(wb: Any) -> None:
        for index, raw in enumerate(operations):
            if not isinstance(raw, dict):
                raise MutationAborted(_invalid(f"operations[{index}] 必须是对象"))
            kind = str(_op_get(raw, "kind") or "")
            if kind == "write":
                applied.append(
                    _apply_write(wb, raw, create_sheet_if_missing=create_workbook)
                )
            elif kind == "insert":
                applied.append(_apply_insert(wb, raw))
            elif kind == "sheet":
                applied.append(_apply_sheet(wb, raw))
            elif kind == "copy":
                applied.append(_apply_copy(wb, raw))
            elif kind == "delete_rows":
                applied.append(_apply_delete_rows(wb, raw))
            elif kind == "delete_columns":
                applied.append(_apply_delete_columns(wb, raw))
            elif kind == "pivot":
                applied.append(_apply_pivot(wb, raw, file_path))
            elif kind == "transform":
                applied.append(_apply_transform(wb, raw))
            else:
                raise MutationAborted(
                    _invalid(
                        f"不支持的 edit.kind={kind}。"
                        "值/表结构用 write|insert|sheet|copy|delete_rows|delete_columns|pivot|transform；"
                        "外观用 format_spreadsheet"
                    )
                )

    committed = _commit(
        file_path=file_path,
        mutate_fn=mutate,
        create=create_workbook,
        expected_version=expected_version,
        selection_bound=selection_bound,
    )
    if isinstance(committed, ToolResult):
        return committed
    rel, _safe, cr = committed
    return _success(
        {
            "file_path": cr.path or rel,
            "content_version": cr.content_version,
            "applied": applied,
        }
    )


def format_spreadsheet(
    file_path: str = "",
    operations: list[dict[str, Any]] | None = None,
    expected_version: str | None = None,
    path: str = "",
    content_version: str | None = None,
) -> ToolResult:
    """一次原子请求：字体/填充/边框/对齐、合并、行列尺寸、冻结窗格。"""
    file_path = file_path or path
    expected_version = expected_version or content_version
    operations = _coerce_operations(operations)
    if isinstance(operations, ToolResult):
        return operations
    if not file_path:
        return _invalid("需要 file_path")
    if not operations:
        return _invalid("请提供 format operations")

    from excelmanus.security.source_isolation import (
        PROBE_FILE_FORBIDDEN,
        is_probe_path,
        probe_error_message,
    )

    if is_probe_path(file_path):
        return _invalid(probe_error_message(file_path), code=PROBE_FILE_FORBIDDEN)

    applied: list[str] = []
    skipped_merged_non_anchors: list[str] = []
    appearance: dict[str, Any] = {"sheets": []}

    def mutate(wb: Any) -> None:
        touched: list[str] = []
        for index, raw in enumerate(operations):
            if not isinstance(raw, dict):
                raise MutationAborted(_invalid(f"operations[{index}] 必须是对象"))
            label, skipped = _apply_format(wb, raw)
            applied.append(label)
            skipped_merged_non_anchors.extend(skipped)
            sheet = _op_get(raw, "sheet", "sheet_name")
            if sheet:
                touched.append(str(sheet))
        names = list(dict.fromkeys(touched)) or list(wb.sheetnames[:1])
        sheets_info: list[dict[str, Any]] = []
        for name in names:
            try:
                ws = get_worksheet(wb, name)
            except ValueError:
                continue
            widths: dict[str, float] = {}
            for letter, dim in list(ws.column_dimensions.items())[:16]:
                width = getattr(dim, "width", None)
                if isinstance(width, (int, float)) and width > 0:
                    widths[str(letter)] = round(float(width), 1)
            freeze = getattr(ws, "freeze_panes", None)
            sheets_info.append(
                {
                    "name": ws.title,
                    "freeze_panes": str(freeze) if freeze else None,
                    "column_widths": widths,
                }
            )
        appearance["sheets"] = sheets_info

    committed = _commit(
        file_path=file_path,
        mutate_fn=mutate,
        expected_version=expected_version,
    )
    if isinstance(committed, ToolResult):
        return committed
    rel, _safe, cr = committed
    payload: dict[str, Any] = {
        "file_path": cr.path or rel,
        "content_version": cr.content_version,
        "applied": applied,
        "appearance": appearance,
    }
    if skipped_merged_non_anchors:
        payload["skipped_merged_non_anchors"] = skipped_merged_non_anchors
    return _success(payload)


def manage_spreadsheet_versions(
    file_path: str,
    action: str,
    revision_id: str | None = None,
    expected_version: str | None = None,
    label: str | None = None,
    limit: int = 50,
) -> ToolResult:
    """列出当前版本与检查点，创建检查点，或按检查点恢复。

    后台只打 RevisionStore（``.excelmanus/revisions/``）。不再读取
    ``intent_revisions.json`` / ``outputs/.versions/rev_*``。
    """
    from excelmanus.workspace.revisions import RevisionIntegrityError, RevisionStore

    guard = _get_guard()
    try:
        dest = guard.resolve_and_validate(file_path)
    except SecurityViolationError:
        return commit_error_result(CommitError("PATH_INVALID", OUTSIDE_WORKSPACE_MESSAGE))
    rel = workspace_relpath(guard, dest).replace("\\", "/")
    current = content_version_of_file(dest) if dest.is_file() else None
    seen = peek_seen_content_version(rel)
    from excelmanus.workspace.file_service import WorkspaceFileService

    svc = WorkspaceFileService(guard.workspace_root)
    store = RevisionStore(guard.workspace_root)

    if action == "list":
        records = [rec.to_public_dict() for rec in svc.list_history(rel)]
        cap = max(1, int(limit))
        return _success(
            {
                "file_path": rel,
                "content_version": current,
                "seen_version": seen,
                "revisions": records[-cap:],
            }
        )

    if action == "checkpoint":
        if not dest.is_file() or not current:
            return _invalid("文件不存在，无法建立检查点", code="PATH_INVALID")
        rec = store.checkpoint(rel, dest.read_bytes(), label=label)
        entry = rec.to_public_dict()
        return _success({"file_path": rel, "content_version": current, "revision": entry})

    if action == "restore":
        if not revision_id:
            return _invalid("restore 需要 revision_id")
        live = dest.is_file()
        if live and not (expected_version or "").strip():
            return _invalid("restore 必须提供 expected_version", code="VERSION_CONFLICT")
        try:
            receipt = svc.restore(
                rel,
                revision_id,
                expected_version=expected_version,
                restore_missing=not live,
            )
            svc.raise_if_failed(receipt)
        except RevisionIntegrityError:
            return _invalid("检查点快照损坏", code="NOT_FOUND")
        except CommitError as exc:
            return commit_error_result(exc)
        return _success(
            {
                "file_path": receipt.primary_path() or rel,
                "content_version": receipt.primary_version(),
                "restored_revision": revision_id,
                "lineage_id": receipt.targets[-1].lineage_id if receipt.targets else None,
                "exists_after": receipt.targets[-1].exists_after if receipt.targets else True,
                "summary": (
                    f"已恢复 {receipt.primary_path() or rel} 到修订 {revision_id}"
                ),
            }
        )

    return _invalid("action 必须是 list / checkpoint / restore")


_MODEL_CAPABILITIES = {
    "mode": "capabilities",
    "model_facing": [
        "inspect_spreadsheet",
        "analyze_spreadsheet",
        "compare_spreadsheets",
        "edit_spreadsheet",
        "format_spreadsheet",
        "split_spreadsheet",
        "manage_spreadsheet_objects",
        "trace_spreadsheet_formulas",
        "manage_spreadsheet_versions",
    ],
    "inspect_modes": ["overview", "range", "search", "capabilities"],
    "analyze_modes": ["profile", "quality", "filter", "aggregate", "distinct", "relationships", "files"],
    "compare_alignments": ["position", "key"],
    "trace_modes": ["map", "trace", "impact"],
    "notes": [
        "当前会话可见工具以工具目录为准；只读与计划模式不含改表工具",
        "截断、采样、推断或缓存结果不是完整证据",
        "已有文件必须使用精确 content_version；冲突不会落盘",
        "没有结束工具",
    ],
}


def inspect_spreadsheet(
    request: dict[str, Any] | None = None,
    mode: str | None = None,
    file_path: str = "",
    path: str = "",
    sheet_name: str | None = None,
    sheet: str | None = None,
    range: str | None = None,
    cell_range: str | None = None,
    include: list[str] | None = None,
    max_rows: int | None = None,
    query: str = "",
    match_mode: str = "contains",
    directory: str = ".",
    header_row: int | None = None,
    offset: int | None = None,
    sample_rows: int | None = None,
    max_results: int = 50,
    expected_version: str | None = None,
    content_version: str | None = None,
) -> ToolResult:
    """只读探查：overview / range / search / capabilities。"""
    args = _merge_request(
        request,
        mode=mode,
        file_path=file_path or path,
        path=path,
        sheet_name=sheet_name or sheet,
        sheet=sheet,
        range=range or cell_range,
        include=include,
        max_rows=max_rows,
        query=query,
        match_mode=match_mode,
        directory=directory,
        header_row=header_row,
        offset=offset,
        sample_rows=sample_rows,
        max_results=max_results,
        expected_version=expected_version or content_version,
    )
    # include 在 schema 中为数组；SDK/裸 JSON 可能传单值或 JSON 字符串，统一归一为列表
    from excelmanus.workbook.data import _maybe_json

    _inc = _maybe_json(args.get("include"))
    if _inc is not None:
        args["include"] = _inc if isinstance(_inc, list) else [_inc]
    chosen = str(args.get("mode") or "").strip()
    target = str(args.get("file_path") or "")
    raw_range = args.get("range") or args.get("cell_range")
    if raw_range:
        try:
            parsed = parse_sheet_address(str(raw_range))
        except InvalidRefError as exc:
            return _invalid(str(exc), code="RANGE_INVALID")
        args["range"] = parsed.address
        try:
            args["sheet_name"] = combine_sheet_names(args.get("sheet_name"), parsed.sheet)
        except ValueError as exc:
            return _invalid(str(exc))
        if not chosen:
            chosen = "range"
    if not chosen:
        chosen = "overview"

    mode_err = _reject_mode_fields(
        args, chosen, _INSPECT_MODE_FIELDS.get(chosen, frozenset()), _INSPECT_DEFAULTS,
        required_for_mode=["query"] if chosen == "search" else (
            ["range"] if chosen == "range" else (
                ["file_path"] if chosen == "overview" else []
            )
        ),
    )
    if mode_err is not None:
        return mode_err

    if chosen == "capabilities":
        return _success(dict(_MODEL_CAPABILITIES))

    if chosen == "search":
        from excelmanus.workbook.data import search_excel_values

        q = str(args.get("query") or "")
        if not q:
            return _invalid("search 需要 query")
        result = search_excel_values(
            file_path=target,
            query=q,
            match_mode=str(args.get("match_mode") or args.get("searchMode") or "contains"),
            sheets=[args["sheet_name"]] if args.get("sheet_name") else None,
            max_results=int(args.get("max_results") or args.get("maxResults") or 50),
        )
        sheets = [args["sheet_name"]] if args.get("sheet_name") else None
        return _with_resolved_sheet(result, sheet=args.get("sheet_name"), sheets=sheets)

    if chosen == "range":
        from excelmanus.workbook.data import read_excel

        if not target:
            return _invalid("range 需要 file_path / path")
        include = args.get("include")
        if include:
            extra = [item for item in include if item != "formulas"]
            if extra:
                return _invalid(
                    "range 的 include 仅支持 formulas",
                    ignored_fields=extra,
                )
        cell_range = args.get("range")
        extras = [
            name
            for name, val in (
                ("max_rows", args.get("max_rows") or args.get("maxRows")),
                ("offset", args.get("offset")),
                ("sample_rows", args.get("sample_rows") or args.get("sampleRows")),
            )
            if val is not None and cell_range
        ]
        result = read_excel(
                file_path=target,
                sheet_name=args.get("sheet_name"),
                header_row=args.get("header_row") if args.get("header_row") is not None else args.get("headerRow"),
                include=include,
                range=cell_range,
                max_rows=None if cell_range else (args.get("max_rows") or args.get("maxRows")),
                offset=None if cell_range else args.get("offset"),
                sample_rows=None if cell_range else (args.get("sample_rows") or args.get("sampleRows")),
                expected_version=args.get("expected_version"),
            )
        if extras and result.success and isinstance(result.value, dict):
            warns = list(result.value.get("warnings") or [])
            note = f"精确 range 已忽略 {', '.join(extras)}"
            if note not in warns:
                result.value.setdefault("warnings", []).append(note)
            if result.model_text and note not in result.model_text:
                result.model_text = f"⚠️ {note}\n{result.model_text}"
        return result

    if chosen != "overview":
        return _invalid(
            f"不支持的 inspect.mode={chosen}。可用：overview / range / search / capabilities"
        )

    if target:
        from excelmanus.workbook.sheets import list_sheets

        include = args.get("include")
        if include is None:
            include = ["freeze_panes"]
        return list_sheets(
            file_path=target,
            include=include,
            max_preview_rows=int(args.get("max_rows") or args.get("maxRows") or 5),
        )

    return error_result(
        "overview 需要 file_path。查看工作区有哪些表时用 list_directory 或 analyze_spreadsheet(mode=files)。",
        code="PATH_REQUIRED",
        remediation=(
            "补上工作区相对路径后重试；查找目录用 list_directory 或 analyze_spreadsheet(mode=\"files\")."
        ),
    )


def analyze_spreadsheet(
    request: dict[str, Any] | None = None,
    mode: str | None = None,
    file_path: str = "",
    path: str = "",
    sheet_name: str | None = None,
    sheet: str | None = None,
    header_row: int | None = None,
    column: str | None = None,
    operator: str | None = None,
    value: Any = None,
    conditions: list[dict[str, Any]] | None = None,
    logic: str = "and",
    columns: list[str] | None = None,
    max_rows: int | None = None,
    sort_by: str | None = None,
    ascending: bool = True,
    limit: int | None = None,
    directory: str = ".",
    file_paths: list[str] | None = None,
    paths: list[str] | None = None,
    group_by: Any = None,
    aggregations: Any = None,
    dup_only: bool | None = None,
    join: Any = None,
    index: Any = None,
    values: Any = None,
    aggfunc: str | None = None,
    margins: Any = None,
    margins_name: str | None = None,
) -> ToolResult:
    """只读分析：profile / quality / filter / aggregate / distinct / pivot / relationships / files。"""
    args = _merge_request(
        request,
        mode=mode,
        file_path=file_path or path,
        sheet_name=sheet_name or sheet,
        header_row=header_row,
        column=column,
        operator=operator,
        value=value,
        conditions=conditions,
        logic=logic,
        columns=columns,
        max_rows=max_rows,
        sort_by=sort_by,
        ascending=ascending,
        limit=limit,
        directory=directory,
        file_paths=file_paths or paths,
        group_by=group_by,
        aggregations=aggregations,
        dup_only=dup_only,
        join=join,
        index=index,
        values=values,
        aggfunc=aggfunc,
        margins=margins,
        margins_name=margins_name,
    )
    chosen = str(args.get("mode") or "profile")
    target = str(args.get("file_path") or "")
    if chosen in _INSPECT_ONLY_ANALYZE_MODES:
        return _invalid(
            f"mode={chosen} 属于 inspect_spreadsheet，请改用 inspect_spreadsheet(mode=\"{chosen}\")。"
            "analyze_spreadsheet 可用：profile / quality / filter / aggregate / distinct / pivot / relationships / files"
        )
    mode_err = _reject_mode_fields(
        args, chosen, _ANALYZE_MODE_FIELDS.get(chosen, frozenset()), _ANALYZE_DEFAULTS,
        required_for_mode=_ANALYZE_REQUIRED_FOR_MODE.get(chosen) or [],
        missing_required=_analyze_missing_required(chosen, args),
    )
    if mode_err is not None:
        return mode_err

    if chosen in {"profile", "quality"}:
        from excelmanus.workbook.data import scan_excel_snapshot

        if not target:
            return _invalid(f"{chosen} 需要 file_path / path")
        return scan_excel_snapshot(
            file_path=target,
            max_sample_rows=int(args.get("max_rows") or args.get("maxRows") or 500),
            include_relationships=True,
            sheet_name=args.get("sheet_name"),
        )

    if chosen == "filter":
        from excelmanus.workbook.data import filter_data

        if not target:
            return _invalid("filter 需要 file_path / path")
        return filter_data(
            file_path=target,
            column=args.get("column"),
            operator=args.get("operator"),
            value=args.get("value"),
            sheet_name=args.get("sheet_name"),
            header_row=args.get("header_row") if args.get("header_row") is not None else args.get("headerRow"),
            columns=args.get("columns"),
            conditions=args.get("conditions"),
            logic=str(args.get("logic") or "and"),
            max_rows=args.get("max_rows") or args.get("maxRows"),
            sort_by=args.get("sort_by") or args.get("sortBy"),
            ascending=bool(args.get("ascending", True)),
            limit=args.get("max_rows") or args.get("maxRows") or args.get("limit"),
        )

    if chosen == "aggregate":
        from excelmanus.workbook.data import aggregate_data

        if not target:
            return _invalid("aggregate 需要 file_path / path")
        return aggregate_data(
            file_path=target,
            group_by=args.get("group_by") or args.get("groupBy"),
            aggregations=args.get("aggregations") or args.get("aggs"),
            sheet_name=args.get("sheet_name"),
            header_row=args.get("header_row") if args.get("header_row") is not None else args.get("headerRow"),
            column=args.get("column"),
            operator=args.get("operator"),
            value=args.get("value"),
            conditions=args.get("conditions"),
            logic=str(args.get("logic") or "and"),
            sort_by=args.get("sort_by") or args.get("sortBy"),
            ascending=bool(args.get("ascending", False)),
            limit=args.get("max_rows") or args.get("maxRows") or args.get("limit"),
            join=args.get("join") or args.get("lookup"),
        )

    if chosen == "pivot":
        from excelmanus.workbook.data import pivot_data

        if not target:
            return _invalid("pivot 需要 file_path / path")
        return pivot_data(
            file_path=target,
            index=args.get("index"),
            columns=args.get("columns"),
            values=args.get("values"),
            aggfunc=str(args.get("aggfunc") or "sum"),
            sheet_name=args.get("sheet_name"),
            header_row=args.get("header_row") if args.get("header_row") is not None else args.get("headerRow"),
            column=args.get("column"),
            operator=args.get("operator"),
            value=args.get("value"),
            conditions=args.get("conditions"),
            logic=str(args.get("logic") or "and"),
            join=args.get("join") or args.get("lookup"),
            group_by=args.get("group_by") or args.get("groupBy"),
            limit=args.get("max_rows") or args.get("maxRows") or args.get("limit"),
            margins=args.get("margins") or args.get("totals") or args.get("grand_total"),
            margins_name=args.get("margins_name") or args.get("totals_name") or "合计",
        )

    if chosen == "distinct":
        from excelmanus.workbook.data import distinct_data

        if not target:
            return _invalid("distinct 需要 file_path / path")
        return distinct_data(
            file_path=target,
            column=args.get("column"),
            sheet_name=args.get("sheet_name"),
            header_row=args.get("header_row") if args.get("header_row") is not None else args.get("headerRow"),
            conditions=args.get("conditions"),
            logic=str(args.get("logic") or "and"),
            limit=args.get("limit") or args.get("max_rows") or args.get("maxRows"),
            dup_only=bool(args.get("dup_only") or args.get("dupOnly") or False),
        )

    if chosen == "relationships":
        from excelmanus.workbook.data import _maybe_json, discover_file_relationships

        return discover_file_relationships(
            file_paths=_maybe_json(args.get("file_paths") or args.get("paths")),
            directory=str(args.get("directory") or "."),
            max_files=int(args.get("max_files") or args.get("maxFiles") or 5),
            sample_rows=int(args.get("sample_rows") or args.get("sampleRows") or 200),
        )

    if chosen == "files":
        from excelmanus.workbook.data import _maybe_json, inspect_excel_files

        return inspect_excel_files(
            directory=str(args.get("directory") or "."),
            max_files=int(args.get("max_files") or args.get("maxFiles") or 20),
            include=_maybe_json(args.get("include")),
            search=args.get("query") or args.get("search"),
            sheet_name=args.get("sheet_name"),
        )

    return _invalid(
        f"不支持的 analyze.mode={chosen}。"
        "可用：profile / quality / filter / aggregate / distinct / pivot / relationships / files"
    )


def compare_spreadsheets(
    request: dict[str, Any] | None = None,
    file_a: str = "",
    file_b: str = "",
    path: str = "",
    other_path: str = "",
    file_path: str = "",
    sheet_a: str = "",
    sheet_b: str = "",
    sheet: str = "",
    other_sheet: str = "",
    alignment: str = "position",
    key_columns: list[str] | None = None,
    max_diffs: int = 500,
    ignore_style: bool = True,
) -> ToolResult:
    """只读对比：alignment=position 按坐标，alignment=key 按关键列。"""
    args = _merge_request(
        request,
        file_a=file_a or path or file_path,
        file_b=file_b or other_path,
        path=path,
        other_path=other_path,
        sheet_a=sheet_a or sheet,
        sheet_b=sheet_b or other_sheet,
        alignment=alignment,
        key_columns=key_columns,
        max_diffs=max_diffs,
        ignore_style=ignore_style,
    )
    left = str(args.get("file_a") or args.get("path") or args.get("file_path") or "")
    right_explicit = str(args.get("file_b") or args.get("other_path") or args.get("otherPath") or "")
    right = right_explicit or left
    if not left:
        return _invalid("compare 需要 file_a / path")
    if not right_explicit:
        left_sheet = str(args.get("sheet_a") or args.get("sheet") or "")
        right_sheet = str(args.get("sheet_b") or args.get("other_sheet") or args.get("otherSheet") or "")
        if not left_sheet or not right_sheet or left_sheet == right_sheet:
            return _invalid("compare 需要 file_b，或在同一文件内提供不同的 sheet_a 与 sheet_b")
    align = str(args.get("alignment") or "position").strip().lower()
    keys = args.get("key_columns") or args.get("keyColumns")
    if not bool(args.get("ignore_style", True)):
        return _invalid(
            "当前不支持样式对比。请省略 ignore_style 或设为 true。",
            next_step="run_code",
        )
    if align == "key" and not keys:
        return _invalid("alignment=key 需要 key_columns")
    if align == "position" and keys:
        return _invalid("alignment=position 不能同时提供 key_columns；按键对齐请用 alignment=key")
    from excelmanus.workbook.data import compare_excel

    return compare_excel(
            file_a=left,
            file_b=right,
            sheet_a=str(args.get("sheet_a") or args.get("sheet") or ""),
            sheet_b=str(args.get("sheet_b") or args.get("other_sheet") or args.get("otherSheet") or ""),
            ignore_style=True,
            alignment=align,
            key_columns=list(keys) if keys else None,
            max_diffs=int(args.get("max_diffs") or args.get("maxDifferences") or 500),
        )


def manage_spreadsheet_objects(
    file_path: str = "",
    path: str = "",
    operations: list[dict[str, Any]] | None = None,
    expected_version: str | None = None,
    request: dict[str, Any] | None = None,
    content_version: str | None = None,
) -> ToolResult:
    """图表等富对象。当前实现：kind=chart 插入原生 Excel 图表。一批 operations 一次提交。"""
    args = _merge_request(
        request,
        file_path=file_path or path,
        operations=operations,
        expected_version=expected_version or content_version,
    )
    target = str(args.get("file_path") or "")
    ops = _coerce_operations(args.get("operations"))
    if isinstance(ops, ToolResult):
        return ops
    if not target or not ops:
        return _invalid("需要 file_path 与 operations")
    from excelmanus.workbook.charts import add_chart_to_workbook, normalize_chart_args

    prepared_ops: list[dict[str, Any]] = []
    for index, raw in enumerate(ops):
        if not isinstance(raw, dict):
            return _invalid(f"operations[{index}] 必须是对象")
        kind = str(_op_get(raw, "kind", "action") or "chart")
        if kind not in {"chart", "create_chart"}:
            return _invalid(
                f"不支持的 object.kind={kind}。当前仅支持 chart；"
                "合并单元格请用 format_spreadsheet",
            )
        chart_type = str(_op_get(raw, "chart_type", "chartType") or "")
        data_range = str(_op_get(raw, "data_range", "dataRange") or "")
        if not chart_type or not data_range:
            return _invalid("chart 需要 chart_type 与 data_range")
        prepared_ops.append(raw)

    applied: list[str] = []
    last_meta: dict[str, Any] = {}

    def mutate(wb: Any) -> None:
        for raw in prepared_ops:
            _require_explicit_sheet(
                raw,
                "chart",
                "data_range",
                "dataRange",
                "categories_range",
                "categoriesRange",
                "target_cell",
                "targetCell",
                wb=wb,
            )
            spec = normalize_chart_args(
                chart_type=str(_op_get(raw, "chart_type", "chartType") or ""),
                data_range=str(_op_get(raw, "data_range", "dataRange") or ""),
                categories_range=_op_get(raw, "categories_range", "categoriesRange"),
                sheet_name=_op_get(raw, "sheet", "sheet_name"),
                target_cell=str(_op_get(raw, "target_cell", "targetCell") or "A1"),
                target_sheet=_op_get(raw, "target_sheet", "targetSheet"),
                title=_op_get(raw, "title"),
                x_title=_op_get(raw, "x_title", "xTitle"),
                y_title=_op_get(raw, "y_title", "yTitle"),
                style=_op_get(raw, "style"),
                width=float(_op_get(raw, "width") or 15.0),
                height=float(_op_get(raw, "height") or 10.0),
                from_rows=bool(_op_get(raw, "from_rows", "fromRows") or False),
            )
            if isinstance(spec, ToolResult):
                raise MutationAborted(spec)
            meta = add_chart_to_workbook(wb, spec)
            last_meta.update(meta)
            applied.append(f"{meta.get('target_sheet')}!{meta.get('target_cell')}:{spec.chart_type}")

    committed = _commit(
        file_path=target,
        mutate_fn=mutate,
        expected_version=args.get("expected_version"),
    )
    if isinstance(committed, ToolResult):
        return committed
    rel, _safe, cr = committed
    return _success(
        {
            "file_path": cr.path or rel,
            "content_version": cr.content_version,
            "applied": applied,
            "chart_type": last_meta.get("chart_type"),
            "data_range": last_meta.get("data_range"),
            "target_sheet": last_meta.get("target_sheet"),
            "target_cell": last_meta.get("target_cell"),
            "chart_info": last_meta.get("chart_info") or {},
            "total_charts_on_sheet": last_meta.get("total_charts", 0),
        }
    )


def trace_spreadsheet_formulas(
    request: dict[str, Any] | None = None,
    mode: str | None = None,
    file_path: str = "",
    path: str = "",
    target: str = "",
    direction: str = "both",
    depth: int = 2,
    detail: str = "summary",
) -> ToolResult:
    """公式引用：map 全景、trace 单元格、impact 影响面。"""
    args = _merge_request(
        request,
        mode=mode,
        file_path=file_path or path,
        target=target,
        direction=direction,
        depth=depth,
        detail=detail,
    )
    chosen = str(args.get("mode") or "map")
    target_path = str(args.get("file_path") or "")
    if not target_path:
        return _invalid("需要 file_path / path")
    mode_err = _reject_mode_fields(
        args, chosen, _TRACE_MODE_FIELDS.get(chosen, frozenset()), _TRACE_DEFAULTS,
        required_for_mode=["target"] if chosen in {"trace", "impact"} else [],
    )
    if mode_err is not None:
        return mode_err
    from excelmanus.tools.reference_tools import (
        get_impact_analysis,
        get_reference_map,
        trace_references,
    )

    if chosen == "map":
        return get_reference_map(
            file_path=target_path,
            detail=str(args.get("detail") or "summary"),
        )
    cell = str(args.get("target") or "")
    try:
        parsed = parse_sheet_address(cell)
    except InvalidRefError as exc:
        return _invalid(str(exc), code="RANGE_INVALID")
    if chosen == "trace":
        if not cell:
            return _invalid("trace 需要 target，如 Sheet1!B2")
        return _with_resolved_sheet(
            trace_references(
                file_path=target_path,
                target=cell,
                direction=str(args.get("direction") or "both"),
                depth=int(args.get("depth") or 2),
            ),
            sheet=parsed.sheet,
        )
    if chosen == "impact":
        if not cell:
            return _invalid("impact 需要 target")
        return _with_resolved_sheet(
            get_impact_analysis(
                file_path=target_path,
                target=cell,
                scope=str(args.get("scope") or "all"),
            ),
            sheet=parsed.sheet,
        )
    return _invalid(f"不支持的 trace.mode={chosen}。可用：map / trace / impact")


_SPLIT_MAX_FILES_DEFAULT = 50


def _split_safe_filename(key: Any, *, max_len: int = 60) -> str:
    """把拆分键值转成安全文件名片段；空/NaN → '空白'。"""
    import re

    import pandas as pd

    if key is None or (not isinstance(key, str) and pd.isna(key)):
        text = ""
    else:
        text = str(key).strip()
    if not text:
        text = "空白"
    text = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", text).strip(" ._") or "空白"
    return text[:max_len]


def split_spreadsheet(
    file_path: str = "",
    path: str = "",
    by_column: str = "",
    column: str = "",
    sheet_name: str | None = None,
    sheet: str | None = None,
    output_dir: str = "outputs",
    filename_template: str = "{key}",
    header_row: int | None = None,
    max_files: int = 0,
) -> ToolResult:
    """按某列取值把一个表拆成每组一个 xlsx 文件（只新建，不覆盖已有文件）。

    数据按 by_column 唯一值分组；每组写成一个新工作簿（保留源 sheet 名与列头）。
    文件名由 filename_template 渲染：``{key}``=分组键、``{stem}``=源文件名去扩展名。
    """
    file_path = str(file_path or path or "").strip()
    sheet_name = sheet_name or sheet
    by_column = str(by_column or column or "").strip()
    if not file_path:
        return error_result("缺少 file_path（源文件路径）", code="INVALID_ARGS")
    if not by_column:
        return error_result(
            "缺少 by_column（按哪一列拆分，如 '省份'）",
            code="INVALID_ARGS",
            fields={
                "accepted_fields": [
                    "file_path", "by_column", "sheet_name", "output_dir",
                    "filename_template", "header_row", "max_files",
                ]
            },
        )
    from excelmanus.workbook.data import _load_df_for_tool

    ctx, err = _load_df_for_tool(
        file_path, sheet_name, header_row, column_hints=[by_column],
    )
    if err is not None:
        return err
    assert ctx is not None
    df = ctx["df"]
    sheet_name = ctx["sheet_name"]
    if by_column not in df.columns:
        return error_result(
            f"拆分列 '{by_column}' 不存在。可用列：{list(df.columns)}",
            code="INVALID_ARGS",
            fields={"by_column": by_column, "columns": [str(c) for c in df.columns]},
        )
    key_idx = list(df.columns).index(by_column)
    width = len(df.columns)

    # df 由 dtype=str 读入，只用于分组/列定位；写回值须从工作簿原生读取保类型。
    snap = ctx["snap"]
    if snap.is_csv():
        import csv

        with open(snap.backing_path, newline="", encoding="utf-8-sig") as fh:
            all_rows = [row for row in csv.reader(fh)]
        hdr = max(int(ctx["effective_header"] or 1) - 1, 0)
        body_rows = [
            (list(r[:width]) + [None] * max(0, width - len(r)))[:width]
            for r in all_rows[hdr + 1 :]
        ]
    else:
        wb_src = snap.open_workbook(data_only=True, read_only=True)
        try:
            ws_src = wb_src[sheet_name]
            hdr_row = max(int(ctx["effective_header"] or 1), 1)
            body_rows = []
            for r_i, row in enumerate(
                ws_src.iter_rows(values_only=True), start=1
            ):
                if r_i <= hdr_row:
                    continue
                cells = (list(row[:width]) + [None] * max(0, width - len(row)))[:width]
                body_rows.append(cells)
        finally:
            wb_src.close()
    # 丢弃整行空白行（样式残留不算数据行）
    body_rows = [r for r in body_rows if any(v is not None and str(v) != "" for v in r)]

    groups: dict[str, list[list[Any]]] = {}
    group_order: list[str] = []
    blank_rows = 0
    for row in body_rows:
        key_text = _split_safe_filename(row[key_idx]) if key_idx < len(row) else "空白"
        if key_text == "空白":
            blank_rows += 1
        if key_text not in groups:
            groups[key_text] = []
            group_order.append(key_text)
        groups[key_text].append(row)

    limit = int(max_files) if max_files else _SPLIT_MAX_FILES_DEFAULT
    if len(group_order) > limit:
        return error_result(
            f"按 '{by_column}' 拆分将产生 {len(group_order)} 个文件，超过上限 {limit}。请提高 max_files 或改用更粗粒度列。",
            code="INVALID_ARGS",
            fields={"groups": len(group_order), "max_files": limit},
        )

    guard = _get_guard()
    rel_source = ctx["rel_path"]
    source_stem = rel_source.replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0]

    # 1) 先渲染全部目标路径并预检冲突（all-or-nothing），再逐个原子提交。
    template = str(filename_template or "{key}")
    if "{key}" not in template and "{stem}" not in template:
        return error_result(
            "filename_template 必须包含 {key} 或 {stem} 占位符",
            code="INVALID_ARGS",
            fields={"filename_template": template},
        )
    used_names: set[str] = set()
    plan: list[tuple[str, str]] = []  # (key_text, rel_posix)
    for key_text in group_order:
        try:
            name = template.format(key=key_text, stem=source_stem)
        except (KeyError, IndexError, ValueError) as exc:
            return error_result(
                f"filename_template 占位符非法：{exc}。仅支持 {{key}} 与 {{stem}}",
                code="INVALID_ARGS",
                fields={"filename_template": template},
            )
        name = _split_safe_filename(name)
        if not name.lower().endswith(".xlsx"):
            name = f"{name}.xlsx"
        if name in used_names:
            suffix = 2
            while f"{name[:-5]}_{suffix}.xlsx" in used_names:
                suffix += 1
            name = f"{name[:-5]}_{suffix}.xlsx"
        used_names.add(name)
        out_rel = f"{str(output_dir or 'outputs').strip().rstrip('/')}/{name}"
        plan.append((key_text, out_rel))

    conflicts: list[str] = []
    resolved: list[tuple[str, str]] = []
    for key_text, out_rel in plan:
        try:
            dest = guard.resolve_and_validate(out_rel)
        except SecurityViolationError as exc:
            return error_result(f"输出路径越界：{exc}", code="PATH_INVALID", fields={"path": out_rel})
        rel_posix = str(dest.relative_to(guard.workspace_root)).replace("\\", "/")
        if rel_posix == "uploads" or rel_posix.startswith("uploads/"):
            return error_result(
                "output_dir 不能指向 uploads/（只读附件目录）",
                code="PATH_INVALID",
                fields={"path": rel_posix},
            )
        if dest.exists():
            conflicts.append(rel_posix)
        resolved.append((key_text, rel_posix))
    if conflicts:
        return error_result(
            f"{len(conflicts)} 个目标文件已存在，拆分已取消（不会覆盖）：{conflicts[:5]}",
            code="VERSION_CONFLICT",
            fields={"conflicts": conflicts},
        )

    # 2) 逐组写新工作簿并原子提交（原生值，保留 int/datetime 类型）。
    import io
    import re

    from openpyxl import Workbook

    headers = [str(c) for c in df.columns]
    safe_sheet = re.sub(r"[\[\]\\/*?:]+", "_", str(sheet_name or "Sheet1")).strip()[:31] or "Sheet1"
    files_out: list[dict[str, Any]] = []
    for key_text, rel_posix in resolved:
        wb = Workbook()
        ws = wb.active
        ws.title = safe_sheet
        ws.append(headers)
        for row in groups[key_text]:
            ws.append(list(row))
            # openpyxl 把 '=' 开头的文本自动判为公式；强制按字符串写回。
            for cell in ws[ws.max_row]:
                if cell.data_type == "f" and isinstance(cell.value, str):
                    cell.data_type = "s"
        buf = io.BytesIO()
        wb.save(buf)
        wb.close()
        cr = commit_bytes(guard=guard, file_path=rel_posix, data=buf.getvalue(), expected_version=None)
        files_out.append(
            {
                "file_path": rel_posix,
                "key": key_text,
                "rows": len(groups[key_text]),
                "content_version": cr.content_version,
            }
        )
    files_out.sort(key=lambda f: f["file_path"])
    return _success(
        {
            "status": "ok",
            "file_path": rel_source,
            "sheet_name": sheet_name,
            "by_column": by_column,
            "groups": len(group_order),
            "total_rows": len(body_rows),
            "blank_key_rows": blank_rows,
            "output_dir": str(output_dir or "outputs").strip().rstrip("/"),
            "files": files_out,
        }
    )


def get_tools() -> list[ToolDef]:
    tools = [
        ToolDef(
            name="inspect_spreadsheet",
            description=TOOL_DESCRIPTIONS["inspect_spreadsheet"],
            input_schema={
                "type": "object",
                "properties": {
                    "request": {"type": "object", "description": "可选；与平铺字段合并"},
                    "mode": {
                        "type": "string",
                        "enum": ["overview", "range", "search", "capabilities"],
                        "description": "默认 overview",
                    },
                    "file_path": {
                        "type": "string",
                        "description": "工作区相对路径。点名文件时直接填该相对路径；查找工作区用 list_directory 或 mode=files",
                    },
                    "path": {"type": "string", "description": "deprecated 别名，等同 file_path"},
                    "sheet_name": {
                        "type": "string",
                        "description": _sheet_schema_description(
                            alias_note="规范名是 sheet_name；sheet 是 deprecated 别名。",
                        ),
                    },
                    "sheet": {"type": "string", "description": "deprecated 别名，等同 sheet_name"},
                    "range": {
                        "type": "string",
                        "description": _range_schema_description(
                            extra=(
                                "规范名是 range。整行/整列按已用范围裁剪，超上限会截断并返回 resolved_range。"
                                "成功载荷同时给出 range/values/meta；data 为范围读取兼容字段，preview 为概览兼容字段。"
                            ),
                            alias_note="cell_range 是 deprecated 别名。",
                        ),
                    },
                    "cell_range": {"type": "string", "description": "deprecated 别名，等同 range"},
                    "include": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "overview 可用 columns/styles/charts/formulas/column_widths/merges；"
                            "range 仅 formulas"
                        ),
                    },
                    "header_row": {
                        "type": "integer",
                        "description": "列头所在行号（Excel 行号，1-based，第 1 行 = 1），默认自动检测；表单类文档传 -1",
                    },
                    "max_rows": {
                        "type": "integer",
                        "description": "表格窗口行数上限（规范名）。精确 range 时忽略本参数并警告，不硬拒。",
                    },
                    "query": {"type": "string", "description": "search 模式的查询串"},
                    "match_mode": {
                        "type": "string",
                        "enum": ["contains", "exact", "regex", "startswith"],
                    },
                    "directory": {
                        "type": "string",
                        "description": "files/列举时的相对目录；点名某文件时仍用 file_path",
                    },
                    "offset": {
                        "type": "integer",
                        "description": "表格窗口偏移，从 0 起（规范名）。精确 range 时忽略并警告。",
                    },
                    "sample_rows": {
                        "type": "integer",
                        "description": "表格采样行数（规范名）。精确 range 时忽略并警告。",
                    },
                    "max_results": {
                        "type": "integer",
                        "default": 50,
                        "description": "搜索/列表条数上限（规范名）。",
                    },
                    "expected_version": {
                        "type": "string",
                        "description": (
                            "可选：上次读取返回的 content_version。分页/窗口读取时携带，"
                            "文件中途被改则返回 STALE_SNAPSHOT 而不是混入新页数据"
                        ),
                    },
                    "content_version": {"type": "string", "description": "expected_version 的别名"},
                },
            },
            func=inspect_spreadsheet,
            write_effect="none",
            max_result_chars=0,
        ),
        ToolDef(
            name="analyze_spreadsheet",
            description=TOOL_DESCRIPTIONS["analyze_spreadsheet"],
            input_schema={
                "type": "object",
                "properties": {
                    "request": {"type": "object"},
                    "mode": {
                        "type": "string",
                        "enum": ["profile", "quality", "filter", "aggregate", "distinct", "pivot", "relationships", "files"],
                        "description": "profile/quality 数据框全貌；filter 筛行；aggregate 分组汇总；pivot 二维透视(index×columns×values)；distinct 单列取值分布；relationships 跨文件列关联；files 扫目录",
                    },
                    "file_path": {"type": "string"},
                    "path": {"type": "string"},
                    "sheet_name": {"type": "string", "description": "工作表名，可用 sheet 别名；多表时原则必填，省略时仅当只有一张可见表或列证据唯一命中可见数据表才自动绑定（见 warnings/resolved_sheet）；隐藏表列碰撞仍 SHEET_REQUIRED"},
                    "sheet": {"type": "string"},
                    "header_row": {
                        "type": "integer",
                        "description": "列头所在行号（Excel 行号，1-based，第 1 行 = 1），默认自动检测；表单类文档传 -1",
                    },
                    "column": {"type": "string", "description": "filter/distinct 的目标列；aggregate 单条件筛选用 column+operator+value"},
                    "group_by": {
                        "description": "aggregate 的分组列名或列名数组；也支持日期派生键对象 {column, transform}，transform=year|quarter|month|year_month|date|week|hour（按月份聚合传 {\"column\":\"日期\",\"transform\":\"year_month\"}）；不传则整体汇总；传列名或对象，不要传 JSON 字符串",
                    },
                    "aggregations": {
                        "type": "object",
                        "description": "aggregate 的聚合规格（对象，不要传字符串）{列名: 函数或函数数组}；函数 sum/count/mean/min/max/median/std/nunique/first/last；\"*\" 表示行计数；TopN 效果用 sort_by+limit 不是函数",
                    },
                    "dup_only": {
                        "type": "boolean",
                        "description": "distinct 只看重复取值（count>1）并附行号",
                    },
                    "operator": {
                        "type": "string",
                        "description": "eq/ne/gt/ge/lt/le/contains/not_contains/regex/not_regex/in/not_in/between/isnull/notnull/startswith/endswith；也接受 =、==、!=、not（→ne）",
                    },
                    "value": {},
                    "conditions": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": "条件对象数组 [{column,operator,value}]；传 [] 表示全表不过滤",
                    },
                    "logic": {
                        "type": "string",
                        "enum": ["and", "or", "not"],
                        "description": "条件组合：and（默认）/ or / not（对单个条件整体取反，仅接受恰好一个条件）",
                    },
                    "columns": {
                        "description": "pivot 的列维度：列名或列名数组，也支持日期派生键对象 {column, transform}（如按月份分列传 {\"column\":\"日期\",\"transform\":\"year_month\"}）；其它模式下为选中列名数组；传列名、数组或对象，不要传 JSON 字符串",
                    },
                    "max_rows": {
                        "type": "integer",
                        "description": "结果行数/组数/条目数上限（规范名）；filter/aggregate/distinct/pivot 通用。",
                    },
                    "sort_by": {
                        "type": "string",
                        "description": "结果排序列：分组键或聚合输出列名（如 金额_sum）；传源列名会自动映射到其唯一聚合输出列",
                    },
                    "ascending": {"type": "boolean", "default": True},
                    "limit": {"type": "integer", "description": "deprecated 别名，等同 max_rows"},
                    "join": {
                        "type": "object",
                        "description": "aggregate/pivot 跨表连接（VLOOKUP 语义，左连接右表按键去重）：{sheet: 右表名（同簿）或 file_path: 另一文件, on: 同名键 或 left_on+right_on: 异名键, columns: [带来的列]（缺省=右表全部非键列）}；连接列可参与 group_by/aggregations/conditions",
                    },
                    "index": {
                        "description": "pivot 行维度：列名或列名数组，也支持日期派生键对象 {column, transform}（同 group_by）；传列名、数组或对象，不要传 JSON 字符串",
                    },
                    "values": {
                        "description": "pivot 值列：列名或列名数组",
                    },
                    "aggfunc": {
                        "type": "string",
                        "description": "pivot 聚合函数，默认 sum",
                    },
                    "margins": {
                        "type": "boolean",
                        "description": "pivot 追加合计行与合计列（Excel 总计）；合计标签用 margins_name，默认「合计」。",
                    },
                    "margins_name": {"type": "string"},
                    "totals": {"type": "boolean"},
                    "totals_name": {"type": "string"},
                    "grand_total": {"type": "boolean"},
                    "directory": {"type": "string"},
                    "file_paths": {"type": "array", "items": {"type": "string"}},
                    "paths": {"type": "array", "items": {"type": "string"}},
                    "max_files": {"type": "integer"},
                    "query": {"type": "string"},
                    "include": {"type": "array", "items": {"type": "string"}},
                    "sample_rows": {
                        "type": "integer",
                        "description": "表格采样行数（规范名）。",
                    },
                },
            },
            func=analyze_spreadsheet,
            write_effect="none",
            max_result_chars=0,
        ),
        ToolDef(
            name="compare_spreadsheets",
            description=TOOL_DESCRIPTIONS["compare_spreadsheets"],
            input_schema={
                "type": "object",
                "properties": {
                    "request": {"type": "object"},
                    "file_a": {"type": "string"},
                    "file_b": {"type": "string", "description": "另一个文件；同一文件比两张表时可省略并提供不同 sheet_a/sheet_b"},
                    "file_path": {"type": "string", "description": "file_a 别名"},
                    "path": {"type": "string"},
                    "other_path": {"type": "string"},
                    "sheet_a": {"type": "string"},
                    "sheet_b": {"type": "string"},
                    "alignment": {
                        "type": "string",
                        "enum": ["position", "key"],
                        "description": (
                            "position 按单元格行列坐标；key 需 key_columns。二者不能同时用。"
                            "有 SKU/编号等业务主键时用 key，增删行不要用 position。"
                        ),
                    },
                    "key_columns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "alignment=key 时的业务主键，例如 [\"SKU\"]。",
                    },
                    "max_diffs": {"type": "integer", "default": 500},
                    "ignore_style": {
                        "type": "boolean",
                        "default": True,
                        "description": "必须为 true。false 会返回不支持样式对比。",
                    },
                },
            },
            func=compare_spreadsheets,
            write_effect="none",
            max_result_chars=0,
        ),
        ToolDef(
            name="edit_spreadsheet",
            description=TOOL_DESCRIPTIONS["edit_spreadsheet"],
            input_schema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "工作区相对路径，如 book.xlsx；可用 path 别名",
                    },
                    "path": {"type": "string"},
                    "operations": {
                        "description": (
                            "有序操作数组。"
                            "kind=write: values 二维矩形 + start_cell/selection；"
                            "kind=insert: axis=row|column + at(从1起)/count；"
                            "kind=delete_rows: at/count 或 selection/source_rows(须带 content_version)；"
                            "kind=delete_columns: at/count；"
                            "kind=sheet: action=create|copy|rename|delete；"
                            "kind=copy: 只复制值与公式文本；"
                            "kind=transform: action=dedupe|split|normalize_date|normalize_phone；"
                            "dedupe 可用 key_normalizers={手机号列:phone} + keep=earliest|latest + order_by=日期列，一次按归一键保留最早/最晚；"
                            "kind=pivot: index/columns/pivot_values/aggfunc 写入 target_sheet，margins=true 追加合计行/列（要汇总+合计直接 pivot 写，不要 analyze 拿矩阵再分批 values 回写）；"
                            "operations 传对象数组或等价的 JSON 字符串。"
                        ),
                        "type": ["array", "string"],
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "kind": {
                                    "type": "string",
                                    "enum": [
                                        "write",
                                        "insert",
                                        "sheet",
                                        "copy",
                                        "delete_rows",
                                        "delete_columns",
                                        "pivot",
                                        "transform",
                                    ],
                                },
                                "sheet": {"type": "string"},
                                "sheet_name": {"type": "string"},
                                "start_cell": {
                                    "type": "string",
                                    "description": (
                                        "写入起点：单元格/矩形取左上角；命名区域或表绑定后取左上角。"
                                        "并集与整轴 A:A / 1:1 会 REF_UNSUPPORTED。写入大小由 values 决定。"
                                    ),
                                },
                                "values": {
                                    "description": "按行排列的矩形二维数组，如 [[100,200],[300,null]]；不能是 records 对象数组。null 清空，字符串原样保存，公式以 = 开头。也可传矩阵的 JSON 字符串或 spill 句柄（spill:…），大结果免重序列化落盘。",
                                },
                                "selection": {
                                    "type": ["object", "string"],
                                    "description": "读/筛结果返回的 selection（对象或其 JSON 字符串）。write/delete_rows 消费其 rows 与 content_version。",
                                },
                                "source_rows": {
                                    "type": "array",
                                    "items": {"type": "integer"},
                                    "description": "与 selection.rows 等价的 Excel 1-based 行号；必须同时带该选择的 content_version。",
                                },
                                "content_version": {
                                    "type": "string",
                                    "description": "选择所属快照版本。带 source_rows/selection 时必填，不能用后一次 read 的 seen。",
                                },
                                "axis": {"type": "string"},
                                "at": {},
                                "count": {"type": "integer"},
                                "new_name": {
                                    "type": "string",
                                    "description": "sheet.create 的新表名；sheet / sheet_name 也可当新表名",
                                },
                                "source_sheet": {"type": "string"},
                                "source_range": {
                                    "type": "string",
                                    "description": "复制源：单格/矩形 A1:B2，可带表名前缀及 $；整行 1:1 或整列 A:A 按已用范围裁剪。不接受多区域、命名区域或表引用。",
                                },
                                "target_sheet": {"type": "string"},
                                "target_start": {
                                    "type": "string",
                                    "description": "复制目标起点，如 B2 或 'My Sheet'!B2；矩形取左上角。不接受多区域、命名区域或表引用。",
                                },
                                "index": {},
                                "columns": {},
                                "pivot_values": {},
                                "aggfunc": {"type": "string"},
                                "margins": {
                                    "type": "boolean",
                                    "description": "kind=pivot 追加合计行与合计列（Excel 总计）。",
                                },
                                "margins_name": {
                                    "type": "string",
                                    "description": "合计行/列的标签，默认「合计」。",
                                },
                                "totals": {"type": "boolean"},
                                "totals_name": {"type": "string"},
                                "grand_total": {"type": "boolean"},
                                "join": {"type": "object"},
                                "group_by": {},
                                "conditions": {"type": "array", "items": {"type": "object"}},
                                "logic": {"type": "string"},
                                "header_row": {"type": "integer"},
                                "action": {"type": "string"},
                                "key_columns": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "dedupe 比较键列。",
                                },
                                "key_normalizers": {
                                    "type": "object",
                                    "description": "dedupe 比较键归一器；当前支持 {手机号列: phone}。只影响比较，不改写原值。",
                                    "additionalProperties": {
                                        "type": "string",
                                        "enum": ["phone"],
                                    },
                                },
                                "keep": {
                                    "type": "string",
                                    "enum": ["first", "last", "earliest", "latest"],
                                    "description": "dedupe 保留规则；earliest/latest 必须同时给 order_by 日期列。",
                                },
                                "order_by": {
                                    "type": "string",
                                    "description": "dedupe keep=earliest/latest 的排序日期列；支持混合常见日期格式。",
                                },
                                "column": {"type": "string"},
                                "delimiter": {"type": "string"},
                                "into": {
                                    "description": "transform=split 的新列名数组（等价 new_columns）；dict 形如 {start_cell} 会被忽略。",
                                },
                                "new_columns": {
                                    "description": "transform=split 的新列名数组，如 [\"姓名\",\"工号\"]。",
                                },
                                # ── 实现侧已接受的兼容别名（_op_get 兜底键）──
                                "cell": {"type": "string", "description": "start_cell 的别名"},
                                "start": {"type": "string", "description": "start_cell 的别名"},
                                "startCell": {"type": "string", "description": "start_cell 的别名"},
                                "row": {"description": "insert/delete_rows 中 at 的别名"},
                                "transform": {"type": "string", "description": "action 的别名（kind=transform）"},
                                "sep": {"type": "string", "description": "delimiter 的别名"},
                                "pivot_columns": {"description": "kind=pivot 中 columns 的别名"},
                                "sourceRange": {"type": "string", "description": "source_range 的别名"},
                                "sourceSheet": {"type": "string", "description": "source_sheet 的别名"},
                                "targetStart": {"type": "string", "description": "target_start 的别名"},
                                "targetSheet": {"type": "string", "description": "target_sheet 的别名"},
                                "newName": {"type": "string", "description": "new_name 的别名"},
                                "operator": {"type": "string", "description": "pivot 条件比较符（配合 column/value/conditions）"},
                                "value": {"description": "pivot 条件比较值"},
                            },
                        },
                    },
                    "workbook_spec": {
                        "description": (
                            "创建用 WorkbookSpec，与 operations 互斥。"
                            "必填 sheets 与 uncertainties。嵌套字段用 introspect_capability 查询。"
                        ),
                    },
                    "create_workbook": {
                        "type": "boolean",
                        "default": False,
                        "description": "operations 路径下文件不存在时创建；workbook_spec 本身只用于新建",
                    },
                    "expected_version": {
                        "type": "string",
                        "description": (
                            "已有文件以本轮读到的 content_version 为回退；"
                            "restore 必须显式传入，不能靠回退。"
                        ),
                    },
                    "content_version": {"type": "string", "description": "expected_version 别名"},
                },
                "required": ["file_path"],
            },
            func=edit_spreadsheet,
            write_effect="workspace_write",
            max_result_chars=4000,
        ),
        ToolDef(
            name="format_spreadsheet",
            description=TOOL_DESCRIPTIONS["format_spreadsheet"],
            input_schema={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "工作区相对路径，可用 path 别名"},
                    "path": {"type": "string"},
                    "operations": {
                        "type": ["array", "string"],
                        "description": (
                            "每项带 kind；单表可省略 sheet，多表必须带 sheet（可用 sheet_name）或写成 表!A1。"
                            "数组或等价 JSON 字符串。"
                            "kind=format: range + 可选 font/fill/border/alignment/number_format；"
                            "kind=merge|unmerge: range；"
                            "kind=size: columns/rows 或 auto_fit=true；"
                            "kind=freeze: freeze_panes=A2 冻结首行，空字符串取消；"
                            "kind=conditional_format: range + rule 对象（type/operator/value/font/fill）；"
                            "kind=data_validation: range + rule 对象（type=list/whole/decimal/date/time/textLength/custom，"
                            "list 用 values 数组或 formula1 引用，数值类用 operator+value/value2）；"
                            "remove=true 删除与 range 相交的条件格式/验证规则。"
                            "字段级合同见 tool_detail。"
                        ),
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "kind": {
                                    "type": "string",
                                    "enum": ["format", "merge", "unmerge", "size", "freeze", "conditional_format", "data_validation"],
                                },
                                "sheet": {"type": "string"},
                                "sheet_name": {"type": "string"},
                                "range": {
                                    "type": "string",
                                    "description": _range_schema_description(
                                        extra="format 可写同表并集或整列 B:B（裁到已用范围）；merge/unmerge 只要一个矩形。",
                                    ),
                                },
                                "cell_range": {"type": "string", "description": "range 的别名"},
                                "font": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "properties": {
                                        "name": {"type": "string"},
                                        "size": {"type": "number"},
                                        "bold": {"type": "boolean"},
                                        "italic": {"type": "boolean"},
                                        "color": {"type": "string"},
                                        "underline": {"type": "string"},
                                        "strike": {"type": "boolean"},
                                        "strikethrough": {"type": "boolean"},
                                    },
                                },
                                "fill": {"type": "object"},
                                "border": {"type": "object"},
                                "alignment": {"type": "object"},
                                "number_format": {"type": "string"},
                                "numberFormat": {"type": "string", "description": "number_format 的别名"},
                                "numFmt": {"type": "string", "description": "number_format 的别名"},
                                "columns": {},
                                "column_widths": {"description": "columns 的别名（kind=size）"},
                                "rows": {},
                                "row_heights": {"description": "rows 的别名（kind=size）"},
                                "auto_fit": {"type": "boolean"},
                                "autoFit": {"type": "boolean", "description": "auto_fit 的别名"},
                                "axis": {"type": "string"},
                                "rule": {
                                    "type": ["object", "string"],
                                    "description": "条件格式或数据验证规则对象（或 JSON 字符串）；字段随 kind 而定",
                                },
                                "validation": {"description": "rule 的别名（kind=data_validation）"},
                                "data_validation_rule": {"description": "rule 的别名（kind=data_validation）"},
                                "cf_rule": {"description": "rule 的别名（kind=conditional_format）"},
                                "conditional_format_rule": {"description": "rule 的别名（kind=conditional_format）"},
                                "remove": {"type": "boolean"},
                                "delete": {"type": "boolean", "description": "remove 的别名"},
                                "clear": {"type": "boolean", "description": "remove 的别名"},
                                "freeze_panes": {
                                    "type": "string",
                                    "description": "冻结窗格单元格，A2=冻结首行；空字符串取消。",
                                },
                                "panes": {"type": "string", "description": "freeze_panes 的别名"},
                                "cols": {"type": "integer"},
                            },
                        },
                    },
                    "expected_version": {
                        "type": "string",
                        "description": (
                            "已有文件以本轮读到的 content_version 为回退；"
                            "restore 必须显式传入，不能靠回退。"
                        ),
                    },
                    "content_version": {"type": "string", "description": "expected_version 别名"},
                },
                "required": ["file_path", "operations"],
            },
            func=format_spreadsheet,
            write_effect="workspace_write",
            max_result_chars=4000,
        ),
        ToolDef(
            name="split_spreadsheet",
            description=TOOL_DESCRIPTIONS["split_spreadsheet"],
            input_schema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "源文件工作区相对路径；path 是 deprecated 别名",
                    },
                    "path": {"type": "string", "description": "deprecated 别名，等同 file_path"},
                    "by_column": {
                        "type": "string",
                        "description": "按哪一列的取值拆分（如 '省份'）；column 是 deprecated 别名",
                    },
                    "column": {"type": "string", "description": "deprecated 别名，等同 by_column"},
                    "sheet_name": {
                        "type": "string",
                        "description": _sheet_schema_description(
                            alias_note="规范名是 sheet_name；sheet 是 deprecated 别名。省略时自动绑定唯一可见数据表。",
                        ),
                    },
                    "sheet": {"type": "string", "description": "deprecated 别名，等同 sheet_name"},
                    "output_dir": {
                        "type": "string",
                        "default": "outputs",
                        "description": "输出目录（工作区相对路径）。不能是 uploads/。",
                    },
                    "filename_template": {
                        "type": "string",
                        "default": "{key}",
                        "description": "文件名模板：{key}=分组键、{stem}=源文件名去扩展名。自动补 .xlsx。",
                    },
                    "header_row": {
                        "type": "integer",
                        "description": "列头所在行号（Excel 行号，1-based），默认自动检测",
                    },
                    "max_files": {
                        "type": "integer",
                        "description": "拆分文件数上限（默认 50）。超过即拒绝，不写任何文件。",
                    },
                },
                "required": ["file_path", "by_column"],
            },
            func=split_spreadsheet,
            write_effect="workspace_write",
            max_result_chars=6000,
        ),
        ToolDef(
            name="manage_spreadsheet_objects",
            description=TOOL_DESCRIPTIONS["manage_spreadsheet_objects"],
            input_schema={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "path": {"type": "string"},
                    "operations": {
                        "type": ["array", "string"],
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "kind": {
                                    "type": "string",
                                    "enum": ["chart"],
                                    "description": "当前仅 chart",
                                },
                                "sheet": {"type": "string"},
                                "sheet_name": {"type": "string"},
                                "chart_type": {
                                    "type": "string",
                                    "description": "bar/line/pie/scatter/area；column 视为 bar",
                                },
                                "data_range": {
                                    "type": "string",
                                    "description": _range_schema_description(
                                        extra="规范名是 data_range。图表需要有界矩形，不要写整列/整行。",
                                    ),
                                },
                                "categories_range": {
                                    "type": "string",
                                    "description": _range_schema_description(
                                        extra="规范名是 categories_range。",
                                    ),
                                },
                                "target_cell": {
                                    "type": "string",
                                    "description": _range_schema_description(
                                        extra="规范名是 target_cell。",
                                    ),
                                },
                                "target_sheet": {"type": "string"},
                                "title": {"type": "string"},
                                "x_title": {"type": "string"},
                                "y_title": {"type": "string"},
                                "style": {"type": "integer"},
                                "width": {"type": "number"},
                                "height": {"type": "number"},
                                "from_rows": {"type": "boolean"},
                            },
                        },
                        "description": (
                            "kind=chart 要 chart_type 与 data_range（含表头行），"
                            "可选 sheet/categories_range/target_cell/target_sheet/title/"
                            "x_title/y_title/width/height/from_rows。"
                            "一批 operations 一次提交。"
                        ),
                    },
                    "expected_version": {
                        "type": "string",
                        "description": (
                            "已有文件以本轮读到的 content_version 为回退；"
                            "restore 必须显式传入，不能靠回退。"
                        ),
                    },
                    "content_version": {"type": "string"},
                },
                "required": ["file_path", "operations"],
            },
            func=manage_spreadsheet_objects,
            write_effect="workspace_write",
            max_result_chars=4000,
        ),
        ToolDef(
            name="trace_spreadsheet_formulas",
            description=TOOL_DESCRIPTIONS["trace_spreadsheet_formulas"],
            input_schema={
                "type": "object",
                "properties": {
                    "request": {"type": "object"},
                    "mode": {"type": "string", "enum": ["map", "trace", "impact"]},
                    "file_path": {"type": "string"},
                    "path": {"type": "string"},
                    "target": {
                        "type": "string",
                        "description": _brief_range_description(
                            extra="规范名是 target，如 产品表!B2。",
                        ),
                    },
                    "direction": {
                        "type": "string",
                        "enum": ["precedents", "dependents", "both"],
                    },
                    "depth": {"type": "integer", "default": 2},
                    "detail": {"type": "string", "enum": ["summary", "full"]},
                },
            },
            func=trace_spreadsheet_formulas,
            write_effect="none",
            max_result_chars=8000,
        ),
        ToolDef(
            name="manage_spreadsheet_versions",
            description=TOOL_DESCRIPTIONS["manage_spreadsheet_versions"],
            input_schema={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "action": {
                        "type": "string",
                        "enum": ["list", "checkpoint", "restore"],
                        "description": (
                            "list 只读，read/plan 目录可见；"
                            "checkpoint/restore 写入工作区，执行层按 action 拦截。"
                        ),
                    },
                    "revision_id": {"type": "string"},
                    "expected_version": {
                        "type": "string",
                        "description": (
                            "已有文件以本轮读到的 content_version 为回退；"
                            "restore 必须显式传入，不能靠回退。"
                        ),
                    },
                    "label": {"type": "string"},
                    "limit": {"type": "integer", "default": 50, "minimum": 1},
                },
                "required": ["file_path", "action"],
            },
            func=manage_spreadsheet_versions,
            write_effect="workspace_write",
            actions={"list": {"write_effect": "none"}},
            max_result_chars=4000,
        ),
    ]
    return _enrich_intent_schemas(tools)


def _enrich_intent_schemas(tools: list[ToolDef]) -> list[ToolDef]:
    spec = _workbook_spec_param_schema()
    defs = spec.pop("$defs", {}) or {}
    for tool in tools:
        schema = tool.input_schema if isinstance(getattr(tool, "input_schema", None), dict) else None
        if not isinstance(schema, dict):
            continue
        props = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        if tool.name == "edit_spreadsheet":
            spec_param = dict(spec)
            spec_param["type"] = ["object", "string"]
            spec_param["description"] = (
                "创建用 WorkbookSpec，与 operations 互斥。必填 sheets 与 uncertainties；"
                "可传对象或等价 JSON 字符串。嵌套字段用 introspect_capability 查询。"
            )
            props["workbook_spec"] = spec_param
            schema["$defs"] = {**(schema.get("$defs") or {}), **defs}
            props["expected_version"] = _version_param_schema()
            items = (props.get("operations") or {}).get("items") if isinstance(props.get("operations"), dict) else {}
            op_props = items.get("properties") if isinstance(items, dict) else None
            if isinstance(op_props, dict):
                op_props["join"] = _join_param_schema()
                op_props["values"] = {
                    "type": ["array", "string"],
                    "description": (
                        "kind=write 的二维矩形，如 [[100,200],[300,null]]（不能是 records）；"
                        "kind=pivot 为值列名数组（也可用 pivot_values）。"
                        "null 清空；也接受矩阵的 JSON 字符串或 spill: 句柄（大结果免重序列化落盘）。"
                    ),
                    "items": {},
                }
                op_props["header_row"] = {
                    "type": "integer",
                    "description": "Excel 1-based 行号；表单类文档传 -1",
                }
                if isinstance(op_props.get("conditions"), dict):
                    op_props["conditions"]["type"] = ["array", "string"]
        elif tool.name == "analyze_spreadsheet":
            props["join"] = _join_param_schema()
            if isinstance(props.get("conditions"), dict):
                props["conditions"]["type"] = ["array", "string"]
            if isinstance(props.get("aggregations"), dict):
                props["aggregations"]["type"] = ["object", "array", "string"]
                props["aggregations"]["description"] = (
                    "aggregate 的聚合规格：{列名: 函数或函数数组} 或 [{column, func}] 数组，"
                    "也接受 JSON 字符串；函数 sum/count/mean/min/max/median/std/nunique/first/last；"
                    "\"*\" 表示行计数；TopN 效果用 sort_by+limit 不是函数"
                )
            for _arr in ("file_paths", "paths"):
                if isinstance(props.get(_arr), dict):
                    props[_arr]["type"] = ["array", "string"]
        elif tool.name == "inspect_spreadsheet":
            if isinstance(props.get("include"), dict):
                props["include"]["type"] = ["array", "string"]
        elif tool.name == "format_spreadsheet":
            items = (props.get("operations") or {}).get("items") if isinstance(props.get("operations"), dict) else {}
            op_props = items.get("properties") if isinstance(items, dict) else None
            if isinstance(op_props, dict):
                op_props["rule"] = _format_rule_schema()
            props["expected_version"] = _version_param_schema()
        elif "expected_version" in props:
            props["expected_version"] = _version_param_schema()
    return tools
