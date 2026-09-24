"""版本、备份与更新管理 API 路由。

网页升级通过独立 helper 停机后更新；服务器需显式启用并认证。
"""

from __future__ import annotations

import asyncio
import json as _json
import os
import subprocess
import time
import uuid
from functools import partial
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from excelmanus.logger import get_logger

logger = get_logger("api.version")

router = APIRouter()

_API_SCHEMA_VERSION = 1


def _get_project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _error(status: int, msg: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": msg})


def _is_loopback(request: Request) -> bool:
    host = (request.client.host if request.client else "") or ""
    return host in {"127.0.0.1", "::1", "localhost", "::ffff:127.0.0.1"}


def _require_control_plane(request: Request) -> JSONResponse | None:
    """破坏性操作：server 模式拒绝；非 loopback 拒绝。"""
    from excelmanus.api_app_state import get_config

    if os.environ.get("EXCELMANUS_DESKTOP") == "1":
        return _error(409, "桌面版不支持源码更新或停机恢复，请退出后安装新版 App；用户数据会保留。")
    cfg = get_config()
    if cfg is not None and cfg.is_server:
        return _error(403, "服务器部署请在运维机运行 deploy.sh，不能从生产 API 升级或远程部署。")
    if not _is_loopback(request):
        return _error(403, "升级、恢复与远程部署仅允许本机请求")
    return None


def _web_upgrade_denial(request: Request) -> JSONResponse | None:
    """Only this upgrade action can be enabled on a managed server.

    Restore/deployment endpoints retain their separate control-plane policy.
    """
    from excelmanus.api_app_state import get_config
    from excelmanus.auth.access import access_enabled, authenticated
    from excelmanus.upgrade.runtime import read_runtime

    if os.environ.get("EXCELMANUS_DESKTOP") == "1":
        return _error(409, "桌面版请使用安装包更新")
    cfg = get_config()
    remote = (cfg is not None and cfg.is_server) or not _is_loopback(request)
    if remote:
        if os.environ.get("EXCELMANUS_WEB_UPGRADE_ENABLED") != "1":
            return _error(403, "此服务器尚未启用网页更新，请由管理员设置 EXCELMANUS_WEB_UPGRADE_ENABLED=1")
        if not access_enabled() or not authenticated(request):
            return _error(403, "网页更新需要启用登录保护并通过管理员认证")
    elif access_enabled() and not authenticated(request):
        return _error(403, "请先通过管理员认证")
    from excelmanus.updater import _is_git_repo
    root = _get_project_root().resolve()
    if not _is_git_repo(root):
        return _error(409, "此安装没有 Git 源码，请使用安装包或部署工具更新")
    runtime = read_runtime() or {}
    try:
        managed_root = Path(runtime.get("project_root") or "").resolve()
        workers = int(runtime.get("workers") or 1)
    except (TypeError, ValueError, OSError):
        return _error(409, "启动记录无效，请通过 deploy/start.sh 或 start.ps1 重新启动")
    if not runtime.get("project_root") or managed_root != root:
        return _error(409, "请通过 deploy/start.sh 或 start.ps1 启动此实例，以便更新后自动恢复服务")
    if workers != 1:
        return _error(409, "多工作进程部署请使用运维发布流程更新")
    if runtime.get("backend_only") or runtime.get("frontend_only"):
        return _error(409, "前后端独立部署请使用运维发布流程，网页会自动提示已发布的新版本")
    from excelmanus.upgrade.helper import _start_command
    try:
        _start_command(runtime, root)
    except (TypeError, ValueError, OSError):
        return _error(409, "未找到可用的服务启动脚本，暂不能从网页更新")
    return None


@router.get("/api/v1/version/upgrade/status")
async def version_upgrade_status(request: Request) -> JSONResponse:
    from excelmanus.upgrade.runtime import read_upgrade_status
    return JSONResponse(content=read_upgrade_status() or {}, headers={"Cache-Control": "no-store"})


@router.get("/api/v1/version/upgrade/capability")
async def version_upgrade_capability(request: Request) -> JSONResponse:
    denied = _web_upgrade_denial(request)
    reason = _json.loads(denied.body).get("error") if denied else None
    return JSONResponse(content={"supported": denied is None, "reason": reason}, headers={"Cache-Control": "no-store"})


@router.get("/api/v1/version/upgrade/check")
async def version_upgrade_check(request: Request) -> JSONResponse:
    denied = _web_upgrade_denial(request)
    if denied:
        return denied
    from excelmanus.updater import check_for_updates

    root = _get_project_root()
    info = await asyncio.get_running_loop().run_in_executor(
        None, partial(check_for_updates, root, force=True),
    )
    return JSONResponse(content={
        "current": info.current,
        "latest": info.latest,
        "has_update": info.has_update,
        "commits_behind": info.commits_behind,
        "check_failed": info.check_failed,
        "downgrade_blocked": info.downgrade_blocked,
        "error": info.error or None,
    }, headers={"Cache-Control": "no-store"})


_GIT_COMMIT_UNSET = object()
_git_commit_cache: object = _GIT_COMMIT_UNSET


def reset_git_commit_cache() -> None:
    """测试用：清空 git commit 进程缓存。"""
    global _git_commit_cache
    _git_commit_cache = _GIT_COMMIT_UNSET


def _get_git_commit(root: Path) -> str | None:
    global _git_commit_cache
    if os.environ.get("EXCELMANUS_DESKTOP") == "1":
        return None
    if _git_commit_cache is not _GIT_COMMIT_UNSET:
        return _git_commit_cache  # type: ignore[return-value]
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(root), capture_output=True, text=True, timeout=5,
        )
        value = r.stdout.strip() or None if r.returncode == 0 else None
    except Exception:
        value = None
    _git_commit_cache = value
    return value


