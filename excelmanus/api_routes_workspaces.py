"""Registered workspace folders (existing directories only)."""

from __future__ import annotations

from ipaddress import ip_address
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from excelmanus.api_app_state import (
    error_json_response as _error_json_response,
    get_config,
    get_session_manager,
)
from excelmanus.logger import get_logger
from excelmanus.stores.workspace_store import WorkspacePathError
from excelmanus.workspace.folder_picker import FolderPickerError, select_local_folder

logger = get_logger("api.workspaces")

router = APIRouter()


def _is_loopback(host: str | None) -> bool:
    if host == "localhost":
        return True
    try:
        address = ip_address(host or "")
        return address.is_loopback or bool(getattr(address, "ipv4_mapped", None) and address.ipv4_mapped.is_loopback)
    except ValueError:
        return False


@router.post("/api/v1/workspaces/select-folder")
async def select_workspace_folder(request: Request) -> JSONResponse:
    from excelmanus.auth.access import require_browser_header

    require_browser_header(request)
    config = get_config()
    try:
        origin = urlsplit(request.headers.get("origin", ""))
        local_origin = origin.scheme in {"http", "https"} and _is_loopback(origin.hostname)
    except ValueError:
        local_origin = False
    # A reverse proxy can make a remote request look like a loopback peer.
    # Require the browser origin and every forwarded peer to be local as well.
    forwarded = request.headers.get("x-forwarded-for", "")
    if (
        (config is not None and config.is_server)
        or not _is_loopback(request.client.host if request.client else None)
        or not local_origin
        or any(not _is_loopback(host.strip()) for host in forwarded.split(",") if host.strip())
    ):
        return _error_json_response(403, "系统文件夹选择器仅支持在运行 ExcelManus 的电脑上通过 localhost 打开网页；远程访问请填写服务所在电脑的文件夹路径")
    try:
        path = await run_in_threadpool(select_local_folder)
    except FolderPickerError as exc:
        return _error_json_response(exc.status_code, str(exc))
    return JSONResponse(content={"path": path}, headers={"Cache-Control": "no-store"})


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
