"""版本与备份管理 API 路由。

提供版本检查、备份列表/删除、安装注册表管理等端点。
由 api.py 在 lifespan 完成后通过 include_router 注册。
"""

from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from excelmanus.logger import get_logger

logger = get_logger("api.version")

router = APIRouter()


# ── 辅助函数 ──────────────────────────────────────────


def _get_project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _error(status: int, msg: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": msg})


def _is_admin_or_noauth(request: Request) -> bool:
    """管理员或无认证模式下允许操作。"""
    from excelmanus.api import _config
    if _config is None or not _config.auth_enabled:
        return True
    user = getattr(request.state, "user", None)
    if user and getattr(user, "role", None) == "admin":
        return True
    return False


# ── 版本检查 ──────────────────────────────────────────


@router.get("/api/v1/version/check")
async def version_check(request: Request) -> JSONResponse:
    """检查当前版本与可用更新。"""
    from excelmanus.updater import check_for_updates, get_current_version

    root = _get_project_root()
    current = get_current_version(root)
    try:
        info = check_for_updates(root)
        return JSONResponse(content={
            "current": current,
            "latest": info.latest,
            "has_update": info.has_update,
            "commits_behind": info.commits_behind,
            "release_notes": info.release_notes,
            "check_method": info.check_method,
        })
    except Exception as e:
        logger.warning("版本检查失败: %s", e)
        return JSONResponse(content={
            "current": current,
            "latest": current,
            "has_update": False,
            "commits_behind": 0,
            "release_notes": "",
            "check_method": "error",
            "error": str(e),
        })


# ── 备份管理 ──────────────────────────────────────────


@router.get("/api/v1/version/backups")
async def list_version_backups(request: Request) -> JSONResponse:
    """列出所有更新备份。"""
    from excelmanus.updater import list_backups

    backups = list_backups(_get_project_root())
    return JSONResponse(content={"backups": backups})


class DeleteBackupRequest(BaseModel):
    backup_name: str = Field(description="备份目录名称，如 backup_1.6.6_20260301_162242")


@router.post("/api/v1/version/backups/delete")
async def delete_version_backup(body: DeleteBackupRequest, request: Request) -> JSONResponse:
    """删除指定更新备份。"""
    if not _is_admin_or_noauth(request):
        return _error(403, "需要管理员权限")

    root = _get_project_root()
    name = body.backup_name
    # 安全检查：名称必须以 backup_ 开头且无路径穿越
    if not name.startswith("backup_") or "/" in name or "\\" in name or ".." in name:
        return _error(400, f"非法备份名称: {name}")

    backup_dir = root / "backups" / name
    if not backup_dir.is_dir():
        return _error(404, f"备份不存在: {name}")

    try:
        shutil.rmtree(str(backup_dir))
        logger.info("已删除备份: %s", backup_dir)
        return JSONResponse(content={"status": "ok", "deleted": name})
    except Exception as e:
        return _error(500, f"删除失败: {e}")


@router.post("/api/v1/version/backups/cleanup")
async def cleanup_version_backups(request: Request) -> JSONResponse:
    """清理旧备份，保留最近 N 个。"""
    if not _is_admin_or_noauth(request):
        return _error(403, "需要管理员权限")

    from excelmanus.updater import cleanup_old_backups

    max_keep = 2
    try:
        body = await request.json()
        max_keep = int(body.get("max_keep", 2))
    except Exception:
        pass

    removed = cleanup_old_backups(_get_project_root(), max_keep=max(1, max_keep))
    return JSONResponse(content={
        "status": "ok",
        "removed_count": len(removed),
        "removed": [Path(r).name for r in removed],
    })


# ── 安装注册表 ────────────────────────────────────────


@router.get("/api/v1/version/installations")
async def list_installations(request: Request) -> JSONResponse:
    """列出所有已注册的安装记录。"""
    from excelmanus.data_home import discover_old_installations

    installations = discover_old_installations()
    return JSONResponse(content={"installations": installations})


class DeleteInstallationRequest(BaseModel):
    path: str = Field(description="安装路径")


@router.post("/api/v1/version/installations/delete")
async def delete_installation(body: DeleteInstallationRequest, request: Request) -> JSONResponse:
    """从注册表中移除指定安装记录。"""
    if not _is_admin_or_noauth(request):
        return _error(403, "需要管理员权限")

    from excelmanus.data_home import _load_installations, _save_installations

    installations = _load_installations()
    before = len(installations)
    installations = [i for i in installations if i.get("path") != body.path]
    if len(installations) == before:
        return _error(404, f"未找到安装记录: {body.path}")

    _save_installations(installations)
    return JSONResponse(content={"status": "ok", "remaining": len(installations)})
