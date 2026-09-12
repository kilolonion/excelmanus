"""文件相关 API：Excel / Word / 工作区 / 下载 / 文件组。

从 api.py 抽出的独立路由模块。运行时状态只从 api_app_state 读取，
禁止 ``from excelmanus.api import _config`` 反向导入。
由 api.py 在 create_app 中 include_router 注册。
"""

from __future__ import annotations

import os
import re
import uuid
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict

from excelmanus.api_app_state import (
    UnicodeJSONResponse,
    error_json_response as _error_json_response,
    get_config,
    get_file_registry as _get_file_registry,
    get_session_manager,
    make_content_disposition as _make_content_disposition,
    resolve_excel_path as _resolve_excel_path,
    resolve_workspace as _resolve_workspace,
    resolve_workspace_root as _resolve_workspace_root,
    safe_uploads_path as _safe_uploads_path,
    uploads_create_file as _uploads_create_file,
    uploads_delete as _uploads_delete,
    uploads_mkdir as _uploads_mkdir,
    uploads_rename as _uploads_rename,
)
from excelmanus.logger import get_logger
from excelmanus.workbook_commit import content_version_of

logger = get_logger("api.files")

router = APIRouter()


def _bind_file_bytes(path: str) -> tuple[bytes, str]:
    """同一份 bytes 既用于解析也用于 content_version。"""
    from pathlib import Path as _Path

    data = _Path(path).read_bytes()
    return data, content_version_of(data)


def _with_bound_version(payload: dict[str, Any], version: str) -> dict[str, Any]:
    payload["content_version"] = version
    return payload


def _apply_cell_write(ws: Any, cell_ref: str, value: Any) -> None:
    """写入或清空单元格。``ws.cell(..., value=None)`` 不会改已有值。"""
    from openpyxl.utils.cell import coordinate_to_tuple

    row, col = coordinate_to_tuple(str(cell_ref).upper())
    cell = ws.cell(row=row, column=col)
    if value is None or value == "":
        cell.value = None
    else:
        cell.value = value


def _record_commit_history(session_id: str | None, user_id: str | None, rel_path: str, content_version: str) -> None:
    """History is recorded by AtomicPublish / RevisionStore. This is a no-op."""
    _ = (session_id, user_id, rel_path, content_version)
    return


@router.get("/api/v1/files/excel")
async def get_excel_file(request: Request) -> StreamingResponse:
    """返回 xlsx 文件二进制流供前端 Univer 加载。"""
    assert get_config() is not None, "服务未初始化"

    path = request.query_params.get("path", "")
    session_id = request.query_params.get("session_id")
    if not path:
        return _error_json_response(400, "缺少 path 参数")  # type: ignore[return-value]

    ws_root = _resolve_workspace_root(request)
    resolved = _resolve_excel_path(path, session_id, workspace_root=ws_root)
    if resolved is None:
        return _error_json_response(404, f"文件不存在或路径非法: {path}")  # type: ignore[return-value]

    from pathlib import Path as _Path

    file_path = _Path(resolved)
    suffix = file_path.suffix.lower()
    if suffix not in {".xlsx", ".xls", ".xlsb", ".xlsm", ".csv"}:
        return _error_json_response(400, f"不支持的文件格式: {suffix}")  # type: ignore[return-value]

    # .xls/.xlsb → 透明转换为 xlsx 供前端 Univer 加载
    actual_file = resolved
    from excelmanus.xls_converter import needs_conversion as _nc2, ensure_xlsx as _ensure2
    if _nc2(resolved):
        try:
            _xlsx_p2, _ = _ensure2(resolved, workspace_root=ws_root)
            actual_file = str(_xlsx_p2)
            suffix = ".xlsx"
        except Exception:
            logger.warning("excel 流转换失败，返回原始文件: %s", resolved)

    content_type = (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        if suffix in (".xlsx", ".xlsm")
        else "text/csv"
    )

    def _iter_file():
        with open(actual_file, "rb") as f:  # type: ignore[arg-type]
            while chunk := f.read(65536):
                yield chunk

    return StreamingResponse(
        _iter_file(),
        media_type=content_type,
        headers={"Content-Disposition": _make_content_disposition(file_path.name)},
    )

