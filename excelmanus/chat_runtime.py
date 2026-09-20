"""与 API lifespan 对齐的会话运行时：给 bench / 进程内直聊复用。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from excelmanus.config import ExcelManusConfig
from excelmanus.logger import get_logger
from excelmanus.mcp.manager import MCPManager
from excelmanus.session import SessionManager
from excelmanus.skillpacks import SkillpackLoader, SkillRouter
from excelmanus.tools import ToolRegistry

logger = get_logger("chat_runtime")


@dataclass
class ChatRuntime:
    """一套与前端服务相同的会话基础设施。"""

    manager: SessionManager
    database: Any | None = None
    mcp_manager: MCPManager | None = None

    async def aclose(self) -> None:
        try:
            await self.manager.shutdown()
        except Exception:
            logger.debug("SessionManager 关闭失败", exc_info=True)
        if self.database is not None:
            try:
                self.database.close()
            except Exception:
                logger.debug("数据库关闭失败", exc_info=True)


def build_chat_runtime(config: ExcelManusConfig) -> ChatRuntime:
    """按 API 启动路径组装 SessionManager（工具、技能、DB、MCP、凭证）。"""
    workspace_root = str(getattr(config, "workspace_root", ".") or ".")
    registry = ToolRegistry()
    registry.register_builtin_tools(workspace_root)

    loader = SkillpackLoader(config, registry)
    loader.load_all()
    router = SkillRouter(config, loader)

    database = None
    chat_history = None
    config_store = None
    from excelmanus.data_home import resolve_db_path

    db_path = resolve_db_path()
    if db_path:
        try:
            from excelmanus.database import Database
            from excelmanus.settings_persist import bind_settings_store, refresh_config_in_place

            database = Database(db_path)
            if bool(getattr(config, "chat_history_enabled", True)):
                from excelmanus.chat_history import ChatHistoryStore

                chat_history = ChatHistoryStore(database)
            config_store = bind_settings_store(database)
            refresh_config_in_place(config)
        except Exception:
            logger.warning("聊天运行时数据库初始化失败，会话将不落库", exc_info=True)
            database = None
            chat_history = None
            config_store = None

    mcp_manager = MCPManager(workspace_root, app_config=config)
    manager = SessionManager(
        max_sessions=int(getattr(config, "max_sessions", 1000) or 1000),
        ttl_seconds=int(getattr(config, "session_ttl_seconds", 1800) or 1800),
        config=config,
        registry=registry,
        skill_router=router,
        shared_mcp_manager=mcp_manager,
        chat_history=chat_history,
        database=database,
        config_store=config_store,
    )
    if database is not None:
        try:
            from excelmanus.auth.providers.credential_store import CredentialStore
            from excelmanus.auth.providers.resolver import CredentialResolver

            cred_store = CredentialStore(database.conn)
            try:
                from excelmanus.auth.providers.workbuddy import migrate_legacy_workbuddy
                migrate_legacy_workbuddy(cred_store, config_store)
            except Exception:
                logger.debug("旧版 workbuddy 数据迁移失败", exc_info=True)
            manager.set_credential_store(cred_store)
            manager.set_credential_resolver(CredentialResolver(credential_store=cred_store))
        except Exception:
            logger.debug("凭证解析器初始化失败", exc_info=True)
    try:
        manager.ensure_default_workspace()
    except Exception:
        logger.debug("默认工作区登记失败", exc_info=True)
    return ChatRuntime(manager=manager, database=database, mcp_manager=mcp_manager)
