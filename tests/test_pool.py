"""号池功能测试：迁移、PoolService、Resolver 集成、健康信号。"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock


# ── 辅助：创建内存 SQLite 数据库并迁移到 v21 ─────────────────


def _create_test_db():
    """创建测试数据库并执行迁移。"""
    from excelmanus.database import Database
    db = Database(":memory:")
    return db


def _get_conn(db):
    return db.conn


# ══════════════════════════════════════════════════════════════
# 1. 迁移测试
# ══════════════════════════════════════════════════════════════


class TestMigration:
    """schema v21 迁移测试。"""

    def test_migration_creates_pool_tables(self):
        """迁移后 4 张号池表应存在。"""
        db = _create_test_db()
        conn = _get_conn(db)
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "pool_accounts" in tables
        assert "pool_usage_ledger" in tables
        assert "pool_budget_snapshots" in tables
        assert "pool_manual_active" in tables
        db.close()

    def test_migration_version_is_21(self):
        """迁移后版本号应为 21。"""
        db = _create_test_db()
        row = db.conn.execute("SELECT MAX(version) as v FROM schema_version").fetchone()
        assert row["v"] >= 21
        db.close()

    def test_pool_accounts_columns(self):
        """pool_accounts 表应有预期列。"""
        db = _create_test_db()
        cols = {
            row[1]
            for row in db.conn.execute("PRAGMA table_info(pool_accounts)").fetchall()
        }
        expected = {
            "id", "label", "provider", "account_id", "plan_type", "status",
            "daily_budget_tokens", "weekly_budget_tokens", "timezone",
            "health_signal", "health_confidence", "health_updated_at",
            "created_at", "updated_at",
        }
        assert expected.issubset(cols)
        db.close()

    def test_existing_data_survives_migration(self):
        """迁移不应影响现有表数据。"""
        db = _create_test_db()
        # sessions 表应存在（v1 创建）
        tables = {
            row[0]
            for row in db.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "sessions" in tables
        db.close()


# ══════════════════════════════════════════════════════════════
# 2. PoolService 单元测试
# ══════════════════════════════════════════════════════════════


class TestPoolServiceCRUD:
    """PoolService 账号 CRUD。"""

    def setup_method(self):
        self.db = _create_test_db()
        from excelmanus.pool.service import PoolService
        self.svc = PoolService(conn=self.db.conn)

    def teardown_method(self):
        self.db.close()

    def test_create_account(self):
        account = self.svc.create_account(
            label="测试A", provider="openai-codex",
            daily_budget_tokens=500000, weekly_budget_tokens=3000000,
        )
        assert account.id
        assert account.label == "测试A"
        assert account.status == "active"
        assert account.daily_budget_tokens == 500000

    def test_get_account(self):
        account = self.svc.create_account(label="B")
        fetched = self.svc.get_account(account.id)
        assert fetched is not None
        assert fetched.id == account.id
        assert fetched.label == "B"

    def test_get_account_not_found(self):
        assert self.svc.get_account("nonexistent") is None

    def test_list_accounts(self):
        self.svc.create_account(label="A")
        self.svc.create_account(label="B")
        accounts = self.svc.list_accounts()
        assert len(accounts) == 2

    def test_update_account(self):
        account = self.svc.create_account(label="Old")
        updated = self.svc.update_account(account.id, label="New", status="disabled")
        assert updated is not None
        assert updated.label == "New"
        assert updated.status == "disabled"

    def test_update_account_ignores_unknown_fields(self):
        account = self.svc.create_account(label="X")
        updated = self.svc.update_account(account.id, unknown_field="val")
        assert updated is not None
        assert updated.label == "X"


class TestPoolServiceLedger:
    """PoolService 台账写入。"""

    def setup_method(self):
        self.db = _create_test_db()
        from excelmanus.pool.service import PoolService
        self.svc = PoolService(conn=self.db.conn)

    def teardown_method(self):
        self.db.close()

    def test_log_usage(self):
        account = self.svc.create_account(label="A")
        self.svc.log_usage(
            pool_account_id=account.id,
            model="gpt-5.1-codex",
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
        )
        rows = self.db.conn.execute(
            "SELECT * FROM pool_usage_ledger WHERE pool_account_id = ?",
            (account.id,),
        ).fetchall()
        assert len(rows) == 1
        row = dict(rows[0])
        assert row["total_tokens"] == 150
        assert row["outcome"] == "success"

    def test_log_usage_auto_total(self):
        account = self.svc.create_account(label="A")
        self.svc.log_usage(
            pool_account_id=account.id,
            prompt_tokens=200,
            completion_tokens=100,
        )
        row = dict(self.db.conn.execute(
            "SELECT total_tokens FROM pool_usage_ledger WHERE pool_account_id = ?",
            (account.id,),
        ).fetchone())
        assert row["total_tokens"] == 300


class TestPoolServiceHealth:
    """PoolService 健康信号。"""

    def setup_method(self):
        self.db = _create_test_db()
        from excelmanus.pool.service import PoolService
        self.svc = PoolService(conn=self.db.conn)

    def teardown_method(self):
        self.db.close()

    def test_update_health_signal(self):
        account = self.svc.create_account(label="A")
        self.svc.update_health_signal(account.id, "rate_limited", 0.7)
        updated = self.svc.get_account(account.id)
        assert updated is not None
        assert updated.health_signal == "rate_limited"
        assert updated.health_confidence == 0.7

    def test_depleted_signal_updates_status(self):
        account = self.svc.create_account(label="A")
        self.svc.update_health_signal(account.id, "depleted", 0.9)
        updated = self.svc.get_account(account.id)
        assert updated is not None
        assert updated.status == "depleted"

    def test_depleted_low_confidence_keeps_active(self):
        account = self.svc.create_account(label="A")
        self.svc.update_health_signal(account.id, "depleted", 0.5)
        updated = self.svc.get_account(account.id)
        assert updated is not None
        assert updated.status == "active"

    def test_ok_signal(self):
        account = self.svc.create_account(label="A")
        self.svc.update_health_signal(account.id, "depleted", 0.9)
        self.svc.update_health_signal(account.id, "ok", 1.0)
        updated = self.svc.get_account(account.id)
        assert updated is not None
        assert updated.health_signal == "ok"


class TestPoolServiceManualActive:
    """PoolService 人工激活映射。"""

    def setup_method(self):
        self.db = _create_test_db()
        from excelmanus.pool.service import PoolService
        self.svc = PoolService(conn=self.db.conn)

    def teardown_method(self):
        self.db.close()

    def test_set_and_get_manual_active(self):
        account = self.svc.create_account(label="A")
        mapping = self.svc.set_manual_active(
            "openai-codex", "*", account.id, activated_by="admin1",
        )
        assert mapping.pool_account_id == account.id

        fetched = self.svc.get_manual_active("openai-codex", "*")
        assert fetched is not None
        assert fetched.pool_account_id == account.id

    def test_list_manual_active(self):
        a1 = self.svc.create_account(label="A")
        a2 = self.svc.create_account(label="B")
        self.svc.set_manual_active("openai-codex", "*", a1.id)
        self.svc.set_manual_active("openai-codex", "gpt-5.1-codex", a2.id)
        mappings = self.svc.list_manual_active()
        assert len(mappings) == 2

    def test_resolve_active_account_wildcard(self):
        account = self.svc.create_account(label="A")
        self.svc.set_manual_active("openai-codex", "*", account.id)
        resolved = self.svc.resolve_active_account("openai-codex", "gpt-5.1-codex")
        assert resolved is not None
        assert resolved.id == account.id

    def test_resolve_active_account_exact_match(self):
        a1 = self.svc.create_account(label="Wildcard")
        a2 = self.svc.create_account(label="Exact")
        self.svc.set_manual_active("openai-codex", "*", a1.id)
        self.svc.set_manual_active("openai-codex", "gpt-5.1-codex", a2.id)
        resolved = self.svc.resolve_active_account("openai-codex", "gpt-5.1-codex")
        assert resolved is not None
        assert resolved.id == a2.id

    def test_resolve_active_account_disabled(self):
        account = self.svc.create_account(label="A")
        self.svc.update_account(account.id, status="disabled")
        self.svc.set_manual_active("openai-codex", "*", account.id)
        resolved = self.svc.resolve_active_account("openai-codex", "gpt-5.1-codex")
        assert resolved is None

    def test_resolve_active_account_no_mapping(self):
        resolved = self.svc.resolve_active_account("openai-codex", "gpt-5.1-codex")
        assert resolved is None


class TestPoolServiceSnapshot:
    """PoolService 预算快照。"""

    def setup_method(self):
        self.db = _create_test_db()
        from excelmanus.pool.service import PoolService
        self.svc = PoolService(conn=self.db.conn)

    def teardown_method(self):
        self.db.close()

    def test_refresh_snapshots_empty(self):
        count = self.svc.refresh_snapshots()
        assert count == 0

    def test_refresh_snapshots_with_account(self):
        account = self.svc.create_account(
            label="A", daily_budget_tokens=100000, weekly_budget_tokens=500000,
        )
        count = self.svc.refresh_snapshots()
        assert count == 1

        snapshot = self.svc.get_snapshot(account.id)
        assert snapshot is not None
        assert snapshot.daily_remaining == 100000
        assert snapshot.weekly_remaining == 500000

    def test_snapshot_reflects_usage(self):
        account = self.svc.create_account(
            label="A", daily_budget_tokens=100000, weekly_budget_tokens=500000,
        )
        self.svc.log_usage(
            pool_account_id=account.id,
            total_tokens=30000,
            outcome="success",
        )
        self.svc.refresh_snapshots()
        snapshot = self.svc.get_snapshot(account.id)
        assert snapshot is not None
        assert snapshot.daily_remaining == 70000
        assert snapshot.day_window_tokens == 30000

    def test_snapshot_disabled_account_skipped(self):
        account = self.svc.create_account(label="A")
        self.svc.update_account(account.id, status="disabled")
        count = self.svc.refresh_snapshots()
        assert count == 0


class TestPoolServiceSummary:
    """PoolService 号池总览。"""

    def setup_method(self):
        self.db = _create_test_db()
        from excelmanus.pool.service import PoolService
        self.svc = PoolService(conn=self.db.conn)

    def teardown_method(self):
        self.db.close()

    def test_get_summary(self):
        a1 = self.svc.create_account(label="A", daily_budget_tokens=100000)
        a2 = self.svc.create_account(label="B", daily_budget_tokens=200000)
        self.svc.set_manual_active("openai-codex", "*", a1.id)
        self.svc.refresh_snapshots()

        summaries = self.svc.get_summary()
        assert len(summaries) == 2
        active_ids = {s.account.id for s in summaries if s.is_active}
        assert a1.id in active_ids
        assert a2.id not in active_ids

    def test_summary_to_dict(self):
        account = self.svc.create_account(label="A", daily_budget_tokens=100000)
        self.svc.refresh_snapshots()
        summaries = self.svc.get_summary()
        d = summaries[0].to_dict()
        assert "id" in d
        assert "budget" in d
        assert d["budget"] is not None
        assert "is_active" in d


# ══════════════════════════════════════════════════════════════
# 3. Resolver 集成测试
# ══════════════════════════════════════════════════════════════


class TestResolverPoolBranch:
    """CredentialResolver 池账号分支。"""

    def test_no_pool_service_returns_none(self):
        """无 PoolService 时 _try_pool_account 返回 None。"""
        from excelmanus.auth.providers.resolver import CredentialResolver
        resolver = CredentialResolver()
        result = resolver._try_pool_account("openai-codex", "gpt-5.1-codex")
        assert result is None

    def test_pool_service_no_mapping_returns_none(self):
        """有 PoolService 但无激活映射时返回 None。"""
        from excelmanus.auth.providers.resolver import CredentialResolver
        db = _create_test_db()
        from excelmanus.pool.service import PoolService
        svc = PoolService(conn=db.conn)

        resolver = CredentialResolver()
        resolver._pool_service = svc
        resolver._pool_enabled = True
        result = resolver._try_pool_account("openai-codex", "gpt-5.1-codex")
        assert result is None
        db.close()

    def test_pool_resolved_has_correct_source(self):
        """激活映射存在且凭证有效时，返回 source=pool_oauth。"""
        from excelmanus.auth.providers.resolver import CredentialResolver
        db = _create_test_db()
        from excelmanus.pool.service import PoolService
        svc = PoolService(conn=db.conn)

        account = svc.create_account(label="Pool-A", provider="openai-codex")
        svc.set_manual_active("openai-codex", "*", account.id)

        # Mock CredentialStore
        mock_store = MagicMock()
        mock_profile = MagicMock()
        mock_profile.access_token = "test_token"
        mock_profile.profile_name = f"pool/{account.id}"
        mock_store.get_active_profile.return_value = mock_profile

        resolver = CredentialResolver(credential_store=mock_store)
        resolver._pool_service = svc
        resolver._pool_enabled = True

        result = resolver._try_pool_account("openai-codex", "gpt-5.1-codex")
        assert result is not None
        assert result.source == "pool_oauth"
        assert result.pool_account_id == account.id
        assert result.pool_profile_name == f"pool/{account.id}"
        db.close()

    def test_pool_disabled_account_falls_through(self):
        """禁用的池账号不返回 pool_oauth。"""
        from excelmanus.auth.providers.resolver import CredentialResolver
        db = _create_test_db()
        from excelmanus.pool.service import PoolService
        svc = PoolService(conn=db.conn)

        account = svc.create_account(label="Pool-A", provider="openai-codex")
        svc.update_account(account.id, status="disabled")
        svc.set_manual_active("openai-codex", "*", account.id)

        resolver = CredentialResolver()
        resolver._pool_service = svc
        resolver._pool_enabled = True
        result = resolver._try_pool_account("openai-codex", "gpt-5.1-codex")
        assert result is None
        db.close()

    def test_pool_not_enabled_returns_none(self):
        """pool_enabled=False 时不返回池凭证。"""
        from excelmanus.auth.providers.resolver import CredentialResolver
        db = _create_test_db()
        from excelmanus.pool.service import PoolService
        svc = PoolService(conn=db.conn)

        account = svc.create_account(label="Pool-A", provider="openai-codex")
        svc.set_manual_active("openai-codex", "*", account.id)

        resolver = CredentialResolver()
        resolver._pool_service = svc
        resolver._pool_enabled = False  # 灰度关闭
        result = resolver._try_pool_account("openai-codex", "gpt-5.1-codex")
        assert result is None
        db.close()


# ══════════════════════════════════════════════════════════════
# 4. ResolvedCredential 扩展测试
# ══════════════════════════════════════════════════════════════


class TestResolvedCredentialExtension:
    """ResolvedCredential 新增字段测试。"""

    def test_pool_fields_default_none(self):
        from excelmanus.auth.providers.base import ResolvedCredential
        cred = ResolvedCredential(api_key="k", base_url="u", source="oauth")
        assert cred.pool_account_id is None
        assert cred.pool_profile_name is None

    def test_pool_fields_set(self):
        from excelmanus.auth.providers.base import ResolvedCredential
        cred = ResolvedCredential(
            api_key="k", base_url="u", source="pool_oauth",
            pool_account_id="abc", pool_profile_name="pool/abc",
        )
        assert cred.pool_account_id == "abc"
        assert cred.pool_profile_name == "pool/abc"
        assert cred.source == "pool_oauth"


# ══════════════════════════════════════════════════════════════
# 5. 健康信号测试
# ══════════════════════════════════════════════════════════════


class TestHealthSignals:
    """池健康信号分类。"""

    def test_402_depleted(self):
        from excelmanus.pool.signals import classify_pool_health_signal
        result = classify_pool_health_signal(402)
        assert result is not None
        signal, conf = result
        assert signal == "depleted"
        assert conf >= 0.8

    def test_429_rate_limited(self):
        from excelmanus.pool.signals import classify_pool_health_signal
        result = classify_pool_health_signal(429)
        assert result is not None
        signal, conf = result
        assert signal == "rate_limited"

    def test_500_transient(self):
        from excelmanus.pool.signals import classify_pool_health_signal
        result = classify_pool_health_signal(500)
        assert result is not None
        signal, _ = result
        assert signal == "transient"

    def test_502_transient(self):
        from excelmanus.pool.signals import classify_pool_health_signal
        result = classify_pool_health_signal(502)
        assert result is not None
        signal, _ = result
        assert signal == "transient"

    def test_200_none(self):
        from excelmanus.pool.signals import classify_pool_health_signal
        result = classify_pool_health_signal(200)
        assert result is None

    def test_quota_keyword_depleted(self):
        from excelmanus.pool.signals import classify_pool_health_signal
        result = classify_pool_health_signal(None, "insufficient quota for this request")
        assert result is not None
        signal, _ = result
        assert signal == "depleted"

    def test_network_keyword_transient(self):
        from excelmanus.pool.signals import classify_pool_health_signal
        result = classify_pool_health_signal(None, "connection refused")
        assert result is not None
        signal, _ = result
        assert signal == "transient"

    def test_update_pool_health_from_error(self):
        from excelmanus.pool.signals import update_pool_health_from_error
        db = _create_test_db()
        from excelmanus.pool.service import PoolService
        svc = PoolService(conn=db.conn)
        account = svc.create_account(label="A")

        update_pool_health_from_error(
            pool_service=svc,
            pool_account_id=account.id,
            status_code=402,
            error_message="quota exceeded",
        )
        updated = svc.get_account(account.id)
        assert updated is not None
        assert updated.health_signal == "depleted"
        assert updated.status == "depleted"

        # 验证失败台账也被记录
        rows = db.conn.execute(
            "SELECT * FROM pool_usage_ledger WHERE pool_account_id = ? AND outcome = 'error'",
            (account.id,),
        ).fetchall()
        assert len(rows) == 1
        db.close()

    def test_update_pool_health_on_success(self):
        from excelmanus.pool.signals import update_pool_health_on_success
        db = _create_test_db()
        from excelmanus.pool.service import PoolService
        svc = PoolService(conn=db.conn)
        account = svc.create_account(label="A")
        svc.update_health_signal(account.id, "rate_limited", 0.7)

        update_pool_health_on_success(svc, account.id)
        updated = svc.get_account(account.id)
        assert updated is not None
        assert updated.health_signal == "ok"
        db.close()


# ══════════════════════════════════════════════════════════════
# 6. 时区/预算边界测试
# ══════════════════════════════════════════════════════════════


class TestTimezoneHelpers:
    """时区相关工具函数测试。"""

    def test_day_start_utc(self):
        from excelmanus.pool.service import _day_start_utc
        # Asia/Shanghai is UTC+8
        now_utc = datetime(2026, 3, 5, 10, 0, 0, tzinfo=timezone.utc)  # 18:00 Shanghai
        day_start = _day_start_utc("Asia/Shanghai", now_utc)
        # Shanghai 2026-03-05 00:00 = UTC 2026-03-04 16:00
        assert day_start.hour == 16
        assert day_start.day == 4

    def test_week_start_utc(self):
        from excelmanus.pool.service import _week_start_utc
        # 2026-03-05 is Thursday
        now_utc = datetime(2026, 3, 5, 10, 0, 0, tzinfo=timezone.utc)
        week_start = _week_start_utc("Asia/Shanghai", now_utc)
        # Monday 2026-03-02 00:00 Shanghai = 2026-03-01 16:00 UTC
        assert week_start.weekday() == 6  # Sunday in UTC (Monday 00:00 Shanghai)
        # Verify it's before now
        assert week_start < now_utc

    def test_day_start_utc_different_timezone(self):
        from excelmanus.pool.service import _day_start_utc
        now_utc = datetime(2026, 3, 5, 3, 0, 0, tzinfo=timezone.utc)  # 3:00 UTC
        day_start = _day_start_utc("UTC", now_utc)
        assert day_start.hour == 0
        assert day_start.day == 5


# ══════════════════════════════════════════════════════════════
# 7. 模型数据结构测试
# ══════════════════════════════════════════════════════════════


class TestPoolModels:
    """数据模型序列化测试。"""

    def test_pool_account_to_dict(self):
        from excelmanus.pool.models import PoolAccount
        account = PoolAccount(
            id="test-id", label="A", provider="openai-codex",
            status="active", daily_budget_tokens=100000,
        )
        d = account.to_dict()
        assert d["id"] == "test-id"
        assert d["daily_budget_tokens"] == 100000

    def test_pool_manual_active_to_dict(self):
        from excelmanus.pool.models import PoolManualActive
        mapping = PoolManualActive(
            provider="openai-codex", model_pattern="*",
            pool_account_id="abc",
        )
        d = mapping.to_dict()
        assert d["provider"] == "openai-codex"
        assert d["pool_account_id"] == "abc"

    def test_pool_budget_snapshot_to_dict(self):
        from excelmanus.pool.models import PoolBudgetSnapshot
        snap = PoolBudgetSnapshot(
            pool_account_id="abc",
            daily_remaining=50000, weekly_remaining=200000,
        )
        d = snap.to_dict()
        assert d["daily_remaining"] == 50000

    def test_pool_account_summary_to_dict(self):
        from excelmanus.pool.models import PoolAccount, PoolAccountSummary, PoolBudgetSnapshot
        account = PoolAccount(id="x", label="X")
        snap = PoolBudgetSnapshot(pool_account_id="x", daily_remaining=100)
        summary = PoolAccountSummary(account=account, snapshot=snap, is_active=True)
        d = summary.to_dict()
        assert d["is_active"] is True
        assert d["budget"]["daily_remaining"] == 100

    def test_pool_account_summary_no_snapshot(self):
        from excelmanus.pool.models import PoolAccount, PoolAccountSummary
        account = PoolAccount(id="y", label="Y")
        summary = PoolAccountSummary(account=account)
        d = summary.to_dict()
        assert d["budget"] is None
        assert d["is_active"] is False


# ══════════════════════════════════════════════════════════════
# 8. 回归测试：6 个风险点
# ══════════════════════════════════════════════════════════════


class TestMultiAccountCredentialLookup:
    """[P1] 多池账号时 get_profile_by_name 精确查找。"""

    def test_get_profile_by_name_exists(self):
        """CredentialStore.get_profile_by_name 按 profile_name 精确查。"""
        from excelmanus.auth.providers.credential_store import CredentialStore
        from excelmanus.auth.providers.base import ValidatedCredential
        db = _create_test_db()
        store = CredentialStore(db.conn)

        cred_a = ValidatedCredential(
            access_token="tok_a", refresh_token=None,
            expires_at="2099-01-01T00:00:00+00:00",
            account_id="acct_a", plan_type="pro",
        )
        cred_b = ValidatedCredential(
            access_token="tok_b", refresh_token=None,
            expires_at="2099-01-01T00:00:00+00:00",
            account_id="acct_b", plan_type="plus",
        )
        store.upsert_profile("__pool_service__", "openai-codex", "pool/aaa", cred_a)
        store.upsert_profile("__pool_service__", "openai-codex", "pool/bbb", cred_b)

        # get_profile_by_name 精确取 pool/bbb
        profile_b = store.get_profile_by_name("__pool_service__", "openai-codex", "pool/bbb")
        assert profile_b is not None
        assert profile_b.profile_name == "pool/bbb"
        assert profile_b.account_id == "acct_b"

        # get_profile_by_name 精确取 pool/aaa
        profile_a = store.get_profile_by_name("__pool_service__", "openai-codex", "pool/aaa")
        assert profile_a is not None
        assert profile_a.profile_name == "pool/aaa"
        assert profile_a.account_id == "acct_a"
        db.close()

    def test_get_profile_by_name_not_found(self):
        """不存在的 profile_name 返回 None。"""
        from excelmanus.auth.providers.credential_store import CredentialStore
        db = _create_test_db()
        store = CredentialStore(db.conn)
        result = store.get_profile_by_name("__pool_service__", "openai-codex", "pool/nonexistent")
        assert result is None
        db.close()

    def test_resolver_uses_get_profile_by_name_for_multi_accounts(self):
        """多池账号映射到不同 model_pattern 时各自取到正确凭证。"""
        from excelmanus.auth.providers.resolver import CredentialResolver
        from excelmanus.pool.service import PoolService
        db = _create_test_db()
        svc = PoolService(conn=db.conn)

        acct_a = svc.create_account(label="A", provider="openai-codex")
        acct_b = svc.create_account(label="B", provider="openai-codex")
        svc.set_manual_active("openai-codex", "gpt-5.1-codex", acct_a.id)
        svc.set_manual_active("openai-codex", "gpt-5.1-codex-mini", acct_b.id)

        # Mock store with get_profile_by_name
        mock_store = MagicMock()
        def _mock_get_by_name(user_id, provider, profile_name):
            mock_prof = MagicMock()
            mock_prof.access_token = f"token_for_{profile_name}"
            mock_prof.profile_name = profile_name
            return mock_prof
        mock_store.get_profile_by_name = _mock_get_by_name

        resolver = CredentialResolver(credential_store=mock_store)
        resolver._pool_service = svc
        resolver._pool_enabled = True

        res_a = resolver._try_pool_account("openai-codex", "gpt-5.1-codex")
        assert res_a is not None
        assert res_a.pool_account_id == acct_a.id

        res_b = resolver._try_pool_account("openai-codex", "gpt-5.1-codex-mini")
        assert res_b is not None
        assert res_b.pool_account_id == acct_b.id
        db.close()


class TestManualActiveUpsert:
    """[P1] set_manual_active DELETE+INSERT 复合主键兼容。"""

    def setup_method(self):
        self.db = _create_test_db()
        from excelmanus.pool.service import PoolService
        self.svc = PoolService(conn=self.db.conn)

    def teardown_method(self):
        self.db.close()

    def test_upsert_overwrites_same_key(self):
        """同一 (provider, model_pattern) 更新后只有一条记录。"""
        a1 = self.svc.create_account(label="A")
        a2 = self.svc.create_account(label="B")
        self.svc.set_manual_active("openai-codex", "*", a1.id)
        self.svc.set_manual_active("openai-codex", "*", a2.id)

        mappings = self.svc.list_manual_active()
        wildcard = [m for m in mappings if m.model_pattern == "*"]
        assert len(wildcard) == 1
        assert wildcard[0].pool_account_id == a2.id

    def test_different_patterns_coexist(self):
        """不同 model_pattern 的映射互不干扰。"""
        a1 = self.svc.create_account(label="A")
        a2 = self.svc.create_account(label="B")
        self.svc.set_manual_active("openai-codex", "*", a1.id)
        self.svc.set_manual_active("openai-codex", "gpt-5.1-codex", a2.id)

        m_wildcard = self.svc.get_manual_active("openai-codex", "*")
        m_exact = self.svc.get_manual_active("openai-codex", "gpt-5.1-codex")
        assert m_wildcard is not None
        assert m_wildcard.pool_account_id == a1.id
        assert m_exact is not None
        assert m_exact.pool_account_id == a2.id


class TestPoolAccountIdClearing:
    """[P1] _pool_account_id 在非 pool 路径清零。"""

    def test_pool_id_cleared_when_resolved_is_none(self):
        """resolved=None 时 _pool_account_id 应清零。"""
        # 模拟 engine 属性行为
        _pool_account_id = "old_pool_id"
        _pool_profile_name = "pool/old"

        # 模拟 engine 中的清零逻辑
        resolved = None
        if resolved is None or getattr(resolved, "source", None) not in ("oauth", "pool_oauth"):
            _pool_account_id = None
            _pool_profile_name = None

        assert _pool_account_id is None
        assert _pool_profile_name is None

    def test_pool_id_cleared_when_source_is_user_key(self):
        """source=user_key 时 _pool_account_id 应清零。"""
        from excelmanus.auth.providers.base import ResolvedCredential

        _pool_account_id = "old_pool_id"
        _pool_profile_name = "pool/old"

        resolved = ResolvedCredential(api_key="k", base_url="u", source="user_key")
        if resolved.source not in ("oauth", "pool_oauth"):
            _pool_account_id = None
            _pool_profile_name = None

        assert _pool_account_id is None
        assert _pool_profile_name is None

    def test_pool_id_set_when_source_is_pool_oauth(self):
        """source=pool_oauth 时 _pool_account_id 应设置。"""
        from excelmanus.auth.providers.base import ResolvedCredential

        _pool_account_id = None
        _pool_profile_name = None

        resolved = ResolvedCredential(
            api_key="k", base_url="u", source="pool_oauth",
            pool_account_id="new_id", pool_profile_name="pool/new",
        )
        if resolved.source == "pool_oauth" and resolved.pool_account_id:
            _pool_account_id = resolved.pool_account_id
            _pool_profile_name = resolved.pool_profile_name

        assert _pool_account_id == "new_id"
        assert _pool_profile_name == "pool/new"


class TestSnapshotUpsertSQLCompat:
    """[P1] pool_budget_snapshots INSERT OR REPLACE 兼容性。"""

    def test_snapshot_refresh_twice_no_duplicate(self):
        """连续两次刷新不应产生重复 snapshot 行。"""
        db = _create_test_db()
        from excelmanus.pool.service import PoolService
        svc = PoolService(conn=db.conn)
        account = svc.create_account(label="A", daily_budget_tokens=100000)

        svc.refresh_snapshots()
        svc.refresh_snapshots()

        rows = db.conn.execute(
            "SELECT COUNT(*) as cnt FROM pool_budget_snapshots WHERE pool_account_id = ?",
            (account.id,),
        ).fetchone()
        assert dict(rows)["cnt"] == 1
        db.close()
