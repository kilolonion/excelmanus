"""UserScope：贯穿请求生命周期的用户作用域。

统一持有 identity（UserContext）+ scoped DB access（ScopedDatabase）+
Store 工厂方法，替代所有裸 user_id 传递。

典型用法::

    scope = UserScope.create(user_id, shared_database, workspace_root)
    mem_store = scope.memory_store()
    approval  = scope.approval_store()
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from excelmanus.db_adapter import ConnectionAdapter
from excelmanus.scoped_database import ScopedDatabase
from excelmanus.user_context import UserContext

if TYPE_CHECKING:
    from excelmanus.chat_history import ChatHistoryStore
    from excelmanus.database import Database
    from excelmanus.stores.approval_store import ApprovalStore
    from excelmanus.stores.config_store import UserConfigStore
    from excelmanus.stores.llm_call_store import LLMCallStore
    from excelmanus.stores.memory_store import MemoryStore
    from excelmanus.stores.tool_call_store import ToolCallStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class UserScope:
    """贯穿请求生命周期的用户作用域。

    持有不可变的用户身份 + 作用域数据库连接，提供所有 Store 的
    工厂方法。Store 实例通过此工厂创建时自动绑定 user_id，
    Store 内部不再需要 ``if user_id is None / else`` 分支。
    """

    user_ctx: UserContext
    _scoped_db: ScopedDatabase

    # ── 工厂 ──────────────────────────────────────────────

    @staticmethod
    def create(
        user_id: str | None,
        shared_database: "Database",
        global_workspace_root: str,
    ) -> "UserScope":
        """从裸 user_id 创建完整的 UserScope。

        - user_id 为 None → 匿名模式（兼容旧行为）
        - user_id 非空 → 多租户模式（per-user 隔离）
        """
        if user_id is None:
            ctx = UserContext.anonymous(global_workspace_root)
        else:
            ctx = UserContext.create(
                user_id, global_workspace_root=global_workspace_root
            )
        scoped_db = ScopedDatabase(ctx, shared_database)
        return UserScope(user_ctx=ctx, _scoped_db=scoped_db)

    # ── 便捷属性 ──────────────────────────────────────────

    @property
    def user_id(self) -> str | None:
        """数据库查询用的 user_id（匿名时为 None）。"""
        return self.user_ctx.db_user_id

    @property
    def conn(self) -> ConnectionAdapter:
        """作用域连接（SQLite 物理隔离 / PG 共享连接）。"""
        return self._scoped_db.conn

    @property
    def scoped_db(self) -> ScopedDatabase:
        return self._scoped_db

    @property
    def workspace_root(self) -> str:
        return str(self.user_ctx.workspace_root)

    @property
    def is_anonymous(self) -> bool:
        return self.user_ctx.is_anonymous

    # ── Store 工厂方法 ────────────────────────────────────

    def memory_store(self) -> "MemoryStore":
        from excelmanus.stores.memory_store import MemoryStore

        return MemoryStore(self.conn, user_id=self.user_id)

    def approval_store(self) -> "ApprovalStore":
        from excelmanus.stores.approval_store import ApprovalStore

        return ApprovalStore(self.conn, user_id=self.user_id)

    def tool_call_store(self) -> "ToolCallStore":
        from excelmanus.stores.tool_call_store import ToolCallStore

        return ToolCallStore(self.conn, user_id=self.user_id)

    def llm_call_store(self) -> "LLMCallStore":
        from excelmanus.stores.llm_call_store import LLMCallStore

        return LLMCallStore(self.conn, user_id=self.user_id)

    def chat_history_store(self) -> "ChatHistoryStore":
        from excelmanus.chat_history import ChatHistoryStore

        return ChatHistoryStore(self.conn, user_id=self.user_id)

    def user_config_store(self) -> "UserConfigStore":
        from excelmanus.stores.config_store import UserConfigStore

        return UserConfigStore(self.conn, user_id=self.user_id)

    def close(self) -> None:
        """释放作用域数据库连接（如有独立连接）。"""
        self._scoped_db.close()
