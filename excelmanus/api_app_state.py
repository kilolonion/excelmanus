"""API 运行时状态：供拆出的路由模块读取，禁止反向 import ``excelmanus.api`` 全局。

``api.py`` lifespan 写入；files 等子路由只通过本模块 getter 读取。
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

_config: Any = None
_session_manager: Any = None
_database: Any = None
_config_store: Any = None
_file_registries: dict[str, Any] = {}
_config_incomplete: bool = False
_restart_reason: str = ""
_cap_probe_job_manager: Any = None


def set_config(config: Any) -> None:
    global _config
    _config = config


def get_config() -> Any:
    return _config


def set_session_manager(session_manager: Any) -> None:
    global _session_manager
    _session_manager = session_manager


def get_session_manager() -> Any:
    return _session_manager


def set_database(database: Any) -> None:
    global _database
    _database = database


def get_database() -> Any:
    return _database


def set_config_store(config_store: Any) -> None:
    global _config_store
    _config_store = config_store


def get_config_store() -> Any:
    return _config_store


_channel_launcher: Any = None


def set_channel_launcher(launcher: Any) -> None:
    global _channel_launcher
    _channel_launcher = launcher
    # 与 api.py 模块级别名同步，避免 lifespan 仍读 excelmanus.api._channel_launcher 时漏改
    api_mod = sys.modules.get("excelmanus.api")
    if api_mod is not None:
        api_mod._channel_launcher = launcher


def get_channel_launcher() -> Any:
    return _channel_launcher


_skillpack_manager: Any = None
_skillpack_loader: Any = None


def set_skillpack_manager(manager: Any) -> None:
    global _skillpack_manager
    _skillpack_manager = manager
    api_mod = sys.modules.get("excelmanus.api")
    if api_mod is not None:
        api_mod._skillpack_manager = manager


def get_skillpack_manager() -> Any:
    # 测试常直接赋值 excelmanus.api._skillpack_manager；优先读别名以免 app_state 过期。
    api_mod = sys.modules.get("excelmanus.api")
    if api_mod is not None:
        return getattr(api_mod, "_skillpack_manager", _skillpack_manager)
    return _skillpack_manager


def set_skillpack_loader(loader: Any) -> None:
    global _skillpack_loader
    _skillpack_loader = loader
    api_mod = sys.modules.get("excelmanus.api")
    if api_mod is not None:
        api_mod._skillpack_loader = loader


def get_skillpack_loader() -> Any:
    api_mod = sys.modules.get("excelmanus.api")
    if api_mod is not None:
        return getattr(api_mod, "_skillpack_loader", _skillpack_loader)
    return _skillpack_loader


_tool_registry: Any = None
_draining: bool = False

# chat stream / subscribe / abort 共用；测试会整体替换本模块上的名字。
_active_chat_tasks: dict[str, asyncio.Task[Any]] = {}
_session_stream_states: dict[str, Any] = {}


def set_tool_registry(registry: Any) -> None:
    global _tool_registry
    _tool_registry = registry
    api_mod = sys.modules.get("excelmanus.api")
    if api_mod is not None:
        api_mod._tool_registry = registry


def get_tool_registry() -> Any:
    # 测试常直接赋值 excelmanus.api._tool_registry；优先读别名以免 app_state 过期。
    api_mod = sys.modules.get("excelmanus.api")
    if api_mod is not None:
        return getattr(api_mod, "_tool_registry", _tool_registry)
    return _tool_registry


def set_draining(draining: bool) -> None:
    global _draining
    _draining = draining
    api_mod = sys.modules.get("excelmanus.api")
    if api_mod is not None:
        api_mod._draining = draining


def get_draining() -> bool:
    api_mod = sys.modules.get("excelmanus.api")
    if api_mod is not None:
        return bool(getattr(api_mod, "_draining", _draining))
    return _draining


def set_config_incomplete(incomplete: bool) -> None:
    global _config_incomplete
    _config_incomplete = incomplete


def get_config_incomplete() -> bool:
    return _config_incomplete


def set_restart_reason(reason: str) -> None:
    global _restart_reason
    _restart_reason = reason


def get_restart_reason() -> str:
    return _restart_reason


def set_cap_probe_job_manager(mgr: Any) -> None:
    global _cap_probe_job_manager
    _cap_probe_job_manager = mgr


def get_cap_probe_job_manager() -> Any:
    return _cap_probe_job_manager


def _get_probe_job_mgr() -> Any:
    """获取或懒创建异步能力探测任务管理器。"""
    global _cap_probe_job_manager
    if _cap_probe_job_manager is None:
        from excelmanus.capability_probe_jobs import CapabilityProbeJobManager

        _cap_probe_job_manager = CapabilityProbeJobManager()
    return _cap_probe_job_manager


def _list_available_model_names() -> list[str]:
    """返回可切换的模型名称列表（含 default）。"""
    names: list[str] = ["default"]
    config_store = get_config_store()
    if config_store is not None:
        names.extend((p.get("name") or "") for p in config_store.list_profiles())
    dedup: list[str] = []
    seen: set[str] = set()
    for n in names:
        n = n.strip()
        if not n or n in seen:
            continue
        seen.add(n)
        dedup.append(n)
    return dedup


def _sync_config_profiles_from_db() -> None:
    """从数据库读取 model_profiles 并同步到 config.models。"""
    config = get_config()
    config_store = get_config_store()
    if config is None or config_store is None:
        return
    from excelmanus.config import ModelProfile

    rows = config_store.list_profiles()
    default_api_key = config.api_key
    default_base_url = config.base_url
    profiles: list[Any] = []
    for row in rows:
        profiles.append(ModelProfile(
            name=row["name"],
            model=row["model"],
            api_key=row.get("api_key") or default_api_key,
            base_url=row.get("base_url") or default_base_url,
            description=row.get("description", ""),
            protocol=row.get("protocol", "auto"),
            thinking_mode=row.get("thinking_mode", "auto"),
            model_family=row.get("model_family", ""),
            custom_extra_body=row.get("custom_extra_body", ""),
            custom_extra_headers=row.get("custom_extra_headers", ""),
        ))
    object.__setattr__(config, "models", tuple(profiles))


def bind_app_state(
    app: Any,
    *,
    config: Any | None = None,
    session_manager: Any | None = None,
    database: Any | None = None,
    config_store: Any | None = None,
) -> None:
    """把当前运行时对象挂到 ``app.state``，与模块 getter 同步。"""
    if config is not None:
        set_config(config)
        app.state.config = config
    if session_manager is not None:
        set_session_manager(session_manager)
        app.state.session_manager = session_manager
    if database is not None:
        set_database(database)
        app.state.database = database
    if config_store is not None:
        set_config_store(config_store)
        app.state.config_store = config_store


def get_file_registry(workspace_root: str) -> Any:
    """获取或懒创建指定工作区的 FileRegistry，始终共用主库。"""
    database = get_database()
    if database is None:
        return None
    cache_key = workspace_root
    reg = _file_registries.get(cache_key)
    if reg is not None:
        return reg
    try:
        from excelmanus.file_registry import FileRegistry

        reg = FileRegistry(database, workspace_root)
        _file_registries[cache_key] = reg
        return reg
    except Exception:
        from excelmanus.logger import get_logger

        get_logger("api").debug("FileRegistry 创建失败 (%s)", workspace_root, exc_info=True)
        return None


def safe_uploads_path(uploads_dir: Path, relative: str) -> Path | None:
    """在 uploads_dir 下解析 relative 路径并确保不越界。"""
    cleaned = relative.replace("\\", "/").strip("/")
    if ".." in cleaned.split("/"):
        return None
    target = (uploads_dir / cleaned).resolve()
    if not str(target).startswith(str(uploads_dir.resolve())):
        return None
    return target


def is_external_safe_mode() -> bool:
    """是否启用对外安全模式（默认开启）。"""
    config = get_config()
    if config is None:
        return True
    return bool(config.external_safe_mode)


async def has_session_access(session_id: str, request: Request) -> bool:
    """会话存在时返回 True。"""
    from excelmanus.session import SessionNotFoundError

    session_manager = get_session_manager()
    if session_manager is None:
        return False
    try:
        await session_manager.get_session_detail(session_id)
    except SessionNotFoundError:
        return False
    return True


def resolve_workspace(request: Request) -> Any:
    """解析进程唯一工作区。"""
    config = get_config()
    assert config is not None
    from excelmanus.workspace import IsolatedWorkspace, SandboxConfig

    docker_enabled = getattr(request.app.state, "docker_sandbox_enabled", False)
    return IsolatedWorkspace.resolve(
        config.workspace_root,
        sandbox_config=SandboxConfig(docker_enabled=docker_enabled),
        transaction_enabled=config.backup_enabled,
        data_root=config.data_root,
    )


def resolve_workspace_root(request: Request) -> str:
    """返回当前请求对应的工作区根目录路径字符串。"""
    return str(resolve_workspace(request).root_dir)


def resolve_excel_path(
    path: str,
    session_id: str | None = None,
    *,
    workspace_root: str | None = None,
) -> str | None:
    """将相对/绝对路径解析为安全的绝对路径。"""
    config = get_config()
    if config is None:
        return None

    ws_root = workspace_root or config.workspace_root
    workspace = Path(ws_root).resolve()
    workspace_str = str(workspace)

    resolved: str | None = None

    candidate = Path(path)
    if candidate.is_absolute():
        abs_resolved = candidate.resolve()
        if str(abs_resolved).startswith(workspace_str) and abs_resolved.is_file():
            resolved = str(abs_resolved)
    else:
        target = (workspace / path).resolve()
        if str(target).startswith(workspace_str) and target.is_file():
            resolved = str(target)
        else:
            for _subdir in ("outputs", "scripts", "uploads"):
                fallback = (workspace / _subdir / path).resolve()
                if str(fallback).startswith(workspace_str) and fallback.is_file():
                    resolved = str(fallback)
                    break

    if resolved is None:
        return None

    session_manager = get_session_manager()
    if session_id and session_manager is not None:
        engine = session_manager.get_engine(session_id)
        if engine is not None and engine.backup_enabled:
            tx = engine.transaction
            if tx is not None:
                try:
                    staged_resolved = tx.resolve_read(resolved)
                    if staged_resolved != resolved and Path(staged_resolved).is_file():
                        return staged_resolved
                except ValueError:
                    pass

    return resolved


class UnicodeJSONResponse(JSONResponse):
    """与 api.py CustomJSONResponse 一致：中文不转义。"""

    def render(self, content: Any) -> bytes:
        return json.dumps(
            content,
            ensure_ascii=False,
            allow_nan=False,
            indent=None,
            separators=(",", ":"),
        ).encode("utf-8")


def error_json_response(status_code: int, message: str, **extra: Any) -> JSONResponse:
    """构建统一错误响应（ensure_ascii=False，可附带 code 等字段）。"""
    error_id = str(uuid.uuid4())
    content: dict[str, Any] = {"error": message, "error_id": error_id, **extra}
    return UnicodeJSONResponse(status_code=status_code, content=content)


def make_content_disposition(filename: str) -> str:
    """构建兼容非 ASCII 文件名的 Content-Disposition 头部值（RFC 5987）。"""
    try:
        filename.encode("ascii")
        return f'attachment; filename="{filename}"'
    except UnicodeEncodeError:
        ascii_fallback = filename.encode("ascii", "replace").decode("ascii")
        encoded = quote(filename)
        return (
            f'attachment; filename="{ascii_fallback}"; '
            f"filename*=UTF-8''{encoded}"
        )
