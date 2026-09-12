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
    _SQLITE_MIGRATIONS,
)


class TestSqliteAlterTableIdempotent:
    """SQLite ALTER TABLE 幂等保护：列已存在时不崩溃。"""

    def test_reopen_after_partial_migration_does_not_crash(self, tmp_path: Path) -> None:
        """未来版本部分列已加、schema_version 回退后重新打开不应报错。"""
        db_path = str(tmp_path / "test.db")
        db = Database(db_path)
        db.close()

        future_version = _LATEST_VERSION + 1
        extended = dict(_SQLITE_MIGRATIONS)
        extended[future_version] = [
            "ALTER TABLE sessions ADD COLUMN extra_col TEXT DEFAULT ''",
            "ALTER TABLE sessions ADD COLUMN extra_col2 TEXT DEFAULT ''",
        ]
        with patch("excelmanus.database._SQLITE_MIGRATIONS", extended), \
             patch("excelmanus.database._LATEST_VERSION", future_version):
            db2 = Database(db_path)
            db2.close()

            conn = sqlite3.connect(db_path)
            conn.execute(
                f"DELETE FROM schema_version WHERE version = {future_version}"
            )
            conn.commit()
            conn.close()

            db3 = Database(db_path)
            assert db3._current_version() == future_version
            db3.close()

    def test_alter_table_column_already_exists(self, tmp_path: Path) -> None:
        """直接测试 _safe_execute_sql 跳过已存在列。"""
        db_path = str(tmp_path / "test.db")
        db = Database(db_path)

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
        """从当前版本升到未来版本时应创建 .bak 文件。"""
        db_path = str(tmp_path / "test.db")
        db = Database(db_path)
        db.close()

        future_version = _LATEST_VERSION + 1
        extended = dict(_SQLITE_MIGRATIONS)
        extended[future_version] = [
            "ALTER TABLE sessions ADD COLUMN bak_col TEXT DEFAULT ''",
        ]
        with patch("excelmanus.database._SQLITE_MIGRATIONS", extended), \
             patch("excelmanus.database._LATEST_VERSION", future_version):
            db2 = Database(db_path)
            db2.close()

        bak_files = list(tmp_path.glob("*.bak"))
        assert len(bak_files) >= 1
        bak_name = bak_files[0].name
        assert f"v{_LATEST_VERSION}_to_v{future_version}" in bak_name

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
        db = Database(db_path)
        db.close()

        future_version = _LATEST_VERSION + 1
        bad_migrations = dict(_SQLITE_MIGRATIONS)
        bad_migrations[future_version] = ["THIS IS INVALID SQL THAT WILL FAIL"]

        with patch("excelmanus.database._SQLITE_MIGRATIONS", bad_migrations), \
             patch("excelmanus.database._LATEST_VERSION", future_version):
            with pytest.raises(RuntimeError, match=f"v{future_version} 失败"):
                Database(db_path)

    def test_partial_migration_preserves_successful_versions(self, tmp_path: Path) -> None:
        """部分迁移成功的版本应被记录。"""
        db_path = str(tmp_path / "test.db")
        db = Database(db_path)
        db.close()

        v_ok = _LATEST_VERSION + 1
        v_bad = _LATEST_VERSION + 2
        bad_migrations = dict(_SQLITE_MIGRATIONS)
        bad_migrations[v_ok] = [
            "ALTER TABLE sessions ADD COLUMN ok_col TEXT DEFAULT ''",
        ]
        bad_migrations[v_bad] = ["THIS IS INVALID SQL"]

        with patch("excelmanus.database._SQLITE_MIGRATIONS", bad_migrations), \
             patch("excelmanus.database._LATEST_VERSION", v_bad):
            with pytest.raises(RuntimeError, match=f"v{v_bad} 失败"):
                Database(db_path)

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT MAX(version) as v FROM schema_version"
        ).fetchone()
        assert row["v"] == v_ok
        conn.close()


