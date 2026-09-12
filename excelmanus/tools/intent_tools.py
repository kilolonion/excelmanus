"""模型面表格意图工具：八类职责，复用本仓库提交路径与 openpyxl 实现。

模型与 Code Mode SDK 只看到这八个名字。旧微工具（read_excel / filter_data /
create_excel_chart 等）只作为本模块的内部实现，不再注册、也不作为回退入口。
Host 仍拥有锁、版本校验和宏字节保留。
"""

from __future__ import annotations

import json
from typing import Any, NoReturn

from openpyxl.utils import column_index_from_string, get_column_letter
from openpyxl.utils.cell import coordinate_to_tuple, range_boundaries

from excelmanus.engine_core.tool_result import ToolError, ToolResult, ToolUiMeta, from_payload
from excelmanus.logger import get_logger
from excelmanus.prompt.canonical import TOOL_DESCRIPTIONS
from excelmanus.security import FileAccessGuard, SecurityViolationError
from excelmanus.tools._guard_ctx import get_guard as _get_ctx_guard
from excelmanus.tools._helpers import (
    MutationAborted,
    commit_error_result,
    commit_workbook_tool,
    get_worksheet,
    prepare_excel_commit_path,
    unwrap_mutation_abort,
    workspace_relpath,
)
from excelmanus.workbook.address import (
    column_map_to_list,
    combine_sheet_names,
    looks_like_coordinate_error,
    parse_sheet_address,
    top_left_cell,
)
from excelmanus.workbook.cells import assign_cell_value, _resolve_merged_cell
from excelmanus.workbook.styles import (
    _build_border,
    _build_fill,
    _patch_alignment,
    _patch_font,
    apply_column_sizes,
    apply_row_sizes,
)
from excelmanus.tools.registry import ToolDef
from excelmanus.workbook_commit import (
    CommitError,
    commit_bytes,
    content_version_of_file,
    peek_seen_content_version,
)

logger = get_logger("tools.intent")

_guard: FileAccessGuard | None = None


def _get_guard() -> FileAccessGuard:
    ctx = _get_ctx_guard()
    if ctx is not None:
        return ctx
    global _guard
    if _guard is None:
        _guard = FileAccessGuard(".")
    return _guard


def init_guard(workspace_root: str) -> None:
    global _guard
    _guard = FileAccessGuard(workspace_root)


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
    uncertainties = payload.get("uncertainties")
    if isinstance(uncertainties, list):
        parts.append(f"uncertainties={len(uncertainties)}")
    if parts:
        return " ".join(parts)
    keys = [str(key) for key in payload if key != "status"]
    return "ok " + " ".join(keys[:8]) if keys else "ok"


def _invalid(message: str, *, code: str = "INVALID_ARGS", **extra: Any) -> ToolResult:
    payload = {"status": "error", "code": code, "message": message, **extra}
    return ToolResult(
        success=False,
        model_text=message,
        value=payload,
        error=ToolError(code=code, message=message, fields=payload),
    )