def _get_frontend_build_id(root: Path, deploy_meta: dict | None = None) -> str | None:
    build_id_file = root / "web" / ".next" / "BUILD_ID"
    try:
        if build_id_file.is_file():
            val = build_id_file.read_text(encoding="utf-8").strip()
            if val:
                return val
    except Exception:
        pass
    if deploy_meta:
        meta_bid = deploy_meta.get("frontend_build_id")
        if meta_bid:
            return str(meta_bid)
    return None


def _get_deploy_meta(root: Path) -> dict:
    meta_file = root / ".deploy_meta.json"
    try:
        if meta_file.is_file():
            data = _json.loads(meta_file.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


def get_manifest_data() -> dict:
    """版本指纹与上次升级结果。禁止在此开数据库（health 每次轮询会走到这里）。"""
    import excelmanus

    root = _get_project_root()
    meta = _get_deploy_meta(root)
    git_commit = _get_git_commit(root)
    build_id = _get_frontend_build_id(root, deploy_meta=meta)

    fingerprint_parts = [excelmanus.__version__]
    if build_id:
        fingerprint_parts.append(build_id)
    if git_commit:
        fingerprint_parts.append(git_commit)

    from excelmanus.api_app_state import get_config
    cfg = get_config()
    live_mode = cfg.deploy_mode if cfg is not None else None

    last_upgrade = None
    try:
        from excelmanus.upgrade.runtime import read_upgrade_status
        last_upgrade = read_upgrade_status()
    except Exception:
        last_upgrade = None

    return {
        "release_id": meta.get("release_id") or git_commit or "unknown",
        "backend_version": excelmanus.__version__,
        "api_schema_version": _API_SCHEMA_VERSION,
        "frontend_build_id": build_id,
        "version_fingerprint": "|".join(fingerprint_parts),
        "git_commit": git_commit,
        "deployed_at": meta.get("deployed_at"),
        "deploy_mode": live_mode or meta.get("deploy_mode"),
        "topology": meta.get("topology"),
        "last_upgrade": last_upgrade,
    }


def _exit_after_response() -> None:
    time.sleep(0.8)
    os._exit(0)


def _schedule_helper_and_exit(root: Path, request_payload: dict) -> JSONResponse:
    from excelmanus.api_app_state import set_draining, set_restart_reason
    from excelmanus.upgrade.helper import spawn_detached_helper
    from excelmanus.upgrade.runtime import reserve_request, clear_request, write_upgrade_status

    request_id = uuid.uuid4().hex
    payload = {**request_payload, "request_id": request_id}
    try:
        reserve_request(payload)
    except FileExistsError:
        return _error(409, "已有更新或恢复请求正在处理，请等待完成")
    try:
        write_upgrade_status({"request_id": request_id, "action": payload["action"], "ok": None, "phase": "准备更新", "progress": 0})
        spawn_detached_helper(root)
    except Exception as exc:
        clear_request()
        write_upgrade_status({"request_id": request_id, "action": payload["action"], "ok": False, "error": f"无法启动更新进程: {exc}"})
        return _error(500, "无法启动更新进程，当前服务保持运行，请查看日志后重试")
    set_restart_reason("正在停机升级")
    set_draining(True)
    return JSONResponse(
        status_code=202,
        content={"accepted": True, "request_id": request_id, "message": "已开始网页更新，完成后将自动恢复连接。"},
        background=BackgroundTask(_exit_after_response),
    )


@router.get("/api/v1/version/manifest")
async def version_manifest(request: Request) -> JSONResponse:
    return JSONResponse(content=get_manifest_data())


@router.get("/api/v1/version/check")
async def version_check(request: Request) -> JSONResponse:
    from excelmanus.release_check import check_release_updates
    from excelmanus.updater import get_current_version

    root = _get_project_root()
    if os.environ.get("EXCELMANUS_DESKTOP") == "1":
        import excelmanus
        return JSONResponse(content={
            "current": excelmanus.__version__, "latest": None, "has_update": False,
            "commits_behind": 0, "release_notes": "请通过新版安装包更新桌面 App，用户数据会保留。",
            "check_method": "desktop_installer", "check_failed": False, "error": None,
        })
    current = get_current_version(root)
    force = request.query_params.get("force", "") == "1"
    try:
        loop = asyncio.get_running_loop()
        info = await loop.run_in_executor(
            None, partial(check_release_updates, root, force=force),
        )
        return JSONResponse(content={
            "current": current,
            "latest": info.latest,
            "has_update": info.has_update,
            "commits_behind": info.commits_behind,
            "release_notes": info.release_notes,
            "release_url": info.release_url,
            "check_method": info.check_method,
            "check_failed": info.check_failed,
            "error": info.error or None,
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
            "check_failed": True,
            "error": str(e),
        })


@router.get("/api/v1/version/backups")
async def list_version_backups(request: Request) -> JSONResponse:
    from excelmanus.updater import list_backups

    backups = list_backups(_get_project_root())
    return JSONResponse(content={"backups": backups})


class DeleteBackupRequest(BaseModel):
    backup_name: str = Field(description="备份目录名称")


@router.post("/api/v1/version/backups/delete")
async def delete_version_backup(body: DeleteBackupRequest, request: Request) -> JSONResponse:
    denied = _require_control_plane(request)
    if denied:
        return denied

    from excelmanus.updater import find_backup_dir

    backup_dir = find_backup_dir(body.backup_name, _get_project_root())
    if backup_dir is None:
        return _error(404, f"备份不存在: {body.backup_name}")
    try:
        import shutil
        shutil.rmtree(str(backup_dir))
        return JSONResponse(content={"status": "ok", "deleted": body.backup_name})
    except Exception as e:
        return _error(500, f"删除失败: {e}")


@router.post("/api/v1/version/backups/cleanup")
async def cleanup_version_backups(request: Request) -> JSONResponse:
    denied = _require_control_plane(request)
    if denied:
        return denied

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


@router.get("/api/v1/version/installations")
async def list_installations(request: Request) -> JSONResponse:
    from excelmanus.data_home import discover_old_installations

    current_path = _get_project_root().resolve()
    installations = discover_old_installations(current_path)
    return JSONResponse(content={
        "installations": installations,
        "current_path": str(current_path),
        "stale_count": sum(1 for item in installations if item.get("status") == "missing"),
    }, headers={"Cache-Control": "no-store"})


class DeleteInstallationRequest(BaseModel):
    path: str = Field(description="安装路径")
    delete_directory: bool = Field(
        default=False,
        description="是否同时删除已登记的旧安装目录",
    )


@router.post("/api/v1/version/installations/delete")
async def delete_installation(body: DeleteInstallationRequest, request: Request) -> JSONResponse:
    denied = _require_control_plane(request)
    if denied:
        return denied

    from excelmanus.data_home import (
        InstallationDeletionError,
        _installation_path_key,
        _load_installations,
        remove_installation,
    )

    current_path = _get_project_root().resolve()
    if _installation_path_key(body.path) == _installation_path_key(current_path):
        return _error(409, "不能移除当前正在运行的安装记录")

    try:
        removed = remove_installation(
            body.path,
            delete_directory=body.delete_directory,
            protected_paths=(current_path,),
        )
    except InstallationDeletionError as exc:
        return _error(409, str(exc))
    if removed is None:
        return _error(404, f"未找到安装记录: {body.path}")
    remaining = len(_load_installations())
    directory_deleted = bool(removed.get("directory_deleted", False))
    return JSONResponse(content={
        "status": "ok",
        "deleted_path": removed.get("path", body.path),
        "remaining": remaining,
        "directory_deleted": directory_deleted,
    })


@router.post("/api/v1/version/installations/cleanup")
async def cleanup_installations(request: Request) -> JSONResponse:
    """清理已不存在的旧安装记录，不删除任何磁盘目录。"""
    denied = _require_control_plane(request)
    if denied:
        return denied

    from excelmanus.data_home import prune_missing_installations

    removed = prune_missing_installations(_get_project_root().resolve())
    return JSONResponse(content={
        "status": "ok",
        "removed_count": len(removed),
        "removed": [entry.get("path", "") for entry in removed],
        "directory_deleted": False,
    })


class UpgradeRequest(BaseModel):
    skip_backup: bool = Field(default=False)
    skip_deps: bool = Field(default=False)
    use_mirror: bool = Field(default=False)


@router.post("/api/v1/version/upgrade")
async def version_upgrade(body: UpgradeRequest, request: Request) -> JSONResponse:
    denied = _web_upgrade_denial(request)
    if denied:
        return denied
    from excelmanus.auth.access import require_browser_header
    from excelmanus.api_app_state import get_runtime
    require_browser_header(request)
    runtime = get_runtime()
    if runtime.draining or any(not task.done() for task in runtime.active_chat_tasks.values()):
        return _error(409, "还有任务正在运行或服务正在重启，请稍后再更新")
    manager = runtime.session_manager
    if manager is not None:
        sessions = await manager.list_sessions()
        if any(session.get("in_flight") for session in sessions):
            return _error(409, "还有任务正在运行，请等待完成后再更新")
    from excelmanus.upgrade.preflight import check_upgrade_environment
    root = _get_project_root()
    environment_error = await asyncio.get_running_loop().run_in_executor(None, check_upgrade_environment, root)
    if environment_error:
        return _error(409, environment_error)
    from excelmanus.updater import check_for_updates
    info = await asyncio.get_running_loop().run_in_executor(
        None, partial(check_for_updates, root, force=True),
    )
    if info.check_failed or info.downgrade_blocked:
        return _error(409, info.error or "无法确认源码分支更新，当前服务未停止")
    if not info.has_update:
        return _error(409, "当前源码分支没有可更新的新提交，当前服务未停止")
    # No await from this point through reservation/draining: other chat requests
    # on this single-worker event loop cannot enter between the final checks.
    if runtime.draining or any(not task.done() for task in runtime.active_chat_tasks.values()):
        return _error(409, "还有任务正在运行，请等待完成后再更新")

    return _schedule_helper_and_exit(root, {
        "action": "upgrade",
        "skip_backup": False,
        "skip_deps": body.skip_deps,
        "use_mirror": body.use_mirror,
    })


class RestoreBackupRequest(BaseModel):
    backup_name: str = Field(description="备份目录名称")


@router.post("/api/v1/version/backups/restore")
async def version_restore_backup(body: RestoreBackupRequest, request: Request) -> JSONResponse:
    denied = _require_control_plane(request)
    if denied:
        return denied

    from excelmanus.updater import find_backup_dir

    if find_backup_dir(body.backup_name, _get_project_root()) is None:
        return _error(404, f"备份不存在: {body.backup_name}")
    return _schedule_helper_and_exit(_get_project_root(), {
        "action": "restore",
        "backup_name": body.backup_name,
    })


@router.post("/api/v1/version/data/migrate")
async def version_migrate_data(request: Request) -> JSONResponse:
    denied = _require_control_plane(request)
    if denied:
        return denied

    from excelmanus.data_home import migrate_data_from_project

    body: dict = {}
    try:
        body = await request.json()
    except Exception:
        pass
    source = body.get("source", "") or str(_get_project_root())
    source_path = Path(source).expanduser().resolve()
    root = _get_project_root().resolve()
    home = Path.home().resolve()
    if root not in source_path.parents and source_path != root:
        if home not in source_path.parents and source_path != home:
            return _error(400, "迁移源路径必须位于项目根或用户主目录下")

    loop = asyncio.get_running_loop()
    stats = await loop.run_in_executor(None, migrate_data_from_project, str(source_path))
    return JSONResponse(content={"status": "ok", "migrated": stats})


@router.get("/api/v1/deploy/status")
async def deploy_status(request: Request) -> JSONResponse:
    from excelmanus.updater import get_deploy_status

    loop = asyncio.get_running_loop()
    status = await loop.run_in_executor(None, get_deploy_status, _get_project_root())
    return JSONResponse(content=status)


@router.post("/api/v1/deploy/build")
async def deploy_build_artifact(request: Request) -> JSONResponse:
    denied = _require_control_plane(request)
    if denied:
        return denied
    from excelmanus.updater import build_frontend_artifact

    root = _get_project_root()
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, build_frontend_artifact, root)
    return JSONResponse(
        status_code=200 if result.success else 500,
        content={
            "success": result.success,
            "version": result.version,
            "artifact_path": result.artifact_path,
            "steps_completed": result.steps_completed,
            "error": result.error,
        },
    )


