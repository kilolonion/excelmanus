"""API 运行时状态：供拆出的路由模块读取，禁止反向 import ``excelmanus.api`` 全局。

``api.py`` lifespan 写入；files 等子路由只通过本模块 getter 读取。
"""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from contextvars import ContextVar
from dataclasses import dataclass, field


@dataclass
class AppRuntime:
    """One application owns all mutable runtime state."""
    config: Any = None
    session_manager: Any = None
    database: Any = None
    config_store: Any = None
    skillpack_manager: Any = None
    skillpack_loader: Any = None
    tool_registry: Any = None
    skill_router: Any = None
    rules_manager: Any = None
    persistent_memory: Any = None
    config_incomplete: bool = False
    draining: bool = False
    restart_reason: str = ""
    cap_probe_job_manager: Any = None
    active_chat_tasks: dict[str, Any] = field(default_factory=dict)
    session_stream_states: dict[str, Any] = field(default_factory=dict)


_runtime: ContextVar[AppRuntime | None] = ContextVar("excelmanus_app_runtime", default=None)


def get_runtime() -> AppRuntime:
    runtime = _runtime.get()
    if runtime is None:
        # Direct bench/testing callers have their own context, never module slots.
        runtime = AppRuntime()
        _runtime.set(runtime)
    return runtime


def bind_runtime(runtime: AppRuntime):
    return _runtime.set(runtime)


def reset_runtime(token) -> None:
    _runtime.reset(token)


class RuntimeMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        runtime = scope["app"].state.runtime
        token = bind_runtime(runtime)
        try:
            await self.app(scope, receive, send)
        finally:
            try:
                manager = runtime.session_manager
                if manager is not None and hasattr(manager, "drain_workspace_events"):
                    manager.drain_workspace_events()
            finally:
                reset_runtime(token)


def set_config(value: Any) -> None:
    get_runtime().config = value


def get_config() -> Any:
    return get_runtime().config


def set_session_manager(value: Any) -> None:
    get_runtime().session_manager = value


def get_session_manager() -> Any:
    return get_runtime().session_manager


def set_database(value: Any) -> None:
    get_runtime().database = value


def get_database() -> Any:
    return get_runtime().database


def set_config_store(value: Any) -> None:
    get_runtime().config_store = value
    from excelmanus.settings_runtime import bind_store, unbind_store

    if value is None:
        unbind_store()
    else:
        bind_store(value)


def get_config_store() -> Any:
    return get_runtime().config_store


def set_skillpack_manager(value: Any) -> None:
    get_runtime().skillpack_manager = value


def get_skillpack_manager() -> Any:
    return get_runtime().skillpack_manager


def set_skillpack_loader(value: Any) -> None:
    get_runtime().skillpack_loader = value


def get_skillpack_loader() -> Any:
    return get_runtime().skillpack_loader


def set_tool_registry(value: Any) -> None:
    get_runtime().tool_registry = value


def get_tool_registry() -> Any:
    return get_runtime().tool_registry


def set_skill_router(value: Any) -> None:
    get_runtime().skill_router = value


def get_skill_router() -> Any:
    return get_runtime().skill_router


def set_rules_manager(value: Any) -> None:
    get_runtime().rules_manager = value


def get_rules_manager() -> Any:
    return get_runtime().rules_manager


def set_persistent_memory(value: Any) -> None:
    get_runtime().persistent_memory = value


def get_persistent_memory() -> Any:
    return get_runtime().persistent_memory


def set_config_incomplete(value: Any) -> None:
    get_runtime().config_incomplete = value


def get_config_incomplete() -> Any:
    return get_runtime().config_incomplete


def set_draining(value: Any) -> None:
    get_runtime().draining = value


def get_draining() -> Any:
    return get_runtime().draining


def set_restart_reason(value: Any) -> None:
    get_runtime().restart_reason = value


def get_restart_reason() -> Any:
    return get_runtime().restart_reason


def set_cap_probe_job_manager(value: Any) -> None:
    get_runtime().cap_probe_job_manager = value


def get_cap_probe_job_manager() -> Any:
    return get_runtime().cap_probe_job_manager


def _get_probe_job_mgr() -> Any:
    runtime = get_runtime()
    if runtime.cap_probe_job_manager is None:
        from excelmanus.capability_probe_jobs import CapabilityProbeJobManager
        runtime.cap_probe_job_manager = CapabilityProbeJobManager()
    return runtime.cap_probe_job_manager


