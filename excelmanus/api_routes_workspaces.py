"""Registered workspace folders (existing directories only)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from excelmanus.api_app_state import (
    error_json_response as _error_json_response,
    get_session_manager,
)
from excelmanus.logger import get_logger
from excelmanus.stores.workspace_store import WorkspacePathError

logger = get_logger("api.workspaces")

router = APIRouter()


@router.get("/api/v1/workspaces")
async def list_workspaces() -> JSONResponse:
    session_manager = get_session_manager()
    if session_manager is None:
        raise HTTPException(status_code=503, detail="服务未初始化")
    workspaces = await run_in_threadpool(session_manager.list_workspaces)
    return JSONResponse(content={"workspaces": workspaces})


@router.post("/api/v1/workspaces")
async def create_workspace(request: Request) -> JSONResponse:
    session_manager = get_session_manager()
    if session_manager is None:
        raise HTTPException(status_code=503, detail="服务未初始化")
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    path = str(body.get("path") or "").strip()
    title = str(body.get("title") or "").strip()
    if not path:
        return _error_json_response(400, "缺少 path 参数")
    try:
        rec, created = session_manager.register_workspace(path, title=title)
    except WorkspacePathError as exc:
        return _error_json_response(400, str(exc))
    return JSONResponse(
        content={"workspace": rec, "created": created},
        status_code=201 if created else 200,
    )


@router.put("/api/v1/workspaces/order")
async def reorder_workspaces(request: Request) -> JSONResponse:
    session_manager = get_session_manager()
    if session_manager is None:
        raise HTTPException(status_code=503, detail="服务未初始化")
    try:
        body = await request.json()
    except Exception:
        body = {}
    workspace_ids = (body or {}).get("workspace_ids") if isinstance(body, dict) else None
    if not isinstance(workspace_ids, list) or not all(isinstance(item, str) for item in workspace_ids):
        return _error_json_response(400, "缺少有效的 workspace_ids 参数")
    try:
        workspaces = session_manager.reorder_workspaces(workspace_ids)
    except WorkspacePathError as exc:
        return _error_json_response(400, str(exc))
    return JSONResponse(content={"workspaces": workspaces})


@router.patch("/api/v1/workspaces/{workspace_id}")
async def update_workspace(workspace_id: str, request: Request) -> JSONResponse:
    session_manager = get_session_manager()
    if session_manager is None:
        raise HTTPException(status_code=503, detail="服务未初始化")
    try:
        body = await request.json()
    except Exception:
        body = {}
    title = str((body or {}).get("title") or "").strip()
    path = str((body or {}).get("path") or "").strip()
    if not title and not path:
        return _error_json_response(400, "名称不能为空")
    try:
        rec = session_manager.update_workspace(
            workspace_id,
            title=title or None,
            path=path or None,
        )
    except WorkspacePathError as exc:
        return _error_json_response(400, str(exc))
    if rec is None:
        return _error_json_response(404, "工作区不存在")
    return JSONResponse(content={"workspace": rec})


@router.delete("/api/v1/workspaces/{workspace_id}")
async def delete_workspace(workspace_id: str) -> JSONResponse:
    session_manager = get_session_manager()
    if session_manager is None:
        raise HTTPException(status_code=503, detail="服务未初始化")
    try:
        deleted = session_manager.delete_workspace_registration(workspace_id)
    except WorkspacePathError as exc:
        return _error_json_response(400, str(exc))
    if not deleted:
        return _error_json_response(404, "工作区不存在")
    return JSONResponse(content={"status": "ok", "workspace_id": workspace_id})