@router.get("/api/v1/files/excel/snapshot")
async def get_excel_snapshot(request: Request) -> JSONResponse:
    """返回 Excel 文件的轻量 JSON 快照（供聊天内嵌预览）。

    参数:
      - path: 文件路径
      - sheet: 指定工作表名（可选）
      - max_rows: 最大行数（默认 50）
      - session_id: 会话 ID（可选）
      - all_sheets: 设为 1 时一次返回所有工作表快照（减少 HTTP 往返）
    """
    assert get_config() is not None, "服务未初始化"

    path = request.query_params.get("path", "")
    sheet = request.query_params.get("sheet")
    max_rows = int(request.query_params.get("max_rows", "50"))
    session_id = request.query_params.get("session_id")
    all_sheets = request.query_params.get("all_sheets", "").strip() in ("1", "true")
    with_styles = request.query_params.get("with_styles", "1").strip() in ("1", "true")

    if not path:
        return _error_json_response(400, "缺少 path 参数")

    ws_root = _resolve_workspace_root(request)
    resolved = _resolve_excel_path(path, session_id, workspace_root=ws_root)
    if resolved is None:
        return _error_json_response(404, f"文件不存在或路径非法: {path}")

    # ── CSV 快捷路径 ──────────────────────────────────────
    if os.path.splitext(resolved)[1].lower() == ".csv":
        try:
            import csv as _csv

            csv_bytes, bound_version = _bind_file_bytes(resolved)
            _enc = "utf-8"
            decoded = ""
            for _try_enc in ("utf-8-sig", "utf-8", "gbk", "gb18030", "latin-1"):
                try:
                    decoded = csv_bytes.decode(_try_enc)
                    _enc = _try_enc
                    break
                except (UnicodeDecodeError, LookupError):
                    continue
            if not decoded and csv_bytes:
                decoded = csv_bytes.decode("latin-1")

            import io as _io

            reader = _csv.reader(_io.StringIO(decoded))
            all_rows_raw: list[list[str]] = []
            for row in reader:
                all_rows_raw.append(row)
                if len(all_rows_raw) > max_rows + 1:
                    break

            total_rows = len(all_rows_raw)
            total_cols = max((len(r) for r in all_rows_raw), default=0)
            headers = all_rows_raw[0] if all_rows_raw else []
            col_letters = [chr(65 + i) if i < 26 else f"A{chr(65 + i - 26)}" for i in range(min(total_cols, 100))]
            data_rows = all_rows_raw[1: min(max_rows + 1, total_rows)]
            # 将纯数字字符串转换为数字
            converted_rows: list[list[Any]] = []
            for dr in data_rows:
                conv: list[Any] = []
                for v in dr:
                    if v == "":
                        conv.append(None)
                    else:
                        try:
                            conv.append(int(v))
                        except ValueError:
                            try:
                                conv.append(float(v))
                            except ValueError:
                                conv.append(v)
                converted_rows.append(conv)

            snap_csv: dict[str, Any] = {
                "file": os.path.basename(resolved),
                "sheet": "Sheet1",
                "sheets": ["Sheet1"],
                "shape": {"rows": total_rows, "columns": total_cols},
                "column_letters": col_letters,
                "headers": headers,
                "rows": converted_rows,
                "total_rows": total_rows,
                "truncated": total_rows > max_rows + 1,
            }
            if all_sheets:
                return JSONResponse(content=_with_bound_version({
                    "file": os.path.basename(resolved),
                    "sheets": ["Sheet1"],
                    "all_snapshots": [snap_csv],
                }, bound_version))
            return JSONResponse(content=_with_bound_version(snap_csv, bound_version))
        except Exception as exc:
            logger.error("CSV snapshot 生成失败: %s", exc, exc_info=True)
            return _error_json_response(500, f"读取文件失败: {exc}")

    try:
        from openpyxl import load_workbook
        from openpyxl.utils import get_column_letter

        from excelmanus.tools._style_extract import extract_cell_style as _extract_cell_style

        # .xls/.xlsb → 透明转换为 xlsx 后再用 openpyxl 打开
        from excelmanus.xls_converter import needs_conversion as _nc, ensure_xlsx as _ensure
        _actual_path = resolved
        if _nc(resolved):
            try:
                _xlsx_p, _ = _ensure(resolved, workspace_root=ws_root)
                _actual_path = str(_xlsx_p)
            except Exception:
                logger.warning("snapshot 转换失败，尝试直接打开: %s", resolved)

        from io import BytesIO

        file_bytes, bound_version = _bind_file_bytes(_actual_path)
        wb = load_workbook(BytesIO(file_bytes), data_only=True, read_only=not with_styles)
        sheet_names = wb.sheetnames

        def _read_sheet(ws_obj: Any) -> dict:
            """读取单个工作表并返回快照 dict。"""
            s_total_rows = ws_obj.max_row or 0
            s_total_cols = ws_obj.max_column or 0
            s_headers: list[str] = []
            s_col_letters: list[str] = []
            for c in range(1, min(s_total_cols + 1, 101)):
                s_col_letters.append(get_column_letter(c))
                cell_val = ws_obj.cell(row=1, column=c).value
                s_headers.append(str(cell_val) if cell_val is not None else "")
            s_rows: list[list[Any]] = []
            s_row_limit = min(max_rows, s_total_rows, 200)
            for r in range(2, s_row_limit + 2):
                if r > s_total_rows:
                    break
                row_data: list[Any] = []
                for c in range(1, min(s_total_cols + 1, 101)):
                    val = ws_obj.cell(row=r, column=c).value
                    if val is None:
                        row_data.append(None)
                    elif isinstance(val, (int, float, bool)):
                        row_data.append(val)
                    else:
                        row_data.append(str(val))
                s_rows.append(row_data)

            result: dict[str, Any] = {
                "sheet": ws_obj.title,
                "shape": {"rows": s_total_rows, "columns": s_total_cols},
                "column_letters": s_col_letters,
                "headers": s_headers,
                "rows": s_rows,
                "total_rows": s_total_rows,
                "truncated": s_total_rows > s_row_limit,
            }

            # 提取样式（仅 with_styles=True 时）
            if with_styles:
                cell_styles: dict[str, dict] = {}
                merged: list[dict] = []
                for r in range(1, s_row_limit + 2):
                    if r > s_total_rows:
                        break
                    for c in range(1, min(s_total_cols + 1, 101)):
                        cell_obj = ws_obj.cell(row=r, column=c)
                        style = _extract_cell_style(cell_obj)
                        if style:
                            cell_styles[f"{r-1},{c-1}"] = style
                # 合并单元格
                try:
                    for merge_range in ws_obj.merged_cells.ranges:
                        merged.append({
                            "startRow": merge_range.min_row - 1,
                            "startColumn": merge_range.min_col - 1,
                            "endRow": merge_range.max_row - 1,
                            "endColumn": merge_range.max_col - 1,
                        })
                except Exception:
                    pass
                # 列宽
                col_widths: dict[str, float] = {}
                try:
                    for col_letter, dim in ws_obj.column_dimensions.items():
                        if dim.width and dim.width != 8.43:  # 默认宽度
                            col_idx = 0
                            for i, ch in enumerate(reversed(col_letter.upper())):
                                col_idx += (ord(ch) - 64) * (26 ** i)
                            col_widths[str(col_idx - 1)] = dim.width
                except Exception:
                    pass
                # 行高
                row_heights: dict[str, float] = {}
                try:
                    for row_idx, dim in ws_obj.row_dimensions.items():
                        if dim.height and dim.height != 15:  # 默认行高
                            row_heights[str(row_idx - 1)] = dim.height
                except Exception:
                    pass

                if cell_styles:
                    result["cell_styles"] = cell_styles
                if merged:
                    result["merged_cells"] = merged
                if col_widths:
                    result["column_widths"] = col_widths
                if row_heights:
                    result["row_heights"] = row_heights

            return result

        if all_sheets:
            # 一次返回所有工作表快照
            snapshots = []
            for sn in sheet_names:
                ws_obj = wb[sn]
                snapshots.append(_read_sheet(ws_obj))
            wb.close()
            return JSONResponse(content=_with_bound_version({
                "file": os.path.basename(resolved),
                "sheets": sheet_names,
                "all_snapshots": snapshots,
            }, bound_version))

        # 单 sheet 模式（向后兼容）
        ws = wb[sheet] if sheet and sheet in sheet_names else wb.active
        if ws is None:
            wb.close()
            return _error_json_response(404, "工作表不存在")

        snap = _read_sheet(ws)
        snap["file"] = os.path.basename(resolved)
        snap["sheets"] = sheet_names
        wb.close()
        return JSONResponse(content=_with_bound_version(snap, bound_version))
    except Exception as exc:
        logger.error("Excel snapshot 生成失败: %s", exc, exc_info=True)
        return _error_json_response(500, f"读取文件失败: {exc}")


class ExcelWriteRequest(BaseModel):
    """Excel 单元格写入请求。"""

    model_config = ConfigDict(extra="forbid")
    session_id: str | None = None
    path: str
    sheet: str | None = None
    changes: list[dict[str, Any]]
    expected_version: str | None = None


