"""工作表只读探查：列出工作表名与按需维度。

创建 / 复制 / 重命名 / 删除 / 跨表拷贝走 `edit_spreadsheet`，本模块不提交工作簿。
"""

from __future__ import annotations

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
from excelmanus.tools._guard_ctx import get_guard as _get_ctx_guard
from excelmanus.tools._helpers import check_file_exists, workspace_relpath
from excelmanus.workbook_commit import content_version_of_file

logger = get_logger("tools.sheet")

_guard: FileAccessGuard | None = None
_MAX_LIST_PAGE_SIZE = 500


def _get_guard() -> FileAccessGuard:
    """获取或创建 FileAccessGuard（优先 per-session contextvar）。"""
    ctx_guard = _get_ctx_guard()
    if ctx_guard is not None:
        return ctx_guard
    global _guard
    if _guard is None:
        _guard = FileAccessGuard(".")
    return _guard


def init_guard(workspace_root: str) -> None:
    """初始化文件访问守卫（供外部配置调用）。"""
    global _guard
    _guard = FileAccessGuard(workspace_root)


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
)


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
        max_preview_rows: preview 维度的预览行数，默认 5。

    Returns:
        ToolResult（value 为工作表列表，ui_meta 带路径与版本）。
    """
    paging_error = _validate_pagination(offset, limit)
    if paging_error is not None:
        return error_result(
            paging_error,
            code="INVALID_ARGS",
            fields={"error": paging_error},
        )

    guard = _get_guard()
    safe_path = guard.resolve_and_validate(file_path)

    from excelmanus.tools._helpers import ensure_openpyxl_compatible
    safe_path = ensure_openpyxl_compatible(safe_path)

    not_found = check_file_exists(safe_path, file_path, guard)
    if not_found is not None:
        return not_found

    include_set: set[str] = set(include) if include else set()
    invalid_dims = include_set - set(_LIST_SHEETS_DIMENSIONS)
    include_set -= invalid_dims

    needs_full = bool(include_set - {"columns", "dtypes", "preview"})

    wb = load_workbook(safe_path, read_only=not needs_full, data_only=True)
    try:
        active_name = wb.active.title if wb.active else None
        sheets: list[dict[str, Any]] = []
        for ws in wb.worksheets:
            info: dict[str, Any] = {
                "name": ws.title,
                "rows": ws.max_row or 0,
                "columns": ws.max_column or 0,
                "is_active": ws.title == active_name,
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

            sheets.append(info)
    finally:
        wb.close()

    total = len(sheets)
    end = offset + limit
    paged_sheets = sheets[offset:end]
    has_more = end < total

    rel = workspace_relpath(guard, safe_path)
    version = content_version_of_file(safe_path)
    result: dict[str, Any] = {
        "file": safe_path.name,
        "file_path": rel,
        "sheet_count": total,
        "offset": offset,
        "limit": limit,
        "returned": len(paged_sheets),
        "has_more": has_more,
        "sheets": paged_sheets,
        "content_version": version,
    }
    if invalid_dims:
        result["include_warning"] = f"未知的 include 维度已忽略: {sorted(invalid_dims)}"

    names = [str(item.get("name") or "") for item in paged_sheets if item.get("name")]
    model_text = f"{safe_path.name}: {total} sheets"
    if names:
        shown = ", ".join(names[:12])
        model_text += f" ({shown})"
        if len(names) > 12 or has_more:
            model_text += " …"
    return ok_result(
        result,
        model_text=model_text,
        ui_meta=ToolUiMeta(
            files=[rel],
            content_version=version,
            preview={"sheet_count": total, "sheets": names},
        ),
    )
