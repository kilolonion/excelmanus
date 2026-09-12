"""SQLite 连接适配器：WAL / busy_timeout / foreign_keys / 行 dict 访问。

所有 Store 通过 ``Database.conn`` 获取 ``ConnectionAdapter``，
使用 ``?`` 占位符执行 SQL。行结果以 ``sqlite3.Row`` 支持 ``row["column"]``。
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any, Sequence

__all__ = [
    "ConnectionAdapter",
    "CursorAdapter",
    "Backend",
    "user_filter_clause",
    "create_sqlite_adapter",
]


# ── 可选 user_id 过滤 ─────────────────────────────────────────


def user_filter_clause(
    column: str = "user_id",
    user_id: str | None = None,
) -> tuple[str, tuple[str, ...] | tuple[()]]:
    """生成 user_id 过滤条件，统一处理 NULL vs 值。

    Returns:
        (sql_fragment, params) — 可直接拼入 WHERE 子句。

    Examples:
        >>> user_filter_clause("user_id", None)
        ('user_id IS NULL', ())
        >>> user_filter_clause("user_id", "abc-123")
        ('user_id = ?', ('abc-123',))
    """
    if user_id is None:
        return f"{column} IS NULL", ()
    return f"{column} = ?", (user_id,)


class Backend:
    SQLITE = "sqlite"


# ── 游标适配 ─────────────────────────────────────────────────


class CursorAdapter:
    """统一游标，返回 sqlite3.Row（dict 式访问）。"""

    __slots__ = ("_cursor",)

    def __init__(self, cursor: sqlite3.Cursor) -> None:
        self._cursor = cursor

    def fetchone(self) -> sqlite3.Row | None:
        return self._cursor.fetchone()

    def fetchall(self) -> list:
        return self._cursor.fetchall()

    @property
    def rowcount(self) -> int:
        return self._cursor.rowcount

    @property
    def lastrowid(self) -> int | None:
        return self._cursor.lastrowid

    def close(self) -> None:
        self._cursor.close()


# ── 连接适配 ─────────────────────────────────────────────────


class ConnectionAdapter:
    """SQLite 连接封装：RLock 串行化、sqlite3.Row、table_exists。"""

    __slots__ = ("_conn", "_lock")

    def __init__(self, conn: sqlite3.Connection, backend: str = Backend.SQLITE) -> None:
        del backend  # 历史构造签名兼容；仅 SQLite
        self._conn = conn
        self._lock = threading.RLock()

    @property
    def backend(self) -> str:
        return Backend.SQLITE

    @property
    def raw(self) -> sqlite3.Connection:
        """返回底层原始连接（仅迁移 / 特殊场景使用）。"""
        return self._conn

    @property
    def total_changes(self) -> int:
        return self._conn.total_changes

    def execute(self, sql: str, params: Any = None) -> CursorAdapter:
        with self._lock:
            if params is None:
                cursor = self._conn.execute(sql)
            else:
                cursor = self._conn.execute(sql, params)
            return CursorAdapter(cursor)

    def executemany(self, sql: str, params_seq: Sequence) -> CursorAdapter:
        with self._lock:
            cursor = self._conn.executemany(sql, params_seq)
            return CursorAdapter(cursor)

    def executescript(self, script: str) -> None:
        """执行多条 SQL 语句。"""
        with self._lock:
            self._conn.executescript(script)

    def commit(self) -> None:
        with self._lock:
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @property
    def row_factory(self) -> Any:
        return self._conn.row_factory

    @row_factory.setter
    def row_factory(self, value: Any) -> None:
        with self._lock:
            self._conn.row_factory = value

    def table_exists(self, table_name: str) -> bool:
        """检查表是否存在。"""
        row = self.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        ).fetchone()
        return row is not None


# ── 工厂 ─────────────────────────────────────────────────────


def create_sqlite_adapter(db_path: str) -> ConnectionAdapter:
    """创建 SQLite 连接适配器。"""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return ConnectionAdapter(conn)