def _list_available_model_names() -> list[str]:
    """返回可切换的模型档案名称。"""
    names: list[str] = []
    config_store = get_config_store()
    if config_store is not None:
        names.extend(
            (p.get("name") or "")
            for p in config_store.list_profiles()
            if not is_placeholder_model_profile(
                p.get("name", ""), p.get("model", ""), p.get("base_url", ""),
            )
        )
    dedup: list[str] = []
    seen: set[str] = set()
    for n in names:
        n = n.strip()
        if not n or n in seen:
            continue
        seen.add(n)
        dedup.append(n)
    return dedup


def _user_config_store() -> Any:
    store = get_config_store()
    if store is None:
        return None
    from excelmanus.stores.config_store import UserConfigStore
    return UserConfigStore(store._conn)


def _is_subscription_profile(name: str, model: str = "") -> bool:
    """name/model 命中任一已注册订阅 provider 的 MODEL_NAME_PREFIX。"""
    try:
        from excelmanus.auth.providers.registry import managed_provider_for
        return (
            managed_provider_for(str(name)) is not None
            or managed_provider_for(str(model)) is not None
        )
    except Exception:
        return str(name).startswith("openai-codex/") or str(model).startswith("openai-codex/")


_PLACEHOLDER_MODEL_IDS = frozenset({"test-model", "dummy-model", "placeholder-model"})
_PLACEHOLDER_HOSTS = frozenset({"example.com", "www.example.com", "example.invalid"})


def is_placeholder_model_profile(
    name: str = "",
    model: str = "",
    base_url: str = "",
) -> bool:
    """识别测试残留档案（test-model / example.com），不能当作可激活模型。"""
    for ident in (name, model):
        if str(ident).strip().lower() in _PLACEHOLDER_MODEL_IDS:
            return True
    host = (urlparse(str(base_url or "")).hostname or "").lower()
    return host in _PLACEHOLDER_HOSTS


def apply_profile_to_config(name: str) -> bool:
    """把指定档案完整写入运行时 config 快照，不继承其它档案的凭证。"""
    config = get_config()
    store = get_config_store()
    if config is None or store is None:
        return False
    profile = store.get_profile(name)
    if profile is None:
        return False
    model = profile.get("model") or ""
    api_key = profile.get("api_key") or ""
    base_url = profile.get("base_url") or ""
    protocol = profile.get("protocol") or "auto"
    object.__setattr__(config, "model", model)
    object.__setattr__(config, "api_key", api_key)
    object.__setattr__(config, "base_url", base_url)
    object.__setattr__(config, "protocol", protocol)
    if _is_subscription_profile(name, model) or (api_key and base_url and model):
        set_config_incomplete(False)
    else:
        set_config_incomplete(True)
    return True


def ensure_active_model() -> None:
    """启动时套用已激活档案；没有档案但当前配置已有凭证时迁成档案。"""
    config = get_config()
    store = get_config_store()
    if config is None or store is None:
        return
    user = _user_config_store()
    if user is None:
        return
    purged = False
    for row in list(store.list_profiles()):
        if not is_placeholder_model_profile(
            row.get("name", ""), row.get("model", ""), row.get("base_url", ""),
        ):
            continue
        store.delete_profile(row["name"])
        if user.get_active_model() == row["name"]:
            user.set_active_model(None)
        purged = True
    if purged:
        _sync_config_profiles_from_db()
    profiles = [
        row for row in store.list_profiles()
        if not is_placeholder_model_profile(
            row.get("name", ""), row.get("model", ""), row.get("base_url", ""),
        )
    ]
    if not profiles:
        if config.model and config.api_key and config.base_url:
            if is_placeholder_model_profile(config.model, config.model, config.base_url):
                return
            name = config.model
            if store.get_profile(name) is None:
                store.add_profile(
                    name=name,
                    model=config.model,
                    api_key=config.api_key,
                    base_url=config.base_url,
                    description="",
                    protocol=config.protocol or "auto",
                )
            user.set_active_model(name)
            _sync_config_profiles_from_db()
        return
    name = user.get_active_model()
    if name and (
        is_placeholder_model_profile(name)
        or store.get_profile(name) is None
    ):
        name = None
    if name and store.get_profile(name):
        apply_profile_to_config(name)
        return
    first = profiles[0]["name"]
    user.set_active_model(first)
    apply_profile_to_config(first)


