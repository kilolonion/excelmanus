"""Database 连接管理与 schema 迁移测试。"""
from __future__ import annotations

from pathlib import Path

import pytest

from excelmanus.database import Database


class TestDatabase:
    def test_creates_db_file_and_tables(self, tmp_path: Path) -> None:
        db_path = str(tmp_path / "test.db")
        db = Database(db_path)
        assert Path(db_path).exists()
        row = db.conn.execute(
            "SELECT version FROM schema_version ORDER BY version DESC LIMIT 1"
        ).fetchone()
        assert row is not None
        assert row["version"] >= 1
        db.close()

    def test_wal_mode_enabled(self, tmp_path: Path) -> None:
        db = Database(str(tmp_path / "test.db"))
        mode = db.conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"
        db.close()

    def test_foreign_keys_enabled(self, tmp_path: Path) -> None:
        db = Database(str(tmp_path / "test.db"))
        fk = db.conn.execute("PRAGMA foreign_keys").fetchone()[0]
        assert fk == 1
        db.close()

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        db_path = str(tmp_path / "nested" / "dir" / "test.db")
        db = Database(db_path)
        assert Path(db_path).exists()
        db.close()

    def test_idempotent_open(self, tmp_path: Path) -> None:
        db_path = str(tmp_path / "test.db")
        db1 = Database(db_path)
        db1.close()
        db2 = Database(db_path)
        row = db2.conn.execute(
            "SELECT COUNT(*) as cnt FROM schema_version"
        ).fetchone()
        assert row["cnt"] >= 1
        db2.close()

    def test_all_domain_tables_exist(self, tmp_path: Path) -> None:
        db = Database(str(tmp_path / "test.db"))
        tables = {
            row[0]
            for row in db.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        for expected in (
            "schema_version",
            "sessions",
            "messages",
            "memory_entries",
            "vector_records",
            "approvals",
        ):
            assert expected in tables, f"缺少表: {expected}"
        db.close()

    def test_schema_version_increments(self, tmp_path: Path) -> None:
        """打开后 schema_version 应至少为 1。"""
        db = Database(str(tmp_path / "test.db"))
        version = db.conn.execute(
            "SELECT MAX(version) as v FROM schema_version"
        ).fetchone()["v"]
        assert version >= 1
        db.close()

    def test_migration_is_idempotent(self, tmp_path: Path) -> None:
        """多次打开同一个 DB 不应重复执行迁移。"""
        db_path = str(tmp_path / "test.db")
        db1 = Database(db_path)
        v1 = db1.conn.execute(
            "SELECT COUNT(*) as cnt FROM schema_version"
        ).fetchone()["cnt"]
        db1.close()

        db2 = Database(db_path)
        v2 = db2.conn.execute(
            "SELECT COUNT(*) as cnt FROM schema_version"
        ).fetchone()["cnt"]
        db2.close()
        assert v1 == v2
