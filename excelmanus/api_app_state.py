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
from urllib.parse import quote, urlparse

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

_config: Any = None
_session_manager: Any = None
_database: Any = None
_config_store: Any = None
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


def _is_codex_profile(name: str, model: str = "") -> bool:
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
    if _is_codex_profile(name, model) or (api_key and base_url and model):
        set_config_incomplete(False)
    else:
        set_config_incomplete(True)
    return True


def ensure_active_model() -> None:
    """启动时套用已激活档案；没有档案但有环境凭证时迁成档案。"""
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


def _sync_config_profiles_from_db() -> None:
    """从数据库读取 model_profiles 并同步到 config.models。"""
    config = get_config()
    config_store = get_config_store()
    if config is None or config_store is None:
        return
    from excelmanus.config import ModelProfile

    rows = config_store.list_profiles()
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


def uploads_mkdir(uploads_dir: Path, relative: str) -> Path | None:
    """Create uploads subdirectory without following any symlink component."""
    import os
    import stat

    target = safe_uploads_path(uploads_dir, relative)
    if target is None:
        return None
    cleaned = relative.replace("\\", "/").strip("/")
    cursor = Path(uploads_dir)
    for part in cleaned.split("/"):
        nxt = cursor / part
        try:
            st = os.lstat(nxt)
        except FileNotFoundError:
            os.mkdir(nxt)
            try:
                st = os.lstat(nxt)
            except OSError:
                return None
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            return None
        cursor = nxt
    return cursor


def uploads_create_file(uploads_dir: Path, relative: str) -> Path | None:
    """Create an empty file with O_EXCL and O_NOFOLLOW when available."""
    import os
    import stat

    target = safe_uploads_path(uploads_dir, relative)
    if target is None:
        return None
    parent = uploads_mkdir(uploads_dir, str(target.parent.relative_to(uploads_dir))) if target.parent != uploads_dir else uploads_dir
    if parent is None:
        return None
    try:
        pst = os.lstat(parent)
    except OSError:
        return None
    if stat.S_ISLNK(pst.st_mode):
        return None
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(str(target), flags, 0o644)
    except OSError:
        return None
    os.close(fd)
    return target


def uploads_delete(uploads_dir: Path, relative: str) -> tuple[Path | None, str | None]:
    """Delete a file or directory after confirming the node is still not a symlink."""
    import os
    import shutil
    import stat

    target = safe_uploads_path(uploads_dir, relative)
    if target is None:
        return None, "非法目标路径"
    try:
        st = os.lstat(target)
    except FileNotFoundError:
        return None, "路径不存在"
    except OSError:
        return None, "非法目标路径"
    if stat.S_ISLNK(st.st_mode):
        return None, "非法目标路径"
    try:
        if target.resolve() == Path(uploads_dir).resolve():
            return None, "无法删除根目录"
    except OSError:
        return None, "非法目标路径"
    try:
        st2 = os.lstat(target)
        if stat.S_ISLNK(st2.st_mode) or (st2.st_ino, st2.st_dev) != (st.st_ino, st.st_dev):
            return None, "非法目标路径"
        if stat.S_ISDIR(st2.st_mode):
            shutil.rmtree(target)
        else:
            os.unlink(target)
    except OSError:
        return None, "删除失败"
    return target, None


def uploads_rename(
    uploads_dir: Path, old_path: str, new_path: str,
) -> tuple[Path | None, Path | None, str | None]:
    import os
    import stat

    src = safe_uploads_path(uploads_dir, old_path)
    dst = safe_uploads_path(uploads_dir, new_path)
    if src is None or dst is None:
        return None, None, "非法路径"
    try:
        src_st = os.lstat(src)
    except FileNotFoundError:
        return None, None, "源路径不存在"
    except OSError:
        return None, None, "非法路径"
    if stat.S_ISLNK(src_st.st_mode):
        return None, None, "非法路径"
    try:
        os.lstat(dst)
        return None, None, "目标路径已存在"
    except FileNotFoundError:
        pass
    except OSError:
        return None, None, "非法路径"
    parent = dst.parent
    if parent != Path(uploads_dir):
        made = uploads_mkdir(uploads_dir, str(parent.relative_to(uploads_dir)))
        if made is None:
            return None, None, "非法路径"
    try:
        st2 = os.lstat(src)
        if stat.S_ISLNK(st2.st_mode) or (st2.st_ino, st2.st_dev) != (src_st.st_ino, src_st.st_dev):
            return None, None, "非法路径"
        os.rename(str(src), str(dst))
    except OSError:
        return None, None, "重命名失败"
    return src, dst, None


def write_new_file_nofollow(dest: Path, content: bytes) -> None:
    """Create a new file without following a trailing symlink."""
    import os

    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(str(dest), flags, 0o644)
    try:
        os.write(fd, content)
    finally:
        os.close(fd)


def sanitize_upload_filename(filename: str, *, max_len: int = 120) -> str:
    """Basename + whitelist; never keep path separators."""
    import re

    base = Path(str(filename or "").replace("\\", "/")).name
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._")
    if not cleaned:
        cleaned = "unnamed"
    if len(cleaned) > max_len:
        stem = Path(cleaned).stem[: max(1, max_len - 8)]
        suffix = Path(cleaned).suffix[:8]
        cleaned = (stem + suffix)[:max_len]
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


def resolve_workspace(request: Request, session_id: str | None = None) -> Any:
    """Resolve the folder for this request: session cwd, else process default."""
    config = get_config()
    assert config is not None
    from excelmanus.workspace import IsolatedWorkspace, SandboxConfig
    from excelmanus.workspace.paths import default_workspace_path, paths_equal

    sid = (session_id or request.query_params.get("session_id") or "").strip() or None
    manager = get_session_manager()
    if sid and manager is not None:
        path = manager.workspace_path_for_session(sid)
        default_path = default_workspace_path(config)
        create_missing = paths_equal(path, default_path)
        try:
            return IsolatedWorkspace(
                root_dir=path,
                sandbox_config=SandboxConfig(),
                create_missing=create_missing,
            )
        except FileNotFoundError as exc:
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return IsolatedWorkspace.resolve(
        config.workspace_root,
        sandbox_config=SandboxConfig(),
        transaction_enabled=False,
        data_root=config.data_root,
    )


def resolve_workspace_root(request: Request, session_id: str | None = None) -> str:
    """返回当前请求对应的工作区根目录路径字符串。"""
    return str(resolve_workspace(request, session_id=session_id).root_dir)


def resolve_excel_path(
    path: str,
    session_id: str | None = None,
    *,
    workspace_root: str | None = None,
) -> str | None:
    """将相对/绝对路径解析为安全的绝对路径。"""
    from excelmanus.security.guard import FileAccessGuard, SecurityViolationError

    config = get_config()
    if config is None:
        return None

    ws_root = workspace_root
    if not ws_root and session_id:
        manager = get_session_manager()
        if manager is not None:
            ws_root = manager.workspace_path_for_session(session_id)
    if not ws_root:
        from excelmanus.workspace.paths import default_workspace_path
        ws_root = default_workspace_path(config)
    guard = FileAccessGuard(str(ws_root))

    candidates: list[str] = [path]
    raw = str(path or "").replace("\\", "/").lstrip("./")
    if raw and not Path(path).is_absolute():
        for subdir in ("outputs", "scripts", "uploads"):
            candidates.append(f"{subdir}/{raw}")

    for candidate in candidates:
        try:
            resolved = guard.resolve_and_validate(candidate)
        except (SecurityViolationError, OSError):
            continue
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