@router.post("/api/v1/files/excel/write")
async def write_excel_cells(request: ExcelWriteRequest, raw_request: Request) -> JSONResponse:
    """侧边面板编辑回写：将单元格变更写入文件。"""
    assert get_config() is not None, "服务未初始化"

    ws_root = _resolve_workspace_root(raw_request, session_id=request.session_id)

    # 先解析工作区路径
    resolved = _resolve_excel_path(request.path, request.session_id, workspace_root=ws_root)
    if resolved is None:
        return _error_json_response(404, f"文件不存在或路径非法: {request.path}")

    from pathlib import Path as _Path

    from excelmanus.security.guard import FileAccessGuard
    from excelmanus.tools._helpers import MutationAborted, unwrap_mutation_abort
    from excelmanus.workbook_commit import CommitError, commit_workbook

    try:
        # .xls/.xlsb → 透明转换为 xlsx
        from excelmanus.xls_converter import needs_conversion as _nc3, ensure_xlsx as _ensure3
        if _nc3(resolved):
            try:
                _xlsx_p3, _ = _ensure3(resolved, workspace_root=ws_root)
                resolved = str(_xlsx_p3)
            except Exception:
                pass

        guard = FileAccessGuard(ws_root)
        dest = _Path(resolved).resolve()
        try:
            rel = str(dest.relative_to(guard.workspace_root))
        except ValueError:
            return _error_json_response(400, f"写入路径不在工作区内: {resolved}")

        captured: dict[str, Any] = {}

        def mutate(wb) -> None:
            sheet_names = wb.sheetnames
            cells_written = 0
            for change in request.changes:
                cell_ref = change.get("cell", "")
                if not cell_ref:
                    continue
                sheet_name = change.get("sheet") or request.sheet
                ws = wb[sheet_name] if sheet_name and sheet_name in sheet_names else wb.active
                if ws is None:
                    raise MutationAborted({"error": "工作表不存在"})
                _apply_cell_write(ws, cell_ref, change.get("value"))
                cells_written += 1
            captured["cells_written"] = cells_written

        if request.expected_version is None:
            error_id = str(uuid.uuid4())
            return UnicodeJSONResponse(
                status_code=409,
                content={
                    "error": "更新必须提供 expected_version（来自快照 content_version）",
                    "error_id": error_id,
                    "code": "VERSION_CONFLICT",
                },
            )
        try:
            cr = commit_workbook(
                guard=guard,
                file_path=rel,
                mutate_fn=mutate,
                expected_version=request.expected_version,
            )
        except CommitError as exc:
            aborted = unwrap_mutation_abort(exc)
            if aborted is not None:
                return _error_json_response(404, "工作表不存在")
            status = 409 if exc.code == "VERSION_CONFLICT" else 400 if exc.code == "PATH_INVALID" else 500
            error_id = str(uuid.uuid4())
            return UnicodeJSONResponse(
                status_code=status,
                content={"error": exc.message, "error_id": error_id, "code": exc.code},
            )

        _record_commit_history(
            request.session_id, None, cr.path, cr.content_version,
        )
        return JSONResponse(content={
            "status": "success",
            "cells_written": captured.get("cells_written", 0),
            "content_version": cr.content_version,
        })
    except CommitError as exc:
        status = 409 if exc.code == "VERSION_CONFLICT" else 400 if exc.code == "PATH_INVALID" else 500
        error_id = str(uuid.uuid4())
        return UnicodeJSONResponse(
            status_code=status,
            content={"error": exc.message, "error_id": error_id, "code": exc.code},
        )
    except Exception as exc:
        logger.error("Excel write 失败: %s", exc, exc_info=True)
        return _error_json_response(500, f"写入失败: {exc}")


@router.get("/api/v1/files/excel/list")
async def list_excel_files(request: Request) -> JSONResponse:
    """扫描当前用户 workspace 中所有 Excel 文件，返回路径列表。"""
    assert get_config() is not None, "服务未初始化"
    from pathlib import Path as _Path

    workspace = _Path(_resolve_workspace_root(request)).resolve()
    excel_exts = {".xlsx", ".xls", ".xlsm", ".xlsb", ".csv"}
    skip_dirs = {"node_modules", "__pycache__", ".venv", ".git", ".next"}
    results: list[dict[str, Any]] = []

    import re
    _upload_prefix_re = re.compile(r"^[0-9a-f]{8}_")
    uploads_dir = str(workspace / "uploads")
    backups_dir = str((workspace / "outputs" / "backups").resolve())
    audits_dir = str((workspace / "outputs" / "audits").resolve())

    for root, dirs, filenames in os.walk(workspace):
        dirs[:] = [d for d in dirs if d not in skip_dirs and not d.startswith(".")]
        root_resolved = str(_Path(root).resolve())
        if root_resolved.startswith(backups_dir) or root_resolved.startswith(audits_dir):
            dirs.clear()
            continue
        for fname in filenames:
            ext = os.path.splitext(fname)[1].lower()
            if ext not in excel_exts:
                continue
            full = os.path.join(root, fname)
            try:
                rel = os.path.relpath(full, workspace)
                mtime = os.path.getmtime(full)
            except OSError:
                continue
            display_name = fname
            if root == uploads_dir and _upload_prefix_re.match(fname):
                display_name = fname[9:]
            results.append({
                "path": f"./{rel}",
                "filename": display_name,
                "modified_at": mtime,
            })

    results.sort(key=lambda x: x["modified_at"], reverse=True)
    return JSONResponse(content={"files": results})

_MAX_WORKSPACE_FILES = 2000

# 工作区内部目录，不向前端暴露
_WORKSPACE_HIDDEN_DIRS = frozenset({
    ".tmp", ".staging", ".versions", ".git", ".venv",
    "__pycache__", "node_modules",
})

# uploads/ 下 hash 前缀正则（8位hex + 下划线）
_UPLOAD_PREFIX_RE = re.compile(r"^[0-9a-f]{8}_")

@router.get("/api/v1/files/workspace/list")
async def list_workspace_files(request: Request) -> JSONResponse:
    """扫描用户工作区根目录中的文件与文件夹（用于文件树视图）。

    扫描范围：完整工作区根目录（包含 agent 创建的文件、uploads/ 等）。
    排除：隐藏目录/文件、内部目录（.tmp/.staging/scripts/temp 等）。
    """
    assert get_config() is not None, "服务未初始化"

    ws = _resolve_workspace(request)
    ws_root = str(ws.root_dir)  # 完整工作区根目录

    results: list[dict[str, Any]] = []
    for root, dirs, filenames in os.walk(ws_root):
        # 统一使用 / 分隔符（兼容 Windows）
        rel_root = os.path.relpath(root, ws_root).replace("\\", "/")

        # 过滤隐藏目录与内部目录
        dirs[:] = sorted(
            d for d in dirs
            if not d.startswith(".")
            and d not in _WORKSPACE_HIDDEN_DIRS
            # scripts/temp 是 run_code 临时脚本目录，不展示
            and not (rel_root == "scripts" and d == "temp")
            # outputs/backups 和 outputs/audits 是内部备份/审计目录
            and not (rel_root == "outputs" and d in ("backups", "audits"))
        )

        for dname in dirs:
            full = os.path.join(root, dname)
            try:
                mtime = os.path.getmtime(full)
            except OSError:
                continue
            rel = f"{rel_root}/{dname}" if rel_root != "." else dname
            results.append({
                "path": rel,
                "filename": dname,
                "modified_at": mtime,
                "is_dir": True,
            })
        for fname in sorted(filenames):
            if fname.startswith(".") or fname.startswith("_rc_") or fname.startswith("_sw_"):
                continue
            full = os.path.join(root, fname)
            try:
                mtime = os.path.getmtime(full)
            except OSError:
                continue
            display_name = fname
            # uploads/ 下的 hash 前缀文件显示原始名
            if rel_root == "uploads" or (rel_root != "." and rel_root.startswith("uploads/")):
                if _UPLOAD_PREFIX_RE.match(fname):
                    display_name = fname[9:]
            rel = f"{rel_root}/{fname}" if rel_root != "." else fname
            results.append({
                "path": rel,
                "filename": display_name,
                "modified_at": mtime,
                "is_dir": False,
            })
            if len(results) >= _MAX_WORKSPACE_FILES:
                break
        if len(results) >= _MAX_WORKSPACE_FILES:
            break

    results.sort(key=lambda x: (not x["is_dir"], x["path"].lower()))
    return JSONResponse(content={
        "files": results,
        "truncated": len(results) >= _MAX_WORKSPACE_FILES,
        "workspace_path": ws_root,
    })


