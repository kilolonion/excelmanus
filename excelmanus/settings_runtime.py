"""产品设置运行时：主库 ``config_kv`` + 进程覆盖层。

``get_setting`` 只读 ``using_values`` → overlay → 主库。进程环境不是设置仓。
定位符（数据卷、监听端口、部署模式、子进程 IPC）由启动进程写入 ``os.environ``，
经 ``data_home`` / 绑定端口等专用入口读取，不经过本模块。
"""
from __future__ import annotations

import logging
import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from excelmanus.data_home import env_value_is_set

logger = logging.getLogger(__name__)

_overlay: dict[str, str] = {}
_store: Any = None
_values_override: ContextVar[dict[str, str] | None] = ContextVar(
    "excelmanus_settings_values", default=None,
)
_warned_ignored_env = False

# 进程定位符：找到数据卷 / 绑定端口 / 部署模式 / 子进程 IPC。不是设置仓。
LOCATOR_KEYS = frozenset({
    "EXCELMANUS_HOME",
    "EXCELMANUS_DESKTOP",
    "EXCELMANUS_DESKTOP_CONTROL_STDIN",
    "EXCELMANUS_DB_PATH",
    "EXCELMANUS_CHAT_HISTORY_DB_PATH",
    "EXCELMANUS_DATA_ROOT",
    "EXCELMANUS_DEPLOY_MODE",
    "EXCELMANUS_API_HOST",
    "EXCELMANUS_API_PORT",
    "EXCELMANUS_BACKEND_PORT",
    "EXCELMANUS_FRONTEND_PORT",
    "EXCELMANUS_WEB_WORKERS",
    "EXCELMANUS_MANAGE_TOKEN",
    "EXCELMANUS_SECRET_KEY",
    "EXCELMANUS_RUN_PYTHON",
    "EXCELMANUS_WORKDIR",
    "EXCELMANUS_SAVE_VERSIONS_LOG",
})
LOCATOR_PREFIXES = (
    "EXCELMANUS_CODE_MODE_",
    "EXCELMANUS_PENDING_",
    "EXCELMANUS_BENCH_",
)


def is_locator_key(key: str) -> bool:
    if key in LOCATOR_KEYS:
        return True
    return any(key.startswith(prefix) for prefix in LOCATOR_PREFIXES)


def bind_store(store: Any) -> None:
    global _store
    _store = store


def unbind_store() -> None:
    global _store
    _store = None


def get_bound_store() -> Any:
    return _store


def override_settings(updates: Mapping[str, str | None]) -> None:
    """写入进程覆盖层。空值表示显式取消。"""
    for key, value in updates.items():
        if value is None or not env_value_is_set(str(value)):
            _overlay[key] = ""
        else:
            _overlay[key] = str(value)


def clear_setting(key: str) -> None:
    _overlay[key] = ""


def reset_runtime_settings() -> None:
    global _store
    _overlay.clear()
    _store = None


@contextmanager
def using_values(values: Mapping[str, str]) -> Iterator[None]:
    """单元测试：临时用一份映射代替 store / overlay。"""
    token = _values_override.set(dict(values))
    try:
        yield
    finally:
        _values_override.reset(token)


def get_setting(key: str) -> str | None:
    """读取产品设置。顺序：``using_values`` → overlay → 主库。不读进程环境。

    定位符不走本函数；即使 overlay / 主库里有残留键也忽略。
    """
    if is_locator_key(key):
        return None
    values = _values_override.get()
    if values is not None:
        raw = values.get(key)
        if raw is None or not env_value_is_set(str(raw)):
            return None
        return str(raw)
    if key in _overlay:
        raw = _overlay[key]
        return raw if env_value_is_set(raw) else None
    if _store is not None:
        try:
            raw = _store.get(key)
        except Exception:
            raw = None
        if env_value_is_set(raw):
            return str(raw)
    return None


def credentials_from_store() -> dict[str, str]:
    """从已绑定主库读取激活档案凭证。"""
    store = _store
    if store is None or not hasattr(store, "get_profile"):
        return {}
    try:
        from excelmanus.api_app_state import is_placeholder_model_profile
    except Exception:
        is_placeholder_model_profile = None  # type: ignore[assignment]
    active = ""
    try:
        active = str(store.get("active_model") or "").strip()
    except Exception:
        return {}
    row = store.get_profile(active) if active else None
    if row is not None and is_placeholder_model_profile is not None and is_placeholder_model_profile(
        row.get("name", ""), row.get("model", ""), row.get("base_url", ""),
    ):
        row = None
    if row is None and hasattr(store, "list_profiles"):
        profiles = []
        for item in store.list_profiles():
            if is_placeholder_model_profile is not None and is_placeholder_model_profile(
                item.get("name", ""), item.get("model", ""), item.get("base_url", ""),
            ):
                continue
            profiles.append(item)
        row = profiles[0] if profiles else None
    if not row:
        return {}
    if is_placeholder_model_profile is not None and is_placeholder_model_profile(
        row.get("name", ""), row.get("model", ""), row.get("base_url", ""),
    ):
        return {}
    api_key = str(row.get("api_key") or "").strip()
    base_url = str(row.get("base_url") or "").strip()
    model = str(row.get("model") or "").strip()
    protocol = str(row.get("protocol") or "").strip()
    if not (api_key and base_url and model):
        return {}
    out = {"api_key": api_key, "base_url": base_url, "model": model}
    if protocol:
        out["protocol"] = protocol
    return out


def models_from_store() -> tuple[Any, ...]:
    store = _store
    if store is None or not hasattr(store, "list_profiles"):
        return ()
    try:
        from excelmanus.api_app_state import build_model_profiles_from_rows
        return tuple(build_model_profiles_from_rows(store.list_profiles()))
    except Exception:
        return ()


def warn_ignored_product_env() -> None:
    """进程环境里残留的产品设置键会被忽略。"""
    global _warned_ignored_env
    if _warned_ignored_env:
        return
    stale = sorted(
        key
        for key in os.environ
        if key.startswith("EXCELMANUS_") and not is_locator_key(key)
    )
    if not stale:
        return
    _warned_ignored_env = True
    logger.warning(
        "进程环境中的产品设置已被忽略，请在 Web 设置页写入主库：%s",
        ", ".join(stale),
    )
