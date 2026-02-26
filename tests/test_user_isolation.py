"""多用户隔离集成测试：验证跨用户数据不可见性。"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest


# ── UserContext 测试 ─────────────────────────────────────


class TestUserContext:
    def test_create_makes_per_user_dir(self, tmp_path: Path) -> None:
        from excelmanus.user_context import UserContext

        ctx = UserContext.create(
            "user-aaa",
            global_workspace_root=str(tmp_path),
        )
        assert ctx.user_id == "user-aaa"
        assert ctx.workspace_root.exists()
        assert "users/user-aaa" in str(ctx.workspace_root)
        assert not ctx.is_anonymous

    def test_anonymous_context(self, tmp_path: Path) -> None:
        from excelmanus.user_context import UserContext

        ctx = UserContext.anonymous(str(tmp_path))
        assert ctx.is_anonymous
        assert ctx.db_user_id is None
        assert ctx.role == "admin"

    def test_frozen(self, tmp_path: Path) -> None:
        from excelmanus.user_context import UserContext

        ctx = UserContext.create("u1", global_workspace_root=str(tmp_path))
        with pytest.raises(AttributeError):
            ctx.user_id = "u2"  # type: ignore[misc]


# ── UserConfigStore 隔离测试 ─────────────────────────────


class TestUserConfigStoreIsolation:
    """验证 UserConfigStore 按用户隔离 active_model。"""

    def _make_db(self, db_path: str) -> "ConnectionAdapter":
        from excelmanus.db_adapter import create_sqlite_adapter
        conn = create_sqlite_adapter(db_path)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS config_kv ("
            "  key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL"
            ")"
        )
        conn.commit()
        return conn

    def test_active_model_isolated(self, tmp_path: Path) -> None:
        from excelmanus.stores.config_store import UserConfigStore

        db_path = str(tmp_path / "test.db")
        conn = self._make_db(db_path)

        store_a = UserConfigStore(conn, user_id="user-a")
        store_b = UserConfigStore(conn, user_id="user-b")

        store_a.set_active_model("gpt4")
        store_b.set_active_model("claude")

        assert store_a.get_active_model() == "gpt4"
        assert store_b.get_active_model() == "claude"

        store_a.set_active_model(None)
        assert store_a.get_active_model() is None
        assert store_b.get_active_model() == "claude"

    def test_anonymous_uses_global_kv(self, tmp_path: Path) -> None:
        from excelmanus.stores.config_store import UserConfigStore

        db_path = str(tmp_path / "test.db")
        conn = self._make_db(db_path)

        store_anon = UserConfigStore(conn, user_id=None)
        store_anon.set_active_model("gpt4")
        assert store_anon.get_active_model() == "gpt4"


# ── 各 Store 按 user_id 过滤测试 ──────────────────────────


class TestApprovalStoreIsolation:
    """验证 ApprovalStore 按 user_id 过滤。"""

    def test_approvals_filtered_by_user(self, tmp_path: Path) -> None:
        from excelmanus.database import Database
        from excelmanus.stores.approval_store import ApprovalStore

        db = Database(str(tmp_path / "test.db"))

        store_a = ApprovalStore(db, user_id="user-a")
        store_b = ApprovalStore(db, user_id="user-b")

        store_a.save({
            "id": "approval-1",
            "tool_name": "tool_x",
            "execution_status": "success",
        })
        store_b.save({
            "id": "approval-2",
            "tool_name": "tool_y",
            "execution_status": "success",
        })

        a_result = store_a.get("approval-1")
        assert a_result is not None

        b_result = store_b.get("approval-1")
        assert b_result is None

        a_list = store_a.list_approvals()
        b_list = store_b.list_approvals()
        a_ids = {r["id"] for r in a_list}
        b_ids = {r["id"] for r in b_list}
        assert "approval-1" in a_ids
        assert "approval-2" not in a_ids
        assert "approval-2" in b_ids
        assert "approval-1" not in b_ids

        db.close()


class TestToolCallStoreIsolation:
    """验证 ToolCallStore 记录日志时包含 user_id。"""

    def test_log_includes_user_id(self, tmp_path: Path) -> None:
        from excelmanus.database import Database
        from excelmanus.stores.tool_call_store import ToolCallStore

        db = Database(str(tmp_path / "test.db"))
        store = ToolCallStore(db, user_id="user-a")

        store.log(
            session_id="sess1",
            tool_name="run_code",
            success=True,
            duration_ms=10,
        )

        row = db.conn.execute(
            "SELECT user_id FROM tool_call_log WHERE session_id = ?", ("sess1",)
        ).fetchone()
        assert row is not None
        assert row["user_id"] == "user-a"

        db.close()


class TestLLMCallStoreIsolation:
    """验证 LLMCallStore 记录日志时包含 user_id。"""

    def test_log_includes_user_id(self, tmp_path: Path) -> None:
        from excelmanus.database import Database
        from excelmanus.stores.llm_call_store import LLMCallStore

        db = Database(str(tmp_path / "test.db"))
        store = LLMCallStore(db, user_id="user-b")

        store.log(
            session_id="sess2",
            model="gpt-4",
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
            latency_ms=500,
        )

        row = db.conn.execute(
            "SELECT user_id FROM llm_call_log WHERE session_id = ?", ("sess2",)
        ).fetchone()
        assert row is not None
        assert row["user_id"] == "user-b"

        db.close()


# ── ScopedDatabase 测试 ───────────────────────────────────


class TestScopedDatabase:
    """验证 ScopedDatabase 为 SQLite 创建按用户隔离的数据库文件。"""

    def test_sqlite_creates_per_user_db(self, tmp_path: Path) -> None:
        from excelmanus.database import Database
        from excelmanus.scoped_database import ScopedDatabase
        from excelmanus.user_context import UserContext

        shared_db = Database(str(tmp_path / "shared.db"))
        ctx = UserContext.create("user-x", global_workspace_root=str(tmp_path))

        scoped = ScopedDatabase(ctx, shared_db)
        assert scoped.db_user_id == "user-x"

        user_db_path = ctx.workspace_root / "data.db"
        assert user_db_path.exists()

        scoped.close()
        shared_db.close()

    def test_anonymous_uses_shared_db(self, tmp_path: Path) -> None:
        from excelmanus.database import Database
        from excelmanus.scoped_database import ScopedDatabase
        from excelmanus.user_context import UserContext

        shared_db = Database(str(tmp_path / "shared.db"))
        ctx = UserContext.anonymous(str(tmp_path))

        scoped = ScopedDatabase(ctx, shared_db)
        assert scoped.db_user_id is None
        assert scoped.conn is shared_db.conn

        scoped.close()
        shared_db.close()


# ── Guard contextvar 隔离测试 ─────────────────────────────


class TestGuardContextVar:
    """验证 FileAccessGuard 的 contextvar 隔离。"""

    def test_contextvar_takes_priority(self, tmp_path: Path) -> None:
        from excelmanus.security import FileAccessGuard
        from excelmanus.tools._guard_ctx import set_guard, get_guard, reset_guard

        global_guard = FileAccessGuard(str(tmp_path / "global"))
        session_guard = FileAccessGuard(str(tmp_path / "user1"))

        assert get_guard() is None

        token = set_guard(session_guard)
        assert get_guard() is session_guard

        reset_guard(token)
        assert get_guard() is None


# ── 数据库迁移 v10 测试 ───────────────────────────────────


class TestMigrationV10:
    """验证迁移 v10 为所有必需表添加 user_id 列。"""

    def test_migration_adds_user_id_columns(self, tmp_path: Path) -> None:
        from excelmanus.database import Database

        db = Database(str(tmp_path / "test.db"))
        conn = db.conn

        tables_with_user_id = ["sessions", "memory_entries", "approvals", "tool_call_log", "llm_call_log", "workspace_files"]
        for table in tables_with_user_id:
            cols_cursor = conn.execute(f"PRAGMA table_info({table})")
            col_names = [r["name"] for r in cols_cursor.fetchall()]
            assert "user_id" in col_names, f"Table {table} missing user_id column"

        assert conn.table_exists("user_config_kv"), "user_config_kv table not created"

        db.close()


# ── 系统模式回退缓存隔离测试 ──────────────────────────────


class TestSystemModeFallbackCache:
    """验证类级别缓存以 (model, base_url) 为键。"""

    def test_cache_keyed_by_model(self) -> None:
        from excelmanus.engine import AgentEngine

        AgentEngine._system_mode_fallback_cache.clear()
        AgentEngine._system_mode_fallback_cache[("gpt-4", "https://a.com")] = "merge"

        assert AgentEngine._system_mode_fallback_cache.get(("gpt-4", "https://a.com")) == "merge"
        assert AgentEngine._system_mode_fallback_cache.get(("claude", "https://b.com")) is None

        AgentEngine._system_mode_fallback_cache.clear()