@router.get("/api/v1/files/registry")
async def get_file_registry(request: Request) -> JSONResponse:
    """返回 FileRegistry 全量文件列表 + 可选事件历史。

    Query params:
      - include_deleted: bool (default false) — 是否包含已软删除的文件
      - include_events: bool (default false) — 是否附带每个文件的事件历史
      - file_id: str (optional) — 仅返回指定文件及其事件/谱系
    """
    assert get_config() is not None, "服务未初始化"

    ws_root = _resolve_workspace_root(request)
    registry = _get_file_registry(ws_root)
    if registry is None:
        return JSONResponse(content={"files": [], "total": 0})

    include_deleted = request.query_params.get("include_deleted", "").lower() in ("1", "true")
    include_events = request.query_params.get("include_events", "").lower() in ("1", "true")
    file_id = request.query_params.get("file_id", "").strip()

    def _event_to_dict(evt: Any) -> dict[str, Any]:
        return {
            "id": evt.id,
            "file_id": evt.file_id,
            "event_type": evt.event_type,
            "session_id": evt.session_id,
            "turn": evt.turn,
            "tool_name": evt.tool_name,
            "details": evt.details,
            "created_at": evt.created_at,
        }

    # 单文件查询模式
    if file_id:
        entry = registry.get_by_id(file_id)
        if entry is None:
            # 退而求其次：按路径查
            entry = registry.get_by_path(file_id)
        if entry is None:
            return _error_json_response(404, f"文件未找到: {file_id}")
        file_dict = entry.to_dict()
        if include_events:
            file_dict["events"] = [_event_to_dict(e) for e in registry.get_events(entry.id)]
        children = registry.get_children(entry.id)
        file_dict["children"] = [c.to_dict() for c in children]
        lineage = registry.get_lineage(entry.id)
        file_dict["lineage"] = [a.to_dict() for a in lineage]
        return JSONResponse(content={"file": file_dict})

    # 全量列表模式
    entries = registry.list_all(include_deleted=include_deleted)
    files: list[dict[str, Any]] = []
    for entry in entries:
        d = entry.to_dict()
        if include_events:
            d["events"] = [_event_to_dict(e) for e in registry.get_events(entry.id)]
        files.append(d)

    return JSONResponse(content={"files": files, "total": len(files)})

# ── File Groups API ──────────────────────────────────────────

@router.get("/api/v1/files/groups")
async def list_file_groups(request: Request) -> JSONResponse:
    """列出当前工作区的所有文件组（附成员摘要）。"""
    assert get_config() is not None, "服务未初始化"
    ws_root = _resolve_workspace_root(request)
    registry = _get_file_registry(ws_root)
    if registry is None:
        return JSONResponse(content={"groups": []})

    try:
        groups = registry.list_groups()
        result = []
        for g in groups:
            members = registry.get_group_files(g.id)
            result.append({
                **g.to_dict(),
                "members": members,
            })
        return JSONResponse(content={"groups": result})
    except Exception:
        return JSONResponse(content={"groups": []})

@router.post("/api/v1/files/groups")
async def create_file_group(request: Request) -> JSONResponse:
    """创建文件组。

    Body: {name: str, description?: str, file_ids?: [{id: str, role?: str}]}
    """
    assert get_config() is not None, "服务未初始化"
    body = await request.json()
    ws_root = _resolve_workspace_root(request, session_id=(body.get("session_id") or None))
    registry = _get_file_registry(ws_root)
    if registry is None:
        return _error_json_response(500, "FileRegistry 不可用")

    name = body.get("name", "").strip()
    if not name:
        return _error_json_response(400, "缺少文件组名称")

    description = body.get("description", "")
    file_ids_raw = body.get("file_ids", [])

    # 提取纯 file_id 列表用于创建
    plain_ids = []
    role_map: dict[str, str] = {}
    for item in file_ids_raw:
        if isinstance(item, dict):
            fid = item.get("id", "")
            role = item.get("role", "member")
        else:
            fid = str(item)
            role = "member"
        if fid:
            plain_ids.append(fid)
            role_map[fid] = role

    try:
        group = registry.create_group(name, file_ids=plain_ids, description=description)
        # 设置角色（create_group 默认 member，需更新非默认角色）
        for fid, role in role_map.items():
            if role != "member":
                registry.add_to_group(group.id, fid, role)
        members = registry.get_group_files(group.id)
        return JSONResponse(status_code=201, content={**group.to_dict(), "members": members})
    except Exception as exc:
        return _error_json_response(500, f"创建文件组失败: {exc}")

@router.put("/api/v1/files/groups/{group_id}")
async def update_file_group(group_id: str, request: Request) -> JSONResponse:
    """更新文件组名称/描述。

    Body: {name?: str, description?: str}
    """
    assert get_config() is not None, "服务未初始化"
    ws_root = _resolve_workspace_root(request)
    registry = _get_file_registry(ws_root)
    if registry is None:
        return _error_json_response(500, "FileRegistry 不可用")

    body = await request.json()
    name = body.get("name")
    description = body.get("description")

    group = registry.update_group(group_id, name=name, description=description)
    if group is None:
        return _error_json_response(404, f"文件组未找到: {group_id}")

    members = registry.get_group_files(group.id)
    return JSONResponse(content={**group.to_dict(), "members": members})

@router.delete("/api/v1/files/groups/{group_id}")
async def delete_file_group(group_id: str, request: Request) -> JSONResponse:
    """删除文件组。"""
    assert get_config() is not None, "服务未初始化"
    ws_root = _resolve_workspace_root(request)
    registry = _get_file_registry(ws_root)
    if registry is None:
        return _error_json_response(500, "FileRegistry 不可用")

    ok = registry.delete_group(group_id)
    if not ok:
        return _error_json_response(404, f"文件组未找到: {group_id}")
    return JSONResponse(content={"status": "deleted", "group_id": group_id})

@router.put("/api/v1/files/groups/{group_id}/members")
async def update_file_group_members(group_id: str, request: Request) -> JSONResponse:
    """管理文件组成员。

    Body: {add?: [{file_id: str, role?: str}], remove?: [str]}
    """
    assert get_config() is not None, "服务未初始化"
    ws_root = _resolve_workspace_root(request)
    registry = _get_file_registry(ws_root)
    if registry is None:
        return _error_json_response(500, "FileRegistry 不可用")

    group = registry.get_group(group_id)
    if group is None:
        return _error_json_response(404, f"文件组未找到: {group_id}")

    body = await request.json()
    to_add = body.get("add", [])
    to_remove = body.get("remove", [])

    for item in to_add:
        if isinstance(item, dict):
            fid = item.get("file_id", "")
            role = item.get("role", "member")
        else:
            fid = str(item)
            role = "member"
        if fid:
            registry.add_to_group(group_id, fid, role)

    for fid in to_remove:
        if fid:
            registry.remove_from_group(group_id, str(fid))

    members = registry.get_group_files(group_id)
    return JSONResponse(content={**group.to_dict(), "members": members})

