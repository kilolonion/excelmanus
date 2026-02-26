"""会话管理模块：并发安全的会话容器，支持 TTL 与上限控制。"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from dataclasses import replace
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from excelmanus.config import ExcelManusConfig
from excelmanus.engine import AgentEngine
from excelmanus.logger import get_logger
from excelmanus.mcp.manager import MCPManager
from excelmanus.skillpacks import SkillRouter
from excelmanus.user_context import UserContext
from excelmanus.user_scope import UserScope
from excelmanus.workspace import IsolatedWorkspace, SandboxConfig

from excelmanus.conversation_persistence import ConversationPersistence

if __import__("typing").TYPE_CHECKING:
    from excelmanus.chat_history import ChatHistoryStore
    from excelmanus.database import Database

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
class _PersistenceSnapshot:
    """release_for_chat 在锁内捕获的消息快照，用于锁外安全持久化。"""

    messages: list[dict]
    snapshot_index: int
    turn: int
    user_id: str | None
    new_snapshot_index: int = 0  # 持久化后应设置的新 snapshot index


@dataclass
class _SessionEntry:
    """单个会话的内部记录。"""

    engine: AgentEngine
    last_access: float
    in_flight: bool = field(default=False)
    user_id: str | None = field(default=None)
    user_ctx: UserContext | None = field(default=None)
    scope: UserScope | None = field(default=None)


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
        shared_mcp_manager: MCPManager | None = None,
        chat_history: ChatHistoryStore | None = None,
        database: "Database | None" = None,
        config_store: Any = None,
    ) -> None:
        self._max_sessions = max_sessions
        self._ttl_seconds = ttl_seconds
        self._config = config
        self._registry = registry
        self._skill_router = skill_router
        self._shared_mcp_manager = shared_mcp_manager
        self._chat_history = chat_history
        self._conv_persistence: ConversationPersistence | None = (
            ConversationPersistence(chat_history) if chat_history is not None else None
        )
        self._database = database
        self._config_store = config_store
        self._mcp_initialized = False
        self._mcp_init_lock = asyncio.Lock()
        self._sessions: dict[str, _SessionEntry] = {}
        self._lock = asyncio.Lock()
        self._cleanup_task: asyncio.Task[None] | None = None
        self._cleanup_task_lock = asyncio.Lock()
        # Docker 沙盒配置在启动时解析一次，注入到各工作区中。
        self._sandbox_config = SandboxConfig(
            docker_enabled=bool(
                os.environ.get("EXCELMANUS_DOCKER_SANDBOX", "").strip().lower()
                in ("1", "true", "yes")
            ),
        )

    @property
    def database(self) -> "Database | None":
        """底层 Database 实例（只读）。"""
        return self._database

    @property
    def chat_history(self) -> "ChatHistoryStore | None":
        """底层 ChatHistoryStore 实例（只读）。"""
        return self._chat_history

    def reset_mcp_initialized(self) -> None:
        """MCP 热重载后重置初始化标志。"""
        self._mcp_initialized = False

    async def broadcast_model_capabilities(
        self, model: str, caps: Any
    ) -> None:
        """向所有使用指定模型的活跃会话广播能力更新（锁保护）。"""
        async with self._lock:
            for entry in self._sessions.values():
                if entry.engine.current_model == model:
                    entry.engine.set_model_capabilities(caps)

    async def broadcast_aux_config(
        self,
        *,
        aux_model: str | None = None,
        aux_api_key: str | None = None,
        aux_base_url: str | None = None,
    ) -> None:
        """向所有活跃会话广播 AUX 配置变更（锁保护）。"""
        async with self._lock:
            for entry in self._sessions.values():
                entry.engine.update_aux_config(
                    aux_model=aux_model,
                    aux_api_key=aux_api_key,
                    aux_base_url=aux_base_url,
                )

    def set_sandbox_docker_enabled(self, enabled: bool) -> None:
        """更新 Docker 沙盒开关（由 API lifespan 调用）。

        同时同步到所有活跃会话，使已有会话无需重建即可生效。
        """
        self._sandbox_config = SandboxConfig(docker_enabled=enabled)
        for entry in self._sessions.values():
            engine = entry.engine
            ws = engine.workspace
            ws.sandbox_config = self._sandbox_config
            engine.sandbox_env = ws.create_sandbox_env(
                transaction=engine.transaction,
            )

    def notify_file_deleted(self, file_path: str) -> None:
        """W4: 通知所有活跃 session 文件已被删除，清理 staging 条目。"""
        for entry in self._sessions.values():
            try:
                _reg = entry.engine.file_registry
                if _reg is not None and _reg.has_versions:
                    _reg.remove_staging_for_path(file_path)
            except Exception:
                pass

    def notify_file_renamed(self, old_path: str, new_path: str) -> None:
        """W5: 通知所有活跃 session 文件已被重命名，更新 staging 条目。"""
        for entry in self._sessions.values():
            try:
                _reg = entry.engine.file_registry
                if _reg is not None and _reg.has_versions:
                    _reg.rename_staging_path(old_path, new_path)
            except Exception:
                pass

    def _resolve_user_config_store(self, user_id: str | None) -> Any:
        """返回用户级 ConfigStore（用于 active_model 等偏好）。"""
        if self._database is None:
            return self._config_store
        try:
            from excelmanus.stores.config_store import UserConfigStore
            return UserConfigStore(self._database.conn, user_id=user_id)
        except Exception:
            return self._config_store

    @staticmethod
    def cleanup_interval_from_ttl(ttl_seconds: int) -> int:
        """根据 TTL 计算清理间隔，确保小 TTL 场景及时清理。"""
        return max(1, min(60, ttl_seconds // 2 if ttl_seconds > 1 else 1))

    async def _background_cleanup_loop(self, interval_seconds: int) -> None:
        """后台协程：定期清理过期会话。"""
        while True:
            await asyncio.sleep(interval_seconds)
            try:
                cleaned = await self.cleanup_expired()
                if cleaned:
                    logger.info("定期清理：已清理 %d 个过期会话", cleaned)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("定期清理异常", exc_info=True)

    async def start_background_cleanup(
        self, interval_seconds: int | None = None
    ) -> None:
        """启动后台 TTL 清理任务（幂等）。"""
        interval = (
            interval_seconds
            if interval_seconds is not None
            else self.cleanup_interval_from_ttl(self._ttl_seconds)
        )
        if interval <= 0:
            raise ValueError("interval_seconds 必须为正整数。")

        async with self._cleanup_task_lock:
            task = self._cleanup_task
            if task is not None and task.done():
                try:
                    task.exception()
                except asyncio.CancelledError:
                    pass
                except Exception:
                    logger.warning("上一轮定期清理任务异常退出", exc_info=True)
                self._cleanup_task = None
                task = None

            if task is not None:
                return

            self._cleanup_task = asyncio.create_task(
                self._background_cleanup_loop(interval)
            )
            logger.info("已启动会话定期清理任务（间隔: %d 秒）", interval)

    async def stop_background_cleanup(self) -> None:
        """停止后台 TTL 清理任务（幂等）。"""
        task: asyncio.Task[None] | None = None
        async with self._cleanup_task_lock:
            task = self._cleanup_task
            self._cleanup_task = None

        if task is None:
            return

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.warning("停止会话定期清理任务时发生异常", exc_info=True)

    def _create_memory_components(
        self,
        *,
        user_id: str | None = None,
        scope: UserScope | None = None,
    ) -> tuple["PersistentMemory | None", "MemoryExtractor | None"]:
        """根据 config.memory_enabled 创建持久记忆组件。

        memory_enabled 为 False 时返回 (None, None)，跳过所有记忆操作。
        使用局部导入避免循环依赖。

        优先使用 scope（UserScope）创建 MemoryStore，回退到裸 user_id。
        """
        if not self._config.memory_enabled:
            return None, None

        from excelmanus.persistent_memory import PersistentMemory
        from excelmanus.memory_extractor import MemoryExtractor
        from excelmanus.providers import create_client

        if self._database is not None:
            if scope is not None:
                backend: Any = scope.memory_store()
            else:
                from excelmanus.stores.memory_store import MemoryStore
                backend = MemoryStore(self._database, user_id=user_id)
        else:
            from excelmanus.stores.file_memory_backend import FileMemoryBackend
            backend = FileMemoryBackend(
                memory_dir=self._config.memory_dir,
                auto_load_lines=self._config.memory_auto_load_lines,
            )

        persistent_memory = PersistentMemory(
            backend=backend,
            auto_load_lines=self._config.memory_auto_load_lines,
        )
        # 记忆提取优先使用 aux 模型，节省主模型 token
        mem_model = self._config.aux_model or self._config.model
        mem_api_key = self._config.aux_api_key or self._config.api_key
        mem_base_url = self._config.aux_base_url or self._config.base_url
        client = create_client(
            api_key=mem_api_key,
            base_url=mem_base_url,
        )
        memory_extractor = MemoryExtractor(
            client=client,
            model=mem_model,
        )
        return persistent_memory, memory_extractor

    async def ensure_mcp_initialized(self) -> None:
        """初始化共享 MCP 管理器（仅执行一次）。"""
        if self._shared_mcp_manager is None or self._mcp_initialized:
            return
        async with self._mcp_init_lock:
            if self._mcp_initialized:
                return
            await self._shared_mcp_manager.initialize(self._registry)
            self._mcp_initialized = True

    def _create_engine_with_history(
        self,
        session_id: str,
        history_messages: list[dict] | None = None,
        *,
        user_id: str | None = None,
        user_ctx: UserContext | None = None,
        scope: UserScope | None = None,
    ) -> AgentEngine:
        """创建 AgentEngine 并可选地注入历史消息。"""
        # 解析工作区：认证启用时按用户隔离，否则共享。
        auth_enabled = user_id is not None
        isolated_ws = IsolatedWorkspace.resolve(
            self._config.workspace_root,
            user_id=user_id,
            auth_enabled=auth_enabled,
            sandbox_config=self._sandbox_config,
            transaction_enabled=self._config.backup_enabled,
        )
        engine_config = self._config
        if user_id is not None:
            engine_config = replace(
                self._config, workspace_root=str(isolated_ws.root_dir)
            )
        persistent_memory, memory_extractor = self._create_memory_components(
            user_id=user_id, scope=scope,
        )
        engine = AgentEngine(
            config=engine_config,
            registry=self._registry,
            skill_router=self._skill_router,
            persistent_memory=persistent_memory,
            memory_extractor=memory_extractor,
            mcp_manager=self._shared_mcp_manager,
            own_mcp_manager=self._shared_mcp_manager is None,
            database=self._database,
            workspace=isolated_ws,
            user_id=user_id,
        )
        if history_messages:
            engine.inject_history(history_messages)
        # 从用户级配置恢复激活模型（隔离：每个用户独立的 active_model）
        _user_config = self._resolve_user_config_store(user_id)
        if _user_config is not None:
            active_name = _user_config.get_active_model()
            if active_name:
                try:
                    engine.switch_model(active_name)
                except Exception:
                    logger.debug("恢复激活模型 %s 失败", active_name, exc_info=True)
        # 从数据库加载模型能力探测缓存
        if self._database is not None:
            try:
                from excelmanus.model_probe import load_capabilities
                caps = load_capabilities(
                    self._database,
                    engine.current_model,
                    engine.active_base_url,
                )
                if caps is not None:
                    engine.set_model_capabilities(caps)
            except Exception:
                logger.debug("加载模型能力缓存失败", exc_info=True)
        engine.start_registry_scan()
        return engine


    async def acquire_for_chat(
        self, session_id: str | None, *, user_id: str | None = None,
    ) -> tuple[str, AgentEngine]:
        """获取会话并标记为处理中。

        同一会话在同一时刻仅允许一个请求执行。
        支持从 SQLite 历史记录按需恢复会话。
        当 user_id 不为 None 时，会验证会话归属并记录用户 ID。
        """
        created = False
        restored = False
        async with self._lock:
            now = time.monotonic()
            if session_id is not None and session_id in self._sessions:
                entry = self._sessions[session_id]
                if user_id is not None and entry.user_id != user_id:
                    raise SessionNotFoundError(
                        f"会话 '{session_id}' 不属于当前用户。"
                    )
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

            # 检查 SQLite 中是否有历史会话，按需恢复（含 user_id 归属校验）
            history_messages: list[dict] | None = None
            if (
                session_id is not None
                and self._chat_history is not None
            ):
                if user_id is not None:
                    if not self._chat_history.session_owned_by(session_id, user_id):
                        # 会话存在但不属于当前用户，视为不存在（不泄露）
                        pass
                    else:
                        history_messages = self._chat_history.load_messages(session_id)
                        restored = True
                elif self._chat_history.session_exists(session_id):
                    history_messages = self._chat_history.load_messages(session_id)
                    restored = True

            new_id = session_id if session_id is not None else str(uuid.uuid4())
            # 创建 UserScope（统一的用户作用域）
            scope: UserScope | None = None
            if self._database is not None:
                scope = UserScope.create(
                    user_id, self._database, self._config.workspace_root
                )
            engine = self._create_engine_with_history(
                new_id,
                history_messages,
                user_id=user_id,
                scope=scope,
            )
            engine._session_id = new_id
            engine.set_message_snapshot_index(
                len(history_messages) if history_messages else 0
            )
            self._sessions[new_id] = _SessionEntry(
                engine=engine,
                last_access=now,
                in_flight=True,
                user_id=user_id,
                scope=scope,
            )
            created = True
            if restored:
                logger.info(
                    "从历史恢复会话 %s（%d 条消息，当前总数: %d）",
                    new_id, len(history_messages or []), len(self._sessions),
                )
            else:
                logger.info("创建新会话并加锁 %s（当前总数: %d）", new_id, len(self._sessions))
                # F2: 新建（非恢复）会话立即写入 SQLite，防止 TTL 清理或进程重启导致会话丢失
                if self._chat_history is not None:
                    try:
                        if not self._chat_history.session_exists(new_id, user_id=user_id):
                            self._chat_history.create_session(new_id, "", user_id=user_id)
                    except Exception:
                        logger.warning("新建会话 %s 立即持久化失败", new_id, exc_info=True)

        # 初始化 MCP 连接（失败不影响会话创建）；放在锁外避免阻塞并发请求。
        if created:
            try:
                if self._shared_mcp_manager is None:
                    await engine.initialize_mcp()
                else:
                    await self.ensure_mcp_initialized()
                    engine.sync_mcp_auto_approve()
            except BaseException as exc:
                # initialize_mcp 失败不应中断主请求；若内部抛出 CancelledError，
                # 需要显式清除当前任务的取消状态，避免后续 await 被级联取消。
                if isinstance(exc, asyncio.CancelledError):
                    task = asyncio.current_task()
                    if task is not None:
                        while task.cancelling():
                            task.uncancel()
                logger.warning(
                    "会话 %s MCP 初始化失败，已跳过",
                    new_id,
                    exc_info=True,
                )
        return new_id, engine

    async def release_for_chat(self, session_id: str) -> None:
        """释放会话处理中标记，并将新增消息持久化到 SQLite。

        会话可能已被并发删除，释放时静默忽略缺失条目。

        竞态修复：在锁内捕获消息快照，锁外基于快照持久化，
        避免读取 engine 可变状态时与并发 acquire 冲突。
        """
        snapshot: _PersistenceSnapshot | None = None
        engine_ref: AgentEngine | None = None
        async with self._lock:
            entry = self._sessions.get(session_id)
            if entry is None:
                return
            # 在锁内捕获快照（浅拷贝 messages 列表）
            if self._conv_persistence is not None:
                snapshot = _PersistenceSnapshot(
                    messages=list(entry.engine.raw_messages),
                    snapshot_index=entry.engine.message_snapshot_index,
                    turn=entry.engine.session_turn,
                    user_id=entry.user_id,
                    new_snapshot_index=len(entry.engine.raw_messages),
                )
                engine_ref = entry.engine
            entry.in_flight = False
            entry.last_access = time.monotonic()

        # 锁外基于快照持久化，不再读取 engine 可变状态
        if snapshot is not None and self._conv_persistence is not None:
            try:
                self._conv_persistence.sync_from_snapshot(
                    session_id, snapshot
                )
                # 更新 engine 的 snapshot index（原子赋值，安全）
                if engine_ref is not None:
                    engine_ref.set_message_snapshot_index(
                        snapshot.new_snapshot_index
                    )
            except Exception:
                logger.warning("会话 %s 消息持久化失败", session_id, exc_info=True)

    def flush_messages_sync(self, session_id: str) -> None:
        """同步增量持久化会话消息（供 SSE 事件回调在流式传输中间调用）。

        此方法直接读取 engine 可变状态，但由于 SSE 事件回调在 engine.chat()
        内部同步触发，与 engine 的消息修改在同一协程内，不存在并发问题。
        """
        if self._conv_persistence is None:
            return
        entry = self._sessions.get(session_id)
        if entry is None:
            return
        try:
            self._conv_persistence.sync_new_messages(
                session_id, entry.engine, user_id=entry.user_id,
            )
        except Exception:
            logger.debug("会话 %s 中间持久化失败", session_id, exc_info=True)

    async def delete(self, session_id: str, *, user_id: str | None = None) -> bool:
        """删除指定会话。

        删除前会提取会话记忆并持久化（在锁外执行，避免长时间持有锁）。
        当 user_id 非空时，会校验会话归属，非归属用户视为不存在。

        Args:
            session_id: 要删除的会话 ID。
            user_id: 当前用户 ID，用于归属校验（多租户场景）。

        Returns:
            True 表示成功删除，False 表示会话不存在或无权访问。
        """
        engine: AgentEngine | None = None
        async with self._lock:
            if session_id in self._sessions:
                entry = self._sessions[session_id]
                if user_id is not None and entry.user_id != user_id:
                    return False  # 不属于当前用户，视为不存在
                if entry.in_flight:
                    raise SessionBusyError(
                        f"会话 '{session_id}' 正在处理中，暂无法删除。"
                    )
                engine = entry.engine
                del self._sessions[session_id]
                logger.info("已删除会话 %s", session_id)

        if engine is not None:
            try:
                await engine.extract_and_save_memory()
            except Exception:
                logger.warning("会话 %s 记忆提取失败", session_id, exc_info=True)
            if self._shared_mcp_manager is None:
                try:
                    await engine.shutdown_mcp()
                except Exception:
                    logger.warning("会话 %s MCP 关闭失败", session_id, exc_info=True)
            # 同时从 SQLite 删除
            if self._chat_history is not None:
                try:
                    self._chat_history.delete_session(session_id)
                except Exception:
                    logger.warning("会话 %s SQLite 删除失败", session_id, exc_info=True)
            return True

        # 仅存在于 SQLite 中的历史会话（需校验归属）
        if self._chat_history is not None:
            if user_id is not None:
                if not self._chat_history.session_owned_by(session_id, user_id):
                    return False
            elif not self._chat_history.session_exists(session_id):
                return False
            self._chat_history.delete_session(session_id)
            return True
        return False
    async def clear_session(self, session_id: str) -> bool:
        """清除会话的对话历史，但保留会话本身。

        清除引擎内存 + SQLite 消息记录。

        Args:
            session_id: 要清除的会话 ID。

        Returns:
            True 表示成功清除，False 表示会话不存在。
        """
        engine: AgentEngine | None = None
        async with self._lock:
            entry = self._sessions.get(session_id)
            if entry is not None:
                if entry.in_flight:
                    raise SessionBusyError(
                        f"会话 '{session_id}' 正在处理中，暂无法清除。"
                    )
                engine = entry.engine

        if engine is not None:
            engine.clear_memory()
            if self._conv_persistence is not None:
                self._conv_persistence.clear(session_id, engine)
            logger.info("已清除会话 %s 引擎内存", session_id)
            return True

        # 仅存在于 SQLite 中的历史会话
        if self._conv_persistence is not None and self._chat_history is not None:
            if self._chat_history.session_exists(session_id):
                self._conv_persistence.clear(session_id)
                return True
        return False

    async def clear_all_sessions(self, *, user_id: str | None = None) -> tuple[int, int]:
        """清空会话及消息。若有会话正在处理中则抛出 SessionBusyError。

        当 user_id 非空时，仅清空该用户的会话；否则清空全部。

        Returns:
            (删除的会话数, 删除的消息数)
        """
        active_engines: list[tuple[str, AgentEngine]] = []
        async with self._lock:
            targets = {
                sid: entry for sid, entry in self._sessions.items()
                if user_id is None or entry.user_id == user_id
            }
            for sid, entry in targets.items():
                if entry.in_flight:
                    raise SessionBusyError(
                        f"会话 '{sid}' 正在处理中，请完成后重试。"
                    )
            active_engines = [(sid, e.engine) for sid, e in targets.items()]
            for sid in targets:
                del self._sessions[sid]

        for sid, engine in active_engines:
            try:
                await engine.extract_and_save_memory()
            except Exception:
                logger.warning("会话 %s 记忆提取失败", sid, exc_info=True)
            if self._shared_mcp_manager is None:
                try:
                    await engine.shutdown_mcp()
                except Exception:
                    logger.warning("会话 %s MCP 关闭失败", sid, exc_info=True)

        sess_count, msg_count = 0, 0
        if self._chat_history is not None:
            try:
                sess_count, msg_count = self._chat_history.delete_all_sessions(
                    user_id=user_id,
                )
            except Exception:
                logger.warning("清空 SQLite 会话失败", exc_info=True)
        return sess_count, msg_count

    async def archive_session(
        self, session_id: str, archive: bool = True, *, user_id: str | None = None
    ) -> bool:
        """归档或取消归档会话。

        对于内存中的活跃会话，仅更新 SQLite 状态（不影响运行中会话）。
        对于仅存在于 SQLite 中的历史会话，直接更新状态。
        当 user_id 非空时，会校验会话归属。

        Args:
            session_id: 要归档/取消归档的会话 ID。
            archive: True 表示归档，False 表示取消归档。
            user_id: 当前用户 ID，用于归属校验。

        Returns:
            True 表示成功更新，False 表示会话不存在或无权访问。
        """
        new_status = "archived" if archive else "active"

        # R2: 在锁内捕获 entry 引用，避免锁释放后并发删除导致 None
        entry: _SessionEntry | None = None
        async with self._lock:
            in_memory = session_id in self._sessions
            if in_memory:
                entry = self._sessions[session_id]
                if user_id is not None and entry.user_id != user_id:
                    return False  # 不属于当前用户

        if in_memory and entry is not None:
            # 活跃会话：先持久化到 SQLite（确保记录存在），再更新状态
            if self._chat_history is not None:
                if self._conv_persistence is not None:
                    self._conv_persistence.sync_new_messages(
                        session_id, entry.engine, user_id=entry.user_id
                    )
                self._chat_history.update_session(session_id, status=new_status)
                return True
            return False

        # 仅存在于 SQLite 中的历史会话（需校验归属）
        if self._chat_history is not None:
            if user_id is not None:
                if not self._chat_history.session_owned_by(session_id, user_id):
                    return False
            elif not self._chat_history.session_exists(session_id):
                return False
            self._chat_history.update_session(session_id, status=new_status)
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
            if self._shared_mcp_manager is None:
                try:
                    await engine.shutdown_mcp()
                except Exception:
                    logger.warning("过期会话 %s MCP 关闭失败", sid, exc_info=True)

        return count

    async def shutdown(self) -> None:
        """关闭 SessionManager：清空会话并收尾 MCP 生命周期。"""
        await self.stop_background_cleanup()

        active_engines: list[tuple[str, AgentEngine]] = []
        async with self._lock:
            active_engines = [
                (sid, entry.engine)
                for sid, entry in self._sessions.items()
            ]
            self._sessions.clear()

        for sid, engine in active_engines:
            try:
                await engine.extract_and_save_memory()
            except Exception:
                logger.warning("会话 %s 关闭时记忆提取失败", sid, exc_info=True)
            if self._shared_mcp_manager is None:
                try:
                    await engine.shutdown_mcp()
                except Exception:
                    logger.warning("会话 %s 关闭时 MCP 关闭失败", sid, exc_info=True)

        if self._shared_mcp_manager is not None and self._mcp_initialized:
            try:
                await self._shared_mcp_manager.shutdown()
            except Exception:
                logger.warning("共享 MCP 管理器关闭失败", exc_info=True)
            finally:
                self._mcp_initialized = False

    def get_engine(self, session_id: str, *, user_id: str | None = None) -> "AgentEngine | None":
        """同步获取指定会话的 AgentEngine（无锁，仅用于只读查询）。

        当 user_id 非空时，校验会话归属；不匹配则返回 None。
        """
        entry = self._sessions.get(session_id)
        if entry is None:
            return None
        if user_id is not None and entry.user_id != user_id:
            return None
        return entry.engine

    def is_session_in_flight(self, session_id: str) -> bool:
        """W10: 检查会话是否正在处理中（用于防止 backup apply 竞态）。"""
        entry = self._sessions.get(session_id)
        return entry.in_flight if entry is not None else False

    def session_exists(self, session_id: str) -> bool:
        """检查会话是否存在（内存或 SQLite 历史）。"""
        if session_id in self._sessions:
            return True
        if self._chat_history is not None and self._chat_history.session_exists(session_id):
            return True
        return False

    def can_restore_session(
        self, session_id: str, *, user_id: str | None = None
    ) -> bool:
        """检查会话是否可从 SQLite 恢复（内存中不存在时）。"""
        if session_id in self._sessions:
            return True
        if user_id is not None and self._chat_history is not None:
            return self._chat_history.session_owned_by(session_id, user_id)
        return self.session_exists(session_id)

    async def get_or_restore_engine(
        self,
        session_id: str,
        *,
        user_id: str | None = None,
    ) -> AgentEngine | None:
        """获取引擎，若会话仅在 SQLite 中存在则懒恢复。

        不改变 in_flight 状态，恢复后立即释放。
        适用于只读查询场景（status、compact、memory extract、registry scan）。
        """
        engine = self.get_engine(session_id, user_id=user_id)
        if engine is not None:
            return engine

        if not self.can_restore_session(session_id, user_id=user_id):
            return None

        acquired_session_id: str | None = None
        try:
            acquired_session_id, engine = await self.acquire_for_chat(
                session_id, user_id=user_id
            )
        except SessionBusyError:
            engine = self.get_engine(session_id, user_id=user_id)
        except Exception:
            logger.debug("懒恢复会话失败: %s", session_id, exc_info=True)
            engine = None
        finally:
            if acquired_session_id is not None:
                await self.release_for_chat(acquired_session_id)
        return engine

    def get_any_engine(self) -> "AgentEngine | None":
        """同步获取任一活跃会话的 AgentEngine（无锁，仅用于只读查询）。"""
        for entry in self._sessions.values():
            return entry.engine
        return None

    async def get_active_count(self) -> int:
        """获取当前活跃会话数量（锁保护）。"""
        async with self._lock:
            return len(self._sessions)

    async def get_user_active_count(self, user_id: str) -> int:
        """获取指定用户的活跃会话数量。"""
        async with self._lock:
            return sum(1 for e in self._sessions.values() if e.user_id == user_id)

    async def list_sessions(
        self, include_archived: bool = False, *, user_id: str | None = None
    ) -> list[dict]:
        """列出所有会话的摘要信息（内存活跃 + SQLite 历史合并）。

        当 user_id 非空时，仅返回该用户的会话（内存 + DB 均按 user_id 过滤）。
        """
        in_memory_ids: set[str] = set()
        results: list[dict] = []
        now = time.monotonic()

        async with self._lock:
            for sid, entry in self._sessions.items():
                if user_id is not None and entry.user_id != user_id:
                    continue
                in_memory_ids.add(sid)
                engine = entry.engine
                msg_count = len(engine.raw_messages) if hasattr(engine, "raw_messages") else 0
                title = ""
                if msg_count > 0:
                    for msg in engine.raw_messages:
                        if isinstance(msg, dict) and msg.get("role") == "user":
                            content = msg.get("content", "")
                            if isinstance(content, str):
                                title = content[:80]
                            break
                # 将 monotonic last_access 转换为 wall-clock ISO 时间戳
                # monotonic 不可直接转 wall-clock，需用当前两者的差值推算
                wall_updated = time.time() - (now - entry.last_access)
                updated_at_iso = datetime.fromtimestamp(
                    wall_updated, tz=timezone.utc
                ).isoformat()
                results.append({
                    "id": sid,
                    "title": title or f"会话 {sid[:8]}",
                    "message_count": msg_count,
                    "in_flight": entry.in_flight,
                    "status": "active",
                    "updated_at": updated_at_iso,
                })

        # 合并 SQLite 中的历史会话（排除已在内存中的）
        if self._chat_history is not None:
            try:
                db_sessions = self._chat_history.list_sessions(
                    include_archived=include_archived,
                    user_id=user_id,
                )
                for ds in db_sessions:
                    if ds["id"] not in in_memory_ids:
                        results.append({
                            "id": ds["id"],
                            "title": ds.get("title") or f"会话 {ds['id'][:8]}",
                            "message_count": ds.get("message_count", 0),
                            "in_flight": False,
                            "status": ds.get("status", "active"),
                            "updated_at": ds.get("updated_at", ""),
                        })
            except Exception:
                logger.warning("合并 SQLite 会话列表失败", exc_info=True)

        # F6: 全局按 updated_at 降序排序，保证前端收到的列表顺序一致
        results.sort(key=lambda s: s.get("updated_at", ""), reverse=True)
        return results

    async def get_session_detail(
        self, session_id: str, *, user_id: str | None = None
    ) -> dict:
        """获取会话详情含消息历史。当 user_id 非空时，会校验会话归属。"""
        async with self._lock:
            entry = self._sessions.get(session_id)
            if entry is not None:
                if user_id is not None and entry.user_id != user_id:
                    raise SessionNotFoundError(f"会话 '{session_id}' 不存在。")
                engine = entry.engine
                messages = []
                if hasattr(engine, "raw_messages"):
                    messages = list(engine.raw_messages)

                # 序列化待处理的审批/问题状态，供前端刷新后恢复
                pending_approval_data = None
                if engine.has_pending_approval():
                    pa = engine.current_pending_approval()
                    if pa is not None:
                        from excelmanus.tools.policy import (
                            get_tool_risk_level,
                            sanitize_approval_args_summary,
                        )
                        pending_approval_data = {
                            "approval_id": pa.approval_id,
                            "tool_name": pa.tool_name,
                            "risk_level": get_tool_risk_level(pa.tool_name),
                            "args_summary": sanitize_approval_args_summary(pa.arguments),
                        }

                pending_question_data = None
                if engine.has_pending_question():
                    pq = engine.current_pending_question()
                    if pq is not None:
                        pending_question_data = {
                            "id": pq.question_id,
                            "header": pq.header,
                            "text": pq.text,
                            "options": [
                                {"label": o.label, "description": o.description}
                                for o in pq.options
                            ],
                            "multi_select": pq.multi_select,
                        }

                # 序列化最近路由结果，供前端刷新后重建路由状态 block
                last_route_data = None
                lr = engine.last_route_result
                if lr is not None:
                    last_route_data = {
                        "route_mode": lr.route_mode,
                        "skills_used": list(lr.skills_used),
                        "tool_scope": list(lr.tool_scope) if lr.tool_scope else [],
                    }

                return {
                    "id": session_id,
                    "message_count": len(messages),
                    "in_flight": entry.in_flight,
                    "messages": messages,
                    "full_access_enabled": engine.full_access_enabled,
                    "chat_mode": getattr(engine, '_current_chat_mode', 'write'),
                    "current_model": engine.current_model,
                    "current_model_name": engine.current_model_name,
                    "vision_capable": engine.is_vision_capable or engine.vlm_enhance_available,
                    "pending_approval": pending_approval_data,
                    "pending_question": pending_question_data,
                    "last_route": last_route_data,
                }

        # 回退到 SQLite 历史（需校验归属）
        if self._chat_history is not None:
            if user_id is not None:
                if not self._chat_history.session_owned_by(session_id, user_id):
                    raise SessionNotFoundError(f"会话 '{session_id}' 不存在。")
            elif not self._chat_history.session_exists(session_id):
                raise SessionNotFoundError(f"会话 '{session_id}' 不存在。")
            messages = self._chat_history.load_messages(session_id)
            return {
                "id": session_id,
                "message_count": len(messages),
                "in_flight": False,
                "messages": messages,
                "full_access_enabled": False,
                "chat_mode": "write",
                "current_model": None,
                "current_model_name": None,
                "vision_capable": False,
                "pending_approval": None,
                "pending_question": None,
                "last_route": None,
            }
        raise SessionNotFoundError(f"会话 '{session_id}' 不存在。")

    async def rollback_session(
        self,
        session_id: str,
        turn_index: int,
        *,
        rollback_files: bool = False,
        new_message: str | None = None,
        resend_mode: bool = False,
        user_id: str | None = None,
    ) -> dict:
        """回退指定会话到目标用户轮次，并与持久化历史保持一致。

        Args:
            resend_mode: 若为 True，目标用户消息将被一并移除（而非保留），
                调用方应随后通过 /chat/stream 发送新消息。此时 new_message
                参数被忽略。
        """
        exists = (
            self._chat_history.session_owned_by(session_id, user_id)
            if user_id is not None and self._chat_history is not None
            else self.session_exists(session_id)
        )
        if not exists:
            raise SessionNotFoundError(f"会话 '{session_id}' 不存在。")

        acquired_session_id, engine = await self.acquire_for_chat(
            session_id, user_id=user_id
        )
        try:
            result = engine.rollback_conversation(
                turn_index,
                rollback_files=rollback_files,
                keep_target=not resend_mode,
            )

            if not resend_mode and new_message is not None and new_message.strip():
                turns = engine.list_user_turns()
                for turn in turns:
                    if turn["index"] == turn_index:
                        engine.replace_user_message(
                            turn["msg_index"], new_message.strip()
                        )
                        break

            if self._conv_persistence is not None:
                self._conv_persistence.reset_after_rollback(
                    acquired_session_id, engine
                )

            return result
        finally:
            await self.release_for_chat(acquired_session_id)

    async def get_session_messages(
        self,
        session_id: str,
        limit: int = 50,
        offset: int = 0,
        *,
        user_id: str | None = None,
    ) -> list[dict]:
        """分页获取会话消息（优先内存，回退 SQLite）。当 user_id 非空时，会校验会话归属。"""
        async with self._lock:
            entry = self._sessions.get(session_id)
            if entry is not None:
                if user_id is not None and entry.user_id != user_id:
                    return []  # 不属于当前用户，返回空
                engine = entry.engine
                msgs = engine.raw_messages
                return msgs[offset: offset + limit]

        # 回退到 SQLite（需校验归属）
        if self._chat_history is not None:
            if user_id is not None:
                if not self._chat_history.session_owned_by(session_id, user_id):
                    return []
            elif not self._chat_history.session_exists(session_id):
                return []
            return self._chat_history.load_messages(session_id, limit=limit, offset=offset)
        return []