def build_model_profiles_from_rows(rows: list[dict[str, Any]]) -> list[Any]:
    """把 config_store 的 profile 行转换为 ModelProfile 列表（过滤占位档案）。

    纯函数，不依赖绑定的 runtime；API lifespan 与 bench 进程共用。
    """
    from excelmanus.config import ModelProfile

    profiles: list[Any] = []
    for row in rows:
        if is_placeholder_model_profile(
            row.get("name", ""), row.get("model", ""), row.get("base_url", ""),
        ):
            continue
        profiles.append(ModelProfile(
            name=row["name"],
            model=row["model"],
            api_key=row.get("api_key") or "",
            base_url=row.get("base_url") or "",
            description=row.get("description", ""),
            protocol=row.get("protocol", "auto"),
            thinking_mode=row.get("thinking_mode", "auto"),
            model_family=row.get("model_family", ""),
            custom_extra_body=row.get("custom_extra_body", ""),
            custom_extra_headers=row.get("custom_extra_headers", ""),
        ))
    return profiles


def _sync_config_profiles_from_db() -> None:
    """从数据库读取 model_profiles 并同步到 config.models。"""
    config = get_config()
    config_store = get_config_store()
    if config is None or config_store is None:
        return
    profiles = build_model_profiles_from_rows(config_store.list_profiles())
    object.__setattr__(config, "models", tuple(profiles))


def bind_app_state(
    app: Any,
    *,
    config: Any | None = None,
    session_manager: Any | None = None,
    database: Any | None = None,
    config_store: Any | None = None,
) -> None:
    """Bind explicit application state for lifespan and direct test calls."""
    if not hasattr(app.state, "runtime"):
        app.state.runtime = AppRuntime()
    runtime = app.state.runtime
    bind_runtime(runtime)
    for key, value in {"config": config, "session_manager": session_manager, "database": database, "config_store": config_store}.items():
        if value is not None:
            setattr(runtime, key, value)


def get_file_registry(workspace_root: str) -> Any:
    """获取或懒创建指定工作区的 FileRegistry，与引擎共用同一实例。"""
    database = get_database()
    if database is None:
        return None
    try:
        from excelmanus.file_registry import get_shared_file_registry

        return get_shared_file_registry(database, workspace_root)
    except Exception:
        from excelmanus.logger import get_logger

        get_logger("api").debug("FileRegistry 创建失败 (%s)", workspace_root, exc_info=True)
        return None


def safe_uploads_path(uploads_dir: Path, relative: str) -> Path | None:
    """在 uploads_dir 下解析 relative 路径并确保不越界。存在节点不得为 symlink。"""
    import os
    import stat

    from excelmanus.security.guard import FileAccessGuard, SecurityViolationError

    cleaned = relative.replace("\\", "/").strip("/")
    if not cleaned or ".." in cleaned.split("/"):
        return None
    try:
        guard = FileAccessGuard(str(uploads_dir))
        target = guard.resolve_and_validate(cleaned)
    except (SecurityViolationError, OSError):
        return None
    try:
        st = os.lstat(target)
    except FileNotFoundError:
        if not _uploads_ancestors_nonsymlink(uploads_dir, target):
            return None
        return target
    except OSError:
        return None
    if stat.S_ISLNK(st.st_mode):
        return None
    return target


def _uploads_ancestors_nonsymlink(uploads_dir: Path, target: Path) -> bool:
    import os
    import stat

    try:
        root = uploads_dir.resolve()
        cursor = target.parent
        while True:
            try:
                st = os.lstat(cursor)
            except FileNotFoundError:
                if cursor == cursor.parent:
                    return False
                cursor = cursor.parent
                continue
            if stat.S_ISLNK(st.st_mode):
                return False
            try:
                if cursor.resolve() == root:
                    return True
            except OSError:
                return False
            parent = cursor.parent
            if parent == cursor:
                return False
            cursor = parent
    except OSError:
        return False


