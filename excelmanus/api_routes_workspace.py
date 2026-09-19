"""Revision APIs. Overlay /backup and turn-checkpoint file rollback are gone."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from excelmanus.api_app_state import (
    get_config,
    get_session_manager,
)
from excelmanus.logger import get_logger
from excelmanus.session import SessionBusyError
from excelmanus.workbook_commit import CommitError, content_version_of_file
from excelmanus.workspace.file_service import WorkspaceFileService
from excelmanus.workspace.identity import IdentityError, is_reserved_relative, resolve_canonical
from excelmanus.workspace.revisions import RevisionIntegrityError

logger = get_logger("api.revisions")
router = APIRouter()


class RevisionRestoreRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str | None = None
    path: str
    revision_id: str
    expected_version: str | None = None


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


def _workspace_root(session_id: str | None = None) -> Path:
    cfg = get_config()
    if cfg is None:
        raise HTTPException(status_code=503, detail="服务未初始化")
    manager = get_session_manager()
    if session_id and manager is not None:
        return Path(manager.workspace_path_for_session(session_id)).expanduser().resolve()
    from excelmanus.workspace.paths import default_workspace_path
    return Path(default_workspace_path(cfg))


@router.get("/api/v1/revisions")
async def list_revisions(path: str, session_id: str | None = None) -> JSONResponse:
    """Revision timeline for one identity. Restore requires current content_version."""
    root = _workspace_root(session_id)
    try:
        ident = resolve_canonical(root, path)
    except IdentityError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    dest = root / ident.relative
    current = content_version_of_file(dest) if dest.is_file() else None
    items = [rec.to_public_dict() for rec in WorkspaceFileService(root).list_history(ident.relative)]
    return JSONResponse(content={
        "path": ident.public,
        "content_version": current,
        "revisions": items,
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
    root = _workspace_root(request.session_id)
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
        receipt = svc.restore(
            ident.relative,
            request.revision_id,
            expected_version=expected or None,
            restore_missing=not live,
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
    return JSONResponse(content={
        "status": "ok",
        "path": receipt.primary_path(),
        "content_version": receipt.primary_version(),
        "restored_revision": request.revision_id,
        "lineage_id": receipt.targets[-1].lineage_id if receipt.targets else None,
        "exists_after": True,
    })
