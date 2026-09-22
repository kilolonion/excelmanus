# -*- coding: utf-8 -*-
"""Word 文档工具：提供 .docx 读取、写入、检查和搜索能力。"""

from __future__ import annotations

import copy
import json
import os
import re
from datetime import date, datetime
from io import BytesIO
from pathlib import Path
from typing import Any

from excelmanus.engine_core.tool_result import ToolResult, error_result, from_payload
from excelmanus.logger import get_logger
from excelmanus.security import SecurityViolationError
from excelmanus.tools.context import ToolContextMissing, require_guard
from excelmanus.tools._helpers import check_file_exists, commit_error_result, workspace_relpath
from excelmanus.tools.registry import ToolDef
from excelmanus.workbook_commit import (
    CommitError,
    content_version_of_file,
    peek_seen_content_version,
    remember_content_version,
)

logger = get_logger("tools.word")

_WORD_SUFFIXES: frozenset[str] = frozenset({".docx"})

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _error_dict(err: ToolResult | str) -> dict[str, Any]:
    if isinstance(err, ToolResult) and isinstance(err.value, dict):
        return err.value
    if isinstance(err, str) and err.strip().startswith("{"):
        try:
            parsed = json.loads(err)
        except (json.JSONDecodeError, TypeError):
            parsed = None
        if isinstance(parsed, dict):
            return parsed
    return {"error": str(err)}


def _item_error_message(err: ToolResult | str) -> str:
    payload = _error_dict(err)
    return str(payload.get("message") or payload.get("error") or err)


def _resolve_path(file_path: str) -> tuple[Path, ToolResult | None]:
    """解析并校验文件路径，返回 (safe_path, error_result)。"""
    try:
        guard = require_guard()
    except ToolContextMissing:
        return Path(), error_result(
            "文件访问守卫未初始化",
            code="TOOL_CONTEXT_MISSING",
            fields={"file_path": file_path},
        )
    try:
        safe = guard.resolve_and_validate(file_path)
    except SecurityViolationError as exc:
        return Path(), error_result(
            f"路径校验失败: {exc}",
            code="PATH_INVALID",
            fields={"file_path": file_path},
        )
    err = check_file_exists(safe, file_path, guard)
    if err:
        return Path(), err
    return safe, None


def _ensure_docx(file_path: str) -> ToolResult | None:
    """如果文件不是 .docx 返回错误结果，否则返回 None。"""
    if not file_path.lower().endswith(".docx"):
        return error_result(
            "仅支持 .docx 格式文件，当前文件不是 .docx",
            code="INVALID_ARGS",
            fields={"file_path": file_path},
        )
    return None


def _open_docx(safe_path: Path):  # -> docx.Document
    from docx import Document
    return Document(str(safe_path))


def _para_style_name(para) -> str:
    """获取段落样式名，回退到 'Normal'。"""
    try:
        return para.style.name or "Normal"
    except Exception:
        return "Normal"


def _heading_level(para) -> int | None:
    """如果段落是 Heading 样式，返回其级别 (1-9)，否则返回 None。"""
    style = _para_style_name(para)
    if style.startswith("Heading"):
        try:
            return int(style.split()[-1])
        except (ValueError, IndexError):
            pass
    if style == "Title":
        return 0
    return None


def _run_to_dict(run) -> dict[str, Any]:
    """将 Run 对象转为精简字典。"""
    d: dict[str, Any] = {"text": run.text}
    fmt: dict[str, Any] = {}
    if run.bold:
        fmt["bold"] = True
    if run.italic:
        fmt["italic"] = True
    if run.underline:
        fmt["underline"] = True
    if run.font.size:
        fmt["size_pt"] = round(run.font.size.pt, 1)
    if run.font.name:
        fmt["font"] = run.font.name
    if run.font.color and run.font.color.rgb:
        fmt["color"] = str(run.font.color.rgb)
    if fmt:
        d["format"] = fmt
    return d


def _table_to_dict(table) -> dict[str, Any]:
    """将 Table 对象转为字典。"""
    rows_data: list[list[str]] = []
    for row in table.rows:
        rows_data.append([cell.text.strip() for cell in row.cells])
    return {
        "rows": len(table.rows),
        "columns": len(table.columns),
        "data": rows_data,
    }


# ---------------------------------------------------------------------------
# read_word
# ---------------------------------------------------------------------------


def read_word(
    file_path: str,
    *,
    offset: int = 0,
    max_paragraphs: int = 100,
    include_format: bool = False,
    include_tables: bool = True,
) -> ToolResult:
    """读取 Word 文档段落和表格。

    Args:
        file_path: .docx 文件路径
        offset: 起始段落索引（0-based）
        max_paragraphs: 最多返回段落数
        include_format: 是否包含行内格式信息（加粗/斜体/字号等）
        include_tables: 是否包含表格数据
    """
    fmt_err = _ensure_docx(file_path)
    if fmt_err:
        return fmt_err

    safe_path, err = _resolve_path(file_path)
    if err:
        return err

    try:
        doc = _open_docx(safe_path)
    except Exception as exc:
        return error_result(f"无法打开文档: {exc}", code="EXECUTION_FAILED")

    total = len(doc.paragraphs)
    end = min(offset + max_paragraphs, total)
    paragraphs: list[dict[str, Any]] = []

    for i in range(offset, end):
        para = doc.paragraphs[i]
        entry: dict[str, Any] = {
            "index": i,
            "text": para.text,
            "style": _para_style_name(para),
        }
        level = _heading_level(para)
        if level is not None:
            entry["heading_level"] = level

        if include_format and para.runs:
            entry["runs"] = [_run_to_dict(r) for r in para.runs if r.text]

        paragraphs.append(entry)

    result: dict[str, Any] = {
        "file_path": file_path,
        "total_paragraphs": total,
        "offset": offset,
        "returned": len(paragraphs),
        "truncated": end < total,
        "paragraphs": paragraphs,
    }

    if include_tables:
        tables = []
        for idx, tbl in enumerate(doc.tables):
            t = _table_to_dict(tbl)
            t["index"] = idx
            tables.append(t)
        result["tables"] = tables
        result["total_tables"] = len(doc.tables)

    return from_payload(result)


# ---------------------------------------------------------------------------
# write_word
# ---------------------------------------------------------------------------


def _docx_bytes(doc: Any) -> bytes:
    buf = BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _replace_paragraph_text(para, text: str, style: str | None = None) -> None:
    """替换段落可见文本，保留 pPr 与首个 run 的行内格式。

    已有 run 时保留 ``para.runs[0]``（含 rPr），把它的 text 设为新文本，
    并去掉 ``w:p`` 下除 ``w:pPr`` 与该 run 之外的子元素（含其它 run / 超链接）。
    无 run 时 ``add_run(text)``。``style`` 非空才改段落样式。
    """
    from docx.oxml.ns import qn

    p_el = para._element
    if para.runs:
        keep_run = para.runs[0]
        keep_run.text = text
        keep_el = keep_run._element
        parent = keep_el.getparent()
        if parent is not None and parent is not p_el:
            parent.remove(keep_el)
            p_el.append(keep_el)
    else:
        keep_el = para.add_run(text)._element

    for child in list(p_el):
        if child is keep_el or child.tag == qn("w:pPr"):
            continue
        p_el.remove(child)

    if style:
        para.style = para.part.styles[style]


_SOURCE_TABLE_SUFFIXES: frozenset[str] = frozenset({".xlsx", ".xlsm"})
_CAPTION_DISPLAY_LIMIT = 80
_FORMULAS_UNCACHED_RE = re.compile(r"formulas_uncached=(\d+)")


