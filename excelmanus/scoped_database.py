"""用户级作用域数据库：为每个用户提供物理隔离(SQLite)或逻辑隔离(PostgreSQL)的数据访问。

SQLite 模式：每个用户拥有独立的 data.db 文件，物理隔离。
PostgreSQL 模式：共享连接 + 强制 user_id 过滤，逻辑隔离。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from excelmanus.db_adapter import Backend, ConnectionAdapter, create_sqlite_adapter
from excelmanus.user_context import UserContext

if TYPE_CHECKING:
    from excelmanus.database import Database

logger = logging.getLogger(__name__)

_USER_DB_FILENAME = "data.db"
_GLOBAL_DB_DIRNAME = "shared"
_GLOBAL_DB_FILENAME = "global.db"


class ScopedDatabase:
    """用户级作用域数据库。

    SQLite 模式下连接到 ``users/{user_id}/data.db``，物理隔离。
    PostgreSQL 模式下包装共享连接，所有查询自动注入 user_id。
    匿名模式下回退到共享 Database（向后兼容）。
    """

    def __init__(
        self,
        user_ctx: UserContext,
        shared_database: "Database",
    ) -> None:
        self._user_ctx = user_ctx
        self._shared_database = shared_database
        self._own_conn: ConnectionAdapter | None = None

        if user_ctx.is_anonymous:
            self._conn = shared_database.conn
            self._backend = shared_database.backend
        elif shared_database.backend == Backend.SQLITE:
            db_path = user_ctx.workspace_root / _USER_DB_FILENAME
            self._own_conn = create_sqlite_adapter(str(db_path))
            self._conn = self._own_conn
            self._backend = Backend.SQLITE
            self._ensure_user_schema()
        else:
            self._conn = shared_database.conn
            self._backend = Backend.POSTGRES

    @property
    def conn(self) -> ConnectionAdapter:
        return self._conn

    @property
    def backend(self) -> str:
        return self._backend

    @property
    def is_pg(self) -> bool:
        return self._backend == Backend.POSTGRES

    @property
    def user_ctx(self) -> UserContext:
        return self._user_ctx

    @property
    def db_user_id(self) -> str | None:
        return self._user_ctx.db_user_id

    def close(self) -> None:
        if self._own_conn is not None:
            self._own_conn.close()
            self._own_conn = None

    def _ensure_user_schema(self) -> None:
        """对独立 SQLite 执行 schema 迁移（复用 Database 的迁移逻辑）。"""
        from excelmanus.database import Database as _DB

        db_path = self._user_ctx.workspace_root / _USER_DB_FILENAME
        try:
            _temp = _DB(str(db_path))
            _temp.close()
        except Exception:
            logger.warning(
                "用户 %s 数据库 schema 初始化失败",
                self._user_ctx.user_id,
                exc_info=True,
            )


def resolve_global_db_path(workspace_root: str) -> str:
    """返回全局共享 DB 的路径（仅 SQLite 模式使用）。"""
    p = Path(workspace_root) / _GLOBAL_DB_DIRNAME
    p.mkdir(parents=True, exist_ok=True)
    return str(p / _GLOBAL_DB_FILENAME)
