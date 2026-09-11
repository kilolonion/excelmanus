"""模型面表格意图工具：八类职责，复用本仓库提交路径与 openpyxl 实现。

模型与 Code Mode SDK 只看到这八个名字。旧微工具（read_excel / filter_data /
create_excel_chart 等）只作为本模块的内部实现，不再注册、也不作为回退入口。
Host 仍拥有锁、版本校验和宏字节保留。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from openpyxl.utils import column_index_from_string, get_column_letter
from openpyxl.utils.cell import coordinate_to_tuple, range_boundaries

from excelmanus.engine_core.tool_result import ToolError, ToolResult, ToolUiMeta, from_payload
from excelmanus.logger import get_logger
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
from excelmanus.workbook.cells import _coerce_value, _resolve_merged_cell
from excelmanus.workbook.styles import (
    _build_alignment,
    _build_border,
    _build_fill,
    _build_font,
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


def _invalid(message: str, *, code: str = "INVALID_ARGS") -> ToolResult:
    payload = {"status": "error", "code": code, "message": message}
    return ToolResult(
        success=False,
        model_text=message,
        value=payload,
        error=ToolError(code=code, message=message, fields=payload),
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
        return commit_error_result(exc)

    return rel, safe_path, cr


def _apply_write(wb: Any, op: dict[str, Any]) -> str:
    sheet = _op_get(op, "sheet", "sheet_name")
    start = str(_op_get(op, "start_cell", "startCell", "cell") or "")
    values = _op_get(op, "values")
    if not start or not isinstance(values, list) or not values:
        raise MutationAborted(_invalid("write 需要 start_cell 与非空矩形 values"))
    ws = get_worksheet(wb, sheet)
    row0, col0 = coordinate_to_tuple(start.upper())
    width = None
    for r_idx, row in enumerate(values):
        cells = row if isinstance(row, list) else [row]
        if width is None:
            width = len(cells)
        elif len(cells) != width:
            raise MutationAborted(
                _invalid("values 必须是矩形；不同宽度请拆成多次 write 或补 null")
            )
        for c_idx, raw in enumerate(cells):
            actual_row, actual_col, _redirected = _resolve_merged_cell(
                ws, row0 + r_idx, col0 + c_idx
            )
            ws.cell(row=actual_row, column=actual_col, value=_coerce_value(raw))
    return f"{start}:{get_column_letter(col0 + width - 1)}{row0 + len(values) - 1}"


def _apply_insert(wb: Any, op: dict[str, Any]) -> str:
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
    ws = get_worksheet(wb, sheet)
    if axis == "row":
        ws.insert_rows(at, amount=count)
        return f"rows@{at}+{count}"
    if axis == "column":
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
    src_sheet = _op_get(op, "source_sheet", "sourceSheet")
    src_range = str(_op_get(op, "source_range", "sourceRange") or "")
    dst_sheet = _op_get(op, "target_sheet", "targetSheet")
    dst_start = str(_op_get(op, "target_start", "targetStart") or "A1")
    if not src_sheet or not src_range or not dst_sheet:
        raise MutationAborted(_invalid("copy 需要 source_sheet/source_range/target_sheet"))
    src = get_worksheet(wb, src_sheet)
    dst = get_worksheet(wb, dst_sheet)
    min_col, min_row, max_col, max_row = range_boundaries(src_range.upper())
    start_row, start_col = coordinate_to_tuple(dst_start.upper())
    for row in range(min_row, max_row + 1):
        for col in range(min_col, max_col + 1):
            value = src.cell(row=row, column=col).value
            dst.cell(
                row=start_row + row - min_row,
                column=start_col + col - min_col,
                value=value,
            )
    return f"{src_sheet}!{src_range}->{dst_sheet}!{dst_start}"


def _apply_format(wb: Any, op: dict[str, Any]) -> str:
    kind = str(_op_get(op, "kind") or "format")
    sheet = _op_get(op, "sheet", "sheet_name")
    ws = get_worksheet(wb, sheet)
    if kind == "merge":
        cell_range = str(_op_get(op, "range", "cell_range") or "")
        if not cell_range:
            raise MutationAborted(_invalid("merge 需要 range"))
        ws.merge_cells(cell_range)
        return f"merge:{cell_range}"
    if kind == "unmerge":
        cell_range = str(_op_get(op, "range", "cell_range") or "")
        if not cell_range:
            raise MutationAborted(_invalid("unmerge 需要 range"))
        ws.unmerge_cells(cell_range)
        return f"unmerge:{cell_range}"
    if kind == "size":
        columns = _op_get(op, "columns") or {}
        rows = _op_get(op, "rows") or {}
        auto_fit = bool(_op_get(op, "auto_fit", "autoFit"))
        axis = str(_op_get(op, "axis") or "").lower()
        if auto_fit:
            if axis in {"", "column", "columns"}:
                apply_column_sizes(ws, auto_fit=True)
            if axis in {"", "row", "rows"}:
                apply_row_sizes(ws, auto_fit=True)
            if axis and axis not in {"column", "columns", "row", "rows"}:
                raise MutationAborted(_invalid("size.axis 必须是 row 或 column"))
            return "size:auto_fit"
        if isinstance(columns, dict) and columns:
            apply_column_sizes(ws, columns)
        if isinstance(rows, dict) and rows:
            apply_row_sizes(ws, rows)
        return "size"
    cell_range = str(_op_get(op, "range", "cell_range") or "")
    if not cell_range:
        raise MutationAborted(_invalid("format 需要 range"))
    font = _build_font(_op_get(op, "font")) if _op_get(op, "font") else None
    fill = _build_fill(_op_get(op, "fill")) if _op_get(op, "fill") else None
    border = _build_border(_op_get(op, "border")) if _op_get(op, "border") else None
    alignment = _build_alignment(_op_get(op, "alignment")) if _op_get(op, "alignment") else None
    number_format = _op_get(op, "number_format", "numberFormat")
    data = ws[cell_range]
    if not isinstance(data, tuple):
        rows_data = ((data,),)
    elif data and not isinstance(data[0], tuple):
        rows_data = (data,)
    else:
        rows_data = data
    for row in rows_data:
        for cell in row if isinstance(row, tuple) else (row,):
            if font is not None:
                cell.font = font
            if fill is not None:
                cell.fill = fill
            if border is not None:
                cell.border = border
            if alignment is not None:
                cell.alignment = alignment
            if number_format:
                cell.number_format = str(number_format)
    return f"format:{cell_range}"


def edit_spreadsheet(
    file_path: str,
    operations: list[dict[str, Any]] | None = None,
    workbook_spec: dict[str, Any] | str | None = None,
    create_workbook: bool = False,
    expected_version: str | None = None,
) -> ToolResult:
    """一次原子请求：写值/插入行列/改表结构，或编译 WorkbookSpec。"""
    if workbook_spec not in (None, ""):
        if operations:
            return _invalid("operations 与 workbook_spec 互斥")
        from excelmanus.replica_spec import SpecValidationError, compile_workbook_spec_to_bytes, validate_workbook_spec

        try:
            spec = validate_workbook_spec(workbook_spec)
        except SpecValidationError as exc:
            return from_payload(exc.to_payload())

        guard = _get_guard()
        target = file_path or "outputs/draft.xlsx"
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
    file_path: str,
    operations: list[dict[str, Any]] | None = None,
    expected_version: str | None = None,
) -> ToolResult:
    """一次原子请求：字体/填充/边框/对齐、合并、行列尺寸。"""
    if not operations:
        return _invalid("请提供 format operations")

    applied: list[str] = []

    def mutate(wb: Any) -> None:
        for index, raw in enumerate(operations):
            if not isinstance(raw, dict):
                raise MutationAborted(_invalid(f"operations[{index}] 必须是对象"))
            applied.append(_apply_format(wb, raw))

    committed = _commit(
        file_path=file_path,
        mutate_fn=mutate,
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


def _revision_index_path(guard: FileAccessGuard) -> Path:
    return guard.workspace_root / "outputs" / ".versions" / "intent_revisions.json"


def _load_revisions(guard: FileAccessGuard) -> dict[str, list[dict[str, Any]]]:
    path = _revision_index_path(guard)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_revisions(guard: FileAccessGuard, data: dict[str, list[dict[str, Any]]]) -> None:
    path = _revision_index_path(guard)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def manage_spreadsheet_versions(
    file_path: str,
    action: str,
    revision_id: str | None = None,
    expected_version: str | None = None,
    label: str | None = None,
    limit: int = 50,
) -> ToolResult:
    """列出当前版本与检查点，创建检查点，或按检查点恢复。"""
    guard = _get_guard()
    try:
        dest = guard.resolve_and_validate(file_path)
    except SecurityViolationError as exc:
        return commit_error_result(CommitError("PATH_INVALID", str(exc)))
    rel = workspace_relpath(guard, dest)
    current = content_version_of_file(dest) if dest.is_file() else None
    seen = peek_seen_content_version(rel)
    bag = _load_revisions(guard)
    history = list(bag.get(rel, []))

    if action == "list":
        return _success(
            {
                "file_path": rel,
                "content_version": current,
                "seen_version": seen,
                "revisions": history[-max(1, int(limit)) :],
            }
        )

    if action == "checkpoint":
        if not dest.is_file() or not current:
            return _invalid("文件不存在，无法建立检查点", code="PATH_INVALID")
        revision = f"rev_{int(time.time() * 1000):x}"
        store = dest.parent / ".em-revisions"
        # snapshots live under workspace versions dir
        store = guard.workspace_root / "outputs" / ".versions" / revision
        store.mkdir(parents=True, exist_ok=True)
        snapshot = store / dest.name
        snapshot.write_bytes(dest.read_bytes())
        entry = {
            "revision_id": revision,
            "content_version": current,
            "snapshot": str(snapshot.relative_to(guard.workspace_root)),
            "label": label or "",
            "created_at": time.time(),
        }
        history.append(entry)
        bag[rel] = history
        _save_revisions(guard, bag)
        return _success({"file_path": rel, "content_version": current, "revision": entry})

    if action == "restore":
        if not revision_id:
            return _invalid("restore 需要 revision_id")
        target = next((item for item in history if item.get("revision_id") == revision_id), None)
        if target is None:
            return _invalid(f"找不到 revision_id={revision_id}", code="NOT_FOUND")
        snap = guard.workspace_root / str(target["snapshot"])
        if not snap.is_file():
            return _invalid("检查点快照缺失", code="NOT_FOUND")
        try:
            cr = commit_bytes(
                guard=guard,
                file_path=rel,
                data=snap.read_bytes(),
                expected_version=expected_version or current,
            )
        except CommitError as exc:
            return commit_error_result(exc)
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
        "写入必须使用最新 content_version；冲突时先 inspect 再 rebase",
        "图片由主模型阅读后产出 WorkbookSpec，经 edit_spreadsheet(workbook_spec=) 编译",
        "组合多步读/写用 run_code 调本目录 SDK，不要并行两个 mutation",
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
        range=range,
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
    chosen = str(args.get("mode") or "overview")
    target = str(args.get("file_path") or "")

    if chosen == "capabilities":
        return _success(dict(_MODEL_CAPABILITIES))

    if chosen == "search":
        from excelmanus.workbook.data import search_excel_values

        q = str(args.get("query") or "")
        if not q:
            return _invalid("search 需要 query")
        return search_excel_values(
            file_path=target,
            query=q,
            match_mode=str(args.get("match_mode") or args.get("searchMode") or "contains"),
            sheets=[args["sheet_name"]] if args.get("sheet_name") else None,
            max_results=int(args.get("max_results") or args.get("maxResults") or 50),
        )

    if chosen == "range":
        from excelmanus.workbook.data import read_excel

        if not target:
            return _invalid("range 需要 file_path / path")
        return read_excel(
                file_path=target,
                sheet_name=args.get("sheet_name"),
                max_rows=args.get("max_rows") or args.get("maxRows"),
                header_row=args.get("header_row") if args.get("header_row") is not None else args.get("headerRow"),
                include=args.get("include"),
                range=args.get("range"),
                offset=args.get("offset"),
                sample_rows=args.get("sample_rows") or args.get("sampleRows"),
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

    if chosen in {"profile", "quality"}:
        from excelmanus.workbook.data import scan_excel_snapshot

        if not target:
            return _invalid(f"{chosen} 需要 file_path / path")
        return scan_excel_snapshot(
            file_path=target,
            max_sample_rows=int(args.get("max_rows") or args.get("maxRows") or 500),
            include_relationships=True,
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
        file_a=file_a or path,
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
    right = str(args.get("file_b") or args.get("other_path") or args.get("otherPath") or left)
    if not left:
        return _invalid("compare 需要 path / file_a")
    align = str(args.get("alignment") or "position")
    keys = args.get("key_columns") or args.get("keyColumns")
    if align == "key" and not keys:
        return _invalid("alignment=key 需要 key_columns")
    from excelmanus.workbook.data import compare_excel

    return compare_excel(
            file_a=left,
            file_b=right,
            sheet_a=str(args.get("sheet_a") or args.get("sheet") or ""),
            sheet_b=str(args.get("sheet_b") or args.get("other_sheet") or args.get("otherSheet") or ""),
            ignore_style=bool(args.get("ignore_style", True)),
            key_columns=list(keys) if keys else None,
            max_diffs=int(args.get("max_diffs") or args.get("maxDifferences") or 500),
        )


def manage_spreadsheet_objects(
    file_path: str = "",
    path: str = "",
    operations: list[dict[str, Any]] | None = None,
    expected_version: str | None = None,
    request: dict[str, Any] | None = None,
) -> ToolResult:
    """图表等富对象。当前实现：kind=chart 插入原生 Excel 图表。"""
    args = _merge_request(
        request,
        file_path=file_path or path,
        operations=operations,
        expected_version=expected_version,
    )
    target = str(args.get("file_path") or "")
    ops = args.get("operations")
    if not target or not ops:
        return _invalid("需要 file_path 与 operations")
    from excelmanus.workbook.charts import create_excel_chart

    last = ""
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
        last = create_excel_chart(
            file_path=target,
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
            expected_version=args.get("expected_version"),
        )
        if not last.success:
            return last
    return last


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
    if chosen == "trace":
        if not cell:
            return _invalid("trace 需要 target，如 Sheet1!B2")
        return trace_references(
            file_path=target_path,
            target=cell,
            direction=str(args.get("direction") or "both"),
            depth=int(args.get("depth") or 2),
        )
    if chosen == "impact":
        if not cell:
            return _invalid("impact 需要 target")
        return get_impact_analysis(
            file_path=target_path,
            target=cell,
            scope=str(args.get("scope") or "all"),
        )
    return _invalid(f"不支持的 trace.mode={chosen}。可用：map / trace / impact")


def get_tools() -> list[ToolDef]:
    return [
        ToolDef(
            name="inspect_spreadsheet",
            description=(
                "只读探查工作簿。mode=overview 看表结构；mode=range 读区域/预览；"
                "mode=search 跨表搜值；mode=capabilities 查询本目录能力。"
                "截断或采样结果不是全表事实。返回 content_version 供后续写入。"
            ),
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
                    "sheet_name": {"type": "string"},
                    "range": {"type": "string", "description": "如 A1:F20；range 模式精确读取"},
                    "include": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "styles/charts/formulas/summary 等附加维度",
                    },
                    "header_row": {
                        "type": "integer",
                        "description": "列头所在行号（从0开始），默认自动检测",
                    },
                    "max_rows": {"type": "integer"},
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
            description=(
                "只读数据分析。mode=profile|quality 扫描全貌与质量信号；"
                "mode=filter 按条件筛选；mode=relationships 跨文件列关联；"
                "mode=files 扫描目录工作簿。推断关系不是事实。"
            ),
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
                    "sheet_name": {"type": "string"},
                    "header_row": {
                        "type": "integer",
                        "description": "列头所在行号（从0开始），默认自动检测",
                    },
                    "column": {"type": "string"},
                    "operator": {"type": "string"},
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
                },
            },
            func=analyze_spreadsheet,
            write_effect="none",
            max_result_chars=0,
        ),
        ToolDef(
            name="compare_spreadsheets",
            description=(
                "只读对比两个工作簿或同簿两表。alignment=position 按坐标；"
                "alignment=key 且提供 key_columns 时按键对齐。差异样本不可当作全表事实。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "request": {"type": "object"},
                    "file_a": {"type": "string"},
                    "file_b": {"type": "string"},
                    "path": {"type": "string"},
                    "other_path": {"type": "string"},
                    "sheet_a": {"type": "string"},
                    "sheet_b": {"type": "string"},
                    "alignment": {"type": "string", "enum": ["position", "key"]},
                    "key_columns": {"type": "array", "items": {"type": "string"}},
                    "max_diffs": {"type": "integer", "default": 500},
                    "ignore_style": {"type": "boolean", "default": True},
                },
            },
            func=compare_spreadsheets,
            write_effect="none",
            max_result_chars=0,
        ),
        ToolDef(
            name="edit_spreadsheet",
            description=(
                "原子编辑工作簿：写值/公式、插入行列、新建/复制/重命名/删除工作表、同簿复制区域，"
                "或传入 workbook_spec 编译新表。单格修改用 write，不要为此写 run_code。"
                "相关写入走同一提交路径（版本、锁、原子替换）。外观改动请用 format_spreadsheet。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "工作区相对路径，如 book.xlsx",
                    },
                    "operations": {
                        "type": "array",
                        "description": (
                            "有序操作。kind=write 要 sheet/start_cell/values（矩形）；"
                            "kind=insert 要 axis=row|column 与 at/count；"
                            "kind=sheet 要 action=create|copy|rename|delete；"
                            "kind=copy 要 source_sheet/source_range/target_sheet/target_start。"
                        ),
                        "items": {"type": "object"},
                    },
                    "workbook_spec": {
                        "description": "最小 WorkbookSpec；提供时编译为 file_path，不再执行 operations",
                    },
                    "create_workbook": {
                        "type": "boolean",
                        "default": False,
                        "description": "文件不存在时创建",
                    },
                    "expected_version": {
                        "type": "string",
                        "description": "可选；默认使用本轮已读到的 content_version",
                    },
                },
                "required": ["file_path"],
            },
            func=edit_spreadsheet,
            write_effect="workspace_write",
            max_result_chars=4000,
        ),
        ToolDef(
            name="format_spreadsheet",
            description=(
                "原子修改外观：单元格字体/填充/边框/对齐/数字格式、合并/取消合并、行列尺寸。"
                "不要为“标题加粗”写 openpyxl 脚本。值和表结构请用 edit_spreadsheet。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "工作区相对路径"},
                    "operations": {
                        "type": "array",
                        "description": (
                            "kind=format 要 range 与可选 font/fill/border/alignment/number_format；"
                            "kind=merge|unmerge 要 range；kind=size 要 columns/rows 映射。"
                        ),
                        "items": {"type": "object"},
                    },
                    "expected_version": {"type": "string"},
                },
                "required": ["file_path", "operations"],
            },
            func=format_spreadsheet,
            write_effect="workspace_write",
            max_result_chars=4000,
        ),
        ToolDef(
            name="manage_spreadsheet_objects",
            description=(
                "富对象入口：当前支持 kind=chart 插入原生 Excel 图表（bar/line/pie/scatter/area）。"
                "合并单元格请用 format_spreadsheet。不要为插图写 openpyxl 脚本。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "path": {"type": "string"},
                    "operations": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": (
                            "kind=chart 要 chart_type 与 data_range，可选 categories_range/"
                            "sheet/target_cell/title"
                        ),
                    },
                    "expected_version": {"type": "string"},
                },
                "required": ["operations"],
            },
            func=manage_spreadsheet_objects,
            write_effect="workspace_write",
            max_result_chars=4000,
        ),
        ToolDef(
            name="trace_spreadsheet_formulas",
            description=(
                "只读公式分析。mode=map 全景引用；mode=trace 追踪单元格先例/依赖；"
                "mode=impact 分析修改影响面。部分解析结果是限定证据，不是证明。"
            ),
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
            description=(
                "版本入口：list 当前 content_version 与检查点；checkpoint 打快照；"
                "restore 按 revision_id 恢复（必须带或读到 expected_version）。"
                "历史读取结果不可当作写入目标。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "action": {
                        "type": "string",
                        "enum": ["list", "checkpoint", "restore"],
                    },
                    "revision_id": {"type": "string"},
                    "expected_version": {"type": "string"},
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
