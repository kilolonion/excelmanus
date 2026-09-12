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
from excelmanus.workspace import IsolatedWorkspace, SandboxConfig
from excelmanus.workspace.paths import (
    default_workspace_path,
    paths_equal,
    workspace_title_from_path,
)
from excelmanus.stores.workspace_store import WorkspacePathError, WorkspaceStore

from excelmanus.conversation_persistence import ConversationPersistence
from excelmanus.session_title import title_from_messages
from pathlib import Path

if __import__("typing").TYPE_CHECKING:
    from excelmanus.chat_history import ChatHistoryStore
    from excelmanus.database import Database
    from excelmanus.persistent_memory import PersistentMemory

logger = get_logger("session")


def _path_in_workspace(file_path: str, ws_root: str) -> bool:
    from excelmanus.security.guard import SecurityViolationError, contained_in

    try:
        contained_in(Path(ws_root), Path(file_path))
        return True
    except (SecurityViolationError, OSError, ValueError):
        return False


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
    new_snapshot_index: int = 0  # 持久化后应设置的新 snapshot index


@dataclass
class _SessionEntry:
    """单个会话的内部记录。"""

    engine: AgentEngine
    last_access: float
    in_flight: bool = field(default=False)
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
        self._sandbox_config = SandboxConfig()
        self._workspace_store: WorkspaceStore | None = (
            WorkspaceStore(database) if database is not None else None
        )

    @property
    def database(self) -> "Database | None":
        """底层 Database 实例（只读）。"""
        return self._database

    @property
    def chat_history(self) -> "ChatHistoryStore | None":
        """底层 ChatHistoryStore 实例（只读）。"""
        return self._chat_history

    def default_workspace_binding(self) -> tuple[str, str | None]:
        """Process default folder path and optional registry id."""
        path = default_workspace_path(self._config)
        Path(path).mkdir(parents=True, exist_ok=True)
        if self._workspace_store is None:
            return path, None
        rec = self._workspace_store.ensure_path(path)
        return rec["path"], rec["id"]

    def ensure_default_workspace(self) -> dict[str, Any]:
        path, workspace_id = self.default_workspace_binding()
        if self._chat_history is not None:
            self._chat_history.backfill_workspace_paths(path, workspace_id)
        return {
            "id": workspace_id,
            "path": path,
            "title": workspace_title_from_path(path),
        }

    def _mark_default_workspaces(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        default_path, default_id = self.default_workspace_binding()
        marked: list[dict[str, Any]] = []
        for item in items:
            rec = dict(item)
            rec_path = str(rec.get("path") or "")
            rec["is_default"] = bool(
                (default_id and rec.get("id") == default_id)
                or (rec_path and paths_equal(rec_path, default_path))
            )
            marked.append(rec)
        return marked

    def list_workspaces(self) -> list[dict[str, Any]]:
        if self._workspace_store is None:
            path, workspace_id = self.default_workspace_binding()
            return self._mark_default_workspaces([{
                "id": workspace_id,
                "path": path,
                "title": workspace_title_from_path(path),
                "created_at": "",
                "updated_at": "",
                "sort_index": 0,
            }])
        items = self._workspace_store.list()
        if items:
            return self._mark_default_workspaces(items)
        rec = self.ensure_default_workspace()
        found = self._workspace_store.get(rec["id"]) if rec.get("id") else None
        return self._mark_default_workspaces([found] if found else [rec])

    def resolve_workspace_binding(
        self,
        workspace_id: str | None = None,
        workspace_path: str | None = None,
    ) -> tuple[str, str | None]:
        """Resolve a registered folder. Unregistered paths are rejected unless default."""
        if workspace_id:
            if self._workspace_store is None:
                raise WorkspacePathError("工作区登记未启用")
            rec = self._workspace_store.get(workspace_id)
            if rec is None:
                raise WorkspacePathError("工作区不存在")
            return rec["path"], rec["id"]
        if workspace_path:
            from excelmanus.workspace.paths import canonicalize_workspace_path

            canon = canonicalize_workspace_path(workspace_path)
            if self._workspace_store is not None:
                rec = self._workspace_store.get_by_path(canon)
                if rec is not None:
                    return rec["path"], rec["id"]
            default_path, default_id = self.default_workspace_binding()
            if paths_equal(canon, default_path):
                return default_path, default_id
            raise WorkspacePathError("工作区未登记")
        return self.default_workspace_binding()

    def register_workspace(self, path: str, *, title: str = "") -> tuple[dict[str, Any], bool]:
        if self._workspace_store is None:
            raise WorkspacePathError("工作区登记未启用")
        return self._workspace_store.create(path, title=title)

    def rename_workspace(self, workspace_id: str, title: str) -> dict[str, Any] | None:
        return self.update_workspace(workspace_id, title=title)

    def update_workspace(
        self,
        workspace_id: str,
        *,
        title: str | None = None,
        path: str | None = None,
    ) -> dict[str, Any] | None:
        if self._workspace_store is None:
            raise WorkspacePathError("工作区登记未启用")
        rec = self._workspace_store.get(workspace_id)
        if rec is None:
            return None
        default_path, default_id = self.default_workspace_binding()
        is_default = bool(
            (default_id and workspace_id == default_id)
            or paths_equal(rec["path"], default_path)
        )
        if path:
            from excelmanus.workspace.paths import canonicalize_workspace_path

            canon = canonicalize_workspace_path(path)
            if is_default and not paths_equal(canon, default_path):
                raise WorkspacePathError("不能更改默认工作区的文件夹")
        updated = self._workspace_store.update(workspace_id, title=title, path=path)
        if updated is None:
            return None
        return self._mark_default_workspaces([updated])[0]

    def delete_workspace_registration(self, workspace_id: str) -> bool:
        if self._workspace_store is None:
            raise WorkspacePathError("工作区登记未启用")
        default_path, default_id = self.default_workspace_binding()
        if default_id and workspace_id == default_id:
            raise WorkspacePathError("不能删除默认工作区")
        rec = self._workspace_store.get(workspace_id)
        if rec is not None and paths_equal(rec["path"], default_path):
            raise WorkspacePathError("不能删除默认工作区")
        return self._workspace_store.delete(workspace_id)

    def workspace_path_for_session(self, session_id: str) -> str:
        if self._chat_history is not None:
            try:
                meta = self._chat_history.get_session_meta(session_id)
            except Exception:
                meta = None
            if isinstance(meta, dict):
                path = meta.get("workspace_path")
                if isinstance(path, str) and path.strip():
                    return path
        entry = self._sessions.get(session_id)
        if entry is not None:
            try:
                return str(entry.engine.workspace.root_dir)
            except Exception:
                logger.debug("读取会话工作区失败", exc_info=True)
        return default_workspace_path(self._config)

    def _session_public_dict(
        self,
        row: dict[str, Any],
        *,
        in_flight: bool = False,
        message_count: int | None = None,
        title: str | None = None,
        updated_at: str | None = None,
        pending_approval: bool = False,
        pending_question: bool = False,
    ) -> dict[str, Any]:
        sid = str(row.get("id") or "")
        path = str(row.get("workspace_path") or "") or default_workspace_path(self._config)
        blank_raw = row.get("blank")
        msg_count = message_count if message_count is not None else int(row.get("message_count") or 0)
        if blank_raw is None:
            blank = msg_count == 0
        else:
            blank = bool(int(blank_raw))
        fallback = f"会话 {sid[:8]}" if sid else "新对话"
        resolved_title = title if title is not None else (row.get("title") or fallback)
        return {
            "id": sid,
            "title": resolved_title or fallback,
            "message_count": msg_count,
            "in_flight": in_flight,
            "updated_at": updated_at if updated_at is not None else (row.get("updated_at") or ""),
            "workspace_path": path,
            "workspace_id": row.get("workspace_id"),
            "blank": blank,
            "workspace_title": workspace_title_from_path(path),
            "pending_approval": bool(pending_approval),
            "pending_question": bool(pending_question),
        }

    async def create_or_reuse_session(
        self,
        *,
        workspace_id: str | None = None,
        workspace_path: str | None = None,
        title: str = "新对话",
    ) -> dict[str, Any]:
        """Create a blank session or reuse the unused one for this folder. No engine."""
        path, ws_id = self.resolve_workspace_binding(workspace_id, workspace_path)
        display = (title or "").strip() or "新对话"
        async with self._lock:
            if self._chat_history is not None:
                existing = self._chat_history.find_blank_session(path)
                if existing is not None:
                    return self._session_public_dict(existing)
                new_id = str(uuid.uuid4())
                self._chat_history.create_session(
                    new_id,
                    display,
                    workspace_path=path,
                    workspace_id=ws_id,
                    blank=True,
                )
                meta = self._chat_history.get_session_meta(new_id) or {
                    "id": new_id,
                    "title": display,
                    "workspace_path": path,
                    "workspace_id": ws_id,
                    "blank": 1,
                }
                return self._session_public_dict(meta)
            new_id = str(uuid.uuid4())
            now = datetime.now(timezone.utc).isoformat()
            return self._session_public_dict(
                {
                    "id": new_id,
                    "title": display,
                    "message_count": 0,
                    "workspace_path": path,
                    "workspace_id": ws_id,
                    "blank": 1,
                    "updated_at": now,
                }
            )

    def _engine_workspace(self, session_id: str) -> IsolatedWorkspace:
        ws_path = self.workspace_path_for_session(session_id)
        default_path = default_workspace_path(self._config)
        create_missing = paths_equal(ws_path, default_path)
        return IsolatedWorkspace(
            root_dir=ws_path,
            sandbox_config=self._sandbox_config,
            create_missing=create_missing,
        )

    def set_credential_store(self, credential_store: Any) -> None:
        """注入 CredentialStore 实例（订阅凭证管理）。"""
        self._credential_store = credential_store

    def set_credential_resolver(self, resolver: Any) -> None:
        """注入 CredentialResolver 实例（运行时凭证解析）。"""
        self._credential_resolver = resolver

    def sync_user_subscription_profiles(
        self,
        engine: AgentEngine,
    ) -> None:
        """为已有 DB profile 注入进程级订阅 OAuth 凭证。

        对 name 以 ``openai-codex/`` 开头的 profile，用 CredentialStore 中的
        access_token 替换空 api_key，并更新 base_url。
        """
        from excelmanus.auth.providers.openai_codex import OpenAICodexProvider

        if self._credential_store is None:
            return

        try:
            active_cred = self._credential_store.get_active_profile("openai-codex")
        except Exception:
            logger.debug("读取进程级 Codex 凭证失败", exc_info=True)
            return

        if active_cred is None or not active_cred.access_token:
            return

        api_key, base_url = OpenAICodexProvider().get_api_credential(active_cred.access_token)

        augmented: list[ModelProfile] = []
        changed = False
        for p in engine._config.models:
            if OpenAICodexProvider.is_codex_profile_name(p.name):
                augmented.append(replace(
                    p,
                    api_key=api_key,
                    base_url=base_url,
                    protocol=OpenAICodexProvider.PROTOCOL,
                ))
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

    async def broadcast_context_optimization(
        self,
        *,
        max_context_tokens: int | None = None,
        compaction_enabled: bool | None = None,
        compaction_threshold_ratio: float | None = None,
    ) -> None:
        """向所有活跃会话广播上下文窗口 / 压缩配置（锁保护）。"""
        async with self._lock:
            for entry in self._sessions.values():
                entry.engine.apply_context_optimization(
                    max_context_tokens=max_context_tokens,
                    compaction_enabled=compaction_enabled,
                    compaction_threshold_ratio=compaction_threshold_ratio,
                )

    async def broadcast_model_profiles(self, profiles: tuple) -> None:
        """向所有活跃会话广播模型档案列表变更（锁保护）。

        广播全局档案后立刻按会话归属重新注入订阅 OAuth，避免冲掉用户凭证。
        """
        async with self._lock:
            for entry in self._sessions.values():
                entry.engine.sync_model_profiles(profiles)
                self.sync_user_subscription_profiles(entry.engine)

    def notify_file_deleted(self, file_path: str) -> None:
        """W4: 通知活跃 session 文件已被删除。

        ISO-4: 仅通知工作区包含目标文件的会话，避免跨用户干扰。
        """
        entries = list(self._sessions.values())
        for entry in entries:
            try:
                ws_root = str(entry.engine.workspace.root_dir)
                if not _path_in_workspace(file_path, ws_root):
                    continue
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
                if not _path_in_workspace(old_path, ws_root):
                    continue
            except Exception:
                logger.debug("notify_file_renamed 处理异常", exc_info=True)

    def _resolve_user_config_store(self, user_id: str | None = None) -> Any:
        """返回进程级 UserConfigStore（用于 active_model 等偏好）。"""
        if self._database is None:
            return self._config_store
        try:
            from excelmanus.stores.config_store import UserConfigStore
            return UserConfigStore(self._database.conn)
        except Exception:
            return self._config_store

    def _refresh_engine_model_profiles(self, engine: AgentEngine, *, force_db: bool = False) -> None:
        """把运行时 / 数据库档案同步到引擎，避免新会话只用环境快照。"""
        live_models = getattr(self._config, "models", ()) or ()
        try:
            from excelmanus.api_app_state import _sync_config_profiles_from_db, get_config

            live = get_config()
            if force_db or not getattr(live, "models", ()):
                _sync_config_profiles_from_db()
                live = get_config()
            if live is not None and getattr(live, "models", ()):
                live_models = live.models
        except Exception:
            logger.debug("同步运行时模型档案失败", exc_info=True)
        if live_models:
            engine.sync_model_profiles(live_models)

    def _apply_persisted_active_model(self, engine: AgentEngine, user_config: Any | None = None) -> None:
        """按已激活档案切换新会话，失败时先补档案再试一次。"""
        self._refresh_engine_model_profiles(engine)
        store = user_config if user_config is not None else self._resolve_user_config_store()
        if store is None or not hasattr(store, "get_active_model"):
            return
        active_name = store.get_active_model()
        if not active_name:
            return
        try:
            from excelmanus.api_app_state import is_placeholder_model_profile

            if is_placeholder_model_profile(active_name):
                logger.warning("忽略占位激活模型 %s", active_name)
                return
        except Exception:
            pass
        try:
            switched = engine.switch_model(active_name)
            if isinstance(switched, str) and switched.startswith("未找到模型"):
                self._refresh_engine_model_profiles(engine, force_db=True)
                switched = engine.switch_model(active_name)
            if isinstance(switched, str) and switched.startswith("未找到模型"):
                logger.warning(
                    "恢复激活模型失败，会话仍使用 %s: %s",
                    engine.current_model,
                    switched,
                )
        except Exception:
            logger.debug("恢复激活模型 %s 失败", active_name, exc_info=True)

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

    def _create_memory_components(self) -> "PersistentMemory | None":
        """根据 config.memory_enabled 创建持久记忆存储。提取器不在默认路径构造。"""
        if not self._config.memory_enabled:
            return None

        from excelmanus.persistent_memory import PersistentMemory

        if self._database is not None:
            from excelmanus.stores.memory_store import MemoryStore
            backend: Any = MemoryStore(self._database)
        else:
            from excelmanus.stores.file_memory_backend import FileMemoryBackend
            backend = FileMemoryBackend(
                memory_dir=self._config.memory_dir,
                auto_load_lines=self._config.memory_auto_load_lines,
            )

        return PersistentMemory(
            backend=backend,
            auto_load_lines=self._config.memory_auto_load_lines,
        )

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
        user_ctx: Any = None,
        scope: Any = None,
    ) -> AgentEngine:
        """创建 AgentEngine 并注入历史。文件根取该会话的 workspace_path。"""
        isolated_ws = self._engine_workspace(session_id)
        engine_config = self._config
        overrides: dict[str, Any] = {"workspace_root": str(isolated_ws.root_dir)}
        _target_model = self._config.model
        try:
            from excelmanus.auth.providers.openai_codex import OpenAICodexProvider
            if isinstance(_target_model, str) and OpenAICodexProvider.is_codex_profile_name(_target_model):
                _resolved_model = OpenAICodexProvider.model_from_profile_name(_target_model)
                if _resolved_model:
                    _target_model = _resolved_model
                    overrides["model"] = _resolved_model
        except Exception:
            logger.debug("Codex 模型前缀解析失败", exc_info=True)
        if self._credential_resolver is not None:
            try:
                _resolved_cred = self._credential_resolver.resolve_sync(_target_model)
                if _resolved_cred:
                    overrides["api_key"] = _resolved_cred.api_key
                    if _resolved_cred.base_url:
                        overrides["base_url"] = _resolved_cred.base_url
                    if _resolved_cred.protocol and _resolved_cred.protocol != "openai":
                        overrides["protocol"] = _resolved_cred.protocol
                    logger.info(
                        "使用 %s 订阅凭证 (source=%s)",
                        _resolved_cred.provider or "unknown", _resolved_cred.source,
                    )
            except Exception:
                logger.debug("订阅凭证解析失败", exc_info=True)
        engine_config = replace(self._config, **overrides)
        persistent_memory = self._create_memory_components()
        engine = AgentEngine(
            config=engine_config,
            registry=self._registry,
            skill_router=self._skill_router,
            persistent_memory=persistent_memory,
            mcp_manager=self._shared_mcp_manager,
            own_mcp_manager=self._shared_mcp_manager is None,
            database=self._database,
            workspace=isolated_ws,
        )
        self.sync_user_subscription_profiles(engine)
        if self._credential_resolver is not None:
            engine._credential_resolver = self._credential_resolver
        if history_messages:
            engine.inject_history(history_messages)
            engine._session_id = session_id
            engine.restore_session_snapshot()
        _user_config = self._resolve_user_config_store()
        if _user_config is not None:
            self._apply_persisted_active_model(engine, _user_config)
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

            # 预留 slot
            new_id = session_id if session_id is not None else str(uuid.uuid4())
            self._pending_creates.add(new_id)
            _need_create = True

        # ── Phase 2: 锁外执行重量级操作 ────────────────────────
        engine: AgentEngine | None = None
        restored = False
        try:
            history_messages: list[dict] | None = None
            if (
                session_id is not None
                and self._chat_history is not None
                and self._chat_history.session_exists(session_id)
            ):
                history_messages = self._chat_history.load_messages(session_id)
                restored = True
            elif self._chat_history is not None and not self._chat_history.session_exists(new_id):
                path, ws_id = self.default_workspace_binding()
                self._chat_history.create_session(
                    new_id, "", workspace_path=path, workspace_id=ws_id, blank=True,
                )

            engine = self._create_engine_with_history(
                new_id,
                history_messages,
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
                        if not self._chat_history.session_exists(new_id):
                            path, ws_id = self.default_workspace_binding()
                            self._chat_history.create_session(
                                new_id, "", workspace_path=path, workspace_id=ws_id, blank=True,
                            )
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

        此方法直接读取 engine 可变状态，但由于 SSE 事件回调在 engine.followup()
        内部同步触发，与 engine 的消息修改在同一协程内，不存在并发问题。
        """
        if self._conv_persistence is None:
            return
        entry = self._sessions.get(session_id)
        if entry is None:
            return
        try:
            self._conv_persistence.sync_new_messages(session_id, entry.engine)
        except Exception:
            logger.debug("会话 %s 中间持久化失败", session_id, exc_info=True)

    async def delete(self, session_id: str, *, user_id: str | None = None) -> bool:
        """删除指定会话。"""
        engine: AgentEngine | None = None
        async with self._lock:
            if session_id in self._sessions:
                entry = self._sessions[session_id]
                if entry.in_flight:
                    raise SessionBusyError(
                        f"会话 '{session_id}' 正在处理中，暂无法删除。"
                    )
                engine = entry.engine
                del self._sessions[session_id]
                logger.info("已删除会话 %s", session_id)

        if engine is not None:
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

        if self._chat_history is not None:
            if not self._chat_history.session_exists(session_id):
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
        """清空全部会话及消息。若有会话正在处理中则抛出 SessionBusyError。

        Returns:
            (删除的会话数, 删除的消息数)
        """
        active_engines: list[tuple[str, AgentEngine]] = []
        async with self._lock:
            targets = dict(self._sessions)
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
                sess_count, msg_count = self._chat_history.delete_all_sessions()
            except Exception:
                logger.warning("清空 SQLite 会话失败", exc_info=True)
        return sess_count, msg_count

    async def update_session_title(
        self, session_id: str, title: str, *, user_id: str | None = None
    ) -> bool:
        """更新会话标题（用户手动设置），返回是否成功。"""
        if self._chat_history is None:
            return False
        if not self._chat_history.session_exists(session_id):
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

        # 在锁外生成摘要并关闭 MCP，避免长时间持有锁
        for sid, engine in expired_engines:
            try:
                await self._generate_session_summary(sid, engine)
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
                await self._generate_session_summary(sid, engine)
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
        """同步获取指定会话的 AgentEngine（无锁，仅用于只读查询）。"""
        entry = self._sessions.get(session_id)
        if entry is None:
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

        from excelmanus.session_summarizer import SessionSummarizer
        from excelmanus.providers import create_client

        client = create_client(
            api_key=self._config.api_key,
            base_url=self._config.base_url,
            protocol=self._config.protocol,
        )
        summarizer = SessionSummarizer(client=client, model=self._config.model)

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

    async def enqueue_user_interrupt(self, session_id: str, message: str) -> bool:
        """飞行中用户消息入 next-turn。成功入队返回 True，否则 False。"""
        text = str(message or "").strip()
        if not session_id or not text:
            return False
        async with self._lock:
            entry = self._sessions.get(session_id)
            if entry is None or not entry.in_flight:
                return False
            entry.engine.push_interrupt_message(text)
            logger.info("会话 %s 插话已入队（%d 字）", session_id[:8], len(text))
            return True

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

    async def list_sessions(
        self, *, user_id: str | None = None
    ) -> list[dict]:
        """列出所有会话的摘要信息（内存活跃 + SQLite 历史合并）。"""
        in_memory_ids: set[str] = set()
        results: list[dict] = []
        now = time.monotonic()

        # 预取 SQLite 会话，用于内存会话标题优先级判断和历史合并
        db_sessions_map: dict[str, dict] = {}
        if self._chat_history is not None:
            try:
                for ds in self._chat_history.list_sessions():
                    db_sessions_map[ds["id"]] = ds
            except Exception:
                logger.warning("预取 SQLite 会话列表失败", exc_info=True)

        # B6: 锁内仅收集轻量数据，锁外构建完整结果，减少锁持有时间
        _raw_entries: list[tuple[str, int, str, bool, float, bool, bool]] = []
        wall_now = time.time()
        async with self._lock:
            for sid, entry in self._sessions.items():
                in_memory_ids.add(sid)
                engine = entry.engine
                msg_count = len(engine.raw_messages) if hasattr(engine, "raw_messages") else 0
                # 从第一条用户消息截取标题（轻量遍历）
                rule_title = ""
                if msg_count > 0:
                    rule_title = title_from_messages(engine.raw_messages)
                pending_approval = False
                pending_question = False
                try:
                    pending_approval = bool(engine.has_pending_approval())
                    pending_question = bool(engine.has_pending_question())
                except Exception:
                    logger.debug("读取会话待处理状态失败", exc_info=True)
                _raw_entries.append(
                    (
                        sid,
                        msg_count,
                        rule_title,
                        entry.in_flight,
                        entry.last_access,
                        pending_approval,
                        pending_question,
                    )
                )

        # 锁外构建完整结果字典
        for sid, msg_count, rule_title, in_flight, last_access, pending_approval, pending_question in _raw_entries:
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
            results.append(self._session_public_dict(
                {"id": sid, **db_info},
                in_flight=in_flight,
                message_count=msg_count,
                title=title,
                updated_at=updated_at_iso,
                pending_approval=pending_approval,
                pending_question=pending_question,
            ))

        # 合并 SQLite 中的历史会话（排除已在内存中的）
        for ds_id, ds in db_sessions_map.items():
            if ds_id not in in_memory_ids:
                results.append(self._session_public_dict(ds, in_flight=False))

        # F6: 全局按 updated_at 降序排序，保证前端收到的列表顺序一致
        results.sort(key=lambda s: s.get("updated_at", ""), reverse=True)
        return results

    async def get_session_detail(
        self, session_id: str, *, user_id: str | None = None
    ) -> dict:
        """获取会话详情含消息历史。

        性能优化：锁内仅做快速引用捕获（微秒级），
        所有序列化工作在锁外执行，避免阻塞并发 acquire/release。
        """
        engine: AgentEngine | None = None
        in_flight = False

        # ── 锁内：快速捕获引用（微秒级） ──
        async with self._lock:
            entry = self._sessions.get(session_id)
            if entry is not None:
                engine = entry.engine
                in_flight = entry.in_flight

        # ── 锁外：序列化（可能耗时但不阻塞其他会话） ──
        if engine is not None:
            messages = []
            if hasattr(engine, "raw_messages"):
                raw_messages = list(engine.raw_messages)
                for idx, message in enumerate(raw_messages):
                    if isinstance(message, dict):
                        item = dict(message)
                    else:
                        item = {"role": "unknown", "content": str(message)}
                    if not item.get("message_id"):
                        item["message_id"] = f"volatile:{session_id}:{idx}"
                    messages.append(item)

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
                "present_as": getattr(engine, '_present_as', 'native'),
                "current_model": engine.current_model,
                "current_model_name": engine.current_model_name,
                "vision_capable": engine.is_vision_capable,
                "pending_approval": pending_approval_data,
                "pending_question": pending_question_data,
                "last_route": last_route_data,
            }

        if self._chat_history is not None:
            if not self._chat_history.session_exists(session_id):
                raise SessionNotFoundError(f"会话 '{session_id}' 不存在。")
            messages = self._chat_history.load_messages(session_id)
            _fa = False
            _uc = self._resolve_user_config_store()
            if _uc is not None and hasattr(_uc, "get_full_access"):
                _fa = _uc.get_full_access()
            return {
                "id": session_id,
                "message_count": len(messages),
                "in_flight": False,
                "messages": messages,
                "full_access_enabled": _fa,
                "chat_mode": "write",
                "present_as": "native",
                "current_model": None,
                "current_model_name": None,
                "vision_capable": None,
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
        exists = self.session_exists(session_id)
        if not exists:
            raise SessionNotFoundError(f"会话 '{session_id}' 不存在。")

        # B7: 跳过会话上限检查，rollback 不应因上限而失败
        acquired_session_id, engine = await self.acquire_for_chat(
            session_id, user_id=user_id, _skip_limit_check=True
        )
        try:
            result = engine.rollback_conversation(
                turn_index,
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
        """分页获取会话消息（优先内存，回退 SQLite）。"""
        async with self._lock:
            entry = self._sessions.get(session_id)
            if entry is not None:
                engine = entry.engine
                raw_messages = list(engine.raw_messages)
                page = raw_messages[offset: offset + limit]
                normalized: list[dict] = []
                for idx, message in enumerate(page):
                    if isinstance(message, dict):
                        item = dict(message)
                    else:
                        item = {"role": "unknown", "content": str(message)}
                    if not item.get("message_id"):
                        item["message_id"] = f"volatile:{session_id}:{offset + idx}"
                    normalized.append(item)
                return normalized

        if self._chat_history is not None:
            if not self._chat_history.session_exists(session_id):
                return []
            return self._chat_history.load_messages(session_id, limit=limit, offset=offset)
        return []