@router.get("/api/v1/files/spec")
async def get_spec_file(request: Request) -> JSONResponse:
    """返回 workspace 内 JSON spec 文件内容（WorkbookSpec 等）。"""
    assert get_config() is not None, "服务未初始化"

    path = request.query_params.get("path", "")
    if not path:
        return _error_json_response(400, "缺少 path 参数")  # type: ignore[return-value]

    from excelmanus.security.guard import FileAccessGuard, SecurityViolationError

    ws_root = _resolve_workspace_root(request)
    try:
        file_path = FileAccessGuard(ws_root).resolve_and_validate(path)
    except SecurityViolationError:
        return _error_json_response(404, f"Spec 文件不存在: {path}")  # type: ignore[return-value]

    if not file_path.is_file() or file_path.suffix.lower() != ".json":
        return _error_json_response(404, f"Spec 文件不存在: {path}")  # type: ignore[return-value]

    if file_path.stat().st_size > 2 * 1024 * 1024:
        return _error_json_response(400, "Spec 文件过大")  # type: ignore[return-value]

    import json as _json

    try:
        content = file_path.read_text(encoding="utf-8")
        data = _json.loads(content)
    except Exception as exc:
        return _error_json_response(500, f"读取 spec 失败: {exc}")  # type: ignore[return-value]
    if not isinstance(data, (dict, list)):
        return _error_json_response(400, "Spec 必须是 JSON 对象或数组")  # type: ignore[return-value]

    return JSONResponse(content=data)

@router.get("/api/v1/files/image")
async def get_image_file(request: Request) -> StreamingResponse:
    """返回 workspace 内图片文件的二进制流。"""
    assert get_config() is not None, "服务未初始化"

    path = request.query_params.get("path", "")
    session_id = request.query_params.get("session_id")
    if not path:
        return _error_json_response(400, "缺少 path 参数")  # type: ignore[return-value]

    from pathlib import Path as _Path
    import mimetypes

    ws_root = _resolve_workspace_root(request)

    # 使用与下载相同的路径解析逻辑
    resolved = _resolve_excel_path(path, session_id, workspace_root=ws_root)
    if resolved is None:
        return _error_json_response(404, f"图片文件不存在: {path}")  # type: ignore[return-value]

    file_path = _Path(resolved)

    _IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg"}
    if not file_path.is_file() or file_path.suffix.lower() not in _IMAGE_EXTENSIONS:
        return _error_json_response(404, f"图片文件不存在: {path}")  # type: ignore[return-value]

    content_type = mimetypes.guess_type(file_path.name)[0] or "image/png"

    def _iter_image():
        with open(file_path, "rb") as f:
            while chunk := f.read(65536):
                yield chunk

    return StreamingResponse(
        _iter_image(),
        media_type=content_type,
        headers={"Cache-Control": "public, max-age=3600"},
    )

@router.get("/api/v1/files/read")
async def read_text_file(request: Request) -> JSONResponse:
    """返回 workspace 内文本文件的内容（供代码/MD 预览）。"""
    assert get_config() is not None, "服务未初始化"

    path = request.query_params.get("path", "")
    session_id = request.query_params.get("session_id")
    if not path:
        return _error_json_response(400, "缺少 path 参数")  # type: ignore[return-value]

    from pathlib import Path as _Path

    ws_root = _resolve_workspace_root(request)

    # 使用与下载相同的路径解析逻辑
    resolved = _resolve_excel_path(path, session_id, workspace_root=ws_root)
    if resolved is None:
        return _error_json_response(404, f"文本文件不存在: {path}")  # type: ignore[return-value]

    file_path = _Path(resolved)

    # 支持的文本文件扩展名
    _TEXT_EXTENSIONS = {
        ".txt", ".md", ".markdown", ".json", ".js", ".jsx", ".ts", ".tsx",
        ".py", ".rb", ".go", ".rs", ".java", ".c", ".cpp", ".h", ".hpp",
        ".cs", ".php", ".swift", ".kt", ".scala", ".sh", ".bash", ".zsh",
        ".sql", ".html", ".css", ".scss", ".less", ".xml", ".yaml", ".yml",
        ".toml", ".ini", ".cfg", ".conf", ".log", ".env", ".gitignore",
        ".dockerignore", ".graphql", ".gql", ".vue", ".svelte", ".ex",
        ".exs", ".erl", ".hs", ".ml", ".fs", ".clj", ".lua", ".r", ".dart",
        ".groovy", ".txt", ".csv", ".tsv",
    }

    if not file_path.is_file() or file_path.suffix.lower() not in _TEXT_EXTENSIONS:
        return _error_json_response(404, f"文本文件不存在: {path}")  # type: ignore[return-value]

    # 限制文件大小（最大 1MB）
    if file_path.stat().st_size > 1024 * 1024:
        return _error_json_response(400, "文件过大，无法预览")  # type: ignore[return-value]

    try:
        content = file_path.read_text(encoding="utf-8")
        return JSONResponse(content={"content": content})
    except UnicodeDecodeError:
        return _error_json_response(400, "文件编码不支持，请使用 UTF-8 编码")  # type: ignore[return-value]
    except Exception as exc:
        return _error_json_response(500, f"读取文件失败: {exc}")  # type: ignore[return-value]

@router.get("/api/v1/files/download")
async def download_file(request: Request) -> StreamingResponse:
    """通用文件下载：返回 workspace 内任意文件的二进制流。"""
    assert get_config() is not None, "服务未初始化"

    path = request.query_params.get("path", "")
    session_id = request.query_params.get("session_id")
    if not path:
        return _error_json_response(400, "缺少 path 参数")  # type: ignore[return-value]

    ws_root = _resolve_workspace_root(request)
    resolved = _resolve_excel_path(path, session_id, workspace_root=ws_root)
    if resolved is None:
        return _error_json_response(404, f"文件不存在或路径非法: {path}")  # type: ignore[return-value]

    from pathlib import Path as _Path
    import mimetypes

    file_path = _Path(resolved)
    content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"

    def _iter_file():
        with open(resolved, "rb") as f:  # type: ignore[arg-type]
            while chunk := f.read(65536):
                yield chunk

    return StreamingResponse(
        _iter_file(),
        media_type=content_type,
        headers={"Content-Disposition": _make_content_disposition(file_path.name)},
    )