class DeployExecuteRequest(BaseModel):
    target: str = Field(default="full")
    skip_build: bool = Field(default=False)
    artifact_path: str = Field(default="")
    from_local: bool = Field(default=True)
    skip_deps: bool = Field(default=False)


@router.post("/api/v1/deploy/execute")
async def deploy_execute(body: DeployExecuteRequest, request: Request) -> JSONResponse:
    denied = _require_control_plane(request)
    if denied:
        return denied
    from excelmanus.updater import DeployConfig, perform_remote_deploy

    root = _get_project_root()
    if not (root / "deploy" / ".env.deploy").is_file():
        return _error(400, "未找到 deploy/.env.deploy")
    config = DeployConfig(
        target=body.target,
        skip_build=body.skip_build,
        artifact_path=body.artifact_path,
        from_local=body.from_local,
        skip_deps=body.skip_deps,
    )
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(
        None, lambda: perform_remote_deploy(root, config=config),
    )
    return JSONResponse(
        status_code=200 if result.success else 500,
        content={
            "success": result.success,
            "version": result.version,
            "artifact_path": result.artifact_path,
            "steps_completed": result.steps_completed,
            "deploy_output": result.deploy_output,
            "error": result.error,
        },
    )


@router.get("/api/v1/deploy/history")
async def deploy_history(request: Request) -> JSONResponse:
    from excelmanus.updater import get_deploy_history_structured

    history = get_deploy_history_structured(_get_project_root())
    return JSONResponse(content={"history": history})


