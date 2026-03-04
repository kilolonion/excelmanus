"""版本、备份与更新管理 API 路由。

提供版本检查、备份列表/删除、执行更新、恢复备份、
安装注册表管理、数据迁移等端点。
由 api.py 在 lifespan 完成后通过 include_router 注册。
"""

from __future__ import annotations

import asyncio
import json as _json
import shutil
import subprocess
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from excelmanus.logger import get_logger

logger = get_logger("api.version")

router = APIRouter()

# 递增此常量以表示 API schema 不兼容变更（仅在破坏性变更时递增）
_API_SCHEMA_VERSION = 1

# 最低兼容前端 build ID（None = 不约束；设置后，低于此值的前端将被提示升级）
_MIN_FRONTEND_BUILD_ID: str | None = None
# 最低兼容后端版本（None = 不约束；设置后，低于此值的后端将被前端提示不兼容）
_MIN_BACKEND_VERSION: str | None = None


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


def _get_git_commit(root: Path) -> str | None:
    """读取当前 git short commit hash，失败返回 None。"""
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(root), capture_output=True, text=True, timeout=5,
        )
        return r.stdout.strip() or None if r.returncode == 0 else None
    except Exception:
        return None


def _get_frontend_build_id(root: Path, deploy_meta: dict | None = None) -> str | None:
    """读取前端 build 指纹，支持 split topology 回退链。

    优先级：
      1. web/.next/BUILD_ID（同机部署）
      2. .deploy_meta.json 中的 frontend_build_id（远程部署写入）
      3. None（调用方可继续回退到 git_commit）
    """
    build_id_file = root / "web" / ".next" / "BUILD_ID"
    try:
        if build_id_file.is_file():
            val = build_id_file.read_text(encoding="utf-8").strip()
            if val:
                return val
    except Exception:
        pass
    # 回退: deploy_meta 中可能记录了远程构建的 build_id
    if deploy_meta:
        meta_bid = deploy_meta.get("frontend_build_id")
        if meta_bid:
            return str(meta_bid)
    return None


