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
            "approvals",
        ):
            assert expected in tables, f"缺少表: {expected}"
        assert "vector_records" not in tables
        db.close()

    def test_sessions_schema_has_no_archive_status(self, tmp_path: Path) -> None:
        db = Database(str(tmp_path / "test.db"))
        columns = {
            row["name"]
            for row in db.conn.execute("PRAGMA table_info(sessions)").fetchall()
        }
        assert "status" not in columns
        db.close()

    def test_messages_unique_session_message_id(self, tmp_path: Path) -> None:
        db = Database(str(tmp_path / "test.db"))
        columns = {
            row["name"]
            for row in db.conn.execute("PRAGMA table_info(messages)").fetchall()
        }
        assert "message_id" in columns
        indexes = {
            row["name"]
            for row in db.conn.execute("PRAGMA index_list(messages)").fetchall()
        }
        assert "idx_messages_session_message_id" in indexes
        db.close()

    def test_schema_version_increments(self, tmp_path: Path) -> None:
        """打开后 schema_version 应至少为 1。"""
        db = Database(str(tmp_path / "test.db"))
        version = db.conn.execute(
            "SELECT MAX(version) as v FROM schema_version"
        ).fetchone()["v"]
        assert version >= 1
        db.close()

    def test_v2_session_workspace_columns_and_workspaces_table(self, tmp_path: Path) -> None:
        db = Database(str(tmp_path / "test.db"))
        version = db.conn.execute(
            "SELECT MAX(version) as v FROM schema_version"
        ).fetchone()["v"]
        assert version >= 2
        cols = {
            row["name"] for row in db.conn.execute("PRAGMA table_info(sessions)").fetchall()
        }
        assert {"workspace_path", "workspace_id", "blank"} <= cols
        names = {
            row["name"]
            for row in db.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "workspaces" in names
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


class TestLegacyEmbeddingRemoval:
    def test_session_summaries_has_no_embedding_column(self, tmp_path: Path) -> None:
        db = Database(str(tmp_path / "test.db"))
        columns = {
            row["name"]
            for row in db.conn.execute("PRAGMA table_info(session_summaries)").fetchall()
        }
        assert "embedding" not in columns
        db.close()

    def test_legacy_embedding_column_is_dropped_on_upgrade(self, tmp_path: Path) -> None:
        """v3 旧库的 embedding 列在升级时应被移除，且摘要数据保留。"""
        db_path = str(tmp_path / "test.db")
        db = Database(db_path)
        db.conn.execute("ALTER TABLE session_summaries ADD COLUMN embedding BLOB")
        db.conn.execute(
            "INSERT INTO session_summaries "
            "(session_id, summary_text, created_at, updated_at) VALUES (?, ?, ?, ?)",
            ("sess-1", "keep-me", "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
        )
        db.conn.execute("DELETE FROM schema_version WHERE version >= 4")
        db.conn.commit()
        db.close()

        upgraded = Database(db_path)
        columns = {
            row["name"]
            for row in upgraded.conn.execute("PRAGMA table_info(session_summaries)").fetchall()
        }
        assert "embedding" not in columns
        row = upgraded.conn.execute(
            "SELECT summary_text FROM session_summaries WHERE session_id = ?",
            ("sess-1",),
        ).fetchone()
        assert row is not None and row["summary_text"] == "keep-me"
        upgraded.close()
