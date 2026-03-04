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

from excelmanus.config import ExcelManusConfig, ModelProfile
from excelmanus.engine import AgentEngine
from excelmanus.logger import get_logger
from excelmanus.mcp.manager import MCPManager
from excelmanus.skillpacks import SkillRouter
from excelmanus.skillpacks.user_skill_service import UserSkillService
from excelmanus.user_context import UserContext
from excelmanus.user_scope import UserScope
from excelmanus.workspace import IsolatedWorkspace, SandboxConfig

from excelmanus.conversation_persistence import ConversationPersistence

if __import__("typing").TYPE_CHECKING:
    from excelmanus.auth.store import UserStore
    from excelmanus.chat_history import ChatHistoryStore
    from excelmanus.database import Database
    from excelmanus.memory_extractor import MemoryExtractor
    from excelmanus.persistent_memory import PersistentMemory

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
    restored_readonly: bool = field(default=False)  # B4: 懒恢复会话标记，使用更短 TTL


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
        user_store: "UserStore | None" = None,
        user_skill_service: UserSkillService | None = None,
    ) -> None:
        self._max_sessions = max_sessions
        self._max_sessions_per_user = config.max_sessions_per_user
        self._ttl_seconds = ttl_seconds
        self._config = config
        self._registry = registry
        self._skill_router = skill_router
        self._user_skill_service = user_skill_service
        self._shared_mcp_manager = shared_mcp_manager
        self._chat_history = chat_history
        self._conv_persistence: ConversationPersistence | None = (
            ConversationPersistence(chat_history) if chat_history is not None else None
        )
        self._database = database
        self._config_store = config_store
        self._user_store = user_store
        self._credential_store = None  # CredentialStore，由 lifespan 注入
        self._credential_resolver = None  # CredentialResolver，由 lifespan 注入
        # 历史会话摘要存储
        self._session_summary_store: Any = None
        if database is not None and config.session_summary_enabled:
            try:
                from excelmanus.stores.session_summary_store import SessionSummaryStore
                self._session_summary_store = SessionSummaryStore(database)
            except Exception:
                logger.debug("SessionSummaryStore 初始化失败", exc_info=True)
        self._mcp_initialized: bool = False
        self._mcp_init_lock = asyncio.Lock()
        self._sessions: dict[str, _SessionEntry] = {}
        self._pending_creates: set[str] = set()  # B2: 正在锁外创建的会话 ID
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

    def set_user_store(self, user_store: Any) -> None:
        """注入 UserStore 实例（用于延迟初始化场景）。"""
        self._user_store = user_store

    def set_credential_store(self, credential_store: Any) -> None:
        """注入 CredentialStore 实例（订阅凭证管理）。"""
        self._credential_store = credential_store

    def set_credential_resolver(self, resolver: Any) -> None:
        """注入 CredentialResolver 实例（运行时凭证解析）。"""
        self._credential_resolver = resolver

    def sync_user_subscription_profiles(
        self,
        engine: AgentEngine,
        user_id: str | None,
    ) -> None:
        """为已有 DB profile 注入用户的订阅 OAuth 凭证。

        统一架构：DB profile 是唯一来源，此方法仅填充运行时 OAuth token。
        对 name 以 ``openai-codex/`` 开头的 profile，用 CredentialStore 中的
        access_token 替换空 api_key，并更新 base_url。
        """
        from excelmanus.auth.providers.openai_codex import OpenAICodexProvider

        if user_id is None or self._credential_store is None:
            return

        try:
            active_cred = self._credential_store.get_active_profile(user_id, "openai-codex")
        except Exception:
            logger.debug("读取用户 Codex 凭证失败", exc_info=True)
            return

        if active_cred is None or not active_cred.access_token:
            return

        api_key, base_url = OpenAICodexProvider().get_api_credential(active_cred.access_token)

        augmented: list[ModelProfile] = []
        changed = False
        for p in engine._config.models:
            if OpenAICodexProvider.is_codex_profile_name(p.name):
                augmented.append(replace(p, api_key=api_key, base_url=base_url))
                changed = True
            else:
                augmented.append(p)

        if changed:
            engine.sync_model_profiles(tuple(augmented))

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
        aux_enabled: bool = True,
        aux_model: str | None = None,
        aux_api_key: str | None = None,
        aux_base_url: str | None = None,
    ) -> None:
        """向所有活跃会话广播 AUX 配置变更（锁保护）。"""
        async with self._lock:
            for entry in self._sessions.values():
                entry.engine.update_aux_config(
                    aux_enabled=aux_enabled,
                    aux_model=aux_model,
                    aux_api_key=aux_api_key,
                    aux_base_url=aux_base_url,
                )

    async def broadcast_model_profiles(self, profiles: tuple) -> None:
        """向所有活跃会话广播模型档案列表变更（锁保护）。"""
        async with self._lock:
            for entry in self._sessions.values():
                entry.engine.sync_model_profiles(profiles)

    async def set_sandbox_docker_enabled(self, enabled: bool) -> None:
        """更新 Docker 沙盒开关（由 API lifespan 调用）。

        同时同步到所有活跃会话，使已有会话无需重建即可生效。
        """
        self._sandbox_config = SandboxConfig(docker_enabled=enabled)
        async with self._lock:
            for entry in self._sessions.values():
                engine = entry.engine
                ws = engine.workspace
                ws.sandbox_config = self._sandbox_config
                engine.sandbox_env = ws.create_sandbox_env(
                    transaction=engine.transaction,
                )

    def notify_file_deleted(self, file_path: str) -> None:
        """W4: 通知活跃 session 文件已被删除，清理 staging 条目。

        ISO-4: 仅通知工作区包含目标文件的会话，避免跨用户干扰。
        """
        entries = list(self._sessions.values())
        for entry in entries:
            try:
                ws_root = str(entry.engine.workspace.root_dir)
                if not file_path.startswith(ws_root):
                    continue
                _reg = entry.engine.file_registry
                if _reg is not None and _reg.has_versions:
                    _reg.remove_staging_for_path(file_path)
            except Exception:
                logger.debug("notify_file_deleted 处理异常", exc_info=True)

    def notify_file_renamed(self, old_path: str, new_path: str) -> None:
        """W5: 通知活跃 session 文件已被重命名，更新 staging 条目。

        ISO-4: 仅通知工作区包含目标文件的会话。
        注意：list() 快照 + try-except 保护确保即使并发删除/清理会话也不会崩溃。
        """
        try:
            entries = list(self._sessions.values())
        except RuntimeError:
            return
        for entry in entries:
            try:
                engine = entry.engine
                if engine is None:
                    continue
                ws_root = str(engine.workspace.root_dir)
                if not old_path.startswith(ws_root):
                    continue
                _reg = engine.file_registry
                if _reg is not None and _reg.has_versions:
                    _reg.rename_staging_path(old_path, new_path)
            except Exception:
                logger.debug("notify_file_renamed 处理异常", exc_info=True)

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
        _mem_protocol = (
            self._config.aux_protocol
            if self._config.aux_enabled and self._config.aux_model
            else self._config.protocol
        )
        client = create_client(
            api_key=mem_api_key,
            base_url=mem_base_url,
            protocol=_mem_protocol,
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
            data_root=self._config.data_root,
        )
        engine_config = self._config
        if user_id is not None:
            overrides: dict[str, Any] = {"workspace_root": str(isolated_ws.root_dir)}
            # 用户自定义 LLM 配置覆盖全局默认值
            if self._user_store is not None:
                user_rec = self._user_store.get_by_id(user_id)
                if user_rec is not None:
                    if user_rec.llm_api_key:
                        overrides["api_key"] = user_rec.llm_api_key
                    if user_rec.llm_base_url:
                        overrides["base_url"] = user_rec.llm_base_url
                    if user_rec.llm_model:
                        overrides["model"] = user_rec.llm_model
                    if any(k in overrides for k in ("api_key", "base_url", "model")):
                        logger.info(
                            "用户 %s 使用自定义 LLM 配置 (model=%s, base_url=%s)",
                            user_id,
                            overrides.get("model", "<inherited>"),
                            overrides.get("base_url", "<inherited>"),
                        )
            # 订阅凭证覆盖：通过 CredentialResolver 通用框架解析 OAuth token
            if self._credential_resolver is not None:
                _target_model = overrides.get("model", self._config.model)
                try:
                    from excelmanus.auth.providers.openai_codex import OpenAICodexProvider
                    if isinstance(_target_model, str) and OpenAICodexProvider.is_codex_profile_name(_target_model):
                        _resolved_model = OpenAICodexProvider.model_from_profile_name(_target_model)
                        if _resolved_model:
                            _target_model = _resolved_model
                            overrides["model"] = _resolved_model
                    _resolved_cred = self._credential_resolver.resolve_sync(user_id, _target_model)
                    if _resolved_cred:
                        overrides["api_key"] = _resolved_cred.api_key
                        if _resolved_cred.base_url:
                            overrides["base_url"] = _resolved_cred.base_url
                        if _resolved_cred.protocol and _resolved_cred.protocol != "openai":
                            overrides["protocol"] = _resolved_cred.protocol
                        logger.info(
                            "用户 %s 使用 %s 订阅凭证 (source=%s)",
                            user_id, _resolved_cred.provider or "unknown", _resolved_cred.source,
                        )
                except Exception:
                    logger.debug("订阅凭证解析失败", exc_info=True)
            engine_config = replace(self._config, **overrides)
        persistent_memory, memory_extractor = self._create_memory_components(
            user_id=user_id, scope=scope,
        )
        # 技能隔离：优先使用 per-user router，回退到全局 router
        if self._user_skill_service is not None:
            _user_skill_router = self._user_skill_service.get_router(user_id)
        else:
            _user_skill_router = self._skill_router
        engine = AgentEngine(
            config=engine_config,
            registry=self._registry,
            skill_router=_user_skill_router,
            persistent_memory=persistent_memory,
            memory_extractor=memory_extractor,
            mcp_manager=self._shared_mcp_manager,
            own_mcp_manager=self._shared_mcp_manager is None,
            database=self._database,
            workspace=isolated_ws,
            user_id=user_id,
        )
        self.sync_user_subscription_profiles(engine, user_id)
        # 注入 CredentialResolver 到引擎，支持 LLM 调用前自动刷新 OAuth token
        if self._credential_resolver is not None:
            engine._credential_resolver = self._credential_resolver
        if history_messages:
            engine.inject_history(history_messages)
            # 恢复 checkpoint（SessionState + TaskStore）
            engine._session_id = session_id
            engine.restore_checkpoint()
        # 从用户级配置恢复激活模型（隔离：每个用户独立的 active_model）
        _user_config = self._resolve_user_config_store(user_id)
        if _user_config is not None:
            active_name = _user_config.get_active_model()
            if active_name:
                try:
                    engine.switch_model(active_name)
                except Exception:
                    logger.debug("恢复激活模型 %s 失败", active_name, exc_info=True)
            # 从用户级配置恢复 full_access 开关（跨会话持久化）
            if hasattr(_user_config, "get_full_access"):
                engine._full_access_enabled = _user_config.get_full_access()
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
        # 注入历史会话摘要存储（供 engine 在 chat() 中检索历史摘要）
        if self._session_summary_store is not None:
            engine._session_summary_store = self._session_summary_store
        engine.start_registry_scan()
        return engine


    async def acquire_for_chat(
        self, session_id: str | None, *, user_id: str | None = None,
        _skip_limit_check: bool = False,
    ) -> tuple[str, AgentEngine]:
        """获取会话并标记为处理中。

        同一会话在同一时刻仅允许一个请求执行。
        支持从 SQLite 历史记录按需恢复会话。
        当 user_id 不为 None 时，会验证会话归属并记录用户 ID。

        B2 优化：引擎创建和历史消息加载在锁外执行，避免阻塞并发请求。
        锁内仅做快速路径判断和 slot 预留。

        Args:
            _skip_limit_check: 内部参数，跳过会话数量上限检查
                （用于 rollback 等不应受上限约束的场景）。
        """
        # ── Phase 1: 锁内快速路径 ──────────────────────────────
        _need_create = False
        new_id: str = ""
        async with self._lock:
            now = time.monotonic()
            # 快速路径：内存中已有会话
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
                entry.restored_readonly = False  # B4: 活跃使用时恢复正常 TTL
                logger.debug("复用会话并加锁 %s", session_id)
                return session_id, entry.engine

            # 另一个请求正在创建同一会话
            if session_id is not None and session_id in self._pending_creates:
                raise SessionBusyError(
                    f"会话 '{session_id}' 正在初始化中，请稍后重试。"
                )

            # 会话上限检查（含正在创建的 slot）
            total_slots = len(self._sessions) + len(self._pending_creates)
            if not _skip_limit_check and total_slots >= self._max_sessions:
                raise SessionLimitExceededError(
                    f"会话数量已达上限（{self._max_sessions}），请稍后重试。"
                )

            # W8: 每用户会话上限检查
            if (
                not _skip_limit_check
                and user_id is not None
                and self._max_sessions_per_user > 0
            ):
                _user_count = sum(
                    1 for e in self._sessions.values() if e.user_id == user_id
                )
                if _user_count >= self._max_sessions_per_user:
                    raise SessionLimitExceededError(
                        f"用户会话数量已达上限（{self._max_sessions_per_user}），"
                        f"请关闭部分会话后重试。"
                    )

            # 预留 slot
            new_id = session_id if session_id is not None else str(uuid.uuid4())
            self._pending_creates.add(new_id)
            _need_create = True

        # ── Phase 2: 锁外执行重量级操作 ────────────────────────
        engine: AgentEngine | None = None
        scope: UserScope | None = None
        restored = False
        try:
            # SQLite 历史检查 & 消息加载
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

            # 创建 UserScope（统一的用户作用域）
            if self._database is not None:
                scope = UserScope.create(
                    user_id, self._database, self._config.workspace_root,
                    data_root=self._config.data_root,
                )
            # 引擎创建（耗时操作：文件系统、DB、LLM client 等）
            engine = self._create_engine_with_history(
                new_id,
                history_messages,
                user_id=user_id,
                scope=scope,
            )
            engine._session_id = new_id
            engine._approval.set_session_id(new_id)
            engine.set_message_snapshot_index(
                len(history_messages) if history_messages else 0
            )
        except Exception:
            # 创建失败：释放预留 slot
            async with self._lock:
                self._pending_creates.discard(new_id)
            raise

        # ── Phase 3: 锁内注册引擎 ─────────────────────────────
        async with self._lock:
            self._pending_creates.discard(new_id)
            self._sessions[new_id] = _SessionEntry(
                engine=engine,
                last_access=time.monotonic(),
                in_flight=True,
                user_id=user_id,
                scope=scope,
            )
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

        # ── Phase 4: MCP 初始化（锁外） ───────────────────────
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
        # ── Phase 5: 异步预热 prompt cache（fire-and-forget） ──
        # 仅对 Anthropic ClaudeClient 生效：预热稳定 system prompt 前缀，
        # 使首条用户消息即可命中 cache，大幅降低首次 TTFT。
        try:
            from excelmanus.engine_utils import fire_and_forget
            fire_and_forget(engine.warmup_prompt_cache(), name="warmup_prompt_cache")
        except Exception:
            logger.debug("prompt cache 预热任务创建失败，跳过", exc_info=True)

        return new_id, engine

    async def release_for_chat(self, session_id: str) -> None:
        """释放会话处理中标记，并将新增消息持久化到 SQLite。

        会话可能已被并发删除，释放时静默忽略缺失条目。

        竞态修复：在锁内捕获消息快照，锁外基于快照持久化，
        避免读取 engine 可变状态时与并发 acquire 冲突。
        """
        snapshot: _PersistenceSnapshot | None = None
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
                # B1-fix: 在锁内立即更新 snapshot_index，防止并发
                # flush_messages_sync 读到旧值导致消息重复持久化。
                entry.engine.set_message_snapshot_index(
                    snapshot.new_snapshot_index
                )
            entry.in_flight = False
            entry.last_access = time.monotonic()

        # 锁外基于快照持久化，不再读取 engine 可变状态
        if snapshot is not None and self._conv_persistence is not None:
            try:
                self._conv_persistence.sync_from_snapshot(
                    session_id, snapshot
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
            try:
                await self._generate_session_summary(
                    session_id, engine, user_id=user_id,
                )
            except Exception:
                logger.debug("会话 %s 摘要生成失败", session_id, exc_info=True)
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
        锁内标记 in_flight 防止并发 acquire，锁外完成清理后释放。

        Args:
            session_id: 要清除的会话 ID。

        Returns:
            True 表示成功清除，False 表示会话不存在。
        """
        engine: AgentEngine | None = None
        entry_ref: _SessionEntry | None = None
        async with self._lock:
            entry = self._sessions.get(session_id)
            if entry is not None:
                if entry.in_flight:
                    raise SessionBusyError(
                        f"会话 '{session_id}' 正在处理中，暂无法清除。"
                    )
                entry.in_flight = True
                entry_ref = entry
                engine = entry.engine

        if engine is not None:
            try:
                engine.clear_memory()
                if self._conv_persistence is not None:
                    self._conv_persistence.clear(session_id, engine)
                logger.info("已清除会话 %s 引擎内存", session_id)
            finally:
                if entry_ref is not None:
                    entry_ref.in_flight = False
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
            try:
                await self._generate_session_summary(sid, engine, user_id=user_id)
            except Exception:
                logger.debug("会话 %s 摘要生成失败", sid, exc_info=True)
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

        # R2: 在锁内完成活跃会话的持久化，避免锁释放后并发删除导致幽灵记录
        async with self._lock:
            in_memory = session_id in self._sessions
            if in_memory:
                entry = self._sessions[session_id]
                if user_id is not None and entry.user_id != user_id:
                    return False  # 不属于当前用户
                # 活跃会话：在锁内持久化到 SQLite，再更新状态
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

    async def update_session_title(
        self, session_id: str, title: str, *, user_id: str | None = None
    ) -> bool:
        """更新会话标题（用户手动设置），返回是否成功。"""
        if self._chat_history is None:
            return False
        if user_id is not None:
            if not self._chat_history.session_owned_by(session_id, user_id):
                return False
        elif not self._chat_history.session_exists(session_id):
            return False
        self._chat_history.update_session(
            session_id, title=title, title_source="user"
        )
        return True

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
            # B4: restored_readonly 会话使用 1/4 TTL，加速回收懒恢复资源
            readonly_ttl = max(30, self._ttl_seconds // 4)
            expired_ids = [
                sid
                for sid, entry in self._sessions.items()
                if (not entry.in_flight)
                and (now - entry.last_access) > (
                    readonly_ttl if entry.restored_readonly else self._ttl_seconds
                )
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
                await self._generate_session_summary(
                    sid, engine, user_id=getattr(engine, "_user_id", None),
                )
            except Exception:
                logger.debug("过期会话 %s 摘要生成失败", sid, exc_info=True)
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
            try:
                await self._generate_session_summary(
                    sid, engine, user_id=getattr(engine, "_user_id", None),
                )
            except Exception:
                logger.debug("会话 %s 关闭时摘要生成失败", sid, exc_info=True)
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

    @property
    def session_summary_store(self) -> Any:
        """底层 SessionSummaryStore 实例（只读）。"""
        return self._session_summary_store

    async def _generate_session_summary(
        self,
        session_id: str,
        engine: AgentEngine,
        *,
        user_id: str | None = None,
    ) -> None:
        """为指定会话异步生成结构化摘要并持久化。

        门控条件：
        - session_summary_enabled 开启
        - session_turn >= min_turns
        - 有 chat_history 可加载消息
        - 该 session 尚无摘要或摘要已过期
        """
        if self._session_summary_store is None:
            return
        if not self._config.session_summary_enabled:
            return
        if engine.session_turn < self._config.session_summary_min_turns:
            return
        if self._chat_history is None:
            return

        # 检查是否已有摘要（避免重复生成）
        try:
            existing = self._session_summary_store.get_by_session(session_id)
            if existing is not None:
                return
        except Exception:
            pass

        # 加载消息
        try:
            messages = self._chat_history.load_messages(session_id)
        except Exception:
            logger.debug("会话摘要: 加载消息失败 %s", session_id, exc_info=True)
            return
        if not messages:
            return

        # 创建 summarizer（优先 aux 模型节省 token）
        from excelmanus.session_summarizer import SessionSummarizer
        from excelmanus.providers import create_client

        mem_model = self._config.aux_model or self._config.model
        mem_api_key = self._config.aux_api_key or self._config.api_key
        mem_base_url = self._config.aux_base_url or self._config.base_url
        _protocol = (
            self._config.aux_protocol
            if self._config.aux_enabled and self._config.aux_model
            else self._config.protocol
        )
        client = create_client(
            api_key=mem_api_key,
            base_url=mem_base_url,
            protocol=_protocol,
        )
        summarizer = SessionSummarizer(client=client, model=mem_model)

        result = await summarizer.summarize(messages)
        if result is None:
            return

        from excelmanus.stores.session_summary_store import SessionSummary
        from excelmanus.memory import TokenCounter

        summary_text = result.get("summary", "")
        token_count = TokenCounter.count(summary_text) if summary_text else 0

        summary = SessionSummary(
            session_id=session_id,
            summary_text=summary_text,
            user_id=user_id,
            task_goal=result.get("task_goal", ""),
            files_involved=result.get("files_involved", []),
            outcome=result.get("outcome", "partial"),
            unfinished=result.get("unfinished", ""),
            token_count=token_count,
        )

        # embedding 向量化（如果 embedding 客户端可用）
        if self._config.embedding_enabled and summary_text:
            try:
                _emb_client = getattr(engine, "_embedding_client", None)
                if _emb_client is not None:
                    vec = await _emb_client.embed_single(summary_text)
                    summary.embedding = vec
            except Exception:
                logger.debug("会话摘要向量化失败，跳过", exc_info=True)

        try:
            self._session_summary_store.upsert(summary)
            logger.info(
                "会话摘要已生成: session=%s goal=%s outcome=%s files=%d tokens=%d",
                session_id,
                summary.task_goal[:40],
                summary.outcome,
                len(summary.files_involved),
                token_count,
            )
        except Exception:
            logger.warning("会话摘要持久化失败 %s", session_id, exc_info=True)

    async def is_session_in_flight(self, session_id: str) -> bool:
        """W10: 检查会话是否正在处理中（用于防止 backup apply 竞态）。"""
        async with self._lock:
            entry = self._sessions.get(session_id)
            return entry.in_flight if entry is not None else False

    async def get_engine_if_idle(
        self, session_id: str, *, user_id: str | None = None
    ) -> AgentEngine | None:
        """B3: 原子化获取引擎（仅当会话空闲时返回），消除 TOCTOU 竞态。

        在同一把锁内同时检查 in_flight 和获取 engine 引用。
        若会话不存在、不属于当前用户或正在处理中，返回 None。
        调用方应根据 None 返回决定错误类型（404 vs 409）。

        Returns:
            AgentEngine 引用（会话空闲时）或 None。

        Raises:
            SessionBusyError: 会话正在处理中。
        """
        async with self._lock:
            entry = self._sessions.get(session_id)
            if entry is None:
                return None
            if user_id is not None and entry.user_id != user_id:
                return None
            if entry.in_flight:
                raise SessionBusyError(
                    f"会话 '{session_id}' 正在处理中，请等待完成后再操作。"
                )
            return entry.engine

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
                # B4: 标记为只读恢复会话，使用更短 TTL 加速回收
                entry = self._sessions.get(acquired_session_id)
                if entry is not None:
                    entry.restored_readonly = True
        return engine

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

        # 预取 SQLite 会话，用于内存会话标题优先级判断和历史合并
        db_sessions_map: dict[str, dict] = {}
        if self._chat_history is not None:
            try:
                for ds in self._chat_history.list_sessions(
                    include_archived=include_archived,
                    user_id=user_id,
                ):
                    db_sessions_map[ds["id"]] = ds
            except Exception:
                logger.warning("预取 SQLite 会话列表失败", exc_info=True)

        # B6: 锁内仅收集轻量数据，锁外构建完整结果，减少锁持有时间
        _raw_entries: list[tuple[str, int, str, bool, float]] = []
        wall_now = time.time()
        async with self._lock:
            for sid, entry in self._sessions.items():
                if user_id is not None and entry.user_id != user_id:
                    continue
                in_memory_ids.add(sid)
                engine = entry.engine
                msg_count = len(engine.raw_messages) if hasattr(engine, "raw_messages") else 0
                # 从第一条用户消息截取标题（轻量遍历）
                rule_title = ""
                if msg_count > 0:
                    for msg in engine.raw_messages:
                        if isinstance(msg, dict) and msg.get("role") == "user":
                            content = msg.get("content", "")
                            if isinstance(content, str):
                                rule_title = content[:80]
                            break
                _raw_entries.append((sid, msg_count, rule_title, entry.in_flight, entry.last_access))

        # 锁外构建完整结果字典
        for sid, msg_count, rule_title, in_flight, last_access in _raw_entries:
            db_info = db_sessions_map.get(sid, {})
            db_title = db_info.get("title", "")
            fallback = f"会话 {sid[:8]}"
            if db_title and db_title != fallback:
                title = db_title
            else:
                title = rule_title or fallback
            wall_updated = wall_now - (now - last_access)
            updated_at_iso = datetime.fromtimestamp(
                wall_updated, tz=timezone.utc
            ).isoformat()
            results.append({
                "id": sid,
                "title": title,
                "message_count": msg_count,
                "in_flight": in_flight,
                "status": "active",
                "updated_at": updated_at_iso,
            })

        # 合并 SQLite 中的历史会话（排除已在内存中的）
        for ds_id, ds in db_sessions_map.items():
            if ds_id not in in_memory_ids:
                results.append({
                    "id": ds_id,
                    "title": ds.get("title") or f"会话 {ds_id[:8]}",
                    "message_count": ds.get("message_count", 0),
                    "in_flight": False,
                    "status": ds.get("status", "active"),
                    "updated_at": ds.get("updated_at", ""),
                })

        # F6: 全局按 updated_at 降序排序，保证前端收到的列表顺序一致
        results.sort(key=lambda s: s.get("updated_at", ""), reverse=True)
        return results

    async def get_session_detail(
        self, session_id: str, *, user_id: str | None = None
    ) -> dict:
        """获取会话详情含消息历史。当 user_id 非空时，会校验会话归属。

        性能优化：锁内仅做快速引用捕获（微秒级），
        所有序列化工作在锁外执行，避免阻塞并发 acquire/release。
        """
        engine: AgentEngine | None = None
        in_flight = False

        # ── 锁内：快速捕获引用（微秒级） ──
        async with self._lock:
            entry = self._sessions.get(session_id)
            if entry is not None:
                if user_id is not None and entry.user_id != user_id:
                    raise SessionNotFoundError(f"会话 '{session_id}' 不存在。")
                engine = entry.engine
                in_flight = entry.in_flight

        # ── 锁外：序列化（可能耗时但不阻塞其他会话） ──
        if engine is not None:
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
                "in_flight": in_flight,
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
            # 从持久化配置读取 full_access 开关
            _fa = False
            _uc = self._resolve_user_config_store(user_id)
            if _uc is not None and hasattr(_uc, "get_full_access"):
                _fa = _uc.get_full_access()
            return {
                "id": session_id,
                "message_count": len(messages),
                "in_flight": False,
                "messages": messages,
                "full_access_enabled": _fa,
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

        # B7: 跳过会话上限检查，rollback 不应因上限而失败
        acquired_session_id, engine = await self.acquire_for_chat(
            session_id, user_id=user_id, _skip_limit_check=True
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

    # ── 完整会话导出 / 导入 ───────────────────────────────────

    async def export_full_session(
        self,
        session_id: str,
        *,
        user_id: str | None = None,
        include_workspace: bool = True,
    ) -> dict:
        """完整导出会话为 EMX v2.0 格式（消息 + 状态 + 记忆 + 工作区文件）。

        Args:
            session_id: 要导出的会话 ID。
            user_id: 当前用户 ID，用于归属校验。
            include_workspace: 是否包含工作区文件。

        Returns:
            EMX v2.0 格式的 dict。

        Raises:
            SessionNotFoundError: 会话不存在或无权访问。
        """
        from excelmanus.session_export import (
            collect_workspace_files,
            export_emx,
        )

        # ── 获取消息 ──
        messages = await self.get_session_messages(
            session_id, limit=100000, user_id=user_id,
        )
        if not messages and not self.session_exists(session_id):
            raise SessionNotFoundError(f"会话 '{session_id}' 不存在。")

        # ── 会话元数据 ──
        session_meta: dict = {"id": session_id}
        ch = self._chat_history
        if ch is not None:
            meta = ch.get_session_meta(session_id)
            if meta:
                session_meta.update(meta)

        # ── Excel 事件数据 ──
        excel_diffs: list[dict] = []
        excel_previews: list[dict] = []
        affected_files: list[str] = []
        if ch is not None:
            excel_diffs = ch.load_excel_diffs(session_id)
            excel_previews = ch.load_excel_previews(session_id)
            affected_files = ch.load_affected_files(session_id)

        # ── 引擎状态（内存中有活跃会话时） ──
        session_state: dict | None = None
        task_list: dict | None = None
        config_snapshot: dict | None = None
        workspace_root: str = ""

        engine = self.get_engine(session_id, user_id=user_id)
        if engine is not None:
            # 引擎活跃：直接采集运行时状态
            session_state = engine._state.to_dict()
            task_list = engine._task_store.to_dict()
            config_snapshot = {
                "model": engine.current_model,
                "chat_mode": getattr(engine, "_current_chat_mode", "write"),
                "full_access_enabled": engine.full_access_enabled,
            }
            workspace_root = str(engine.workspace.root_dir)
        else:
            # 引擎不在内存：尝试从 checkpoint 恢复状态
            if self._database is not None:
                try:
                    from excelmanus.stores.session_state_store import SessionStateStore
                    store = SessionStateStore(self._database)
                    cp = store.load_latest_checkpoint(session_id)
                    if cp is not None:
                        session_state = cp.get("state_dict")
                        task_list = cp.get("task_list_dict")
                except Exception:
                    logger.debug("导出时加载 checkpoint 失败", exc_info=True)
            # 解析工作区路径
            try:
                ws = IsolatedWorkspace.resolve(
                    self._config.workspace_root,
                    user_id=user_id,
                    auth_enabled=user_id is not None,
                    data_root=self._config.data_root,
                )
                workspace_root = str(ws.root_dir)
            except Exception:
                workspace_root = self._config.workspace_root

        # ── 持久记忆 ──
        memories_list: list[dict] | None = None
        if self._database is not None:
            try:
                from excelmanus.stores.memory_store import MemoryStore
                mem_store = MemoryStore(self._database, user_id=user_id)
                entries = mem_store.load_all()
                if entries:
                    memories_list = [
                        {
                            "category": e.category.value,
                            "content": e.content,
                            "source": e.source or "",
                            "created_at": e.timestamp.isoformat() if e.timestamp else "",
                        }
                        for e in entries
                    ]
            except Exception:
                logger.debug("导出时加载记忆失败", exc_info=True)

        # ── 工作区文件 ──
        workspace_files: list[dict] | None = None
        if include_workspace and workspace_root:
            try:
                workspace_files = collect_workspace_files(
                    workspace_root,
                    affected_only=affected_files or None,
                )
            except Exception:
                logger.debug("导出时收集工作区文件失败", exc_info=True)

        return export_emx(
            session_meta,
            messages,
            excel_diffs,
            excel_previews,
            affected_files,
            session_state=session_state,
            task_list=task_list,
            memories=memories_list,
            config_snapshot=config_snapshot,
            workspace_files=workspace_files,
        )

    async def import_full_session(
        self,
        parsed: dict,
        *,
        user_id: str | None = None,
    ) -> dict:
        """从 EMX 解析结果导入完整会话（消息 + 状态 + 记忆 + 工作区文件）。

        Args:
            parsed: parse_emx() 的返回值。
            user_id: 当前用户 ID。

        Returns:
            {"session_id", "title", "message_count", "files_restored",
             "memories_restored", "state_restored"}
        """
        ch = self._chat_history
        if ch is None:
            raise RuntimeError("聊天记录存储未启用，无法导入")

        new_session_id = str(uuid.uuid4())
        meta = parsed["session_meta"]
        title = meta.get("title") or "导入的会话"
        messages = parsed["messages"]

        # ── 1. 创建会话 + 写入消息 ──
        ch.create_session(new_session_id, title, user_id=user_id)
        if messages:
            ch.save_turn_messages(new_session_id, messages, turn_number=0)

        # ── 2. 恢复 Excel 事件数据 ──
        for diff in parsed.get("excel_diffs") or []:
            try:
                ch.save_excel_diff(
                    new_session_id,
                    diff.get("tool_call_id", ""),
                    diff.get("file_path", ""),
                    diff.get("sheet", ""),
                    diff.get("affected_range", ""),
                    diff.get("changes", []),
                )
            except Exception:
                pass
        for preview in parsed.get("excel_previews") or []:
            try:
                ch.save_excel_preview(
                    new_session_id,
                    preview.get("tool_call_id", ""),
                    preview.get("file_path", ""),
                    preview.get("sheet", ""),
                    preview.get("columns", []),
                    preview.get("rows", []),
                    preview.get("total_rows", 0),
                    preview.get("truncated", False),
                )
            except Exception:
                pass
        for fp in parsed.get("affected_files") or []:
            try:
                ch.save_affected_file(new_session_id, fp)
            except Exception:
                pass

        # ── 3. 恢复 SessionState + TaskList checkpoint ──
        state_restored = False
        session_state = parsed.get("session_state")
        task_list = parsed.get("task_list")
        if (session_state or task_list) and self._database is not None:
            try:
                from excelmanus.stores.session_state_store import SessionStateStore
                store = SessionStateStore(self._database)
                store.save_checkpoint(
                    session_id=new_session_id,
                    state_dict=session_state or {},
                    task_list_dict=task_list or {},
                    turn_number=session_state.get("session_turn", 0) if session_state else 0,
                    checkpoint_type="import",
                )
                state_restored = True
            except Exception:
                logger.debug("导入时保存 checkpoint 失败", exc_info=True)

        # ── 4. 恢复持久记忆 ──
        memories_restored = 0
        memories = parsed.get("memories")
        if memories and self._database is not None:
            try:
                from excelmanus.memory_models import MemoryCategory, MemoryEntry
                from excelmanus.stores.memory_store import MemoryStore

                mem_store = MemoryStore(self._database, user_id=user_id)
                entries: list[MemoryEntry] = []
                for m in memories:
                    try:
                        cat = MemoryCategory(m.get("category", "general"))
                    except ValueError:
                        cat = MemoryCategory.GENERAL
                    ts_str = m.get("created_at", "")
                    try:
                        ts = datetime.fromisoformat(ts_str) if ts_str else datetime.now(timezone.utc)
                    except (ValueError, TypeError):
                        ts = datetime.now(timezone.utc)
                    entries.append(MemoryEntry(
                        content=m.get("content", ""),
                        category=cat,
                        timestamp=ts,
                        source=m.get("source", "emx_import"),
                    ))
                memories_restored = mem_store.save_entries(entries)
            except Exception:
                logger.debug("导入时恢复记忆失败", exc_info=True)

        # ── 5. 恢复工作区文件 ──
        files_restored = 0
        workspace_files = parsed.get("workspace_files")
        if workspace_files:
            from excelmanus.session_export import restore_workspace_files

            try:
                ws = IsolatedWorkspace.resolve(
                    self._config.workspace_root,
                    user_id=user_id,
                    auth_enabled=user_id is not None,
                    data_root=self._config.data_root,
                )
                ws_root = str(ws.root_dir)
            except Exception:
                ws_root = self._config.workspace_root

            try:
                files_restored, _ = restore_workspace_files(ws_root, workspace_files)
            except Exception:
                logger.debug("导入时恢复工作区文件失败", exc_info=True)

        logger.info(
            "完整会话导入成功: session=%s messages=%d files=%d memories=%d state=%s",
            new_session_id, len(messages), files_restored, memories_restored, state_restored,
        )

        return {
            "session_id": new_session_id,
            "title": title,
            "message_count": len(messages),
            "files_restored": files_restored,
            "memories_restored": memories_restored,
            "state_restored": state_restored,
        }
