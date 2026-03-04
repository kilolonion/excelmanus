"""Per-user 技能服务：为多用户场景提供隔离的 Loader/Router/Manager 实例。

单用户/CLI 模式（user_id=None）下行为与全局单例完全一致。
多用户模式下，每个用户拥有独立的技能加载器和管理器，
用户私有技能目录位于 ``{user_workspace}/skillpacks/``。

典型用法::

    service = UserSkillService(config, registry)
    loader  = service.get_loader(user_id)
    router  = service.get_router(user_id)
    manager = service.get_manager(user_id)
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import replace
from pathlib import Path
from typing import Any, NamedTuple

from excelmanus.config import ExcelManusConfig
from excelmanus.logger import get_logger
from excelmanus.skillpacks.loader import SkillpackLoader
from excelmanus.skillpacks.manager import SkillpackManager
from excelmanus.skillpacks.router import SkillRouter

logger = get_logger("skillpacks.user_skill_service")

_DEFAULT_CACHE_MAX = 128


class _UserSkillBundle(NamedTuple):
    """缓存的 per-user 技能三元组。"""

    loader: SkillpackLoader
    router: SkillRouter
    manager: SkillpackManager


class UserSkillService:
    """Per-user 技能服务：缓存 Loader/Router/Manager 实例。

    - system 技能对所有用户共享（由每个 loader 各自加载 system 目录）
    - user 技能隔离到 ``{user_workspace}/skillpacks/``
    - project 技能对所有用户共享
    """

    def __init__(
        self,
        config: ExcelManusConfig,
        registry: Any,
        *,
        cache_max: int = _DEFAULT_CACHE_MAX,
    ) -> None:
        self._config = config
        self._registry = registry
        self._cache_max = cache_max
        self._lock = threading.Lock()
        self._cache: OrderedDict[str | None, _UserSkillBundle] = OrderedDict()

    # ── 公开接口 ──────────────────────────────────────────

    def get_loader(self, user_id: str | None) -> SkillpackLoader:
        """获取 per-user SkillpackLoader（含 LRU 缓存）。"""
        return self._get_or_create(user_id).loader

    def get_router(self, user_id: str | None) -> SkillRouter:
        """获取 per-user SkillRouter。"""
        return self._get_or_create(user_id).router

    def get_manager(self, user_id: str | None) -> SkillpackManager:
        """获取 per-user SkillpackManager。"""
        return self._get_or_create(user_id).manager

    def invalidate(self, user_id: str | None = None) -> None:
        """使指定用户的缓存失效（技能 CRUD 后调用）。

        user_id=None 时清除匿名用户缓存。
        """
        with self._lock:
            self._cache.pop(user_id, None)

    def invalidate_all(self) -> None:
        """清除所有用户缓存。"""
        with self._lock:
            self._cache.clear()

    # ── 内部实现 ──────────────────────────────────────────

    def _get_or_create(self, user_id: str | None) -> _UserSkillBundle:
        with self._lock:
            bundle = self._cache.get(user_id)
            if bundle is not None:
                # LRU: 移到末尾
                self._cache.move_to_end(user_id)
                return bundle

        # 在锁外创建（loader.load_all 可能耗时）
        bundle = self._create_bundle(user_id)

        with self._lock:
            # double-check：其他线程可能已创建
            existing = self._cache.get(user_id)
            if existing is not None:
                self._cache.move_to_end(user_id)
                return existing

            self._cache[user_id] = bundle
            self._cache.move_to_end(user_id)

            # LRU 淘汰
            while len(self._cache) > self._cache_max:
                evicted_key, _ = self._cache.popitem(last=False)
                logger.debug("技能缓存 LRU 淘汰: user_id=%s", evicted_key)

            return bundle

    def _create_bundle(self, user_id: str | None) -> _UserSkillBundle:
        """为指定用户创建 Loader/Router/Manager 三元组。"""
        user_config = self._build_user_config(user_id)

        loader = SkillpackLoader(user_config, self._registry)
        loader.load_all()

        router = SkillRouter(user_config, loader)

        # 计算用户私有技能写入目录
        user_skill_dir = self._resolve_user_skill_dir(user_id)
        manager = SkillpackManager(
            user_config, loader,
            user_skill_dir=user_skill_dir,
        )

        logger.info(
            "创建 per-user 技能实例: user_id=%s, user_skill_dir=%s",
            user_id or "<anonymous>",
            user_skill_dir,
        )
        return _UserSkillBundle(loader=loader, router=router, manager=manager)

    def _build_user_config(self, user_id: str | None) -> ExcelManusConfig:
        """构建 per-user config，仅覆盖 skills_user_dir。"""
        if user_id is None:
            return self._config

        user_skill_dir = self._resolve_user_skill_dir(user_id)
        user_skill_dir.mkdir(parents=True, exist_ok=True)

        return replace(
            self._config,
            skills_user_dir=str(user_skill_dir),
        )

    def _resolve_user_skill_dir(self, user_id: str | None) -> Path:
        """计算用户私有技能目录路径。

        - user_id=None → 使用全局 skills_user_dir（~/.excelmanus/skillpacks）
        - user_id 非空 → {data_root|workspace_root}/users/{user_id}/skillpacks/
        """
        if user_id is None:
            return Path(self._config.skills_user_dir).expanduser().resolve()

        data_root = self._config.data_root
        if data_root:
            base = Path(data_root) / "users" / user_id
        else:
            base = Path(self._config.workspace_root) / "users" / user_id
        return (base / "skillpacks").resolve()