class RollbackRequest(BaseModel):
    target: str = Field(default="full")
    release_id: str = Field(default="")
    commit: str = Field(default="")
    skip_deps: bool = Field(default=False)


@router.post("/api/v1/deploy/rollback")
async def deploy_rollback(body: RollbackRequest, request: Request) -> JSONResponse:
    denied = _require_control_plane(request)
    if denied:
        return denied
    from excelmanus.updater import perform_remote_rollback

    root = _get_project_root()
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(
        None,
        lambda: perform_remote_rollback(
            root,
            target=body.target,
            release_id=body.release_id,
            commit=body.commit,
            skip_deps=body.skip_deps,
        ),
    )
    status_code = 200 if result.get("success") else 500
    return JSONResponse(status_code=status_code, content=result)


@router.get("/api/v1/deploy/lock/status")
async def deploy_lock_status(request: Request) -> JSONResponse:
    from excelmanus.updater import _deploy_lock, check_remote_deploy_lock

    root = _get_project_root()
    loop = asyncio.get_running_loop()
    remote_lock = await loop.run_in_executor(None, check_remote_deploy_lock, root)
    return JSONResponse(content={
        "local_locked": _deploy_lock.locked(),
        "remote": remote_lock,
    })


@router.get("/api/v1/deploy/history/{release_id}/log")
async def deploy_history_log(release_id: str, request: Request) -> JSONResponse:
    from excelmanus.updater import get_deploy_log

    log_content = get_deploy_log(_get_project_root(), release_id)
    return JSONResponse(content={"release_id": release_id, "log": log_content})
