"""会话管理模块：并发安全的会话容器，支持 TTL 与上限控制。"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from excelmanus.config import ExcelManusConfig
from excelmanus.engine import AgentEngine
from excelmanus.logger import get_logger
from excelmanus.skillpacks import SkillRouter

logger = get_logger("session")


# ── 异常定义 ──────────────────────────────────────────────


class SessionNotFoundError(Exception):
    """会话不存在时抛出，API 层映射为 404。"""


class SessionLimitExceededError(Exception):
    """会话数量达到上限时抛出，API 层映射为 429。"""


class SessionBusyError(Exception):
    """会话已有请求在处理中时抛出，API 层映射为 409。"""


# ── 内部会话条目 ──────────────────────────────────────────


@dataclass
class _SessionEntry:
    """单个会话的内部记录。"""

    engine: AgentEngine
    last_access: float
    in_flight: bool = field(default=False)


# ── SessionManager ────────────────────────────────────────


class SessionManager:
    """并发安全的会话容器，支持 TTL 与上限控制。

    所有公开方法均通过 asyncio.Lock 保护，避免并发竞态。
    """

    def __init__(
        self,
        max_sessions: int,
        ttl_seconds: int,
        *,
        config: ExcelManusConfig,
        registry: Any,
        skill_router: SkillRouter | None = None,
    ) -> None:
        self._max_sessions = max_sessions
        self._ttl_seconds = ttl_seconds
        self._config = config
        self._registry = registry
        self._skill_router = skill_router
        self._sessions: dict[str, _SessionEntry] = {}
        self._lock = asyncio.Lock()

    # ── 公开接口 ──────────────────────────────────────────

    async def get_or_create(
        self, session_id: str | None
    ) -> tuple[str, AgentEngine]:
        """获取已有会话或创建新会话。

        Args:
            session_id: 会话 ID。为 None 时创建新会话。

        Returns:
            (session_id, engine) 元组。

        Raises:
            SessionLimitExceededError: 会话数达到上限且需要创建新会话时抛出。
        """
        async with self._lock:
            # 已有会话：刷新访问时间并返回
            if session_id is not None and session_id in self._sessions:
                entry = self._sessions[session_id]
                entry.last_access = time.monotonic()
                logger.debug("复用会话 %s", session_id)
                return session_id, entry.engine

            # 需要创建新会话：检查容量上限
            if len(self._sessions) >= self._max_sessions:
                raise SessionLimitExceededError(
                    f"会话数量已达上限（{self._max_sessions}），请稍后重试。"
                )

            # 创建新会话
            new_id = session_id if session_id is not None else str(uuid.uuid4())
            engine = AgentEngine(
                config=self._config,
                registry=self._registry,
                skill_router=self._skill_router,
            )
            self._sessions[new_id] = _SessionEntry(
                engine=engine,
                last_access=time.monotonic(),
            )
            logger.info("创建新会话 %s（当前总数: %d）", new_id, len(self._sessions))
            return new_id, engine

    async def acquire_for_chat(
        self, session_id: str | None
    ) -> tuple[str, AgentEngine]:
        """获取会话并标记为处理中。

        同一会话在同一时刻仅允许一个请求执行。
        """
        async with self._lock:
            now = time.monotonic()
            if session_id is not None and session_id in self._sessions:
                entry = self._sessions[session_id]
                if entry.in_flight:
                    raise SessionBusyError(
                        f"会话 '{session_id}' 正在处理中，请稍后重试。"
                    )
                entry.in_flight = True
                entry.last_access = now
                logger.debug("复用会话并加锁 %s", session_id)
                return session_id, entry.engine

            if len(self._sessions) >= self._max_sessions:
                raise SessionLimitExceededError(
                    f"会话数量已达上限（{self._max_sessions}），请稍后重试。"
                )

            new_id = session_id if session_id is not None else str(uuid.uuid4())
            engine = AgentEngine(
                config=self._config,
                registry=self._registry,
                skill_router=self._skill_router,
            )
            self._sessions[new_id] = _SessionEntry(
                engine=engine,
                last_access=now,
                in_flight=True,
            )
            logger.info("创建新会话并加锁 %s（当前总数: %d）", new_id, len(self._sessions))
            return new_id, engine

    async def release_for_chat(self, session_id: str) -> None:
        """释放会话处理中标记。

        会话可能已被并发删除，释放时静默忽略缺失条目。
        """
        async with self._lock:
            entry = self._sessions.get(session_id)
            if entry is None:
                return
            entry.in_flight = False
            entry.last_access = time.monotonic()

    async def delete(self, session_id: str) -> bool:
        """删除指定会话。

        Args:
            session_id: 要删除的会话 ID。

        Returns:
            True 表示成功删除，False 表示会话不存在。
        """
        async with self._lock:
            if session_id in self._sessions:
                if self._sessions[session_id].in_flight:
                    raise SessionBusyError(
                        f"会话 '{session_id}' 正在处理中，暂无法删除。"
                    )
                del self._sessions[session_id]
                logger.info("已删除会话 %s", session_id)
                return True
            return False

    async def cleanup_expired(self, now: float | None = None) -> int:
        """清理超过 TTL 的空闲会话。

        Args:
            now: 当前时间戳（monotonic），默认使用 time.monotonic()。
                 允许外部注入以便测试。

        Returns:
            被清理的会话数量。
        """
        if now is None:
            now = time.monotonic()

        async with self._lock:
            expired_ids = [
                sid
                for sid, entry in self._sessions.items()
                if (not entry.in_flight)
                and (now - entry.last_access) > self._ttl_seconds
            ]
            for sid in expired_ids:
                del self._sessions[sid]

            if expired_ids:
                logger.info("已清理 %d 个过期会话", len(expired_ids))
            return len(expired_ids)

    @property
    def active_count(self) -> int:
        """当前活跃会话数量（非线程安全，仅用于监控/日志）。"""
        return len(self._sessions)
