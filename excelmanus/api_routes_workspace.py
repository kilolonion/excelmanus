"""工作区备份、staged commit 与 checkpoint API。

从 api.py 抽出的独立路由模块。运行时状态只从 api_app_state 读取，
禁止 ``from excelmanus.api import _config`` 反向导入。
由 api.py 在 create_app 中 include_router 注册。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from excelmanus.api_app_state import (
    get_session_manager,
)
from excelmanus.session import SessionBusyError

router = APIRouter()


class BackupApplyRequest(BaseModel):
    """将备份文件应用回原文件。"""
    session_id: str
    files: list[str] | None = Field(default=None, description="要应用的原始文件路径列表。为空或 null 时应用全部。")


class BackupDiscardRequest(BaseModel):
    """丢弃备份文件。"""
    session_id: str
    files: list[str] | None = Field(default=None, description="要丢弃的原始文件路径列表。为空或 null 时丢弃全部。")


class BackupUndoRequest(BaseModel):
    """撤销已应用的备份。"""
    session_id: str
    original_path: str = Field(description="原始文件路径")
    undo_path: str = Field(description="undo 备份文件路径")


class CheckpointRollbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    turn_number: int


@router.get("/api/v1/backup/list")
async def backup_list(session_id: str, request: Request) -> JSONResponse:
    """列出指定会话的待应用备份文件。"""
    session_manager = get_session_manager()
    if session_manager is None:
        raise HTTPException(status_code=503, detail="服务未初始化")
    engine = session_manager.get_engine(session_id)
    if engine is None:
        return JSONResponse(status_code=200, content={"files": [], "backup_enabled": False})
    tx = engine.transaction
    if tx is None:
        return JSONResponse(status_code=200, content={"files": [], "backup_enabled": False})
    staged = tx.list_staged()
    files = []
    for b in staged:
        bp = Path(b["backup"])
        file_info: dict = {
            "original_path": tx.to_relative(b["original"]),
            "backup_path": tx.to_relative(b["backup"]),
            "exists": b["exists"] == "True",
            "modified_at": bp.stat().st_mtime if bp.exists() else None,
        }
        # 附加轻量变更摘要
        try:
            summary = tx.diff_staged_summary(b["original"])
            if summary:
                file_info["summary"] = summary
        except Exception:
            pass
        files.append(file_info)
    # 检查 agent 是否活跃（用于前端 in-flight 提示）
    in_flight = False
    if session_manager is not None:
        try:
            in_flight = await session_manager.is_session_in_flight(session_id)
        except Exception:
            pass
    return JSONResponse(status_code=200, content={
        "files": files,
        "backup_enabled": True,
        "in_flight": in_flight,
    })


@router.post("/api/v1/backup/apply")
async def backup_apply(request: BackupApplyRequest, raw_request: Request) -> JSONResponse:
    """将备份副本应用回原始文件。"""
    session_manager = get_session_manager()
    if session_manager is None:
        raise HTTPException(status_code=503, detail="服务未初始化")
    # B3: 原子化检查 in_flight + 获取 engine，消除 TOCTOU 竞态
    try:
        engine = await session_manager.get_engine_if_idle(request.session_id)
    except SessionBusyError:
        raise HTTPException(status_code=409, detail="会话正在处理中，请等待完成后再应用备份。")
    if engine is None:
        raise HTTPException(status_code=404, detail=f"会话 '{request.session_id}' 不存在或未加载。")
    tx = engine.transaction
    if tx is None:
        return JSONResponse(status_code=400, content={"detail": "该会话未启用备份模式。"})
    if request.files:
        applied = []
        original_rel_paths: set[str] = set()
        for fp in request.files:
            result = tx.commit_one(fp)
            if result:
                item: dict = {
                    "original": tx.to_relative(result["original"]),
                    "backup": tx.to_relative(result["backup"]),
                }
                if result.get("undo_path"):
                    item["undo_path"] = result["undo_path"]
                applied.append(item)
                original_rel_paths.add(tx.to_relative(result["original"]))
        if original_rel_paths:
            engine._approval.mark_non_undoable_for_paths(original_rel_paths)
        remaining = len(tx.list_staged())
        return JSONResponse(status_code=200, content={
            "status": "ok", "applied": applied, "count": len(applied),
            "pending_count": remaining,
        })
    else:
        raw_applied = tx.commit_all()
        applied = []
        original_rel_paths_all: set[str] = set()
        for a in raw_applied:
            item_all: dict = {
                "original": tx.to_relative(a["original"]),
                "backup": tx.to_relative(a["backup"]),
            }
            if a.get("undo_path"):
                item_all["undo_path"] = a["undo_path"]
            applied.append(item_all)
            original_rel_paths_all.add(tx.to_relative(a["original"]))
        if original_rel_paths_all:
            engine._approval.mark_non_undoable_for_paths(original_rel_paths_all)
        return JSONResponse(status_code=200, content={
            "status": "ok", "applied": applied, "count": len(applied),
            "pending_count": 0,
        })


@router.post("/api/v1/backup/discard")
async def backup_discard(request: BackupDiscardRequest, raw_request: Request) -> JSONResponse:
    """丢弃备份映射。"""
    session_manager = get_session_manager()
    if session_manager is None:
        raise HTTPException(status_code=503, detail="服务未初始化")
    # B3: 原子化检查 in_flight + 获取 engine，消除 TOCTOU 竞态
    try:
        engine = await session_manager.get_engine_if_idle(request.session_id)
    except SessionBusyError:
        return JSONResponse(status_code=400, content={"detail": "会话正在处理中，请等待完成后再丢弃备份。"})
    if engine is None:
        raise HTTPException(status_code=404, detail=f"会话 '{request.session_id}' 不存在或未加载。")
    tx = engine.transaction
    if tx is None:
        return JSONResponse(status_code=400, content={"detail": "该会话未启用备份模式。"})
    if request.files:
        count = 0
        for fp in request.files:
            if tx.rollback_one(fp):
                count += 1
        remaining = len(tx.list_staged())
        return JSONResponse(status_code=200, content={"status": "ok", "discarded": count, "pending_count": remaining})
    else:
        tx.rollback_all()
        return JSONResponse(status_code=200, content={"status": "ok", "discarded": "all", "pending_count": 0})


@router.post("/api/v1/backup/undo")
async def backup_undo(request: BackupUndoRequest, raw_request: Request) -> JSONResponse:
    """撤销已应用的备份，将原始文件恢复到应用前的状态。"""
    session_manager = get_session_manager()
    if session_manager is None:
        raise HTTPException(status_code=503, detail="服务未初始化")
    engine = session_manager.get_engine(request.session_id)
    if engine is None:
        raise HTTPException(status_code=404, detail=f"会话 '{request.session_id}' 不存在或未加载。")
    tx = engine.transaction
    if tx is None:
        return JSONResponse(status_code=400, content={"detail": "该会话未启用备份模式。"})
    ok = tx.undo_commit(request.original_path, request.undo_path)
    if not ok:
        return JSONResponse(status_code=400, content={"detail": "撤销失败：undo 备份文件不存在或已过期。"})
    return JSONResponse(status_code=200, content={"status": "ok", "undone": request.original_path})


# ── 工作区事务别名（规范名称） ────────
# 委托到 backup_* 处理器以保持向后兼容。
# /backup/* 路径保留为废弃别名。

@router.get("/api/v1/workspace/staged")
async def workspace_staged(session_id: str, request: Request) -> JSONResponse:
    return await backup_list(session_id, request)


@router.post("/api/v1/workspace/commit")
async def workspace_commit(request: BackupApplyRequest, raw_request: Request) -> JSONResponse:
    return await backup_apply(request, raw_request)


@router.post("/api/v1/workspace/rollback")
async def workspace_rollback(request: BackupDiscardRequest, raw_request: Request) -> JSONResponse:
    return await backup_discard(request, raw_request)


# ── 轮次 Checkpoint API ──────────────────────────────────


@router.get("/api/v1/checkpoint/list")
async def checkpoint_list(session_id: str, request: Request) -> JSONResponse:
    """列出指定会话的轮次 checkpoint 时间线。"""
    session_manager = get_session_manager()
    if session_manager is None:
        raise HTTPException(status_code=503, detail="服务未初始化")
    engine = session_manager.get_engine(session_id)
    if engine is None:
        return JSONResponse(status_code=404, content={"detail": f"会话 '{session_id}' 不存在或未加载。"})
    if not engine.checkpoint_enabled:
        return JSONResponse(status_code=200, content={
            "checkpoints": [], "checkpoint_enabled": False,
        })
    _reg = engine.file_registry
    if _reg is None or not getattr(_reg, 'has_versions', False):
        return JSONResponse(status_code=200, content={
            "checkpoints": [], "checkpoint_enabled": True,
            "error": "FileRegistry not available",
        })
    cps = _reg.list_turn_checkpoints()
    items = []
    for cp in cps:
        items.append({
            "turn_number": cp.turn_number,
            "created_at": cp.created_at,
            "files_modified": cp.files_modified,
            "tool_names": cp.tool_names,
            "version_count": len(cp.version_ids),
        })
    return JSONResponse(status_code=200, content={
        "checkpoints": items, "checkpoint_enabled": True,
    })


@router.post("/api/v1/checkpoint/rollback")
async def checkpoint_rollback(
    request: CheckpointRollbackRequest, raw_request: Request,
) -> JSONResponse:
    """回退到指定轮次之前的文件状态。"""
    session_manager = get_session_manager()
    if session_manager is None:
        raise HTTPException(status_code=503, detail="服务未初始化")
    # B3: 原子化检查 in_flight + 获取 engine，消除 TOCTOU 竞态
    try:
        engine = await session_manager.get_engine_if_idle(request.session_id)
    except SessionBusyError:
        return JSONResponse(status_code=409, content={"detail": "会话正在处理中，请等待完成后再回退。"})
    if engine is None:
        raise HTTPException(status_code=404, detail=f"会话 '{request.session_id}' 不存在或未加载。")
    if not engine.checkpoint_enabled:
        return JSONResponse(status_code=400, content={"detail": "该会话未启用 checkpoint 模式。"})
    _reg = engine.file_registry
    if _reg is None or not getattr(_reg, 'has_versions', False):
        return JSONResponse(status_code=400, content={"detail": "FileRegistry not available for rollback."})
    restored = _reg.rollback_to_turn(request.turn_number)
    return JSONResponse(status_code=200, content={
        "status": "ok",
        "turn_number": request.turn_number,
        "restored_files": restored,
        "count": len(restored),
    })
