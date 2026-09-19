"""工作表只读探查：列出工作表名与按需维度。

创建 / 复制 / 重命名 / 删除 / 跨表拷贝走 `edit_spreadsheet`，本模块不提交工作簿。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from excelmanus.engine_core.tool_result import (
    ToolResult,
    ToolUiMeta,
    error_result,
    ok_result,
)
from excelmanus.logger import get_logger
from excelmanus.security import FileAccessGuard
from excelmanus.tools.context import bind_workspace, require_guard
from excelmanus.tools._helpers import check_file_exists, workspace_relpath


def _collect_compact_merges(ws: Any, *, limit: int = 20) -> dict[str, Any]:
    ranges = [str(item) for item in ws.merged_cells.ranges]
    return {"count": len(ranges), "ranges": ranges[:limit]}


def _collect_compact_formulas(ws: Any, *, limit: int = 20) -> dict[str, Any]:
    from excelmanus.workbook.data import _collect_formulas

    payload = _collect_formulas(ws, max_rows=80)
    items = payload.get("items") or []
    return {
        "count": len(items),
        "sample": items[:limit],
        "rows_scanned": payload.get("rows_scanned", 80),
        "truncated": bool(payload.get("truncated")),
    }


def _collect_compact_styles(ws: Any) -> dict[str, Any]:
    from excelmanus.workbook.data import _collect_styles_compressed

    return _collect_styles_compressed(ws, max_rows=80)


def _collect_compact_dtypes(ws: Any, *, max_rows: int = 20) -> dict[str, str]:
    from collections import Counter

    from openpyxl.utils import get_column_letter

    types: dict[str, str] = {}
    max_col = min(ws.max_column or 0, 40)
    max_row = min(ws.max_row or 0, max_rows)
    for col in range(1, max_col + 1):
        counts: Counter[str] = Counter()
        for row in range(1, max_row + 1):
            value = ws.cell(row=row, column=col).value
            if value is None:
                continue
            counts[type(value).__name__] += 1
        types[get_column_letter(col)] = counts.most_common(1)[0][0] if counts else "empty"
    return types

logger = get_logger("tools.sheet")

_MAX_LIST_PAGE_SIZE = 500


def _get_guard() -> FileAccessGuard:
    return require_guard()


def init_guard(workspace_root: str) -> None:
    bind_workspace(workspace_root)


def _validate_pagination(offset: int, limit: int, *, max_limit: int = _MAX_LIST_PAGE_SIZE) -> str | None:
    """校验分页参数，返回错误信息或 None。"""
    if offset < 0:
        return "offset 必须大于或等于 0"
    if limit <= 0:
        return "limit 必须为正整数"
    if limit > max_limit:
        return f"limit 不能超过 {max_limit}"
    return None


_LIST_SHEETS_DIMENSIONS = (
    "columns",
    "dtypes",
    "freeze_panes",
    "preview",
    "charts",
    "images",
    "conditional_formatting",
    "column_widths",
    "styles",
    "merges",
    "formulas",
)
_LIST_SHEETS_LIGHT_DIMS = {"columns", "preview"}


def _sheet_state_of(ws: Any) -> str:
    return str(getattr(ws, "sheet_state", None) or "visible")


def _overview_sheet_line(item: dict[str, Any]) -> str:
    name = str(item.get("name") or "")
    rows = item.get("rows", 0)
    cols = item.get("columns", 0)
    state = str(item.get("sheet_state") or "visible")
    hidden = "" if state == "visible" else f" {state}"
    bits: list[str] = []
    names = item.get("column_names")
    if names:
        shown = ",".join("" if c is None else str(c) for c in names[:8])
        if len(names) > 8:
            shown += "…"
        bits.append(f"列={shown}")
    merges = item.get("merges")
    if isinstance(merges, dict):
        bits.append(f"合并{int(merges.get('count') or 0)}")
        ranges = merges.get("ranges") or []
        if ranges:
            bits.append("/".join(str(r) for r in ranges[:3]))
    formulas = item.get("formulas")
    if isinstance(formulas, dict):
        count = int(formulas.get("count") or 0)
        bits.append(f"公式{count}")
        sample = formulas.get("sample") or []
        shown: list[str] = []
        for entry in sample[:8]:
            if not isinstance(entry, dict):
                continue
            cell = entry.get("cell")
            formula = entry.get("formula")
            if not cell or not formula:
                continue
            formula_text = str(formula)
            if len(formula_text) > 48:
                formula_text = formula_text[:45] + "…"
            shown.append(f"{cell}{formula_text}")
        if shown:
            bits.append("; ".join(shown))
            rest = count - len(shown)
            if rest > 0:
                bits.append(f"等{rest}个")
    styles = item.get("styles")
    if isinstance(styles, dict):
        classes = styles.get("style_classes") or {}
        bits.append(f"样式类{len(classes)}")
    freeze = item.get("freeze_panes")
    if freeze:
        bits.append(f"冻结={freeze}")
    extra = f" {' '.join(bits)}" if bits else ""
    return f"- {name}: {rows}×{cols}{hidden}{extra}"


def _overview_model_text(
    rel: str,
    total: int,
    paged_sheets: list[dict[str, Any]],
    has_more: bool,
    include_warning: str,
) -> str:
    lines = [f"{Path(rel).name}: {total} sheets"]
    for item in paged_sheets[:12]:
        lines.append(_overview_sheet_line(item))
    if len(paged_sheets) > 12 or has_more:
        lines.append("…")
    if include_warning:
        lines.append(f"⚠️ {include_warning}")
    return "\n".join(lines)


def list_sheets(
    file_path: str,
    offset: int = 0,
    limit: int = 100,
    include: list[str] | None = None,
    max_preview_rows: int = 5,
) -> ToolResult:
    """列出 Excel 文件中所有工作表的名称和基本信息，可按需附加额外维度。

    Args:
        file_path: Excel 文件路径。
        offset: 分页起始偏移（从 0 开始），默认 0。
        limit: 分页大小，默认 100，最大 500。
        include: 按需请求的额外维度列表。可选值：
            columns — 列名列表
            dtypes — 列数据类型（需用 pandas 读取）
            freeze_panes — 冻结窗格位置
            preview — 前 N 行数据预览
            charts — 嵌入图表元信息
            images — 嵌入图片元信息
            conditional_formatting — 条件格式规则
            column_widths — 非默认列宽
            styles / merges / formulas — 压缩摘要，不是全表 dump
            dtypes — 前若干行推断的列类型
        max_preview_rows: preview 维度的预览行数，默认 5。

    Returns:
        ToolResult（value 为工作表列表，ui_meta 带路径与版本）。
    """
    paging_error = _validate_pagination(offset, limit)
    if paging_error is not None:
        return error_result(
            paging_error,
            code="INVALID_ARGS",
        )

    guard = _get_guard()
    live_path = guard.resolve_and_validate(file_path)
    not_found = check_file_exists(live_path, file_path, guard)
    if not_found is not None:
        return not_found
    from excelmanus.workbook.data import _open_tool_snapshot

    snap, snap_err = _open_tool_snapshot(file_path)
    if snap_err is not None:
        return snap_err
    safe_path = snap.backing_path

    include_set: set[str] = set(include) if include else set()
    invalid_dims = include_set - set(_LIST_SHEETS_DIMENSIONS)
    include_set -= invalid_dims
    include_warning = ""
    if invalid_dims:
        include_warning = f"未知的 include 维度已忽略: {sorted(invalid_dims)}"

    needs_full = bool(include_set - _LIST_SHEETS_LIGHT_DIMS)
    needs_formulas = "formulas" in include_set

    wb = load_workbook(safe_path, read_only=not needs_full, data_only=not needs_formulas)
    try:
        active_name = wb.active.title if wb.active else None
        sheets: list[dict[str, Any]] = []
        for ws in wb.worksheets:
            state = _sheet_state_of(ws)
            info: dict[str, Any] = {
                "name": ws.title,
                "rows": ws.max_row or 0,
                "columns": ws.max_column or 0,
                "is_active": ws.title == active_name,
                "sheet_state": state,
                "hidden": state != "visible",
            }

            if "columns" in include_set:
                header_row = list(ws.iter_rows(
                    min_row=1, max_row=1, values_only=True,
                ))
                if header_row and header_row[0]:
                    info["column_names"] = [
                        str(c) if c is not None else None
                        for c in header_row[0]
                        if c is not None
                    ]

            if "preview" in include_set:
                preview_rows: list[list[Any]] = []
                for row in ws.iter_rows(
                    min_row=2, max_row=1 + max_preview_rows, values_only=True,
                ):
                    preview_rows.append([
                        str(c) if c is not None else None for c in row
                    ])
                info["preview"] = preview_rows

            if needs_full and include_set:
                from excelmanus.workbook.data import (
                    _collect_charts,
                    _collect_column_widths,
                    _collect_conditional_formatting,
                    _collect_freeze_panes,
                    _collect_images,
                )

                if "freeze_panes" in include_set:
                    info["freeze_panes"] = _collect_freeze_panes(ws)
                if "charts" in include_set:
                    info["charts"] = _collect_charts(ws)
                if "images" in include_set:
                    info["images"] = _collect_images(ws)
                if "conditional_formatting" in include_set:
                    info["conditional_formatting"] = _collect_conditional_formatting(ws)
                if "column_widths" in include_set:
                    info["column_widths"] = _collect_column_widths(ws)
                if "styles" in include_set:
                    info["styles"] = _collect_compact_styles(ws)
                if "merges" in include_set:
                    info["merges"] = _collect_compact_merges(ws)
                if "formulas" in include_set:
                    info["formulas"] = _collect_compact_formulas(ws)
                if "dtypes" in include_set:
                    info["dtypes"] = _collect_compact_dtypes(ws)

            sheets.append(info)
    finally:
        wb.close()

    total = len(sheets)
    end = offset + limit
    paged_sheets = sheets[offset:end]
    has_more = end < total

    rel = snap.file.relative
    version = snap.content_version
    from excelmanus.workbook.snapshot import Coverage, apply_read_contract

    result: dict[str, Any] = {
        "file": Path(rel).name,
        "file_path": rel,
        "sheet_count": total,
        "offset": offset,
        "limit": limit,
        "returned": len(paged_sheets),
        "has_more": has_more,
        "sheets": paged_sheets,
        "content_version": version,
    }
    if include_warning:
        result["include_warning"] = include_warning

    names = [str(item.get("name") or "") for item in paged_sheets if item.get("name")]
    result["resolved_sheets"] = names
    if len(names) == 1:
        result["resolved_sheet"] = names[0]
    apply_read_contract(
        result,
        snapshot=snap,
        result_kind="matrix",
        sheet=result.get("resolved_sheet"),
        coverage=Coverage(
            kind="truncated" if has_more else "complete",
            returned_rows=len(paged_sheets),
            total_rows=total,
            offset=offset,
        ),
        formulas_uncached="unknown",
        meta_kind="overview",
    )
    model_text = _overview_model_text(
        rel, total, paged_sheets, has_more, include_warning,
    )
    return ok_result(
        result,
        model_text=model_text,
        ui_meta=ToolUiMeta(
            files=[rel],
            content_version=version,
            preview={"sheet_count": total, "sheets": names},
        ),
    )
