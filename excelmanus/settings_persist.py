"""用户设置的唯一持久化：主库 ``config_kv``。

设置页、导入、``/config`` 都写这里。定位符不入库。
"""
from __future__ import annotations

import logging
from dataclasses import fields
from typing import Any

from excelmanus.data_home import env_value_is_set
from excelmanus.settings_runtime import (
    bind_store,
    get_bound_store,
    is_locator_key,
    override_settings,
    warn_ignored_product_env,
)

logger = logging.getLogger(__name__)


def persist_settings(updates: dict[str, str]) -> None:
    """把设置写入 ``config_kv``（若已绑定）并更新进程覆盖层。"""
    if not updates:
        return
    store = get_bound_store()
    if store is None:
        try:
            from excelmanus.api_app_state import get_config_store

            store = get_config_store()
        except Exception:
            store = None
        if store is not None:
            bind_store(store)

    writable: dict[str, str] = {}
    for key, value in updates.items():
        if is_locator_key(key):
            logger.warning("定位符不能写入设置仓，已忽略：%s", key)
            continue
        writable[key] = value
        if store is not None:
            if env_value_is_set(value):
                store.set(key, value)
            else:
                store.delete_key(key)
    if writable:
        override_settings(writable)


def apply_settings_from_store(store: Any) -> int:
    """绑定主库为设置源。"""
    if store is None:
        return 0
    bind_store(store)
    warn_ignored_product_env()
    count = 0
    try:
        items = store.list_kv()
    except Exception:
        return 0
    for key, value in items.items():
        if env_value_is_set(value):
            count += 1
    return count


def hydrate_runtime_settings(store: Any) -> int:
    """启动时绑定主库设置源。"""
    return apply_settings_from_store(store)


def refresh_config_in_place(config: Any) -> Any:
    """用当前设置源重载字段，保持原对象身份。"""
    if config is None:
        return config
    from excelmanus.config import ConfigError, ExcelManusConfig, load_config

    try:
        refreshed = load_config(allow_incomplete=True)
    except ConfigError:
        return config
    for field in fields(ExcelManusConfig):
        object.__setattr__(config, field.name, getattr(refreshed, field.name))
    return config


def bind_settings_store(database: Any) -> Any:
    """绑定主库配置存储。"""
    from excelmanus.api_app_state import set_config_store, set_database
    from excelmanus.stores.config_store import GlobalConfigStore

    store = GlobalConfigStore(database)
    set_database(database)
    set_config_store(store)
    hydrate_runtime_settings(store)
    return store