class TestCrossVersionUpgrade:
    """跨多个版本升级的完整性。"""

    def test_fresh_db_is_current_form(self, tmp_path: Path) -> None:
        """空库打开后即为当前形态，无需 1→25 梯子。"""
        db_path = str(tmp_path / "test.db")
        db = Database(db_path)
        assert db._current_version() == _LATEST_VERSION
        assert not db._sqlite_column_exists("sessions", "status")
        assert db._sqlite_column_exists("sessions", "user_id")
        assert db._sqlite_column_exists("messages", "message_id")
        assert db._sqlite_column_exists("approvals", "session_id")
        for col in (
            "thinking_mode", "model_family",
            "custom_extra_body", "custom_extra_headers",
        ):
            assert db._sqlite_column_exists("model_profiles", col), f"缺少列: {col}"
        for col in ("hysteresis_delta", "min_dwell_seconds", "breaker_open_seconds"):
            assert db._sqlite_column_exists("pool_auto_policies", col), f"缺少列: {col}"
        row = db.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' "
            "AND name='idx_messages_session_message_id'"
        ).fetchone()
        assert row is not None
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

    def test_sessions_have_user_id(self, tmp_path: Path) -> None:
        """当前形态 sessions 表含 user_id 列。"""
        db_path = str(tmp_path / "test.db")
        db = Database(db_path)
        assert db._sqlite_column_exists("sessions", "user_id")
        db.close()

    def test_approvals_have_session_id(self, tmp_path: Path) -> None:
        """当前形态 approvals 表含 session_id 列。"""
        db_path = str(tmp_path / "test.db")
        db = Database(db_path)
        assert db._sqlite_column_exists("approvals", "session_id")
        db.close()

    def test_model_profiles_have_thinking_columns(self, tmp_path: Path) -> None:
        """当前形态 model_profiles 含 thinking / 自定义列。"""
        db_path = str(tmp_path / "test.db")
        db = Database(db_path)
        for col in ("thinking_mode", "model_family", "custom_extra_body", "custom_extra_headers"):
            assert db._sqlite_column_exists("model_profiles", col), f"缺少列: {col}"
        db.close()


# ── SQLite 迁移定义守护 ──────────────────────────────────────────


class TestSqliteMigrations:
    """squash 后的 SQLite 迁移定义约束。"""

    def test_latest_version_consistent(self) -> None:
        """_LATEST_VERSION 应等于 dict 的最大 key。"""
        assert _LATEST_VERSION == max(_SQLITE_MIGRATIONS.keys())

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


class TestLegacySchemaStamp:
    """旧梯子 MAX(schema_version)=25 必须 stamp 成 v1，否则未来 v2 会 skip。"""

    def _fake_legacy_versions(self, db_path: str, max_version: int = 25) -> None:
        conn = sqlite3.connect(db_path)
        conn.execute("DELETE FROM schema_version")
        for version in range(1, max_version + 1):
            conn.execute(
                "INSERT INTO schema_version (version, applied_at) VALUES (?, 'now')",
                (version,),
            )
        conn.commit()
        conn.close()

    def test_stamp_v25_to_v1_preserves_data(self, tmp_path: Path) -> None:
        db_path = str(tmp_path / "test.db")
        db = Database(db_path)
        db.conn.execute(
            "INSERT INTO sessions (id, title, created_at, updated_at, message_count) "
            "VALUES (?, ?, ?, ?, ?)",
            ("s1", "hello", "t", "t", 0),
        )
        db.conn.commit()
        db.close()

        self._fake_legacy_versions(db_path, 25)

        db2 = Database(db_path)
        assert db2._current_version() == _LATEST_VERSION
        versions = [
            r["version"]
            for r in db2.conn.execute(
                "SELECT version FROM schema_version ORDER BY version"
            ).fetchall()
        ]
        assert versions == list(range(1, _LATEST_VERSION + 1))
        assert 25 not in versions
        title = db2.conn.execute(
            "SELECT title FROM sessions WHERE id = ?", ("s1",)
        ).fetchone()["title"]
        assert title == "hello"
        assert db2._sqlite_column_exists("sessions", "workspace_path")
        db2.close()

    def test_stamped_legacy_db_can_apply_future_v2(self, tmp_path: Path) -> None:
        db_path = str(tmp_path / "test.db")
        db = Database(db_path)
        db.close()
        self._fake_legacy_versions(db_path, 25)

        future_version = _LATEST_VERSION + 1
        extended = dict(_SQLITE_MIGRATIONS)
        extended[future_version] = [
            "ALTER TABLE sessions ADD COLUMN stamp_col TEXT DEFAULT ''",
        ]
        with patch("excelmanus.database._SQLITE_MIGRATIONS", extended), \
             patch("excelmanus.database._LATEST_VERSION", future_version):
            db2 = Database(db_path)
            assert db2._current_version() == future_version
            assert db2._sqlite_column_exists("sessions", "stamp_col")
            versions = [
                r["version"]
                for r in db2.conn.execute(
                    "SELECT version FROM schema_version ORDER BY version"
                ).fetchall()
            ]
            assert 25 not in versions
            assert 1 in versions
            assert future_version in versions
            db2.close()

    def test_incomplete_legacy_db_refuses_stamp(self, tmp_path: Path) -> None:
        db_path = str(tmp_path / "broken.db")
        conn = sqlite3.connect(db_path)
        conn.execute(
            "CREATE TABLE schema_version ("
            "  version INTEGER PRIMARY KEY, applied_at TEXT)"
        )
        conn.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, status TEXT)")
        conn.execute("INSERT INTO schema_version (version) VALUES (25)")
        conn.commit()
        conn.close()

        with pytest.raises(RuntimeError, match="无法 stamp"):
            Database(db_path)


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
            "CREATE INDEX IF NOT EXISTS idx_sessions_updated_at "
            "ON sessions(updated_at)",
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