@router.get("/api/v1/files/excel/compare")
async def get_excel_compare(request: Request) -> JSONResponse:
    """返回两个 Excel 文件的快照 + 跨文件列关系，供前端对比视图使用。

    参数:
      - path_a: 左侧文件路径
      - path_b: 右侧文件路径
      - session_id: 会话 ID（可选）
      - max_rows: 最大行数（默认 50）
    """
    assert get_config() is not None, "服务未初始化"

    path_a = request.query_params.get("path_a", "")
    path_b = request.query_params.get("path_b", "")
    session_id = request.query_params.get("session_id")
    max_rows = int(request.query_params.get("max_rows", "50"))

    if not path_a or not path_b:
        return _error_json_response(400, "缺少 path_a 或 path_b 参数")

    ws_root = _resolve_workspace_root(request)

    resolved_a = _resolve_excel_path(path_a, session_id, workspace_root=ws_root)
    resolved_b = _resolve_excel_path(path_b, session_id, workspace_root=ws_root)

    if resolved_a is None:
        return _error_json_response(404, f"文件不存在: {path_a}")
    if resolved_b is None:
        return _error_json_response(404, f"文件不存在: {path_b}")

    import asyncio

    def _load_snapshot(resolved: str) -> dict[str, Any]:
        """自包含的 snapshot 加载（支持 xlsx/xls/xlsb/csv）。"""
        basename = os.path.basename(resolved)
        ext = os.path.splitext(resolved)[1].lower()

        # ── CSV 快捷路径 ──
        if ext == ".csv":
            try:
                import csv as _csv
                import io as _io

                csv_bytes, bound_version = _bind_file_bytes(resolved)
                decoded = ""
                for _try_enc in ("utf-8-sig", "utf-8", "gbk", "gb18030", "latin-1"):
                    try:
                        decoded = csv_bytes.decode(_try_enc)
                        break
                    except (UnicodeDecodeError, LookupError):
                        continue
                if not decoded and csv_bytes:
                    decoded = csv_bytes.decode("latin-1")
                reader = _csv.reader(_io.StringIO(decoded))
                all_rows_raw: list[list[str]] = []
                for row in reader:
                    all_rows_raw.append(row)
                    if len(all_rows_raw) > max_rows + 1:
                        break
                total = len(all_rows_raw)
                total_cols = max((len(r) for r in all_rows_raw), default=0)
                headers = all_rows_raw[0] if all_rows_raw else []
                col_letters = [chr(65 + i) if i < 26 else f"A{chr(65 + i - 26)}" for i in range(min(total_cols, 100))]
                data_rows = all_rows_raw[1: min(max_rows + 1, total)]
                converted: list[list[Any]] = []
                for dr in data_rows:
                    conv: list[Any] = []
                    for v in dr:
                        if v == "":
                            conv.append(None)
                        else:
                            try:
                                conv.append(int(v))
                            except ValueError:
                                try:
                                    conv.append(float(v))
                                except ValueError:
                                    conv.append(v)
                    converted.append(conv)
                snap_csv = {
                    "file": basename, "sheet": "Sheet1", "sheets": ["Sheet1"],
                    "shape": {"rows": total, "columns": total_cols},
                    "column_letters": col_letters, "headers": headers,
                    "rows": converted, "total_rows": total,
                    "truncated": total > max_rows + 1,
                }
                return _with_bound_version(
                    {"file": basename, "sheets": ["Sheet1"], "all_snapshots": [snap_csv]},
                    bound_version,
                )
            except Exception as exc:
                logger.error("Compare CSV snapshot 失败: %s — %s", resolved, exc)
                return {"file": basename, "sheets": [], "all_snapshots": [], "error": str(exc)}

        # ── xls/xlsb 格式转换 ──
        _actual_path = resolved
        if ext in (".xls", ".xlsb"):
            try:
                from excelmanus.xls_converter import ensure_xlsx as _ensure_cmp
                _xlsx_cmp, _ = _ensure_cmp(resolved, workspace_root=ws_root)
                _actual_path = str(_xlsx_cmp)
            except Exception:
                logger.warning("Compare 格式转换失败，尝试直接打开: %s", resolved)

        # ── xlsx/xlsm 主路径 ──
        from openpyxl import load_workbook as _lwb

        def _read_ws(ws_obj: Any, _max_rows: int = max_rows) -> dict[str, Any]:
            from openpyxl.utils import get_column_letter as _gcl
            s_total_rows = ws_obj.max_row or 0
            s_total_cols = ws_obj.max_column or 0
            s_headers: list[str] = []
            s_col_letters: list[str] = []
            for c in range(1, min(s_total_cols + 1, 101)):
                s_col_letters.append(_gcl(c))
                cell_val = ws_obj.cell(row=1, column=c).value
                s_headers.append(str(cell_val) if cell_val is not None else "")
            s_rows: list[list[Any]] = []
            s_row_limit = min(_max_rows, s_total_rows, 200)
            for r in range(2, s_row_limit + 2):
                if r > s_total_rows:
                    break
                row_data: list[Any] = []
                for c in range(1, min(s_total_cols + 1, 101)):
                    val = ws_obj.cell(row=r, column=c).value
                    if val is None:
                        row_data.append(None)
                    elif isinstance(val, (int, float, bool)):
                        row_data.append(val)
                    else:
                        row_data.append(str(val))
                s_rows.append(row_data)
            return {
                "sheet": ws_obj.title,
                "shape": {"rows": s_total_rows, "columns": s_total_cols},
                "column_letters": s_col_letters,
                "headers": s_headers,
                "rows": s_rows,
                "total_rows": s_total_rows,
                "truncated": s_total_rows > s_row_limit,
            }

        try:
            from io import BytesIO

            file_bytes, bound_version = _bind_file_bytes(_actual_path)
            wb = _lwb(BytesIO(file_bytes), data_only=True, read_only=True)
            sheet_names = wb.sheetnames
            snapshots = []
            for sn in sheet_names:
                ws_obj = wb[sn]
                snap = _read_ws(ws_obj)
                snap["file"] = basename
                snap["sheets"] = sheet_names
                snapshots.append(snap)
            wb.close()
            return _with_bound_version(
                {"file": basename, "sheets": sheet_names, "all_snapshots": snapshots},
                bound_version,
            )
        except Exception as exc:
            logger.error("Compare snapshot 失败: %s — %s", resolved, exc)
            return {"file": basename, "sheets": [], "all_snapshots": [], "error": str(exc)}

    snap_a, snap_b = await asyncio.gather(
        asyncio.to_thread(_load_snapshot, resolved_a),
        asyncio.to_thread(_load_snapshot, resolved_b),
    )

    # ── 跨文件关系检测 ──
    relationships: dict[str, Any] = {"shared_columns": []}
    try:
        from excelmanus.workbook.data import discover_file_relationships as _dfr

        rel_result = await asyncio.to_thread(
            _dfr, file_paths=[resolved_a, resolved_b], max_files=2
        )
        rel_data = rel_result.value if isinstance(getattr(rel_result, "value", None), dict) else {}
        pairs = rel_data.get("file_pairs", [])
        if pairs:
            relationships["shared_columns"] = pairs[0].get("shared_columns", [])
        hints = rel_data.get("merge_hints", [])
        if hints:
            relationships["merge_hint"] = hints[0]
    except Exception as exc:
        logger.debug("Compare 关系检测失败: %s", exc)

    return JSONResponse(content={
        "file_a": snap_a,
        "file_b": snap_b,
        "relationships": relationships,
    })

