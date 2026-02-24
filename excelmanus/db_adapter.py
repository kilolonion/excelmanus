"""统一数据库连接适配器：屏蔽 SQLite / PostgreSQL 差异。

所有 Store 通过 ``Database.conn`` 获取 ``ConnectionAdapter``，
使用 ``?`` 占位符执行 SQL。适配器自动处理：

- 占位符转换（``?`` → ``%s``）
- 行结果的 dict 访问（row["column"]）
- ``INSERT OR IGNORE`` / ``INSERT OR REPLACE`` 语法转换
- 表存在性检查的跨后端兼容
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any, Iterator, Sequence

__all__ = ["ConnectionAdapter", "CursorAdapter", "DictRow", "Backend"]


class Backend:
    SQLITE = "sqlite"
    POSTGRES = "postgres"


# ── 行包装 ──────────────────────────────────────────────────


class DictRow:
    """将 psycopg2 元组行 + description 包装为 dict 式访问。"""

    __slots__ = ("_data", "_keys")

    def __init__(self, values: tuple, description: Sequence) -> None:
        self._keys = [col[0] for col in description]  # type: ignore[index]
        self._data = dict(zip(self._keys, values))

    def __getitem__(self, key: str | int) -> Any:
        if isinstance(key, int):
            return list(self._data.values())[key]
        return self._data[key]

    def __contains__(self, key: str) -> bool:
        return key in self._data

    def __iter__(self) -> Iterator[str]:
        return iter(self._keys)

    def __len__(self) -> int:
        return len(self._keys)

    def keys(self) -> list[str]:
        return list(self._keys)

    def values(self) -> list[Any]:
        return list(self._data.values())

    def items(self) -> list[tuple[str, Any]]:
        return list(self._data.items())

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)


# ── SQL 转换 ─────────────────────────────────────────────────

_INSERT_OR_IGNORE_RE = re.compile(
    r"\bINSERT\s+OR\s+IGNORE\s+INTO\b", re.IGNORECASE
)
_INSERT_OR_REPLACE_RE = re.compile(
    r"\bINSERT\s+OR\s+REPLACE\s+INTO\b", re.IGNORECASE
)


def _sqlite_to_pg(sql: str) -> str:
    """将 SQLite 方言 SQL 转换为 PostgreSQL 兼容形式。

    转换内容：
    1. ``?`` → ``%s``
    2. ``INSERT OR IGNORE INTO t`` → ``INSERT INTO t ... ON CONFLICT DO NOTHING``
    3. ``INSERT OR REPLACE INTO t (cols) VALUES (vals)``
       → ``INSERT INTO t (cols) VALUES (vals) ON CONFLICT (first_col) DO UPDATE SET ...``
    """
    sql = sql.replace("?", "%s")

    if _INSERT_OR_IGNORE_RE.search(sql):
        sql = _INSERT_OR_IGNORE_RE.sub("INSERT INTO", sql)
        sql = sql.rstrip().rstrip(";")
        sql += " ON CONFLICT DO NOTHING"

    elif _INSERT_OR_REPLACE_RE.search(sql):
        sql = _rewrite_insert_or_replace(sql)

    return sql


def _rewrite_insert_or_replace(sql: str) -> str:
    """将 INSERT OR REPLACE 改写为 PostgreSQL 的 ON CONFLICT DO UPDATE。

    假设冲突列为第一个括号组的第一列（通常是 PRIMARY KEY）。
    """
    sql = _INSERT_OR_REPLACE_RE.sub("INSERT INTO", sql)
    match = re.search(r"INTO\s+\w+\s*\(([^)]+)\)", sql, re.IGNORECASE)
    if not match:
        return sql

    columns = [c.strip() for c in match.group(1).split(",")]
    if not columns:
        return sql

    pk = columns[0]
    update_cols = [c for c in columns if c != pk]
    if not update_cols:
        sql = sql.rstrip().rstrip(";")
        return sql + f" ON CONFLICT ({pk}) DO NOTHING"

    set_clause = ", ".join(f"{c} = EXCLUDED.{c}" for c in update_cols)
    sql = sql.rstrip().rstrip(";")
    return sql + f" ON CONFLICT ({pk}) DO UPDATE SET {set_clause}"


# ── 游标适配 ─────────────────────────────────────────────────


class CursorAdapter:
    """统一游标，确保返回 dict 式行。"""

    __slots__ = ("_cursor", "_backend", "_description")

    def __init__(self, cursor: Any, backend: str) -> None:
        self._cursor = cursor
        self._backend = backend
        self._description = None

    def fetchone(self) -> DictRow | sqlite3.Row | None:
        row = self._cursor.fetchone()
        if row is None:
            return None
        if self._backend == Backend.POSTGRES:
            return DictRow(row, self._cursor.description)
        return row

    def fetchall(self) -> list:
        rows = self._cursor.fetchall()
        if self._backend == Backend.POSTGRES and rows:
            desc = self._cursor.description
            return [DictRow(r, desc) for r in rows]
        return rows

    @property
    def rowcount(self) -> int:
        return self._cursor.rowcount

    @property
    def lastrowid(self) -> int | None:
        if self._backend == Backend.SQLITE:
            return self._cursor.lastrowid
        return None

    def close(self) -> None:
        self._cursor.close()


# ── 连接适配 ─────────────────────────────────────────────────


class ConnectionAdapter:
    """统一连接接口：所有 Store 通过此对象执行 SQL。"""

    __slots__ = ("_conn", "_backend")

    def __init__(self, conn: Any, backend: str) -> None:
        self._conn = conn
        self._backend = backend

    @property
    def backend(self) -> str:
        return self._backend

    @property
    def is_pg(self) -> bool:
        return self._backend == Backend.POSTGRES

    @property
    def raw(self) -> Any:
        """返回底层原始连接（仅迁移 / 特殊场景使用）。"""
        return self._conn

    # sqlite3.Connection.total_changes 兼容
    @property
    def total_changes(self) -> int:
        if self._backend == Backend.SQLITE:
            return self._conn.total_changes
        return 0

    def execute(self, sql: str, params: Any = None) -> CursorAdapter:
        real_sql = sql if self._backend == Backend.SQLITE else _sqlite_to_pg(sql)
        if self._backend == Backend.SQLITE:
            if params is None:
                cursor = self._conn.execute(real_sql)
            else:
                cursor = self._conn.execute(real_sql, params)
        else:
            try:
                cursor = self._conn.cursor()
                cursor.execute(real_sql, params or ())
            except Exception:
                self._conn.rollback()
                raise
        return CursorAdapter(cursor, self._backend)

    def executemany(self, sql: str, params_seq: Sequence) -> CursorAdapter:
        real_sql = sql if self._backend == Backend.SQLITE else _sqlite_to_pg(sql)
        if self._backend == Backend.SQLITE:
            cursor = self._conn.executemany(real_sql, params_seq)
        else:
            try:
                cursor = self._conn.cursor()
                for params in params_seq:
                    cursor.execute(real_sql, params)
            except Exception:
                self._conn.rollback()
                raise
        return CursorAdapter(cursor, self._backend)

    def executescript(self, script: str) -> None:
        """执行多条 SQL 语句（仅 SQLite 使用，PG 用 execute 逐条）。"""
        if self._backend == Backend.SQLITE:
            self._conn.executescript(script)
        else:
            cursor = self._conn.cursor()
            for stmt in script.split(";"):
                stmt = stmt.strip()
                if stmt:
                    cursor.execute(stmt)
            cursor.close()

    def commit(self) -> None:
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    @property
    def row_factory(self) -> Any:
        if self._backend == Backend.SQLITE:
            return self._conn.row_factory
        return None

    @row_factory.setter
    def row_factory(self, value: Any) -> None:
        if self._backend == Backend.SQLITE:
            self._conn.row_factory = value

    def table_exists(self, table_name: str) -> bool:
        """跨后端检查表是否存在。"""
        if self._backend == Backend.SQLITE:
            row = self.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table_name,),
            ).fetchone()
        else:
            row = self.execute(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = %s",
                (table_name,),
            ).fetchone()
        return row is not None


# ── 工厂 ─────────────────────────────────────────────────────


def create_sqlite_adapter(db_path: str) -> ConnectionAdapter:
    """创建 SQLite 连接适配器。"""
    from pathlib import Path
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return ConnectionAdapter(conn, Backend.SQLITE)


def create_pg_adapter(database_url: str) -> ConnectionAdapter:
    """创建 PostgreSQL 连接适配器。"""
    import psycopg2
    conn = psycopg2.connect(database_url)
    conn.autocommit = False
    return ConnectionAdapter(conn, Backend.POSTGRES)