_INSPECT_MODE_FIELDS: dict[str, frozenset[str]] = {
    "overview": frozenset({
        "request", "mode", "file_path", "path", "sheet", "sheet_name",
        "include", "max_rows", "directory", "query", "header_row",
    }),
    "range": frozenset({
        "request", "mode", "file_path", "path", "sheet", "sheet_name",
        "range", "cell_range", "include", "header_row",
        "max_rows", "offset", "sample_rows",
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
    "relationships": frozenset({
        "request", "mode", "file_path", "path", "file_paths", "paths",
        "directory", "max_files", "sample_rows",
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
    if not extras:
        return None
    return _invalid(
        f"mode={mode} 不接受字段：{', '.join(extras)}",
        ignored_fields=extras,
        accepted_fields=sorted(allowed),
    )


def _address_has_sheet(raw: Any) -> bool:
    if raw in (None, ""):
        return False
    return bool(parse_sheet_address(str(raw)).sheet)


def _require_explicit_sheet(
    op: dict[str, Any],
    action: str,
    *address_keys: str,
    sheet_keys: tuple[str, ...] = ("sheet", "sheet_name"),
) -> None:
    if _op_get(op, *sheet_keys):
        return
    for key in address_keys:
        if _address_has_sheet(_op_get(op, key)):
            return
    raise MutationAborted(
        _invalid(f"{action} 必须提供 sheet，或在地址里写 表!A1")
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
        raise MutationAborted(_invalid(str(exc))) from exc


def _split_op_address(op: dict[str, Any], raw: str) -> tuple[str | None, str]:
    parsed = parse_sheet_address(raw)
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
    extra = f"：{exc}" if exc is not None else ""
    raise MutationAborted(
        _invalid(
            f"{field}={raw!r} 不是合法坐标{extra}。"
            "请用 A1 或 A1:C5；工作表名放在 sheet，或写成 区域汇总!A5:C5。"
        )
    )


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


def _commit(
    *,
    file_path: str,
    mutate_fn: Any,
    create: bool = False,
    expected_version: str | None = None,
) -> ToolResult | tuple[str, Any, Any]:
    guard = _get_guard()
    try:
        safe_path, rel = prepare_excel_commit_path(guard, file_path)
    except SecurityViolationError as exc:
        return commit_error_result(CommitError("PATH_INVALID", str(exc)))
    except CommitError as exc:
        return commit_error_result(exc)

    try:
        cr = commit_workbook_tool(
            guard=guard,
            file_path=rel,
            mutate_fn=mutate_fn,
            expected_version=expected_version,
            create=create,
        )
    except CommitError as exc:
        aborted = unwrap_mutation_abort(exc)
        if aborted is not None:
            return aborted.result
        if looks_like_coordinate_error(exc):
            return _invalid(
                f"{getattr(exc, 'message', exc)}。"
                "请用 A1 或 A1:C5；工作表名放在 sheet，或写成 区域汇总!A5:C5。"
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


def _refuse_unmaintained_structure(ws: Any, action: str) -> None:
    formulas, charts = _count_sheet_formulas_and_charts(ws)
    if formulas == 0 and charts == 0:
        return
    raise MutationAborted(
        _invalid(
            f"{action} 不会维护公式、图表或表对象引用（openpyxl 限制，不等价于 Excel UI）。"
            f"当前表有 {formulas} 个公式、{charts} 张图表。"
            "请先改公式/图表，或在无公式的表上操作。"
        )
    )


def _refuse_rename_if_workbook_has_refs(wb: Any) -> None:
    formulas = 0
    charts = 0
    for ws in wb.worksheets:
        f_count, c_count = _count_sheet_formulas_and_charts(ws)
        formulas += f_count
        charts += c_count
    if formulas == 0 and charts == 0:
        return
    raise MutationAborted(
        _invalid(
            "sheet.rename 不会改写其它单元格里的表名引用。"
            f"工作簿现有 {formulas} 个公式、{charts} 张图表。"
            "请先确认没有跨表引用，或在无公式的簿上重命名。"
        )
    )


def _apply_write(wb: Any, op: dict[str, Any]) -> str:
    _require_explicit_sheet(op, "write", "start_cell", "startCell", "cell", "start")
    raw_start = str(_op_get(op, "start_cell", "startCell", "cell", "start") or "")
    sheet, start = _split_op_address(op, raw_start)
    values = _op_get(op, "values")
    if not start or not isinstance(values, list) or not values:
        raise MutationAborted(_invalid("write 需要 start_cell 与非空矩形 values"))
    try:
        start = top_left_cell(start).upper()
        row0, col0 = coordinate_to_tuple(start)
    except Exception as exc:
        _abort_bad_address("start_cell", raw_start, exc)
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
    return f"{start}:{get_column_letter(col0 + width - 1)}{row0 + len(values) - 1}"


def _apply_insert(wb: Any, op: dict[str, Any]) -> str:
    _require_explicit_sheet(op, "insert")
    sheet = _op_get(op, "sheet", "sheet_name")
    axis = str(_op_get(op, "axis") or "")
    at_raw = _op_get(op, "at", "row", "column")
    if isinstance(at_raw, str) and at_raw.strip().isalpha():
        try:
            at = column_index_from_string(at_raw.strip().upper())
        except ValueError as exc:
            raise MutationAborted(_invalid(f"insert 列字母无效：{at_raw}")) from exc
    else:
        try:
            at = int(at_raw or 0)
        except (TypeError, ValueError) as exc:
            raise MutationAborted(_invalid("insert 的 at 必须是列字母或正整数")) from exc
    count_raw = _op_get(op, "count")
    try:
        count = 1 if count_raw is None else int(count_raw)
    except (TypeError, ValueError) as exc:
        raise MutationAborted(_invalid("insert 的 count 必须是正整数")) from exc
    if at < 1 or count < 1:
        raise MutationAborted(_invalid("insert 的 at/count 必须 >= 1"))
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


def _apply_sheet(wb: Any, op: dict[str, Any]) -> str:
    action = str(_op_get(op, "action") or "")
    name = _op_get(op, "sheet", "sheet_name")
    new_name = _op_get(op, "new_name", "newName")
    if action == "create":
        if not new_name:
            raise MutationAborted(_invalid("sheet.create 需要 new_name"))
        if new_name in wb.sheetnames:
            raise MutationAborted(_invalid(f"工作表已存在：{new_name}"))
        wb.create_sheet(title=str(new_name))
        return f"create:{new_name}"
    if action == "rename":
        if not name or not new_name:
            raise MutationAborted(_invalid("sheet.rename 需要 sheet 与 new_name"))
        if name not in wb.sheetnames:
            raise MutationAborted(_invalid(f"工作表不存在：{name}"))
        if new_name in wb.sheetnames:
            raise MutationAborted(_invalid(f"工作表已存在：{new_name}"))
        _refuse_rename_if_workbook_has_refs(wb)
        wb[name].title = str(new_name)
        return f"rename:{name}->{new_name}"
    if action == "delete":
        if not name:
            raise MutationAborted(_invalid("sheet.delete 需要 sheet"))
        if name not in wb.sheetnames:
            raise MutationAborted(_invalid(f"工作表不存在：{name}"))
        if len(wb.sheetnames) <= 1:
            raise MutationAborted(_invalid("不能删除唯一的工作表"))
        del wb[name]
        return f"delete:{name}"
    if action == "copy":
        if not name or not new_name:
            raise MutationAborted(_invalid("sheet.copy 需要 sheet 与 new_name"))
        if name not in wb.sheetnames:
            raise MutationAborted(_invalid(f"工作表不存在：{name}"))
        if new_name in wb.sheetnames:
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
        min_col, min_row, max_col, max_row = range_boundaries(src_range.upper())
        start_row, start_col = coordinate_to_tuple(dst_start.upper())
    except Exception as exc:
        _abort_bad_address("source_range/target_start", f"{raw_src_range}->{raw_dst_start}", exc)
    if None in (min_col, min_row, max_col, max_row):
        raise MutationAborted(
            _invalid(
                f"copy 不支持整列/整行地址 {src_range!r}。"
                "请写 A1:A100 这类有行列边界的范围。"
            )
        )
    src = _worksheet(wb, src_sheet)
    dst = _worksheet(wb, dst_sheet)
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
    return f"{src_sheet}!{src_range}->{dst_sheet}!{dst_start}"


def _merged_anchor_cell(ws: Any, cell: Any) -> Any | None:
    from openpyxl.cell.cell import MergedCell

    if not isinstance(cell, MergedCell):
        return cell
    coord = getattr(cell, "coordinate", "")
    for merged in ws.merged_cells.ranges:
        if coord in merged:
            return ws.cell(row=merged.min_row, column=merged.min_col)
    return None


def _apply_format(wb: Any, op: dict[str, Any]) -> tuple[str, list[str]]:
    kind = str(_op_get(op, "kind") or "format")
    _require_explicit_sheet(op, "format", "range", "cell_range")
    raw_range = str(_op_get(op, "range", "cell_range") or "")
    sheet, cell_range = _split_op_address(op, raw_range)
    ws = _worksheet(wb, sheet)
    if kind not in {"format", "merge", "unmerge", "size"}:
        raise MutationAborted(
            _invalid(f"不支持的 format.kind={kind}。可用：format / merge / unmerge / size")
        )
    if kind == "merge":
        if not cell_range:
            raise MutationAborted(_invalid("merge 需要 range"))
        try:
            ws.merge_cells(cell_range)
        except Exception as exc:
            _abort_bad_address("range", raw_range, exc)
        return f"merge:{cell_range}", []
    if kind == "unmerge":
        if not cell_range:
            raise MutationAborted(_invalid("unmerge 需要 range"))
        try:
            ws.unmerge_cells(cell_range)
        except Exception as exc:
            _abort_bad_address("range", raw_range, exc)
        return f"unmerge:{cell_range}", []
    if kind == "size":
        columns = _op_get(op, "columns", "column_widths") or {}
        rows = _op_get(op, "rows", "row_heights") or {}
        auto_fit = bool(_op_get(op, "auto_fit", "autoFit"))
        axis = str(_op_get(op, "axis") or "").lower()
        if auto_fit:
            if axis in {"", "column", "columns", "col", "cols"}:
                apply_column_sizes(ws, auto_fit=True)
            if axis in {"", "row", "rows"}:
                apply_row_sizes(ws, auto_fit=True)
            if axis and axis not in {"column", "columns", "col", "cols", "row", "rows"}:
                raise MutationAborted(_invalid("size.axis 必须是 row 或 column"))
            return "size:auto_fit", []
        if isinstance(columns, list):
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
    if not cell_range:
        raise MutationAborted(_invalid("format 需要 range"))
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
    return f"format:{cell_range}", skipped


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
        target = file_path
        try:
            dest = guard.resolve_and_validate(target)
        except SecurityViolationError as exc:
            return _invalid(str(exc), code="PATH_INVALID")
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
        return _success(
            {
                "file_path": cr.path or rel,
                "content_version": cr.content_version,
                "uncertainties": [item.model_dump() for item in spec.uncertainties],
                "build_summary": summary,
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

    applied: list[str] = []

    def mutate(wb: Any) -> None:
        for index, raw in enumerate(operations):
            if not isinstance(raw, dict):
                raise MutationAborted(_invalid(f"operations[{index}] 必须是对象"))
            kind = str(_op_get(raw, "kind") or "")
            if kind == "write":
                applied.append(_apply_write(wb, raw))
            elif kind == "insert":
                applied.append(_apply_insert(wb, raw))
            elif kind == "sheet":
                applied.append(_apply_sheet(wb, raw))
            elif kind == "copy":
                applied.append(_apply_copy(wb, raw))
            else:
                raise MutationAborted(
                    _invalid(
                        f"不支持的 edit.kind={kind}。"
                        "值/表结构用 write|insert|sheet|copy；外观用 format_spreadsheet"
                    )
                )

    committed = _commit(
        file_path=file_path,
        mutate_fn=mutate,
        create=create_workbook,
        expected_version=expected_version,
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
    """一次原子请求：字体/填充/边框/对齐、合并、行列尺寸。"""
    file_path = file_path or path
    expected_version = expected_version or content_version
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

    def mutate(wb: Any) -> None:
        for index, raw in enumerate(operations):
            if not isinstance(raw, dict):
                raise MutationAborted(_invalid(f"operations[{index}] 必须是对象"))
            label, skipped = _apply_format(wb, raw)
            applied.append(label)
            skipped_merged_non_anchors.extend(skipped)

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
    except SecurityViolationError as exc:
        return commit_error_result(CommitError("PATH_INVALID", str(exc)))
    rel = workspace_relpath(guard, dest).replace("\\", "/")
    current = content_version_of_file(dest) if dest.is_file() else None
    seen = peek_seen_content_version(rel)
    store = RevisionStore(guard.workspace_root)

    if action == "list":
        records = [rec.to_public_dict() for rec in store.list(rel)]
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
        if not (expected_version or "").strip():
            return _invalid("restore 必须提供 expected_version", code="VERSION_CONFLICT")
        if not dest.is_file() or not current:
            return _invalid("文件不存在，无法恢复", code="PATH_INVALID")

        current_bytes = dest.read_bytes()
        tx: str | None = None
        before_rec = None
        restore_blob: bytes | None = None

        if store.get(rel, revision_id) is not None:
            try:
                tx, before_rec, restore_blob = store.restore_prepare(
                    rel, revision_id, current_bytes
                )
            except RevisionIntegrityError:
                return _invalid("检查点快照损坏", code="NOT_FOUND")
            except (KeyError, FileNotFoundError):
                return _invalid("检查点快照缺失", code="NOT_FOUND")
        else:
            return _invalid(f"找不到 revision_id={revision_id}", code="NOT_FOUND")

        if restore_blob is None:
            return _invalid("检查点快照缺失", code="NOT_FOUND")

        try:
            cr = commit_bytes(
                guard=guard,
                file_path=rel,
                data=restore_blob,
                expected_version=expected_version,
                record_history=False,
            )
        except CommitError as exc:
            if tx:
                store.discard_transaction(tx)
            return commit_error_result(exc)

        if tx:
            try:
                store.finish_restore(
                    rel,
                    transaction_id=tx,
                    after_bytes=restore_blob,
                    parent_revision_id=before_rec.id if before_rec is not None else None,
                )
            except Exception:
                logger.warning("restore afterEdit 记录失败，文件已写回 %s", rel, exc_info=True)
        return _success(
            {
                "file_path": cr.path,
                "content_version": cr.content_version,
                "restored_revision": revision_id,
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
        "manage_spreadsheet_objects",
        "trace_spreadsheet_formulas",
        "manage_spreadsheet_versions",
    ],
    "inspect_modes": ["overview", "range", "search", "capabilities"],
    "analyze_modes": ["profile", "quality", "filter", "relationships", "files"],
    "compare_alignments": ["position", "key"],
    "trace_modes": ["map", "trace", "impact"],
    "notes": [
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
    )
    chosen = str(args.get("mode") or "").strip()
    target = str(args.get("file_path") or "")
    raw_range = args.get("range") or args.get("cell_range")
    if raw_range:
        parsed = parse_sheet_address(str(raw_range))
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
        if extras:
            return _invalid(
                "精确 range 不能同时使用 max_rows/offset/sample_rows",
                ignored_fields=extras,
            )
        return read_excel(
                file_path=target,
                sheet_name=args.get("sheet_name"),
                header_row=args.get("header_row") if args.get("header_row") is not None else args.get("headerRow"),
                include=include,
                range=cell_range,
                max_rows=None if cell_range else (args.get("max_rows") or args.get("maxRows")),
                offset=None if cell_range else args.get("offset"),
                sample_rows=None if cell_range else (args.get("sample_rows") or args.get("sampleRows")),
            )

    if chosen != "overview":
        return _invalid(
            f"不支持的 inspect.mode={chosen}。可用：overview / range / search / capabilities"
        )

    if target:
        from excelmanus.workbook.sheets import list_sheets

        return list_sheets(
            file_path=target,
            include=args.get("include"),
            max_preview_rows=int(args.get("max_rows") or args.get("maxRows") or 5),
        )

    from excelmanus.workbook.data import inspect_excel_files

    return inspect_excel_files(
        directory=str(args.get("directory") or "."),
        include=args.get("include"),
        search=args.get("query") or None,
        sheet_name=args.get("sheet_name"),
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
) -> ToolResult:
    """只读分析：profile / quality / filter / relationships / files。"""
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
    )
    chosen = str(args.get("mode") or "profile")
    target = str(args.get("file_path") or "")
    mode_err = _reject_mode_fields(
        args, chosen, _ANALYZE_MODE_FIELDS.get(chosen, frozenset()), _ANALYZE_DEFAULTS,
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
            limit=args.get("limit"),
        )

    if chosen == "relationships":
        from excelmanus.workbook.data import discover_file_relationships

        return discover_file_relationships(
            file_paths=args.get("file_paths") or args.get("paths"),
            directory=str(args.get("directory") or "."),
            max_files=int(args.get("max_files") or args.get("maxFiles") or 5),
            sample_rows=int(args.get("sample_rows") or args.get("sampleRows") or 200),
        )

    if chosen == "files":
        from excelmanus.workbook.data import inspect_excel_files

        return inspect_excel_files(
            directory=str(args.get("directory") or "."),
            max_files=int(args.get("max_files") or args.get("maxFiles") or 20),
            include=args.get("include"),
            search=args.get("query") or args.get("search"),
            sheet_name=args.get("sheet_name"),
        )

    return _invalid(
        f"不支持的 analyze.mode={chosen}。"
        "可用：profile / quality / filter / relationships / files"
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
        return _invalid("当前不支持样式对比。请省略 ignore_style 或设为 true。")
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
    ops = args.get("operations")
    if not target or not ops:
        return _invalid("需要 file_path 与 operations")
    from excelmanus.workbook.charts import add_chart_to_workbook, normalize_chart_args

    prepared = []
    for index, raw in enumerate(ops):
        if not isinstance(raw, dict):
            return _invalid(f"operations[{index}] 必须是对象")
        kind = str(_op_get(raw, "kind", "action") or "chart")
        if kind not in {"chart", "create_chart"}:
            return _invalid(
                f"不支持的 object.kind={kind}。当前仅支持 chart；"
                "合并单元格请用 format_spreadsheet"
            )
        chart_type = str(_op_get(raw, "chart_type", "chartType") or "")
        data_range = str(_op_get(raw, "data_range", "dataRange") or "")
        if not chart_type or not data_range:
            return _invalid("chart 需要 chart_type 与 data_range")
        try:
            _require_explicit_sheet(
                raw,
                "chart",
                "data_range",
                "dataRange",
                "categories_range",
                "categoriesRange",
                "target_cell",
                "targetCell",
            )
        except MutationAborted as exc:
            return exc.result
        spec = normalize_chart_args(
            chart_type=chart_type,
            data_range=data_range,
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
            return spec
        prepared.append(spec)

    applied: list[str] = []
    last_meta: dict[str, Any] = {}

    def mutate(wb: Any) -> None:
        for spec in prepared:
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
    parsed = parse_sheet_address(cell)
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


def get_tools() -> list[ToolDef]:
    return [
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
                    "file_path": {"type": "string", "description": "工作区相对路径，可用 path 别名"},
                    "path": {"type": "string"},
                    "sheet_name": {"type": "string", "description": "工作表名，可用 sheet 别名"},
                    "sheet": {"type": "string"},
                    "range": {
                        "type": "string",
                        "description": "A1:F20，或 表名!A1:F20 / '表名'!A1:F20",
                    },
                    "cell_range": {"type": "string", "description": "range 的别名"},
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
                        "description": "列头所在行号（从0开始，Excel 第1行=0），默认自动检测",
                    },
                    "max_rows": {
                        "type": "integer",
                        "description": "无精确 range 时限制行数；与 range 同时用会 INVALID_ARGS",
                    },
                    "query": {"type": "string", "description": "search 模式的查询串"},
                    "match_mode": {
                        "type": "string",
                        "enum": ["contains", "exact", "regex", "startswith"],
                    },
                    "directory": {"type": "string", "description": "overview 且无 file_path 时扫描目录"},
                    "offset": {"type": "integer"},
                    "sample_rows": {"type": "integer"},
                    "max_results": {"type": "integer", "default": 50},
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
                        "enum": ["profile", "quality", "filter", "relationships", "files"],
                    },
                    "file_path": {"type": "string"},
                    "path": {"type": "string"},
                    "sheet_name": {"type": "string", "description": "工作表名，可用 sheet 别名"},
                    "sheet": {"type": "string"},
                    "header_row": {
                        "type": "integer",
                        "description": "列头所在行号（从0开始，Excel 第1行=0），默认自动检测",
                    },
                    "column": {"type": "string"},
                    "operator": {
                        "type": "string",
                        "description": "eq/ne/gt/ge/lt/le/contains/regex/in/not_in/between/isnull/notnull/startswith/endswith；也接受 =、==、!=",
                    },
                    "value": {},
                    "conditions": {"type": "array", "items": {"type": "object"}},
                    "logic": {"type": "string", "enum": ["and", "or"]},
                    "columns": {"type": "array", "items": {"type": "string"}},
                    "max_rows": {"type": "integer"},
                    "sort_by": {"type": "string"},
                    "ascending": {"type": "boolean", "default": True},
                    "limit": {"type": "integer"},
                    "directory": {"type": "string"},
                    "file_paths": {"type": "array", "items": {"type": "string"}},
                    "paths": {"type": "array", "items": {"type": "string"}},
                    "max_files": {"type": "integer"},
                    "query": {"type": "string"},
                    "include": {"type": "array", "items": {"type": "string"}},
                    "sample_rows": {"type": "integer"},
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
                        "description": "position 按单元格行列坐标；key 需 key_columns。二者不能同时用。",
                    },
                    "key_columns": {"type": "array", "items": {"type": "string"}},
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
                        "type": "array",
                        "description": (
                            "有序操作。kind=write 要 sheet/start_cell/values（矩形；start_cell 可用 表!B2）；"
                            "kind=insert 要 axis=row|column 与 at/count（at 从 1 起，列可用字母）；"
                            "有公式或图表的表不能 insert/rename（不维护引用）。"
                            "kind=sheet 要 action=create|copy|rename|delete；"
                            "kind=copy 只复制值与公式文本，不译相对引用、不拷样式；"
                            "source_range 必须有行列边界（不要 A:A）；表名冲突会拒绝。"
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "kind": {
                                    "type": "string",
                                    "enum": ["write", "insert", "sheet", "copy"],
                                },
                                "sheet": {"type": "string"},
                                "sheet_name": {"type": "string"},
                                "start_cell": {"type": "string", "description": "A1，或 销售明细!B2"},
                                "values": {"type": "array"},
                                "axis": {"type": "string"},
                                "at": {},
                                "count": {"type": "integer"},
                                "action": {"type": "string"},
                                "new_name": {"type": "string"},
                                "source_sheet": {"type": "string"},
                                "source_range": {"type": "string"},
                                "target_sheet": {"type": "string"},
                                "target_start": {"type": "string"},
                            },
                        },
                    },
                    "workbook_spec": {
                        "description": (
                            "创建用 WorkbookSpec，与 operations 互斥。"
                            "必填 sheets[]（name、dimensions{rows,cols}）与 uncertainties[]"
                            "（项为 {location, reason, candidate_values}；没有不确定项时为 []）。"
                            "每个 sheet 可用 value_blocks[{start, values}]、"
                            "formula_blocks[{start, formulas}]、"
                            "cells[{address, value；公式用 value='=A1' 且 value_type=formula}]、"
                            "styles{style_id → {font, fill:{type,color}, border, alignment, number_format}}、"
                            "style_regions[{range, style_id}]、merged_ranges[{range} 或 'A1:B1']、"
                            "column_widths（[18,12] 或 {\"A\":18,\"B\":12}）、"
                            "row_heights（{\"1\":22} 或 [22,15]）。"
                            "可选顶层 name、locale、default_font（{\"name\":\"微软雅黑\"} 或 \"微软雅黑\"）、theme_hint。"
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
                        "type": "array",
                        "description": (
                            "每项带 sheet（可用 sheet_name）与 kind。"
                            "range 写 A5:C5，也接受 区域汇总!A5:C5。"
                            "kind=format：range + 可选 font/fill/border/alignment/number_format；"
                            "fill 可用 {color, type|pattern|fill_type}。"
                            "kind=merge|unmerge：range；"
                            "kind=size：columns 为 {\"A\":18} 或 [18,12]，rows 为 {\"1\":22} 或 [22,15]；"
                            "或 auto_fit=true。"
                        ),
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "kind": {
                                    "type": "string",
                                    "enum": ["format", "merge", "unmerge", "size"],
                                },
                                "sheet": {"type": "string"},
                                "sheet_name": {"type": "string"},
                                "range": {
                                    "type": "string",
                                    "description": "A5:C5，或 区域汇总!A5:C5",
                                },
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
                                "columns": {},
                                "rows": {},
                                "auto_fit": {"type": "boolean"},
                                "axis": {"type": "string"},
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
            name="manage_spreadsheet_objects",
            description=TOOL_DESCRIPTIONS["manage_spreadsheet_objects"],
            input_schema={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "path": {"type": "string"},
                    "operations": {
                        "type": "array",
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
                                    "description": "A1:B12，或 数据!A1:B12",
                                },
                                "categories_range": {"type": "string"},
                                "target_cell": {"type": "string"},
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
                    "target": {"type": "string", "description": "Sheet!Cell，如 产品表!B2"},
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
            max_result_chars=4000,
        ),
    ]
