"""数据库迁移安全性测试 — 覆盖部分失败恢复、幂等性、备份、跨版本升级、一致性守护。"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from excelmanus.database import (
    Database,
    _LATEST_VERSION,
    _PG_MIGRATIONS,
    _SQLITE_MIGRATIONS,
)


class TestSqliteAlterTableIdempotent:
    """SQLite ALTER TABLE 幂等保护：列已存在时不崩溃。"""

    def test_reopen_after_partial_migration_does_not_crash(self, tmp_path: Path) -> None:
        """模拟：v10 部分执行（部分列已加），重新打开不应报错。"""
        db_path = str(tmp_path / "test.db")
        # 正常创建到最新版本
        db = Database(db_path)
        db.close()

        # 人为回退 schema_version 到 v7（假装 v8-17 未完成）
        conn = sqlite3.connect(db_path)
        conn.execute("DELETE FROM schema_version WHERE version > 7")
        conn.commit()
        conn.close()

        # 重新打开 — 应安全地跳过已存在的列
        db2 = Database(db_path)
        current = db2._current_version()
        assert current == _LATEST_VERSION
        db2.close()

    def test_alter_table_column_already_exists(self, tmp_path: Path) -> None:
        """直接测试 _safe_execute_sql 跳过已存在列。"""
        db_path = str(tmp_path / "test.db")
        db = Database(db_path)

        # sessions.user_id 已在 migration 8 中创建
        # 再次执行应安全跳过
        db._safe_execute_sql("ALTER TABLE sessions ADD COLUMN user_id TEXT")
        db.close()

    def test_alter_table_new_column_succeeds(self, tmp_path: Path) -> None:
        """_safe_execute_sql 对不存在的列正常执行。"""
        db_path = str(tmp_path / "test.db")
        db = Database(db_path)
        db._safe_execute_sql(
            "ALTER TABLE sessions ADD COLUMN test_col_xyz TEXT DEFAULT ''"
        )
        # 验证列已添加
        assert db._sqlite_column_exists("sessions", "test_col_xyz")
        db.close()


class TestMigrationBackup:
    """迁移前自动备份。"""

    def test_backup_created_on_upgrade(self, tmp_path: Path) -> None:
        """从旧版本升级时应创建 .bak 文件。"""
        db_path = str(tmp_path / "test.db")
        # 手动创建一个 v1 数据库
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            "CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TEXT)"
        )
        for sql in _SQLITE_MIGRATIONS[1]:
            conn.execute(sql)
        conn.execute("INSERT INTO schema_version (version, applied_at) VALUES (1, 'now')")
        conn.commit()
        conn.close()

        # 打开 Database 触发迁移 v2 -> v_LATEST
        db = Database(db_path)
        db.close()

        # 检查备份文件存在
        bak_files = list(tmp_path.glob("*.bak"))
        assert len(bak_files) >= 1
        bak_name = bak_files[0].name
        assert "v1_to_v" in bak_name

    def test_no_backup_for_fresh_db(self, tmp_path: Path) -> None:
        """全新数据库无需备份。"""
        db_path = str(tmp_path / "test.db")
        db = Database(db_path)
        db.close()

        bak_files = list(tmp_path.glob("*.bak"))
        assert len(bak_files) == 0

    def test_cleanup_old_backups(self, tmp_path: Path) -> None:
        """cleanup_migration_backups 保留最近 N 个。"""
        db_path = str(tmp_path / "test.db")
        # 创建 5 个假备份
        for i in range(5):
            (tmp_path / f"test.v{i}_to_v{i+1}.bak").write_text(f"bak{i}")

        Database.cleanup_migration_backups(db_path, keep=2)
        remaining = list(tmp_path.glob("test.v*_to_v*.bak"))
        assert len(remaining) == 2


class TestMigrationErrorHandling:
    """迁移失败时的错误处理。"""

    def test_migration_failure_raises_runtime_error(self, tmp_path: Path) -> None:
        """迁移中某条 SQL 失败应抛出 RuntimeError。"""
        db_path = str(tmp_path / "test.db")

        # 创建 v1 数据库
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            "CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TEXT)"
        )
        for sql in _SQLITE_MIGRATIONS[1]:
            conn.execute(sql)
        conn.execute("INSERT INTO schema_version (version, applied_at) VALUES (1, 'now')")
        conn.commit()
        conn.close()

        # 注入一条必定失败的 SQL 到 v2
        bad_migrations = dict(_SQLITE_MIGRATIONS)
        bad_migrations[2] = ["THIS IS INVALID SQL THAT WILL FAIL"]

        with patch("excelmanus.database._SQLITE_MIGRATIONS", bad_migrations):
            with pytest.raises(RuntimeError, match="v2 失败"):
                Database(db_path)

    def test_partial_migration_preserves_successful_versions(self, tmp_path: Path) -> None:
        """部分迁移成功的版本应被记录。"""
        db_path = str(tmp_path / "test.db")

        # 创建 v1 数据库
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TEXT)"
        )
        for sql in _SQLITE_MIGRATIONS[1]:
            conn.execute(sql)
        conn.execute("INSERT INTO schema_version (version, applied_at) VALUES (1, 'now')")
        conn.commit()
        conn.close()

        # v2 正常，v3 注入失败
        bad_migrations = dict(_SQLITE_MIGRATIONS)
        bad_migrations[3] = ["THIS IS INVALID SQL"]

        with patch("excelmanus.database._SQLITE_MIGRATIONS", bad_migrations):
            with pytest.raises(RuntimeError, match="v3 失败"):
                Database(db_path)

        # v2 应已成功记录
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT MAX(version) as v FROM schema_version"
        ).fetchone()
        assert row["v"] == 2
        conn.close()


class TestCrossVersionUpgrade:
    """跨多个版本升级的完整性。"""

    def test_upgrade_from_v1_to_latest(self, tmp_path: Path) -> None:
        """从 v1 一路升级到最新版本。"""
        db_path = str(tmp_path / "test.db")

        # 手动创建 v1
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            "CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TEXT)"
        )
        for sql in _SQLITE_MIGRATIONS[1]:
            conn.execute(sql)
        conn.execute("INSERT INTO schema_version (version, applied_at) VALUES (1, 'now')")
        conn.commit()
        conn.close()

        db = Database(db_path)
        assert db._current_version() == _LATEST_VERSION
        db.close()

    def test_all_migration_versions_recorded(self, tmp_path: Path) -> None:
        """所有中间版本都应被记录在 schema_version 表中。"""
        db_path = str(tmp_path / "test.db")
        db = Database(db_path)

        rows = db.conn.execute(
            "SELECT version FROM schema_version ORDER BY version"
        ).fetchall()
        versions = [r["version"] for r in rows]
        assert versions == list(range(1, _LATEST_VERSION + 1))
        db.close()

    def test_migration_8_adds_user_id_to_sessions(self, tmp_path: Path) -> None:
        """验证 migration 8 确实为 sessions 表添加了 user_id 列。"""
        db_path = str(tmp_path / "test.db")
        db = Database(db_path)
        assert db._sqlite_column_exists("sessions", "user_id")
        db.close()

    def test_migration_13_adds_session_id_to_approvals(self, tmp_path: Path) -> None:
        """验证 migration 13 确实为 approvals 表添加了 session_id 列。"""
        db_path = str(tmp_path / "test.db")
        db = Database(db_path)
        assert db._sqlite_column_exists("approvals", "session_id")
        db.close()

    def test_migration_17_adds_model_profile_columns(self, tmp_path: Path) -> None:
        """验证 migration 17 为 model_profiles 添加了所有新列。"""
        db_path = str(tmp_path / "test.db")
        db = Database(db_path)
        for col in ("thinking_mode", "model_family", "custom_extra_body", "custom_extra_headers"):
            assert db._sqlite_column_exists("model_profiles", col), f"缺少列: {col}"
        db.close()


class TestScopedDatabaseMigration:
    """ScopedDatabase 用户级 DB 迁移安全性。"""

    def test_user_db_gets_latest_schema(self, tmp_path: Path) -> None:
        """用户级 SQLite DB 也应迁移到最新版本。"""
        from excelmanus.scoped_database import ScopedDatabase
        from excelmanus.user_context import UserContext

        # 创建共享 Database
        shared_db = Database(str(tmp_path / "shared.db"))

        # 创建用户上下文
        user_root = tmp_path / "users" / "test-user-123"
        user_root.mkdir(parents=True)
        ctx = UserContext.create(
            "test-user-123",
            global_workspace_root=str(tmp_path),
            data_root=str(tmp_path),
        )

        scoped = ScopedDatabase(ctx, shared_db)

        # 验证用户 DB 也到了最新版本
        user_db_path = ctx.workspace_root / "data.db"
        if user_db_path.exists():
            conn = sqlite3.connect(str(user_db_path))
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT MAX(version) as v FROM schema_version"
            ).fetchone()
            assert row["v"] == _LATEST_VERSION
            conn.close()

        scoped.close()
        shared_db.close()


# ── SQLite / PG 迁移一致性守护 ────────────────────────────────────


class TestMigrationParity:
    """确保 SQLite 和 PostgreSQL 迁移定义始终保持同步。

    如果有人只加了 SQLite 迁移忘了加 PG（或反过来），这些测试会立刻失败。
    """

    def test_version_keys_identical(self) -> None:
        """两个 dict 的版本号集合必须完全一致。"""
        assert sorted(_SQLITE_MIGRATIONS.keys()) == sorted(_PG_MIGRATIONS.keys()), (
            "SQLite 和 PG 迁移版本号不一致！\n"
            f"  SQLite: {sorted(_SQLITE_MIGRATIONS.keys())}\n"
            f"  PG:     {sorted(_PG_MIGRATIONS.keys())}"
        )

    def test_statement_count_per_version(self) -> None:
        """每个版本的语句数必须相同（DDL 逻辑应对称）。"""
        for v in _SQLITE_MIGRATIONS:
            s_count = len(_SQLITE_MIGRATIONS[v])
            p_count = len(_PG_MIGRATIONS[v])
            assert s_count == p_count, (
                f"v{v} 语句数不一致: SQLite={s_count}, PG={p_count}"
            )

    def test_latest_version_consistent(self) -> None:
        """_LATEST_VERSION 应等于两个 dict 的最大 key。"""
        assert _LATEST_VERSION == max(_SQLITE_MIGRATIONS.keys())
        assert _LATEST_VERSION == max(_PG_MIGRATIONS.keys())

    def test_no_gaps_in_version_sequence(self) -> None:
        """版本号必须连续，不能跳号。"""
        versions = sorted(_SQLITE_MIGRATIONS.keys())
        expected = list(range(1, max(versions) + 1))
        assert versions == expected, (
            f"版本号有间隔: {versions} vs expected {expected}"
        )

    def test_sqlite_alter_table_has_no_if_not_exists(self) -> None:
        """SQLite ALTER TABLE 不支持 IF NOT EXISTS，确认不含此语法。

        如果有人误加了 IF NOT EXISTS 到 SQLite ALTER TABLE，_safe_execute_sql
        的幂等检查就不会触发（因为 SQLite 会直接报语法错误）。
        """
        alter_re = re.compile(
            r"ALTER\s+TABLE\s+\S+\s+ADD\s+COLUMN\s+IF\s+NOT\s+EXISTS",
            re.IGNORECASE,
        )
        for v, stmts in _SQLITE_MIGRATIONS.items():
            for sql in stmts:
                assert not alter_re.search(sql), (
                    f"SQLite v{v} 含 'ADD COLUMN IF NOT EXISTS'，"
                    f"SQLite 不支持此语法: {sql[:80]}"
                )


# ── 模拟未来结构变更 ──────────────────────────────────────────────


class TestFutureSchemaChanges:
    """模拟未来可能的各种结构变更，验证迁移系统能正确处理。"""

    def _create_db_at_latest(self, tmp_path: Path) -> str:
        """创建一个已迁移到最新版本的 DB。"""
        db_path = str(tmp_path / "test.db")
        db = Database(db_path)
        db.close()
        return db_path

    def test_add_new_table(self, tmp_path: Path) -> None:
        """未来版本新增表 — 应正常迁移。"""
        db_path = self._create_db_at_latest(tmp_path)

        future_version = _LATEST_VERSION + 1
        extended = dict(_SQLITE_MIGRATIONS)
        extended[future_version] = [
            """CREATE TABLE IF NOT EXISTS future_feature (
                id   INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                data TEXT DEFAULT '{}'
            )""",
            "CREATE INDEX IF NOT EXISTS idx_ff_name ON future_feature(name)",
        ]

        with patch("excelmanus.database._SQLITE_MIGRATIONS", extended), \
             patch("excelmanus.database._LATEST_VERSION", future_version):
            db = Database(db_path)
            assert db._current_version() == future_version
            # 验证表存在
            row = db.conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='future_feature'"
            ).fetchone()
            assert row is not None
            db.close()

    def test_add_new_column(self, tmp_path: Path) -> None:
        """未来版本为已有表新增列 — 应正常迁移。"""
        db_path = self._create_db_at_latest(tmp_path)

        future_version = _LATEST_VERSION + 1
        extended = dict(_SQLITE_MIGRATIONS)
        extended[future_version] = [
            "ALTER TABLE sessions ADD COLUMN priority INTEGER DEFAULT 0",
        ]

        with patch("excelmanus.database._SQLITE_MIGRATIONS", extended), \
             patch("excelmanus.database._LATEST_VERSION", future_version):
            db = Database(db_path)
            assert db._sqlite_column_exists("sessions", "priority")
            db.close()

    def test_add_new_column_is_idempotent(self, tmp_path: Path) -> None:
        """新增列后再次打开（模拟 schema_version 丢失）不崩溃。"""
        db_path = self._create_db_at_latest(tmp_path)

        future_version = _LATEST_VERSION + 1
        extended = dict(_SQLITE_MIGRATIONS)
        extended[future_version] = [
            "ALTER TABLE sessions ADD COLUMN extra_info TEXT DEFAULT ''",
        ]

        with patch("excelmanus.database._SQLITE_MIGRATIONS", extended), \
             patch("excelmanus.database._LATEST_VERSION", future_version):
            # 第一次：正常迁移
            db = Database(db_path)
            db.close()

            # 人为回退版本号
            conn = sqlite3.connect(db_path)
            conn.execute(f"DELETE FROM schema_version WHERE version = {future_version}")
            conn.commit()
            conn.close()

            # 第二次：应安全跳过已存在的列
            db2 = Database(db_path)
            assert db2._current_version() == future_version
            db2.close()

    def test_add_new_index_only(self, tmp_path: Path) -> None:
        """未来版本仅添加索引 — 应正常迁移。"""
        db_path = self._create_db_at_latest(tmp_path)

        future_version = _LATEST_VERSION + 1
        extended = dict(_SQLITE_MIGRATIONS)
        extended[future_version] = [
            "CREATE INDEX IF NOT EXISTS idx_sessions_status_updated "
            "ON sessions(status, updated_at)",
        ]

        with patch("excelmanus.database._SQLITE_MIGRATIONS", extended), \
             patch("excelmanus.database._LATEST_VERSION", future_version):
            db = Database(db_path)
            assert db._current_version() == future_version
            db.close()

    def test_data_migration_with_dml(self, tmp_path: Path) -> None:
        """未来版本含 DML 数据迁移（INSERT/UPDATE）— 应正常执行。"""
        db_path = self._create_db_at_latest(tmp_path)

        # 先插入一些测试数据
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO config_kv (key, value, updated_at) "
            "VALUES ('test_key', 'old_value', '2026-01-01')"
        )
        conn.commit()
        conn.close()

        future_version = _LATEST_VERSION + 1
        extended = dict(_SQLITE_MIGRATIONS)
        extended[future_version] = [
            # DML: 更新已有数据
            "UPDATE config_kv SET value = 'migrated_value' WHERE key = 'test_key'",
        ]

        with patch("excelmanus.database._SQLITE_MIGRATIONS", extended), \
             patch("excelmanus.database._LATEST_VERSION", future_version):
            db = Database(db_path)
            row = db.conn.execute(
                "SELECT value FROM config_kv WHERE key = 'test_key'"
            ).fetchone()
            assert row["value"] == "migrated_value"
            db.close()

    def test_mixed_ddl_and_dml(self, tmp_path: Path) -> None:
        """未来版本混合 DDL + DML — 应按顺序执行。"""
        db_path = self._create_db_at_latest(tmp_path)

        future_version = _LATEST_VERSION + 1
        extended = dict(_SQLITE_MIGRATIONS)
        extended[future_version] = [
            # 1. DDL: 新增表
            """CREATE TABLE IF NOT EXISTS feature_flags (
                key TEXT PRIMARY KEY,
                enabled INTEGER DEFAULT 0
            )""",
            # 2. DML: 插入默认数据
            "INSERT OR IGNORE INTO feature_flags (key, enabled) VALUES ('beta_mode', 0)",
            "INSERT OR IGNORE INTO feature_flags (key, enabled) VALUES ('debug_mode', 0)",
        ]

        with patch("excelmanus.database._SQLITE_MIGRATIONS", extended), \
             patch("excelmanus.database._LATEST_VERSION", future_version):
            db = Database(db_path)
            count = db.conn.execute(
                "SELECT COUNT(*) as cnt FROM feature_flags"
            ).fetchone()["cnt"]
            assert count == 2
            db.close()

    def test_multi_version_jump(self, tmp_path: Path) -> None:
        """一次跨多个新版本 — 应逐个版本按序迁移。"""
        db_path = self._create_db_at_latest(tmp_path)

        v18 = _LATEST_VERSION + 1
        v19 = _LATEST_VERSION + 2
        v20 = _LATEST_VERSION + 3
        extended = dict(_SQLITE_MIGRATIONS)
        extended[v18] = [
            "ALTER TABLE sessions ADD COLUMN tags TEXT DEFAULT '[]'",
        ]
        extended[v19] = [
            "ALTER TABLE sessions ADD COLUMN archived_at TEXT",
        ]
        extended[v20] = [
            """CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                action TEXT NOT NULL,
                created_at TEXT NOT NULL
            )""",
        ]

        with patch("excelmanus.database._SQLITE_MIGRATIONS", extended), \
             patch("excelmanus.database._LATEST_VERSION", v20):
            db = Database(db_path)
            assert db._current_version() == v20
            assert db._sqlite_column_exists("sessions", "tags")
            assert db._sqlite_column_exists("sessions", "archived_at")
            row = db.conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='audit_log'"
            ).fetchone()
            assert row is not None

            # 验证所有版本都被记录
            rows = db.conn.execute(
                "SELECT version FROM schema_version ORDER BY version"
            ).fetchall()
            versions = [r["version"] for r in rows]
            assert v18 in versions
            assert v19 in versions
            assert v20 in versions
            db.close()
