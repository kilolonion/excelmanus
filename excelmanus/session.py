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
    def _create_memory_components(
        self,
    ) -> tuple["PersistentMemory | None", "MemoryExtractor | None"]:
        """根据 config.memory_enabled 创建持久记忆组件。

        memory_enabled 为 False 时返回 (None, None)，跳过所有记忆操作。
        使用局部导入避免循环依赖。
        """
        if not self._config.memory_enabled:
            return None, None

        from excelmanus.persistent_memory import PersistentMemory
        from excelmanus.memory_extractor import MemoryExtractor

        import openai

        persistent_memory = PersistentMemory(
            memory_dir=self._config.memory_dir,
            auto_load_lines=self._config.memory_auto_load_lines,
        )
        client = openai.AsyncOpenAI(
            api_key=self._config.api_key,
            base_url=self._config.base_url,
        )
        memory_extractor = MemoryExtractor(
            client=client,
            model=self._config.model,
        )
        return persistent_memory, memory_extractor


    async def acquire_for_chat(
        self, session_id: str | None
    ) -> tuple[str, AgentEngine]:
        """获取会话并标记为处理中。

        同一会话在同一时刻仅允许一个请求执行。
        """
        created = False
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
            persistent_memory, memory_extractor = self._create_memory_components()
            engine = AgentEngine(
                config=self._config,
                registry=self._registry,
                skill_router=self._skill_router,
                persistent_memory=persistent_memory,
                memory_extractor=memory_extractor,
            )
            self._sessions[new_id] = _SessionEntry(
                engine=engine,
                last_access=now,
                in_flight=True,
            )
            created = True
            logger.info("创建新会话并加锁 %s（当前总数: %d）", new_id, len(self._sessions))

        # 初始化 MCP 连接（失败不影响会话创建）；放在锁外避免阻塞并发请求。
        if created:
            try:
                await engine.initialize_mcp()
            except BaseException:
                logger.warning("会话 %s MCP 初始化失败，已跳过", new_id, exc_info=True)
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

        删除前会提取会话记忆并持久化（在锁外执行，避免长时间持有锁）。

        Args:
            session_id: 要删除的会话 ID。

        Returns:
            True 表示成功删除，False 表示会话不存在。
        """
        engine: AgentEngine | None = None
        async with self._lock:
            if session_id in self._sessions:
                if self._sessions[session_id].in_flight:
                    raise SessionBusyError(
                        f"会话 '{session_id}' 正在处理中，暂无法删除。"
                    )
                engine = self._sessions[session_id].engine
                del self._sessions[session_id]
                logger.info("已删除会话 %s", session_id)

        if engine is not None:
            try:
                await engine.extract_and_save_memory()
            except Exception:
                logger.warning("会话 %s 记忆提取失败", session_id, exc_info=True)
            try:
                await engine.shutdown_mcp()
            except Exception:
                logger.warning("会话 %s MCP 关闭失败", session_id, exc_info=True)
            return True
        return False

    async def cleanup_expired(self, now: float | None = None) -> int:
        """清理超过 TTL 的空闲会话。

        清理前会提取每个过期会话的记忆并持久化（在锁外执行）。

        Args:
            now: 当前时间戳（monotonic），默认使用 time.monotonic()。
                 允许外部注入以便测试。

        Returns:
            被清理的会话数量。
        """
        if now is None:
            now = time.monotonic()

        expired_engines: list[tuple[str, AgentEngine]] = []
        async with self._lock:
            expired_ids = [
                sid
                for sid, entry in self._sessions.items()
                if (not entry.in_flight)
                and (now - entry.last_access) > self._ttl_seconds
            ]
            for sid in expired_ids:
                expired_engines.append((sid, self._sessions[sid].engine))
                del self._sessions[sid]

            if expired_ids:
                logger.info("已清理 %d 个过期会话", len(expired_ids))
            count = len(expired_ids)

        # 在锁外逐个提取记忆并关闭 MCP，避免长时间持有锁
        for sid, engine in expired_engines:
            try:
                await engine.extract_and_save_memory()
            except Exception:
                logger.warning("过期会话 %s 记忆提取失败", sid, exc_info=True)
            try:
                await engine.shutdown_mcp()
            except Exception:
                logger.warning("过期会话 %s MCP 关闭失败", sid, exc_info=True)

        return count

    @property
    def active_count(self) -> int:
        """当前活跃会话数量（非线程安全，仅用于监控/日志）。"""
        return len(self._sessions)