def _cell_value_to_text(value: Any) -> str:
    """把 Excel 单元格值写成 Word 单元格文本。"""
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return str(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _truncate_caption(text: str, limit: int = _CAPTION_DISPLAY_LIMIT) -> str:
    compact = " ".join(str(text or "").split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1] + "…"


def _oxml_paragraph_text(p_el: Any) -> str:
    from docx.oxml.ns import qn

    return "".join((node.text or "") for node in p_el.iter(qn("w:t"))).strip()


def _table_captions(doc: Any) -> list[tuple[int, str]]:
    """每个顶层 w:tbl 之前最近的非空段落文本，顺序与 doc.tables 对齐。"""
    from docx.oxml.ns import qn

    last = ""
    out: list[tuple[int, str]] = []
    idx = 0
    for child in doc.element.body:
        tag = child.tag
        if tag == qn("w:p"):
            text = _oxml_paragraph_text(child)
            if text:
                last = text
        elif tag == qn("w:tbl"):
            out.append((idx, last))
            idx += 1
    return out


def _resolve_replace_table_target(
    doc: Any, op: dict[str, Any], *, op_name: str = "replace_table"
) -> tuple[Any | None, int | None, str | None]:
    raw_index = op.get("table_index")
    raw_caption = op.get("caption")
    caption = str(raw_caption).strip() if raw_caption is not None else ""
    has_index = raw_index is not None and raw_index != ""
    has_caption = bool(caption)
    if has_index == has_caption:
        return None, None, f"{op_name}: table_index 与 caption 必须恰好提供其一"

    if has_index:
        try:
            idx = int(raw_index)
        except (TypeError, ValueError):
            return None, None, f"{op_name}: table_index 无效: {raw_index!r}"
        total = len(doc.tables)
        if idx < 0 or idx >= total:
            return None, None, f"{op_name}: 表格索引 {idx} 超出范围（共 {total} 个表格）"
        return doc.tables[idx], idx, None

    needle = caption.lower()
    captions = _table_captions(doc)
    hits = [(i, cap) for i, cap in captions if needle in cap.lower()]
    if len(hits) == 1:
        idx = hits[0][0]
        return doc.tables[idx], idx, None
    if len(hits) > 1:
        listed = "; ".join(
            f"{i}={_truncate_caption(cap)!r}" for i, cap in hits
        )
        return None, None, f"{op_name}: caption={caption!r} 命中多个表格: {listed}"

    available = "; ".join(
        f"{i}={_truncate_caption(cap) or '（无标题）'!r}" for i, cap in captions
    ) or "（无表格）"
    return None, None, f"{op_name}: caption={caption!r} 未命中；可用标题: {available}"


def _table_has_merged_cells(table: Any) -> bool:
    for tc in table._tbl.iter_tcs():
        if tc.grid_span > 1 or tc.vMerge is not None:
            return True
    return False


def _set_cell_text_keep_format(cell: Any, text: str) -> None:
    """写单元格首段文本并去掉多余段落，保留首选段格式。"""
    paragraphs = list(cell.paragraphs)
    if not paragraphs:
        cell.add_paragraph(text)
        return
    _replace_paragraph_text(paragraphs[0], text)
    for extra in paragraphs[1:]:
        el = extra._element
        parent = el.getparent()
        if parent is not None:
            parent.remove(el)


def _wrap_cell(table: Any, tc: Any) -> Any:
    from docx.table import _Cell

    return _Cell(tc, table)


def _add_table_rows(table: Any, count: int) -> None:
    tbl = table._tbl
    for _ in range(count):
        last = tbl.tr_lst[-1]
        new_tr = copy.deepcopy(last)
        last.addnext(new_tr)
        for tc in new_tr.tc_lst:
            _set_cell_text_keep_format(_wrap_cell(table, tc), "")


def _delete_table_rows(table: Any, count: int) -> None:
    tbl = table._tbl
    for _ in range(count):
        last = tbl.tr_lst[-1]
        tbl.remove(last)


def _add_table_columns(table: Any, count: int) -> None:
    tbl = table._tbl
    grid = tbl.tblGrid
    for _ in range(count):
        last_gc = grid.gridCol_lst[-1]
        grid.append(copy.deepcopy(last_gc))
        for tr in tbl.tr_lst:
            last_tc = tr.tc_lst[-1]
            new_tc = copy.deepcopy(last_tc)
            last_tc.addnext(new_tc)
            _set_cell_text_keep_format(_wrap_cell(table, new_tc), "")


def _delete_table_columns(table: Any, count: int) -> None:
    tbl = table._tbl
    grid = tbl.tblGrid
    for _ in range(count):
        grid.remove(grid.gridCol_lst[-1])
        for tr in tbl.tr_lst:
            tr.remove(tr.tc_lst[-1])


def _resize_table(table: Any, target_rows: int, target_cols: int) -> str | None:
    tbl = table._tbl
    if not tbl.tr_lst or not tbl.tblGrid.gridCol_lst:
        return "replace_table: 目标表缺少行或列，无法调整结构"
    if target_rows < 1 or target_cols < 1:
        return "replace_table: 源数据矩阵为空"
    cur_rows = len(tbl.tr_lst)
    cur_cols = len(tbl.tblGrid.gridCol_lst)
    if target_rows > cur_rows:
        _add_table_rows(table, target_rows - cur_rows)
    elif target_rows < cur_rows:
        _delete_table_rows(table, cur_rows - target_rows)
    if target_cols > cur_cols:
        _add_table_columns(table, target_cols - cur_cols)
    elif target_cols < cur_cols:
        _delete_table_columns(table, cur_cols - target_cols)
    return None


def _looks_like_formula(value: Any) -> bool:
    if isinstance(value, str):
        return value.startswith("=")
    text = getattr(value, "text", None)
    return isinstance(text, str) and text.startswith("=")


def _read_source_matrix(
    source_file: str,
    source_sheet: str | None,
    source_range: str | None,
) -> tuple[list[list[Any]] | None, dict[str, Any], str | None]:
    """读取工作簿矩形为值矩阵。返回 (matrix, meta, error)。"""
    from openpyxl import load_workbook
    from openpyxl.utils import get_column_letter

    from excelmanus.workbook.address import (
        InvalidRefError,
        resolve_range_to_bounds,
        worksheet_used_shape,
    )

    suffix = Path(source_file).suffix.lower()
    if suffix not in _SOURCE_TABLE_SUFFIXES:
        return None, {}, (
            f"replace_table: source_file 仅支持 .xlsx/.xlsm，当前为 '{suffix or '无后缀'}'"
        )

    from excelmanus.workbook.data import _open_tool_snapshot
    from excelmanus.workbook.refs import parse_ref
    from excelmanus.workbook.snapshot import RefUnsupported, SnapshotError, require_default_sheet

    snap, snap_err = _open_tool_snapshot(source_file)
    if snap_err is not None:
        return None, {}, f"replace_table: {snap_err.model_text or snap_err.error}"
    safe_path = snap.backing_path

    sheet_name = (source_sheet or "").strip() or None
    range_text = (source_range or "").strip() or None
    formulas_uncached = 0
    matrix: list[list[Any]] = []
    resolved_sheet = ""
    resolved_range = ""
    min_row = min_col = max_row = max_col = 1

    try:
        wb = load_workbook(safe_path, read_only=False, data_only=True)
    except Exception as exc:
        return None, {}, f"replace_table: 无法打开工作簿: {exc}"
    try:
        try:
            resolved_sheet = require_default_sheet(list(wb.sheetnames), sheet_name)
        except SnapshotError as exc:
            return None, {}, f"replace_table: {exc}"
        ws = wb[resolved_sheet]
        used_rows, used_cols = worksheet_used_shape(ws)
        address = range_text or f"A1:{get_column_letter(used_cols)}{used_rows}"
        try:
            area = parse_ref(address, default_sheet=resolved_sheet)
            if len(area.areas) != 1:
                return None, {}, (
                    "replace_table: source_range 不支持并集。请改用单一矩形，例如 A1:B2。"
                )
            from excelmanus.workbook.data import _bind_area_in_workbook, WorkbookRefBindError

            rects = _bind_area_in_workbook(wb, area, default_sheet=resolved_sheet)
            if len(rects) != 1:
                return None, {}, "replace_table: 命名/表引用解析为多区域，请改用单一矩形。"
            rect = rects[0]
            bounds = resolve_range_to_bounds(
                rect.to_a1(include_sheet=False),
                used_max_row=used_rows,
                used_max_col=used_cols,
            )
        except (InvalidRefError, WorkbookRefBindError, SnapshotError, RefUnsupported) as exc:
            return None, {}, f"replace_table: 无法解析 source_range: {exc}"
        resolved_range = bounds.resolved
        min_row, min_col = bounds.min_row, bounds.min_col
        max_row, max_col = bounds.max_row, bounds.max_col
        width = max_col - min_col + 1
        for row in ws.iter_rows(
            min_row=min_row,
            max_row=max_row,
            min_col=min_col,
            max_col=max_col,
            values_only=True,
        ):
            cells = list(row)
            if len(cells) < width:
                cells.extend([None] * (width - len(cells)))
            matrix.append(cells[:width])
    finally:
        wb.close()

    if not matrix or not matrix[0]:
        return None, {}, "replace_table: 源数据矩阵为空"

    if any(cell is None for row in matrix for cell in row):
        try:
            wb_f = load_workbook(safe_path, read_only=True, data_only=False)
        except Exception:
            wb_f = None
        if wb_f is not None:
            try:
                ws_f = (
                    wb_f[resolved_sheet]
                    if resolved_sheet in wb_f.sheetnames
                    else wb_f.active
                )
                if ws_f is not None:
                    for r_i, row in enumerate(
                        ws_f.iter_rows(
                            min_row=min_row,
                            max_row=max_row,
                            min_col=min_col,
                            max_col=max_col,
                            values_only=True,
                        )
                    ):
                        for c_i, raw in enumerate(row):
                            if r_i >= len(matrix) or c_i >= len(matrix[r_i]):
                                continue
                            if matrix[r_i][c_i] is None and _looks_like_formula(raw):
                                formulas_uncached += 1
            finally:
                wb_f.close()

    meta = {
        "resolved_sheet": resolved_sheet,
        "resolved_range": resolved_range,
        "formulas_uncached": formulas_uncached,
        "content_version": snap.content_version,
        "snapshot_id": snap.id.key(),
        "source_file": snap.file.relative,
    }
    return matrix, meta, None


def _fill_table_from_matrix(table: Any, matrix: list[list[Any]]) -> None:
    tbl = table._tbl
    for r_i, row_vals in enumerate(matrix):
        tr = tbl.tr_lst[r_i]
        for c_i, value in enumerate(row_vals):
            _set_cell_text_keep_format(
                _wrap_cell(table, tr.tc_lst[c_i]),
                _cell_value_to_text(value),
            )


def _fill_merged_table_from_matrix(table: Any, matrix: list[list[Any]]) -> None:
    """Fill only merge anchors while preserving w:gridSpan/w:vMerge XML."""
    tbl = table._tbl
    for r_i, tr in enumerate(tbl.tr_lst):
        col = 0
        for tc in tr.tc_lst:
            grid_span = int(getattr(tc, "grid_span", 1) or 1)
            vmerge = getattr(tc, "vMerge", None)
            vmerge_val = getattr(vmerge, "val", None) if vmerge is not None else None
            if vmerge_val not in {"continue", "cont"} and r_i < len(matrix) and col < len(matrix[r_i]):
                _set_cell_text_keep_format(
                    _wrap_cell(table, tc), _cell_value_to_text(matrix[r_i][col])
                )
            col += grid_span


def _note_formulas_uncached(
    warnings: list[str] | None,
    *,
    table_idx: int,
    count: int,
    sheet: str,
    rng: str,
) -> None:
    if warnings is None or count <= 0:
        return
    warnings.append(
        f"replace_table table={table_idx} from={sheet}!{rng}: "
        f"formulas_uncached={count}，工作簿未缓存公式计算结果，对应单元格已写入空值"
    )


def _sum_formulas_uncached(warnings: list[str]) -> int:
    total = 0
    for item in warnings:
        match = _FORMULAS_UNCACHED_RE.search(item)
        if match:
            total += int(match.group(1))
    return total


def _apply_replace_table(
    doc: Any,
    op: dict[str, Any],
    warnings: list[str] | None,
) -> tuple[str | None, str | None]:
    """应用 replace_table。成功返回 (applied, None)，失败返回 (None, error)。"""
    table, table_idx, locate_err = _resolve_replace_table_target(doc, op)
    if locate_err or table is None or table_idx is None:
        return None, locate_err or "replace_table: 未找到目标表格"

    merged_present = _table_has_merged_cells(table)
    merged = merged_present and bool(op.get("allow_merged", False))
    if merged_present and not merged:
        return None, (
            f"表格 {table_idx} 含合并单元格；如源数据尺寸与模板一致，传 allow_merged=true 保留合并结构，"
            "否则请改用 run_code"
        )

    source_file = str(op.get("source_file") or "").strip()
    if not source_file:
        return None, "replace_table: 缺少 source_file"

    source_sheet = str(op.get("source_sheet") or "").strip() or None
    source_range = str(op.get("source_range") or "").strip() or None
    matrix, meta, read_err = _read_source_matrix(source_file, source_sheet, source_range)
    if read_err or matrix is None:
        return None, read_err or "replace_table: 读取源工作簿失败"

    target_rows = len(matrix)
    target_cols = len(matrix[0])
    if merged:
        current_rows = len(table._tbl.tr_lst)
        current_cols = len(table._tbl.tblGrid.gridCol_lst)
        if (target_rows, target_cols) != (current_rows, current_cols):
            return None, (
                f"replace_table: 含合并单元格的表格只能替换同样的 {current_rows}x{current_cols} 结构，"
                f"源数据为 {target_rows}x{target_cols}；请先调整模板合并结构"
            )
        _fill_merged_table_from_matrix(table, matrix)
        if warnings is not None:
            warnings.append(f"replace_table table={table_idx}: 保留合并单元格结构，仅更新合并锚点")
    else:
        resize_err = _resize_table(table, target_rows, target_cols)
        if resize_err:
            return None, resize_err
        _fill_table_from_matrix(table, matrix)
    sheet = str(meta.get("resolved_sheet") or "")
    rng = str(meta.get("resolved_range") or "")
    uncached = int(meta.get("formulas_uncached") or 0)
    _note_formulas_uncached(
        warnings,
        table_idx=table_idx,
        count=uncached,
        sheet=sheet,
        rng=rng,
    )
    return (
        f"replace_table table={table_idx} {target_rows}x{target_cols} from={sheet}!{rng}",
        None,
    )


_PLACEHOLDER_RE = re.compile(r"\{\{([^{}]+)\}\}")


def _unique_extend(dst: list[str], items: list[str]) -> None:
    seen = set(dst)
    for item in items:
        if item not in seen:
            seen.add(item)
            dst.append(item)


def _iter_body_paragraphs(doc: Any, *, include_headers_footers: bool = False):
    """正文/表格段落，并可包含所有 section 的页眉页脚。"""
    from docx.oxml.ns import qn
    from docx.text.paragraph import Paragraph

    seen: set[int] = set()
    body = doc.element.body
    for p_el in body.iter(qn("w:p")):
        seen.add(id(p_el))
        yield Paragraph(p_el, doc)
    if include_headers_footers:
        for section in doc.sections:
            containers = (
                section.header, section.first_page_header, section.even_page_header,
                section.footer, section.first_page_footer, section.even_page_footer,
            )
            for container in containers:
                for para in getattr(container, "paragraphs", []) or []:
                    p_el = para._element
                    if id(p_el) not in seen:
                        seen.add(id(p_el))
                        yield para
                for table in getattr(container, "tables", []) or []:
                    for para in table._element.iter(qn("w:p")):
                        if id(para) not in seen:
                            seen.add(id(para))
                            yield Paragraph(para, table)


def _paragraph_runs(para: Any) -> list[Any]:
    from docx.oxml.ns import qn
    from docx.text.run import Run

    return [Run(r_el, para) for r_el in para._element.iter(qn("w:r"))]


def _replace_span_across_runs(runs: list[Any], start: int, end: int, replacement: str) -> None:
    """把拼接文本 [start, end) 换成 replacement：前缀留首 run、后缀留末 run、中间清空。"""
    if start < 0 or end < start or not runs:
        return
    pos = 0
    start_i: int | None = None
    start_off = 0
    end_i: int | None = None
    end_off = 0
    last = end - 1
    for i, run in enumerate(runs):
        text = run.text or ""
        run_start = pos
        run_end = pos + len(text)
        if start_i is None and start < run_end and start >= run_start:
            start_i = i
            start_off = start - run_start
        if last >= run_start and last < run_end:
            end_i = i
            end_off = last - run_start
        pos = run_end

    if start_i is None or end_i is None:
        return

    start_run = runs[start_i]
    end_run = runs[end_i]
    prefix = (start_run.text or "")[:start_off]
    suffix = (end_run.text or "")[end_off + 1 :]
    if start_i == end_i:
        start_run.text = prefix + replacement + suffix
        return
    start_run.text = prefix + replacement
    end_run.text = suffix
    for i in range(start_i + 1, end_i):
        if runs[i].text:
            runs[i].text = ""


def _fill_placeholders_in_paragraph(
    para: Any,
    values: dict[str, str],
) -> tuple[int, list[str], list[str]]:
    """返回 (填充次数, 已填 key 顺序, 未给值但出现过的 key)。"""
    runs = _paragraph_runs(para)
    full = "".join(run.text or "" for run in runs)
    matches = list(_PLACEHOLDER_RE.finditer(full))
    if not matches:
        return 0, [], []

    filled_keys: list[str] = []
    unfilled_keys: list[str] = []
    filled = 0
    for match in matches:
        key = match.group(1).strip()
        if not key:
            continue
        if key in values:
            _unique_extend(filled_keys, [key])
        else:
            _unique_extend(unfilled_keys, [key])

    for match in reversed(matches):
        key = match.group(1).strip()
        if not key or key not in values:
            continue
        _replace_span_across_runs(runs, match.start(), match.end(), values[key])
        filled += 1
    return filled, filled_keys, unfilled_keys


def _set_run_element_text(r_el: Any, text: str) -> None:
    from docx.text.run import Run

    Run(r_el, None).text = text


def _bookmark_range_runs(body: Any, start_el: Any) -> tuple[list[Any], bool]:
    """bookmarkStart 到对应 bookmarkEnd 之间的 w:r。返回 (runs, found_end)。"""
    from docx.oxml.ns import qn

    bookmark_end = qn("w:bookmarkEnd")
    run_tag = qn("w:r")
    bid = start_el.get(qn("w:id"))
    collecting = False
    found_end = False
    runs: list[Any] = []
    for el in body.iter():
        if el is start_el:
            collecting = True
            continue
        if not collecting:
            continue
        if el.tag == bookmark_end and el.get(qn("w:id")) == bid:
            found_end = True
            break
        if el.tag == run_tag:
            runs.append(el)
    return runs, found_end


def _fill_bookmarks(
    doc: Any,
    values: dict[str, str],
    warnings: list[str] | None,
) -> tuple[int, list[str], list[str]]:
    """填充正文（含表格）书签。返回 (填充次数, 已填名, 未给值但出现过的名)。"""
    from docx.oxml.ns import qn

    body = doc.element.body
    filled = 0
    filled_names: list[str] = []
    unfilled_names: list[str] = []
    for start_el in list(body.iter(qn("w:bookmarkStart"))):
        name = (start_el.get(qn("w:name")) or "").strip()
        if not name or name.startswith("_"):
            continue
        if name not in values:
            _unique_extend(unfilled_names, [name])
            continue
        runs, found_end = _bookmark_range_runs(body, start_el)
        if not found_end:
            if warnings is not None:
                warnings.append(
                    f"fill_template: 书签 {name!r} 未找到 bookmarkEnd，已跳过"
                )
            continue
        if not runs:
            if warnings is not None:
                warnings.append(
                    f"fill_template: 书签 {name!r} 范围内无 run，已跳过"
                )
            continue
        _set_run_element_text(runs[0], values[name])
        for extra in runs[1:]:
            _set_run_element_text(extra, "")
        filled += 1
        _unique_extend(filled_names, [name])
    return filled, filled_names, unfilled_names


def _normalize_template_values(raw: dict[Any, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in raw.items():
        name = str(key).strip()
        if not name:
            continue
        out[name] = _cell_value_to_text(value)
    return out


def _coerce_excel_row(
    value: Any,
    field: str,
    *,
    default: int | None = None,
) -> tuple[int | None, str | None]:
    if value is None or value == "":
        if default is not None:
            return default, None
        return None, f"fill_template: 缺少 {field}"
    if isinstance(value, bool):
        return None, f"fill_template: {field} 无效: {value!r}"
    try:
        if isinstance(value, float) and not value.is_integer():
            return None, f"fill_template: {field} 无效: {value!r}"
        row = int(value)
    except (TypeError, ValueError):
        return None, f"fill_template: {field} 无效: {value!r}"
    if row < 1:
        return None, f"fill_template: {field} 必须 ≥1（Excel 1-based）"
    return row, None


def _relabel_source_error(err: str | None, op_name: str) -> str | None:
    if not err:
        return err
    prefix = "replace_table:"
    if err.startswith(prefix):
        return f"{op_name}:{err[len(prefix):]}"
    return err


def _values_from_source_row(op: dict[str, Any]) -> tuple[dict[str, str] | None, str | None]:
    source_file = str(op.get("source_file") or "").strip()
    if not source_file:
        return None, "fill_template: 缺少 source_file"
    source_row, row_err = _coerce_excel_row(op.get("source_row"), "source_row")
    if row_err or source_row is None:
        return None, row_err or "fill_template: 缺少 source_row"
    header_row, header_err = _coerce_excel_row(
        op.get("header_row"), "header_row", default=1
    )
    if header_err or header_row is None:
        return None, header_err or "fill_template: header_row 无效"
    if source_row == header_row:
        return None, (
            f"fill_template: source_row 必须 ≠ header_row（当前均为 {source_row}）"
        )

    source_sheet = str(op.get("source_sheet") or "").strip() or None
    matrix, meta, read_err = _read_source_matrix(source_file, source_sheet, None)
    read_err = _relabel_source_error(read_err, "fill_template")
    if read_err or matrix is None:
        return None, read_err or "fill_template: 读取源工作簿失败", {}

    n_rows = len(matrix)
    if header_row > n_rows:
        return None, (
            f"fill_template: header_row={header_row} 超出工作表范围（共 {n_rows} 行）"
        )
    if source_row > n_rows:
        return None, (
            f"fill_template: source_row={source_row} 超出工作表范围（共 {n_rows} 行）"
        )

    header_vals = matrix[header_row - 1]
    data_vals = matrix[source_row - 1]
    keys: list[str] = []
    seen: dict[str, int] = {}
    for col, raw in enumerate(header_vals):
        key = _cell_value_to_text(raw).strip()
        if not key:
            keys.append("")
            continue
        if key in seen:
            return None, f"fill_template: 表头重复: {key!r}"
        seen[key] = col
        keys.append(key)

    values: dict[str, str] = {}
    width = max(len(keys), len(data_vals))
    for col in range(width):
        key = keys[col] if col < len(keys) else ""
        if not key:
            continue
        raw = data_vals[col] if col < len(data_vals) else None
        values[key] = _cell_value_to_text(raw)
    return values, None


def _load_fill_values(op: dict[str, Any]) -> tuple[dict[str, str] | None, str | None]:
    raw_values = op.get("values")
    source_file = str(op.get("source_file") or "").strip()
    raw_row = op.get("source_row")
    has_values = raw_values is not None
    has_row_hint = bool(source_file) or not (raw_row is None or raw_row == "")
    if has_values and has_row_hint:
        return None, "fill_template: values 与 source_file+source_row 必须恰好提供其一"
    if has_values:
        if not isinstance(raw_values, dict):
            return None, "fill_template: values 必须是对象"
        return _normalize_template_values(raw_values), None
    if source_file and not (raw_row is None or raw_row == ""):
        return _values_from_source_row(op)
    return None, "fill_template: 必须提供 values 或 source_file+source_row 恰好其一"


def _merge_fill_extras(
    extras: dict[str, Any] | None,
    *,
    filled_keys: list[str],
    unfilled_keys: list[str],
    bookmarks_filled: int,
) -> None:
    if extras is None:
        return
    extras["fill_template"] = True
    extras.setdefault("filled_keys", [])
    extras.setdefault("unfilled_keys", [])
    _unique_extend(extras["filled_keys"], filled_keys)
    _unique_extend(extras["unfilled_keys"], unfilled_keys)
    extras["bookmarks_filled"] = int(extras.get("bookmarks_filled") or 0) + bookmarks_filled


def _apply_fill_template(
    doc: Any,
    op: dict[str, Any],
    warnings: list[str] | None,
    extras: dict[str, Any] | None,
) -> tuple[str | None, str | None]:
    """应用 fill_template。成功返回 (applied, None)，失败返回 (None, error)。"""
    values, load_err = _load_fill_values(op)
    if load_err or values is None:
        return None, load_err or "fill_template: 无法解析填充值"

    filled_count = 0
    filled_keys: list[str] = []
    unfilled_keys: list[str] = []

    include_headers = bool(op.get("include_headers_footers", op.get("headers_footers", True)))
    for para in _iter_body_paragraphs(doc, include_headers_footers=include_headers):
        n, keys, missing = _fill_placeholders_in_paragraph(para, values)
        filled_count += n
        _unique_extend(filled_keys, keys)
        _unique_extend(unfilled_keys, missing)

    bookmarks_filled, bm_keys, bm_missing = _fill_bookmarks(doc, values, warnings)
    filled_count += bookmarks_filled
    _unique_extend(filled_keys, bm_keys)
    _unique_extend(unfilled_keys, bm_missing)
    unfilled_keys = [key for key in unfilled_keys if key not in filled_keys]

    _merge_fill_extras(
        extras,
        filled_keys=filled_keys,
        unfilled_keys=unfilled_keys,
        bookmarks_filled=bookmarks_filled,
    )
    source_file = str(op.get("source_file") or "").strip()
    if extras is not None and source_file:
        extras["source_file"] = source_file
        try:
            dest = require_guard().resolve_and_validate(source_file)
            extras["source_version"] = content_version_of_file(dest)
        except Exception:
            extras["source_version"] = peek_seen_content_version(source_file)
    return (
        f"fill_template filled={filled_count} keys={filled_keys} headers_footers={include_headers}",
        None,
    )


_EXTRACT_NUMERIC_RE = re.compile(r"^-?\d+(\.\d+)?$")
_EXTRACT_TARGET_SUFFIX = ".xlsx"


def _parse_extracted_cell(text: str) -> Any:
    """Word 单元格文本 → 工作簿值：空→None，纯数字→int/float（前导零除外）。"""
    stripped = (text or "").strip()
    if not stripped:
        return None
    if not _EXTRACT_NUMERIC_RE.match(stripped):
        return stripped
    unsigned = stripped[1:] if stripped.startswith("-") else stripped
    int_part = unsigned.split(".", 1)[0]
    if len(int_part) > 1 and int_part.startswith("0"):
        return stripped
    if "." in stripped:
        return float(stripped)
    return int(stripped)


def _parse_extract_start_cell(
    raw: Any,
) -> tuple[int | None, int | None, str | None, str | None]:
    """返回 (row, col, A1, error)。缺省 A1。"""
    from openpyxl.utils import get_column_letter
    from openpyxl.utils.cell import column_index_from_string, coordinate_from_string

    text = "A1" if raw is None or str(raw).strip() == "" else str(raw).strip()
    try:
        token = text.replace("$", "").upper()
        if "!" in token or ":" in token:
            raise ValueError("not a single cell")
        col_letter, row = coordinate_from_string(token)
        col = int(column_index_from_string(col_letter))
        row = int(row)
    except Exception:
        return None, None, None, f"extract_table: start_cell 无效: {raw!r}"
    if row < 1 or col < 1:
        return None, None, None, f"extract_table: start_cell 无效: {raw!r}"
    return row, col, f"{get_column_letter(col)}{row}", None


def _resolve_extract_target_file(
    target_file: str,
) -> tuple[Path | None, str | None, str | None]:
    raw = str(target_file or "").strip()
    if not raw:
        return None, None, "extract_table: 缺少 target_file"
    suffix = Path(raw).suffix.lower()
    if suffix != _EXTRACT_TARGET_SUFFIX:
        return None, None, (
            f"extract_table: target_file 仅支持 .xlsx，当前为 '{suffix or '无后缀'}'"
        )
    try:
        guard = require_guard()
    except ToolContextMissing:
        return None, None, "extract_table: 文件访问守卫未初始化"
    try:
        dest = guard.resolve_and_validate(raw)
    except SecurityViolationError as exc:
        return None, None, f"extract_table: 路径校验失败: {exc}"
    dest_rel = workspace_relpath(guard, dest).replace("\\", "/")
    return dest, dest_rel, None


def _extract_table_matrix(table: Any) -> list[list[Any]]:
    return [
        [_parse_extracted_cell(cell.text) for cell in row.cells]
        for row in table.rows
    ]


def _peek_extract_sheet(dest: Path, target_sheet: str | None) -> str:
    wanted = (target_sheet or "").strip() or None
    if not dest.is_file():
        return wanted or "Sheet1"
    from openpyxl import load_workbook

    try:
        wb = load_workbook(dest, read_only=True)
    except Exception:
        return wanted or "Sheet1"
    try:
        names = list(wb.sheetnames)
        active = wb.active.title if wb.active is not None else (names[0] if names else "Sheet1")
    finally:
        wb.close()
    if not wanted:
        return active
    matched = next((name for name in names if name == wanted), None)
    if matched is None:
        matched = next((name for name in names if name.lower() == wanted.lower()), None)
    return matched if matched is not None else wanted


def _write_matrix_at(
    ws: Any,
    matrix: list[list[Any]],
    start_row: int,
    start_col: int,
) -> None:
    for r_i, row_vals in enumerate(matrix):
        for c_i, value in enumerate(row_vals):
            if value is None:
                continue
            ws.cell(row=start_row + r_i, column=start_col + c_i, value=value)


def _workbook_bytes(wb: Any) -> bytes:
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _apply_extract_table(
    doc: Any,
    op: dict[str, Any],
    warnings: list[str] | None,
    extras: dict[str, Any] | None,
) -> tuple[str | None, str | None]:
    """抽取表格到 extras['extracts']，不落盘。成功返回 (applied, None)。"""
    table, table_idx, locate_err = _resolve_replace_table_target(
        doc, op, op_name="extract_table"
    )
    if locate_err or table is None or table_idx is None:
        return None, locate_err or "extract_table: 未找到目标表格"

    dest, dest_rel, dest_err = _resolve_extract_target_file(str(op.get("target_file") or ""))
    if dest_err or dest is None or dest_rel is None:
        return None, dest_err or "extract_table: 缺少 target_file"

    start_row, start_col, start_a1, start_err = _parse_extract_start_cell(
        op.get("start_cell")
    )
    if start_err or start_row is None or start_col is None or start_a1 is None:
        return None, start_err or "extract_table: start_cell 无效"

    raw_sheet = op.get("target_sheet")
    target_sheet = (
        str(raw_sheet).strip()
        if raw_sheet is not None and str(raw_sheet).strip()
        else None
    )
    resolved_sheet = _peek_extract_sheet(dest, target_sheet)
    matrix = _extract_table_matrix(table)
    n_rows = len(matrix)
    n_cols = len(matrix[0]) if matrix else 0

    if _table_has_merged_cells(table) and warnings is not None:
        warnings.append(
            f"extract_table table={table_idx}: 表格含合并单元格，"
            "python-docx 枚举会把合并格值重复写入相邻单元格"
        )

    if extras is not None:
        extras.setdefault("extracts", []).append({
            "dest": dest,
            "target_rel": dest_rel,
            "target_sheet": target_sheet,
            "start_row": start_row,
            "start_col": start_col,
            "start_cell": start_a1,
            "matrix": matrix,
            "table_idx": table_idx,
            "target_expected_version": (
                str(op.get("target_expected_version") or "").strip() or None
            ),
        })

    return (
        f"extract_table table={table_idx} → {dest_rel}!{resolved_sheet}!{start_a1} "
        f"{n_rows}x{n_cols}",
        None,
    )


def _prepare_pending_extracts(
    extracts: list[dict[str, Any]],
) -> tuple[list[Any], dict[str, Any] | None, ToolResult | None]:
    """Build extract workbooks in memory. Returns (TargetSpecs, last meta, error)."""
    if not extracts:
        return [], None, None
    from openpyxl import Workbook, load_workbook

    from excelmanus.workspace.file_service import TargetSpec

    specs: list[Any] = []
    last: dict[str, Any] | None = None
    for item in extracts:
        dest: Path = item["dest"]
        dest_rel: str = item["target_rel"]
        target_sheet: str | None = item["target_sheet"]
        matrix: list[list[Any]] = item["matrix"]
        start_row = int(item["start_row"])
        start_col = int(item["start_col"])
        created_sheet = False
        wb: Any = None
        try:
            if dest.is_file():
                seen = (
                    str(item.get("target_expected_version") or "").strip()
                    or peek_seen_content_version(dest_rel)
                )
                if not seen:
                    return [], None, error_result(
                        "extract_table 覆盖已有工作簿必须提供 target_expected_version",
                        code="VERSION_CONFLICT",
                        fields={"file_path": dest_rel},
                    )
                expected: str | None = seen
                wb = load_workbook(dest)
                names = list(wb.sheetnames)
                if target_sheet:
                    matched = next((name for name in names if name == target_sheet), None)
                    if matched is None:
                        matched = next(
                            (name for name in names if name.lower() == target_sheet.lower()),
                            None,
                        )
                    if matched is None:
                        ws = wb.create_sheet(title=target_sheet)
                        created_sheet = True
                    else:
                        ws = wb[matched]
                else:
                    ws = wb.active
            else:
                expected = None
                wb = Workbook()
                ws = wb.active
                title = target_sheet or "Sheet1"
                if ws is not None and ws.title != title:
                    ws.title = title
            if ws is None:
                return [], None, error_result(
                    "extract_table: 无法打开工作表",
                    code="EXECUTION_FAILED",
                    fields={"file_path": dest_rel},
                )
            _write_matrix_at(ws, matrix, start_row, start_col)
            resolved_sheet = ws.title
            data = _workbook_bytes(wb)
        except Exception as exc:
            return [], None, error_result(
                f"extract_table: 写入工作簿失败: {exc}",
                code="EXECUTION_FAILED",
                fields={"file_path": dest_rel},
            )
        finally:
            if wb is not None:
                try:
                    wb.close()
                except Exception:
                    pass
        if dest.is_file():
            specs.append(TargetSpec(op="update", path=dest_rel, data=data, expected_version=expected))
        else:
            specs.append(TargetSpec(op="create", path=dest_rel, data=data))
        last = {
            "target_file": dest_rel,
            "target_sheet": resolved_sheet,
            "created_sheet": created_sheet,
            "target_content_version": None,
        }
    return specs, last, None


def _operations_are_extract_only(operations: list[dict[str, Any]]) -> bool:
    if not operations:
        return False
    return all(
        isinstance(op, dict) and op.get("action") == "extract_table"
        for op in operations
    )


def _has_extract_table(operations: list[dict[str, Any]]) -> bool:
    return any(
        isinstance(op, dict) and op.get("action") == "extract_table"
        for op in operations
    )


def apply_word_operations(
    doc: Any,
    operations: list[dict[str, Any]],
    warnings: list[str] | None = None,
    extras: dict[str, Any] | None = None,
) -> tuple[list[str], list[str]]:
    """对已打开的 Document 应用操作。返回 (applied, errors)，不落盘。

    ``warnings`` 为可选第三参；调用方传入 list 时，replace_table 等非致命提示会写入其中。
    ``extras`` 为可选第四参；fill_template 的 filled_keys、extract_table 的待写工作簿写入其中。
    两参调用保持兼容。
    """
    from docx.oxml.ns import qn

    applied: list[str] = []
    errors: list[str] = []

    for op in operations:
        action = op.get("action", "")
        idx = op.get("index")
        text = op.get("text", "")
        style = op.get("style")

        try:
            if action == "replace":
                if idx is None or idx < 0 or idx >= len(doc.paragraphs):
                    errors.append(f"replace: 段落索引 {idx} 超出范围 (0-{len(doc.paragraphs)-1})")
                    continue
                _replace_paragraph_text(doc.paragraphs[idx], text, style)
                applied.append(f"replace paragraph {idx}")

            elif action == "insert_after":
                if idx is None or idx < 0 or idx >= len(doc.paragraphs):
                    errors.append(f"insert_after: 段落索引 {idx} 超出范围")
                    continue
                ref_para = doc.paragraphs[idx]
                new_p = ref_para._element.makeelement(qn("w:p"), {})
                ref_para._element.addnext(new_p)
                from docx.text.paragraph import Paragraph
                new_para = Paragraph(new_p, ref_para._parent)
                new_para.add_run(text)
                if style:
                    new_para.style = doc.styles[style]
                applied.append(f"insert_after paragraph {idx}")

            elif action == "append":
                doc.add_paragraph(text, style=style)
                applied.append("append paragraph")

            elif action == "delete":
                if idx is None or idx < 0 or idx >= len(doc.paragraphs):
                    errors.append(f"delete: 段落索引 {idx} 超出范围")
                    continue
                p_element = doc.paragraphs[idx]._element
                p_element.getparent().remove(p_element)
                applied.append(f"delete paragraph {idx}")

            elif action == "replace_table":
                applied_msg, replace_err = _apply_replace_table(doc, op, warnings)
                if replace_err:
                    errors.append(replace_err)
                    continue
                if applied_msg:
                    applied.append(applied_msg)

            elif action == "fill_template":
                applied_msg, fill_err = _apply_fill_template(doc, op, warnings, extras)
                if fill_err:
                    errors.append(fill_err)
                    continue
                if applied_msg:
                    applied.append(applied_msg)

            elif action == "extract_table":
                applied_msg, extract_err = _apply_extract_table(doc, op, warnings, extras)
                if extract_err:
                    errors.append(extract_err)
                    continue
                if applied_msg:
                    applied.append(applied_msg)

            else:
                errors.append(f"未知操作: {action}")
        except Exception as exc:
            errors.append(f"{action} index={idx}: {exc}")

    return applied, errors


def write_word(
    file_path: str,
    *,
    operations: list[dict[str, Any]],
    expected_version: str | None = None,
    output_file: str | None = None,
    output_expected_version: str | None = None,
) -> ToolResult:
    """对 Word 文档执行写入操作。任一操作失败则不落盘。

    ``output_file`` 非空时为渲染模式：源模板只读，结果写入该路径。
    operations 全为 extract_table 时不改源文档，只写目标工作簿。
    extract_table 与 output_file 互斥。
    """
    fmt_err = _ensure_docx(file_path)
    if fmt_err:
        return fmt_err

    if _has_extract_table(operations) and (output_file or "").strip():
        return error_result(
            "extract_table 与 output_file 互斥",
            code="INVALID_ARGS",
            fields={"file_path": file_path},
        )

    safe_path, err = _resolve_path(file_path)
    if err:
        return err

    try:
        guard = require_guard()
    except ToolContextMissing:
        return error_result(
            "文件访问守卫未初始化",
            code="TOOL_CONTEXT_MISSING",
            fields={"file_path": file_path},
        )

    try:
        doc = _open_docx(safe_path)
    except Exception as exc:
        return error_result(f"无法打开文档: {exc}", code="EXECUTION_FAILED")

    warnings: list[str] = []
    extras: dict[str, Any] = {}
    applied, errors = apply_word_operations(doc, operations, warnings, extras)
    if errors:
        return error_result(
            "部分 Word 操作失败",
            code="EXECUTION_FAILED",
            fields={
                "file_path": file_path,
                "applied": applied,
                "applied_count": len(applied),
                "errors": errors,
            },
        )

    extract_only = _operations_are_extract_only(operations)
    dest_rel = ""
    dest_expected: str | None = None
    if not extract_only:
        dest_rel, dest_expected, dest_err = _resolve_write_destination(
            guard,
            safe_path,
            expected_version=expected_version,
            output_file=output_file,
            output_expected_version=output_expected_version,
        )
        if dest_err:
            return dest_err

    extract_specs, extract_meta, extract_err = _prepare_pending_extracts(
        list(extras.get("extracts") or [])
    )
    if extract_err:
        return extract_err

    from excelmanus.workspace.file_service import TargetSpec, WorkspaceFileService

    specs: list[Any] = list(extract_specs)
    src_rel = workspace_relpath(guard, safe_path).replace("\\", "/")
    source_version = content_version_of_file(safe_path)
    if not extract_only:
        dest_path = Path(guard.workspace_root) / dest_rel
        if dest_path.is_file():
            specs.append(
                TargetSpec(op="update", path=dest_rel, data=_docx_bytes(doc), expected_version=dest_expected)
            )
        else:
            specs.append(TargetSpec(op="create", path=dest_rel, data=_docx_bytes(doc)))

    if not specs:
        payload = {
            "file_path": src_rel,
            "applied": applied,
            "applied_count": len(applied),
            "content_version": source_version,
            "source_file": src_rel,
            "source_version": source_version,
            "status": "success",
        }
    else:
        try:
            svc = WorkspaceFileService(guard.workspace_root)
            receipt = svc.raise_if_failed(svc.apply_batch(specs, actor="word"))
        except CommitError as exc:
            return commit_error_result(exc)
        last = receipt.targets[-1]
        for target in receipt.targets:
            if target.after_version:
                remember_content_version(target.path, target.after_version)
        payload = {
            "file_path": last.to_path or last.path or src_rel,
            "applied": applied,
            "applied_count": len(applied),
            "content_version": last.after_version or source_version,
            "source_file": src_rel,
            "source_version": source_version,
            "status": "success",
            "operation_id": receipt.operation_id,
        }
        if (output_file or "").strip():
            payload["output_file"] = last.path
        if extract_meta:
            extract_target = next((t for t in receipt.targets if t.op in {"create", "update"} and t.path.endswith(".xlsx")), last)
            extract_meta["target_file"] = extract_target.path
            extract_meta["target_content_version"] = extract_target.after_version
        if extract_only:
            payload["file_path"] = src_rel
            payload["content_version"] = source_version
    if extras.get("fill_template"):
        payload["filled_keys"] = list(extras.get("filled_keys") or [])
        payload["unfilled_keys"] = list(extras.get("unfilled_keys") or [])
        payload["bookmarks_filled"] = int(extras.get("bookmarks_filled") or 0)
        if extras.get("source_file"):
            payload["source_file"] = extras.get("source_file")
            payload["source_version"] = extras.get("source_version") or payload.get("source_version")
    if extract_meta:
        payload["target_file"] = extract_meta["target_file"]
        payload["target_sheet"] = extract_meta["target_sheet"]
        payload["created_sheet"] = extract_meta["created_sheet"]
        payload["target_content_version"] = extract_meta["target_content_version"]
    if warnings:
        payload["uncertainties"] = warnings
        uncached = _sum_formulas_uncached(warnings)
        if uncached:
            payload["formulas_uncached"] = uncached
    return from_payload(payload)


def _resolve_write_destination(
    guard: Any,
    source_path: Path,
    *,
    expected_version: str | None,
    output_file: str | None,
    output_expected_version: str | None,
) -> tuple[str, str | None, ToolResult | None]:
    """返回 (commit 相对路径, expected_version, error)。渲染模式不校验源版本。"""
    render_to = (output_file or "").strip()
    if not render_to:
        rel = workspace_relpath(guard, source_path).replace("\\", "/")
        seen = (expected_version or "").strip() or peek_seen_content_version(rel)
        if not seen:
            return "", None, error_result(
                "write_word 必须提供 expected_version",
                code="VERSION_CONFLICT",
                fields={"file_path": rel},
            )
        return rel, seen, None

    fmt_err = _ensure_docx(render_to)
    if fmt_err:
        return "", None, fmt_err
    try:
        dest = guard.resolve_and_validate(render_to)
    except SecurityViolationError as exc:
        return "", None, error_result(
            f"路径校验失败: {exc}",
            code="PATH_INVALID",
            fields={"file_path": render_to},
        )
    dest_rel = workspace_relpath(guard, dest).replace("\\", "/")
    if dest.is_file():
        seen = (
            (output_expected_version or "").strip()
            or peek_seen_content_version(dest_rel)
        )
        if not seen:
            return "", None, error_result(
                "write_word 覆盖 output_file 必须提供 output_expected_version",
                code="VERSION_CONFLICT",
                fields={"file_path": dest_rel},
            )
        return dest_rel, seen, None
    return dest_rel, None, None


# ---------------------------------------------------------------------------
# inspect_word
# ---------------------------------------------------------------------------


def inspect_word(
    file_path: str | None = None,
    *,
    file_paths: list[str] | None = None,
    directory: str = ".",
) -> ToolResult:
    """检查 Word 文档的结构概览。

    可传单个 file_path 或多个 file_paths；若都为空则扫描 directory。
    """
    paths: list[str] = []
    if file_paths:
        paths = file_paths
    elif file_path:
        paths = [file_path]
    else:
        try:
            guard = require_guard()
        except ToolContextMissing:
            return error_result(
                "文件访问守卫未初始化",
                code="TOOL_CONTEXT_MISSING",
                fields={"directory": directory},
            )
        root = guard.resolve_and_validate(directory)
        if root.is_dir():
            for f in sorted(root.iterdir()):
                if f.is_file() and f.suffix.lower() in _WORD_SUFFIXES:
                    try:
                        rel = str(f.relative_to(guard.workspace_root)) if guard else str(f)
                        paths.append(rel)
                    except ValueError:
                        paths.append(f.name)
        if not paths:
            return error_result(f"目录 '{directory}' 下未找到 Word 文件", code="NOT_FOUND")

    results: list[dict[str, Any]] = []

    for fp in paths[:10]:
        fmt_err = _ensure_docx(fp)
        if fmt_err:
            if len(paths) == 1:
                return fmt_err
            results.append({"file_path": fp, "error": _item_error_message(fmt_err)})
            continue

        safe_path, err = _resolve_path(fp)
        if err:
            if len(paths) == 1:
                return err
            results.append({"file_path": fp, "error": _item_error_message(err)})
            continue

        try:
            doc = _open_docx(safe_path)
        except Exception as exc:
            open_err = error_result(
                f"无法打开文档: {exc}",
                code="EXECUTION_FAILED",
                fields={"file_path": fp},
            )
            if len(paths) == 1:
                return open_err
            results.append({"file_path": fp, "error": str(exc)})
            continue

        headings: list[dict[str, Any]] = []
        for i, para in enumerate(doc.paragraphs):
            level = _heading_level(para)
            if level is not None:
                headings.append({"index": i, "level": level, "text": para.text.strip()})

        from docx.enum.section import WD_ORIENT

        sections = []
        for idx, sec in enumerate(doc.sections):
            sections.append({
                "index": idx,
                "width_cm": round(sec.page_width.cm, 1) if sec.page_width else None,
                "height_cm": round(sec.page_height.cm, 1) if sec.page_height else None,
                "orientation": (
                    "landscape" if sec.orientation == WD_ORIENT.LANDSCAPE else "portrait"
                ),
            })

        info: dict[str, Any] = {
            "file_path": fp,
            "total_paragraphs": len(doc.paragraphs),
            "total_tables": len(doc.tables),
            "total_sections": len(doc.sections),
            "headings": headings,
            "sections": sections,
            "size_bytes": safe_path.stat().st_size,
        }

        core = doc.core_properties
        if core.title:
            info["title"] = core.title
        if core.author:
            info["author"] = core.author

        results.append(info)

    if len(results) == 1:
        return from_payload(results[0])
    return from_payload({"files": results})


# ---------------------------------------------------------------------------
# search_word
# ---------------------------------------------------------------------------


def _text_matches(
    text: str,
    query: str,
    match_mode: str,
    case_sensitive: bool,
    flags: int,
) -> bool:
    """段落与表格单元格共用的 match_mode / case_sensitive 语义。"""
    if match_mode == "exact":
        return (text == query) if case_sensitive else (text.lower() == query.lower())
    if match_mode == "startswith":
        return (
            text.startswith(query)
            if case_sensitive
            else text.lower().startswith(query.lower())
        )
    if match_mode == "regex":
        try:
            return bool(re.search(query, text, flags))
        except re.error:
            return False
    return (query in text) if case_sensitive else (query.lower() in text.lower())


def search_word(
    query: str,
    *,
    file_path: str | None = None,
    file_paths: list[str] | None = None,
    match_mode: str = "contains",
    case_sensitive: bool = False,
    max_results: int = 50,
) -> ToolResult:
    """在 Word 文档中搜索段落与表格单元格文本。

    match_mode: contains | exact | regex | startswith
    """
    paths: list[str] = []
    if file_paths:
        paths = file_paths
    elif file_path:
        paths = [file_path]
    else:
        return error_result("必须提供 file_path 或 file_paths", code="INVALID_ARGS")

    flags = 0 if case_sensitive else re.IGNORECASE
    matches: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    inspected_files = 0

    for fp in paths[:10]:
        fmt_err = _ensure_docx(fp)
        if fmt_err:
            if len(paths) == 1:
                return fmt_err
            errors.append({"file_path": fp, "error": _item_error_message(fmt_err)})
            continue

        safe_path, err = _resolve_path(fp)
        if err:
            if len(paths) == 1:
                return err
            errors.append({"file_path": fp, "error": _item_error_message(err)})
            continue

        try:
            doc = _open_docx(safe_path)
        except Exception as exc:
            open_err = error_result(
                f"无法打开文档: {exc}",
                code="EXECUTION_FAILED",
                fields={"file_path": fp},
            )
            if len(paths) == 1:
                return open_err
            errors.append({
                "file_path": fp,
                "error": str(exc),
            })
            continue

        inspected_files += 1

        for i, para in enumerate(doc.paragraphs):
            text = para.text
            if _text_matches(text, query, match_mode, case_sensitive, flags):
                matches.append({
                    "file_path": fp,
                    "kind": "paragraph",
                    "paragraph_index": i,
                    "style": _para_style_name(para),
                    "text": text[:500],
                })
                if len(matches) >= max_results:
                    break

        if len(matches) < max_results:
            for t_i, table in enumerate(doc.tables):
                for r_i, row in enumerate(table.rows):
                    for c_i, cell in enumerate(row.cells):
                        text = cell.text
                        if _text_matches(text, query, match_mode, case_sensitive, flags):
                            matches.append({
                                "file_path": fp,
                                "kind": "table",
                                "table_index": t_i,
                                "row": r_i,
                                "col": c_i,
                                "text": text[:500],
                            })
                            if len(matches) >= max_results:
                                break
                    if len(matches) >= max_results:
                        break
                if len(matches) >= max_results:
                    break

        if len(matches) >= max_results:
            break

    if len(paths[:10]) == 1 and inspected_files == 0 and errors:
        return from_payload(errors[0])

    result: dict[str, Any] = {
        "query": query,
        "match_mode": match_mode,
        "total_matches": len(matches),
        "matches": matches,
    }
    if errors:
        result["errors"] = errors
    return from_payload(result)


# ---------------------------------------------------------------------------
# get_tools
# ---------------------------------------------------------------------------


def get_tools() -> list[ToolDef]:
    return [
        ToolDef(
            name="read_word",
            description=(
                "读取 Word (.docx) 文档的段落内容和表格。"
                "返回段落文本、样式名称和标题层级，可选包含行内格式（加粗/斜体/字号等）。"
                "支持分页：通过 offset 和 max_paragraphs 控制读取范围。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Word 文件路径（相对于工作目录）",
                    },
                    "offset": {
                        "type": "integer",
                        "description": "起始段落索引（0-based），默认 0",
                        "default": 0,
                        "minimum": 0,
                    },
                    "max_paragraphs": {
                        "type": "integer",
                        "description": "最多返回段落数，默认 100",
                        "default": 100,
                        "minimum": 1,
                    },
                    "include_format": {
                        "type": "boolean",
                        "description": "是否包含行内格式信息（加粗/斜体/字号等），默认 false",
                        "default": False,
                    },
                    "include_tables": {
                        "type": "boolean",
                        "description": "是否包含表格数据，默认 true",
                        "default": True,
                    },
                },
                "required": ["file_path"],
                "additionalProperties": False,
            },
            func=read_word,
            max_result_chars=8000,
            write_effect="none",
        ),
        ToolDef(
            name="write_word",
            description=(
                "对 Word (.docx) 文档执行写入操作。"
                "支持的操作：replace（替换段落内容）、insert_after（在段落后插入）、"
                "append（追加段落）、delete（删除段落）、"
                "replace_table（用工作簿 sheet/range 的值矩阵整表替换文档表格）、"
                "fill_template（用 {{列名}} 占位符与书签填模板）、"
                "extract_table（把文档表格抽成 .xlsx 工作簿）。"
                "replace 保留原段落样式与首个 run 的行内格式（加粗/斜体/字号等）；"
                "需要改段落样式时显式传 style。"
                "replace_table 按 table_index（0-based）或表前 caption（子串匹配、忽略大小写）"
                "恰好其一锁定目标表；源为 source_file（.xlsx/.xlsm）及可选 source_sheet/"
                "source_range（缺省 active sheet 的 used range）。"
                "保留表格样式与单元格首选段格式，按源数据增删行列。"
                "replace_table 默认保护合并表结构；传 allow_merged=true 且源数据尺寸相同即可更新合并锚点。"
                "fill_template 扫描正文与表格单元格（含单元格多段、跨 run）中的 {{列名}}，"
                "并填充同名书签；值写入匹配起点所在 run，未命中 run 的格式保留。"
                "值来源恰好其一：values 对象，或 source_file+source_row"
                "（.xlsx/.xlsm；可选 source_sheet、header_row 默认 1，Excel 1-based）。"
                "文档中出现但未给值的占位符与书签保持原文，并在 unfilled_keys 报告。"
                "页眉页脚不在填充范围，请用 run_code。"
                "extract_table 是 replace_table 的反向：按 table_index 或 caption 恰好其一"
                "锁定源表，写入 target_file（必填 .xlsx，不能是源 docx；与工具级 output_file 互斥）。"
                "可选 target_sheet（缺省：新文件 Sheet1 / 已有文件 active sheet；不存在则新建）、"
                "start_cell（缺省 A1）、target_expected_version（覆盖已有工作簿时，或用本轮已见版本）。"
                "单元格文本 strip 后，匹配 ^-?\\d+(\\.\\d+)?$ 且无前导零（0 / 0.xxx 除外）的写成 int/float，"
                "其余保持字符串；空串写成空单元格（None 跳过不覆盖已有值）。"
                "含合并单元格的表仍抽取，但会在 uncertainties 提示合并格值可能重复。"
                "本次 operations 全为 extract_table 时不改源 docx、不要求源 expected_version。"
                "工具级 output_file 将结果另存为新 .docx（不改源模板），"
                "用于名册/报价表逐行生成多份文档；目标已存在时用 output_expected_version"
                "（或本轮已见版本）做版本校验。"
                "每次调用可包含多个操作，按顺序执行。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Word 文件路径（相对于工作目录）",
                    },
                    "operations": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "action": {
                                    "type": "string",
                                    "enum": [
                                        "replace",
                                        "insert_after",
                                        "append",
                                        "delete",
                                        "replace_table",
                                        "fill_template",
                                        "extract_table",
                                    ],
                                },
                                "index": {
                                    "type": "integer",
                                    "description": "目标段落索引（replace/insert_after/delete 需要）",
                                },
                                "text": {
                                    "type": "string",
                                    "description": "新内容（replace/insert_after/append 需要）",
                                },
                                "style": {
                                    "type": "string",
                                    "description": "段落样式名（可选，如 Heading 1, Normal）",
                                },
                                "table_index": {
                                    "type": "integer",
                                    "description": "目标表格序号（0-based）；与 caption 恰好给其一",
                                    "minimum": 0,
                                },
                                "caption": {
                                    "type": "string",
                                    "description": "表前最近非空段落标题（子串匹配，忽略大小写）；与 table_index 恰好给其一",
                                },
                                "source_file": {
                                    "type": "string",
                                    "description": (
                                        "源工作簿路径（.xlsx/.xlsm）；"
                                        "replace_table 必填；fill_template 行源模式与 source_row 联用"
                                    ),
                                },
                                "source_sheet": {
                                    "type": "string",
                                    "description": "源工作表名，缺省 active sheet",
                                },
                                "source_range": {
                                    "type": "string",
                                    "description": "源单元格范围（如 A1:C10），缺省该 sheet 的 used range",
                                },
                                "values": {
                                    "type": "object",
                                    "description": (
                                        "fill_template 显式键值（str→标量），"
                                        "与 source_file+source_row 互斥"
                                    ),
                                    "additionalProperties": True,
                                },
                                "source_row": {
                                    "type": "integer",
                                    "description": (
                                        "fill_template 行源：数据行号（Excel 1-based），"
                                        "必须 ≠ header_row"
                                    ),
                                    "minimum": 1,
                                },
                                "header_row": {
                                    "type": "integer",
                                    "description": "fill_template 行源：表头行号，默认 1",
                                    "default": 1,
                                    "minimum": 1,
                                },
                                "include_headers_footers": {
                                    "type": "boolean",
                                    "description": "fill_template 是否扫描页眉页脚及其表格，默认 true",
                                    "default": True,
                                },
                                "allow_merged": {
                                    "type": "boolean",
                                    "description": "replace_table 是否保留同尺寸的合并单元格结构，默认 false",
                                    "default": False,
                                },
                                "target_file": {
                                    "type": "string",
                                    "description": (
                                        "extract_table 目标工作簿路径（.xlsx）；"
                                        "不可为源 docx，与工具级 output_file 互斥"
                                    ),
                                },
                                "target_sheet": {
                                    "type": "string",
                                    "description": (
                                        "extract_table 目标工作表；"
                                        "缺省新文件为 Sheet1、已有文件为 active sheet；不存在则新建"
                                    ),
                                },
                                "start_cell": {
                                    "type": "string",
                                    "description": "extract_table 写入起点（A1 样式），默认 A1",
                                    "default": "A1",
                                },
                                "target_expected_version": {
                                    "type": "string",
                                    "description": (
                                        "extract_table 覆盖已有 target_file 时的 content_version；"
                                        "缺省使用本轮已读到的目标文件版本"
                                    ),
                                },
                            },
                            "required": ["action"],
                        },
                        "description": "写入操作列表",
                    },
                    "expected_version": {
                        "type": "string",
                        "description": "当前 content_version；缺省使用本轮已读到的版本；渲染模式不校验源版本",
                    },
                    "output_file": {
                        "type": "string",
                        "description": (
                            "渲染输出路径（.docx）。提供时源模板只读、结果另存此文件，"
                            "用于逐行套模板生成多份文档"
                        ),
                    },
                    "output_expected_version": {
                        "type": "string",
                        "description": (
                            "output_file 已存在时的 content_version；"
                            "缺省使用本轮已读到的输出文件版本"
                        ),
                    },
                },
                "required": ["file_path", "operations"],
                "additionalProperties": False,
            },
            func=write_word,
            write_effect="workspace_write",
        ),
        ToolDef(
            name="inspect_word",
            description=(
                "检查 Word (.docx) 文档的结构：标题树、段落数、表格数、节数、页面设置。"
                "可传单个 file_path、多个 file_paths，或扫描 directory 下所有 .docx 文件。"
                "适用于了解文档结构后再进行精准编辑。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "单个 Word 文件路径",
                    },
                    "file_paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "多个文件路径（最多 10 个）",
                    },
                    "directory": {
                        "type": "string",
                        "description": "扫描目录（file_path 和 file_paths 均为空时使用），默认当前目录",
                        "default": ".",
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
            func=inspect_word,
            max_result_chars=6000,
            write_effect="none",
        ),
        ToolDef(
            name="search_word",
            description=(
                "在 Word (.docx) 文档中搜索文本，覆盖段落与表格单元格。"
                "段落命中返回 paragraph_index；表格命中返回 table_index/row/col（均为 0-based）。"
                "支持多种匹配模式：contains（包含）、exact（精确）、regex（正则）、startswith（前缀）。"
                "可同时搜索多个文件。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索字符串或正则表达式",
                    },
                    "file_path": {
                        "type": "string",
                        "description": "单个 Word 文件路径",
                    },
                    "file_paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "多个文件路径（最多 10 个）",
                    },
                    "match_mode": {
                        "type": "string",
                        "enum": ["contains", "exact", "regex", "startswith"],
                        "description": "匹配模式，默认 contains",
                        "default": "contains",
                    },
                    "case_sensitive": {
                        "type": "boolean",
                        "description": "是否区分大小写，默认 false",
                        "default": False,
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "最大返回匹配数，默认 50",
                        "default": 50,
                        "minimum": 1,
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            func=search_word,
            max_result_chars=8000,
            write_effect="none",
        ),
    ]
