"""数据工具：Excel 读取、过滤、探查与对比（模型面经八类意图调用）。"""

from __future__ import annotations

import functools
import json
import operator as _operator
import re
import shutil
from datetime import date, datetime
from typing import Any

import pandas as pd

_builtin_range = range  # 保存内置 range，避免被同名函数参数遮蔽

from dataclasses import replace

from excelmanus.engine_core import error_payload as _err_codes
from excelmanus.engine_core.tool_result import ToolResult, ToolUiMeta, error_result
from excelmanus.logger import get_logger
from excelmanus.security import FileAccessGuard
from excelmanus.tools.context import bind_workspace, require_guard
from excelmanus.tools._helpers import check_file_exists, get_worksheet, resolve_sheet_name
from excelmanus.workbook.refs import (
    AreaRef,
    InvalidRefError,
    NamedRef,
    RectRef,
    TableRef,
    parse_rect,
    parse_ref,
)

logger = get_logger("tools.data")

NAMED_RANGE_NOT_FOUND = getattr(_err_codes, "NAMED_RANGE_NOT_FOUND", "NAMED_RANGE_NOT_FOUND")
TABLE_NOT_FOUND = getattr(_err_codes, "TABLE_NOT_FOUND", "TABLE_NOT_FOUND")
RANGE_INVALID = _err_codes.RANGE_INVALID
INVALID_ARGS = _err_codes.INVALID_ARGS
SHEET_NOT_FOUND = _err_codes.SHEET_NOT_FOUND


class WorkbookRefBindError(ValueError):
    """命名区域 / 表引用无法绑定到工作簿元数据。"""

    def __init__(self, message: str, *, code: str, fields: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.fields = dict(fields or {})

# ── 表头识别配置 ──────────────────────────────────────────

_HEADER_MIN_NON_EMPTY = 3
_HEADER_SCAN_ROWS = 30
_HEADER_SCAN_COLS = 200
_HEADER_KEYWORDS = (
    "月份",
    "日期",
    "时间",
    "城市",
    "地区",
    "产品",
    "部门",
    "姓名",
    "工号",
    "编号",
    "金额",
    "数量",
    "状态",
    "营收",
    "利润",
    "成本",
)
_TITLE_HINT_PREFIXES = (
    "生成时间",
    "汇总",
    "分析",
    "报表",
    "仪表盘",
    "机密",
)

# ── 表单类文档识别配置 ────────────────────────────────────
# 表单类文档特征：大量标签-值对，标签行占比高，大量合并单元格
_FORM_LABEL_KEYWORDS = frozenset({
    # 只保留“表单专属”的标签词（收据/付款/账务场景）。
    # 注意不要再加入 姓名/日期/单位/备注/编号/电话/地址/客户 这类
    # 正常数据表的列名——它们会让花名册、联系人表、发票登记表被误判为
    # 表单文档（header=None → 模型拿到数字列名）。
    "交款人", "交款单位", "付款账户", "付款人",
    "付款事由", "付款方式", "付款金额", "其他金额", "费用合计", "大写金额",
    "开户行", "账号", "发票", "联系方式",
})
_FORM_LABEL_RATIO_THRESHOLD = 0.3  # 标签行占比超过此阈值认为是表单类文档
_FORM_MERGED_CELL_RATIO_THRESHOLD = 0.15  # 合并单元格占比超过此阈值认为是表单类文档
# 表头候选行达到该分数即视为“明确的数据表”，优先于表单类判定。
# 依据 _header_row_score 的量级：典型 5 列文本表头约 25-40 分，
# 单格标题行/标签行被 _HEADER_MIN_NON_EMPTY 门限或 title 惩罚压到 8 分以下。
_HEADER_CONFIDENT_SCORE = 8.0

# ── 合并单元格摘要配置 ─────────────────────────────────────
_MERGED_SUMMARY_MAX_SPANS = 20  # 摘要中最多列出的合并区域数


def _collect_merged_cell_summary(ws: Any) -> dict[str, Any] | None:
    """收集工作表的合并单元格摘要信息。

    将合并区域按语义角色分类（标题跨列、列组标头、数据区合并），
    并计算合并单元格占比，附带处理建议。

    Args:
        ws: openpyxl Worksheet 对象（非 read_only 模式）。

    Returns:
        合并摘要字典，无合并时返回 None。
    """
    from openpyxl.utils import get_column_letter

    merged_ranges = list(ws.merged_cells.ranges)
    if not merged_ranges:
        return None

    total_rows = ws.max_row or 1
    total_cols = ws.max_column or 1
    total_cells = total_rows * total_cols

    # 统计合并单元格总数
    merged_cell_count = 0
    for mr in merged_ranges:
        merged_cell_count += (mr.max_row - mr.min_row + 1) * (mr.max_col - mr.min_col + 1)
    merged_ratio = merged_cell_count / max(total_cells, 1)

    # 分类合并区域
    header_spans: list[str] = []       # 宽跨列（标题/分组标头）
    column_group_spans: list[str] = []  # 列组标头（如 "星期一" 跨若干列）
    data_merged_count = 0               # 数据区合并（跨行，暗示 NaN）

    for mr in merged_ranges:
        col_span = mr.max_col - mr.min_col + 1
        row_span = mr.max_row - mr.min_row + 1
        start_col_letter = get_column_letter(mr.min_col)
        end_col_letter = get_column_letter(mr.max_col)

        # 读取合并区域左上角值
        top_left_value = ws.cell(row=mr.min_row, column=mr.min_col).value
        label = f"'{top_left_value}'" if top_left_value else "(空)"

        range_str = str(mr)

        if col_span > total_cols * 0.5:
            # 宽跨列：跨度超过总列数 50%，通常是标题行
            header_spans.append(f"{range_str} → {label}")
        elif col_span >= 2 and row_span <= 2 and mr.min_row <= 5:
            # 列组标头：前 5 行内、跨 2+ 列但不太宽，通常是分组标头
            column_group_spans.append(
                f"{range_str} → {label} (cols {start_col_letter}:{end_col_letter})"
            )
        elif row_span >= 2:
            # 数据区跨行合并：pandas 读取时仅首格有值，其余为 NaN
            data_merged_count += 1

    summary: dict[str, Any] = {
        "merged_range_count": len(merged_ranges),
        "merged_cell_ratio": f"{merged_ratio:.1%}",
    }

    if header_spans:
        summary["header_spans"] = header_spans[:_MERGED_SUMMARY_MAX_SPANS]
    if column_group_spans:
        summary["column_group_spans"] = column_group_spans[:_MERGED_SUMMARY_MAX_SPANS]
    if data_merged_count > 0:
        summary["data_merged_ranges"] = data_merged_count
        summary["hint"] = (
            f"数据区存在 {data_merged_count} 处跨行合并，"
            "pandas 读取时仅合并区域左上角单元格有值，其余为 NaN。"
            "建议用 openpyxl ws.merged_cells.ranges 获取合并信息后做值传播（forward-fill）。"
        )

    return summary


# ── CSV/TSV 支持 ──────────────────────────────────────────

_CSV_EXTENSIONS: frozenset[str] = frozenset({".csv", ".tsv", ".txt"})

def _get_guard():
    return require_guard()


def init_guard(workspace_root: str) -> None:
    bind_workspace(workspace_root)


def build_completeness_meta(
    total_available: int,
    returned: int,
    *,
    entity_name: str = "行",
) -> dict[str, Any]:
    """构建数据完整性元数据，供工具统一使用。

    当 returned < total_available 时，附加截断标记和自然语言提示，
    帮助 LLM 正确理解数据范围，避免将预览数据误判为全量数据。
    """
    meta: dict[str, Any] = {
        "total_available": total_available,
        "returned": returned,
    }
    if returned < total_available:
        meta["is_truncated"] = True
        meta["truncation_note"] = (
            f"⚠️ 仅返回 {returned} {entity_name}（共 {total_available} {entity_name}）。"
            f"如需操作全量数据，请注意实际数据范围。"
        )
    return meta



def _is_date_like(value: Any) -> bool:
    """判断值是否为日期/时间类型。"""
    return isinstance(value, (date, datetime, pd.Timestamp))


def _normalize_cell(value: Any) -> Any:
    """将单元格值规范化为便于表头检测的格式。"""
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return text if text else None
    return value


def _is_csv_file(path: Any) -> bool:
    """判断文件是否为 CSV/TSV 格式。"""
    from pathlib import Path

    p = Path(path) if not isinstance(path, Path) else path
    return p.suffix.lower() in _CSV_EXTENSIONS


def _serialize_cell_value(value: Any) -> Any:
    """将单元格值序列化为 JSON 兼容类型。"""
    if value is None:
        return None
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, (int, float, bool)):
        return value
    return str(value)


def _open_tool_snapshot(file_path: str, *, expected_version: str | None = None):
    """打开权威快照；失败返回 (None, ToolResult)。

    有 ToolCallContext 走 ``open_snapshot``；否则用本模块 ``_get_guard``
    （测试可 patch，生产即 ``require_guard``），不回退 cwd。
    """
    from pathlib import Path

    from excelmanus.tools._helpers import workspace_relpath
    from excelmanus.tools.context import ToolContextMissing, current_call
    from excelmanus.workbook.snapshot import SnapshotError, SnapshotStale, open_snapshot, open_snapshot_at
    from excelmanus.workspace.refs import WorkspaceRef

    try:
        if current_call() is not None:
            return open_snapshot(file_path, expected_version=expected_version), None
        guard = _get_guard()
        live = Path(guard.resolve_and_validate(file_path))
        rel = workspace_relpath(guard, live).replace("\\", "/")
        snap = open_snapshot_at(
            live,
            relative=rel,
            workspace=WorkspaceRef.from_root(guard.workspace_root),
            expected_version=expected_version,
        )
        return snap, None
    except SnapshotStale as exc:
        return None, error_result(str(exc), code=exc.code, fields=exc.fields or None)
    except SnapshotError as exc:
        return None, error_result(str(exc), code=exc.code, fields=exc.fields or None)
    except ToolContextMissing as exc:
        return None, error_result(str(exc), code="TOOL_CONTEXT_MISSING")


def _mark_success(payload: dict[str, Any]) -> dict[str, Any]:
    payload["status"] = "success"
    return payload


def _bind_area_in_workbook(
    wb: Any,
    area: AreaRef,
    *,
    default_sheet: str | None = None,
) -> list[RectRef]:
    """把 AreaRef 里的命名区域 / 表引用绑定成矩形。"""
    rects: list[RectRef] = []
    fallback = str(default_sheet).strip() if default_sheet not in (None, "") else None
    for part in area.areas:
        if isinstance(part, RectRef):
            rects.append(_bind_rect_sheet(wb, part, fallback))
        elif isinstance(part, NamedRef):
            rects.extend(_named_ref_to_rects(wb, part, fallback))
        elif isinstance(part, TableRef):
            rects.append(_table_ref_to_rect(wb, part, fallback))
        else:
            raise InvalidRefError(
                f"无法绑定引用 {getattr(part, 'to_a1', lambda: part)()!r}。"
                "正确写法示例：A1:B2 或 MyName。",
            )
    return rects


def _bind_rect_sheet(wb: Any, rect: RectRef, default_sheet: str | None) -> RectRef:
    from excelmanus.workbook.snapshot import SheetRequired, SnapshotError, bind_rect_sheet

    try:
        return bind_rect_sheet(wb, rect, default_sheet)
    except SheetRequired as exc:
        raise WorkbookRefBindError(str(exc), code=exc.code, fields=getattr(exc, "fields", None)) from exc
    except SnapshotError as exc:
        raise WorkbookRefBindError(str(exc), code=exc.code, fields=getattr(exc, "fields", None)) from exc


def _named_ref_to_rects(
    wb: Any,
    named: NamedRef,
    default_sheet: str | None,
) -> list[RectRef]:
    defn = _lookup_defined_name(wb, named.name, named.sheet or default_sheet)
    if defn is None:
        raise WorkbookRefBindError(
            f"命名区域 {named.name!r} 不存在。正确写法示例：MyName。",
            code=NAMED_RANGE_NOT_FOUND,
        )
    dests: list[tuple[str | None, str]] = []
    try:
        dests = [(sheet, cells) for sheet, cells in defn.destinations]
    except Exception:
        dests = []
    if dests:
        rects: list[RectRef] = []
        for sheet, cells in dests:
            try:
                rect = parse_rect(str(cells), default_sheet=sheet or named.sheet or default_sheet)
            except InvalidRefError as exc:
                raise WorkbookRefBindError(str(exc), code=RANGE_INVALID) from exc
            rects.append(_bind_rect_sheet(wb, rect, sheet or named.sheet or default_sheet))
        return rects
    raw = str(getattr(defn, "attr_text", None) or getattr(defn, "value", None) or "").strip()
    if not raw:
        raise WorkbookRefBindError(
            f"命名区域 {named.name!r} 没有可解析的目标。正确写法示例：MyName。",
            code=NAMED_RANGE_NOT_FOUND,
        )
    try:
        nested = parse_ref(raw, default_sheet=named.sheet or default_sheet)
    except InvalidRefError as exc:
        raise WorkbookRefBindError(str(exc), code=RANGE_INVALID) from exc
    return _bind_area_in_workbook(wb, nested, default_sheet=named.sheet or default_sheet)


def _lookup_defined_name(wb: Any, name: str, sheet: str | None) -> Any | None:
    defined = getattr(wb, "defined_names", None)
    if defined is None:
        return None
    items = list(defined.values()) if hasattr(defined, "values") else []
    sheet_idx: int | None = None
    if sheet:
        resolved = resolve_sheet_name(sheet, list(wb.sheetnames))
        if resolved is not None:
            try:
                sheet_idx = list(wb.sheetnames).index(resolved)
            except ValueError:
                sheet_idx = None
    scoped = None
    global_match = None
    ci_scoped = None
    ci_global = None
    target = str(name)
    for defn in items:
        defn_name = str(getattr(defn, "name", "") or "")
        local = getattr(defn, "localSheetId", None)
        exact = defn_name == target
        folded = defn_name.lower() == target.lower()
        if not (exact or folded):
            continue
        if local is None:
            if exact:
                global_match = defn
            elif ci_global is None:
                ci_global = defn
            continue
        if sheet_idx is not None and int(local) == sheet_idx:
            if exact:
                scoped = defn
            elif ci_scoped is None:
                ci_scoped = defn
    return scoped or global_match or ci_scoped or ci_global


def _table_ref_to_rect(
    wb: Any,
    table_ref: TableRef,
    default_sheet: str | None,
) -> RectRef:
    wanted_sheet = table_ref.sheet or default_sheet
    sheets = list(wb.worksheets)
    if wanted_sheet:
        resolved = resolve_sheet_name(wanted_sheet, list(wb.sheetnames))
        if resolved is None:
            raise WorkbookRefBindError(
                f"工作表 '{wanted_sheet}' 不存在。该文件包含: {list(wb.sheetnames)}",
                code=SHEET_NOT_FOUND,
            )
        sheets = [wb[resolved]]
    found: tuple[Any, Any] | None = None
    ci_found: tuple[Any, Any] | None = None
    target = str(table_ref.table)
    for ws in sheets:
        tables = getattr(ws, "tables", None) or {}
        mapping = dict(tables) if not isinstance(tables, dict) else tables
        for table in mapping.values():
            names = {
                str(getattr(table, "displayName", "") or ""),
                str(getattr(table, "name", "") or ""),
            }
            if target in names:
                found = (ws, table)
                break
            if target.lower() in {item.lower() for item in names if item}:
                ci_found = ci_found or (ws, table)
        if found is not None:
            break
    match = found or ci_found
    if match is None:
        raise WorkbookRefBindError(
            f"表对象 {table_ref.table!r} 不存在。正确写法示例：Table1[列] 或 Table1[#All]。",
            code=TABLE_NOT_FOUND,
        )
    ws, table = match
    return _table_object_to_rect(ws, table, table_ref.column)


def _table_object_to_rect(ws: Any, table: Any, column: str | None) -> RectRef:
    raw_ref = str(getattr(table, "ref", "") or "").strip()
    if not raw_ref:
        raise WorkbookRefBindError(
            f"表对象 {getattr(table, 'displayName', '')!r} 没有区域。正确写法示例：Table1[#All]。",
            code=TABLE_NOT_FOUND,
        )
    try:
        base = parse_rect(raw_ref, default_sheet=ws.title)
    except InvalidRefError as exc:
        raise WorkbookRefBindError(str(exc), code=RANGE_INVALID) from exc
    spec = str(column or "#All").strip()
    header_count = int(getattr(table, "headerRowCount", None) or 0)
    totals_count = int(getattr(table, "totalsRowCount", None) or 0)
    min_row, max_row = base.min_row, base.max_row
    min_col, max_col = base.min_col, base.max_col
    lowered = spec.lower()
    if lowered in {"#all"}:
        pass
    elif lowered in {"#data"}:
        min_row = min_row + header_count
        max_row = max_row - totals_count
    elif lowered in {"#headers", "#header"}:
        max_row = min_row + max(header_count, 1) - 1
    elif lowered in {"#totals", "#total"}:
        min_row = max_row - max(totals_count, 1) + 1
    else:
        names = list(getattr(table, "column_names", None) or [])
        index = next((i for i, name in enumerate(names) if name == spec), None)
        if index is None:
            index = next(
                (i for i, name in enumerate(names) if str(name).lower() == spec.lower()),
                None,
            )
        if index is None:
            raise WorkbookRefBindError(
                f"表 {getattr(table, 'displayName', '')!r} 没有列 {spec!r}。"
                "正确写法示例：Table1[列] 或 Table1[#All]。",
                code=TABLE_NOT_FOUND,
            )
        min_col = max_col = base.min_col + index
        min_row = base.min_row + header_count
        max_row = base.max_row - totals_count
    if min_row > max_row:
        min_row = max_row = base.min_row
    if min_col > max_col:
        min_col = max_col = base.min_col
    return RectRef(
        sheet=ws.title,
        min_row=min_row,
        max_row=max_row,
        min_col=min_col,
        max_col=max_col,
    )


def _read_rect_from_ws(
    ws: Any,
    rect: RectRef,
    *,
    used_max_row: int,
    used_max_col: int,
) -> dict[str, Any]:
    from excelmanus.workbook.address import (
        MAX_WHOLE_RANGE_CELLS,
        resolve_range_to_bounds,
    )

    bounds = resolve_range_to_bounds(
        rect.to_a1(include_sheet=False),
        used_max_row=used_max_row,
        used_max_col=used_max_col,
    )
    min_col, min_row, max_col, max_row = (
        bounds.min_col, bounds.min_row, bounds.max_col, bounds.max_row,
    )
    width = max_col - min_col + 1
    rows: list[list[Any]] = []
    for row in ws.iter_rows(
        min_row=min_row, max_row=max_row,
        min_col=min_col, max_col=max_col,
        values_only=True,
    ):
        cells = [_serialize_cell_value(c) for c in row]
        if len(cells) < width:
            cells.extend([None] * (width - len(cells)))
        rows.append(cells[:width])
    payload: dict[str, Any] = {
        "range": bounds.resolved,
        "resolved_range": bounds.resolved,
        "start_row": min_row,
        "end_row": max_row,
        "start_col": min_col,
        "end_col": max_col,
        "rows_count": len(rows),
        "columns_count": width,
        "shape": {"rows": len(rows), "columns": width},
        "data": rows,
        "values": rows,
        "resolved_sheet": ws.title,
        "formulas_uncached": "unknown",
        "source_rows": list(range(min_row, max_row + 1)),
        "source_cols": list(range(min_col, max_col + 1)),
    }
    if bounds.requested != bounds.resolved:
        payload["requested_range"] = bounds.requested
    if bounds.clipped:
        payload["clipped_to_used_range"] = True
    if bounds.truncated:
        payload["is_truncated"] = True
        payload["total_rows_in_sheet"] = used_max_row
        payload["truncation_note"] = (
            f"整行/整列已裁到 {bounds.resolved}（上限 {MAX_WHOLE_RANGE_CELLS} 格），不是全轴事实。"
        )
    return payload


def _read_formula_grid(ws: Any, part: dict[str, Any]) -> list[list[Any]]:
    min_row = int(part["start_row"])
    max_row = int(part["end_row"])
    min_col = int(part["start_col"])
    max_col = int(part["end_col"])
    width = max_col - min_col + 1
    formula_rows: list[list[Any]] = []
    for row in ws.iter_rows(
        min_row=min_row, max_row=max_row,
        min_col=min_col, max_col=max_col,
        values_only=True,
    ):
        cells: list[Any] = []
        for raw in row:
            if isinstance(raw, str) and raw.startswith("="):
                cells.append(raw)
            else:
                cells.append(None)
        if len(cells) < width:
            cells.extend([None] * (width - len(cells)))
        formula_rows.append(cells[:width])
    return formula_rows


def _merge_range_parts(parts: list[dict[str, Any]], *, requested: str) -> dict[str, Any]:
    if not parts:
        raise InvalidRefError("引用没有区域。正确写法示例：A1 或 A1:B2,C3:D4。")
    if len(parts) == 1:
        payload = dict(parts[0])
        payload.setdefault("requested_range", requested)
        return payload
    sheets = []
    for part in parts:
        title = part.get("resolved_sheet")
        if title and title not in sheets:
            sheets.append(title)
    payload: dict[str, Any] = {
        "range": ",".join(str(part.get("range") or "") for part in parts),
        "resolved_range": ",".join(str(part.get("resolved_range") or part.get("range") or "") for part in parts),
        "requested_range": requested,
        "areas": parts,
        "data": [part.get("data") for part in parts],
        "values": [part.get("values") for part in parts],
        "start_row": parts[0].get("start_row"),
        "end_row": parts[-1].get("end_row"),
        "rows_count": sum(int(part.get("rows_count") or 0) for part in parts),
        "columns_count": sum(int(part.get("columns_count") or 0) for part in parts),
        "shape": {
            "rows": sum(int((part.get("shape") or {}).get("rows") or 0) for part in parts),
            "columns": max(int((part.get("shape") or {}).get("columns") or 0) for part in parts),
        },
        "resolved_sheet": sheets[0] if len(sheets) == 1 else None,
        "resolved_sheets": sheets,
        "formulas_uncached": "unknown",
        "source_rows": [row for part in parts for row in (part.get("source_rows") or [])],
        "clipped_to_used_range": any(part.get("clipped_to_used_range") for part in parts),
        "is_truncated": any(part.get("is_truncated") for part in parts),
    }
    notes = [str(part["truncation_note"]) for part in parts if part.get("truncation_note")]
    if notes:
        payload["truncation_note"] = " ".join(notes)
    return payload


def _trim_trailing_nulls_generic(row: list[Any]) -> list[Any]:
    """裁剪尾部空值，减少噪音列影响。"""
    end = len(row)
    while end > 0 and row[end - 1] is None:
        end -= 1
    return row[:end]


def _looks_like_title_row(first_cell: Any) -> bool:
    """判断首单元格是否更像标题而非字段名。"""
    if not isinstance(first_cell, str):
        return False
    text = first_cell.strip()
    if not text:
        return False
    if any(token in text for token in ("──", "——", "年度", "季度")):
        return True
    return any(hint in text for hint in _TITLE_HINT_PREFIXES)


def _is_form_type_document(
    safe_path: Any,
    sheet_name: str | None,
    max_scan: int = _HEADER_SCAN_ROWS,
) -> tuple[bool, str]:
    """检测是否为表单类文档（如收据、模板等非标准数据表格）。

    表单类文档特征：
    1. 大量标签-值对（标签行占比高）
    2. 大量合并单元格
    3. 缺少标准列头

    Returns:
        (is_form_document, reason) 元组。
    """
    try:
        from openpyxl import load_workbook
        wb = load_workbook(safe_path, read_only=False, data_only=True)
    except Exception:
        return False, ""

    try:
        if sheet_name:
            resolved = resolve_sheet_name(sheet_name, wb.sheetnames)
            ws = wb[resolved] if resolved else wb.active
        else:
            ws = wb.active
        if ws is None:
            return False, ""

        max_row = min(max_scan, ws.max_row or max_scan)
        max_col = ws.max_column or 10

        # 统计合并单元格占比
        total_cells = max_row * max_col
        merged_cells = 0
        for merged_range in ws.merged_cells.ranges:
            merged_cells += (merged_range.max_row - merged_range.min_row + 1) * \
                           (merged_range.max_col - merged_range.min_col + 1)

        merged_ratio = merged_cells / max(total_cells, 1)

        # 统计标签行（包含表单标签关键词的非空行）占比
        label_rows = 0
        total_scannable_rows = 0

        for row in ws.iter_rows(min_row=1, max_row=max_scan, min_col=1, max_col=max_col, values_only=True):
            row_values = [_normalize_cell(c) for c in row]
            non_empty = [v for v in row_values if v is not None]

            if len(non_empty) >= 2:  # 至少2个非空单元格才计入
                total_scannable_rows += 1
                # 检查是否包含表单标签关键词（精确匹配单元格值，避免数据行误判）
                cell_texts = {str(v).strip() for v in non_empty if isinstance(v, str)}
                if any(ct in _FORM_LABEL_KEYWORDS for ct in cell_texts):
                    label_rows += 1

        label_ratio = label_rows / max(total_scannable_rows, 1)

        # 判断逻辑
        if merged_ratio > _FORM_MERGED_CELL_RATIO_THRESHOLD:
            return True, f"合并单元格占比 {merged_ratio:.1%} 超过阈值 {_FORM_MERGED_CELL_RATIO_THRESHOLD:.1%}"

        if label_ratio > _FORM_LABEL_RATIO_THRESHOLD:
            return True, f"表单标签行占比 {label_ratio:.1%} 超过阈值 {_FORM_LABEL_RATIO_THRESHOLD:.1%}"

        return False, ""
    finally:
        wb.close()


def _workbook_may_have_merged_cells(safe_path: Any) -> bool:
    """Cheaply detect merge metadata without materializing every worksheet cell.

    ``openpyxl.load_workbook(read_only=False)`` is disproportionately expensive
    for ordinary workbooks because it binds every cell before callers can inspect
    ``merged_cells``.  The worksheet XML already contains a small ``mergeCell``
    marker, so use the ZIP package as a conservative preflight.  Returning
    ``True`` on any parsing error preserves the old full-scan behaviour.
    """
    from pathlib import Path
    from zipfile import BadZipFile, ZipFile

    try:
        path = Path(safe_path)
        with ZipFile(path) as archive:
            for name in archive.namelist():
                if not name.startswith("xl/worksheets/") or not name.endswith(".xml"):
                    continue
                # Avoid parsing XML: this is only a conservative presence check.
                if b"<mergeCell" in archive.read(name):
                    return True
        return False
    except (BadZipFile, OSError, ValueError):
        return True


def _header_row_score(
    row_values: list[Any],
    row_idx0: int,
    next_row_values: list[Any] | None = None,
) -> float:
    """计算候选表头行分数。分数越高越可能是表头。"""
    non_empty = [v for v in row_values if v is not None]
    if len(non_empty) < _HEADER_MIN_NON_EMPTY:
        return float("-inf")

    text_values = [str(v).strip() for v in non_empty if isinstance(v, str) and str(v).strip()]
    numeric_count = sum(1 for v in non_empty if isinstance(v, (int, float)))
    date_count = sum(1 for v in non_empty if _is_date_like(v))
    string_count = len(text_values)
    unique_ratio = len(set(map(str, non_empty))) / max(len(non_empty), 1)
    keyword_hits = sum(
        1
        for text in text_values
        if any(k in text for k in _HEADER_KEYWORDS)
    )

    score = 0.0
    score += len(non_empty) * 2.0
    score += string_count * 1.6
    score -= numeric_count * 1.4
    score -= date_count * 1.0
    score += unique_ratio * 2.5
    score += keyword_hits * 2.8
    score -= row_idx0 * 0.03  # 轻微偏好靠前行

    first_cell = row_values[0] if row_values else None
    if _looks_like_title_row(first_cell):
        score -= 6.0

    if next_row_values is not None:
        next_non_empty = [v for v in next_row_values if v is not None]
        if next_non_empty:
            next_numeric_ratio = sum(1 for v in next_non_empty if isinstance(v, (int, float))) / len(next_non_empty)
            # 表头下一行常见“数据占比更高”
            score += next_numeric_ratio * 1.8
    return score


def _best_header_row_with_score(
    rows: list[list[Any]],
    *,
    max_scan: int | None = None,
    skip_rows: set[int] | None = None,
) -> tuple[int | None, float]:
    """返回 (最佳 header 行号 0-indexed, 分数)；无候选时 (None, -inf)。"""
    if not rows:
        return None, float("-inf")

    upper = len(rows) if max_scan is None else min(len(rows), max_scan)
    best_row: int | None = None
    best_score = float("-inf")

    for idx in range(upper):
        if skip_rows and idx in skip_rows:
            continue
        row = _trim_trailing_nulls_generic(rows[idx])
        next_row = _trim_trailing_nulls_generic(rows[idx + 1]) if idx + 1 < upper else None
        score = _header_row_score(row, idx, next_row)
        if score > best_score:
            best_score = score
            best_row = idx

    if best_row is None or best_score == float("-inf"):
        return None, float("-inf")
    return best_row, best_score


def _guess_header_row_from_rows(rows: list[list[Any]], *, max_scan: int | None = None, skip_rows: set[int] | None = None) -> int | None:
    """基于抽样行猜测 header 行号（0-indexed）。"""
    return _best_header_row_with_score(
        rows, max_scan=max_scan, skip_rows=skip_rows,
    )[0]

def _detect_header_row(
    safe_path: Any,
    sheet_name: str | None,
    max_scan: int = _HEADER_SCAN_ROWS,
    max_scan_columns: int = _HEADER_SCAN_COLS,
) -> int | None:
    """启发式检测 header 行号（0-indexed）。

    策略：
    1. 首先检测是否为表单类文档（收据、模板等），如果是则返回 -1 表示无需 header
    2. 扫描前 N 行（默认 30）和前 M 列（默认 200）；
    3. 对每一行按"文本占比、关键字、唯一性、数据行特征"打分；
    4. 选择分数最高者作为表头。

    Returns:
        检测到的 header 行号（从0开始），无法确定时返回 None。
        返回 -1 表示检测为表单类文档，不应使用 header。
    """
    try:
        from openpyxl import load_workbook
        # Header detection only needs the first few rows.  The previous full
        # workbook load bound every cell before looking at those rows, making a
        # ``read_excel(max_rows=50)`` call scan the entire file.  Read-only mode
        # keeps the normal data-table path proportional to the header window.
        wb = load_workbook(safe_path, read_only=True, data_only=True)
    except Exception:
        return None

    try:
        if sheet_name:
            resolved = resolve_sheet_name(sheet_name, wb.sheetnames)
            if resolved:
                ws = wb[resolved]
            else:
                ws = wb.active
        else:
            ws = wb.active
        if ws is None:
            return None

        # ReadOnlyWorksheet does not expose merged_cells.  Ambiguous/form
        # documents still fall back to _is_form_type_document below, which
        # retains the full merge-aware check; the common confident-header path
        # never needs to materialize the workbook.
        scan_cols = max(1, min(max_scan_columns, ws.max_column or max_scan_columns))
        wide_merged_rows: set[int] = set()
        merged_cells = getattr(ws, "merged_cells", None)
        if merged_cells is not None:
            for merged_range in merged_cells.ranges:
                col_span = merged_range.max_col - merged_range.min_col + 1
                if col_span > scan_cols * 0.5:
                    for r in range(merged_range.min_row, merged_range.max_row + 1):
                        if r <= max_scan:
                            wide_merged_rows.add(r - 1)  # 转为 0-indexed

        rows: list[list[Any]] = []
        for row in ws.iter_rows(
            min_row=1,
            max_row=max_scan,
            min_col=1,
            max_col=scan_cols,
            values_only=True,
        ):
            rows.append([_normalize_cell(c) for c in row])

        if not rows:
            return None

        best_row, best_score = _best_header_row_with_score(
            rows, max_scan=max_scan, skip_rows=wide_merged_rows,
        )
        # 明确的数据表头优先于“表单类”判定：花名册/联系人表/登记表等
        # 正常数据表的表头行本就含文本列名，不应因标签词命中而退化为 header=None。
        if best_row is not None and best_score >= _HEADER_CONFIDENT_SCORE:
            return best_row

        is_form, reason = _is_form_type_document(safe_path, sheet_name, max_scan)
        if is_form:
            logger.info("检测为表单类文档：%s", reason)
            return -1  # 特殊标记：表单类文档，不使用 header
        return best_row
    finally:
        wb.close()


def _build_read_kwargs(
    safe_path: Any,
    sheet_name: str | None,
    max_rows: int | None = None,
    header_row: int | None = None,
) -> dict[str, Any]:
    """构建 pd.read_excel 的公共参数，统一处理 header_row。

    Args:
        safe_path: 已校验的文件路径。
        sheet_name: 工作表名称。
        max_rows: 最大读取行数。
        header_row: 列头所在行号（**Excel 行号，1-based，第 1 行 = 1**），默认自动检测。
            不传此参数时工具会启发式检测真正的表头行；
            仅在自动检测不准确时才显式指定。
            特殊值 -1 表示表单类文档，不使用 header（header=None）。

    Returns:
        可直接传给 pd.read_excel 的关键字参数字典。
        包含特殊键 "_form_type_document" 表示是否被检测为表单类文档。
    """
    kwargs: dict[str, Any] = {"io": safe_path, "dtype": str, "keep_default_na": False, "na_values": [""]}
    if sheet_name is not None:
        kwargs["sheet_name"] = sheet_name
    if max_rows is not None:
        kwargs["nrows"] = max_rows
    if header_row is not None:
        # header_row=-1 表示表单类文档，不使用 header
        if header_row == -1:
            kwargs["header"] = None
            kwargs["_form_type_document"] = True
            logger.info("检测为表单类文档，使用 header=None 读取 (sheet=%s)", sheet_name)
        elif header_row < 1:
            raise ValueError(
                "header_row 使用 Excel 行号（1-based，第 1 行 = 1）；"
                "表单类文档请传 -1，自动检测请省略该参数。"
            )
        else:
            # 模型面统一 Excel 行号（1-based）；pandas header 需要 0-based
            kwargs["header"] = header_row - 1
    else:
        # 启发式自动检测 header 行（仅当用户未显式指定时）
        detected = _detect_header_row(safe_path, sheet_name)
        if detected is not None:
            if detected == -1:
                # 表单类文档，不使用 header
                kwargs["header"] = None
                kwargs["_form_type_document"] = True
                logger.info("自动检测为表单类文档，使用 header=None 读取 (sheet=%s)", sheet_name)
            elif detected > 0:
                kwargs["header"] = detected
                logger.info("自动检测 header_row=%d (sheet=%s)", detected + 1, sheet_name)
    return kwargs