def sanitize_upload_filename(filename: str, *, max_len: int = 120) -> str:
    """Basename + whitelist; never keep path separators. 保留安全扩展名。"""
    import re

    base = Path(str(filename or "").replace("\\", "/")).name
    suffix = Path(base).suffix
    stem = Path(base).stem
    # 保留 Unicode 字母/汉字（主力用户的中文文件名是常态输入），
    # 只剥路径分隔符、控制字符、零宽/双向覆盖等非 \w 字符。
    cleaned_stem = re.sub(r"[^\w.\-]+", "_", stem).strip("._") or "unnamed"
    cleaned_suffix = re.sub(r"[^A-Za-z0-9.]+", "", suffix)
    if cleaned_suffix and not cleaned_suffix.startswith("."):
        cleaned_suffix = f".{cleaned_suffix}"
    cleaned = f"{cleaned_stem}{cleaned_suffix}"
    if len(cleaned) > max_len:
        keep = max(1, max_len - len(cleaned_suffix))
        cleaned = f"{cleaned_stem[:keep]}{cleaned_suffix}"[:max_len]
    return cleaned


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


def _workspace_id_from_request(
    request: Request,
    workspace_id: str | None = None,
) -> str | None:
    if workspace_id and str(workspace_id).strip():
        return str(workspace_id).strip()
    return (request.query_params.get("workspace_id") or "").strip() or None


def resolve_workspace(
    request: Request,
    session_id: str | None = None,
    workspace_id: str | None = None,
    *,
    require_scope: bool = False,
) -> Any:
    """Resolve the folder for this request.

    File routes must pass ``require_scope=True``: missing both a resolvable
    ``session_id`` and ``workspace_id`` is 400 ``FILE_SCOPE_REQUIRED``.
    Non-file callers may still fall back to the process default workspace.
    """
    from fastapi import HTTPException

    config = get_config()
    assert config is not None
    from excelmanus.stores.workspace_store import WorkspacePathError
    from excelmanus.workspace import IsolatedWorkspace, SandboxConfig
    from excelmanus.workspace.paths import default_workspace_path, paths_equal

    sid = (session_id or request.query_params.get("session_id") or "").strip() or None
    wid = _workspace_id_from_request(request, workspace_id)
    manager = get_session_manager()

    def _open(path: str) -> Any:
        default_path = default_workspace_path(config)
        create_missing = paths_equal(path, default_path)
        try:
            return IsolatedWorkspace(
                root_dir=path,
                sandbox_config=SandboxConfig(),
                create_missing=create_missing,
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    if sid and manager is not None:
        if require_scope and not manager.session_has_file_scope(sid):
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "无法确定工作区，请从会话重新打开文件",
                    "code": "FILE_SCOPE_REQUIRED",
                },
            )
        return _open(manager.workspace_path_for_session(sid))
    if wid and manager is not None:
        try:
            path, _bound_id = manager.resolve_workspace_binding(wid, None)
        except WorkspacePathError as exc:
            raise HTTPException(
                status_code=400,
                detail={"error": str(exc), "code": "FILE_SCOPE_REQUIRED"},
            ) from exc
        return _open(path)
    if require_scope:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "无法确定工作区，请从会话重新打开文件",
                "code": "FILE_SCOPE_REQUIRED",
            },
        )
    return _open(default_workspace_path(config))


def resolve_workspace_root(
    request: Request,
    session_id: str | None = None,
    workspace_id: str | None = None,
    *,
    require_scope: bool = False,
) -> str:
    """返回当前请求对应的工作区根目录路径字符串。"""
    return str(
        resolve_workspace(
            request,
            session_id=session_id,
            workspace_id=workspace_id,
            require_scope=require_scope,
        ).root_dir
    )


def resolve_excel_path(
    path: str,
    session_id: str | None = None,
    *,
    workspace_root: str | None = None,
) -> str | None:
    """将调用方给出的相对/绝对路径解析为安全的绝对路径。

    无 workspace_root 且无 session 时不猜默认根；也不把裸名补成
    ``uploads/`` / ``outputs/`` / ``scripts/`` 下的同名文件。
    """
    from excelmanus.security.guard import FileAccessGuard, SecurityViolationError

    config = get_config()
    if config is None:
        return None

    ws_root = workspace_root
    if not ws_root and session_id:
        manager = get_session_manager()
        if manager is not None and manager.session_has_file_scope(session_id):
            ws_root = manager.workspace_path_for_session(session_id)
    if not ws_root:
        return None
    guard = FileAccessGuard(str(ws_root))
    try:
        resolved = guard.resolve_and_validate(path)
    except (SecurityViolationError, OSError):
        return None
    if resolved.is_file():
        return str(resolved)
    return None


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
