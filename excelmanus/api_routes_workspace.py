"""Revision APIs. Overlay /backup and turn-checkpoint file rollback are gone."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict
from starlette.concurrency import run_in_threadpool

from excelmanus.api_app_state import (
    get_config,
    get_session_manager,
)
from excelmanus.logger import get_logger
from excelmanus.session import SessionBusyError
from excelmanus.stores.workspace_store import WorkspacePathError
from excelmanus.workbook_commit import CommitError, content_version_of_file
from excelmanus.workspace.file_service import WorkspaceFileService
from excelmanus.workspace.identity import IdentityError, is_reserved_relative, resolve_canonical
from excelmanus.workspace.revisions import RevisionIntegrityError

logger = get_logger("api.revisions")
router = APIRouter()


class RevisionRestoreRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str | None = None
    workspace_id: str | None = None
    path: str
    revision_id: str
    expected_version: str | None = None


class RevisionDeleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str | None = None
    workspace_id: str | None = None
    path: str
    revision_id: str


class TransactionRecoveryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    operation_id: str
    action: str = "resume"


@router.post("/api/v1/transactions/recover")
async def recover_transaction(request: TransactionRecoveryRequest) -> JSONResponse:
    """Retry remaining prepared writes, or explicitly abandon them without undo."""
    if request.action not in {"resume", "abort"}:
        raise HTTPException(400, "action 必须是 resume 或 abort")
    manager = get_session_manager()
    if manager is None:
        raise HTTPException(503, "服务未初始化")
    try:
        await manager.get_engine_if_idle(request.session_id)
    except SessionBusyError:
        raise HTTPException(409, "会话正在处理中")
    svc = WorkspaceFileService(_workspace_root(request.session_id))
    try:
        if request.action == "abort":
            receipt = svc.abort_recovery(request.operation_id).to_dict()
        else:
            svc.recover()
            receipt = svc.txlog.read_receipt(request.operation_id)
        if not receipt:
            raise HTTPException(404, "找不到操作")
        return JSONResponse(content=receipt)
    except CommitError as exc:
        raise HTTPException(409, detail={"code": exc.code, "message": str(exc)}) from exc


def _workspace_root(session_id: str | None = None, workspace_id: str | None = None) -> Path:
    cfg = get_config()
    if cfg is None:
        raise HTTPException(status_code=503, detail="服务未初始化")
    manager = get_session_manager()
    if (session_id or workspace_id) and manager is None:
        raise HTTPException(status_code=503, detail="服务未初始化")
    if session_id and manager is not None:
        if not manager.session_exists(session_id):
            raise HTTPException(status_code=404, detail="会话不存在，无法确定历史文件所属工作区")
        session_root = Path(manager.workspace_path_for_session(session_id)).expanduser().resolve()
        if workspace_id:
            try:
                path, _ = manager.resolve_workspace_binding(workspace_id, None)
            except WorkspacePathError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            if Path(path).expanduser().resolve() != session_root:
                raise HTTPException(status_code=409, detail="会话与文件工作区不一致，请重新打开文件历史")
        return session_root
    if workspace_id and manager is not None:
        try:
            path, _ = manager.resolve_workspace_binding(workspace_id, None)
        except WorkspacePathError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return Path(path).expanduser().resolve()
    from excelmanus.workspace.paths import default_workspace_path
    return Path(default_workspace_path(cfg))


@router.get("/api/v1/revisions")
async def list_revisions(
    path: str, session_id: str | None = None, workspace_id: str | None = None,
    limit: int = 100,
) -> JSONResponse:
    return await run_in_threadpool(_list_revisions, path, session_id, workspace_id, limit)


def _list_revisions(path: str, session_id: str | None, workspace_id: str | None, limit: int) -> JSONResponse:
    """Revision timeline for one identity. Restore requires current content_version."""
    root = _workspace_root(session_id, workspace_id)
    try:
        ident = resolve_canonical(root, path)
    except IdentityError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    dest = root / ident.relative
    current = content_version_of_file(dest) if dest.is_file() else None
    if limit < 1 or limit > 500:
        raise HTTPException(status_code=400, detail="limit 必须在 1 到 500 之间")
    history = WorkspaceFileService(root).list_history(ident.relative)
    items = [rec.to_public_dict() for rec in history[-limit:]]
    return JSONResponse(content={
        "path": ident.public,
        "content_version": current,
        "revisions": items,
        "total": len(history),
    })


@router.post("/api/v1/revisions/restore")
async def restore_revision(request: RevisionRestoreRequest) -> JSONResponse:
    session_manager = get_session_manager()
    if session_manager is None:
        raise HTTPException(status_code=503, detail="服务未初始化")
    if request.session_id:
        try:
            engine = await session_manager.get_engine_if_idle(request.session_id)
        except SessionBusyError:
            return JSONResponse(status_code=409, content={"detail": "会话正在处理中，请等待完成后再恢复。"})
        if engine is None:
            raise HTTPException(status_code=404, detail=f"会话 '{request.session_id}' 不存在或未加载。")
    root = _workspace_root(request.session_id, request.workspace_id)
    try:
        ident = resolve_canonical(root, request.path)
    except IdentityError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if is_reserved_relative(ident.relative):
        raise HTTPException(status_code=400, detail=f"reserved namespace: {ident.relative}")
    dest = root / ident.relative
    live = dest.is_file()
    expected = (request.expected_version or "").strip()
    if live and not expected:
        raise HTTPException(status_code=400, detail="restore 必须提供 expected_version")
    svc = WorkspaceFileService(root)
    try:
        receipt = await run_in_threadpool(
            svc.restore,
            ident.relative,
            request.revision_id,
            expected_version=expected or None,
            restore_missing=not live and not expected,
            event_context={"source": "user", "session_id": request.session_id,
                           "summary": f"用户恢复了整个文件到历史版本 {request.revision_id}，请重新读取。"}
            if dest.suffix.lower() in {".xlsx", ".xlsm", ".xls", ".xlsb", ".csv"} else None,
        )
        svc.raise_if_failed(receipt)
    except RevisionIntegrityError:
        raise HTTPException(status_code=404, detail="检查点快照损坏")
    except CommitError as exc:
        if exc.code == "NOT_FOUND":
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        status = 409 if exc.code in {"VERSION_CONFLICT", "PATH_OCCUPIED"} else 400
        return JSONResponse(status_code=status, content={
            "status": "error",
            "error": exc.code,
            "message": str(exc),
            "fields": exc.fields,
        })
    try:
        await run_in_threadpool(session_manager.drain_workspace_events)
    except Exception:
        logger.warning("历史版本已恢复，改动通知等待重试", exc_info=True)
    return JSONResponse(content={
        "status": "ok",
        "path": receipt.primary_path(),
        "content_version": receipt.primary_version(),
        "restored_revision": request.revision_id,
        "lineage_id": receipt.targets[-1].lineage_id if receipt.targets else None,
        "exists_after": True,
    })


@router.post("/api/v1/revisions/delete")
async def delete_revision(request: RevisionDeleteRequest) -> JSONResponse:
    return await run_in_threadpool(_delete_revision, request)


def _delete_revision(request: RevisionDeleteRequest) -> JSONResponse:
    root = _workspace_root(request.session_id, request.workspace_id)
    try:
        ident = resolve_canonical(root, request.path)
    except IdentityError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        WorkspaceFileService(root).delete_checkpoint(ident.relative, request.revision_id)
    except CommitError as exc:
        status = 404 if exc.code == "NOT_FOUND" else 400
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    except RevisionIntegrityError as exc:
        raise HTTPException(status_code=404, detail="检查点不存在或已损坏") from exc
    return JSONResponse(content={"status": "ok", "path": ident.public, "deleted_revision": request.revision_id})


@router.get("/api/v1/revisions/content")
async def read_revision_content(
    path: str, revision_id: str, session_id: str | None = None, workspace_id: str | None = None,
) -> StreamingResponse:
    return await run_in_threadpool(_read_revision_content, path, revision_id, session_id, workspace_id)


def _read_revision_content(path: str, revision_id: str, session_id: str | None, workspace_id: str | None) -> StreamingResponse:
    root = _workspace_root(session_id, workspace_id)
    try:
        ident = resolve_canonical(root, path)
        rec, data = WorkspaceFileService(root).read_history(ident.relative, revision_id)
    except IdentityError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (CommitError, RevisionIntegrityError, KeyError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail="历史版本不存在或已损坏") from exc
    media_type = "application/octet-stream"
    suffix = Path(ident.relative).suffix.lower()
    if suffix == ".xlsx":
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    elif suffix == ".xlsm":
        media_type = "application/vnd.ms-excel.sheet.macroEnabled.12"
    elif suffix == ".docx":
        media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    from excelmanus.workspace.identity import display_name_for
    return StreamingResponse(
        iter((data,)),
        media_type=media_type,
        headers={"Content-Disposition": f"inline; filename*=UTF-8''{quote(display_name_for(ident.relative), safe='')}", "X-Revision-Id": rec.id},
    )


@router.get("/api/v1/revisions/preview")
async def preview_revision(
    path: str, revision_id: str, session_id: str | None = None, workspace_id: str | None = None,
    sheet: str | None = None, rect: str = "A1:Z50",
) -> JSONResponse:
    return await run_in_threadpool(_preview_revision, path, revision_id, session_id, workspace_id, sheet, rect)


def _preview_revision(
    path: str, revision_id: str, session_id: str | None, workspace_id: str | None,
    sheet: str | None, rect: str,
) -> JSONResponse:
    """Project an immutable revision into the same bounded workbook view as the live file."""
    from excelmanus.workbook.refs import InvalidRefError, parse_rect
    from excelmanus.workbook.snapshot import SnapshotError, open_snapshot_bytes, project_view
    from excelmanus.workspace.refs import WorkspaceRef
    from zipfile import BadZipFile
    from xml.etree.ElementTree import ParseError
    from lxml.etree import XMLSyntaxError

    root = _workspace_root(session_id, workspace_id)
    try:
        base = parse_rect(rect, default_sheet=sheet)
        if (base.max_row - base.min_row + 1) * (base.max_col - base.min_col + 1) > 20_000:
            raise HTTPException(status_code=400, detail={"code": "VIEW_TOO_LARGE", "message": "单次历史预览最多读取 20000 个单元格"})
        ident = resolve_canonical(root, path)
        rec, data = WorkspaceFileService(root).read_history(ident.relative, revision_id)
        suffix = Path(ident.relative).suffix.lower()
        if suffix == ".docx":
            import tempfile
            from excelmanus.api_routes_files import _build_word_snapshot

            # Close the writer before python-docx opens it (Windows file sharing).
            with tempfile.TemporaryDirectory(prefix="excelmanus-revision-") as directory:
                source = Path(directory) / "preview.docx"
                source.write_bytes(data)
                payload = _build_word_snapshot(str(source), max_paragraphs=100)
            payload.update({
                "file": ident.public,
                "content_version": rec.to_public_dict()["content_version"],
                "revision_id": rec.id,
                "revision_reason": rec.reason,
                "revision_label": rec.label or "",
            })
            return JSONResponse(content=payload)
        if suffix not in {".xlsx", ".xlsm", ".csv", ".tsv"}:
            raise HTTPException(status_code=400, detail="历史预览目前支持 .xlsx/.xlsm/.csv/.tsv/.docx")
        snapshot = open_snapshot_bytes(
            data,
            relative=ident.relative,
            workspace=WorkspaceRef.from_root(root, workspace_id=workspace_id),
            suffix=Path(ident.relative).suffix.lower(),
        )
        view = project_view(snapshot, [base], with_styles=True, active_sheet_default=True)
        view["revision_id"] = rec.id
        view["revision_reason"] = rec.reason
        view["revision_label"] = rec.label or ""
        return JSONResponse(content=view)
    except IdentityError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (CommitError, RevisionIntegrityError, KeyError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail="历史版本不存在或已损坏") from exc
    except InvalidRefError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except SnapshotError as exc:
        raise HTTPException(status_code=400, detail={"code": exc.code, "message": str(exc), **exc.fields}) from exc
    except (BadZipFile, ParseError, XMLSyntaxError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="历史文件无法解析，请下载原始版本检查文件内容") from exc