def _get_deploy_meta(root: Path) -> dict:
    """读取 .deploy_meta.json，不存在或解析失败返回空 dict。"""
    meta_file = root / ".deploy_meta.json"
    try:
        if meta_file.is_file():
            return _json.loads(meta_file.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _check_db_migration_needed(root: Path) -> bool:
    """检查数据库是否需要 schema 迁移。"""
    try:
        from excelmanus.updater import verify_database_migration
        ok, msg = verify_database_migration(root)
        return ok and "待迁移" in msg
    except Exception:
        return False


def get_manifest_data() -> dict:
    """构建并返回发布清单数据（供 manifest 端点和 health 端点共用）。"""
    import excelmanus

    root = _get_project_root()
    meta = _get_deploy_meta(root)
    git_commit = _get_git_commit(root)
    build_id = _get_frontend_build_id(root, deploy_meta=meta)

    # version_fingerprint: 组合指纹，确保 split topology 下也有可比较的值
    fingerprint_parts = [excelmanus.__version__]
    if build_id:
        fingerprint_parts.append(build_id)
    elif git_commit:
        fingerprint_parts.append(git_commit)
    version_fingerprint = "|".join(fingerprint_parts)

    return {
        "release_id": meta.get("release_id") or git_commit or "unknown",
        "backend_version": excelmanus.__version__,
        "api_schema_version": _API_SCHEMA_VERSION,
        "frontend_build_id": build_id,
        "version_fingerprint": version_fingerprint,
        "git_commit": git_commit,
        "deployed_at": meta.get("deployed_at"),
        "deploy_mode": meta.get("deploy_mode"),
        "topology": meta.get("topology"),
        "requires_db_migration": _check_db_migration_needed(root),
        "min_frontend_build_id": _MIN_FRONTEND_BUILD_ID or meta.get("min_frontend_build_id"),
        "min_backend_version": _MIN_BACKEND_VERSION or meta.get("min_backend_version"),
    }


# ── 发布清单（Manifest） ─────────────────────────────


@router.get("/api/v1/version/manifest")
async def version_manifest(request: Request) -> JSONResponse:
    """返回当前实例的发布元数据清单，供前端版本兼容校验使用。"""
    return JSONResponse(content=get_manifest_data())


# ── 版本检查 ──────────────────────────────────────────


@router.get("/api/v1/version/check")
async def version_check(request: Request) -> JSONResponse:
    """检查当前版本与可用更新。

    Query params:
        force: 设为 1 跳过 TTL 缓存强制刷新（用户手动点击"检查更新"时使用）。
    """
    import asyncio
    from functools import partial

    from excelmanus.updater import check_for_updates, get_current_version

    root = _get_project_root()
    current = get_current_version(root)
    force = request.query_params.get("force", "") == "1"
    try:
        loop = asyncio.get_running_loop()
        info = await loop.run_in_executor(
            None, partial(check_for_updates, root, force=force),
        )
        return JSONResponse(content={
            "current": current,
            "latest": info.latest,
            "has_update": info.has_update,
            "commits_behind": info.commits_behind,
            "release_notes": info.release_notes,
            "check_method": info.check_method,
            "check_failed": info.check_failed,
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


# ── 执行更新 ──────────────────────────────────────────


class UpdateApplyRequest(BaseModel):
    skip_backup: bool = Field(default=False, description="跳过数据备份")
    skip_deps: bool = Field(default=False, description="跳过依赖重装")
    use_mirror: bool = Field(default=False, description="使用国内镜像")


@router.post("/api/v1/version/update/apply")
async def version_update_apply(body: UpdateApplyRequest, request: Request) -> JSONResponse:
    """执行更新（仅管理员）。"""
    if not _is_admin_or_noauth(request):
        return _error(403, "需要管理员权限")

    # 部署模式校验
    from excelmanus.api import _config
    if _config is not None:
        if _config.is_docker:
            return _error(
                400,
                "容器化部署无法执行本地更新，请重新拉取镜像并重建容器。",
            )

    import asyncio
    from excelmanus.updater import perform_update

    root = _get_project_root()
    loop = asyncio.get_running_loop()

    result = await loop.run_in_executor(
        None,
        lambda: perform_update(
            root,
            skip_backup=body.skip_backup,
            skip_deps=body.skip_deps,
            use_mirror=body.use_mirror,
        ),
    )

    status_code = 200 if result.success else 500
    return JSONResponse(
        status_code=status_code,
        content={
            "success": result.success,
            "old_version": result.old_version,
            "new_version": result.new_version,
            "backup_dir": result.backup_dir,
            "steps_completed": result.steps_completed,
            "error": result.error,
            "needs_restart": result.needs_restart,
        },
    )


@router.post("/api/v1/version/update/stream")
async def version_update_stream(body: UpdateApplyRequest, request: Request) -> StreamingResponse:
    """以 SSE 流式推送更新进度，最终发送 done 或 error 事件。

    事件格式：
        event: progress
        data: {"message": "...", "percent": 50}

        event: done
        data: {"success": true, "old_version": "...", "new_version": "...", ...}

        event: error
        data: {"error": "..."}
    """
    if not _is_admin_or_noauth(request):
        async def _forbidden():
            yield f"event: error\ndata: {_json.dumps({'error': '需要管理员权限'})}\n\n"
        return StreamingResponse(_forbidden(), media_type="text/event-stream", status_code=403)

    from excelmanus.updater import perform_update

    root = _get_project_root()
    queue: asyncio.Queue[dict | None] = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def _progress_cb(msg: str, pct: int) -> None:
        """在 executor 线程中调用，安全地投递到 asyncio Queue。"""
        asyncio.run_coroutine_threadsafe(
            queue.put({"event": "progress", "message": msg, "percent": pct}),
            loop,
        )

    async def _run_update() -> None:
        try:
            result = await loop.run_in_executor(
                None,
                lambda: perform_update(
                    root,
                    skip_backup=body.skip_backup,
                    skip_deps=body.skip_deps,
                    use_mirror=body.use_mirror,
                    progress_cb=_progress_cb,
                ),
            )
            await queue.put({
                "event": "done",
                "success": result.success,
                "old_version": result.old_version,
                "new_version": result.new_version,
                "backup_dir": result.backup_dir,
                "steps_completed": result.steps_completed,
                "error": result.error,
                "needs_restart": result.needs_restart,
            })
        except Exception as exc:
            logger.error("流式更新异常: %s", exc, exc_info=True)
            await queue.put({"event": "error", "error": str(exc)})
        finally:
            await queue.put(None)  # sentinel

    asyncio.create_task(_run_update())

    async def _event_generator():
        while True:
            item = await queue.get()
            if item is None:
                break
            event_type = item.pop("event", "progress")
            yield f"event: {event_type}\ndata: {_json.dumps(item, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        _event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── 恢复备份 ──────────────────────────────────────────


class RestoreBackupRequest(BaseModel):
    backup_name: str = Field(description="备份目录名称，如 backup_1.6.6_20260301_120000")


@router.post("/api/v1/version/backups/restore")
async def version_restore_backup(body: RestoreBackupRequest, request: Request) -> JSONResponse:
    """从指定备份恢复数据（仅管理员）。"""
    if not _is_admin_or_noauth(request):
        return _error(403, "需要管理员权限")

    import asyncio
    from excelmanus.updater import restore_from_backup

    name = body.backup_name
    if not name:
        return _error(400, "缺少 backup_name 参数")

    # 安全检查
    if not name.startswith("backup_") or "/" in name or "\\" in name or ".." in name:
        return _error(400, f"非法备份名称: {name}")

    root = _get_project_root()
    backup_dir = root / "backups" / name
    if not backup_dir.is_dir():
        return _error(404, f"备份不存在: {name}")

    loop = asyncio.get_running_loop()
    ok = await loop.run_in_executor(
        None, restore_from_backup, str(backup_dir), str(root),
    )

    if ok:
        return JSONResponse(content={"status": "ok", "message": "数据已从备份恢复，请重启服务。"})
    return _error(500, "恢复失败，请查看服务日志")


# ── 数据迁移 ──────────────────────────────────────────


class MigrateDataRequest(BaseModel):
    source: str = Field(default="", description="源安装目录路径（为空则使用当前项目）")


@router.post("/api/v1/version/data/migrate")
async def version_migrate_data(request: Request) -> JSONResponse:
    """从指定旧安装目录迁移数据到集中位置（仅管理员）。"""
    if not _is_admin_or_noauth(request):
        return _error(403, "需要管理员权限")

    import asyncio
    from excelmanus.data_home import migrate_data_from_project

    body: dict = {}
    try:
        body = await request.json()
    except Exception:
        pass

    source = body.get("source", "")
    if not source:
        source = str(_get_project_root())

    loop = asyncio.get_running_loop()
    stats = await loop.run_in_executor(None, migrate_data_from_project, source)
    return JSONResponse(content={"status": "ok", "migrated": stats})


# ── 远程部署 ──────────────────────────────────────────


@router.get("/api/v1/deploy/status")
async def deploy_status(request: Request) -> JSONResponse:
    """获取部署环境状态（服务器配置、制品列表、部署历史）。"""
    import asyncio
    from excelmanus.updater import get_deploy_status

    loop = asyncio.get_running_loop()
    status = await loop.run_in_executor(None, get_deploy_status, _get_project_root())
    return JSONResponse(content=status)


@router.post("/api/v1/deploy/build")
async def deploy_build_artifact(request: Request) -> JSONResponse:
    """本地构建前端制品（npm ci + npm run build + tar.gz 打包）。"""
    if not _is_admin_or_noauth(request):
        return _error(403, "需要管理员权限")

    import asyncio
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
    target: str = Field(default="full", description="部署目标: full | backend | frontend")
    skip_build: bool = Field(default=False, description="跳过前端构建（使用已有制品）")
    artifact_path: str = Field(default="", description="已有制品路径（skip_build=True 时需要）")
    from_local: bool = Field(default=True, description="从本地 rsync 同步代码")
    skip_deps: bool = Field(default=False, description="跳过远端依赖安装")


@router.post("/api/v1/deploy/execute")
async def deploy_execute(body: DeployExecuteRequest, request: Request) -> JSONResponse:
    """执行远程部署（构建 + 推送，仅管理员）。"""
    if not _is_admin_or_noauth(request):
        return _error(403, "需要管理员权限")

    import asyncio
    from excelmanus.updater import DeployConfig, perform_remote_deploy

    root = _get_project_root()
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


# ── 结构化部署历史 ────────────────────────────────────


@router.get("/api/v1/deploy/history")
async def deploy_history(request: Request) -> JSONResponse:
    """返回结构化部署历史（供回滚面板使用）。"""
    from excelmanus.updater import get_deploy_history_structured

    history = get_deploy_history_structured(_get_project_root())
    return JSONResponse(content={"history": history})


# ── 远程回滚 ──────────────────────────────────────────


class RollbackRequest(BaseModel):
    target: str = Field(default="full", description="回滚目标: full | backend | frontend")
    release_id: str = Field(default="", description="目标 release_id（与 commit 二选一）")
    commit: str = Field(default="", description="目标 git commit hash（与 release_id 二选一）")
    skip_deps: bool = Field(default=False, description="跳过依赖重装")


@router.post("/api/v1/deploy/rollback")
async def deploy_rollback(body: RollbackRequest, request: Request) -> JSONResponse:
    """执行远程回滚（仅管理员）。"""
    if not _is_admin_or_noauth(request):
        return _error(403, "需要管理员权限")

    import asyncio
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


@router.post("/api/v1/deploy/rollback/stream")
async def deploy_rollback_stream(body: RollbackRequest, request: Request) -> StreamingResponse:
    """以 SSE 流式推送回滚进度。"""
    if not _is_admin_or_noauth(request):
        async def _forbidden():
            yield f"event: error\ndata: {_json.dumps({'error': '需要管理员权限'})}\n\n"
        return StreamingResponse(_forbidden(), media_type="text/event-stream", status_code=403)

    import asyncio
    from excelmanus.updater import perform_remote_rollback

    root = _get_project_root()
    queue: asyncio.Queue[dict | None] = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def _progress_cb(msg: str, pct: int) -> None:
        asyncio.run_coroutine_threadsafe(
            queue.put({"event": "progress", "message": msg, "percent": pct}),
            loop,
        )

    async def _run_rollback() -> None:
        try:
            result = await loop.run_in_executor(
                None,
                lambda: perform_remote_rollback(
                    root,
                    target=body.target,
                    release_id=body.release_id,
                    commit=body.commit,
                    skip_deps=body.skip_deps,
                    progress_cb=_progress_cb,
                ),
            )
            result["event"] = "done"
            await queue.put(result)
        except Exception as exc:
            logger.error("流式回滚异常: %s", exc, exc_info=True)
            await queue.put({"event": "error", "error": str(exc)})
        finally:
            await queue.put(None)

    asyncio.create_task(_run_rollback())

    async def _event_generator():
        while True:
            item = await queue.get()
            if item is None:
                break
            event_type = item.pop("event", "progress")
            yield f"event: {event_type}\ndata: {_json.dumps(item, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        _event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── 灰度（Canary）管理 ───────────────────────────────


@router.get("/api/v1/deploy/canary/status")
async def canary_status(request: Request) -> JSONResponse:
    """获取当前灰度部署状态。"""
    from excelmanus.updater import get_canary_status

    status = get_canary_status(_get_project_root())
    return JSONResponse(content=status)


@router.post("/api/v1/deploy/canary/promote")
async def canary_promote(request: Request) -> JSONResponse:
    """手动提升灰度权重到下一阶梯。"""
    if not _is_admin_or_noauth(request):
        return _error(403, "需要管理员权限")

    from excelmanus.updater import canary_promote as _canary_promote

    result = _canary_promote(_get_project_root())
    status_code = 200 if result.get("success") else 500
    return JSONResponse(status_code=status_code, content=result)


@router.post("/api/v1/deploy/canary/abort")
async def canary_abort(request: Request) -> JSONResponse:
    """中止灰度，回退到 0%。"""
    if not _is_admin_or_noauth(request):
        return _error(403, "需要管理员权限")

    from excelmanus.updater import canary_abort as _canary_abort

    result = _canary_abort(_get_project_root())
    status_code = 200 if result.get("success") else 500
    return JSONResponse(status_code=status_code, content=result)


# ── 灰度发起 ──────────────────────────────────────────


class CanaryStartRequest(BaseModel):
    target: str = Field(default="full", description="部署目标: full | backend")
    observe_seconds: int = Field(default=60, description="每阶段观察时间（秒）")


@router.post("/api/v1/deploy/canary/start")
async def canary_start(body: CanaryStartRequest, request: Request) -> JSONResponse:
    """发起灰度部署（仅管理员）。"""
    if not _is_admin_or_noauth(request):
        return _error(403, "需要管理员权限")

    from excelmanus.updater import canary_start as _canary_start

    root = _get_project_root()
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(
        None,
        lambda: _canary_start(root, target=body.target, observe_seconds=body.observe_seconds),
    )
    status_code = 200 if result.get("success") else 500
    return JSONResponse(status_code=status_code, content=result)


# ── 部署锁状态 ────────────────────────────────────────


@router.get("/api/v1/deploy/lock/status")
async def deploy_lock_status(request: Request) -> JSONResponse:
    """查询本地 + 远程部署锁状态。"""
    import asyncio
    from excelmanus.updater import check_remote_deploy_lock

    root = _get_project_root()
    loop = asyncio.get_running_loop()
    remote_lock = await loop.run_in_executor(None, check_remote_deploy_lock, root)

    # 本地 threading.Lock 状态
    from excelmanus.updater import _deploy_lock
    local_locked = _deploy_lock.locked()

    return JSONResponse(content={
        "local_locked": local_locked,
        "remote": remote_lock,
    })


# ── 部署日志查看 ──────────────────────────────────────


@router.get("/api/v1/deploy/history/{release_id}/log")
async def deploy_history_log(release_id: str, request: Request) -> JSONResponse:
    """按需加载单条部署的日志内容。"""
    from excelmanus.updater import get_deploy_log

    root = _get_project_root()
    log_content = get_deploy_log(root, release_id)
    return JSONResponse(content={"release_id": release_id, "log": log_content})