@router.get("/api/v1/files/relationships")
async def get_file_relationships(request: Request) -> JSONResponse:
    """发现工作区内 Excel 文件之间的列关联关系。

    参数:
      - directory: 扫描目录（可选，默认 "."）
    """
    assert get_config() is not None, "服务未初始化"

    directory = request.query_params.get("directory", ".")
    ws_root = _resolve_workspace_root(request)
    if not directory or directory == ".":
        directory = ws_root

    import asyncio

    try:
        from excelmanus.workbook.data import discover_file_relationships as _dfr

        rel_result = await asyncio.to_thread(_dfr, directory=directory, max_files=5)
        rel_data = rel_result.value if isinstance(getattr(rel_result, "value", None), dict) else {}
        return JSONResponse(content=rel_data)
    except Exception as exc:
        logger.error("文件关系发现失败: %s", exc, exc_info=True)
        return _error_json_response(500, f"分析失败: {exc}")

# ── Word 文件 API ───────────────────────────────────────────────

def _resolve_supported_word_file(
    requested_path: str,
    resolved_path: str | None,
) -> tuple["Path | None", JSONResponse | None]:
    from pathlib import Path as _Path

    if resolved_path is None:
        return None, _error_json_response(404, f"Word 文件不存在: {requested_path}")

    file_path = _Path(resolved_path)
    if not file_path.is_file():
        return None, _error_json_response(404, f"Word 文件不存在: {requested_path}")

    if file_path.suffix.lower() != ".docx":
        return None, _error_json_response(404, f"Word 文件格式不支持（仅支持 .docx）: {requested_path}")

    return file_path, None

@router.get("/api/v1/files/word")
async def get_word_file(request: Request) -> StreamingResponse:
    """返回 .docx 文件二进制流供前端 Univer Doc 加载。"""
    assert get_config() is not None, "服务未初始化"

    path = request.query_params.get("path", "")
    session_id = request.query_params.get("session_id")
    if not path:
        return _error_json_response(400, "缺少 path 参数")  # type: ignore[return-value]

    ws_root = _resolve_workspace_root(request)
    resolved = _resolve_excel_path(path, session_id, workspace_root=ws_root)
    file_path, error_response = _resolve_supported_word_file(path, resolved)
    if error_response is not None:
        return error_response  # type: ignore[return-value]

    def _iter_file():
        with open(resolved, "rb") as f:  # type: ignore[arg-type]
            while chunk := f.read(65536):
                yield chunk

    return StreamingResponse(
        _iter_file(),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": _make_content_disposition(file_path.name)},
    )

@router.get("/api/v1/files/word/snapshot")
async def get_word_snapshot(request: Request) -> JSONResponse:
    """返回 Word 文档的 JSON 快照（段落+样式+表格），供前端 Univer Doc 渲染。"""
    assert get_config() is not None, "服务未初始化"

    path = request.query_params.get("path", "")
    session_id = request.query_params.get("session_id")
    max_paragraphs = int(request.query_params.get("max_paragraphs", "500"))
    if not path:
        return _error_json_response(400, "缺少 path 参数")

    ws_root = _resolve_workspace_root(request)
    resolved = _resolve_excel_path(path, session_id, workspace_root=ws_root)
    file_path, error_response = _resolve_supported_word_file(path, resolved)
    if error_response is not None:
        return error_response

    try:
        import asyncio
        snapshot = await asyncio.to_thread(_build_word_snapshot, str(file_path), max_paragraphs)
        snapshot["file"] = path
        return JSONResponse(content=snapshot)
    except Exception as exc:
        logger.error("Word snapshot 生成失败: %s", exc, exc_info=True)
        return _error_json_response(500, f"读取 Word 文件失败: {exc}")

def _build_word_snapshot(file_path: str, max_paragraphs: int = 500) -> dict[str, Any]:
    """构建 Word 文档 JSON 快照。

    返回结构化数据，包含段落列表（含文本、样式、行内格式）和表格，
    供前端转换为 Univer Doc IDocumentData。
    """
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    doc = Document(file_path)

    alignment_map = {
        WD_ALIGN_PARAGRAPH.LEFT: "left",
        WD_ALIGN_PARAGRAPH.CENTER: "center",
        WD_ALIGN_PARAGRAPH.RIGHT: "right",
        WD_ALIGN_PARAGRAPH.JUSTIFY: "justify",
    }

    paragraphs: list[dict[str, Any]] = []
    for i, para in enumerate(doc.paragraphs):
        if i >= max_paragraphs:
            break
        style_name = para.style.name if para.style else "Normal"

        entry: dict[str, Any] = {
            "text": para.text,
            "style": style_name,
        }

        if style_name.startswith("Heading"):
            try:
                entry["heading_level"] = int(style_name.split()[-1])
            except (ValueError, IndexError):
                pass
        elif style_name == "Title":
            entry["heading_level"] = 0

        if para.alignment is not None:
            entry["alignment"] = alignment_map.get(para.alignment, "left")

        runs: list[dict[str, Any]] = []
        for run in para.runs:
            rd: dict[str, Any] = {"text": run.text}
            if run.bold:
                rd["bold"] = True
            if run.italic:
                rd["italic"] = True
            if run.underline:
                rd["underline"] = True
            if run.font.size:
                rd["size_pt"] = round(run.font.size.pt, 1)
            if run.font.name:
                rd["font_name"] = run.font.name
            if run.font.color and run.font.color.rgb:
                rd["color"] = str(run.font.color.rgb)
            runs.append(rd)

        if runs:
            entry["runs"] = runs

        paragraphs.append(entry)

    tables: list[dict[str, Any]] = []
    for idx, tbl in enumerate(doc.tables):
        rows_data: list[list[str]] = []
        for row in tbl.rows:
            rows_data.append([cell.text.strip() for cell in row.cells])
        tables.append({
            "index": idx,
            "rows": len(tbl.rows),
            "columns": len(tbl.columns),
            "data": rows_data,
        })

    properties: dict[str, Any] = {}
    core = doc.core_properties
    if core.title:
        properties["title"] = core.title
    if core.author:
        properties["author"] = core.author

    return {
        "total_paragraphs": len(doc.paragraphs),
        "returned_paragraphs": len(paragraphs),
        "truncated": len(doc.paragraphs) > max_paragraphs,
        "paragraphs": paragraphs,
        "tables": tables,
        "total_tables": len(doc.tables),
        "sections": len(doc.sections),
        "properties": properties,
    }


class WordWriteRequest(BaseModel):
    """Word 文档写入请求。"""

    model_config = ConfigDict(extra="forbid")
    session_id: str | None = None
    path: str
    operations: list[dict[str, Any]]
    expected_version: str


