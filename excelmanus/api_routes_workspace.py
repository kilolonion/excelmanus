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
from excelmanus.workbook_commit import CommitError, commit_bytes, content_version_of_file
from excelmanus.workspace.identity import IdentityError, is_reserved_relative, resolve_canonical
from excelmanus.workspace.revisions import RevisionIntegrityError, RevisionStore

logger = get_logger("api.revisions")
router = APIRouter()


class RevisionRestoreRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str | None = None
    path: str
    revision_id: str
    expected_version: str


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
    store = RevisionStore(root)
    items = [rec.to_public_dict() for rec in store.list(ident.relative)]
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
    if not dest.is_file():
        raise HTTPException(status_code=404, detail="文件不存在，无法恢复")
    if not request.expected_version.strip():
        raise HTTPException(status_code=400, detail="restore 必须提供 expected_version")
    store = RevisionStore(root)
    current_bytes = dest.read_bytes()
    try:
        tx, before_rec, restore_blob = store.restore_prepare(
            ident.relative, request.revision_id, current_bytes
        )
    except RevisionIntegrityError:
        raise HTTPException(status_code=404, detail="检查点快照损坏")
    except (KeyError, FileNotFoundError):
        raise HTTPException(status_code=404, detail="找不到 revision")
    from excelmanus.security.guard import FileAccessGuard
    guard = FileAccessGuard(str(root))
    try:
        cr = commit_bytes(
            guard=guard,
            file_path=ident.relative,
            data=restore_blob,
            expected_version=request.expected_version,
            record_history=False,
        )
    except CommitError as exc:
        store.discard_transaction(tx)
        status = 409 if exc.code == "VERSION_CONFLICT" else 400
        return JSONResponse(status_code=status, content={
            "status": "error",
            "error": exc.code,
            "message": str(exc),
            "fields": exc.fields,
        })
    try:
        store.finish_restore(
            ident.relative,
            transaction_id=tx,
            after_bytes=restore_blob,
            parent_revision_id=before_rec.id if before_rec is not None else None,
        )
    except Exception:
        logger.warning("restore afterEdit 记录失败，文件已写回 %s", ident.relative, exc_info=True)
    return JSONResponse(content={
        "status": "ok",
        "path": cr.path,
        "content_version": cr.content_version,
        "restored_revision": request.revision_id,
    })