def _get_sheet_total_rows(safe_path: Any, sheet_name: str | None) -> int | None:
    """快速获取 sheet/CSV 总行数（不加载全部数据）。"""
    if _is_csv_file(safe_path):
        try:
            with open(safe_path, encoding="utf-8", errors="replace") as f:
                return max(sum(1 for _ in f) - 1, 0)  # 减去 header 行
        except Exception:
            return None
    try:
        from openpyxl import load_workbook
        wb = load_workbook(safe_path, read_only=True, data_only=True)
        try:
            from excelmanus.workbook.snapshot import require_default_sheet

            title = require_default_sheet(list(wb.sheetnames), sheet_name)
            return wb[title].max_row or 0
        finally:
            wb.close()
    except Exception:
        return None


def _detect_header_row_csv(
    safe_path: Any,
    max_scan: int = _HEADER_SCAN_ROWS,
) -> int | None:
    """启发式检测 CSV/TSV 文件的 header 行号（0-indexed）。

    读取前 max_scan 行为原始文本行，转为 list[list[Any]]，
    复用 _guess_header_row_from_rows 打分逻辑。分隔符与正式读相同。
    """
    from excelmanus.workbook.snapshot import csv_separator_for

    try:
        df_raw = pd.read_csv(
            safe_path,
            header=None,
            nrows=max_scan,
            dtype=str,
            sep=csv_separator_for(safe_path),
            encoding=_detect_csv_encoding(safe_path),
        )
    except Exception:
        return None
    if df_raw.empty:
        return None
    rows: list[list[Any]] = []
    for _, row in df_raw.iterrows():
        rows.append([_normalize_cell(v) if pd.notna(v) else None for v in row])
    return _guess_header_row_from_rows(rows, max_scan=max_scan)


def _public_header_row_to_internal(header_row: int | None) -> int | None:
    """公共 Excel 1-based header_row → 内部 0-based；None 自动检测，-1 表单类保持。"""
    if header_row is None or header_row == -1:
        return header_row
    if header_row < 1:
        raise ValueError(
            "header_row 使用 Excel 行号（1-based，第 1 行 = 1）；"
            "表单类文档请传 -1，自动检测请省略该参数。"
        )
    return header_row - 1


def _to_public_header_row(effective_header: int) -> int | str:
    """内部 0-based header 索引 → 公共 Excel 1-based；-1 为表单类。"""
    if effective_header == -1:
        return "form_type_document"
    return int(effective_header) + 1


def _excel_source_row(effective_header: int, data_row_index: Any) -> int:
    """Excel 1-based 源行 = (pandas header 索引 + 1) + (数据行 index + 1)。"""
    return (int(effective_header) + 1) + (int(data_row_index) + 1)


_CSV_ENCODING_CANDIDATES: tuple[str, ...] = ("utf-8-sig", "utf-8", "gb18030", "utf-16")


def _detect_csv_encoding(safe_path: Any) -> str:
    """按字节样本探测 CSV 编码：BOM 优先，其次候选编码首个能解者。

    只读前 64KB；全 ASCII 的文件返回 utf-8（与任何候选等价）。
    """
    from pathlib import Path

    p = Path(safe_path) if not isinstance(safe_path, Path) else safe_path
    try:
        sample = p.open("rb").read(65536)
    except OSError:
        return "utf-8"
    if sample.startswith(b"\xff\xfe") or sample.startswith(b"\xfe\xff"):
        return "utf-16"
    if sample.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    for enc in _CSV_ENCODING_CANDIDATES:
        try:
            sample.decode(enc)
            return enc
        except (UnicodeDecodeError, ValueError):
            continue
    return "utf-8"


def _read_csv_df(
    safe_path: Any,
    max_rows: int | None = None,
    header_row: int | None = None,
    encoding: str | None = None,
) -> tuple[pd.DataFrame, int]:
    """读取 CSV/TSV 文件为 DataFrame，含 header 自动检测。

    ``header_row`` 为内部 0-based pandas 索引；-1 表示表单类（header=None）。
    ``encoding`` 缺省时按字节样本自动探测（gb18030 兼容 GBK）。
    """
    from pathlib import Path

    from excelmanus.workbook.snapshot import csv_separator_for

    p = Path(safe_path) if not isinstance(safe_path, Path) else safe_path
    sep = csv_separator_for(p)
    kwargs: dict[str, Any] = {
        "filepath_or_buffer": safe_path,
        "sep": sep,
        "dtype": str,
        "keep_default_na": False,
        "na_values": [""],
        "encoding": encoding or _detect_csv_encoding(p),
    }

    # header 自动检测（仅当用户未显式指定时）
    if header_row is None:
        detected = _detect_header_row_csv(safe_path)
        if detected is not None and detected > 0:
            header_row = detected
            logger.info("CSV 自动检测 header_row=%d", detected)

    if header_row == -1:
        kwargs["header"] = None
        effective_header = -1
    elif header_row is not None:
        kwargs["header"] = header_row
        effective_header = header_row
    else:
        effective_header = 0
    if max_rows is not None:
        kwargs["nrows"] = max_rows
    df = pd.read_csv(**kwargs)
    return df, effective_header


def _read_df(
    safe_path: Any,
    sheet_name: str | None,
    max_rows: int | None = None,
    header_row: int | None = None,
) -> tuple[pd.DataFrame, int]:
    """统一读取 Excel/CSV 为 DataFrame，含 header 自动检测。

    ``header_row`` 为公共 Excel 1-based 行号。返回的 ``effective_header``
    仍是内部 0-based pandas header 索引（-1 表示表单类文档）。

    当自动检测的 header_row 导致超过 50% 列名为 Unnamed 时，
    自动向下尝试最多 5 行寻找更合理的表头。

    当检测为表单类文档时（header_row=-1），使用 header=None 读取全部数据，
    不执行 Unnamed 回退逻辑。不在读路径上猜测求值公式列。
    """
    # CSV/TSV：公共 1-based 在此换成内部 0-based，再交给 _read_csv_df
    if _is_csv_file(safe_path):
        return _read_csv_df(
            safe_path,
            max_rows=max_rows,
            header_row=_public_header_row_to_internal(header_row),
        )

    kwargs = _build_read_kwargs(safe_path, sheet_name, max_rows=max_rows, header_row=header_row)
    # 注意=None 时 kwargs.get("header") 返回 None，不是 0
    # 我们需要区分：用户指定 header=None（不使用header）和表单类文档（header=None）
    # 通过检查 kwargs 中是否有特殊的标记来区分
    effective_header = kwargs.get("header")
    if effective_header is None:
        # header=None 可能是用户指定，也可能是表单类文档
        # 需要检查是否是被自动检测为表单类文档
        if kwargs.get("_form_type_document"):
            effective_header = -1
        else:
            effective_header = 0  # 用户显式指定 header=None，使用默认值
    # 移除内部标记，避免传给 pd.read_excel
    kwargs.pop("_form_type_document", None)
    df = pd.read_excel(**kwargs)

    # 表单类文档（header=-1）不使用 Unnamed 回退逻辑
    # 仅在自动检测模式下（用户未显式指定 header_row）执行 Unnamed 回退
    if header_row is None and effective_header != -1:
        unnamed_ratio = (
            sum(1 for c in df.columns if str(c).startswith("Unnamed"))
            / max(len(df.columns), 1)
        )
        if unnamed_ratio > 0.5:
            logger.info(
                "自动检测 header_row=%d 产生 %.0f%% Unnamed 列名，尝试回退",
                effective_header, unnamed_ratio * 100,
            )
            for try_header in range(effective_header + 1, min(effective_header + 6, 30)):
                retry_kwargs = {**kwargs, "header": try_header}
                try:
                    df_retry = pd.read_excel(**retry_kwargs)
                except Exception:
                    break
                if df_retry.empty:
                    break
                retry_unnamed = sum(
                    1 for c in df_retry.columns if str(c).startswith("Unnamed")
                )
                if retry_unnamed / max(len(df_retry.columns), 1) < 0.3:
                    logger.info("回退成功：header_row=%d → %d", effective_header, try_header)
                    df = df_retry
                    effective_header = try_header
                    break

    return df, effective_header


# ── 工具函数 ──────────────────────────────────────────────


def _workspace_rel_path(guard: FileAccessGuard, safe_path: Any, user_path: str) -> str:
    try:
        from pathlib import Path

        return str(Path(safe_path).relative_to(guard.workspace_root))
    except Exception:
        return user_path


def _error_payload_result(
    payload: dict[str, Any],
    *,
    code: str = "EXECUTION_FAILED",
) -> ToolResult:
    message = str(payload.get("error") or payload.get("message") or "tool failed")
    extra = {
        key: value
        for key, value in payload.items()
        if key not in {"error", "code", "status", "message", "error_code"}
    }
    return error_result(message, code=code, fields=extra or None)


_RANGE_PROJECT_MAX_ROWS = 40
_RANGE_PROJECT_MAX_COLS = 20
_RANGE_PROJECT_MAX_CHARS = 2800
_SMALL_RANGE_FORMULA_CELLS = 400


def _is_nested_grids(values: Any) -> bool:
    return (
        isinstance(values, list)
        and bool(values)
        and isinstance(values[0], list)
        and bool(values[0])
        and isinstance(values[0][0], list)
    )


def _clip_value_window(values: Any, max_rows: int, max_cols: int) -> Any:
    if not isinstance(values, list):
        return values
    if _is_nested_grids(values):
        return [_clip_value_window(grid, max_rows, max_cols) for grid in values]
    clipped: list[Any] = []
    for row in values[:max_rows]:
        if isinstance(row, list):
            clipped.append(row[:max_cols])
        else:
            clipped.append(row)
    return clipped


def _value_window_truncated(values: Any, max_rows: int, max_cols: int) -> bool:
    if not isinstance(values, list):
        return False
    if _is_nested_grids(values):
        return any(_value_window_truncated(grid, max_rows, max_cols) for grid in values)
    if len(values) > max_rows:
        return True
    return any(isinstance(row, list) and len(row) > max_cols for row in values)


def _formula_items_for_projection(
    formulas: Any,
    *,
    col0: int | None = None,
    row0: int | None = None,
    max_items: int = 80,
) -> list[dict[str, Any]]:
    from openpyxl.utils.cell import get_column_letter

    items: list[dict[str, Any]] = []
    if not isinstance(formulas, list) or not formulas:
        return items
    if _is_nested_grids(formulas):
        for grid in formulas:
            remain = max_items - len(items)
            if remain <= 0:
                break
            items.extend(
                _formula_items_for_projection(
                    grid, col0=None, row0=None, max_items=remain,
                )
            )
        return items[:max_items]
    for ri, frow in enumerate(formulas):
        if not isinstance(frow, list):
            continue
        for ci, formula in enumerate(frow):
            if not formula:
                continue
            item: dict[str, Any] = {"formula": formula}
            if col0 is not None and row0 is not None:
                item["cell"] = f"{get_column_letter(col0 + ci)}{row0 + ri}"
            items.append(item)
            if len(items) >= max_items:
                return items
    return items


