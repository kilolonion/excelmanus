"""db_adapter SQLite 连接适配器测试。"""
from __future__ import annotations

from pathlib import Path

from excelmanus.db_adapter import (
    Backend,
    ConnectionAdapter,
    create_sqlite_adapter,
)


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


class TestConnectionAdapterMutex:
    def test_concurrent_inserts_are_serialized(self, tmp_path: Path) -> None:
        import threading

        adapter = create_sqlite_adapter(str(tmp_path / "lock.db"))
        adapter.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, val TEXT)")
        adapter.commit()
        errors: list[BaseException] = []

        def _writer(tag: str) -> None:
            try:
                for i in range(20):
                    adapter.execute("INSERT INTO t (val) VALUES (?)", (f"{tag}-{i}",))
                    adapter.commit()
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=_writer, args=(name,)) for name in ("a", "b")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert errors == []
        row = adapter.execute("SELECT COUNT(*) as cnt FROM t").fetchone()
        assert row["cnt"] == 40
        adapter.close()


class TestDatabaseWithAdapter:
    """Database 与 ConnectionAdapter 集成测试。"""

    def test_database_conn_returns_adapter(self, tmp_path: Path) -> None:
        from excelmanus.database import Database
        db = Database(str(tmp_path / "test.db"))
        assert isinstance(db.conn, ConnectionAdapter)
        assert db.conn.backend == Backend.SQLITE
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
