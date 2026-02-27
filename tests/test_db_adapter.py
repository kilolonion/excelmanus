"""db_adapter SQL 转换与连接适配器测试。"""
from __future__ import annotations

from pathlib import Path

import pytest

from excelmanus.db_adapter import (
    Backend,
    ConnectionAdapter,
    CursorAdapter,
    DictRow,
    _sqlite_to_pg,
    create_sqlite_adapter,
)


class TestSqliteToPg:
    """SQL 方言转换单元测试。"""

    def test_placeholder_conversion(self) -> None:
        sql = "SELECT * FROM users WHERE id = ? AND name = ?"
        assert _sqlite_to_pg(sql) == "SELECT * FROM users WHERE id = %s AND name = %s"

    def test_insert_or_ignore(self) -> None:
        sql = "INSERT OR IGNORE INTO memory_entries (category, content) VALUES (?, ?)"
        result = _sqlite_to_pg(sql)
        assert "INSERT INTO" in result
        assert "OR IGNORE" not in result
        assert "ON CONFLICT DO NOTHING" in result

    def test_insert_or_ignore_case_insensitive(self) -> None:
        sql = "insert or ignore into t (a) values (?)"
        result = _sqlite_to_pg(sql)
        assert "ON CONFLICT DO NOTHING" in result
        assert "or ignore" not in result.lower()

    def test_insert_or_replace(self) -> None:
        sql = "INSERT OR REPLACE INTO approvals (id, tool_name, status) VALUES (?, ?, ?)"
        result = _sqlite_to_pg(sql)
        assert "OR REPLACE" not in result
        assert "ON CONFLICT (id) DO UPDATE SET" in result
        assert "tool_name = EXCLUDED.tool_name" in result
        assert "status = EXCLUDED.status" in result

    def test_insert_or_replace_single_column(self) -> None:
        sql = "INSERT OR REPLACE INTO kv (key) VALUES (?)"
        result = _sqlite_to_pg(sql)
        assert "ON CONFLICT (key) DO NOTHING" in result

    def test_plain_insert_unchanged(self) -> None:
        sql = "INSERT INTO messages (session_id, role) VALUES (?, ?)"
        result = _sqlite_to_pg(sql)
        assert result == "INSERT INTO messages (session_id, role) VALUES (%s, %s)"

    def test_select_unchanged(self) -> None:
        sql = "SELECT * FROM sessions WHERE id = ?"
        assert _sqlite_to_pg(sql) == "SELECT * FROM sessions WHERE id = %s"

    def test_on_conflict_already_present_not_modified(self) -> None:
        sql = (
            "INSERT INTO config_kv (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value"
        )
        result = _sqlite_to_pg(sql)
        assert result == (
            "INSERT INTO config_kv (key, value) VALUES (%s, %s) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value"
        )


class TestDictRow:
    """DictRow 行包装测试。"""

    def test_getitem_by_name(self) -> None:
        desc = [("id",), ("name",), ("email",)]
        row = DictRow(("1", "test", "test@example.com"), desc)
        assert row["id"] == "1"
        assert row["name"] == "test"
        assert row["email"] == "test@example.com"

    def test_getitem_by_index(self) -> None:
        desc = [("id",), ("name",)]
        row = DictRow(("1", "test"), desc)
        assert row[0] == "1"
        assert row[1] == "test"

    def test_keys_values_items(self) -> None:
        desc = [("a",), ("b",)]
        row = DictRow((10, 20), desc)
        assert row.keys() == ["a", "b"]
        assert row.values() == [10, 20]
        assert row.items() == [("a", 10), ("b", 20)]

    def test_dict_conversion(self) -> None:
        desc = [("id",), ("name",)]
        row = DictRow(("1", "test"), desc)
        d = dict(row)
        assert d == {"id": "1", "name": "test"}

    def test_contains(self) -> None:
        desc = [("id",), ("name",)]
        row = DictRow(("1", "test"), desc)
        assert "id" in row
        assert "missing" not in row

    def test_get_with_default(self) -> None:
        desc = [("id",)]
        row = DictRow(("1",), desc)
        assert row.get("id") == "1"
        assert row.get("missing", "default") == "default"