def _append_range_projection(
    lines: list[str],
    summary: dict[str, Any],
    *,
    complete: bool,
) -> None:
    areas = summary.get("areas")
    if isinstance(areas, list) and len(areas) > 1:
        for part in areas:
            if not isinstance(part, dict):
                continue
            sheet = str(part.get("resolved_sheet") or "").strip()
            rng = str(part.get("resolved_range") or part.get("range") or "").strip()
            label = f"{sheet} [{rng}]" if sheet else f"[{rng}]"
            lines.append(label)
            _append_range_projection(lines, part, complete=complete)
        return
    values = summary.get("values")
    if not isinstance(values, list) or not values:
        return
    origin: tuple[int, int] | None = None
    try:
        from openpyxl.utils.cell import range_boundaries

        from excelmanus.workbook.address import parse_sheet_address

        local_range = parse_sheet_address(
            str(summary.get("resolved_range") or summary["range"])
        ).address
        col0, row0, _, _ = range_boundaries(local_range)
        origin = (col0, row0)
    except (ValueError, TypeError, AttributeError, KeyError):
        origin = None

    formulas = summary.get("formulas") or summary.get("formula_grid") or []
    items = _formula_items_for_projection(
        formulas,
        col0=origin[0] if origin else None,
        row0=origin[1] if origin else None,
    )
    if items:
        lines.append("formulas: " + json.dumps(items, ensure_ascii=False, default=str))

    max_rows = _RANGE_PROJECT_MAX_ROWS if complete else 3
    max_cols = _RANGE_PROJECT_MAX_COLS if complete else 8
    window = _clip_value_window(values, max_rows, max_cols)
    dumped = json.dumps(window, ensure_ascii=False, default=str)
    while len(dumped) > _RANGE_PROJECT_MAX_CHARS and (max_rows > 3 or max_cols > 4):
        max_rows = max(3, max_rows // 2)
        max_cols = max(4, max_cols // 2)
        window = _clip_value_window(values, max_rows, max_cols)
        dumped = json.dumps(window, ensure_ascii=False, default=str)
    truncated = _value_window_truncated(values, max_rows, max_cols)
    label = "values" if not truncated else f"values（投影 {max_rows} 行 × {max_cols} 列）"
    lines.append(f"{label}: {dumped}")
    if truncated:
        # 全量出口：投影丢弃的行不丢数据——外置到 spill 仓并给句柄，
        # 模型把句柄当 file_path 传给 inspect_spreadsheet 即可取回全量。
        try:
            from excelmanus.engine_core.spill import SpillStore

            full_text = json.dumps(values, ensure_ascii=False, default=str)
            locator = SpillStore(_get_guard().workspace_root).put(full_text)
            summary["spill"] = str(locator)
            lines.append(
                f"全量 values 已外置 {locator}：该句柄不是磁盘路径，把句柄本身当 file_path 传给 "
                "inspect_spreadsheet 即可取回全部行列；不要再对原工作簿分段分页重读。"
            )
        except Exception:
            logger.debug("range 全量 spill 失败，仅保留投影", exc_info=True)


def _finalize_read_excel_result(
    summary: dict[str, Any],
    *,
    rel_path: str,
    sheet_name: str | None,
    sample_rows: int | None = None,
    file_path: str | None = None,
    content_version: str | None = None,
    header_row: Any = None,
    snapshot: Any = None,
) -> ToolResult:
    if summary.get("error") and "shape" not in summary and "columns" not in summary:
        return error_result(
            str(summary.get("error")),
            code=str(summary.get("error_code") or summary.get("code") or "EXECUTION_FAILED"),
            fields={k: v for k, v in summary.items() if k not in {"error", "code", "error_code", "status", "message"}},
        )

    shape = summary.get("shape") or {}
    rows = int(shape.get("rows") or 0)
    cols = int(shape.get("columns") or 0)
    is_truncated = bool(summary.get("is_truncated"))
    has_sample = "sample_preview" in summary
    if is_truncated:
        coverage_kind = "truncated"
    elif has_sample:
        coverage_kind = "sampled"
    elif summary.get("range"):
        coverage_kind = "complete"
    else:
        coverage_kind = "complete"

    total_rows = summary.get("total_rows_in_sheet", rows)
    coverage: dict[str, Any] = {
        "kind": coverage_kind,
        "rows": rows,
        "cols": cols,
    }
    if summary.get("total_rows_in_sheet") is not None:
        coverage["total_rows_in_sheet"] = total_rows

    columns = summary.get("columns") or []
    preview = summary.get("preview")
    if preview is None and isinstance(summary.get("data"), list):
        preview = summary.get("data")

    col_preview = ", ".join(str(c) for c in columns[:8])
    if len(columns) > 8:
        col_preview = f"{col_preview} …(+{len(columns) - 8})"

    sheets = [str(item) for item in (summary.get("resolved_sheets") or []) if str(item).strip()]
    areas = summary.get("areas")
    range_text = str(summary.get("requested_range") or summary.get("range") or "").strip()
    if isinstance(areas, list) and len(areas) > 1:
        names = "、".join(sheets) if sheets else "多表"
        sheet_part = f" / {names}"
        if range_text:
            sheet_part = f"{sheet_part} [{range_text}]"
        size_part = f"{len(areas)} 个区域"
    else:
        sheet_part = f" / {sheet_name}" if sheet_name else ""
        if summary.get("range"):
            sheet_part = f"{sheet_part} [{summary['range']}]" if sheet_part else f"[{summary['range']}]"
        size_part = f"{rows} 行 × {cols} 列"

    lines = [
        f"文件 {summary.get('file', rel_path)}{sheet_part}：{size_part}。",
    ]
    if col_preview:
        lines.append(f"列：{col_preview}")
    if is_truncated:
        lines.append(
            f"⚠️ 截断样本：工作表共 {total_rows} 行，仅返回 {rows} 行；不可当作全表事实。"
        )
    elif has_sample:
        lines.append(f"⚠️ 等距采样预览（{summary.get('sample_note', '')}）。")
    if summary.get("truncation_note"):
        lines.append(str(summary["truncation_note"]))
    warns = summary.get("warnings")
    if isinstance(warns, list):
        for item in warns:
            lines.append(f"⚠️ {item}")
    if isinstance(preview, list) and preview and not summary.get("range"):
        sample_n = min(3, len(preview))
        lines.append(
            f"前 {sample_n} 行样本："
            f"{json.dumps(preview[:sample_n], ensure_ascii=False, default=str)}"
        )
    if summary.get("include_warning"):
        lines.append(f"⚠️ {summary['include_warning']}")
    if summary.get("csv_unsupported_dimensions"):
        lines.append(f"⚠️ {summary['csv_unsupported_dimensions']}")
    if summary.get("detected_form_type"):
        lines.append("版式表单，质量信号按数据框不适用。")
    merged_summary = summary.get("merged_cell_summary")
    if merged_summary:
        lines.append(f"合并摘要：{merged_summary}")

    ui_preview: dict[str, Any] | None = None
    if columns and isinstance(preview, list) and preview:
        rows_data: list[list[Any]] = []
        for record in preview[:50]:
            if isinstance(record, dict):
                rows_data.append([record.get(c) for c in columns])
            elif isinstance(record, list):
                rows_data.append(record)
        ui_preview = {
            "columns": columns,
            "preview": preview[:50],
            "rows": rows_data[:50],
            "total_rows": int(total_rows) if total_rows else rows,
            "truncated": is_truncated,
            "sheet": sheet_name or "",
        }

    if snapshot is not None:
        content_version = snapshot.content_version
        rel_path = snapshot.file.relative
    if content_version:
        summary["content_version"] = content_version
    if rel_path:
        summary["file_path"] = rel_path
    if sheet_name:
        summary.setdefault("resolved_sheet", sheet_name)

    kind = "range" if summary.get("range") else "overview"
    public_header = header_row
    if public_header is None:
        public_header = summary.get("detected_header_row")
    if snapshot is not None:
        from excelmanus.workbook.snapshot import Coverage, apply_read_contract, selection_from_rows

        source_rows = summary.get("source_rows")
        sampled = bool(sample_rows) or bool(summary.get("sample_preview"))
        truncated = is_truncated
        cov = Coverage(
            kind="sampled" if sampled and not truncated else ("truncated" if truncated else "complete"),
            returned_rows=int(summary.get("shape", {}).get("rows") or len(summary.get("values") or summary.get("preview") or [])),
            total_rows=int(total_rows) if total_rows else None,
            offset=int(summary.get("offset") or 0),
            truncated_reason="max_rows" if truncated else None,
        )
        apply_read_contract(
            summary,
            snapshot=snapshot,
            result_kind="areas" if summary.get("areas") else ("matrix" if kind == "range" else "records"),
            sheet=sheet_name,
            header_row=public_header,
            source_rows=source_rows,
            source_cols=summary.get("source_cols"),
            coverage=cov,
            formulas_uncached=summary.get("formulas_uncached", "unknown"),
            selection=selection_from_rows(
                snapshot,
                sheet=str(sheet_name or ""),
                rows=source_rows or [],
                cols=summary.get("source_cols"),
                origin="range" if kind == "range" else "read",
                header_row=public_header,
            ) if source_rows and not summary.get("areas") else None,
            meta_kind=kind,
        )
        if summary.get("areas"):
            for area in summary["areas"]:
                area["selection"] = selection_from_rows(
                    snapshot, sheet=area["resolved_sheet"], rows=area["source_rows"],
                    cols=area["source_cols"], origin="range", header_row=public_header,
                ).to_json()
    elif snapshot is None:
        raise RuntimeError("read finalize 必须提供 WorkbookSnapshot")
    if sample_rows:
        summary["meta"]["sampled"] = True

    selection_hint = _selection_model_hint(summary)
    if selection_hint:
        lines.append(selection_hint)

    if summary["meta"].get("formulas_uncached"):
        lines.append("缓存值未保证重算；null 可能是无公式缓存，不能据此判断为空白格。")
    if kind == "range":
        _append_range_projection(
            lines, summary, complete=(coverage_kind == "complete"),
        )

    return ToolResult(
        success=True,
        model_text="\n".join(lines),
        value=summary,
        ui_meta=ToolUiMeta(
            files=[rel_path],
            preview=ui_preview,
            content_version=content_version,
        ),
        truncated=is_truncated,
        coverage=coverage,
    )


def _finalize_compare_excel_result(
    result: dict[str, Any],
    *,
    rel_path_a: str,
    rel_path_b: str,
    sheet_a: str,
    sheet_b: str,
    key_columns: list[str] | None,
    alignment: str,
    scope: str = "explicit_sheets",
) -> ToolResult:
    if result.get("error"):
        extra = {k: v for k, v in result.items() if k not in {"error", "code"}}
        return error_result(
            str(result["error"]),
            code=str(result.get("code") or result.get("error_code") or "EXECUTION_FAILED"),
            fields=extra or None,
        )

    summary = result.get("summary") or {}
    sample_diffs = result.get("sample_diffs") or []
    truncated = bool(result.get("truncated"))
    diff_mode = result.get("diff_mode", "cross_file")

    value = {
        **result,
        "status": "success",
        "alignment": alignment,
        "key_columns": key_columns or [],
        "diff_mode": diff_mode,
        "file_a": result.get("file_a"),
        "file_b": result.get("file_b"),
        "sheet_a": result.get("sheet_a"),
        "sheet_b": result.get("sheet_b"),
        "summary": summary,
        "sample_diffs": sample_diffs,
        "truncated": truncated,
        "hint": result.get("hint"),
        "duplicate_keys_a": result.get("duplicate_keys_a", []),
        "duplicate_keys_b": result.get("duplicate_keys_b", []),
        "unmatched_in_a": result.get("unmatched_in_a", []),
        "unmatched_in_b": result.get("unmatched_in_b", []),
        "coverage": result.get("coverage", {}),
    }

    cells_diff = summary.get("cells_different", 0)
    unmatched_a = [str(k) for k in (result.get("unmatched_in_a") or []) if str(k).strip()]
    unmatched_b = [str(k) for k in (result.get("unmatched_in_b") or []) if str(k).strip()]
    dups_a = [str(k) for k in (result.get("duplicate_keys_a") or []) if str(k).strip()]
    dups_b = [str(k) for k in (result.get("duplicate_keys_b") or []) if str(k).strip()]

    def _key_list(items: list[str], *, cap: int = 12) -> str:
        shown = items[:cap]
        extra = f" 等{len(items)}个" if len(items) > cap else ""
        return "、".join(shown) + extra

    lines = [
        f"对比 {rel_path_a} vs {rel_path_b}（{diff_mode}，对齐={alignment}）。",
        (
            f"工作表范围：{sheet_a} vs {sheet_b}。"
            + ("未指定表名时仅比较两边第一张表。" if scope == "first_sheet_when_omitted" else "")
        ),
        (
            f"差异：{cells_diff} 处单元格；"
            f"新增行 {summary.get('rows_added', 0)}、删除行 {summary.get('rows_deleted', 0)}、"
            f"修改行 {summary.get('rows_modified', 0)}。"
        ),
    ]
    if unmatched_a:
        lines.append(f"仅 A：{_key_list(unmatched_a)}")
    if unmatched_b:
        lines.append(f"仅 B：{_key_list(unmatched_b)}")
    if dups_a:
        lines.append(f"A 键重复：{_key_list(dups_a)}")
    if dups_b:
        lines.append(f"B 键重复：{_key_list(dups_b)}")
    if truncated:
        lines.append("差异样本已截断，不要当成全表。")
    if sample_diffs:
        lines.append(
            f"单元格样本（前 {min(3, len(sample_diffs))} 条）："
            f"{json.dumps(sample_diffs[:3], ensure_ascii=False, default=str)}"
        )
    if result.get("hint"):
        lines.append(str(result["hint"]))

    row_changes = [
        {"cell": key, "key": key, "old": key, "new": None}
        for key in unmatched_a[:20]
    ] + [
        {"cell": key, "key": key, "old": None, "new": key}
        for key in unmatched_b[:20]
    ]
    cross_changes = row_changes + [
        {
            "cell": sd.get("cell", sd.get("column", "")),
            "key": sd.get("key", ""),
            "old": sd.get("old"),
            "new": sd.get("new"),
        }
        for sd in sample_diffs[:200]
    ]

    ui_meta = ToolUiMeta(
        files=[rel_path_a, rel_path_b],
        diff={
            "diff_mode": diff_mode,
            "file_a": rel_path_a,
            "file_b": rel_path_b,
            "sheet_a": sheet_a,
            "sheet_b": sheet_b,
            "summary": summary,
            "sample_diffs": cross_changes,
            "truncated": truncated,
        },
        merge={
            "source_files": [rel_path_a, rel_path_b],
            "key_columns": key_columns or [],
            "join_type": alignment,
            "rows_matched": summary.get("rows_modified", 0),
            "rows_unmatched": len(result.get("unmatched_in_a") or [])
            + len(result.get("unmatched_in_b") or []),
            "output_file": "",
        },
    )

    return ToolResult(
        success=True,
        model_text="\n".join(lines),
        value=value,
        ui_meta=ui_meta,
        truncated=truncated,
        coverage=result.get("coverage"),
    )


def _attach_file_meta(
    payload: dict[str, Any],
    *,
    rel_path: str | None,
    content_version: str | None,
) -> None:
    if content_version:
        payload["content_version"] = content_version
    if rel_path:
        payload["file_path"] = rel_path


def _preview_from_records(
    columns: list[Any],
    records: list[Any],
    *,
    total_rows: int,
    truncated: bool,
    sheet: str = "",
) -> dict[str, Any] | None:
    if not columns or not isinstance(records, list) or not records:
        return None
    rows_data: list[list[Any]] = []
    for record in records[:50]:
        if isinstance(record, dict):
            rows_data.append([record.get(c) for c in columns])
        elif isinstance(record, list):
            rows_data.append(record)
    return {
        "columns": columns,
        "preview": records[:50],
        "rows": rows_data[:50],
        "total_rows": total_rows,
        "truncated": truncated,
        "sheet": sheet,
    }


def _selection_model_hint(payload: dict[str, Any], *, max_inline_rows: int = 200) -> str:
    """Expose a read result's write-back selection on the native model surface."""
    selection = payload.get("selection")
    if not isinstance(selection, dict):
        return ""
    rows = selection.get("rows")
    if isinstance(rows, list) and len(rows) > max_inline_rows:
        try:
            from excelmanus.engine_core.spill import SpillStore

            locator = SpillStore(_get_guard().workspace_root).put(
                json.dumps({"selection": selection}, ensure_ascii=False, default=str)
            )
            payload["selection_spill"] = str(locator)
            return (
                f"可写回 selection 已外置：{locator}；"
                "把该句柄作为 edit_spreadsheet.operations[].selection 传入，"
                "不要把它当文件路径。"
            )
        except Exception:
            logger.debug("selection spill 失败，回退内联 selection", exc_info=True)
    return "可写回 selection：" + json.dumps(
        selection, ensure_ascii=False, separators=(",", ":"), default=str
    )


def _finalize_filter_data_result(
    result: dict[str, Any],
    *,
    rel_path: str,
    file_path: Any = None,
    sheet_name: str | None = None,
    content_version: str | None = None,
    header_row: Any = None,
    snapshot: Any = None,
) -> ToolResult:
    columns = result.get("columns") or []
    data = result.get("data") or []
    is_truncated = bool(result.get("truncated"))
    original = int(result.get("original_rows") or 0)
    filtered = int(result.get("filtered_rows") or 0)
    returned = int(result.get("returned_rows") or 0)
    _attach_file_meta(result, rel_path=rel_path, content_version=content_version)
    result["records"] = data if isinstance(data, list) else []
    source_rows = result.get("source_rows") if isinstance(result.get("source_rows"), list) else []
    # Preserve original worksheet coordinates after a projected column subset.
    # ``columns`` is the returned view, not a new A-based worksheet.
    original_columns = result.get("source_columns")
    if not isinstance(original_columns, list):
        original_columns = list(columns)
    original_positions = {
        str(column): index + 1 for index, column in enumerate(original_columns)
    }
    source_cols = {
        str(column): original_positions[str(column)]
        for column in columns
        if str(column) in original_positions
    }
    result["source_cols"] = source_cols
    if snapshot is not None:
        from excelmanus.workbook.snapshot import Coverage, apply_read_contract, selection_from_rows

        content_version = snapshot.content_version
        apply_read_contract(
            result,
            snapshot=snapshot,
            result_kind="records",
            sheet=sheet_name,
            header_row=header_row,
            source_rows=source_rows,
            source_cols=source_cols,
            coverage=Coverage(
                kind="truncated" if is_truncated else "complete",
                returned_rows=returned,
                total_rows=filtered,
            ),
            formulas_uncached=result.get("formulas_uncached", "unknown"),
            selection=selection_from_rows(
                snapshot,
                sheet=str(sheet_name or ""),
                rows=[int(r) for r in source_rows],
                cols=list(source_cols.values()),
                origin="filter",
                header_row=header_row,
            ),
            meta_kind="filter",
        )
    elif snapshot is None:
        raise RuntimeError("filter finalize 必须提供 WorkbookSnapshot")

    col_preview = ", ".join(str(c) for c in columns[:8])
    if len(columns) > 8:
        col_preview = f"{col_preview} …(+{len(columns) - 8})"

    lines = [
        f"文件 {result.get('file', rel_path)}：原 {original} 行，匹配 {filtered} 行，返回 {returned} 行。",
    ]
    filters = result.get("filters") or []
    if filters:
        lines.append(
            f"条件（{result.get('logic', 'and')}）："
            f"{json.dumps(filters, ensure_ascii=False, default=str)}"
        )
    if col_preview:
        lines.append(f"列：{col_preview}")
    if is_truncated and result.get("note"):
        lines.append(f"⚠️ {result['note']}")
    if result.get("sheet_disambiguation_warning"):
        lines.append(f"⚠️ {result['sheet_disambiguation_warning']}")
    selection_hint = _selection_model_hint(result)
    if selection_hint:
        lines.append(selection_hint)
    if isinstance(data, list) and data:
        sample_n = min(3, len(data))
        lines.append(
            f"前 {sample_n} 行样本："
            f"{json.dumps(data[:sample_n], ensure_ascii=False, default=str)}"
        )
    if isinstance(data, list) and len(data) > 20:
        # 全量出口：正文只展示 3 行样本时，完整 records 外置到 spill 仓。
        try:
            from excelmanus.engine_core.spill import SpillStore

            full_text = json.dumps(
                {
                    "selection": result.get("selection"),
                    "columns": columns,
                    "records": data,
                    "source_rows": source_rows,
                },
                ensure_ascii=False, default=str,
            )
            locator = SpillStore(_get_guard().workspace_root).put(full_text)
            result["spill"] = str(locator)
            lines.append(
                f"全量 {len(data)} 条 records+source_rows 已外置 {locator}："
                "该句柄不是磁盘路径，把句柄本身当 file_path 传给 inspect_spreadsheet 即可取回全部；"
                "不要再对原工作簿分段分页重读。"
            )
        except Exception:
            logger.debug("filter 全量 spill 失败，仅保留样本", exc_info=True)

    return ToolResult(
        success=True,
        model_text="\n".join(lines),
        value=result,
        ui_meta=ToolUiMeta(
            files=[rel_path] if rel_path else [],
            preview=_preview_from_records(
                columns,
                data if isinstance(data, list) else [],
                total_rows=filtered,
                truncated=is_truncated,
                sheet=sheet_name or "",
            ),
            content_version=content_version,
        ),
        truncated=is_truncated,
        coverage={
            "kind": "truncated" if is_truncated else "complete",
            "rows": returned,
            "original_rows": original,
            "filtered_rows": filtered,
        },
    )


def _finalize_inspect_excel_files_result(
    result: dict[str, Any],
    *,
    file_paths: list[str],
) -> ToolResult:
    is_truncated = bool(result.get("truncated"))
    found = int(result.get("excel_files_found") or 0)
    files = result.get("files") or []
    names: list[str] = []
    for item in files[:8]:
        if isinstance(item, dict):
            names.append(str(item.get("file") or item.get("path") or ""))
    name_part = "、".join(n for n in names if n)
    extra = found - len(names)
    if extra > 0 and name_part:
        name_part = f"{name_part} 等 {found} 个"

    lines = [
        f"目录 {result.get('directory', '.')}：发现 {found} 个 Excel/CSV 文件。",
    ]
    if name_part:
        lines.append(f"文件：{name_part}。")
    if is_truncated:
        lines.append("⚠️ 文件列表已按 max_files 截断。")
    if result.get("include_warning"):
        lines.append(str(result["include_warning"]))

    result["status"] = "success"
    result["result_kind"] = "areas"
    result["meta"] = {
        "kind": "overview",
        "sampled": False,
        "truncated": is_truncated,
        "formulas_uncached": "unknown",
        "header_row": None,
        "sheet": None,
    }
    result["coverage"] = {
        "kind": "truncated" if is_truncated else "complete",
        "returned_rows": len(files) if isinstance(files, list) else 0,
        "total_rows": found,
        "offset": 0,
    }

    ui_preview = None
    first = files[0] if files and isinstance(files[0], dict) else None
    if first:
        sheets = first.get("sheets") or []
        sheet0 = sheets[0] if sheets and isinstance(sheets[0], dict) else None
        if sheet0:
            header = sheet0.get("header") or []
            preview_rows = sheet0.get("preview") or []
            ui_preview = {
                "columns": header,
                "preview": preview_rows[:50],
                "rows": preview_rows[:50],
                "total_rows": int(sheet0.get("rows") or 0),
                "truncated": is_truncated,
                "sheet": sheet0.get("name") or "",
            }

    return ToolResult(
        success=True,
        model_text="\n".join(lines),
        value=result,
        ui_meta=ToolUiMeta(files=list(file_paths), preview=ui_preview),
        truncated=is_truncated,
        coverage={
            "kind": "truncated" if is_truncated else "complete",
            "files": found,
        },
    )


def _finalize_scan_excel_snapshot_result(
    result: dict[str, Any],
    *,
    rel_path: str,
    file_path: Any = None,
    content_version: str | None = None,
) -> ToolResult:
    sheets = result.get("sheets") or []
    sampled = any(isinstance(s, dict) and s.get("sampled") for s in sheets)
    sheet_truncated = bool(result.get("truncated"))
    if sheet_truncated:
        coverage_kind = "truncated"
    elif sampled:
        coverage_kind = "sampled"
    else:
        coverage_kind = "complete"
    _attach_file_meta(result, rel_path=rel_path, content_version=content_version)
    _mark_success(result)

    lines: list[str] = []
    resolved = result.get("resolved_sheet")
    file_label = f"文件 {result.get('file', rel_path)}（{result.get('size', '')}）"
    if result.get("scan_scope") == "sheet" and resolved:
        lines.append(
            f"{file_label}：本次只扫「{resolved}」这一张，不是全书工作表数。"
        )
    else:
        lines.append(
            f"{file_label}：{result.get('sheet_count', len(sheets))} 个工作表。"
        )
    for sheet in sheets[:6]:
        if not isinstance(sheet, dict):
            continue
        part = f"{sheet.get('name')}: {sheet.get('rows', 0)} 行 × {sheet.get('cols', 0)} 列"
        if sheet.get("sampled"):
            part += f"（采样 {sheet.get('sample_size')}）"
        if sheet.get("error"):
            part += f"（{sheet['error']}）"
        lines.append(part)
    signals = result.get("quality_signals") or []
    if signals:
        labels: list[str] = []
        for item in signals[:6]:
            if not isinstance(item, dict):
                continue
            kind = str(item.get("type") or "signal")
            loc = ".".join(
                str(part) for part in (item.get("sheet"), item.get("column")) if part
            )
            labels.append(f"{kind}({loc})" if loc else kind)
        lines.append(f"质量信号 {len(signals)} 条：{', '.join(labels)}。")
    rels = result.get("relationships") or []
    if rels:
        lines.append(f"跨表关联 {len(rels)} 条。")
    if result.get("form_layout"):
        lines.append("版式表单，质量信号按数据框不适用。")
    if result.get("truncated_note"):
        lines.append(f"⚠️ {result['truncated_note']}")
    elif sampled:
        lines.append("⚠️ 大表已采样，统计基于样本，不可当作全表事实。")

    ui_preview = None
    if sheets and isinstance(sheets[0], dict):
        cols = sheets[0].get("columns") or []
        col_names = [c.get("name") if isinstance(c, dict) else str(c) for c in cols]
        ui_preview = {
            "columns": col_names,
            "preview": [],
            "rows": [],
            "total_rows": int(sheets[0].get("rows") or 0),
            "truncated": sheet_truncated or sampled,
            "sheet": sheets[0].get("name") or "",
        }

    return ToolResult(
        success=True,
        model_text="\n".join(lines),
        value=result,
        ui_meta=ToolUiMeta(
            files=[rel_path] if rel_path else [],
            preview=ui_preview,
            content_version=content_version,
        ),
        truncated=sheet_truncated or sampled,
        coverage={
            "kind": coverage_kind,
            "sheets": int(result.get("sheet_count") or len(sheets)),
        },
    )


def _finalize_search_excel_values_result(
    result: dict[str, Any],
    *,
    rel_paths: list[str],
    file_path: Any = None,
    snapshot: Any = None,
) -> ToolResult:
    matches = result.get("matches") or []
    is_truncated = bool(result.get("truncated"))
    total = int(result.get("total_matches") or 0)
    returned = int(result.get("returned") or len(matches))
    content_version = None
    if snapshot is not None:
        content_version = snapshot.content_version
        result["snapshot_id"] = snapshot.id.key()
        if snapshot.file.relative not in rel_paths:
            rel_paths = [snapshot.file.relative]
    if content_version:
        result["content_version"] = content_version
    if len(rel_paths) == 1:
        result["file_path"] = rel_paths[0]
    _mark_success(result)

    lines = [
        f"搜索「{result.get('query', '')}」（{result.get('match_mode', 'contains')}）："
        f"共 {total} 处匹配，返回 {returned} 条。",
    ]
    if result.get("files_searched"):
        lines.append(f"已搜索 {result['files_searched']} 个文件。")
    elif result.get("sheets_searched") is not None:
        lines.append(f"已搜索 {result['sheets_searched']} 个工作表。")
    summary = result.get("summary_by_sheet") or []
    if summary:
        parts: list[str] = []
        for item in summary[:6]:
            if not isinstance(item, dict):
                continue
            label = str(item.get("sheet") or "")
            if item.get("file"):
                label = f"{item['file']}/{label}" if label else str(item["file"])
            parts.append(f"{label} {item.get('matches', 0)}")
        if parts:
            lines.append("按表：" + "、".join(parts) + "。")
    if is_truncated:
        lines.append("⚠️ 匹配结果已截断，完整命中见结构化数据。")
    if matches:
        samples: list[str] = []
        for match in matches[:3]:
            if not isinstance(match, dict):
                continue
            loc = f"{match.get('sheet', '')}!{match.get('cell_ref', '')}"
            samples.append(f"{loc} = {match.get('value', '')}")
        if samples:
            lines.append("样本：" + "；".join(samples) + "。")
    hints = result.get("search_hints") or []
    if hints:
        lines.append("提示：" + "；".join(str(h) for h in hints[:3]))

    ui_preview = None
    if matches:
        ui_preview = {
            "columns": ["sheet", "cell_ref", "column", "value"],
            "preview": matches[:50],
            "rows": [
                [m.get("sheet"), m.get("cell_ref"), m.get("column"), m.get("value")]
                for m in matches[:50]
                if isinstance(m, dict)
            ],
            "total_rows": total,
            "truncated": is_truncated,
            "sheet": "",
        }

    return ToolResult(
        success=True,
        model_text="\n".join(lines),
        value=result,
        ui_meta=ToolUiMeta(
            files=list(rel_paths),
            preview=ui_preview,
            content_version=content_version,
        ),
        truncated=is_truncated,
        coverage={
            "kind": "truncated" if is_truncated else "complete",
            "rows": returned,
            "total_matches": total,
        },
    )


def _finalize_discover_file_relationships_result(
    result: dict[str, Any],
    *,
    file_paths: list[str],
) -> ToolResult:
    hints = result.get("merge_hints") or []
    summary = str(result.get("summary") or "")
    lines = [summary] if summary else ["未发现跨文件列关联。"]
    first_hint = hints[0] if hints and isinstance(hints[0], dict) else None
    if first_hint and first_hint.get("suggested_join_label"):
        lines.append(f"建议：{first_hint['suggested_join_label']}。")
    warnings = result.get("type_warnings") or []
    if warnings:
        lines.append(f"类型告警 {len(warnings)} 条。")

    merge = None
    if first_hint:
        merge = {
            "source_files": list(file_paths),
            "key_columns": [first_hint.get("key_column_a"), first_hint.get("key_column_b")],
            "join_type": first_hint.get("suggested_join") or "inner",
            "rows_matched": 0,
            "rows_unmatched": 0,
            "output_file": "",
        }
    elif file_paths:
        merge = {
            "source_files": list(file_paths),
            "key_columns": [],
            "join_type": "",
            "rows_matched": 0,
            "rows_unmatched": 0,
            "output_file": "",
        }

    _mark_success(result)
    return ToolResult(
        success=True,
        model_text="\n".join(lines),
        value=result,
        ui_meta=ToolUiMeta(files=list(file_paths), merge=merge),
        truncated=False,
        coverage={
            "kind": "complete",
            "files": int(result.get("files_analyzed") or len(file_paths)),
        },
    )


def _read_range_direct(
    safe_path: Any,
    sheet_name: str | None,
    cell_range: str,
    *,
    include_formulas: bool = False,
) -> dict[str, Any]:
    """读取指定引用：单矩形走快路径，并集逐区域读取后合并。"""
    from openpyxl import load_workbook

    from excelmanus.workbook.address import worksheet_used_shape

    area = parse_ref(cell_range, default_sheet=sheet_name)
    needs_bind = any(not isinstance(part, RectRef) for part in area.areas)
    wb = load_workbook(safe_path, read_only=not needs_bind, data_only=True)
    parts: list[dict[str, Any]] = []
    try:
        rects = _bind_area_in_workbook(wb, area, default_sheet=sheet_name)
        used_cache: dict[str, tuple[int, int]] = {}
        for rect in rects:
            title = rect.sheet
            if not title or title not in wb.sheetnames:
                raise WorkbookRefBindError("无法打开工作表", code=SHEET_NOT_FOUND)
            ws = wb[title]
            used = used_cache.get(ws.title)
            if used is None:
                used = worksheet_used_shape(ws)
                used_cache[ws.title] = used
            parts.append(
                _read_rect_from_ws(
                    ws, rect, used_max_row=used[0], used_max_col=used[1],
                )
            )
    finally:
        wb.close()

    payload = _merge_range_parts(parts, requested=cell_range)
    payload["formulas_uncached"] = "unknown"

    cell_count = 0
    for part in parts:
        shape = part.get("shape") or {}
        cell_count += int(shape.get("rows") or 0) * int(shape.get("columns") or 0)
    load_formulas = include_formulas or (
        0 < cell_count <= _SMALL_RANGE_FORMULA_CELLS
    )

    if load_formulas:
        wb_f = load_workbook(safe_path, read_only=True, data_only=False)
        try:
            formula_grids: list[list[list[Any]]] = []
            for part in parts:
                title = str(part.get("resolved_sheet") or "")
                if title not in wb_f.sheetnames:
                    raise WorkbookRefBindError("无法打开工作表", code=SHEET_NOT_FOUND)
                formula_grids.append(_read_formula_grid(wb_f[title], part))
            if len(formula_grids) == 1:
                payload["formula_grid"] = formula_grids[0]
                payload["formulas"] = formula_grids[0]
            else:
                payload["formula_grid"] = formula_grids
                payload["formulas"] = formula_grids
                for part, grid in zip(parts, formula_grids):
                    part["formula_grid"] = grid
                    part["formulas"] = grid
            has_formula = False
            uncached = False
            for grid, part in zip(formula_grids, parts):
                values = part.get("values") or []
                for ri, frow in enumerate(grid):
                    for ci, formula in enumerate(frow):
                        if not formula:
                            continue
                        has_formula = True
                        value = None
                        if ri < len(values) and isinstance(values[ri], list) and ci < len(values[ri]):
                            value = values[ri][ci]
                        if value is None:
                            uncached = True
            payload["formulas_uncached"] = uncached if has_formula else False
        finally:
            wb_f.close()
    return payload


def read_excel(
    file_path: str,
    sheet_name: str | None = None,
    max_rows: int | None = None,
    header_row: int | None = None,
    include: list[str] | None = None,
    max_style_scan_rows: int = 200,
    range: str | None = None,
    offset: int | None = None,
    sample_rows: int | None = None,
    expected_version: str | None = None,
) -> ToolResult:
    """读取 Excel/CSV 文件并返回数据摘要，可通过 include 按需附加额外维度。

    Args:
        file_path: Excel/CSV 文件路径（相对或绝对）。支持 .xlsx/.xls/.xlsm/.xlsb/.csv/.tsv。
        sheet_name: 工作表名称，默认读取第一个（CSV 时忽略）。
        max_rows: 最大读取行数，默认全部读取。
        header_row: 列头所在行号（Excel 行号，1-based），默认自动检测。
            当工作表有合并标题行时，需指定真正的列头行号。
        include: 按需请求的额外维度列表。可选值：
            styles — 压缩样式类（Style Classes + cell_style_map + merged_ranges）
            charts — 嵌入图表元信息
            images — 嵌入图片元信息
            freeze_panes — 冻结窗格位置
            conditional_formatting — 条件格式规则
            data_validation — 数据验证规则
            print_settings — 打印设置
            column_widths — 非默认列宽
            formulas — 含公式的单元格
            categorical_summary — 分类列的 value_counts（unique 值 < 阈值的列）
            summary — 每列数据质量概要（null 率、unique 数、min/max、高频值）
            vba — VBA 宏信息（仅 .xlsm 文件有效，含模块列表及可选源码）
        max_style_scan_rows: styles/formulas 维度扫描的最大行数，默认 200。
        range: Excel 坐标范围（如 "A1:F20"、"B100:D200"），指定后进入精确读取模式，
            绕过 pandas 直接用 openpyxl 读取指定区域，大文件友好。CSV 文件自动忽略此参数。
        offset: 数据行偏移（从0开始，header 之后起算），与 max_rows 组合实现分页。
        sample_rows: 等距采样行数，用于了解大表数据分布。

    Returns:
        ToolResult（value 含结构化摘要，model_text 为短摘要）。
    """
    guard = _get_guard()
    live_path = guard.resolve_and_validate(file_path)
    not_found = check_file_exists(live_path, file_path, guard)
    if not_found is not None:
        return not_found

    from excelmanus.workbook.address import combine_sheet_names, parse_sheet_address
    from excelmanus.workbook.snapshot import (
        Coverage,
        SnapshotError,
        apply_read_contract,
        require_default_sheet,
        selection_from_rows,
    )

    snap, snap_err = _open_tool_snapshot(
        file_path, expected_version=expected_version,
    )
    if snap_err is not None:
        return snap_err
    safe_path = snap.backing_path
    rel_path = snap.file.relative
    bound_version = snap.content_version

    if range is not None:
        try:
            parsed_range = parse_sheet_address(str(range))
        except InvalidRefError as exc:
            return error_result(str(exc), code=RANGE_INVALID)
        range = parsed_range.address or None
        try:
            sheet_name = combine_sheet_names(sheet_name, parsed_range.sheet)
        except ValueError as exc:
            return _error_payload_result(
                {"error": str(exc), "code": "INVALID_ARGS"},
                code="INVALID_ARGS",
            )

    if range is not None and _is_csv_file(safe_path):
        return _error_payload_result(
            {"error": "CSV 不支持 range，请去掉 range 或改用 xlsx。", "code": "INVALID_ARGS"},
            code="INVALID_ARGS",
        )
    if range is not None and (max_rows is not None or offset is not None or sample_rows is not None):
        extras = [name for name, val in (("max_rows", max_rows), ("offset", offset), ("sample_rows", sample_rows)) if val is not None]
        warnings_list = [f"精确 range 已忽略 {', '.join(extras)}"]
        max_rows = None
        offset = None
        sample_rows = None
    else:
        warnings_list = []

    def _finish_read(summary: dict[str, Any], **kwargs: Any) -> ToolResult:
        if warnings_list:
            existing = summary.get("warnings")
            if isinstance(existing, list):
                summary["warnings"] = [*existing, *warnings_list]
            else:
                summary["warnings"] = list(warnings_list)
        if sheet_name and "resolved_sheet" not in summary:
            summary["resolved_sheet"] = sheet_name
        public_header = header_row
        if public_header is None:
            public_header = summary.get("detected_header_row")
        return _finalize_read_excel_result(
            summary,
            rel_path=rel_path,
            sheet_name=sheet_name or summary.get("resolved_sheet"),
            file_path=str(safe_path),
            content_version=bound_version,
            header_row=public_header,
            snapshot=snap,
            **kwargs,
        )

    if not snap.is_csv():
        wb_names = snap.open_workbook(data_only=True, read_only=True)
        try:
            try:
                range_names_sheet = False
                if range is not None:
                    try:
                        range_names_sheet = bool(
                            parse_ref(str(range), default_sheet=None).sheets()
                        )
                    except InvalidRefError:
                        range_names_sheet = False
                if not range_names_sheet:
                    sheet_name = require_default_sheet(list(wb_names.sheetnames), sheet_name)
            except SnapshotError as exc:
                return error_result(str(exc), code=exc.code, fields=exc.fields or None)
        finally:
            wb_names.close()
    else:
        sheet_name = sheet_name or "Sheet1"

    # ── range 模式：精确读取指定坐标范围 ──
    if range is not None and not _is_csv_file(safe_path):
        include_set = set(include or [])
        extra_include = sorted(include_set - {"formulas"})
        try:
            result = _read_range_direct(
                safe_path,
                sheet_name,
                range,
                include_formulas="formulas" in include_set,
            )
        except WorkbookRefBindError as exc:
            return error_result(str(exc), code=exc.code)
        except InvalidRefError as exc:
            return error_result(str(exc), code=RANGE_INVALID)
        except Exception as exc:
            from excelmanus.workbook.address import looks_like_coordinate_error

            code = RANGE_INVALID if looks_like_coordinate_error(exc) else "EXECUTION_FAILED"
            return error_result(
                str(exc) if looks_like_coordinate_error(exc) else f"range={range!r} 读取失败：{exc}",
                code=code,
            )
        result["file"] = snap.file.relative
        if extra_include:
            result["include_warning"] = (
                "range 模式只支持 include=formulas；"
                f"已忽略 {extra_include}。"
            )
        if result.get("resolved_sheet"):
            sheet_name = result["resolved_sheet"]
        return _finish_read(result)

    # ── 标准模式 ──
    # offset 调整：读取 offset + max_rows 行再切片
    effective_max_rows = max_rows
    if offset is not None and offset > 0 and max_rows is not None:
        effective_max_rows = offset + max_rows

    df, effective_header = _read_df(safe_path, sheet_name, max_rows=effective_max_rows, header_row=header_row)

    skip = int(offset or 0)
    if skip > 0:
        df = df.iloc[skip:].reset_index(drop=True)

    # 当 max_rows/offset 限制了读取范围时，获取 sheet 实际总行数
    total_rows_in_sheet: int | None = None
    if max_rows is not None or (offset is not None and offset > 0):
        total_rows_in_sheet = _get_sheet_total_rows(safe_path, sheet_name)

    # 构建摘要信息
    # 对外报告原工作区相对名；safe_path 是快照 backing（内容寻址缓存），不外泄。
    summary: dict[str, Any] = {
        "file": rel_path,
        "shape": {"rows": df.shape[0], "columns": df.shape[1]},
    }

    # 数据完整性指示：截断元数据放在 columns/preview 之前，确保即使被引擎层截断也能保留
    if total_rows_in_sheet is not None and total_rows_in_sheet > df.shape[0]:
        completeness = build_completeness_meta(
            total_available=total_rows_in_sheet,
            returned=df.shape[0],
        )
        summary["total_rows_in_sheet"] = total_rows_in_sheet
        summary["is_truncated"] = completeness.get("is_truncated", False)
        summary["truncation_note"] = completeness.get("truncation_note", "")

    summary["columns"] = [str(c) for c in df.columns]
    summary["dtypes"] = {str(col): str(dtype) for col, dtype in df.dtypes.items()}
    _null_info = _build_null_info(df)
    if _null_info:
        summary["null_info"] = _null_info
    summary["preview"] = _df_to_compact_records(df.head(10))
    summary["data"] = _df_to_compact_records(df)
    summary["source_cols"] = list(_builtin_range(1, len(df.columns) + 1))

    # 自动 tail 预览：表格 > 20 行时附加最后 5 行
    if df.shape[0] > 20:
        tail_start = df.shape[0] - 5
        summary["tail_preview"] = _df_to_compact_records(df.tail(5))
        summary["tail_note"] = f"显示最后 5 行（第 {tail_start + 1}~{df.shape[0]} 行）"

    # 等距采样：sample_rows 指定时附加采样数据
    if sample_rows is not None and sample_rows > 0 and len(df) > sample_rows:
        step = max(1, len(df) // sample_rows)
        indices = list(_builtin_range(0, len(df), step))[:sample_rows]
        sampled_df = df.iloc[indices]
        summary["sample_preview"] = _df_to_compact_records(sampled_df)
        summary["sample_note"] = f"等距采样 {len(indices)} 行（共 {len(df)} 行，间隔 {step}）"

    formula_meta = df.attrs.get("formula_resolution")
    if isinstance(formula_meta, dict):
        if formula_meta.get("resolved_columns") or formula_meta.get("unresolved_columns"):
            summary["formula_resolution"] = formula_meta

    if header_row is None:
        if effective_header == -1:
            summary["detected_form_type"] = True
        summary["detected_header_row"] = _to_public_header_row(effective_header)
    if offset:
        summary["offset"] = int(offset)
    if effective_header == -1:
        summary["source_rows"] = [i + 1 + skip for i in _builtin_range(len(df))]
    else:
        summary["source_rows"] = [
            _excel_source_row(effective_header, i + skip) for i in _builtin_range(len(df))
        ]

    # Unnamed 列名警告：提醒 LLM 列名不可靠，建议指定 header_row
    # 表单类文档不使用此警告
    unnamed_cols = [str(c) for c in df.columns if str(c).startswith("Unnamed")]
    if unnamed_cols and effective_header != -1:
        summary["unnamed_columns_warning"] = (
            f"检测到 {len(unnamed_cols)} 个 Unnamed 列名（共 {len(df.columns)} 列），"
            f"可能是合并标题行导致。建议使用 header_row 参数指定真正的列头行号重新读取。"
        )

    # 合并单元格警告：高合并率时提醒 LLM 注意值传播。普通工作簿没有
    # mergeCell 元数据时直接跳过一次昂贵的 full-mode openpyxl 解析；有
    # 合并区域的文件保留原有完整摘要语义。
    if not _is_csv_file(safe_path) and _workbook_may_have_merged_cells(safe_path):
        try:
            from openpyxl import load_workbook as _lw
            _wb_mc = _lw(safe_path, read_only=False, data_only=True)
            try:
                _ws_mc = (
                    _wb_mc[sheet_name]
                    if sheet_name and sheet_name in _wb_mc.sheetnames
                    else _wb_mc.active
                )
                if _ws_mc is not None:
                    _mc_summary = _collect_merged_cell_summary(_ws_mc)
                    if _mc_summary:
                        summary["merged_cell_summary"] = _mc_summary
            finally:
                _wb_mc.close()
        except Exception:
            pass

    include_set: set[str] = set()
    if include:
        include_set.update(include)

    # 校验 include 维度
    invalid_dims = include_set - set(INCLUDE_DIMENSIONS)
    if invalid_dims:
        summary["include_warning"] = f"未知的 include 维度已忽略: {sorted(invalid_dims)}"
        include_set -= invalid_dims

    # 分发基于 DataFrame 的 include 维度（不需要 openpyxl）
    if "categorical_summary" in include_set:
        summary["categorical_summary"] = _collect_categorical_summary(df)
        include_set.discard("categorical_summary")

    if "summary" in include_set:
        summary["data_summary"] = _collect_data_summary(df)
        include_set.discard("summary")

    # vba 维度：基于文件级别，不依赖 worksheet
    if "vba" in include_set:
        summary["vba"] = _collect_vba_info(safe_path)
        include_set.discard("vba")

    # 分发 include 维度采集（需要用 openpyxl 打开，CSV 不支持）
    _CSV_UNSUPPORTED_DIMS = {"styles", "charts", "images", "freeze_panes",
                             "conditional_formatting", "data_validation",
                             "print_settings", "column_widths", "formulas",
                             "merges"}
    if include_set and _is_csv_file(safe_path):
        skipped = include_set & _CSV_UNSUPPORTED_DIMS
        if skipped:
            summary["csv_unsupported_dimensions"] = (
                f"CSV 文件不支持以下维度（已跳过）: {sorted(skipped)}"
            )
            include_set -= skipped
    if include_set and not _is_csv_file(safe_path):
        from openpyxl import load_workbook

        # styles/charts/images/freeze_panes 等需要非 data_only 模式
        # formulas 也需要非 data_only 模式以读取公式文本
        needs_formulas = "formulas" in include_set
        wb_include = load_workbook(safe_path, data_only=not needs_formulas)
        try:
            ws_include = (
                wb_include[sheet_name]
                if sheet_name and sheet_name in wb_include.sheetnames
                else wb_include.active
            )
            extra = _dispatch_include_dimensions(ws_include, include_set, max_style_scan_rows)
            summary.update(extra)
        finally:
            wb_include.close()

    return _finish_read(summary, sample_rows=sample_rows)


def _load_df_for_tool(
    file_path: str,
    sheet_name: str | None,
    header_row: int | None,
    column_hints: list[str] | None = None,
    expected_version: str | None = None,
) -> tuple[dict[str, Any] | None, ToolResult | None]:
    """filter/aggregate/distinct 共用的前置：守卫→快照→定 sheet→读 df。

    返回 (ctx, err)。ctx 含 df/sheet_name/effective_header/snap/safe_path/rel_path/
    bound_version；err 为 ToolResult 时应直接返回。
    ``column_hints`` 为目标列名（P0-B 列感知消歧用）：多表省略 sheet 时，
    无列证据仅绑定唯一可见表；有列证据仅在唯一安全命中时绑定。两种路径都在
    ctx["sheet_disambiguation"] 声明，否则仍硬失败。
    """
    guard = _get_guard()
    live_path = guard.resolve_and_validate(file_path)
    not_found = check_file_exists(live_path, file_path, guard)
    if not_found is not None:
        return None, not_found
    from excelmanus.workbook.snapshot import (
        SnapshotError,
        require_default_sheet,
        resolve_sheet_by_columns,
        resolve_sheet_by_visibility,
    )

    snap, snap_err = _open_tool_snapshot(file_path, expected_version=expected_version)
    if snap_err is not None:
        return None, snap_err
    safe_path = snap.backing_path
    disambiguation: dict[str, Any] | None = None
    if not snap.is_csv():
        wb_names = snap.open_workbook(data_only=True, read_only=True)
        try:
            names = list(wb_names.sheetnames)
            hidden = {
                str(ws.title): str(getattr(ws, "sheet_state", None) or "visible") != "visible"
                for ws in wb_names.worksheets
            }
            try:
                sheet_name = require_default_sheet(names, sheet_name)
            except SnapshotError as exc:
                if getattr(exc, "code", "") != "SHEET_REQUIRED":
                    return None, error_result(str(exc), code=exc.code, fields=exc.fields or None)
                try:
                    if column_hints:
                        bound, info = resolve_sheet_by_columns(
                            names, column_hints,
                            lambda title: _probe_candidate_columns(
                                safe_path, title, header_row,
                                hidden.get(str(title), False),
                            ),
                        )
                    else:
                        bound, info = resolve_sheet_by_visibility(names, hidden)
                except SnapshotError as exc2:
                    return None, error_result(str(exc2), code=exc2.code, fields=exc2.fields or None)
                sheet_name = bound
                disambiguation = info
        finally:
            wb_names.close()
    else:
        sheet_name = sheet_name or "Sheet1"

    df, effective_header = _read_df(safe_path, sheet_name, header_row=header_row)
    ctx = {
        "df": df,
        "sheet_name": sheet_name,
        "effective_header": effective_header,
        "snap": snap,
        "safe_path": safe_path,
        "rel_path": snap.file.relative,
        "bound_version": snap.content_version,
        "sheet_disambiguation": disambiguation,
    }
    return ctx, None


_DISAMBIG_PROBE_ROWS = 200


def _probe_candidate_columns(
    safe_path: Any,
    sheet_name: str,
    header_row: int | None,
    hidden: bool,
) -> tuple[list[str] | None, str | None, bool]:
    """消歧探针：对单张候选表取列名（显式表名，不触发 wb.active 回退）。

    返回 (columns | None, excluded | None, hidden)。excluded 为
    "form"（表单类）/"empty"（空表或表头不可用）/"error"（读失败）之一。
    """
    if header_row == -1:
        return None, "form", hidden
    try:
        df, effective_header = _read_df(safe_path, sheet_name, max_rows=_DISAMBIG_PROBE_ROWS, header_row=header_row)
    except Exception:
        return None, "error", hidden
    if effective_header == -1:
        return None, "form", hidden
    cols = [str(c) for c in df.columns]
    if not cols or all(c.startswith("Unnamed") for c in cols):
        return None, "empty", hidden
    return cols, None, hidden


def _sanitize_hint_name(value: Any) -> str | None:
    """列 hint 清洗：去空、去 "*"、去 Unnamed（检测失败产物不可作证据）。"""
    text = str(value).strip() if value is not None else ""
    if not text or text == "*" or text.startswith("Unnamed"):
        return None
    return text


def _hint_columns_from_value(value: Any) -> list[str]:
    """从 group_by/columns/index/values 等异形参数提取列名。"""
    try:
        value = _maybe_json(value)
    except Exception:
        pass
    out: list[str] = []
    if value is None or value == "":
        return out
    items = value if isinstance(value, list) else [value]
    for item in items:
        if isinstance(item, dict):
            name = item.get("column", item.get("name", ""))
            clean = _sanitize_hint_name(name)
            if clean:
                out.append(clean)
        else:
            clean = _sanitize_hint_name(item)
            if clean:
                out.append(clean)
    return out


def _collect_column_hints(
    *,
    group_by: Any = None,
    aggregations: Any = None,
    column: Any = None,
    conditions: Any = None,
    columns: Any = None,
    index: Any = None,
    values: Any = None,
    sort_by: Any = None,
    join: Any = None,
) -> list[str]:
    """从分析参数收集消歧证据列（只读主表口径；join 右表列不纳入）。"""
    hints: list[str] = []
    hints.extend(_hint_columns_from_value(group_by))
    hints.extend(_hint_columns_from_value(column))
    hints.extend(_hint_columns_from_value(columns))
    hints.extend(_hint_columns_from_value(index))
    hints.extend(_hint_columns_from_value(values))
    hints.extend(_hint_columns_from_value(sort_by))
    if isinstance(aggregations, dict):
        for key in aggregations:
            clean = _sanitize_hint_name(key)
            if clean:
                hints.append(clean)
    if isinstance(conditions, list):
        for cond in conditions:
            if isinstance(cond, dict):
                clean = _sanitize_hint_name(cond.get("column"))
                if clean:
                    hints.append(clean)
    if isinstance(join, dict):
        # 只消歧主表（左表）：left_on 纳入，right_on/join.columns 不纳入。
        for key in ("left_on",):
            clean = _sanitize_hint_name(join.get(key))
            if clean:
                hints.append(clean)
    seen: set[str] = set()
    ordered: list[str] = []
    for h in hints:
        if h not in seen:
            seen.add(h)
            ordered.append(h)
    return ordered


def _attach_sheet_disambiguation(result: dict[str, Any], disambiguation: dict[str, Any] | None) -> str | None:
    """把消歧声明写入结果（非沉默三件套），返回 model_text 用警告行。"""
    if not disambiguation:
        return None
    result["sheet_disambiguation"] = disambiguation
    matched = disambiguation.get("matched_sheet", "?")
    hints = disambiguation.get("hints") or []
    if disambiguation.get("strategy") == "sole_visible_sheet":
        line = (
            f"未指定 sheet，已自动绑定唯一可见工作表 '{matched}'"
            "（隐藏表未参与选择）；如非预期请显式指定 sheet 重试。"
        )
    else:
        line = (
            f"未指定 sheet，已按列 {hints} 在唯一表 '{matched}' 命中并自动绑定"
            "（显式声明，非默认首表）；如非预期请显式指定 sheet 重试。"
        )
    result.setdefault("warnings", []).append(line)
    result["sheet_disambiguation_warning"] = line
    return line


def _filter_contains_literal(s: pd.Series, v: Any) -> pd.Series:
    return s.astype(str).str.contains(str(v), na=False, regex=False)


def _filter_contains_regex(s: pd.Series, v: Any) -> pd.Series:
    return s.astype(str).str.contains(str(v), na=False, regex=True)


def _date_only(value: Any) -> bool:
    """值是否是"只有日期"的边界（2024-08-31 / date 而非 datetime）。"""
    if isinstance(value, datetime):
        return False
    if isinstance(value, date):
        return True
    return isinstance(value, str) and re.fullmatch(
        r"\d{4}-\d{2}-\d{2}", value.strip()
    ) is not None


def _as_datetime_bound(value: Any, *, upper: bool) -> Any:
    """datetime 列的边界：可解析则转 Timestamp，date-only 上界扩到当日末尾。"""
    ts = pd.to_datetime(value, errors="coerce")
    if pd.isna(ts):
        return value
    if upper and _date_only(value):
        return ts + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)
    return ts


def _as_datetime_series(s: pd.Series) -> pd.Series | None:
    """列若能当日期用则返回 datetime 化副本（object 列里的 ISO 日期也认）。

    判定标准：本身已是 datetime，或 ≥50% 非空值可解析为日期。
    """
    if pd.api.types.is_datetime64_any_dtype(s):
        return s
    converted = pd.to_datetime(s, errors="coerce", format="mixed")
    non_null = int(s.notna().sum())
    if non_null and int(converted.notna().sum()) >= non_null * 0.5:
        return converted
    return None


def _parse_number(value: Any) -> float | None:
    """边界值→float；bool 与不可解析的字符串返回 None。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip().replace(",", "")
        if text and re.fullmatch(r"-?\d+(\.\d+)?([eE][+-]?\d+)?", text):
            return float(text)
    return None


def _numeric_series_for_cmp(s: pd.Series) -> pd.Series | None:
    """列可按数值比较时返回数值化副本；bool/datetime/纯文本列返回 None。

    数值 dtype 直接用；object 列要求 ≥50% 非空值可数值化（与 _as_datetime_series 同阈值），
    避免把编码类文本列误判为数值列。
    """
    if pd.api.types.is_bool_dtype(s) or pd.api.types.is_datetime64_any_dtype(s):
        return None
    converted = _coerce_numeric(s)
    if pd.api.types.is_numeric_dtype(s):
        return converted
    non_null = int(s.notna().sum())
    if non_null and int(converted.notna().sum()) >= non_null * 0.5:
        return converted
    return None


def _filter_between(s: pd.Series, v: Any) -> pd.Series:
    if not isinstance(v, (list, tuple)) or len(v) < 2:
        raise ValueError("between 需要长度为 2 的数组，如 [min, max]")
    if all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in v[:2]):
        return _coerce_numeric(s).between(v[0], v[1])
    num_col = _numeric_series_for_cmp(s)
    if num_col is not None:
        lo = _parse_number(v[0])
        hi = _parse_number(v[1])
        if lo is not None and hi is not None:
            return num_col.between(lo, hi)
        raise ValueError(f"数值列的 between 边界需为数字，收到: {v[:2]!r}")
    ds = _as_datetime_series(s)
    if ds is not None:
        lo = _as_datetime_bound(v[0], upper=False)
        hi = _as_datetime_bound(v[1], upper=True)
        if isinstance(lo, pd.Timestamp) and isinstance(hi, pd.Timestamp):
            return ds.between(lo, hi)
    return s.astype(str).between(str(v[0]), str(v[1]))


def _filter_cmp_eq(s: pd.Series, v: Any) -> pd.Series:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return _coerce_numeric(s) == v
    num_col = _numeric_series_for_cmp(s)
    if num_col is not None:
        parsed = _parse_number(v)
        if parsed is not None:
            return num_col == parsed
    ds = _as_datetime_series(s)
    if ds is not None:
        bound = _as_datetime_bound(v, upper=False)
        if isinstance(bound, pd.Timestamp):
            if _date_only(v):
                return ds.dt.normalize() == bound.normalize()
            return ds == bound
    return s.astype(str) == str(v)


def _filter_cmp_ne(s: pd.Series, v: Any) -> pd.Series:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return _coerce_numeric(s) != v
    num_col = _numeric_series_for_cmp(s)
    if num_col is not None:
        parsed = _parse_number(v)
        if parsed is not None:
            return num_col != parsed
    ds = _as_datetime_series(s)
    if ds is not None:
        bound = _as_datetime_bound(v, upper=False)
        if isinstance(bound, pd.Timestamp):
            if _date_only(v):
                return ds.dt.normalize() != bound.normalize()
            return ds != bound
    return s.astype(str) != str(v)


def _filter_cmp_ord(op, *, upper: bool = False):
    def _inner(s: pd.Series, v: Any) -> pd.Series:
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return op(_coerce_numeric(s), v)
        num_col = _numeric_series_for_cmp(s)
        if num_col is not None:
            bound = _parse_number(v)
            if bound is not None:
                return op(num_col, bound)
            raise ValueError(f"数值列不支持与非数值 {v!r} 做大小比较")
        ds = _as_datetime_series(s)
        if ds is not None:
            bound = _as_datetime_bound(v, upper=upper)
            if isinstance(bound, pd.Timestamp):
                return op(ds, bound)
        return op(s.astype(str), str(v))
    return _inner


def _filter_in(s: pd.Series, v: Any) -> pd.Series:
    vals = v if isinstance(v, list) else [v]
    num_col = _numeric_series_for_cmp(s)
    if num_col is not None:
        vals = [n if (n := _parse_number(x)) is not None else x for x in vals]
        return num_col.isin(vals)
    return s.isin(vals)


_FILTER_OPS: dict[str, Any] = {
    "eq": _filter_cmp_eq,
    "ne": _filter_cmp_ne,
    "gt": _filter_cmp_ord(_operator.gt),
    "ge": _filter_cmp_ord(_operator.ge),
    "lt": _filter_cmp_ord(_operator.lt),
    "le": _filter_cmp_ord(_operator.le, upper=True),
    "contains": _filter_contains_literal,
    "not_contains": lambda s, v: ~_filter_contains_literal(s, v),
    "regex": _filter_contains_regex,
    "not_regex": lambda s, v: ~_filter_contains_regex(s, v),
    "in": _filter_in,
    "not_in": lambda s, v: ~_filter_in(s, v),
    "between": _filter_between,
    "isnull": lambda s, v: s.isna() | (s == ""),
    "notnull": lambda s, v: ~(s.isna() | (s == "")),
    "startswith": lambda s, v: s.astype(str).str.startswith(str(v), na=False),
    "endswith": lambda s, v: s.astype(str).str.endswith(str(v), na=False),
}

_FILTER_OP_ALIASES: dict[str, str] = {
    "==": "eq",
    "=": "eq",
    "equals": "eq",
    "equal": "eq",
    "!=": "ne",
    "<>": "ne",
    "not_equals": "ne",
    ">": "gt",
    ">=": "ge",
    "<": "lt",
    "<=": "le",
    "does_not_contain": "not_contains",
    "not_contains_regex": "not_regex",
    "not_in_list": "not_in",
    "not": "ne",
    "null": "isnull",
    "is_null": "isnull",
    "empty": "isnull",
    "not_null": "notnull",
    "is_not_null": "notnull",
    "not_empty": "notnull",
}


def _maybe_json(value: Any) -> Any:
    """模型常把嵌套参数序列化成 JSON 字符串；可解析时还原为对象/数组。"""
    if isinstance(value, str):
        text = value.strip()
        if text[:1] in ("{", "["):
            try:
                return json.loads(text)
            except (ValueError, TypeError):
                return value
    return value


def _normalize_conditions(
    df: "pd.DataFrame",
    *,
    column: str | None,
    operator: str | None,
    value: Any,
    conditions: list[dict[str, Any]] | None,
    logic: str,
    require: bool,
) -> tuple[list[dict[str, Any]] | None, ToolResult | None]:
    """兼容单条件/多条件，返回 (cond_list, err)；require=False 时允许无条件。"""
    conditions = _maybe_json(conditions)
    # 显式传入的 conditions（含空数组）优先：[] 表示全表，dict 视作单条件。
    if isinstance(conditions, dict):
        conditions = [conditions]
    if conditions is not None and not isinstance(conditions, list):
        return None, _error_payload_result(
            {"error": "conditions 必须是条件对象数组（或单条件对象）"},
            code="INVALID_ARGS",
        )
    if conditions is not None:
        cond_list = conditions
    elif column is not None and operator is not None:
        cond_list = [{"column": column, "operator": operator, "value": _maybe_json(value)}]
    elif require:
        return None, _error_payload_result(
            {"error": "请提供 column/operator/value 单条件，或 conditions 多条件数组"},
            code="INVALID_ARGS",
        )
    else:
        cond_list = []
    # 条件项字段别名归一：模型常缩写为 op/col
    for cond in cond_list:
        if isinstance(cond, dict):
            extras = set(cond) - {"column", "col", "operator", "op", "value"}
            if extras:
                return None, error_result(f"条件不支持字段 {sorted(extras)}；请使用 column/operator/value", code="INVALID_ARGS")
            if "operator" not in cond and "op" in cond:
                cond["operator"] = cond.pop("op")
            if "column" not in cond and "col" in cond:
                cond["column"] = cond.pop("col")
    if logic not in ("and", "or", "not"):
        return None, _error_payload_result(
            {
                "error": f"不支持的逻辑运算符 '{logic}'，支持: and, or, not",
            },
            code="INVALID_ARGS",
        )
    if logic == "not" and len(cond_list) != 1:
        return None, _error_payload_result(
            {
                "error": "logic='not' 表示对单个条件整体取反，仅接受恰好一个条件；"
                "多条件排除请用 operator=ne/not_contains/not_in/not_regex 逐条件表达",
            },
            code="INVALID_ARGS",
        )
    return cond_list, None


def _build_condition_mask(
    df: "pd.DataFrame",
    cond_list: list[dict[str, Any]],
    logic: str,
) -> tuple[Any, ToolResult | None]:
    """逐条件构建并组合 mask；cond_list 为空时 mask=None（表示全表）。"""
    if not cond_list:
        return None, None
    masks = []
    for cond in cond_list:
        if not isinstance(cond, dict):
            return None, _error_payload_result(
                {"error": f"条件项必须是对象 {{column, operator, value}}，收到: {cond!r}"},
                code="INVALID_ARGS",
            )
        col = cond.get("column")
        op = cond.get("operator")
        val = cond.get("value")
        if col is None:
            return None, _error_payload_result(
                {"error": f"条件项缺少 'column' 字段（收到键: {sorted(cond)}）"},
                code="INVALID_ARGS",
            )
        if op is None:
            return None, _error_payload_result(
                {"error": f"条件项缺少 'operator' 字段（收到键: {sorted(cond)}）"},
                code="INVALID_ARGS",
            )
        if isinstance(op, str):
            key = op.strip()
            op = _FILTER_OP_ALIASES.get(key) or _FILTER_OP_ALIASES.get(key.lower()) or key.lower()
        if col not in df.columns:
            return None, _error_payload_result(
                {"error": f"列 '{col}' 不存在，可用列: {[str(c) for c in df.columns]}"},
                code="NOT_FOUND",
            )
        if op not in _FILTER_OPS:
            return None, _error_payload_result(
                {"error": f"不支持的运算符 '{op}'，支持: {list(_FILTER_OPS.keys())}"},
                code="INVALID_ARGS",
            )
        try:
            masks.append(_FILTER_OPS[op](df[col], _maybe_json(val)))
        except ValueError as exc:
            return None, _error_payload_result({"error": str(exc), "code": "INVALID_ARGS"}, code="INVALID_ARGS")

    if logic == "not":
        if len(masks) != 1:
            return None, _error_payload_result(
                {"error": "logic='not' 仅接受恰好一个条件"},
                code="INVALID_ARGS",
            )
        return ~masks[0], None
    if logic == "and":
        return functools.reduce(lambda a, b: a & b, masks), None
    return functools.reduce(lambda a, b: a | b, masks), None


def filter_data(
    file_path: str,
    column: str | None = None,
    operator: str | None = None,
    value: Any = None,
    sheet_name: str | None = None,
    header_row: int | None = None,
    columns: list[str] | None = None,
    conditions: list[dict[str, Any]] | None = None,
    logic: str = "and",
    max_rows: int | None = None,
    sort_by: str | None = None,
    ascending: bool = True,
    limit: int | None = None,
    expected_version: str | None = None,
) -> ToolResult:
    """根据条件过滤 Excel 数据行并可选排序，支持单条件和多条件 AND/OR 组合。

    Args:
        file_path: Excel 文件路径。
        column: 要过滤的列名（单条件模式）。
        operator: 比较运算符（单条件模式）。支持：
            eq/ne/gt/ge/lt/le/contains/not_contains/regex/not_regex/in/not_in/between/isnull/notnull/startswith/endswith
        value: 比较值（单条件模式）。
        sheet_name: 工作表名称，默认第一个。
        header_row: 列头所在行号（Excel 行号，1-based），默认自动检测。
        columns: 只返回指定列（投影），默认返回全部列。
        conditions: 多条件数组，每个元素为 {"column": str, "operator": str, "value": Any}。
        logic: 多条件组合方式，"and"（默认）/ "or" / "not"（对单个条件整体取反）。
        max_rows: 最多返回的数据行数，默认返回全部。
        sort_by: 排序列名，默认不排序。
        ascending: 排序方向，默认升序。
        limit: 排序后限制返回行数，默认返回全部。

    Returns:
        ToolResult（value 含过滤结果，model_text 为短摘要）。
    """
    ctx, err = _load_df_for_tool(
        file_path, sheet_name, header_row,
        column_hints=_collect_column_hints(column=column, conditions=conditions, columns=columns, sort_by=sort_by),
        expected_version=expected_version,
    )
    if err is not None:
        return err
    assert ctx is not None
    df = ctx["df"]
    sheet_name = ctx["sheet_name"]
    effective_header = ctx["effective_header"]
    snap = ctx["snap"]
    safe_path = ctx["safe_path"]
    rel_path = ctx["rel_path"]
    bound_version = ctx["bound_version"]

    cond_list, cond_err = _normalize_conditions(
        df, column=column, operator=operator, value=value,
        conditions=conditions, logic=logic, require=True,
    )
    if cond_err is not None:
        return cond_err
    assert cond_list is not None

    combined_mask, mask_err = _build_condition_mask(df, cond_list, logic)
    if mask_err is not None:
        return mask_err

    filtered = df[combined_mask] if combined_mask is not None else df

    # 先筛选/排序/limit，再投影。排序列按原表判断。
    if sort_by is not None:
        if sort_by not in df.columns:
            return _error_payload_result(
                {"error": f"排序列 '{sort_by}' 不存在，可用列: {[str(c) for c in df.columns]}"},
                code="NOT_FOUND",
            )
        sort_key = _coerce_numeric(filtered[sort_by])
        filtered = filtered.assign(**{"__sort_key__": sort_key}).sort_values(
            by="__sort_key__", ascending=ascending, na_position="last", kind="mergesort"
        ).drop(columns=["__sort_key__"])

    total_filtered = len(filtered)
    if limit is not None and limit > 0:
        filtered = filtered.head(limit)
    if max_rows is not None and max_rows > 0:
        filtered = filtered.head(max_rows)

    missing_cols: list[str] = []
    if columns:
        valid_cols = [c for c in columns if c in filtered.columns]
        missing_cols = [c for c in columns if c not in df.columns]
        filtered = filtered[valid_cols]

    result: dict[str, Any] = {
        "file": rel_path,
        "filters": cond_list,
        "logic": logic,
        "original_rows": len(df),
        "filtered_rows": total_filtered,
        "returned_rows": len(filtered),
        "columns": [str(c) for c in filtered.columns],
        "source_columns": [str(c) for c in df.columns],
        "data": _df_to_compact_records(filtered),
        "source_rows": [_excel_source_row(effective_header, idx) for idx in filtered.index],
    }
    if total_filtered > len(filtered):
        result["truncated"] = True
        result["note"] = f"结果已截断，共 {total_filtered} 条匹配，返回前 {len(filtered)} 条"
    if missing_cols:
        result["missing_columns"] = missing_cols
    if sheet_name:
        result["resolved_sheet"] = sheet_name
    _attach_sheet_disambiguation(result, ctx.get("sheet_disambiguation"))

    return _finalize_filter_data_result(
        result,
        rel_path=rel_path,
        file_path=safe_path,
        sheet_name=sheet_name,
        content_version=bound_version,
        header_row=_to_public_header_row(effective_header),
        snapshot=snap,
    )


_NUMERIC_AGG_FUNCS: frozenset[str] = frozenset({"sum", "mean", "min", "max", "median", "std"})

_AGGREGATE_FUNCS: dict[str, str] = {
    "sum": "sum",
    "count": "count",
    "mean": "mean",
    "avg": "mean",
    "average": "mean",
    "min": "min",
    "max": "max",
    "median": "median",
    "std": "std",
    "nunique": "nunique",
    "distinct": "nunique",
    "first": "first",
    "last": "last",
}

_AGG_LIMIT_DEFAULT = 200
_DISTINCT_TOP_DEFAULT = 50


def _py_scalar(value: Any) -> Any:
    """numpy/pandas 标量转 JSON 友好的 Python 原生值。"""
    if value is None or pd.isna(value):
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if hasattr(value, "item"):
        try:
            return value.item()
        except (ValueError, TypeError, AttributeError):
            pass
    return value


# group_by 派生键支持的日期粒度：{"column": "日期", "transform": "month"}
_GROUP_TRANSFORMS: dict[str, Any] = {
    "year": lambda ds: ds.dt.year,
    "quarter": lambda ds: ds.dt.to_period("Q").astype(str),
    "month": lambda ds: ds.dt.month,
    "year_month": lambda ds: ds.dt.to_period("M").astype(str),
    "date": lambda ds: ds.dt.strftime("%Y-%m-%d"),
    "day": lambda ds: ds.dt.strftime("%Y-%m-%d"),
    "week": lambda ds: ds.dt.isocalendar().week,
    "hour": lambda ds: ds.dt.hour,
}


def _normalize_group_keys(group_by: Any, columns: Any) -> tuple[list[str] | None, list[tuple[str, str, str]] | None, ToolResult | None]:
    """归一 group_by：列名字符串或 {"column": X, "transform": 日期粒度} 派生键。

    返回 (keys, derived_specs, err)；derived_specs 为 (输出列, 源列, transform)。
    """
    group_by = _maybe_json(group_by)
    if isinstance(group_by, str):
        raw_keys = [group_by]
    elif isinstance(group_by, dict):
        raw_keys = [group_by]
    else:
        raw_keys = list(group_by or [])
    keys: list[str] = []
    derived: list[tuple[str, str, str]] = []
    available = {str(c) for c in columns}
    for item in raw_keys:
        if not isinstance(item, dict):
            keys.append(str(item))
            continue
        src = str(item.get("column") or "").strip()
        transform = str(item.get("transform") or item.get("part") or "").strip().lower()
        if not transform:
            # {"column": X} 无 transform：退化为普通列名分组
            keys.append(src)
            continue
        if transform not in _GROUP_TRANSFORMS:
            return None, None, _error_payload_result(
                {"error": f"派生分组键 transform '{transform}' 不支持，支持: {sorted(_GROUP_TRANSFORMS)}"},
                code="INVALID_ARGS",
            )
        out_col = f"{src}__{transform}"
        derived.append((out_col, src, transform))
        keys.append(out_col)
    derived_out = {d[0] for d in derived}
    missing = [k for k in keys if k not in available and k not in derived_out]
    missing += [src for _, src, _ in derived if src not in available]
    if missing:
        return None, None, _error_payload_result(
            {"error": f"分组列 {missing} 不存在，可用列: {[str(c) for c in columns]}"},
            code="NOT_FOUND",
        )
    return keys, derived, None


def _materialize_derived_keys(
    work: "pd.DataFrame",
    derived_specs: list[tuple[str, str, str]],
) -> tuple["pd.DataFrame | None", ToolResult | None]:
    """物化派生分组列（按输出列名去重）。"""
    seen: set[str] = set()
    for out_col, src_col, transform in derived_specs:
        if out_col in seen:
            continue
        seen.add(out_col)
        ds = _as_datetime_series(work[src_col])
        if ds is None:
            return None, _error_payload_result(
                {"error": f"列 '{src_col}' 无法解析为日期，不能应用 transform='{transform}'"},
                code="INVALID_ARGS",
            )
        work = work.assign(**{out_col: _GROUP_TRANSFORMS[transform](ds)})
    return work, None


def _normalize_aggs(aggregations: Any) -> tuple[list[tuple[str, str, str]] | None, ToolResult | None]:
    """把 aggregations 规范成 [(输出列名, 源列或 "*", aggfunc)]。

    接受两种写法：
    - {"金额": "sum", "订单号": ["count", "nunique"]}
    - [{"column": "金额", "func": "sum"}, {"column": "金额", "funcs": ["mean"]}]
    "*" 列表示行计数。
    """
    aggregations = _maybe_json(aggregations)
    entries: list[tuple[str, str, str]] = []

    def _add(col: Any, funcs: Any) -> ToolResult | None:
        col_name = str(col).strip()
        if not col_name:
            return _error_payload_result(
                {"error": "aggregations 的列名不能为空"}, code="INVALID_ARGS",
            )
        func_list = funcs if isinstance(funcs, (list, tuple)) else [funcs]
        for f in func_list:
            key = str(f).strip().lower()
            if key not in _AGGREGATE_FUNCS:
                return _error_payload_result(
                    {"error": f"不支持的聚合函数 '{f}'，支持: {sorted(_AGGREGATE_FUNCS.keys())}"},
                    code="INVALID_ARGS",
                )
            canon = _AGGREGATE_FUNCS[key]
            out = "count" if col_name == "*" else f"{col_name}_{canon}"
            entries.append((out, col_name, canon))
        return None

    if isinstance(aggregations, dict):
        for col, funcs in aggregations.items():
            err = _add(col, funcs)
            if err is not None:
                return None, err
    elif isinstance(aggregations, list):
        for item in aggregations:
            if not isinstance(item, dict):
                return None, _error_payload_result(
                    {"error": "aggregations 列表元素必须是对象"}, code="INVALID_ARGS",
                )
            funcs = item.get("funcs") or item.get("func") or item.get("agg")
            if funcs is None:
                return None, _error_payload_result(
                    {"error": "aggregations 元素缺少 func/funcs"}, code="INVALID_ARGS",
                )
            err = _add(item.get("column") or item.get("col"), funcs)
            if err is not None:
                return None, err
    elif aggregations in (None, "", [], {}):
        return [], None
    else:
        return None, _error_payload_result(
            {"error": "aggregations 必须是 {列名: 函数} 对象或 [{column, func}] 数组"},
            code="INVALID_ARGS",
        )
    return entries, None


def _normalize_join(
    join: Any,
    df: "pd.DataFrame",
) -> tuple[dict[str, Any] | None, "ToolResult | None"]:
    """归一 aggregate 的 join/lookup 参数（VLOOKUP 语义：左连接、右表按键去重首值）。

    返回 (spec, err)。spec 含 right_file/right_sheet/left_on/right_on/columns。
    """
    join = _maybe_json(join)
    if join is None:
        return None, None
    if not isinstance(join, dict):
        return None, _error_payload_result(
            {"error": "join 必须是对象：{sheet|file_path, on|left_on+right_on, columns?}"},
            code="INVALID_ARGS",
        )
    on = join.get("on")
    left_on = join.get("left_on") or join.get("leftOn") or on
    right_on = join.get("right_on") or join.get("rightOn") or on
    if not left_on or not right_on:
        return None, _error_payload_result(
            {"error": "join 需要连接键：on（同名列）或 left_on+right_on（异名列）"},
            code="INVALID_ARGS",
        )
    left_keys = [str(item) for item in left_on] if isinstance(left_on, (list, tuple)) else [str(left_on)]
    right_keys = [str(item) for item in right_on] if isinstance(right_on, (list, tuple)) else [str(right_on)]
    if len(left_keys) != len(right_keys) or not left_keys or any(not item for item in left_keys + right_keys):
        return None, _error_payload_result(
            {"error": "join 连接键必须是同长度的非空列名或列名数组"}, code="INVALID_ARGS",
        )
    missing_left = [key for key in left_keys if key not in df.columns]
    if missing_left:
        return None, _error_payload_result(
            {"error": f"连接列 {missing_left!r} 不在左表，可用列: {[str(c) for c in df.columns]}"},
            code="NOT_FOUND",
        )
    how = str(join.get("how") or "left").strip().lower().replace("-", "_")
    aliases = {"full": "outer", "full_outer": "outer", "anti": "left_anti", "leftanti": "left_anti", "rightanti": "right_anti"}
    how = aliases.get(how, how)
    if how not in {"left", "inner", "right", "outer", "left_anti", "right_anti"}:
        return None, _error_payload_result(
            {"error": "join.how 支持 left/inner/right/outer/left_anti/right_anti"},
            code="INVALID_ARGS",
        )
    columns = _maybe_json(join.get("columns"))
    if columns is not None and not isinstance(columns, list):
        return None, _error_payload_result(
            {"error": "join.columns 必须是列名数组（缺省=右表全部非键列）"},
            code="INVALID_ARGS",
        )
    return {
        "right_file": join.get("file_path") or join.get("path"),
        "right_sheet": join.get("sheet") or join.get("sheet_name"),
        "right_header": join.get("header_row"),
        "expected_version": join.get("expected_version"),
        "left_on": left_keys,
        "right_on": right_keys,
        "how": how,
        "columns": [str(c) for c in columns] if columns else None,
    }, None


def _apply_join(
    df: "pd.DataFrame",
    spec: dict[str, Any],
    default_file: str,
    *,
    source_version: str | None = None,
    right_frame: Any = None,
) -> tuple["pd.DataFrame | None", int, "ToolResult | None"]:
    """按归一 spec 执行左连接；返回 (merged_df, unmatched_count, err)。"""
    right_file = spec["right_file"] or default_file
    if right_frame is None:
        same_file = _get_guard().resolve_and_validate(right_file) == _get_guard().resolve_and_validate(default_file)
        rctx, rerr = _load_df_for_tool(right_file, spec["right_sheet"], spec["right_header"],
                                     expected_version=spec.get("expected_version") or (source_version if same_file else None))
        if rerr is not None:
            return None, 0, rerr
        right = rctx["df"]
        spec["source"] = {"file_path": rctx["rel_path"], "sheet": rctx["sheet_name"], "content_version": rctx["bound_version"]}
    else:
        right = right_frame
        spec["source"] = {"file_path": default_file, "sheet": spec["right_sheet"], "version_scope": "same_atomic_operations"}
    right_keys = list(spec["right_on"])
    left_keys = list(spec["left_on"])
    missing_right = [key for key in right_keys if key not in right.columns]
    if missing_right:
        return None, 0, _error_payload_result(
            {"error": f"连接列 {missing_right!r} 不在右表，可用列: {[str(c) for c in right.columns]}"},
            code="NOT_FOUND",
        )
    bring = spec["columns"]
    if bring is not None:
        missing = [c for c in bring if c not in right.columns]
        if missing:
            return None, 0, _error_payload_result(
                {"error": f"join.columns {missing} 不在右表，可用列: {[str(c) for c in right.columns]}"},
                code="NOT_FOUND",
            )
        keep = right_keys + [c for c in bring if c not in right_keys]
    else:
        keep = right_keys + [c for c in right.columns if c not in right_keys]
    # Preserve legacy VLOOKUP first-match behavior only for left joins.  Other
    # join types retain multiplicity, as pandas/SQL users expect.
    how = str(spec.get("how") or "left")
    if how == "left":
        right = right[keep].drop_duplicates(subset=right_keys, keep="first")
    else:
        right = right[keep]
    indicator_name = "__excelmanus_merge__"
    pandas_how = "left" if how == "left_anti" else "outer" if how == "right_anti" else how
    merged = df.merge(
        right,
        how=pandas_how,
        left_on=left_keys,
        right_on=right_keys,
        suffixes=("", "__lookup"),
        indicator=indicator_name,
    )
    if how == "left_anti":
        unmatched = int(merged[indicator_name].eq("left_only").sum())
        merged = merged.loc[merged[indicator_name].eq("left_only"), df.columns]
    elif how == "right_anti":
        unmatched = int(merged[indicator_name].eq("right_only").sum())
        right_only = merged[indicator_name].eq("right_only")
        # Right anti returns the right relation with its join columns and
        # projected columns; remove left-only columns where pandas created NaN.
        merged = merged.loc[right_only]
    else:
        unmatched = int(merged[indicator_name].eq("left_only").sum())
        merged = merged.drop(columns=[indicator_name])
    return merged, unmatched, None


def aggregate_data(
    file_path: str,
    group_by: Any = None,
    aggregations: Any = None,
    sheet_name: str | None = None,
    header_row: int | None = None,
    column: str | None = None,
    operator: str | None = None,
    value: Any = None,
    conditions: list[dict[str, Any]] | None = None,
    logic: str = "and",
    sort_by: str | None = None,
    ascending: bool = False,
    limit: int | None = None,
    join: Any = None,
    expected_version: str | None = None,
) -> ToolResult:
    """分组聚合：对满足条件的行按 group_by 汇总，返回每组聚合值（不回明细行）。

    Args:
        file_path: Excel 文件路径。
        group_by: 分组列名或列名数组；也支持日期派生键对象
            {"column": "日期", "transform": "year|quarter|month|year_month|date|week|hour"}，
            如按月份聚合传 {"column": "下单日期", "transform": "year_month"}；
            不传则对全部匹配行做整体聚合。
        aggregations: {列名: 函数或函数数组}，函数支持
            sum/count/mean/min/max/median/std/nunique/first/last；"*" 列表示行计数。
            不传时默认按组计数（{"*": "count"}）。
        conditions / column+operator+value / logic: 聚合前预筛（与 filter 同语法）。
        sort_by: 结果排序列（分组键或聚合输出列名），默认按第一个聚合列降序。
        ascending: 排序方向，默认降序（Top-N 场景）。
        limit: 最多返回的组数，默认 200。
        join: 跨表连接（VLOOKUP 语义），对象
            {"sheet": 右表名（同簿）或 "file_path": 另一文件,
             "on": 同名键 或 "left_on"+"right_on": 异名键,
             "columns": [要带来的列]（缺省=右表全部非键列）}。
            右表按键去重取首行，未匹配行保留为 NaN；连接列可参与
            group_by/aggregations/conditions。
    """
    ctx, err = _load_df_for_tool(
        file_path, sheet_name, header_row,
        column_hints=_collect_column_hints(
            group_by=group_by, aggregations=aggregations, column=column,
            conditions=conditions, sort_by=sort_by, join=join,
        ),
        expected_version=expected_version,
    )
    if err is not None:
        return err
    assert ctx is not None
    df = ctx["df"]
    sheet_name = ctx["sheet_name"]
    snap = ctx["snap"]
    rel_path = ctx["rel_path"]
    bound_version = ctx["bound_version"]

    left_rows = len(df)
    join_spec, join_err = _normalize_join(join, df)
    if join_err is not None:
        return join_err
    unmatched = 0
    if join_spec is not None:
        merged, unmatched, merge_err = _apply_join(df, join_spec, file_path, source_version=bound_version)
        if merge_err is not None:
            return merge_err
        assert merged is not None
        df = merged

    group_by = _maybe_json(group_by)
    keys, derived_specs, keys_err = _normalize_group_keys(group_by, df.columns)
    if keys_err is not None:
        return keys_err
    assert keys is not None and derived_specs is not None

    agg_entries, agg_err = _normalize_aggs(aggregations)
    if agg_err is not None:
        return agg_err
    if not agg_entries:
        agg_entries = [("count", "*", "count")]
    else:
        bad_cols = [c for _, c, _ in agg_entries if c != "*" and c not in df.columns]
        if bad_cols:
            return _error_payload_result(
                {"error": f"聚合列 {sorted(set(bad_cols))} 不存在，可用列: {[str(c) for c in df.columns]}"},
                code="NOT_FOUND",
            )

    cond_list, cond_err = _normalize_conditions(
        df, column=column, operator=operator, value=value,
        conditions=conditions, logic=logic, require=False,
    )
    if cond_err is not None:
        return cond_err
    assert cond_list is not None
    combined_mask, mask_err = _build_condition_mask(df, cond_list, logic)
    if mask_err is not None:
        return mask_err
    filtered = df[combined_mask] if combined_mask is not None else df
    matched_rows = len(filtered)

    # 数值型聚合函数的源列先做数值转换（文本存的金额/千分位，否则 sum 变拼接）。
    work = filtered
    coerced: dict[str, Any] = {}
    for _, col, func in agg_entries:
        if col == "*" or func not in _NUMERIC_AGG_FUNCS or col in coerced:
            continue
        series = work[col]
        if pd.api.types.is_numeric_dtype(series):
            continue
        converted = _coerce_numeric(series)
        non_null = int(series.notna().sum())
        if non_null and int(converted.notna().sum()) >= non_null * 0.5:
            coerced[col] = converted
    if coerced:
        work = work.assign(**coerced)

    for out_col, src_col, transform in derived_specs:
        ds = _as_datetime_series(work[src_col])
        if ds is None:
            return _error_payload_result(
                {"error": f"分组列 '{src_col}' 无法解析为日期，不能应用 transform='{transform}'"},
                code="INVALID_ARGS",
            )
        work = work.assign(**{out_col: _GROUP_TRANSFORMS[transform](ds)})

    if keys:
        # "*" 行计数用哨兵列求和（含 NaN 组键的行也计入）。
        if any(c == "*" for _, c, _ in agg_entries):
            work = work.assign(**{"__one__": 1})
        named = {
            out: ("__one__" if col == "*" else col, "sum" if col == "*" else func)
            for out, col, func in agg_entries
        }
        try:
            result_df = work.groupby(keys, dropna=False, sort=False).agg(**named).reset_index()
        except (TypeError, ValueError) as exc:
            return _error_payload_result(
                {"error": f"聚合失败: {exc}（列类型可能不支持该函数，可先 filter 检查数据）"},
                code="INVALID_ARGS",
            )
        result_df = result_df[keys + [c for c in result_df.columns if c not in keys]]
    else:
        row: dict[str, Any] = {}
        for out_name, col, func in agg_entries:
            if col == "*":
                row[out_name] = len(filtered)
                continue
            series = work[col]
            try:
                row[out_name] = getattr(series, func)()
            except (TypeError, ValueError):
                row[out_name] = getattr(_coerce_numeric(series), func)()
        result_df = pd.DataFrame([row])

    total_groups = len(result_df)
    if sort_by is not None:
        if sort_by not in result_df.columns:
            # 源列名宽容映射：聚合输出列形如 "金额_sum"，模型常直接传源列名。
            src_matches = [out for out, col, _ in agg_entries if col == sort_by]
            # 派生分组键同理：sort_by=源列名时映射到 "源列__transform" 输出列。
            if not src_matches:
                src_matches = [
                    out for out, src, _ in derived_specs if src == sort_by
                ]
            if len(src_matches) == 1:
                sort_by = src_matches[0]
            elif src_matches:
                return _error_payload_result(
                    {"error": f"排序列 '{sort_by}' 对应多个聚合输出 {src_matches}，请指定其一", "code": "INVALID_ARGS"},
                    code="INVALID_ARGS",
                )
            else:
                return _error_payload_result(
                    {"error": f"排序列 '{sort_by}' 不在结果中（须为分组键或聚合输出列），可用列: {[str(c) for c in result_df.columns]}"},
                    code="NOT_FOUND",
                )
        result_df = result_df.sort_values(
            by=sort_by, ascending=ascending, na_position="last", kind="mergesort",
        ).reset_index(drop=True)
    elif total_groups > 1:
        first_agg_col = agg_entries[0][0]
        if first_agg_col in result_df.columns:
            result_df = result_df.sort_values(
                by=first_agg_col, ascending=False, na_position="last", kind="mergesort",
            ).reset_index(drop=True)

    cap = limit if isinstance(limit, int) and limit > 0 else _AGG_LIMIT_DEFAULT
    truncated = total_groups > cap
    result_df = result_df.head(cap)

    groups = _df_to_compact_records(result_df)
    out_columns = [str(c) for c in result_df.columns]
    result: dict[str, Any] = {
        "file": rel_path,
        "mode": "aggregate",
        "group_by": keys,
        "aggregations": [{"column": c, "func": f, "output": o} for o, c, f in agg_entries],
        "conditions": cond_list,
        "logic": logic,
        "original_rows": left_rows,
        "matched_rows": matched_rows,
        "group_count": total_groups,
        "returned_groups": len(groups),
        "columns": out_columns,
        "groups": groups,
        "data": groups,
        "original_rows_note": "groups 为全量聚合结果（除截断外），不是采样",
    }
    if truncated:
        result["truncated"] = True
        result["note"] = f"共 {total_groups} 组，返回前 {len(groups)} 组（limit={cap}）"
    if join_spec is not None:
        result["join"] = {
            "left_on": join_spec["left_on"],
            "right_on": join_spec["right_on"],
            "source": join_spec.get("source"),
            "right_sheet": join_spec["right_sheet"],
            "unmatched_rows": unmatched,
        }
        if unmatched:
            result.setdefault("warnings", []).append(
                f"join 有 {unmatched}/{left_rows} 行未匹配到右表（连接列值在右表不存在），对应列为空"
            )
    if sheet_name:
        result["resolved_sheet"] = sheet_name
    _attach_sheet_disambiguation(result, ctx.get("sheet_disambiguation"))
    return _finalize_aggregate_result(
        result,
        rel_path=rel_path,
        sheet_name=sheet_name,
        content_version=bound_version,
        matched_rows=matched_rows,
        snapshot=snap,
    )


def _finalize_aggregate_result(
    result: dict[str, Any],
    *,
    rel_path: str,
    sheet_name: str | None,
    content_version: str | None,
    matched_rows: int,
    snapshot: Any,
) -> ToolResult:
    groups = result.get("groups") or []
    is_truncated = bool(result.get("truncated"))
    total_groups = int(result.get("group_count") or len(groups))
    _attach_file_meta(result, rel_path=rel_path, content_version=content_version)
    if snapshot is not None:
        from excelmanus.workbook.snapshot import Coverage, apply_read_contract

        content_version = snapshot.content_version
        apply_read_contract(
            result,
            snapshot=snapshot,
            result_kind="records",
            sheet=sheet_name,
            coverage=Coverage(
                kind="truncated" if is_truncated else "complete",
                returned_rows=len(groups),
                total_rows=total_groups,
            ),
            formulas_uncached="unknown",
            meta_kind="aggregate",
        )
    else:
        raise RuntimeError("aggregate finalize 必须提供 WorkbookSnapshot")

    aggs_desc = ", ".join(
        f"{e['column']}→{e['func']}" for e in (result.get("aggregations") or [])
    )
    group_desc = "+".join(str(k) for k in (result.get("group_by") or [])) or "全表"
    lines = [
        f"文件 {result.get('file', rel_path)}：原 {result.get('original_rows')} 行，"
        f"匹配 {matched_rows} 行，按 {group_desc} 聚合出 {total_groups} 组。",
        f"聚合：{aggs_desc}",
    ]
    if result.get("conditions"):
        lines.append(
            f"预筛（{result.get('logic', 'and')}）："
            f"{json.dumps(result['conditions'], ensure_ascii=False, default=str)}"
        )
    if is_truncated and result.get("note"):
        lines.append(f"⚠️ {result['note']}")
    if result.get("sheet_disambiguation_warning"):
        lines.append(f"⚠️ {result['sheet_disambiguation_warning']}")
    if groups:
        budget = _RANGE_PROJECT_MAX_CHARS
        shown: list[Any] = []
        dumped = ""
        for item in groups:
            candidate = shown + [item]
            dumped = json.dumps(candidate, ensure_ascii=False, default=str)
            if len(dumped) > budget and shown:
                break
            shown = candidate
        prefix = "groups" if len(shown) == len(groups) else f"groups 投影 {len(shown)}/{len(groups)}"
        lines.append(f"{prefix}：{json.dumps(shown, ensure_ascii=False, default=str)}")
        if len(shown) < len(groups):
            lines.append("其余组在 value.groups；超预算时由 spill 外置。")
    return ToolResult(
        success=True,
        model_text="\n".join(lines),
        value=result,
        ui_meta=ToolUiMeta(
            files=[rel_path] if rel_path else [],
            preview=_preview_from_records(
                result.get("columns") or [],
                groups,
                total_rows=total_groups,
                truncated=is_truncated,
                sheet=sheet_name or "",
            ),
            content_version=content_version,
        ),
        truncated=is_truncated,
        coverage={
            "kind": "truncated" if is_truncated else "complete",
            "rows": len(groups),
            "original_rows": result.get("original_rows"),
            "filtered_rows": matched_rows,
        },
        )


def _as_str_list(value: Any) -> list[str]:
    value = _maybe_json(value)
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip() != ""]
    return [str(value)]


def _flatten_pivot_column(col: Any) -> str:
    if isinstance(col, tuple):
        parts = [str(item) for item in col if item is not None and str(item) != ""]
        return "_".join(parts) or "value"
    return str(col)


def _pivot_frame(
    df: "pd.DataFrame",
    *,
    index: Any,
    columns: Any,
    values: Any,
    aggfunc: str = "sum",
    margins: Any = False,
    margins_name: Any = "合计",
) -> tuple["pd.DataFrame | None", str | None]:
    index_cols = _as_str_list(index)
    col_dims = _as_str_list(columns)
    val_cols = _as_str_list(values)
    if not index_cols or not col_dims or not val_cols:
        return None, "pivot 需要 index、columns、values"
    missing = [c for c in [*index_cols, *col_dims, *val_cols] if c not in df.columns]
    if missing:
        return None, f"透视列不存在: {missing}；可用列: {[str(c) for c in df.columns]}"
    func = str(aggfunc or "sum").lower()
    allowed = {"sum", "count", "mean", "min", "max", "median", "nunique", "first", "last"}
    if func not in allowed:
        return None, f"不支持的 aggfunc={aggfunc}，可用: {sorted(allowed)}"
    use_margins = margins is True or str(margins).strip().lower() in {"1", "true", "yes", "on"}
    total_label = str(margins_name or "合计")
    work = df
    for col in val_cols:
        if func in {"sum", "mean", "min", "max", "median"} and not pd.api.types.is_numeric_dtype(work[col]):
            work = work.assign(**{col: _coerce_numeric(work[col])})
    try:
        table = pd.pivot_table(
            work,
            index=index_cols if len(index_cols) > 1 else index_cols[0],
            columns=col_dims if len(col_dims) > 1 else col_dims[0],
            values=val_cols if len(val_cols) > 1 else val_cols[0],
            aggfunc="size" if func == "count" else func,
            fill_value=0,
            margins=use_margins,
            margins_name=total_label,
        )
    except Exception as exc:
        return None, f"透视失败: {exc}"
    table = table.reset_index()
    table.columns = [_flatten_pivot_column(c) for c in table.columns]
    return table, None


def dataframe_from_worksheet(ws: Any, header_row: int | None = 1) -> "pd.DataFrame":
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return pd.DataFrame()
    hdr_idx = max(int(header_row or 1) - 1, 0)
    if hdr_idx >= len(rows):
        return pd.DataFrame()
    headers: list[str] = []
    seen: dict[str, int] = {}
    for i, cell in enumerate(rows[hdr_idx]):
        name = str(cell).strip() if cell is not None else f"col{i + 1}"
        if not name:
            name = f"col{i + 1}"
        if name in seen:
            seen[name] += 1
            name = f"{name}_{seen[name]}"
        else:
            seen[name] = 0
        headers.append(name)
    body = rows[hdr_idx + 1 :]
    data = []
    width = len(headers)
    for row in body:
        cells = list(row[:width]) + [None] * max(0, width - len(row))
        data.append(cells[:width])
    return pd.DataFrame(data, columns=headers)


def write_dataframe_to_worksheet(
    ws: Any,
    df: "pd.DataFrame",
    *,
    start_row: int = 1,
    source_rows: list[int] | None = None,
    source_columns: list[int] | None = None,
) -> None:
    """Write a DataFrame while preserving an optional prefix above start_row."""
    start_row = max(int(start_row or 1), 1)
    max_row = ws.max_row or start_row
    max_col = max(ws.max_column or 1, len(df.columns) or 1)
    from copy import copy

    styles = {}
    if source_columns is not None:
        row_map = [start_row, *(source_rows or list(range(start_row + 1, start_row + 1 + len(df))))]
        for out_row, src_row in enumerate(row_map, start_row):
            for out_col, src_col in enumerate(source_columns, 1):
                cell = ws.cell(src_row, src_col)
                styles[(out_row, out_col)] = (copy(cell._style), copy(cell.comment), copy(cell.hyperlink))
    clear_from = start_row
    for r in range(clear_from, max_row + 1):
        for c in range(1, max_col + 1):
            ws.cell(row=r, column=c).value = None
    headers = [str(c) for c in df.columns]
    values = df.where(pd.notnull(df), None)
    matrix = [headers]
    for row in values.itertuples(index=False, name=None):
        matrix.append(list(row))
    target_rows = max(1, len(matrix))
    target_cols = max(1, len(headers))
    target_end_row = start_row + target_rows - 1
    if max_row > target_end_row:
        ws.delete_rows(target_end_row + 1, max_row - target_end_row)
    # When a prefix is preserved, do not delete columns that may contain
    # title/form metadata above the data block.
    if start_row == 1 and max_col > target_cols:
        ws.delete_cols(target_cols + 1, max_col - target_cols)
    for r, row in enumerate(matrix, start=start_row):
        for c, val in enumerate(row, start=1):
            ws.cell(row=r, column=c).value = val
            if (r, c) in styles:
                cell = ws.cell(r, c)
                cell._style, cell.comment, link = styles[(r, c)]
                cell._hyperlink = None
                if link is not None:
                    cell.hyperlink = link


_PHONE_DIGITS_RE = re.compile(r"\D+")


def _normalize_phone_value(value: Any) -> Any:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return value
    digits = _PHONE_DIGITS_RE.sub("", str(value))
    if digits.startswith("86") and len(digits) == 13:
        digits = digits[2:]
    if digits.startswith("0086") and len(digits) == 15:
        digits = digits[4:]
    return digits or value


def _parse_mixed_date_series(series: "pd.Series") -> "pd.Series":
    text = series.astype("string").str.strip()
    text = text.str.replace(
        r"^(\d{4})年(\d{1,2})月(\d{1,2})日$",
        r"\1-\2-\3",
        regex=True,
    )
    text = text.str.replace(
        r"^(\d{4})[./](\d{1,2})[./](\d{1,2})$",
        r"\1-\2-\3",
        regex=True,
    )
    compact = text.str.fullmatch(r"\d{8}", na=False)
    text.loc[compact] = (
        text.loc[compact].str.slice(0, 4)
        + "-"
        + text.loc[compact].str.slice(4, 6)
        + "-"
        + text.loc[compact].str.slice(6, 8)
    )
    return pd.to_datetime(text, errors="coerce", format="mixed")


def apply_transform_frame(
    df: "pd.DataFrame",
    action: str,
    *,
    key_columns: Any = None,
    key_normalizers: Any = None,
    keep: str = "first",
    order_by: str | None = None,
    column: str | None = None,
    delimiter: str = ",",
    into: Any = None,
) -> tuple["pd.DataFrame | None", str | None]:
    act = str(action or "").strip().lower()
    if act == "dedupe":
        keys = _as_str_list(key_columns)
        if not keys:
            return None, "dedupe 需要 key_columns"
        missing = [c for c in keys if c not in df.columns]
        if missing:
            return None, f"去重列不存在: {missing}"
        normalizers = key_normalizers or {}
        if not isinstance(normalizers, dict):
            return None, "dedupe.key_normalizers 必须是 {列名: phone} 对象"
        unknown = [str(c) for c in normalizers if c not in keys]
        if unknown:
            return None, f"key_normalizers 只能配置 key_columns，未知列: {unknown}"
        unsupported = [
            f"{col}={kind}"
            for col, kind in normalizers.items()
            if str(kind).strip().lower() != "phone"
        ]
        if unsupported:
            return None, f"key_normalizers 当前仅支持 phone: {unsupported}"
        comparable = df[keys].copy()
        for key, kind in normalizers.items():
            if str(kind).strip().lower() == "phone":
                comparable[key] = comparable[key].map(_normalize_phone_value)
        how = str(keep or "first").strip().lower()
        if how in {"first", "last"}:
            retained = ~comparable.duplicated(subset=keys, keep=how)
            out = df.loc[retained].reset_index(drop=True)
            out.attrs["source_positions"] = [int(i) for i in df.index[retained]]
            return out, None
        if how not in {"earliest", "latest"}:
            return None, "dedupe.keep 必须是 first / last / earliest / latest"
        if not order_by:
            return None, f"dedupe.keep={how} 需要 order_by"
        if order_by not in df.columns:
            return None, f"dedupe.order_by 列不存在: {order_by}"
        order_values = _parse_mixed_date_series(df[order_by])
        duplicate_keys = comparable.duplicated(subset=keys, keep=False)
        invalid = order_values.isna() & duplicate_keys
        if invalid.any():
            samples = [str(v) for v in df.loc[invalid, order_by].head(5).tolist()]
            return None, f"dedupe.order_by 有 {int(invalid.sum())} 个日期无法解析: {samples}"
        order_key = "__em_dedupe_order"
        row_key = "__em_dedupe_row"
        while order_key in comparable.columns:
            order_key += "_"
        while row_key in comparable.columns or row_key == order_key:
            row_key += "_"
        ranked = comparable.copy()
        ranked[order_key] = order_values.to_numpy()
        ranked[row_key] = range(len(ranked))
        ranked = ranked.sort_values(
            order_key,
            ascending=how == "earliest",
            kind="stable",
        )
        winners = ranked.drop_duplicates(subset=keys, keep="first")[row_key]
        positions = sorted(int(pos) for pos in winners.tolist())
        out = df.iloc[positions].reset_index(drop=True)
        out.attrs["source_positions"] = positions
        return out, None
    if act == "split":
        if not column or column not in df.columns:
            return None, f"split 需要存在的 column，可用列: {[str(c) for c in df.columns]}"
        names = _as_str_list(into)
        expanded = df[column].astype("string").str.split(str(delimiter), expand=True, regex=False)
        if not names:
            names = [f"{column}_{i + 1}" for i in range(expanded.shape[1])]
        if len(set(names)) != len(names) or any(name in df.columns and name != column for name in names):
            return None, "split 的新列名不能重复或覆盖其他已有列"
        if len(names) < expanded.shape[1]:
            return None, f"split 产生 {expanded.shape[1]} 列，new_columns 仅 {len(names)} 项；请提供足够列名"
        while expanded.shape[1] < len(names):
            expanded[expanded.shape[1]] = None
        expanded = expanded.iloc[:, : len(names)]
        expanded.columns = names
        # Excel「分列」语义：新列原位替换源列，而非追加到表尾。
        out = df.drop(columns=[column]).copy()
        pos = list(df.columns).index(column)
        for i, name in enumerate(names):
            if name in out.columns:
                out[name] = expanded[name]
            else:
                out.insert(pos + i, name, expanded[name])
        return out, None
    if act == "normalize_date":
        if not column or column not in df.columns:
            return None, "normalize_date 需要存在的 column"
        parsed = _parse_mixed_date_series(df[column])
        out = df.copy()
        out[column] = parsed.dt.strftime("%Y-%m-%d")
        out.loc[parsed.isna() & df[column].notna(), column] = df[column]
        return out, None
    if act == "normalize_phone":
        if not column or column not in df.columns:
            return None, "normalize_phone 需要存在的 column"
        out = df.copy()
        out[column] = out[column].map(_normalize_phone_value)
        return out, None
    return None, f"不支持的 transform.action={action}。可用：dedupe / split / normalize_date / normalize_phone"


def pivot_data(
    file_path: str,
    index: Any = None,
    columns: Any = None,
    values: Any = None,
    aggfunc: str = "sum",
    sheet_name: str | None = None,
    header_row: int | None = None,
    column: str | None = None,
    operator: str | None = None,
    value: Any = None,
    conditions: list[dict[str, Any]] | None = None,
    logic: str = "and",
    join: Any = None,
    group_by: Any = None,
    limit: int | None = None,
    margins: Any = False,
    margins_name: Any = "合计",
    expected_version: str | None = None,
) -> ToolResult:
    """只读二维透视：index × columns × values；margins=True 追加合计行/列。"""
    ctx, err = _load_df_for_tool(
        file_path, sheet_name, header_row,
        column_hints=_collect_column_hints(
            group_by=group_by, index=index, columns=columns, values=values,
            column=column, conditions=conditions, join=join,
        ),
        expected_version=expected_version,
    )
    if err is not None:
        return err
    assert ctx is not None
    df = ctx["df"]
    sheet_name = ctx["sheet_name"]
    snap = ctx["snap"]
    rel_path = ctx["rel_path"]
    bound_version = ctx["bound_version"]

    join_spec, join_err = _normalize_join(join, df)
    if join_err is not None:
        return join_err
    unmatched = 0
    if join_spec is not None:
        merged, unmatched, merge_err = _apply_join(df, join_spec, file_path, source_version=bound_version)
        if merge_err is not None:
            return merge_err
        assert merged is not None
        df = merged

    keys, derived_specs, keys_err = _normalize_group_keys(group_by, df.columns)
    if keys_err is not None:
        return keys_err
    idx_keys, idx_derived, idx_err = _normalize_group_keys(index, df.columns)
    if idx_err is not None:
        return idx_err
    col_keys, col_derived, col_err = _normalize_group_keys(columns, df.columns)
    if col_err is not None:
        return col_err
    assert derived_specs is not None and idx_derived is not None and col_derived is not None
    work, mat_err = _materialize_derived_keys(df, [*derived_specs, *idx_derived, *col_derived])
    if mat_err is not None:
        return mat_err
    assert work is not None
    if idx_keys:
        index = idx_keys
    elif keys and index is None:
        index = keys
    if col_keys:
        columns = col_keys

    cond_list, cond_err = _normalize_conditions(
        work, column=column, operator=operator, value=value,
        conditions=conditions, logic=logic, require=False,
    )
    if cond_err is not None:
        return cond_err
    assert cond_list is not None
    combined_mask, mask_err = _build_condition_mask(work, cond_list, logic)
    if mask_err is not None:
        return mask_err
    filtered = work[combined_mask] if combined_mask is not None else work

    table, pivot_err = _pivot_frame(
        filtered, index=index, columns=columns, values=values, aggfunc=aggfunc,
        margins=margins, margins_name=margins_name,
    )
    if pivot_err is not None:
        return _error_payload_result({"error": pivot_err}, code="INVALID_ARGS")
    assert table is not None
    total = len(table)
    cap = limit if isinstance(limit, int) and limit > 0 else total
    truncated = total > cap
    shown = table.head(cap)
    records = _df_to_compact_records(shown)
    out_columns = [str(c) for c in shown.columns]
    matrix = [out_columns] + [[row.get(col) for col in out_columns] for row in records]
    result: dict[str, Any] = {
        "file": rel_path,
        "mode": "pivot",
        "index": _as_str_list(index),
        "columns": out_columns,
        "matrix": matrix,
        "data": records,
        "returned": len(records),
        "total": total,
        "original_rows": len(df),
        "matched_rows": len(filtered),
    }
    if truncated:
        result["truncated"] = True
        result["note"] = f"共 {total} 行，返回前 {len(records)} 行"
    if len(out_columns) > 31 and not col_derived:
        result["warning"] = (
            f"列维度基数 {len(out_columns)} 过高（>{31}）。若按日期分列，"
            "请改用派生键 columns={\"column\":\"日期列\",\"transform\":\"year_month\"} 按月分列。"
        )
    if join_spec is not None:
        result["join"] = {
            "unmatched_rows": unmatched,
            "left_on": join_spec["left_on"],
            "right_on": join_spec["right_on"],
            "source": join_spec.get("source"),
        }
    if sheet_name:
        result["resolved_sheet"] = sheet_name
    _attach_sheet_disambiguation(result, ctx.get("sheet_disambiguation"))
    _attach_file_meta(result, rel_path=rel_path, content_version=bound_version)
    if snap is not None:
        from excelmanus.workbook.snapshot import Coverage, apply_read_contract

        apply_read_contract(
            result,
            snapshot=snap,
            result_kind="records",
            sheet=sheet_name,
            coverage=Coverage(
                kind="truncated" if truncated else "complete",
                returned_rows=len(records),
                total_rows=total,
            ),
            formulas_uncached="unknown",
            meta_kind="pivot",
        )
    budget = _RANGE_PROJECT_MAX_CHARS
    dumped = json.dumps(matrix, ensure_ascii=False, default=str)
    projected = matrix
    while len(dumped) > budget and len(projected) > 2:
        projected = projected[:-1]
        dumped = json.dumps(projected, ensure_ascii=False, default=str)
    lines = [
        f"文件 {rel_path}：透视 {result['index']} × {columns}，{total} 行。",
        f"matrix 投影 {len(projected)}/{len(matrix)}：{dumped}",
    ]
    if truncated:
        lines.append(f"⚠️ {result['note']}")
    if result.get("sheet_disambiguation_warning"):
        lines.append(f"⚠️ {result['sheet_disambiguation_warning']}")
    if len(projected) < len(matrix):
        lines.append(f"returned={len(records)} total={total}；其余见 value.matrix / spill")
    return ToolResult(
        success=True,
        model_text="\n".join(lines),
        value=result,
        ui_meta=ToolUiMeta(
            files=[rel_path] if rel_path else [],
            content_version=bound_version,
        ),
        truncated=truncated or len(projected) < len(matrix),
        coverage={
            "kind": "truncated" if truncated else "complete",
            "rows": len(records),
            "original_rows": len(df),
        },
    )


def distinct_data(
    file_path: str,
    column: str | None = None,
    sheet_name: str | None = None,
    header_row: int | None = None,
    conditions: list[dict[str, Any]] | None = None,
    logic: str = "and",
    limit: int | None = None,
    dup_only: bool = False,
    expected_version: str | None = None,
) -> ToolResult:
    """单列取值分布：唯一值计数 + value_counts + 可选重复键与行号。

    Args:
        file_path: Excel 文件路径。
        column: 目标列名（必填）。
        conditions/logic: 统计前预筛（与 filter 同语法），可选。
        limit: 最多返回的取值条目数，默认 50。
        dup_only: True 时只列重复取值（count>1）并附 source_rows。
    """
    ctx, err = _load_df_for_tool(
        file_path, sheet_name, header_row,
        column_hints=_collect_column_hints(column=column, conditions=conditions),
        expected_version=expected_version,
    )
    if err is not None:
        return err
    assert ctx is not None
    df = ctx["df"]
    sheet_name = ctx["sheet_name"]
    effective_header = ctx["effective_header"]
    snap = ctx["snap"]
    rel_path = ctx["rel_path"]
    bound_version = ctx["bound_version"]

    if not column:
        return _error_payload_result(
            {"error": "distinct 需要 column"}, code="INVALID_ARGS",
        )
    if column not in df.columns:
        return _error_payload_result(
            {"error": f"列 '{column}' 不存在，可用列: {[str(c) for c in df.columns]}"},
            code="NOT_FOUND",
        )

    cond_list, cond_err = _normalize_conditions(
        df, column=None, operator=None, value=None,
        conditions=conditions, logic=logic, require=False,
    )
    if cond_err is not None:
        return cond_err
    assert cond_list is not None
    combined_mask, mask_err = _build_condition_mask(df, cond_list, logic)
    if mask_err is not None:
        return mask_err
    filtered = df[combined_mask] if combined_mask is not None else df

    series = filtered[column]
    non_blank = _scan_non_blank(series)
    blank_count = int(len(series) - len(non_blank))
    vc = non_blank.value_counts()
    unique_count = int(non_blank.nunique())
    dup_mask = vc > 1
    dup_values = vc[dup_mask]

    cap = limit if isinstance(limit, int) and limit > 0 else _DISTINCT_TOP_DEFAULT
    head = dup_values if dup_only else vc
    truncated = len(head) > cap
    entries = [
        {"value": _py_scalar(v), "count": int(c)}
        for v, c in head.head(cap).items()
    ]
    if dup_only:
        for entry in entries:
            val = entry["value"]
            match_idx = non_blank[non_blank.astype(str) == str(val)].index[:10]
            entry["source_rows"] = [
                _excel_source_row(effective_header, idx) for idx in match_idx
            ]

    result: dict[str, Any] = {
        "file": rel_path,
        "mode": "distinct",
        "column": str(column),
        "conditions": cond_list,
        "original_rows": len(df),
        "matched_rows": len(filtered),
        "blank_count": blank_count,
        "unique_count": unique_count,
        "duplicate_value_count": int(dup_mask.sum()),
        "duplicate_row_count": int(dup_values.sum()) if not dup_values.empty else 0,
        "values": entries,
        "data": entries,
        "dup_only": dup_only,
    }
    if truncated:
        result["truncated"] = True
        result["note"] = f"共 {len(head)} 个取值，返回前 {len(entries)} 个（limit={cap}）"
    if sheet_name:
        result["resolved_sheet"] = sheet_name
    _attach_sheet_disambiguation(result, ctx.get("sheet_disambiguation"))

    _attach_file_meta(result, rel_path=rel_path, content_version=bound_version)
    if snap is not None:
        from excelmanus.workbook.snapshot import Coverage, apply_read_contract

        apply_read_contract(
            result,
            snapshot=snap,
            result_kind="records",
            sheet=sheet_name,
            coverage=Coverage(
                kind="truncated" if truncated else "complete",
                returned_rows=len(entries),
                total_rows=len(head),
            ),
            formulas_uncached="unknown",
            meta_kind="distinct",
        )
    else:
        raise RuntimeError("distinct finalize 必须提供 WorkbookSnapshot")

    dup_desc = (
        f"，{int(dup_mask.sum())} 个重复取值（涉 {int(dup_values.sum())} 行）"
        if int(dup_mask.sum()) else "，无重复"
    )
    lines = [
        f"文件 {result.get('file', rel_path)} / {column}：匹配 {len(filtered)} 行，"
        f"{unique_count} 个唯一值{dup_desc}，空值 {blank_count} 行。",
    ]
    if result.get("conditions"):
        lines.append(
            f"预筛（{logic}）：{json.dumps(cond_list, ensure_ascii=False, default=str)}"
        )
    if truncated and result.get("note"):
        lines.append(f"⚠️ {result['note']}")
    if result.get("sheet_disambiguation_warning"):
        lines.append(f"⚠️ {result['sheet_disambiguation_warning']}")
    if entries:
        lines.append(
            f"取值（{len(entries)}）：{json.dumps(entries[:10], ensure_ascii=False, default=str)}"
        )
    return ToolResult(
        success=True,
        model_text="\n".join(lines),
        value=result,
        ui_meta=ToolUiMeta(
            files=[rel_path] if rel_path else [],
            content_version=bound_version,
        ),
        truncated=truncated,
        coverage={
            "kind": "truncated" if truncated else "complete",
            "rows": len(entries),
            "original_rows": len(df),
            "filtered_rows": len(filtered),
        },
    )





# inspect_excel_files 可用的 include 维度
_SCAN_FILES_DIMENSIONS = (
    "freeze_panes",
    "charts",
    "images",
    "conditional_formatting",
    "column_widths",
    "vba",
)

# 递归扫描时跳过的噪音目录
_SCAN_SKIP_DIRS: frozenset[str] = frozenset({
    ".git", ".venv", "node_modules", "__pycache__",
    ".worktrees", "dist", "build", "outputs",
    ".excelmanus",
})


def inspect_excel_files(
    directory: str = ".",
    max_files: int = 20,
    preview_rows: int = 3,
    max_columns: int = 15,
    include: list[str] | None = None,
    recursive: bool = True,
    search: str | None = None,
    sheet_name: str | None = None,
) -> ToolResult:
    """批量扫描目录下所有 Excel 文件，返回轻量级概览，可按需附加额外维度。

    使用 openpyxl 只读模式，仅读取 sheet 元信息和少量预览行，
    避免加载完整 DataFrame，适合快速了解工作区全貌。

    Args:
        directory: 扫描目录（相对于工作目录），默认当前目录。
        max_files: 最多扫描文件数，默认 20。
        preview_rows: 每个 sheet 预览行数，默认 3。
        max_columns: header/preview 最多展示列数，默认 15。
        include: 按需请求的额外维度列表。可选值：
            freeze_panes, charts, images, conditional_formatting, column_widths。
        recursive: 是否递归扫描子目录，默认 True。
        search: 模糊搜索关键词，匹配文件名或 sheet 名称。
        sheet_name: 按 sheet 名称精确搜索，返回包含该 sheet 的文件。

    Returns:
        ToolResult（value 含批量概览，model_text 为短摘要）。
    """
    from datetime import datetime, timezone
    from pathlib import Path

    from openpyxl import load_workbook

    include_set: set[str] = set(include) if include else set()
    invalid_dims = include_set - set(_SCAN_FILES_DIMENSIONS)
    include_set -= invalid_dims
    needs_full = bool(include_set)

    guard = _get_guard()
    safe_dir = guard.resolve_and_validate(directory)

    if not safe_dir.is_dir():
        return _error_payload_result(
            {"error": f"路径 '{directory}' 不是一个有效的目录"},
            code="PATH_INVALID",
        )

    # 收集 Excel/CSV 文件，跳过隐藏文件和临时文件
    # 先收集全部再排序，确保结果确定性（glob 返回顺序依赖文件系统，不可靠）
    glob_method = safe_dir.rglob if recursive else safe_dir.glob
    excel_paths: list[Path] = []
    for ext in ("*.xlsx", "*.xlsm", "*.xls", "*.xlsb", "*.csv", "*.tsv"):
        for p in glob_method(ext):
            if p.name.startswith((".", "~$")):
                continue
            # 递归模式下跳过噪音目录
            if recursive:
                rel_parts = p.relative_to(safe_dir).parts[:-1]  # 不含文件名
                if any(part in _SCAN_SKIP_DIRS for part in rel_parts):
                    continue
            excel_paths.append(p)
    excel_paths.sort(key=lambda p: str(p.relative_to(safe_dir)).lower())

    # ── 搜索过滤：按文件名 / sheet 名匹配 ──
    if search or sheet_name:
        search_lower = (search or "").lower()
        sheet_lower = (sheet_name or "").lower()
        matched: list[Path] = []
        # 第一轮：按文件名快速过滤（无需打开文件）
        remaining: list[Path] = []
        for fp in excel_paths:
            if search_lower and search_lower in fp.name.lower():
                matched.append(fp)
            else:
                remaining.append(fp)
        # 第二轮：需要读取 sheet names 的文件
        for fp in remaining:
            if len(matched) >= max_files:
                break
            try:
                rel = str(fp.relative_to(guard.workspace_root)).replace("\\", "/")
                snap, snap_err = _open_tool_snapshot(rel)
                if snap_err is not None or snap is None:
                    continue
                if snap.is_csv():
                    names = ["Sheet1"]
                else:
                    wb_peek = load_workbook(snap.backing_path, read_only=True, data_only=True)
                    try:
                        names = list(wb_peek.sheetnames)
                    finally:
                        wb_peek.close()
                for sn in names:
                    sn_lower = sn.lower()
                    if sheet_lower and sheet_lower in sn_lower:
                        matched.append(fp)
                        break
                    if search_lower and search_lower in sn_lower:
                        matched.append(fp)
                        break
            except Exception:  # noqa: BLE001
                continue
        excel_paths = matched

    excel_paths = excel_paths[:max_files]

    files_summary: list[dict[str, Any]] = []
    for fp in excel_paths:
        stat = fp.stat()
        rel = str(fp.relative_to(guard.workspace_root)).replace("\\", "/")
        snap, snap_err = _open_tool_snapshot(rel)
        file_info: dict[str, Any] = {
            "file": fp.name,
            "path": rel,
            "size": _format_size(stat.st_size),
            "modified": datetime.fromtimestamp(
                stat.st_mtime, tz=timezone.utc
            ).strftime("%Y-%m-%d"),
        }
        if snap_err is not None or snap is None:
            file_info["error"] = (
                snap_err.model_text if snap_err is not None else "无法打开快照"
            )
            files_summary.append(file_info)
            continue
        file_info["content_version"] = snap.content_version
        file_info["snapshot_id"] = snap.id.key()
        read_path = snap.backing_path

        sheets_info: list[dict[str, Any]] = []
        try:
            if _is_csv_file(fp):
                from excelmanus.workbook.snapshot import csv_separator_for

                scan_rows_csv = max(8, preview_rows + 8)
                try:
                    df_raw = pd.read_csv(
                        read_path,
                        header=None,
                        nrows=scan_rows_csv,
                        dtype=str,
                        sep=csv_separator_for(fp),
                        encoding=_detect_csv_encoding(read_path),
                    )
                except Exception as csv_exc:
                    file_info["error"] = f"无法读取: {csv_exc}"
                    df_raw = pd.DataFrame()
                if not df_raw.empty:
                    total_csv_rows = _get_sheet_total_rows(read_path, None)
                    rows_raw_csv: list[list[Any]] = []
                    for _, r in df_raw.iterrows():
                        rows_raw_csv.append([_normalize_cell(v) if pd.notna(v) else None for v in r])
                    header_idx = _guess_header_row_from_rows(rows_raw_csv, max_scan=scan_rows_csv)
                    if header_idx is None:
                        header_idx = 0
                    header_raw = rows_raw_csv[header_idx] if header_idx < len(rows_raw_csv) else []
                    header_csv = _trim_trailing_nulls([_cell_to_str(c) for c in header_raw])
                    preview_raw = rows_raw_csv[header_idx + 1:header_idx + 1 + preview_rows]
                    preview_csv = [_trim_trailing_nulls([_cell_to_str(c) for c in r]) for r in preview_raw]
                    if any(len(r) > max_columns for r in preview_csv):
                        preview_csv = [r[:max_columns] for r in preview_csv]
                    csv_sheet: dict[str, Any] = {
                        "name": "Sheet1",
                        "rows": total_csv_rows if total_csv_rows is not None else len(df_raw),
                        "columns": len(df_raw.columns),
                        "header_row": header_idx + 1,
                        "business_columns": len(header_csv),
                        "header": header_csv,
                        "preview": preview_csv,
                    }
                    sheets_info.append(csv_sheet)
            else:
                wb = load_workbook(read_path, read_only=not needs_full, data_only=True)
                for sn in wb.sheetnames:
                    ws = wb[sn]
                    total_cols = ws.max_column or 0
                    sheet_data: dict[str, Any] = {
                        "name": sn,
                        "rows": ws.max_row or 0,
                        "columns": total_cols,
                    }

                    # 读取抽样行，用于表头识别与预览
                    scan_rows = max(8, preview_rows + 8)
                    scan_cols = max(1, min(total_cols if total_cols > 0 else _HEADER_SCAN_COLS, _HEADER_SCAN_COLS))
                    rows_raw: list[list[Any]] = []
                    for row in ws.iter_rows(
                        min_row=1,
                        max_row=scan_rows,
                        min_col=1,
                        max_col=scan_cols,
                        values_only=True,
                    ):
                        rows_raw.append([_normalize_cell(c) for c in row])

                    if rows_raw:
                        header_idx = _guess_header_row_from_rows(rows_raw, max_scan=scan_rows)
                        if header_idx is None:
                            header_idx = 0

                        header_raw = rows_raw[header_idx] if header_idx < len(rows_raw) else []
                        header = _trim_trailing_nulls([_cell_to_str(c) for c in header_raw])

                        preview_raw = rows_raw[header_idx + 1:header_idx + 1 + preview_rows]
                        preview = [_trim_trailing_nulls([_cell_to_str(c) for c in r]) for r in preview_raw]

                        # 仅对 preview 数据行限宽，header 完整保留以确保 agent 理解全部列语义
                        if any(len(r) > max_columns for r in preview):
                            preview = [r[:max_columns] for r in preview]
                            sheet_data["preview_columns_truncated"] = max_columns

                        sheet_data["header_row"] = header_idx + 1
                        sheet_data["business_columns"] = len(header)
                        sheet_data["header"] = header
                        sheet_data["preview"] = preview

                    # 按需采集额外维度
                    if needs_full and include_set:
                        if "freeze_panes" in include_set:
                            sheet_data["freeze_panes"] = _collect_freeze_panes(ws)
                        if "charts" in include_set:
                            sheet_data["charts"] = _collect_charts(ws)
                        if "images" in include_set:
                            sheet_data["images"] = _collect_images(ws)
                        if "conditional_formatting" in include_set:
                            sheet_data["conditional_formatting"] = _collect_conditional_formatting(ws)
                        if "column_widths" in include_set:
                            sheet_data["column_widths"] = _collect_column_widths(ws)

                    sheets_info.append(sheet_data)
                wb.close()
        except Exception as exc:  # noqa: BLE001
            file_info["error"] = f"无法读取: {exc}"

        file_info["sheets"] = sheets_info
        # vba 维度：文件级别，不依赖 sheet
        if "vba" in include_set:
            file_info["vba"] = _collect_vba_info(fp, extract_source=False)
        files_summary.append(file_info)

    # 紧凑文件清单放在最前，即使详细信息被截断也能保留完整文件列表
    file_list = [
        {"file": fp.name, "size": _format_size(fp.stat().st_size)}
        for fp in excel_paths
    ]

    result: dict[str, Any] = {
        "directory": directory,
        "excel_files_found": len(excel_paths),
        "truncated": len(excel_paths) >= max_files,
        "file_list": file_list,
        "files": files_summary,
    }
    if invalid_dims:
        result["include_warning"] = f"未知的 include 维度已忽略: {sorted(invalid_dims)}"
    ui_files = [
        str(item.get("path") or item.get("file"))
        for item in files_summary
        if isinstance(item, dict) and (item.get("path") or item.get("file"))
    ]
    return _finalize_inspect_excel_files_result(result, file_paths=ui_files)


def _cell_to_str(value: Any) -> str | None:
    """将单元格值转换为紧凑字符串，None 保持为 None。"""
    if value is None:
        return None
    return str(value)


def _trim_trailing_nulls(row: list[Any]) -> list[Any]:
    """去除列表尾部连续的 None 值，减少 JSON 体积。"""
    end = len(row)
    while end > 0 and row[end - 1] is None:
        end -= 1
    return row[:end]


def _df_to_compact_records(df: "pd.DataFrame") -> list[dict[str, Any]]:
    """将 DataFrame 转为紧凑记录：去除 null/NaN 键，大幅减少 token 浪费。

    原理：等效于 JSON 版 Markdown-KV——每个值显式关联其键名，无 null 噪音。
    研究表明 KV 格式 LLM 理解度最高（60.7% vs JSON 53.7% vs CSV 44.3%）。
    合并单元格导致的 NaN 由 merged_cell_summary / null_info 独立解释，
    不依赖数据中的 null。空字符串与 0/False 是真实值，原样保留。
    """
    records: list[dict[str, Any]] = []
    cols = [str(c) for c in df.columns]
    for row in df.itertuples(index=False):
        d: dict[str, Any] = {}
        for col_name, val in zip(cols, row):
            if pd.isna(val):
                continue
            if isinstance(val, (date, datetime)):
                d[col_name] = val.isoformat()
            else:
                d[col_name] = val
        records.append(d)
    return records


def _build_null_info(df: "pd.DataFrame") -> dict[str, Any] | None:
    """生成空值摘要：一行代替 N×M 个 null token。

    返回 None 表示无显著空值。
    """
    if df.empty:
        return None
    null_rates = df.isnull().mean()
    all_null_cols = [str(c) for c in null_rates[null_rates == 1.0].index]
    high_null_cols = [str(c) for c in null_rates[(null_rates >= 0.6) & (null_rates < 1.0)].index]
    if not all_null_cols and not high_null_cols:
        return None
    info: dict[str, Any] = {}
    if all_null_cols:
        info["完全为空"] = all_null_cols
    if high_null_cols:
        info["高空值率(≥60%)"] = high_null_cols
    return info


def _format_size(size_bytes: int) -> str:
    """将字节数格式化为可读字符串。"""
    for unit in ("B", "KB", "MB", "GB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f}{unit}" if unit != "B" else f"{size_bytes}{unit}"
        size_bytes /= 1024  # type: ignore[assignment]
    return f"{size_bytes:.1f}TB"


# ── include 维度采集函数 ──────────────────────────────────

# include 参数所有合法维度
INCLUDE_DIMENSIONS = (
    "data_preview",
    "styles",
    "charts",
    "images",
    "freeze_panes",
    "conditional_formatting",
    "data_validation",
    "print_settings",
    "column_widths",
    "formulas",
    "categorical_summary",
    "summary",
    "vba",
    "merges",
)

# categorical_summary 默认阈值：unique 值数量低于此值的列视为分类列
_CATEGORICAL_UNIQUE_THRESHOLD = 20


def _collect_categorical_summary(
    df: "pd.DataFrame",
    threshold: int = _CATEGORICAL_UNIQUE_THRESHOLD,
) -> dict[str, Any]:
    """对分类列（unique 值 < threshold）计算 value_counts，返回摘要字典。

    Returns:
        {"threshold": int, "columns": {col: {val: count, ...}, ...}}
    """
    result: dict[str, dict[str, int]] = {}
    for col in df.columns:
        series = _scan_non_blank(df[col])
        if series.empty:
            continue
        n_unique = series.nunique()
        if 0 < n_unique <= threshold:
            vc = series.value_counts(dropna=True)
            result[str(col)] = {str(k): int(v) for k, v in vc.items()}
    return {"threshold": threshold, "columns": result}


def _collect_data_summary(df: "pd.DataFrame") -> dict[str, Any]:
    """计算每列数据质量概要：null 率、unique 数、min/max（数值列）、top_values（分类列）。"""
    result: dict[str, Any] = {}
    for col in df.columns:
        col_str = str(col)
        series = df[col]
        blank = series.map(_is_scan_blank)
        usable = _scan_non_blank(series)
        info: dict[str, Any] = {
            "null_rate": round(float(blank.mean()), 4),
            "unique": int(usable.nunique()) if not usable.empty else 0,
        }
        numeric = _coerce_numeric(series)
        if numeric.notna().any() and float(numeric.notna().mean()) >= 0.9:
            desc = numeric.describe()
            info["min"] = desc.get("min")
            info["max"] = desc.get("max")
            info["mean"] = round(float(desc.get("mean", 0)), 2)
        else:
            vc = usable.value_counts().head(3)
            if not vc.empty:
                info["top_values"] = [str(v) for v in vc.index.tolist()]
        result[col_str] = info
    return result


def _color_to_hex_short(color: Any) -> str | None:
    """将 openpyxl Color 对象转为 6 位十六进制字符串，无效或默认色返回 None。"""
    if color is None:
        return None
    from excelmanus.tools._style_extract import resolve_color

    color_type = getattr(color, "type", None)
    if color_type == "theme":
        resolved = resolve_color(color)
        return resolved.lstrip("#") if resolved else None
    if color_type == "indexed":
        resolved = resolve_color(color)
        return resolved.lstrip("#") if resolved else None
    if color_type == "rgb":
        resolved = resolve_color(color)
        if resolved is None:
            return None
        hex_val = resolved.lstrip("#")
        if hex_val in {"000000", "FFFFFF"} and str(getattr(color, "rgb", "")).upper() in {
            "00000000",
            "FFFFFFFF",
        }:
            return None
        return hex_val
    resolved = resolve_color(color)
    return resolved.lstrip("#") if resolved else None


def _extract_style_tuple(cell: Any) -> tuple | None:
    """从单元格提取样式关键属性元组，全默认样式返回 None。"""
    parts: list[Any] = []
    has_custom = False

    # 字体
    f = cell.font
    if f:
        font_info: dict[str, Any] = {}
        if f.name and f.name != "Calibri":
            font_info["name"] = f.name
        if f.size and f.size != 11:
            font_info["size"] = f.size
        if f.bold:
            font_info["bold"] = True
        if f.italic:
            font_info["italic"] = True
        if f.underline and f.underline != "none":
            font_info["underline"] = f.underline
        if f.strike:
            font_info["strike"] = True
        c = _color_to_hex_short(f.color)
        if c and c != "000000":
            font_info["color"] = c
        if font_info:
            has_custom = True
        parts.append(tuple(sorted(font_info.items())) if font_info else ())
    else:
        parts.append(())

    # 填充
    fl = cell.fill
    if fl:
        fill_type = fl.fill_type or fl.patternType
        if fill_type and fill_type != "none":
            fg = _color_to_hex_short(fl.fgColor)
            parts.append(("fill", fill_type, fg))
            has_custom = True
        else:
            parts.append(())
    else:
        parts.append(())

    # 边框
    b = cell.border
    if b:
        border_parts: list[tuple[str, str]] = []
        for side_name in ("left", "right", "top", "bottom"):
            side = getattr(b, side_name, None)
            if side and side.style and side.style != "none":
                border_parts.append((side_name, side.style))
        if border_parts:
            has_custom = True
        parts.append(tuple(border_parts))
    else:
        parts.append(())

    # 对齐
    a = cell.alignment
    if a:
        align_info: dict[str, Any] = {}
        if a.horizontal and a.horizontal != "general":
            align_info["horizontal"] = a.horizontal
        if a.vertical and a.vertical != "bottom":
            align_info["vertical"] = a.vertical
        if a.wrap_text:
            align_info["wrap_text"] = True
        if align_info:
            has_custom = True
        parts.append(tuple(sorted(align_info.items())) if align_info else ())
    else:
        parts.append(())

    # 数字格式
    nf = cell.number_format
    if nf and nf != "General":
        parts.append(nf)
        has_custom = True
    else:
        parts.append("")

    if not has_custom:
        return None
    return tuple(parts)


def _style_tuple_to_dict(st: tuple) -> dict[str, Any]:
    """将样式元组还原为可读字典。"""
    result: dict[str, Any] = {}
    font_parts, fill_parts, border_parts, align_parts, num_fmt = st

    if font_parts:
        result["font"] = dict(font_parts)
    if fill_parts:
        _, fill_type, fg = fill_parts
        info: dict[str, Any] = {"type": fill_type}
        if fg:
            info["color"] = fg
        result["fill"] = info
    if border_parts:
        result["border"] = {side: style for side, style in border_parts}
    if align_parts:
        result["alignment"] = dict(align_parts)
    if num_fmt:
        result["number_format"] = num_fmt
    return result


def _collect_styles_compressed(
    ws: Any,
    max_rows: int = 200,
) -> dict[str, Any]:
    """扫描工作表，以 Style Classes 压缩方式返回样式信息。

    算法：
    1. 逐单元格提取样式元组
    2. 为唯一组合分配 sN ID
    3. 按列扫描合并连续相同样式的单元格为范围

    Returns:
        包含 style_classes, cell_style_map, merged_ranges 的字典。
    """
    from openpyxl.utils import get_column_letter

    scan_rows = min(ws.max_row or 0, max_rows)
    scan_cols = ws.max_column or 0
    if scan_rows == 0 or scan_cols == 0:
        return {
            "style_classes": {},
            "cell_style_map": {},
            "merged_ranges": [str(mr) for mr in ws.merged_cells.ranges],
            "rows_scanned": scan_rows,
            "truncated": (ws.max_row or 0) > max_rows,
        }

    # 第一遍：收集所有样式元组，分配 ID
    style_to_id: dict[tuple, str] = {}
    # cell_map[col_idx][row_idx] = style_id
    cell_map: dict[int, dict[int, str]] = {}
    id_counter = 0

    for row in ws.iter_rows(min_row=1, max_row=scan_rows, min_col=1, max_col=scan_cols):
        for cell in row:
            st = _extract_style_tuple(cell)
            if st is None:
                continue
            if st not in style_to_id:
                style_to_id[st] = f"s{id_counter}"
                id_counter += 1
            sid = style_to_id[st]
            col_idx = cell.column
            row_idx = cell.row
            if col_idx not in cell_map:
                cell_map[col_idx] = {}
            cell_map[col_idx][row_idx] = sid

    # 构建 style_classes 字典
    style_classes = {sid: _style_tuple_to_dict(st) for st, sid in style_to_id.items()}

    # 第二遍：按列合并连续相同 style_id 为范围
    range_map: dict[str, str] = {}  # "A1:A10" -> "s0"

    for col_idx in sorted(cell_map.keys()):
        col_letter = get_column_letter(col_idx)
        rows_dict = cell_map[col_idx]
        sorted_rows = sorted(rows_dict.keys())
        if not sorted_rows:
            continue

        # 合并连续行
        start_row = sorted_rows[0]
        current_sid = rows_dict[start_row]
        prev_row = start_row

        for r in sorted_rows[1:]:
            sid = rows_dict[r]
            if sid == current_sid and r == prev_row + 1:
                # 连续且相同
                prev_row = r
            else:
                # 输出前一段
                if start_row == prev_row:
                    range_map[f"{col_letter}{start_row}"] = current_sid
                else:
                    range_map[f"{col_letter}{start_row}:{col_letter}{prev_row}"] = current_sid
                start_row = r
                current_sid = sid
                prev_row = r

        # 输出最后一段
        if start_row == prev_row:
            range_map[f"{col_letter}{start_row}"] = current_sid
        else:
            range_map[f"{col_letter}{start_row}:{col_letter}{prev_row}"] = current_sid

    merged_ranges = [str(mr) for mr in ws.merged_cells.ranges]

    return {
        "style_classes": style_classes,
        "cell_style_map": range_map,
        "merged_ranges": merged_ranges,
        "rows_scanned": scan_rows,
        "truncated": (ws.max_row or 0) > max_rows,
    }


def _collect_charts(ws: Any) -> list[dict[str, Any]]:
    """检测工作表中嵌入的图表，返回元信息列表。"""
    charts_info: list[dict[str, Any]] = []
    chart_list = getattr(ws, "_charts", [])
    for chart in chart_list:
        info: dict[str, Any] = {}
        # 图表类型
        type_name = type(chart).__name__.replace("Chart", "").lower()
        info["type"] = type_name
        if hasattr(chart, "title") and chart.title:
            title = chart.title
            if isinstance(title, str):
                info["title"] = title
            else:
                # openpyxl Title/Text object: drill into rich text paragraphs
                text_obj = title.text if hasattr(title, "text") else title
                rich = getattr(text_obj, "rich", None)
                if rich is not None:
                    parts: list[str] = []
                    for p in getattr(rich, "p", []):
                        for r in (getattr(p, "r", None) or []):
                            if getattr(r, "t", None):
                                parts.append(r.t)
                    if parts:
                        info["title"] = "".join(parts)
        info["series_count"] = len(chart.series) if hasattr(chart, "series") else 0
        # 锚点位置
        if hasattr(chart, "anchor") and chart.anchor:
            anchor = chart.anchor
            if hasattr(anchor, "_from") and anchor._from:
                f = anchor._from
                from openpyxl.utils import get_column_letter as gcl
                info["anchor_cell"] = f"{gcl(f.col + 1)}{f.row + 1}"
        charts_info.append(info)
    return charts_info


def _collect_images(ws: Any) -> list[dict[str, Any]]:
    """检测工作表中嵌入的图片，返回元信息列表。"""
    images_info: list[dict[str, Any]] = []
    image_list = getattr(ws, "_images", [])
    for img in image_list:
        info: dict[str, Any] = {}
        if hasattr(img, "width") and img.width:
            info["width_px"] = img.width
        if hasattr(img, "height") and img.height:
            info["height_px"] = img.height
        # 图片格式
        if hasattr(img, "format"):
            info["format"] = img.format
        elif hasattr(img, "path") and img.path:
            ext = str(img.path).rsplit(".", 1)[-1] if "." in str(img.path) else "unknown"
            info["format"] = ext
        # 锚点位置
        if hasattr(img, "anchor") and img.anchor:
            anchor = img.anchor
            if isinstance(anchor, str):
                info["anchor_cell"] = anchor
            elif hasattr(anchor, "_from") and anchor._from:
                f = anchor._from
                from openpyxl.utils import get_column_letter as gcl
                info["anchor_cell"] = f"{gcl(f.col + 1)}{f.row + 1}"
        images_info.append(info)
    return images_info


def _collect_freeze_panes(ws: Any) -> str | None:
    """返回冻结窗格位置（如 'A4'），未冻结返回 None。"""
    fp = ws.freeze_panes
    return str(fp) if fp else None


def _collect_conditional_formatting(ws: Any) -> list[dict[str, Any]]:
    """收集条件格式规则列表。"""
    rules_info: list[dict[str, Any]] = []
    for cf in ws.conditional_formatting:
        ranges_str = str(cf)
        for rule in cf.rules:
            info: dict[str, Any] = {"range": ranges_str}
            if hasattr(rule, "type") and rule.type:
                info["type"] = rule.type
            if hasattr(rule, "priority") and rule.priority is not None:
                info["priority"] = rule.priority
            if hasattr(rule, "formula") and rule.formula:
                info["formula"] = list(rule.formula) if not isinstance(rule.formula, str) else [rule.formula]
            if hasattr(rule, "operator") and rule.operator:
                info["operator"] = rule.operator
            rules_info.append(info)
    return rules_info


def _collect_data_validation(ws: Any) -> list[dict[str, Any]]:
    """收集数据验证规则列表。"""
    validations: list[dict[str, Any]] = []
    dv_list = getattr(ws, "data_validations", None)
    if dv_list is None:
        return validations
    dv_items = getattr(dv_list, "dataValidation", [])
    for dv in dv_items:
        info: dict[str, Any] = {}
        if hasattr(dv, "sqref") and dv.sqref:
            info["range"] = str(dv.sqref)
        if hasattr(dv, "type") and dv.type:
            info["type"] = dv.type
        if hasattr(dv, "formula1") and dv.formula1:
            info["formula1"] = str(dv.formula1)
        if hasattr(dv, "formula2") and dv.formula2:
            info["formula2"] = str(dv.formula2)
        if hasattr(dv, "allow_blank") and dv.allow_blank is not None:
            info["allow_blank"] = bool(dv.allow_blank)
        if hasattr(dv, "showDropDown") and dv.showDropDown is not None:
            info["show_dropdown"] = bool(dv.showDropDown)
        validations.append(info)
    return validations


def _collect_print_settings(ws: Any) -> dict[str, Any]:
    """收集打印设置信息。"""
    info: dict[str, Any] = {}
    if ws.print_area:
        info["print_area"] = ws.print_area
    ps = ws.page_setup
    if ps:
        if ps.orientation:
            info["orientation"] = ps.orientation
        if ps.paperSize is not None:
            info["paper_size"] = ps.paperSize
        if ps.fitToWidth is not None:
            info["fit_to_width"] = ps.fitToWidth
        if ps.fitToHeight is not None:
            info["fit_to_height"] = ps.fitToHeight
        if ps.scale is not None:
            info["scale"] = ps.scale
    if ws.print_title_rows:
        info["repeat_rows"] = ws.print_title_rows
    if ws.print_title_cols:
        info["repeat_columns"] = ws.print_title_cols
    return info


def _collect_column_widths(ws: Any) -> dict[str, float]:
    """收集非默认列宽映射。"""
    widths: dict[str, float] = {}
    for col_letter, dim in ws.column_dimensions.items():
        if dim.width is not None and dim.width != 8.0:
            widths[col_letter] = round(dim.width, 2)
    return widths


def _collect_formulas(ws: Any, max_rows: int = 200) -> dict[str, Any]:
    """收集含公式的单元格位置和公式内容。"""
    from openpyxl.utils import get_column_letter

    formulas: list[dict[str, str]] = []
    total_rows = ws.max_row or 0
    scan_rows = min(total_rows, max_rows)
    scan_cols = ws.max_column or 0
    if scan_rows == 0 or scan_cols == 0:
        return {
            "items": formulas,
            "rows_scanned": scan_rows,
            "truncated": False,
        }

    for row in ws.iter_rows(min_row=1, max_row=scan_rows, min_col=1, max_col=scan_cols):
        for cell in row:
            val = cell.value
            if isinstance(val, str) and val.startswith("="):
                coord = f"{get_column_letter(cell.column)}{cell.row}"
                formulas.append({"cell": coord, "formula": val})
    return {
        "items": formulas,
        "rows_scanned": scan_rows,
        "truncated": total_rows > max_rows,
    }


def _dispatch_include_dimensions(
    ws_for_include: Any,
    include_set: set[str],
    max_style_scan_rows: int,
) -> dict[str, Any]:
    """根据 include 集合分发各维度采集，返回合并字典。"""
    extra: dict[str, Any] = {}

    if "styles" in include_set:
        extra["styles"] = _collect_styles_compressed(ws_for_include, max_rows=max_style_scan_rows)

    if "charts" in include_set:
        extra["charts"] = _collect_charts(ws_for_include)

    if "images" in include_set:
        extra["images"] = _collect_images(ws_for_include)

    if "freeze_panes" in include_set:
        extra["freeze_panes"] = _collect_freeze_panes(ws_for_include)

    if "conditional_formatting" in include_set:
        extra["conditional_formatting"] = _collect_conditional_formatting(ws_for_include)

    if "data_validation" in include_set:
        extra["data_validation"] = _collect_data_validation(ws_for_include)

    if "print_settings" in include_set:
        extra["print_settings"] = _collect_print_settings(ws_for_include)

    if "column_widths" in include_set:
        extra["column_widths"] = _collect_column_widths(ws_for_include)

    if "formulas" in include_set:
        extra["formulas"] = _collect_formulas(ws_for_include, max_rows=max_style_scan_rows)

    if "merges" in include_set:
        ranges = [str(item) for item in ws_for_include.merged_cells.ranges]
        extra["merges"] = {"count": len(ranges), "ranges": ranges[:20]}

    # vba 维度在调用方单独处理（需要 file path，不依赖 worksheet）

    return extra


# ── VBA 信息提取 ──────────────────────────────────────────


def _collect_vba_info(file_path: Any, *, extract_source: bool = True) -> dict[str, Any]:
    """提取 .xlsm 文件中的 VBA 宏信息。

    使用 zipfile 读取 vbaProject.bin 的存在性和模块列表。
    当 oletools 可用且 extract_source=True 时，提取完整 VBA 源代码。

    Args:
        file_path: Excel 文件路径。
        extract_source: 是否尝试提取 VBA 源代码（需要 oletools）。

    Returns:
        VBA 信息字典，包含 has_vba、modules 及可选的 source。
    """
    import zipfile
    from pathlib import Path

    path = Path(file_path) if not isinstance(file_path, Path) else file_path
    result: dict[str, Any] = {"has_vba": False, "modules": []}

    if path.suffix.lower() not in (".xlsm", ".xlsb"):
        return result

    # 从 ZIP 结构检测 VBA 项目
    try:
        with zipfile.ZipFile(path, "r") as zf:
            vba_names = [n for n in zf.namelist() if n.startswith("xl/vbaProject")]
            if not vba_names:
                return result
            result["has_vba"] = True
            # 提取模块名列表（从 ZIP 中的 VBA 相关条目）
            macro_entries = [
                n for n in zf.namelist()
                if n.startswith("xl/") and (
                    n.endswith(".bin") and "vba" in n.lower()
                )
            ]
            result["vba_archive_entries"] = macro_entries
    except (zipfile.BadZipFile, Exception):
        result["error"] = "无法读取 ZIP 结构"
        return result

    # 尝试用 oletools 提取 VBA 源码
    if extract_source:
        try:
            from oletools.olevba import VBA_Parser  # type: ignore[import-untyped]

            vba_parser = VBA_Parser(str(path))
            if vba_parser.detect_vba_macros():
                modules: list[dict[str, str]] = []
                for (_, _, vba_filename, vba_code) in vba_parser.extract_macros():
                    modules.append({
                        "name": vba_filename,
                        "code": vba_code,
                    })
                result["modules"] = modules
                result["module_count"] = len(modules)

                # 安全分析摘要
                analysis = list(vba_parser.analyze_macros())
                if analysis:
                    suspicious = [
                        {"type": str(a[0]), "keyword": str(a[1]), "description": str(a[2])}
                        for a in analysis
                    ]
                    result["security_analysis"] = suspicious
            vba_parser.close()
        except ImportError:
            result["source_note"] = (
                "VBA 宏已检测到，但未安装 oletools 库，无法提取源代码。"
                "安装方式: pip install oletools"
            )
        except Exception as exc:
            result["source_error"] = f"VBA 源码提取失败: {exc}"

    return result


# ── 数值强制转换辅助 ──────────────────────────────────────


def _coerce_numeric(series: pd.Series) -> pd.Series:
    """尝试将含文本格式的数值列转换为 float。

    处理常见格式：千分位逗号 "1,234.56"、带单位后缀 "1,234.56元"、
    百分号 "16.36%"。无法转换的值保留 NaN。
    """
    if pd.api.types.is_numeric_dtype(series):
        return series

    cleaned = series.astype(str).str.strip()
    # 移除常见中文单位后缀
    cleaned = cleaned.str.replace(r'[元万亿份个台件套]$', '', regex=True)
    # 移除百分号并标记
    is_pct = cleaned.str.endswith('%')
    cleaned = cleaned.str.replace('%', '', regex=False)
    # 移除千分位逗号
    cleaned = cleaned.str.replace(',', '', regex=False)
    # 转换为数值
    result = pd.to_numeric(cleaned, errors='coerce')
    # 百分比列除以 100
    if is_pct.any() and not is_pct.all():
        # 混合格式，不做百分比转换
        pass
    elif is_pct.all():
        result = result / 100
    return result




# ── Excel 对比工具 ─────────────────────────────────────────


def _load_sheet_as_df(
    safe_path: Any,
    sheet_name: str | None,
    header_row: int | None = None,
) -> tuple[pd.DataFrame, list[str]]:
    """加载一个 sheet 为 DataFrame，返回 (df, sheet_names)。"""
    from openpyxl import load_workbook

    if _is_csv_file(safe_path):
        df, _ = _read_df(safe_path, None, max_rows=None, header_row=header_row)
        return df, ["Sheet1"]

    wb = load_workbook(safe_path, read_only=True, data_only=True)
    sheet_names = list(wb.sheetnames)
    wb.close()

    df, effective_header = _read_df(safe_path, sheet_name or sheet_names[0], max_rows=None, header_row=header_row)
    # Restore raw typed values and formulas instead of treating absent caches
    # as blanks. Header detection is shared with the other analysis tools.
    wb = load_workbook(safe_path, read_only=True, data_only=False)
    try:
        ws = wb[sheet_name or sheet_names[0]]
        start_row = effective_header + 2 if effective_header >= 0 else 1
        body = [list(row) for row in ws.iter_rows(min_row=start_row, values_only=True)]
        width = len(df.columns)
        body = [(row + [None] * width)[:width] for row in body]
        while body and all(value is None for value in body[-1]):
            body.pop()
        df = pd.DataFrame(body, columns=df.columns, dtype=object)
        df.attrs["header_row"] = effective_header + 1
    finally:
        wb.close()
    return df, sheet_names


def _compare_matrix(safe_path: Any, sheet_name: str) -> list[list[Any]]:
    """Literal coordinates including headers, title rows and formula text."""
    if _is_csv_file(safe_path):
        import csv
        from excelmanus.workbook.snapshot import csv_separator_for

        with open(safe_path, encoding=_detect_csv_encoding(safe_path), newline="") as handle:
            return list(csv.reader(handle, delimiter=csv_separator_for(safe_path)))
    from openpyxl import load_workbook

    wb = load_workbook(safe_path, read_only=True, data_only=False)
    try:
        rows = [list(row) for row in wb[sheet_name].iter_rows(values_only=True)]
        while rows and all(v is None for v in rows[-1]):
            rows.pop()
        return rows
    finally:
        wb.close()


def _compare_value(value: Any) -> Any:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    return _serialize_cell_value(value)


def _same_compare_value(left: Any, right: Any) -> bool:
    if type(left) is not type(right):
        return (type(left) in (int, float) and type(right) in (int, float)
                and left == right)
    return left == right



def _formula_facts(safe_path: Any, sheet_name: str | None) -> tuple[dict[str, str], str]:
    """Return formula text and whether any formula lacks a cached value."""
    if _is_csv_file(safe_path):
        return {}, "not_applicable"
    from openpyxl import load_workbook

    formulas: dict[str, str] = {}
    uncached = False
    wb_formula = load_workbook(safe_path, read_only=True, data_only=False)
    wb_values = load_workbook(safe_path, read_only=True, data_only=True)
    try:
        title = sheet_name or wb_formula.sheetnames[0]
        if title not in wb_formula.sheetnames or title not in wb_values.sheetnames:
            return {}, "unknown"
        ws_f = wb_formula[title]
        ws_v = wb_values[title]
        for row_f, row_v in zip(ws_f.iter_rows(), ws_v.iter_rows()):
            for cell_f, cell_v in zip(row_f, row_v):
                value = cell_f.value
                if isinstance(value, str) and value.startswith("="):
                    formulas[cell_f.coordinate] = value
                    if cell_v.value is None:
                        uncached = True
    finally:
        wb_formula.close()
        wb_values.close()
    return formulas, ("uncached" if uncached else ("cached" if formulas else "none"))


def compare_excel(
    file_a: str,
    file_b: str,
    sheet_a: str = "",
    sheet_b: str = "",
    ignore_style: bool = True,
    key_columns: list[str] | None = None,
    max_diffs: int = 500,
    alignment: str = "position",
) -> ToolResult:
    """对比两个 Excel 文件（或同一文件的两个 Sheet），返回结构化差异报告。

    支持两种对比模式：
    - alignment=position（默认）：按单元格行列坐标对比
    - alignment=key：按 key_columns join 后对比

    Args:
        file_a: 基准文件路径
        file_b: 对比文件路径（与 file_a 相同时用于跨 Sheet 对比）
        sheet_a: file_a 的工作表名（空=工作簿级清单 + 第一张表数据；多表不套 I8）
        sheet_b: file_b 的工作表名（空=同上）
        ignore_style: 必须为 True；false 返回不支持
        key_columns: alignment=key 时的关键列
        max_diffs: 最大差异数量（超出截断）
        alignment: position 按坐标；key 按关键列

    Returns:
        ToolResult（value 含差异摘要，ui_meta.diff 供 SSE 投影）。
    """
    if isinstance(max_diffs, bool) or not isinstance(max_diffs, int) or max_diffs < 1:
        return error_result("max_diffs 必须是正整数", code="INVALID_ARGS")
    if ignore_style is False:
        return _error_payload_result(
            {"error": "当前不支持样式对比。请省略 ignore_style 或设为 true。", "code": "INVALID_ARGS"},
            code="INVALID_ARGS",
        )
    align = str(alignment or "position").strip().lower()
    if align not in {"position", "key"}:
        return _error_payload_result(
            {"error": "alignment 必须是 position 或 key", "code": "INVALID_ARGS"},
            code="INVALID_ARGS",
        )
    if align == "position" and key_columns:
        return _error_payload_result(
            {
                "error": "alignment=position 不能同时提供 key_columns；按键对齐请用 alignment=key",
                "code": "INVALID_ARGS",
            },
            code="INVALID_ARGS",
        )
    if align == "key" and not key_columns:
        return _error_payload_result(
            {"error": "alignment=key 需要 key_columns", "code": "INVALID_ARGS"},
            code="INVALID_ARGS",
        )
    guard = _get_guard()

    try:
        live_a = guard.resolve_and_validate(file_a)
    except Exception as e:
        return _error_payload_result({"error": f"文件 A 路径无效: {e}"}, code="PATH_INVALID")
    try:
        live_b = guard.resolve_and_validate(file_b)
    except Exception as e:
        return _error_payload_result({"error": f"文件 B 路径无效: {e}"}, code="PATH_INVALID")
    not_found_a = check_file_exists(live_a, file_a, guard)
    if not_found_a is not None:
        return not_found_a
    not_found_b = check_file_exists(live_b, file_b, guard)
    if not_found_b is not None:
        return not_found_b
    snap_a, err_a = _open_tool_snapshot(file_a)
    if err_a is not None:
        return err_a
    snap_b, err_b = (snap_a, None) if live_a.resolve() == live_b.resolve() else _open_tool_snapshot(file_b)
    if err_b is not None:
        return err_b
    safe_a = snap_a.backing_path
    safe_b = snap_b.backing_path
    rel_a = snap_a.file.relative
    rel_b = snap_b.file.relative

    # ── 2. 加载数据 ──
    # 预读 sheet 列表，用于错误提示
    _sheets_a_hint: list[str] = []
    _sheets_b_hint: list[str] = []
    if not _is_csv_file(safe_a):
        try:
            from openpyxl import load_workbook as _lw
            _wb = _lw(safe_a, read_only=True, data_only=True)
            _sheets_a_hint = list(_wb.sheetnames)
            _wb.close()
        except Exception:
            pass
    if not _is_csv_file(safe_b):
        try:
            from openpyxl import load_workbook as _lw2
            _wb2 = _lw2(safe_b, read_only=True, data_only=True)
            _sheets_b_hint = list(_wb2.sheetnames)
            _wb2.close()
        except Exception:
            pass

    from excelmanus.workbook.snapshot import SnapshotError, require_default_sheet

    sheet_a = sheet_a or None
    sheet_b = sheet_b or None
    # 显式 sheet 走 I8/SHEET_NOT_FOUND；省略则做工作簿级清单，不强迫单表。
    if sheet_a and not snap_a.is_csv() and _sheets_a_hint:
        try:
            sheet_a = require_default_sheet(_sheets_a_hint, sheet_a)
        except SnapshotError as exc:
            return error_result(str(exc), code=exc.code, fields=exc.fields or None)
    if sheet_b and not snap_b.is_csv() and _sheets_b_hint:
        try:
            sheet_b = require_default_sheet(_sheets_b_hint, sheet_b)
        except SnapshotError as exc:
            return error_result(str(exc), code=exc.code, fields=exc.fields or None)

    try:
        df_a, sheets_a = _load_sheet_as_df(safe_a, sheet_a or None)
    except Exception as e:
        return _error_payload_result(
            {"error": f"无法读取文件 A: {e}", "available_sheets": _sheets_a_hint},
            code="EXECUTION_FAILED",
        )
    try:
        df_b, sheets_b = _load_sheet_as_df(safe_b, sheet_b or None)
    except Exception as e:
        return _error_payload_result(
            {"error": f"无法读取文件 B: {e}", "available_sheets": _sheets_b_hint},
            code="EXECUTION_FAILED",
        )

    resolved_sheet_a = sheet_a or (sheets_a[0] if sheets_a else "Sheet1")
    resolved_sheet_b = sheet_b or (sheets_b[0] if sheets_b else "Sheet1")
    formula_a, formula_status_a = _formula_facts(safe_a, resolved_sheet_a)
    formula_b, formula_status_b = _formula_facts(safe_b, resolved_sheet_b)

    # ── 3. 结构对比 ──
    # 构建 str→原始列名 映射，用于 .at[] 访问
    col_map_a: dict[str, Any] = {str(c): c for c in df_a.columns}
    col_map_b: dict[str, Any] = {str(c): c for c in df_b.columns}
    cols_a = set(col_map_a.keys())
    cols_b = set(col_map_b.keys())
    columns_added = sorted(cols_b - cols_a)
    columns_deleted = sorted(cols_a - cols_b)
    str_cols_a_list = list(col_map_a.keys())
    common_cols = sorted(cols_a & cols_b, key=lambda c: str_cols_a_list.index(c) if c in str_cols_a_list else 0)

    sheets_only_a = sorted(set(sheets_a) - set(sheets_b)) if snap_a.file.relative != snap_b.file.relative else []
    sheets_only_b = sorted(set(sheets_b) - set(sheets_a)) if snap_a.file.relative != snap_b.file.relative else []

    # Count all differences; only the returned detail list is capped.
    cell_diffs: list[dict[str, Any]] = []
    cells_different = rows_added = rows_deleted = rows_modified = total_cells_compared = 0
    duplicate_keys_a: list[str] = []
    duplicate_keys_b: list[str] = []
    unmatched_in_a: list[str] = []
    unmatched_in_b: list[str] = []
    alignment = align

    def add_difference(item: dict[str, Any]) -> None:
        nonlocal cells_different
        cells_different += 1
        if len(cell_diffs) < max_diffs:
            cell_diffs.append(item)

    if alignment == "key":
        missing = [k for k in (key_columns or []) if k not in cols_a or k not in cols_b]
        if missing:
            return error_result(f"key_columns 不在两边的列中: {missing}", code="NOT_FOUND",
                                fields={"available_columns_a": sorted(cols_a), "available_columns_b": sorted(cols_b)})
        key_a = [col_map_a[k] for k in key_columns]
        key_b = [col_map_b[k] for k in key_columns]
        dup_a = df_a.duplicated(subset=key_a, keep=False)
        dup_b = df_b.duplicated(subset=key_b, keep=False)
        if dup_a.any() or dup_b.any():
            return error_result("业务主键不唯一，无法确定按键对齐；请增加 key_columns 或改用 position。",
                                code="INVALID_ARGS", fields={
                                    "duplicate_keys_a": df_a.loc[dup_a, key_a].drop_duplicates().head(20).to_dict("records"),
                                    "duplicate_keys_b": df_b.loc[dup_b, key_b].drop_duplicates().head(20).to_dict("records"),
                                })
        if df_a[key_a].isna().any().any() or df_b[key_b].isna().any().any():
            return error_result("业务主键含空值，请先清理或改用 position。", code="INVALID_ARGS")
        left = df_a.set_index(key_a)
        right = df_b.set_index(key_b)
        keys_a, keys_b = set(left.index.tolist()), set(right.index.tolist())
        rows_deleted, rows_added = len(keys_a - keys_b), len(keys_b - keys_a)
        unmatched_in_a = [str(k) for k in sorted(keys_a - keys_b, key=str)][:50]
        unmatched_in_b = [str(k) for k in sorted(keys_b - keys_a, key=str)][:50]
        for key in sorted(keys_a & keys_b, key=str):
            changed = False
            for col in common_cols:
                if col in key_columns:
                    continue
                total_cells_compared += 1
                va = _compare_value(left.loc[key, col_map_a[col]])
                vb = _compare_value(right.loc[key, col_map_b[col]])
                if not _same_compare_value(va, vb):
                    changed = True
                    add_difference({"key": str(key), "column": col, "old": va, "new": vb})
            rows_modified += int(changed)
    else:
        from itertools import zip_longest
        from openpyxl.utils import get_column_letter

        matrix_a = _compare_matrix(safe_a, resolved_sheet_a)
        matrix_b = _compare_matrix(safe_b, resolved_sheet_b)
        rows_added = max(0, len(matrix_b) - len(matrix_a))
        rows_deleted = max(0, len(matrix_a) - len(matrix_b))
        for row_number, (row_a, row_b) in enumerate(zip_longest(matrix_a, matrix_b, fillvalue=[]), 1):
            changed = False
            for col_number, (va, vb) in enumerate(zip_longest(row_a, row_b), 1):
                total_cells_compared += 1
                va, vb = _compare_value(va), _compare_value(vb)
                if not _same_compare_value(va, vb):
                    changed = True
                    item = {"cell": f"{get_column_letter(col_number)}{row_number}", "old": va, "new": vb}
                    if isinstance(va, str) and va.startswith("="):
                        item["old_formula"] = va
                    if isinstance(vb, str) and vb.startswith("="):
                        item["new_formula"] = vb
                    add_difference(item)
            if row_number <= min(len(matrix_a), len(matrix_b)):
                rows_modified += int(changed)

    sample_diffs = cell_diffs[:10]
    truncated = cells_different > len(sample_diffs) or (
        alignment == "key" and (rows_deleted > len(unmatched_in_a) or rows_added > len(unmatched_in_b))
    )

    is_same_file = snap_a.file.relative == snap_b.file.relative and snap_a.id.workspace_key == snap_b.id.workspace_key
    diff_mode = "cross_sheet" if is_same_file and (sheet_a or sheet_b) else "cross_file"

    result: dict[str, Any] = {
        "status": "success",
        "diff_mode": diff_mode,
        "file_a": snap_a.file.relative,
        "file_b": snap_b.file.relative,
        "content_version": snap_a.content_version,
        "content_version_a": snap_a.content_version,
        "content_version_b": snap_b.content_version,
        "sheet_a": resolved_sheet_a,
        "sheet_b": resolved_sheet_b,
        "scope": "explicit_sheets" if sheet_a and sheet_b else "first_sheet_when_omitted",
        "compared_sheets": {"a": [resolved_sheet_a], "b": [resolved_sheet_b]},
        "formula_status": {"a": formula_status_a, "b": formula_status_b},
        "summary": {
            "total_cells_compared": total_cells_compared,
            "cells_different": cells_different,
            "rows_added": rows_added,
            "rows_deleted": rows_deleted,
            "rows_modified": rows_modified,
            "columns_added": columns_added,
            "columns_deleted": columns_deleted,
            "sheets_only_in_a": sheets_only_a,
            "sheets_only_in_b": sheets_only_b,
        },
        "sample_diffs": sample_diffs,
        "truncated": truncated,
    }

    structure_changed = bool(columns_added or columns_deleted or sheets_only_a or sheets_only_b)
    if cells_different == 0 and rows_added == 0 and rows_deleted == 0 and not structure_changed:
        if result["scope"] == "first_sheet_when_omitted":
            result["hint"] = "两边指定/默认的第一张工作表数据完全相同；未比较其他工作表。"
        else:
            result["hint"] = "两个文件（或 Sheet）的数据完全相同。"
    else:
        parts = []
        if cells_different > 0:
            parts.append(f"{cells_different} 处单元格差异")
        if rows_added > 0:
            parts.append(f"{rows_added} 行新增")
        if rows_deleted > 0:
            parts.append(f"{rows_deleted} 行删除")
        if columns_added:
            parts.append(f"新增列: {', '.join(columns_added)}")
        if columns_deleted:
            parts.append(f"删除列: {', '.join(columns_deleted)}")
        if sheets_only_a:
            parts.append(f"仅 A 有工作表: {', '.join(sheets_only_a)}")
        if sheets_only_b:
            parts.append(f"仅 B 有工作表: {', '.join(sheets_only_b)}")
        if parts:
            suffix = (
                "按键对齐时增删键见正文；单元格样本不是全表。"
                if alignment == "key"
                else "单元格样本不是全表。"
            )
            result["hint"] = f"共发现 {'、'.join(parts)}。{suffix}"
        else:
            result["hint"] = "结构或数据存在差异。"

    result["duplicate_keys_a"] = duplicate_keys_a
    result["duplicate_keys_b"] = duplicate_keys_b
    result["unmatched_in_a"] = unmatched_in_a
    result["unmatched_in_b"] = unmatched_in_b
    result["coverage"] = {
        "kind": "truncated" if truncated else "complete",
        "diffs_returned": len(sample_diffs),
        "diffs_total": cells_different,
        "counts_complete": True,
        "unmatched_keys_returned": {"a": len(unmatched_in_a), "b": len(unmatched_in_b)},
        "unmatched_keys_total": {"a": rows_deleted, "b": rows_added} if alignment == "key" else None,
        "formula_comparison": "text; no recalculation",
        "max_diffs": max_diffs,
        "cells_compared": total_cells_compared,
    }

    return _finalize_compare_excel_result(
        result,
        rel_path_a=rel_a,
        rel_path_b=rel_b,
        sheet_a=resolved_sheet_a,
        sheet_b=resolved_sheet_b,
        key_columns=key_columns,
        alignment=alignment,
        scope=result.get("scope") or "explicit_sheets",
    )


# ── scan_excel_snapshot ──────────────────────────────────────

_SNAPSHOT_MAX_SHEETS = 10
_SNAPSHOT_MAX_SAMPLE_VALUES = 5
_SNAPSHOT_MAX_TOP_VALUES = 5
_SNAPSHOT_MAX_SIGNALS = 20
_SNAPSHOT_CATEGORICAL_THRESHOLD = 20
_SCAN_BLANK_TOKENS = frozenset({"", "nan", "none", "nat", "null", "#n/a"})


def _is_scan_blank(val: Any) -> bool:
    """扫描统计把空串当缺失；权威读仍保留空串。"""
    if val is None:
        return True
    if isinstance(val, float) and pd.isna(val):
        return True
    if isinstance(val, str) and val.strip().lower() in _SCAN_BLANK_TOKENS:
        return True
    try:
        return bool(pd.isna(val))
    except (TypeError, ValueError):
        return False


def _classify_scan_value(val: Any) -> str:
    """扫描用语义类。dtype=str 后仍识别数字/布尔/日期文本。"""
    if isinstance(val, bool):
        return "boolean"
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        return "numeric"
    if isinstance(val, str):
        text = val.strip()
        lowered = text.lower()
        if lowered in {"true", "false"}:
            return "boolean"
        numeric = pd.to_numeric(pd.Series([text.replace(",", "")]), errors="coerce").iloc[0]
        if pd.notna(numeric):
            return "numeric"
        if any(sep in text for sep in ("-", "/")) and any(ch.isdigit() for ch in text):
            return "date"
        return "string"
    type_name = type(val).__name__.lower()
    if "date" in type_name or "time" in type_name:
        return "date"
    return "string"


def _scan_non_blank(series: "pd.Series") -> pd.Series:
    return series[~series.map(_is_scan_blank)]


def _infer_column_type(series: "pd.Series") -> str:
    """根据实际值分布推断列类型（不只看 dtype）。

    Returns:
        "numeric" | "string" | "date" | "boolean" | "mixed" | "empty"
    """
    non_null = _scan_non_blank(series)
    if non_null.empty:
        return "empty"

    type_counts: dict[str, int] = {}
    for val in non_null.head(200):
        kind = _classify_scan_value(val)
        type_counts[kind] = type_counts.get(kind, 0) + 1

    if not type_counts:
        return "empty"

    dominant = max(type_counts, key=type_counts.get)  # type: ignore[arg-type]
    total = sum(type_counts.values())
    dominant_ratio = type_counts[dominant] / total

    if len(type_counts) == 1:
        return dominant
    if dominant_ratio >= 0.9:
        return dominant
    return "mixed"


def _detect_mixed_types(series: "pd.Series") -> dict[str, int] | None:
    """检测同列语义类型分布。仅在存在混合时返回。"""
    non_null = _scan_non_blank(series)
    if non_null.empty:
        return None

    type_counts: dict[str, int] = {}
    for val in non_null.head(500):
        kind = _classify_scan_value(val)
        type_counts[kind] = type_counts.get(kind, 0) + 1

    if len(type_counts) <= 1:
        return None
    return type_counts


def _detect_outliers_iqr(series: "pd.Series") -> int:
    """使用 IQR 方法检测数值列的异常值个数。"""
    numeric = pd.to_numeric(series, errors="coerce").dropna()
    if len(numeric) < 4:
        return 0
    q1 = float(numeric.quantile(0.25))
    q3 = float(numeric.quantile(0.75))
    iqr = q3 - q1
    if iqr == 0:
        return 0
    lower = q1 - 1.5 * iqr
    upper = q3 + 1.5 * iqr
    return int(((numeric < lower) | (numeric > upper)).sum())


def _compute_column_stats(
    series: "pd.Series",
    inferred_type: str,
    col_name: str,
) -> dict[str, Any]:
    """计算单列的统计信息。"""
    total = len(series)
    null_count = int(series.map(_is_scan_blank).sum())
    null_rate = round(null_count / total, 4) if total > 0 else 0.0
    non_null = _scan_non_blank(series)
    unique_count = int(non_null.nunique()) if not non_null.empty else 0

    stats: dict[str, Any] = {
        "name": col_name,
        "dtype": str(series.dtype),
        "inferred_type": inferred_type,
        "null_count": null_count,
        "null_rate": null_rate,
        "unique_count": unique_count,
    }

    # 样本值
    sample = non_null.head(_SNAPSHOT_MAX_SAMPLE_VALUES).tolist()
    stats["sample_values"] = [
        str(v) if not isinstance(v, (int, float, bool)) else v
        for v in sample
    ]

    # 数值列特有统计
    if inferred_type == "numeric" and not non_null.empty:
        numeric = pd.to_numeric(non_null, errors="coerce").dropna()
        if not numeric.empty:
            stats["min"] = round(float(numeric.min()), 2)
            stats["max"] = round(float(numeric.max()), 2)
            stats["mean"] = round(float(numeric.mean()), 2)
            stats["median"] = round(float(numeric.median()), 2)
            if len(numeric) >= 4:
                stats["q1"] = round(float(numeric.quantile(0.25)), 2)
                stats["q3"] = round(float(numeric.quantile(0.75)), 2)
            stats["outlier_count"] = _detect_outliers_iqr(numeric)

    # 分类列（低基数 string/mixed）特有统计
    if unique_count > 0 and unique_count <= _SNAPSHOT_CATEGORICAL_THRESHOLD:
        vc = non_null.value_counts(dropna=True).head(_SNAPSHOT_MAX_TOP_VALUES)
        stats["top_values"] = [
            {"value": str(k), "count": int(v)} for k, v in vc.items()
        ]

    # 类型混杂检测
    mixed = _detect_mixed_types(series)
    if mixed is not None:
        stats["mixed_type_counts"] = mixed

    return stats


def _detect_cross_sheet_relationships(
    sheet_columns: dict[str, list[str]],
    sheet_dfs: dict[str, "pd.DataFrame"],
) -> list[dict[str, Any]]:
    """检测跨 Sheet 关联：共享列名 + 值重叠率。"""
    relationships: list[dict[str, Any]] = []
    sheet_names = list(sheet_columns.keys())

    # 共享列名检测
    if len(sheet_names) >= 2:
        from itertools import combinations
        for s1, s2 in combinations(sheet_names, 2):
            shared = set(sheet_columns[s1]) & set(sheet_columns[s2])
            if shared:
                relationships.append({
                    "type": "shared_column_name",
                    "columns": sorted(shared),
                    "sheets": [s1, s2],
                })

    # 候选外键检测（值重叠率）
    for rel in list(relationships):
        if rel["type"] != "shared_column_name":
            continue
        s1, s2 = rel["sheets"]
        df1, df2 = sheet_dfs.get(s1), sheet_dfs.get(s2)
        if df1 is None or df2 is None:
            continue
        for col in rel["columns"]:
            if col not in df1.columns or col not in df2.columns:
                continue
            vals1 = set(df1[col].dropna().astype(str).tolist()[:500])
            vals2 = set(df2[col].dropna().astype(str).tolist()[:500])
            if not vals1 or not vals2:
                continue
            overlap = len(vals1 & vals2)
            smaller = min(len(vals1), len(vals2))
            rate = round(overlap / smaller, 2) if smaller > 0 else 0
            if rate >= 0.3:
                relationships.append({
                    "type": "candidate_foreign_key",
                    "source": {"sheet": s2, "column": col},
                    "target": {"sheet": s1, "column": col},
                    "overlap_rate": rate,
                })

    return relationships


def _generate_quality_signals(
    sheets_data: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """根据扫描结果生成阈值化质量信号。"""
    signals: list[dict[str, Any]] = []

    for sheet in sheets_data:
        sheet_name = sheet["name"]
        total_rows = sheet.get("rows", 0)

        for col in sheet.get("columns", []):
            col_name = col["name"]
            null_rate = col.get("null_rate", 0)

            form_layout = bool(sheet.get("is_form_document"))

            # empty_column
            if null_rate >= 1.0:
                if not form_layout:
                    signals.append({
                        "severity": "high",
                        "type": "empty_column",
                        "sheet": sheet_name,
                        "column": col_name,
                        "detail": "该列全部为空值",
                    })
                continue

            # missing_data
            if null_rate > 0.3 and not form_layout:
                signals.append({
                    "severity": "high",
                    "type": "missing_data",
                    "sheet": sheet_name,
                    "column": col_name,
                    "detail": f"{col['null_count']} 个空值 ({null_rate:.1%})",
                })
            elif null_rate > 0.05 and not form_layout:
                signals.append({
                    "severity": "medium",
                    "type": "missing_data",
                    "sheet": sheet_name,
                    "column": col_name,
                    "detail": f"{col['null_count']} 个空值 ({null_rate:.1%})",
                })

            # type_mixed
            if col.get("mixed_type_counts") and not form_layout:
                counts = col["mixed_type_counts"]
                desc = " + ".join(f"{k}({v})" for k, v in counts.items())
                signals.append({
                    "severity": "high",
                    "type": "type_mixed",
                    "sheet": sheet_name,
                    "column": col_name,
                    "detail": f"混合了 {desc}",
                })

            # outliers
            outlier_count = col.get("outlier_count", 0)
            if outlier_count > 0:
                signals.append({
                    "severity": "medium",
                    "type": "outliers",
                    "sheet": sheet_name,
                    "column": col_name,
                    "detail": f"{outlier_count} 个异常值 (IQR 方法)",
                })

            # constant_column
            if col.get("unique_count") == 1 and col.get("null_count", 0) == 0:
                signals.append({
                    "severity": "low",
                    "type": "constant_column",
                    "sheet": sheet_name,
                    "column": col_name,
                    "detail": "仅 1 个唯一值",
                })

            # high_cardinality
            if (
                col.get("inferred_type") == "string"
                and total_rows > 10
                and col.get("unique_count", 0) > 0
            ):
                unique_rate = col["unique_count"] / max(total_rows, 1)
                if unique_rate > 0.9:
                    signals.append({
                        "severity": "info",
                        "type": "high_cardinality",
                        "sheet": sheet_name,
                        "column": col_name,
                        "detail": f"唯一率 {unique_rate:.1%}，疑似 ID 列",
                    })

        # duplicate_rows
        dup_count = sheet.get("duplicate_row_count")
        if dup_count is not None and dup_count > 0:
            dup_rate = dup_count / max(total_rows, 1)
            sev = "high" if dup_rate > 0.05 else "medium"
            signals.append({
                "severity": sev,
                "type": "duplicate_rows",
                "sheet": sheet_name,
                "detail": f"{dup_count} 行完全重复 ({dup_rate:.1%})",
            })

        # high_merge_ratio — 合并单元格占比高，数据读取可能产生大量 NaN
        merged_summary = sheet.get("merged_cell_summary")
        if merged_summary:
            ratio_str = merged_summary.get("merged_cell_ratio", "0%")
            data_merged = merged_summary.get("data_merged_ranges", 0)
            try:
                ratio_val = float(ratio_str.rstrip("%")) / 100
            except (ValueError, AttributeError):
                ratio_val = 0.0
            if ratio_val > _FORM_MERGED_CELL_RATIO_THRESHOLD:
                detail_parts = [f"合并单元格占比 {ratio_str}"]
                if data_merged > 0:
                    detail_parts.append(
                        f"其中 {data_merged} 处数据区跨行合并（pandas 读取会产生 NaN）"
                    )
                col_groups = merged_summary.get("column_group_spans", [])
                if col_groups:
                    detail_parts.append(
                        f"列组标头: {', '.join(col_groups[:5])}"
                    )
                signals.append({
                    "severity": "high",
                    "type": "high_merge_ratio",
                    "sheet": sheet_name,
                    "detail": "；".join(detail_parts),
                })

    return signals[:_SNAPSHOT_MAX_SIGNALS]


def scan_excel_snapshot(
    file_path: str,
    max_sample_rows: int = 500,
    include_relationships: bool = True,
    sheet_name: str | None = None,
    expected_version: str | None = None,
    header_row: int | None = None,
) -> ToolResult:
    """一次性扫描 Excel 文件，返回所有 Sheet 的 schema、列统计、数据质量信号。

    Args:
        file_path: Excel/CSV 文件路径（相对或绝对）。
        max_sample_rows: 大表采样行数上限（默认 500）。
        include_relationships: 是否检测跨 Sheet 关联。

    Returns:
        ToolResult（value 含扫描报告，model_text 为短摘要）。
    """
    guard = _get_guard()
    live_path = guard.resolve_and_validate(file_path)
    not_found = check_file_exists(live_path, file_path, guard)
    if not_found is not None:
        return not_found

    from excelmanus.workbook.snapshot import SnapshotError, require_default_sheet

    snap, snap_err = _open_tool_snapshot(file_path, expected_version=expected_version)
    if snap_err is not None:
        return snap_err
    safe_path = snap.backing_path
    rel_path = snap.file.relative
    bound_version = snap.content_version
    file_size = len(snap.read_bytes())
    size_str = _format_size(file_size)

    if snap.is_csv():
        return _scan_csv_snapshot(
            safe_path, size_str, max_sample_rows,
            rel_path=rel_path, bound_version=bound_version, snapshot=snap,
            header_row=header_row,
        )

    if sheet_name is not None:
        wb_names = snap.open_workbook(data_only=True, read_only=True)
        try:
            try:
                sheet_name = require_default_sheet(list(wb_names.sheetnames), sheet_name)
            except SnapshotError as exc:
                return error_result(str(exc), code=exc.code, fields=exc.fields or None)
        finally:
            wb_names.close()
    requested_sheet = sheet_name

    from openpyxl import load_workbook

    # 元数据扫描（read_only=True，快速获取行列数/公式/合并）
    wb_meta = load_workbook(safe_path, read_only=True, data_only=True)
    sheet_metas: list[dict[str, Any]] = []
    all_meta_sheets = list(wb_meta.worksheets)
    if requested_sheet:
        # An explicit target must not disappear behind the overview cap.
        selected_meta_sheets = [
            ws for ws in all_meta_sheets if ws.title == requested_sheet
        ]
    else:
        selected_meta_sheets = all_meta_sheets[:_SNAPSHOT_MAX_SHEETS]
    for ws in selected_meta_sheets:
        if requested_sheet and ws.title != requested_sheet:
            continue
        # 部分导出文件不写 <dimension>，read_only 下 max_row/max_column 为 0；
        # reset_dimensions 强制按实际行扫描恢复真实边界。
        if not ws.max_row or not ws.max_column:
            try:
                ws.reset_dimensions()
                ws.calculate_dimension(force=True)
            except (TypeError, ValueError):
                pass
        meta: dict[str, Any] = {
            "name": ws.title,
            "rows": ws.max_row or 0,
            "cols": ws.max_column or 0,
        }
        sheet_metas.append(meta)
    wb_meta.close()

    # 检测公式和合并单元格（需要非 read_only 模式，但只读前几行）
    try:
        wb_full = load_workbook(safe_path, read_only=False, data_only=False)
        if requested_sheet:
            selected_full_sheets = [
                ws for ws in wb_full.worksheets if ws.title == requested_sheet
            ]
        else:
            selected_full_sheets = list(wb_full.worksheets[:_SNAPSHOT_MAX_SHEETS])
        for i, ws in enumerate(selected_full_sheets):
            if requested_sheet and ws.title != requested_sheet:
                continue
            meta_i = next((idx for idx, item in enumerate(sheet_metas) if item["name"] == ws.title), None)
            if meta_i is None:
                continue
            i = meta_i
            if i < len(sheet_metas):
                has_merged = len(ws.merged_cells.ranges) > 0
                sheet_metas[i]["has_merged_cells"] = has_merged
                # 合并单元格摘要：语义分类 + 合并率 + 处理建议
                if has_merged:
                    merged_summary = _collect_merged_cell_summary(ws)
                    if merged_summary:
                        sheet_metas[i]["merged_cell_summary"] = merged_summary
                # 检测公式：扫描前 20 行
                has_formulas = False
                for row in ws.iter_rows(min_row=1, max_row=min(20, ws.max_row or 0), values_only=False):
                    for cell in row:
                        if isinstance(cell.value, str) and cell.value.startswith("="):
                            has_formulas = True
                            break
                    if has_formulas:
                        break
                sheet_metas[i]["has_formulas"] = has_formulas
                sheet_metas[i]["formula_scan"] = {"rows_scanned": min(20, ws.max_row or 0), "complete": (ws.max_row or 0) <= 20}
        wb_full.close()
    except Exception:
        for meta in sheet_metas:
            meta.setdefault("has_formulas", False)
            meta.setdefault("has_merged_cells", False)

    # 逐 Sheet 统计
    sheets_data: list[dict[str, Any]] = []
    sheet_columns: dict[str, list[str]] = {}
    sheet_dfs: dict[str, pd.DataFrame] = {}

    for meta in sheet_metas:
        sheet_name = meta["name"]
        total_rows = meta["rows"]
        data_rows = max(0, total_rows - 1)  # 减去 header
        sampled = data_rows > max_sample_rows

        try:
            read_kwargs = _build_read_kwargs(safe_path, sheet_name, max_rows=max_sample_rows if sampled else None, header_row=header_row)
            form_type = read_kwargs.pop("_form_type_document", False)
            internal_header = read_kwargs.get("header")
            public_header = (
                "form_type_document" if form_type or internal_header is None and form_type
                else _to_public_header_row(-1 if form_type else int(internal_header or 0))
            )
            df = pd.read_excel(**read_kwargs)

            if form_type:
                df.columns = [f"Col_{i}" for i in range(len(df.columns))]
        except Exception as exc:
            sheets_data.append({
                **meta,
                "error": f"读取失败: {exc}",
                "columns": [],
            })
            continue

        # 列统计
        columns_stats: list[dict[str, Any]] = []
        col_names: list[str] = []
        for col in df.columns:
            col_str = str(col)
            col_names.append(col_str)
            inferred = _infer_column_type(df[col])
            stats = _compute_column_stats(df[col], inferred, col_str)
            columns_stats.append(stats)

        # 重复行检测
        dup_count: int | None = None
        if data_rows <= 10000:
            try:
                dup_count = int(df.duplicated().sum())
            except Exception:
                dup_count = None

        sheet_data: dict[str, Any] = {
            **meta,
            "header_row": public_header,
            "duplicate_row_count": dup_count,
            "columns": columns_stats,
            "is_form_document": bool(form_type),
        }
        if sampled:
            sheet_data["sampled"] = True
            sheet_data["sample_size"] = len(df)

        sheets_data.append(sheet_data)
        sheet_columns[sheet_name] = col_names
        sheet_dfs[sheet_name] = df

    # 跨 Sheet 关联
    relationships: list[dict[str, Any]] = []
    if include_relationships and len(sheet_columns) >= 2:
        relationships = _detect_cross_sheet_relationships(sheet_columns, sheet_dfs)

    # 质量信号
    quality_signals = _generate_quality_signals(sheets_data)
    form_layout = any(bool(item.get("is_form_document")) for item in sheets_data)

    result: dict[str, Any] = {
        "file": rel_path,
        "size": size_str,
        "sheet_count": len(sheet_metas),
        "sheets": sheets_data,
        "relationships": relationships,
        "quality_signals": quality_signals,
        "form_layout": form_layout,
        "resolved_sheets": [item.get("name") for item in sheets_data if isinstance(item, dict)],
    }
    if requested_sheet:
        result["resolved_sheet"] = requested_sheet
        result["scan_scope"] = "sheet"
    elif len(result["resolved_sheets"]) == 1:
        result["resolved_sheet"] = result["resolved_sheets"][0]

    if not requested_sheet and len(all_meta_sheets) > _SNAPSHOT_MAX_SHEETS:
        result["truncated"] = True
        result["truncated_note"] = f"仅扫描前 {_SNAPSHOT_MAX_SHEETS} 个 Sheet"

    return _finalize_scan_excel_snapshot_result(
        result, rel_path=rel_path, file_path=safe_path, content_version=bound_version,
    )


def _scan_csv_snapshot(
    safe_path: Any,
    size_str: str,
    max_sample_rows: int,
    *,
    rel_path: str = "",
    bound_version: str | None = None,
    snapshot: Any = None,
    header_row: int | None = None,
) -> ToolResult:
    """CSV 文件的 scan_excel_snapshot 简化实现。"""
    try:
        df, effective_header = _read_csv_df(safe_path, max_rows=max_sample_rows, header_row=_public_header_row_to_internal(header_row))
    except Exception as exc:
        return _error_payload_result(
            {"error": f"CSV 读取失败: {exc}"},
            code="EXECUTION_FAILED",
        )

    total_rows = len(df)
    sampled = False
    # 检查实际行数是否超过采样
    try:
        with open(safe_path, "r", encoding="utf-8", errors="ignore") as f:
            actual_lines = sum(1 for _ in f) - 1  # 减去 header
        if actual_lines > max_sample_rows:
            sampled = True
            total_rows = actual_lines
    except Exception:
        pass

    columns_stats = []
    for col in df.columns:
        inferred = _infer_column_type(df[col])
        stats = _compute_column_stats(df[col], inferred, str(col))
        columns_stats.append(stats)

    dup_count = int(df.duplicated().sum()) if len(df) <= 10000 else None

    sheet_data: dict[str, Any] = {
        "name": "Sheet1",
        "rows": total_rows,
        "cols": len(df.columns),
        "header_row": _to_public_header_row(effective_header),
        "duplicate_row_count": dup_count,
        "has_formulas": False,
        "has_merged_cells": False,
        "columns": columns_stats,
    }
    if sampled:
        sheet_data["sampled"] = True
        sheet_data["sample_size"] = len(df)

    quality_signals = _generate_quality_signals([sheet_data])

    result: dict[str, Any] = {
        "file": rel_path or (snapshot.file.relative if snapshot is not None else str(safe_path.name)),
        "size": size_str,
        "sheet_count": 1,
        "sheets": [sheet_data],
        "relationships": [],
        "quality_signals": quality_signals,
    }
    if snapshot is not None:
        bound_version = snapshot.content_version
    return _finalize_scan_excel_snapshot_result(
        result,
        rel_path=rel_path or str(getattr(safe_path, "name", safe_path)),
        file_path=safe_path,
        content_version=bound_version,
    )


# ── search_excel_values ──────────────────────────────────────


def search_excel_values(
    file_path: str = "",
    query: str = "",
    match_mode: str = "contains",
    sheets: list[str] | None = None,
    columns: list[str] | None = None,
    max_results: int = 50,
    case_sensitive: bool = False,
    file_paths: list[str] | None = None,
    expected_version: str | None = None,
) -> ToolResult:
    """跨 Sheet 搜索 Excel 单元格值，类似 ripgrep。

    Args:
        file_path: Excel 文件路径（相对或绝对）。
        query: 搜索字符串或正则表达式。
        match_mode: 匹配模式："contains"（默认）| "exact" | "regex" | "startswith"。
        sheets: 限定搜索的 Sheet 列表（默认全部）。
        columns: 限定搜索的列名列表（默认全部）。
        max_results: 最大返回匹配数（默认 50）。
        case_sensitive: 是否区分大小写（默认 False）。
        file_paths: 多文件搜索路径列表（与 file_path 互补，传入此参数时跨文件搜索）。

    Returns:
        ToolResult（value 含搜索结果，model_text 为短摘要）。
    """
    # 跨文件搜索：当 file_paths 包含多个文件时，逐文件搜索并合并结果
    if file_paths and len(file_paths) > 1:
        all_matches: list[dict[str, Any]] = []
        all_summary: list[dict[str, Any]] = []
        total = 0
        files_searched = 0
        rel_paths: list[str] = []
        per_file_max = max(10, max_results // len(file_paths))
        for fp in file_paths[:10]:  # 最多搜索 10 个文件
            sub_result = search_excel_values(
                file_path=fp, query=query, match_mode=match_mode,
                sheets=sheets, columns=columns, max_results=per_file_max,
                case_sensitive=case_sensitive,
                expected_version=expected_version,
            )
            sub = sub_result.value if isinstance(sub_result.value, dict) else None
            if not isinstance(sub, dict) or "error" in sub:
                continue
            files_searched += 1
            for p in sub_result.ui_meta.files:
                if p not in rel_paths:
                    rel_paths.append(p)
            if fp not in rel_paths:
                rel_paths.append(fp)
            sub_total = sub.get("total_matches", 0)
            total += sub_total
            for m in sub.get("matches", []):
                m["file"] = fp
                all_matches.append(m)
            for s in sub.get("summary_by_sheet", []):
                s["file"] = fp
                all_summary.append(s)
        # 按文件+sheet排序，截断到 max_results
        all_matches = all_matches[:max_results]
        return _finalize_search_excel_values_result(
            {
                "query": query, "match_mode": match_mode,
                "total_matches": total, "returned": len(all_matches),
                "truncated": total > len(all_matches),
                "files_searched": files_searched,
                "matches": all_matches,
                "summary_by_sheet": all_summary,
            },
            rel_paths=rel_paths,
        )

    # 单文件搜索：兼容 file_paths=[单个文件] 的情况
    if not file_path and file_paths:
        file_path = file_paths[0]
    if not file_path:
        return _error_payload_result(
            {"error": "必须提供 file_path 或 file_paths 参数"},
            code="INVALID_ARGS",
        )
    import re as _re

    guard = _get_guard()
    live_path = guard.resolve_and_validate(file_path)
    not_found = check_file_exists(live_path, file_path, guard)
    if not_found is not None:
        return not_found
    snap, snap_err = _open_tool_snapshot(file_path, expected_version=expected_version)
    if snap_err is not None:
        return snap_err
    safe_path = snap.backing_path
    rel_path = snap.file.relative

    if not query:
        return _finalize_search_excel_values_result(
            {
                "query": query,
                "match_mode": match_mode,
                "total_matches": 0,
                "returned": 0,
                "truncated": False,
                "sheets_searched": 0,
                "matches": [],
                "summary_by_sheet": [],
            },
            rel_paths=[rel_path],
            file_path=safe_path,
            snapshot=snap,
        )

    # 编译匹配函数
    if match_mode == "fuzzy":
        # 模糊匹配：将 query 拆分为子串 token，单元格值包含所有 token 即匹配
        # 拆分规则：按空格/标点分割，同时将连续中文与连续数字/字母分离
        # 额外在中文数字边界处拆分（如 '电子一班' → ['电子', '一班']）
        _cjk_re = _re.compile(r'[\u4e00-\u9fff]+|[a-zA-Z]+|\d+', _re.UNICODE)
        _CN_DIGITS_SET = set("一二三四五六七八九十")
        _pre_tokens = _cjk_re.findall(query)
        _raw_tokens: list[str] = []
        for _pt in _pre_tokens:
            # 对纯中文 token，在中文数字与非中文数字字符之间拆分
            if all('\u4e00' <= c <= '\u9fff' for c in _pt) and any(c in _CN_DIGITS_SET for c in _pt):
                _sub_re = _re.compile(r'(?<=[^\u4e00\u4e8c\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341])(?=[一二三四五六七八九十])|(?<=[一二三四五六七八九十])(?=[^\u4e00\u4e8c\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341])')
                _parts = _sub_re.split(_pt)
                _raw_tokens.extend(p for p in _parts if p)
            else:
                _raw_tokens.append(_pt)
        # 中文数字 → 阿拉伯数字等价替换，合并两套 token
        _CN_DIGIT = {"一": "1", "二": "2", "三": "3", "四": "4", "五": "5",
                     "六": "6", "七": "7", "八": "8", "九": "9", "十": "10"}
        _normalized_tokens: list[str] = []
        for _tok in _raw_tokens:
            _alt = _tok
            for _cn, _ar in _CN_DIGIT.items():
                _alt = _alt.replace(_cn, _ar)
            if not case_sensitive:
                _tok = _tok.lower()
                _alt = _alt.lower()
            _normalized_tokens.append(_tok)
            if _alt != _tok:
                _normalized_tokens.append(_alt)
        # 去重并过滤空串
        _fuzzy_tokens = list(dict.fromkeys(t for t in _normalized_tokens if t))
        if not _fuzzy_tokens:
            # 无有效 token 时回退到 contains
            if case_sensitive:
                def _match(cell_str: str) -> bool:
                    return query in cell_str
            else:
                _q_lower = query.lower()
                def _match(cell_str: str) -> bool:
                    return _q_lower in cell_str.lower()
        else:
            # 每个原始 token 至少有一个变体命中即可（原始或数字替换版本）
            # 构造 token 组：每组内的 token 是同一原始词的变体，组内 OR，组间 AND
            _token_groups: list[list[str]] = []
            for _tok in _raw_tokens:
                _group = [_tok.lower() if not case_sensitive else _tok]
                _alt = _tok
                for _cn, _ar in _CN_DIGIT.items():
                    _alt = _alt.replace(_cn, _ar)
                if _alt != _tok:
                    _group.append(_alt.lower() if not case_sensitive else _alt)
                _token_groups.append(_group)

            def _match(cell_str: str) -> bool:
                _s = cell_str if case_sensitive else cell_str.lower()
                return all(any(v in _s for v in grp) for grp in _token_groups)
    elif match_mode == "regex":
        try:
            flags = 0 if case_sensitive else _re.IGNORECASE
            pattern = _re.compile(query, flags)
        except _re.error as exc:
            return _error_payload_result(
                {"error": f"正则表达式无效: {exc}"},
                code="INVALID_ARGS",
            )
        def _match(cell_str: str) -> bool:
            return bool(pattern.search(cell_str))
    elif match_mode == "exact":
        if case_sensitive:
            def _match(cell_str: str) -> bool:
                return cell_str == query
        else:
            _q_lower = query.lower()
            def _match(cell_str: str) -> bool:
                return cell_str.lower() == _q_lower
    elif match_mode == "startswith":
        if case_sensitive:
            def _match(cell_str: str) -> bool:
                return cell_str.startswith(query)
        else:
            _q_lower = query.lower()
            def _match(cell_str: str) -> bool:
                return cell_str.lower().startswith(_q_lower)
    else:  # contains
        if case_sensitive:
            def _match(cell_str: str) -> bool:
                return query in cell_str
        else:
            _q_lower = query.lower()
            def _match(cell_str: str) -> bool:
                return _q_lower in cell_str.lower()

    from openpyxl.utils import get_column_letter

    matches: list[dict[str, Any]] = []
    sheet_match_counts: dict[str, int] = {}
    total_matches = 0
    sheets_searched = 0

    if snap.is_csv():
        df, effective_header = _read_df(safe_path, None)
        sheet_label = "Sheet1"
        sheets_searched = 1
        header = [str(c) for c in df.columns]
        public_header = _to_public_header_row(effective_header)
        header_excel_row = public_header if isinstance(public_header, int) else 1

        target_col_indices: set[int] | None = None
        if columns:
            col_set_lower = {c.lower() for c in columns}
            target_col_indices = {
                i for i, h in enumerate(header) if h.lower() in col_set_lower
            }

        for col_idx, col_name in enumerate(header):
            if target_col_indices is not None and col_idx not in target_col_indices:
                continue
            if not _match(str(col_name)):
                continue
            total_matches += 1
            sheet_match_counts[sheet_label] = sheet_match_counts.get(sheet_label, 0) + 1
            if len(matches) < max_results:
                matches.append({
                    "sheet": sheet_label,
                    "row": header_excel_row,
                    "column": str(col_name),
                    "value": str(col_name)[:200],
                    "cell_ref": f"{get_column_letter(col_idx + 1)}{header_excel_row}",
                    "context": {},
                })

        for row_pos, (_row_idx_0, row_series) in enumerate(df.iterrows()):
            if len(matches) >= max_results:
                break
            row_list = list(row_series)
            for col_idx, cell_val in enumerate(row_list):
                if pd.isna(cell_val) or cell_val == "":
                    continue
                if target_col_indices is not None and col_idx not in target_col_indices:
                    continue
                cell_str = str(cell_val)
                if not _match(cell_str):
                    continue

                total_matches += 1
                sheet_match_counts[sheet_label] = sheet_match_counts.get(sheet_label, 0) + 1

                if len(matches) < max_results:
                    col_name = header[col_idx] if col_idx < len(header) else f"Col_{col_idx}"
                    excel_row = (
                        row_pos + 1
                        if effective_header == -1
                        else _excel_source_row(effective_header, row_pos)
                    )
                    cell_ref = f"{get_column_letter(col_idx + 1)}{excel_row}"

                    context: dict[str, str] = {}
                    ctx_count = 0
                    for ci, cv in enumerate(row_list):
                        if ci == col_idx or pd.isna(cv):
                            continue
                        h = header[ci] if ci < len(header) else f"Col_{ci}"
                        context[h] = str(cv)[:100]
                        ctx_count += 1
                        if ctx_count >= 5:
                            break

                    matches.append({
                        "sheet": sheet_label,
                        "row": excel_row,
                        "column": col_name,
                        "value": cell_str[:200],
                        "cell_ref": cell_ref,
                        "context": context,
                    })
    else:
        from openpyxl import load_workbook

        wb = load_workbook(safe_path, read_only=True, data_only=True)

        target_sheets = set(s.lower() for s in sheets) if sheets else None

        for ws in wb.worksheets:
            if target_sheets and ws.title.lower() not in target_sheets:
                continue
            sheets_searched += 1

            # 读取 header 行（仅用于列名标注，不再跳过 row 1 的搜索）
            header_xl: list[str] = []
            for row in ws.iter_rows(min_row=1, max_row=1, values_only=True):
                header_xl = [str(c) if c is not None else f"Col_{i}" for i, c in enumerate(row)]
                break

            target_col_indices_xl: set[int] | None = None
            if columns:
                col_set_lower = {c.lower() for c in columns}
                target_col_indices_xl = {
                    i for i, h in enumerate(header_xl) if h.lower() in col_set_lower
                }
                if not target_col_indices_xl:
                    continue

            # 逐行扫描（从第 1 行开始，包含 header 行——表单类文档的数据可能从第 1 行起）
            for row_idx, row in enumerate(
                ws.iter_rows(min_row=1, values_only=True), start=1
            ):
                if len(matches) >= max_results:
                    break
                row_list = list(row)
                for col_idx, cell_val in enumerate(row_list):
                    if cell_val is None:
                        continue
                    if target_col_indices_xl is not None and col_idx not in target_col_indices_xl:
                        continue
                    cell_str = str(cell_val)
                    if not _match(cell_str):
                        continue

                    total_matches += 1
                    sheet_match_counts[ws.title] = sheet_match_counts.get(ws.title, 0) + 1

                    if len(matches) < max_results:
                        col_name = header_xl[col_idx] if col_idx < len(header_xl) else f"Col_{col_idx}"
                        cell_ref = f"{get_column_letter(col_idx + 1)}{row_idx}"

                        # context: 同行其他列的值（最多 5 列）
                        context_xl: dict[str, str] = {}
                        ctx_count = 0
                        for ci, cv in enumerate(row_list):
                            if ci == col_idx or cv is None:
                                continue
                            h = header_xl[ci] if ci < len(header_xl) else f"Col_{ci}"
                            context_xl[h] = str(cv)[:100]
                            ctx_count += 1
                            if ctx_count >= 5:
                                break

                        matches.append({
                            "sheet": ws.title,
                            "row": row_idx,
                            "column": col_name,
                            "value": cell_str[:200],
                            "cell_ref": cell_ref,
                            "context": context_xl,
                        })

            if len(matches) >= max_results:
                # 继续统计剩余 sheet 的总匹配数（但不收集详情）
                continue

        wb.close()

    truncated = total_matches > len(matches)
    summary_by_sheet = [
        {"sheet": s, "matches": c}
        for s, c in sorted(sheet_match_counts.items(), key=lambda x: -x[1])
    ]

    result: dict[str, Any] = {
        "query": query,
        "match_mode": match_mode,
        "total_matches": total_matches,
        "returned": len(matches),
        "truncated": truncated,
        "sheets_searched": sheets_searched,
        "matches": matches,
        "summary_by_sheet": summary_by_sheet,
    }

    # 0 结果时添加智能提示，引导 LLM 优化搜索策略
    if total_matches == 0 and match_mode in ("contains", "exact", "startswith"):
        hints: list[str] = []
        if len(query) > 2:
            hints.append(f"缩短搜索词（如只搜索 '{query[:2]}' 或其中某个关键词）")
        # 检测中文数字，提示可能的阿拉伯数字等价
        _CN_DIGIT_MAP = {"一": "1", "二": "2", "三": "3", "四": "4", "五": "5",
                         "六": "6", "七": "7", "八": "8", "九": "9", "十": "10"}
        _has_cn_digit = any(c in query for c in _CN_DIGIT_MAP)
        if _has_cn_digit:
            _alt = query
            for cn, ar in _CN_DIGIT_MAP.items():
                _alt = _alt.replace(cn, ar)
            hints.append(f"查询含中文数字，可尝试阿拉伯数字版本: '{_alt}'")
        hints.append("尝试 match_mode='fuzzy' 进行分词模糊匹配（自动拆分关键词 + 中文数字转换）")
        hints.append("尝试 match_mode='regex' 用正则灵活匹配")
        result["search_hints"] = hints

    return _finalize_search_excel_values_result(
        result, rel_paths=[rel_path], file_path=safe_path, snapshot=snap,
    )


# ── 跨文件关系发现 ──────────────────────────────────────


# 列名归一化映射：中英文常见同义词（小写 key → 归一化标准形式）
_COLUMN_NAME_SYNONYMS: dict[str, str] = {
    "customerid": "客户id",
    "customer_id": "客户id",
    "客户编号": "客户id",
    "客户号": "客户id",
    "cust_id": "客户id",
    "employeeid": "员工id",
    "employee_id": "员工id",
    "员工编号": "员工id",
    "工号": "员工id",
    "emp_id": "员工id",
    "productid": "产品id",
    "product_id": "产品id",
    "产品编号": "产品id",
    "商品编号": "产品id",
    "prod_id": "产品id",
    "orderid": "订单id",
    "order_id": "订单id",
    "订单编号": "订单id",
    "order_no": "订单id",
    "订单号": "订单id",
    # 注意：不映射 name/姓名/名称、date/日期/时间 等泛化列名，
    # 因为它们语义过于宽泛（产品名称 vs 客户姓名），会导致大量误匹配。
    # 这些列只能通过精确列名匹配来发现关联。
}

# 列名后缀去除列表（归一化时尝试剥离）
# 注意：compact 形式已去除下划线，所以后缀列表中不含下划线前缀
_COLUMN_SUFFIX_STRIP = ("id", "编号", "号", "编码", "代码", "code", "no", "num")


def _normalize_column_name(name: str) -> str:
    """将列名归一化为标准形式，用于跨文件列匹配。

    策略：
    1. strip + 统一小写 + 去除空格/下划线差异
    2. 查同义词表精确映射
    3. 去常见后缀后二次查表
    """
    raw = str(name).strip()
    if not raw:
        return ""
    # 统一小写、去首尾空白
    lower = raw.lower().strip()
    # 去除空格和下划线差异
    compact = lower.replace(" ", "").replace("_", "").replace("-", "")

    # 查同义词表（精确匹配原始小写形式）
    if lower in _COLUMN_NAME_SYNONYMS:
        return _COLUMN_NAME_SYNONYMS[lower]
    if compact in _COLUMN_NAME_SYNONYMS:
        return _COLUMN_NAME_SYNONYMS[compact]

    # 去后缀后二次查表
    for suffix in _COLUMN_SUFFIX_STRIP:
        if compact.endswith(suffix) and len(compact) > len(suffix):
            stripped = compact[: -len(suffix)]
            if stripped in _COLUMN_NAME_SYNONYMS:
                return _COLUMN_NAME_SYNONYMS[stripped]

    return compact


def _detect_cross_file_relationships(
    file_columns: dict[str, dict[str, list[str]]],
    file_dfs: dict[str, dict[str, "pd.DataFrame"]],
    *,
    overlap_threshold: float = 0.5,
    max_sample: int = 500,
) -> list[dict[str, Any]]:
    """检测跨文件列关联：精确列名 + 归一化列名 + 值重叠率 + 方向性 + 类型兼容性。

    每个匹配列对额外输出：
    - unique_ratio_a/b: 各列唯一值占比（用于判断一对多方向）
    - relationship: "one_to_many" | "many_to_one" | "one_to_one" | "many_to_many"
    - suggested_join: 推荐的 pandas merge 策略
    - type_a/type_b: 推断列类型
    - type_compatible: 类型是否兼容

    Args:
        file_columns: {file_path: {sheet_name: [col_names]}}
        file_dfs: {file_path: {sheet_name: DataFrame}}
        overlap_threshold: 值重叠率阈值（默认 0.5）
        max_sample: 值重叠检测的最大采样数

    Returns:
        file_pairs 列表
    """
    from itertools import combinations
    from typing import NamedTuple

    # 展开为 (file, sheet, col_name) 四元组
    class _ColRef(NamedTuple):
        file: str
        sheet: str
        col: str
        normalized: str

    all_cols: list[_ColRef] = []
    for fp, sheets in file_columns.items():
        for sheet_name, cols in sheets.items():
            for col in cols:
                if str(col).startswith("Unnamed"):
                    continue
                norm = _normalize_column_name(col)
                if norm:
                    all_cols.append(_ColRef(file=fp, sheet=sheet_name, col=col, normalized=norm))

    # 类型兼容性矩阵（对称）
    _COMPATIBLE_TYPES: set[frozenset[str]] = {
        frozenset({"numeric", "numeric"}),
        frozenset({"string", "string"}),
        frozenset({"date", "date"}),
        frozenset({"numeric", "string"}),  # 数字可能以字符串形式存储
        frozenset({"string", "mixed"}),
        frozenset({"numeric", "mixed"}),
    }

    def _types_compatible(t1: str, t2: str) -> bool:
        if t1 == t2:
            return True
        return frozenset({t1, t2}) in _COMPATIBLE_TYPES

    # 按文件对分组比较
    file_paths_sorted = sorted(file_columns.keys())
    file_pairs: list[dict[str, Any]] = []

    for fp_a, fp_b in combinations(file_paths_sorted, 2):
        cols_a = [c for c in all_cols if c.file == fp_a]
        cols_b = [c for c in all_cols if c.file == fp_b]
        if not cols_a or not cols_b:
            continue

        shared_columns: list[dict[str, Any]] = []
        seen_pairs: set[tuple[str, str, str, str]] = set()  # 去重

        for ca in cols_a:
            for cb in cols_b:
                pair_key = (ca.sheet, ca.col, cb.sheet, cb.col)
                if pair_key in seen_pairs:
                    continue

                # 判断匹配类型
                match_type: str | None = None
                if ca.col.lower().strip() == cb.col.lower().strip():
                    match_type = "exact"
                elif ca.normalized == cb.normalized:
                    match_type = "normalized"
                else:
                    continue

                seen_pairs.add(pair_key)

                # 值重叠检测 + 方向性分析
                df_a = file_dfs.get(fp_a, {}).get(ca.sheet)
                df_b = file_dfs.get(fp_b, {}).get(cb.sheet)
                overlap_ratio = 0.0
                sample_overlap: list[str] = []
                unique_ratio_a = 0.0
                unique_ratio_b = 0.0
                type_a = "unknown"
                type_b = "unknown"

                if df_a is not None and df_b is not None:
                    if ca.col in df_a.columns and cb.col in df_b.columns:
                        series_a = df_a[ca.col].dropna()
                        series_b = df_b[cb.col].dropna()
                        vals_a = set(series_a.astype(str).tolist()[:max_sample])
                        vals_b = set(series_b.astype(str).tolist()[:max_sample])

                        if vals_a and vals_b:
                            overlap = vals_a & vals_b
                            smaller = min(len(vals_a), len(vals_b))
                            overlap_ratio = round(len(overlap) / smaller, 2) if smaller > 0 else 0.0
                            sample_overlap = sorted(overlap)[:5]

                        # 唯一值占比（方向性信号）
                        len_a = len(series_a)
                        len_b = len(series_b)
                        if len_a > 0:
                            unique_ratio_a = round(series_a.nunique() / len_a, 2)
                        if len_b > 0:
                            unique_ratio_b = round(series_b.nunique() / len_b, 2)

                        # 列类型推断
                        type_a = _infer_column_type(df_a[ca.col])
                        type_b = _infer_column_type(df_b[cb.col])

                if overlap_ratio >= overlap_threshold or match_type == "exact":
                    # 方向性判断
                    relationship = "many_to_many"
                    if unique_ratio_a >= 0.95 and unique_ratio_b >= 0.95:
                        relationship = "one_to_one"
                    elif unique_ratio_a >= 0.95:
                        relationship = "one_to_many"  # A 是主表（唯一键），B 是多端
                    elif unique_ratio_b >= 0.95:
                        relationship = "many_to_one"  # B 是主表，A 是多端

                    # 合并策略建议
                    suggested_join = "inner"  # 默认内连接
                    if overlap_ratio >= 0.8:
                        if relationship in ("one_to_many", "one_to_one"):
                            suggested_join = "left"   # A 为主表左连接
                        elif relationship == "many_to_one":
                            suggested_join = "right"  # B 为主表
                        else:
                            suggested_join = "inner"
                    elif overlap_ratio >= 0.5:
                        suggested_join = "left"  # 中等重叠用 left 保留主表全量
                    else:
                        suggested_join = "outer"  # 低重叠用 outer 避免丢数据

                    entry: dict[str, Any] = {
                        "col_a": ca.col,
                        "sheet_a": ca.sheet,
                        "col_b": cb.col,
                        "sheet_b": cb.sheet,
                        "match_type": match_type,
                        "overlap_ratio": overlap_ratio,
                        "unique_ratio_a": unique_ratio_a,
                        "unique_ratio_b": unique_ratio_b,
                        "relationship": relationship,
                        "suggested_join": suggested_join,
                        "type_a": type_a,
                        "type_b": type_b,
                        "type_compatible": _types_compatible(type_a, type_b),
                    }
                    if sample_overlap:
                        entry["sample_overlap"] = sample_overlap
                    if not _types_compatible(type_a, type_b):
                        entry["type_warning"] = (
                            f"列类型不一致（{type_a} vs {type_b}），"
                            "合并前可能需要类型转换"
                        )
                    shared_columns.append(entry)

        if shared_columns:
            file_pairs.append({
                "file_a": fp_a,
                "file_b": fp_b,
                "shared_columns": shared_columns,
            })

    return file_pairs


def discover_file_relationships(
    file_paths: list[str] | None = None,
    directory: str = ".",
    max_files: int = 5,
    sample_rows: int = 200,
) -> ToolResult:
    """发现多个 Excel 文件之间的列关联关系（共享列名、疑似外键）。

    对指定文件（或目录内所有 Excel 文件）提取各 sheet 的列名和样本值，
    跨文件交叉比对（精确匹配 + 归一化匹配 + 值重叠检测）。

    Args:
        file_paths: 要分析的文件路径列表。为空时扫描 directory。
        directory: 扫描目录（相对于工作目录），默认当前目录。
        max_files: 最多分析的文件数，默认 5。
        sample_rows: 每个 sheet 采样的行数，默认 200。

    Returns:
        ToolResult（value 含关系报告，model_text 为短摘要）。
    """
    from pathlib import Path

    guard = _get_guard()

    # ── 收集文件路径 ──
    paths: list[Path] = []
    if file_paths:
        for fp in file_paths[:max_files]:
            try:
                safe = guard.resolve_and_validate(fp)
                if safe.exists() and safe.suffix.lower() in {".xlsx", ".xlsm", ".xls", ".xlsb", ".csv", ".tsv"}:
                    paths.append(safe)
            except Exception:
                continue
    else:
        safe_dir = guard.resolve_and_validate(directory)
        if safe_dir.is_dir():
            for ext in ("*.xlsx", "*.xlsm", "*.xls", "*.xlsb"):
                for p in safe_dir.rglob(ext):
                    if p.name.startswith((".", "~$")):
                        continue
                    # 跳过噪音目录（与 inspect_excel_files 一致）
                    try:
                        rel_parts = p.relative_to(safe_dir).parts[:-1]
                        if any(part in _SCAN_SKIP_DIRS for part in rel_parts):
                            continue
                    except ValueError:
                        pass
                    paths.append(p)
                    if len(paths) >= max_files:
                        break
                if len(paths) >= max_files:
                    break
            paths.sort(key=lambda p: str(p).lower())
            paths = paths[:max_files]

    if len(paths) < 2:
        early_files = [
            str(p.relative_to(guard.workspace_root)) if p.is_relative_to(guard.workspace_root) else str(p)
            for p in paths
        ]
        return _finalize_discover_file_relationships_result(
            {
                "files_analyzed": len(paths),
                "file_pairs": [],
                "summary": "需要至少 2 个文件才能分析跨文件关系" if len(paths) < 2 else "",
            },
            file_paths=early_files,
        )

    # ── 提取列信息和样本数据（只读快照 backing） ──
    from excelmanus.workspace.identity import display_name_for

    file_columns: dict[str, dict[str, list[str]]] = {}  # rel_path → {sheet → [cols]}
    file_dfs: dict[str, dict[str, pd.DataFrame]] = {}  # rel_path → {sheet → df}
    file_display: dict[str, str] = {}  # rel_path → display_name

    for fp in paths:
        rel_path = str(fp.relative_to(guard.workspace_root)) if fp.is_relative_to(guard.workspace_root) else str(fp)
        file_display[rel_path] = display_name_for(rel_path)

        snap, snap_err = _open_tool_snapshot(rel_path)
        if snap_err is not None or snap is None:
            logger.debug("跨文件关系发现：无法打开快照 %s", rel_path)
            continue
        read_path = snap.backing_path

        try:
            if snap.is_csv():
                df, _ = _read_csv_df(read_path, max_rows=sample_rows)
                cols = [str(c) for c in df.columns if not str(c).startswith("Unnamed")]
                file_columns[rel_path] = {"Sheet1": cols}
                file_dfs[rel_path] = {"Sheet1": df}
                continue

            from openpyxl import load_workbook
            wb = load_workbook(read_path, read_only=True, data_only=True)
            sheets_cols: dict[str, list[str]] = {}
            sheets_dfs: dict[str, pd.DataFrame] = {}
            try:
                for ws in wb.worksheets[:8]:  # 最多 8 个 sheet
                    sheet_name = ws.title
                    try:
                        read_kwargs = _build_read_kwargs(read_path, sheet_name, max_rows=sample_rows)
                        read_kwargs.pop("_form_type_document", None)
                        df = pd.read_excel(**read_kwargs)
                        cols = [str(c) for c in df.columns if not str(c).startswith("Unnamed")]
                        if cols:
                            sheets_cols[sheet_name] = cols
                            sheets_dfs[sheet_name] = df
                    except Exception:
                        continue
            finally:
                wb.close()
            if sheets_cols:
                file_columns[rel_path] = sheets_cols
                file_dfs[rel_path] = sheets_dfs
        except Exception as exc:
            logger.debug("跨文件关系发现：读取 %s 失败: %s", fp, exc)
            continue

    if len(file_columns) < 2:
        return _finalize_discover_file_relationships_result(
            {
                "files_analyzed": len(file_columns),
                "file_pairs": [],
                "summary": "可读取的文件不足 2 个，无法分析跨文件关系",
            },
            file_paths=list(file_columns.keys()),
        )

    # ── 跨文件关系检测 ──
    file_pairs = _detect_cross_file_relationships(file_columns, file_dfs)

    # ── 构建摘要 + 合并提示 ──
    summary_parts: list[str] = []
    merge_hints: list[dict[str, Any]] = []
    type_warnings: list[str] = []
    related_file_set: set[str] = set()  # 用于 suggested_groups

    for pair in file_pairs:
        fa = file_display.get(pair["file_a"], pair["file_a"])
        fb = file_display.get(pair["file_b"], pair["file_b"])
        related_file_set.add(pair["file_a"])
        related_file_set.add(pair["file_b"])
        cols = pair["shared_columns"]

        col_descs: list[str] = []
        for c in cols[:3]:
            if c["col_a"] == c["col_b"]:
                col_descs.append(f"{c['col_a']}(重叠{c['overlap_ratio']:.0%})")
            else:
                col_descs.append(f"{c['col_a']}↔{c['col_b']}(重叠{c['overlap_ratio']:.0%})")
            # 收集类型告警
            if c.get("type_warning"):
                type_warnings.append(f"{fa}.{c['col_a']} vs {fb}.{c['col_b']}: {c['type_warning']}")
        desc = "、".join(col_descs)
        if len(cols) > 3:
            desc += f" 等{len(cols)}对"
        summary_parts.append(f"{fa} ↔ {fb}: {desc}")

        # 合并提示（取第一个最强关联列的建议）
        best_col = max(cols, key=lambda x: x.get("overlap_ratio", 0))
        _JOIN_LABELS = {
            "left": "左连接（保留左表全量）",
            "right": "右连接（保留右表全量）",
            "inner": "内连接（仅保留匹配行）",
            "outer": "全外连接（保留两表全量）",
        }
        _REL_LABELS = {
            "one_to_one": "一对一",
            "one_to_many": f"{fa} 为主表，{fb} 为多端",
            "many_to_one": f"{fb} 为主表，{fa} 为多端",
            "many_to_many": "多对多",
        }
        merge_hints.append({
            "file_a": fa,
            "file_b": fb,
            "key_column_a": best_col["col_a"],
            "key_column_b": best_col["col_b"],
            "relationship": _REL_LABELS.get(best_col.get("relationship", ""), ""),
            "suggested_join": best_col.get("suggested_join", "inner"),
            "suggested_join_label": _JOIN_LABELS.get(best_col.get("suggested_join", "inner"), ""),
            "pandas_hint": (
                f"pd.merge(df_a, df_b, "
                f"left_on='{best_col['col_a']}', right_on='{best_col['col_b']}', "
                f"how='{best_col.get('suggested_join', 'inner')}')"
            ),
        })

    summary = ""
    if summary_parts:
        summary = "跨文件关联发现：" + "；".join(summary_parts)
    else:
        analyzed_names = [file_display.get(k, k) for k in file_columns]
        summary = f"在 {', '.join(analyzed_names)} 之间未发现明显的列关联"

    result: dict[str, Any] = {
        "files_analyzed": len(file_columns),
        "file_pairs": file_pairs,
        "summary": summary,
    }

    # 合并操作提示（供 agent 直接参考的可操作建议）
    if merge_hints:
        result["merge_hints"] = merge_hints

    # 类型兼容性告警
    if type_warnings:
        result["type_warnings"] = type_warnings

    # 方面4联动：文件组建议（当 ≥2 个文件有强关联时）
    if len(related_file_set) >= 2:
        group_files = [
            {"path": fp, "display_name": file_display.get(fp, fp)}
            for fp in sorted(related_file_set)
        ]
        result["suggested_groups"] = [{
            "name": "关联文件组",
            "reason": summary,
            "files": group_files,
        }]

    return _finalize_discover_file_relationships_result(
        result,
        file_paths=sorted(file_columns.keys()),
    )