@router.post("/api/v1/files/word/write")
async def write_word_content(request: WordWriteRequest, raw_request: Request) -> JSONResponse:
    """前端编辑回写：将内容变更写入 Word 文件。"""
    assert get_config() is not None, "服务未初始化"

    if not request.expected_version.strip():
        return _error_json_response(400, "write 必须提供 expected_version")

    ws_root = _resolve_workspace_root(raw_request, session_id=request.session_id)
    resolved = _resolve_excel_path(request.path, request.session_id, workspace_root=ws_root)
    file_path, error_response = _resolve_supported_word_file(request.path, resolved)
    if error_response is not None:
        return error_response

    try:
        import asyncio
        result = await asyncio.to_thread(
            _apply_word_write,
            str(file_path),
            request.operations,
            request.expected_version,
            str(ws_root),
        )
        status = 200
        if isinstance(result, dict) and result.get("code") == "VERSION_CONFLICT":
            status = 409
        elif isinstance(result, dict) and result.get("status") == "error" and result.get("code"):
            status = 400
        return JSONResponse(status_code=status, content=result)
    except Exception as exc:
        logger.error("Word write 失败: %s", exc, exc_info=True)
        return _error_json_response(500, f"写入失败: {exc}")


def _apply_word_write(
    file_path: str,
    operations: list[dict[str, Any]],
    expected_version: str,
    workspace_root: str,
) -> dict[str, Any]:
    """执行 Word 文档写入：任一操作失败则不落盘。"""
    from docx import Document

    from excelmanus.security.guard import FileAccessGuard
    from excelmanus.tools.word_tools import apply_word_operations, _docx_bytes
    from excelmanus.workbook_commit import CommitError, commit_bytes

    doc = Document(file_path)
    applied, errors = apply_word_operations(doc, operations)
    if errors:
        return {
            "status": "error",
            "applied": applied,
            "applied_count": len(applied),
            "errors": errors,
        }

    guard = FileAccessGuard(workspace_root)
    from pathlib import Path as _Path
    dest = _Path(file_path).resolve()
    rel = str(dest.relative_to(guard.workspace_root)).replace("\\", "/")
    try:
        cr = commit_bytes(
            guard=guard,
            file_path=rel,
            data=_docx_bytes(doc),
            expected_version=expected_version,
        )
    except CommitError as exc:
        return {
            "status": "error",
            "code": exc.code,
            "error": exc.message,
            "message": exc.message,
            "fields": exc.fields,
        }
    return {
        "status": "success",
        "applied": applied,
        "applied_count": len(applied),
        "content_version": cr.content_version,
        "path": cr.path,
    }

# ── 文件管理 API ─────────────────────────────────────

@router.post("/api/v1/files/workspace/mkdir")
async def workspace_mkdir(request: Request) -> JSONResponse:
    """在 uploads/ 下创建子目录。"""
    assert get_config() is not None, "服务未初始化"
    body = await request.json()
    path = body.get("path", "").strip()
    if not path:
        return _error_json_response(400, "缺少 path 参数")

    ws = _resolve_workspace(request, session_id=(body.get("session_id") or None))
    uploads = ws.get_upload_dir()
    existing = _safe_uploads_path(uploads, path)
    if existing is not None:
        try:
            os.lstat(existing)
            return _error_json_response(409, "目录已存在")
        except FileNotFoundError:
            pass
    target = _uploads_mkdir(uploads, path)
    if target is None:
        return _error_json_response(400, "非法目标路径")
    return JSONResponse(content={"status": "created", "path": path})

@router.post("/api/v1/files/workspace/create")
async def workspace_create_file(request: Request) -> JSONResponse:
    """在 uploads/ 下创建空文件。"""
    assert get_config() is not None, "服务未初始化"
    body = await request.json()
    path = body.get("path", "").strip()
    if not path:
        return _error_json_response(400, "缺少 path 参数")

    ws = _resolve_workspace(request, session_id=(body.get("session_id") or None))
    uploads = ws.get_upload_dir()
    target = _uploads_create_file(uploads, path)
    if target is None:
        existing = _safe_uploads_path(uploads, path)
        if existing is not None:
            try:
                os.lstat(existing)
                return _error_json_response(409, "文件已存在")
            except FileNotFoundError:
                pass
        return _error_json_response(400, "非法目标路径")
    return JSONResponse(content={"status": "created", "path": path})

@router.delete("/api/v1/files/workspace/item")
async def workspace_delete_item(request: Request) -> JSONResponse:
    """删除 uploads/ 下的文件或文件夹。"""
    assert get_config() is not None, "服务未初始化"
    body = await request.json()
    path = body.get("path", "").strip()
    if not path:
        return _error_json_response(400, "缺少 path 参数")

    ws = _resolve_workspace(request, session_id=(body.get("session_id") or None))
    uploads = ws.get_upload_dir()
    target, err = _uploads_delete(uploads, path)
    if err == "路径不存在":
        return _error_json_response(404, err)
    if target is None:
        return _error_json_response(400, err or "非法目标路径")
    if get_session_manager() is not None:
        get_session_manager().notify_file_deleted(str(target))
    return JSONResponse(content={"status": "deleted", "path": path})

@router.post("/api/v1/files/workspace/rename")
async def workspace_rename_item(request: Request) -> JSONResponse:
    """重命名 uploads/ 下的文件或文件夹。"""
    assert get_config() is not None, "服务未初始化"
    body = await request.json()
    old_path = body.get("old_path", "").strip()
    new_path = body.get("new_path", "").strip()
    ws = _resolve_workspace(request, session_id=(body.get("session_id") or None))
    uploads = ws.get_upload_dir()
    src, dst, err = _uploads_rename(uploads, old_path, new_path)
    if err == "源路径不存在":
        return _error_json_response(404, err)
    if err == "目标路径已存在":
        return _error_json_response(409, err)
    if src is None or dst is None:
        return _error_json_response(400, err or "非法路径")
    if get_session_manager() is not None:
        get_session_manager().notify_file_renamed(str(src), str(dst))
    return JSONResponse(content={"status": "renamed", "old_path": old_path, "new_path": new_path})

@router.post("/api/v1/files/reveal")
async def reveal_file(request: Request) -> JSONResponse:
    """在本地文件管理器中打开文件所在目录。"""
    # 服务器/Docker 模式下此功能无意义（打开的是服务器端文件管理器，用户不可见）
    if get_config() is not None and get_config().is_server:
        return _error_json_response(
            400,
            "此功能仅在本地部署模式下可用。服务器模式下无法打开本地文件管理器。",
        )

    import platform
    import subprocess

    body = await request.json()
    file_path = body.get("path", "").strip()
    if not file_path:
        return _error_json_response(400, "缺少 path 参数")

    target_raw = os.path.abspath(file_path)
    if get_config() is not None:
        from excelmanus.security.guard import FileAccessGuard, SecurityViolationError

        try:
            target_path = FileAccessGuard(get_config().workspace_root).resolve_and_validate(file_path)
        except SecurityViolationError:
            return _error_json_response(403, "路径不在工作区范围内")
        target = str(target_path)
    else:
        target = target_raw
    if not os.path.exists(target):
        return _error_json_response(404, f"路径不存在: {target}")

    try:
        system = platform.system()
        if system == "Darwin":
            subprocess.Popen(["open", "-R", target])
        elif system == "Windows":
            subprocess.Popen(["explorer", "/select,", target])
        else:
            parent = os.path.dirname(target) if os.path.isfile(target) else target
            subprocess.Popen(["xdg-open", parent])
    except Exception as exc:
        logger.warning("打开文件管理器失败: %s", exc)
        return _error_json_response(500, f"打开失败: {exc}")

    return JSONResponse(content={"status": "ok", "path": target})