class TestConnectionAdapterSqlite:
    """SQLite 后端的 ConnectionAdapter 集成测试。"""

    def test_create_and_execute(self, tmp_path: Path) -> None:
        adapter = create_sqlite_adapter(str(tmp_path / "test.db"))
        adapter.execute("CREATE TABLE t (id TEXT PRIMARY KEY, val TEXT)")
        adapter.execute("INSERT INTO t VALUES (?, ?)", ("1", "hello"))
        adapter.commit()
        row = adapter.execute("SELECT * FROM t WHERE id = ?", ("1",)).fetchone()
        assert row is not None
        assert row["id"] == "1"
        assert row["val"] == "hello"
        adapter.close()

    def test_fetchall(self, tmp_path: Path) -> None:
        adapter = create_sqlite_adapter(str(tmp_path / "test.db"))
        adapter.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, val TEXT)")
        adapter.execute("INSERT INTO t VALUES (1, 'a')")
        adapter.execute("INSERT INTO t VALUES (2, 'b')")
        adapter.commit()
        rows = adapter.execute("SELECT * FROM t ORDER BY id").fetchall()
        assert len(rows) == 2
        assert rows[0]["val"] == "a"
        assert rows[1]["val"] == "b"
        adapter.close()

    def test_executemany(self, tmp_path: Path) -> None:
        adapter = create_sqlite_adapter(str(tmp_path / "test.db"))
        adapter.execute("CREATE TABLE t (id INTEGER, val TEXT)")
        adapter.executemany(
            "INSERT INTO t VALUES (?, ?)", [(1, "a"), (2, "b"), (3, "c")]
        )
        adapter.commit()
        row = adapter.execute("SELECT COUNT(*) as cnt FROM t").fetchone()
        assert row["cnt"] == 3
        adapter.close()

    def test_table_exists(self, tmp_path: Path) -> None:
        adapter = create_sqlite_adapter(str(tmp_path / "test.db"))
        assert not adapter.table_exists("nonexistent")
        adapter.execute("CREATE TABLE my_table (id INTEGER PRIMARY KEY)")
        adapter.commit()
        assert adapter.table_exists("my_table")
        adapter.close()

    def test_backend_property(self, tmp_path: Path) -> None:
        adapter = create_sqlite_adapter(str(tmp_path / "test.db"))
        assert adapter.backend == Backend.SQLITE
        assert not adapter.is_pg
        adapter.close()

    def test_rowcount(self, tmp_path: Path) -> None:
        adapter = create_sqlite_adapter(str(tmp_path / "test.db"))
        adapter.execute("CREATE TABLE t (id TEXT PRIMARY KEY)")
        adapter.execute("INSERT INTO t VALUES ('a')")
        adapter.commit()
        cur = adapter.execute("DELETE FROM t WHERE id = ?", ("a",))
        assert cur.rowcount == 1
        cur = adapter.execute("DELETE FROM t WHERE id = ?", ("nonexistent",))
        assert cur.rowcount == 0
        adapter.close()


class TestDatabaseWithAdapter:
    """Database 与 ConnectionAdapter 集成测试。"""

    def test_database_conn_returns_adapter(self, tmp_path: Path) -> None:
        from excelmanus.database import Database
        db = Database(str(tmp_path / "test.db"))
        assert isinstance(db.conn, ConnectionAdapter)
        assert db.conn.backend == Backend.SQLITE
        assert not db.is_pg
        db.close()

    def test_database_backend_property(self, tmp_path: Path) -> None:
        from excelmanus.database import Database
        db = Database(str(tmp_path / "test.db"))
        assert db.backend == Backend.SQLITE
        db.close()

    def test_stores_work_with_adapter(self, tmp_path: Path) -> None:
        from excelmanus.database import Database
        from excelmanus.stores.memory_store import MemoryStore
        from excelmanus.memory_models import MemoryCategory, MemoryEntry

        db = Database(str(tmp_path / "test.db"))
        store = MemoryStore(db)
        from datetime import datetime
        entry = MemoryEntry(
            content="Test memory",
            category=MemoryCategory.USER_PREF,
            timestamp=datetime.now(),
        )
        count = store.save_entries([entry])
        assert count == 1
        assert store.count() == 1
        db.close()
