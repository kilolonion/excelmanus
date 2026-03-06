"""号池自动轮换测试：迁移、策略CRUD、触发决策、候选筛选、回退、审计事件、集成。"""

from __future__ import annotations

import asyncio


# ── 辅助：创建内存 SQLite 数据库并迁移到 v22 ─────────────────


def _create_test_db():
    from excelmanus.database import Database
    db = Database(":memory:")
    return db


def _create_svc(db):
    from excelmanus.pool.service import PoolService
    return PoolService(conn=db.conn)


def _create_auto_svc(db, pool_svc):
    from excelmanus.pool.auto_rotate import PoolAutoRotateService
    return PoolAutoRotateService(conn=db.conn, pool_service=pool_svc)


def _make_account(svc, label="A", daily=100000, weekly=500000):
    return svc.create_account(
        label=label, provider="openai-codex",
        daily_budget_tokens=daily, weekly_budget_tokens=weekly,
    )


# ══════════════════════════════════════════════════════════════
# 1. 迁移测试
# ══════════════════════════════════════════════════════════════


class TestMigrationV22:
    """schema v22 迁移测试。"""

    def test_migration_creates_auto_tables(self):
        db = _create_test_db()
        tables = {
            row[0]
            for row in db.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "pool_auto_policies" in tables
        assert "pool_rotation_events" in tables
        db.close()

    def test_migration_version_is_22(self):
        db = _create_test_db()
        row = db.conn.execute("SELECT MAX(version) as v FROM schema_version").fetchone()
        assert row["v"] >= 22
        db.close()

    def test_auto_policies_columns(self):
        db = _create_test_db()
        cols = {
            row[1]
            for row in db.conn.execute("PRAGMA table_info(pool_auto_policies)").fetchall()
        }
        expected = {
            "id", "provider", "model_pattern", "enabled", "low_watermark",
            "rate_limit_threshold", "transient_threshold", "error_window_minutes",
            "cooldown_seconds", "fallback_to_default", "created_at", "updated_at",
        }
        assert expected.issubset(cols)
        db.close()

    def test_existing_data_survives(self):
        """v22 迁移不影响 v21 号池表。"""
        db = _create_test_db()
        tables = {
            row[0]
            for row in db.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "pool_accounts" in tables
        assert "pool_manual_active" in tables
        db.close()


# ══════════════════════════════════════════════════════════════
# 2. 策略 CRUD
# ══════════════════════════════════════════════════════════════


class TestAutoPolicy:
    """PoolAutoRotateService 策略 CRUD。"""

    def setup_method(self):
        self.db = _create_test_db()
        self.svc = _create_svc(self.db)
        self.auto = _create_auto_svc(self.db, self.svc)

    def teardown_method(self):
        self.db.close()

    def test_upsert_creates_policy(self):
        policy = self.auto.upsert_policy(provider="openai-codex", model_pattern="*")
        assert policy.id
        assert policy.provider == "openai-codex"
        assert policy.enabled is True
        assert policy.low_watermark == 0.15

    def test_upsert_overwrites(self):
        p1 = self.auto.upsert_policy(low_watermark=0.1)
        p2 = self.auto.upsert_policy(low_watermark=0.2)
        assert p1.id == p2.id  # 同 scope 复用 ID
        assert p2.low_watermark == 0.2

    def test_get_policy(self):
        self.auto.upsert_policy(provider="openai-codex", model_pattern="*")
        fetched = self.auto.get_policy("openai-codex", "*")
        assert fetched is not None
        assert fetched.provider == "openai-codex"

    def test_get_policy_not_found(self):
        assert self.auto.get_policy("nonexistent") is None

    def test_list_policies(self):
        self.auto.upsert_policy(provider="openai-codex", model_pattern="*")
        self.auto.upsert_policy(provider="openai-codex", model_pattern="gpt-5.1-codex")
        policies = self.auto.list_policies()
        assert len(policies) == 2


# ══════════════════════════════════════════════════════════════
# 3. 硬触发测试
# ══════════════════════════════════════════════════════════════


class TestHardTrigger:
    """硬触发条件检测。"""

    def setup_method(self):
        self.db = _create_test_db()
        self.svc = _create_svc(self.db)
        self.auto = _create_auto_svc(self.db, self.svc)

    def teardown_method(self):
        self.db.close()

    def test_depleted_health(self):
        acct = _make_account(self.svc)
        self.svc.update_health_signal(acct.id, "depleted", 0.9)
        acct = self.svc.get_account(acct.id)
        reason = self.auto._check_hard_trigger(acct, None)
        assert "depleted" in reason

    def test_daily_budget_exhausted(self):
        from excelmanus.pool.models import PoolBudgetSnapshot
        acct = _make_account(self.svc, daily=100000)
        snapshot = PoolBudgetSnapshot(
            pool_account_id=acct.id, daily_remaining=0, weekly_remaining=50000,
        )
        reason = self.auto._check_hard_trigger(acct, snapshot)
        assert "daily_budget_exhausted" in reason

    def test_weekly_budget_exhausted(self):
        from excelmanus.pool.models import PoolBudgetSnapshot
        acct = _make_account(self.svc, weekly=500000)
        snapshot = PoolBudgetSnapshot(
            pool_account_id=acct.id, daily_remaining=50000, weekly_remaining=0,
        )
        reason = self.auto._check_hard_trigger(acct, snapshot)
        assert "weekly_budget_exhausted" in reason

    def test_no_trigger_healthy(self):
        from excelmanus.pool.models import PoolBudgetSnapshot
        acct = _make_account(self.svc)
        snapshot = PoolBudgetSnapshot(
            pool_account_id=acct.id, daily_remaining=50000, weekly_remaining=250000,
        )
        reason = self.auto._check_hard_trigger(acct, snapshot)
        assert reason == ""


# ══════════════════════════════════════════════════════════════
# 4. 软触发测试
# ══════════════════════════════════════════════════════════════


class TestSoftTrigger:
    """软触发条件检测。"""

    def setup_method(self):
        self.db = _create_test_db()
        self.svc = _create_svc(self.db)
        self.auto = _create_auto_svc(self.db, self.svc)

    def teardown_method(self):
        self.db.close()

    def test_low_budget_ratio(self):
        from excelmanus.pool.models import PoolAutoPolicy, PoolBudgetSnapshot
        acct = _make_account(self.svc, daily=100000, weekly=500000)
        snapshot = PoolBudgetSnapshot(
            pool_account_id=acct.id, daily_remaining=10000, weekly_remaining=50000,
        )
        policy = PoolAutoPolicy(id="p", low_watermark=0.15)
        reason = self.auto._check_soft_trigger(acct, snapshot, policy)
        assert "low_budget_ratio" in reason

    def test_rate_limit_errors(self):
        from excelmanus.pool.models import PoolAutoPolicy
        acct = _make_account(self.svc)
        # 制造 3 个 429 错误
        for _ in range(3):
            self.svc.log_usage(
                pool_account_id=acct.id, outcome="error", error_code="429",
            )
        policy = PoolAutoPolicy(
            id="p", rate_limit_threshold=3, error_window_minutes=5,
        )
        reason = self.auto._check_soft_trigger(acct, None, policy)
        assert "rate_limit_errors" in reason

    def test_transient_errors(self):
        from excelmanus.pool.models import PoolAutoPolicy
        acct = _make_account(self.svc)
        for _ in range(5):
            self.svc.log_usage(
                pool_account_id=acct.id, outcome="error", error_code="502",
            )
        policy = PoolAutoPolicy(
            id="p", transient_threshold=5, error_window_minutes=5,
        )
        reason = self.auto._check_soft_trigger(acct, None, policy)
        assert "transient_errors" in reason

    def test_no_trigger_below_threshold(self):
        from excelmanus.pool.models import PoolAutoPolicy, PoolBudgetSnapshot
        acct = _make_account(self.svc, daily=100000, weekly=500000)
        snapshot = PoolBudgetSnapshot(
            pool_account_id=acct.id, daily_remaining=80000, weekly_remaining=400000,
        )
        policy = PoolAutoPolicy(
            id="p", low_watermark=0.15, rate_limit_threshold=3, transient_threshold=5,
        )
        reason = self.auto._check_soft_trigger(acct, snapshot, policy)
        assert reason == ""


# ══════════════════════════════════════════════════════════════
# 5. 候选筛选测试
# ══════════════════════════════════════════════════════════════


class TestCandidateSelection:
    """候选筛选与评分。"""

    def setup_method(self):
        self.db = _create_test_db()
        self.svc = _create_svc(self.db)
        self.auto = _create_auto_svc(self.db, self.svc)

    def teardown_method(self):
        self.db.close()

    def test_disabled_account_excluded(self):
        acct = _make_account(self.svc)
        self.svc.update_account(acct.id, status="disabled")
        candidates = self.auto._list_candidates("openai-codex", exclude_id=None)
        assert len(candidates) == 0

    def test_depleted_health_excluded(self):
        acct = _make_account(self.svc)
        self.svc.update_health_signal(acct.id, "depleted", 0.9)
        candidates = self.auto._list_candidates("openai-codex", exclude_id=None)
        assert len(candidates) == 0

    def test_current_excluded(self):
        acct = _make_account(self.svc)
        candidates = self.auto._list_candidates("openai-codex", exclude_id=acct.id)
        assert len(candidates) == 0

    def test_scoring_prefers_higher_budget(self):
        a1 = _make_account(self.svc, label="Low", daily=100000, weekly=500000)
        a2 = _make_account(self.svc, label="High", daily=100000, weekly=500000)
        # A1 用了很多 → 余额低
        self.svc.log_usage(pool_account_id=a1.id, total_tokens=90000, outcome="success")
        # A2 几乎没用
        self.svc.log_usage(pool_account_id=a2.id, total_tokens=10000, outcome="success")
        self.svc.refresh_snapshots()

        candidates = self.auto._list_candidates("openai-codex", exclude_id=None)
        best = self.auto._select_best(candidates, "openai-codex", "*")
        assert best is not None
        assert best.id == a2.id  # A2 余额更多

    def test_tiebreak_prefers_less_activated(self):
        """并列评分时优先选历史激活次数少的。"""
        a1 = _make_account(self.svc, label="A1", daily=100000, weekly=500000)
        a2 = _make_account(self.svc, label="A2", daily=100000, weekly=500000)
        self.svc.refresh_snapshots()
        # 给 A1 制造激活历史
        self.auto.record_event(
            provider="openai-codex", model_pattern="*",
            to_account_id=a1.id, reason="test",
        )
        self.auto.record_event(
            provider="openai-codex", model_pattern="*",
            to_account_id=a1.id, reason="test",
        )
        candidates = self.auto._list_candidates("openai-codex", exclude_id=None)
        best = self.auto._select_best(candidates, "openai-codex", "*")
        # A2 激活历史为 0，A1 为 2，应优先 A2
        assert best is not None
        assert best.id == a2.id


# ══════════════════════════════════════════════════════════════
# 6. 回退测试
# ══════════════════════════════════════════════════════════════


class TestFallback:
    """无候选时回退原链路。"""

    def setup_method(self):
        self.db = _create_test_db()
        self.svc = _create_svc(self.db)
        self.auto = _create_auto_svc(self.db, self.svc)

    def teardown_method(self):
        self.db.close()

    def test_clear_rotation_removes_mapping(self):
        acct = _make_account(self.svc)
        self.svc.set_manual_active("openai-codex", "*", acct.id)
        self.auto.clear_rotation("openai-codex", "*", from_account_id=acct.id, reason="test")
        mapping = self.svc.get_manual_active("openai-codex", "*")
        assert mapping is None

    def test_fallback_event_recorded(self):
        acct = _make_account(self.svc)
        self.auto.clear_rotation("openai-codex", "*", from_account_id=acct.id, reason="no_candidates")
        events = self.auto.list_events()
        assert len(events) == 1
        assert events[0].fallback_used is True
        assert events[0].trigger == "fallback"
        assert events[0].to_account_id == ""

    def test_recovery_after_fallback(self):
        """回退后有新候选可以重新激活。"""
        acct_a = _make_account(self.svc, label="A")
        acct_b = _make_account(self.svc, label="B")
        # A depleted
        self.svc.update_health_signal(acct_a.id, "depleted", 0.9)
        # B active
        self.svc.refresh_snapshots()

        self.auto.upsert_policy()
        result = asyncio.get_event_loop().run_until_complete(
            self.auto.evaluate_scope("openai-codex", "*")
        )
        # 没有当前激活 → 应该激活 B
        assert result["action"] == "rotate"
        assert result["to"] == acct_b.id


# ══════════════════════════════════════════════════════════════
# 7. 审计事件测试
# ══════════════════════════════════════════════════════════════


class TestRotationEvents:
    """轮换审计事件。"""

    def setup_method(self):
        self.db = _create_test_db()
        self.svc = _create_svc(self.db)
        self.auto = _create_auto_svc(self.db, self.svc)

    def teardown_method(self):
        self.db.close()

    def test_record_event(self):
        self.auto.record_event(
            provider="openai-codex", model_pattern="*",
            from_account_id="a1", to_account_id="a2",
            reason="depleted", trigger="hard",
        )
        events = self.auto.list_events()
        assert len(events) == 1
        assert events[0].from_account_id == "a1"
        assert events[0].to_account_id == "a2"
        assert events[0].trigger == "hard"

    def test_list_events_with_filter(self):
        self.auto.record_event(
            provider="openai-codex", model_pattern="*",
            reason="r1", trigger="hard",
        )
        self.auto.record_event(
            provider="openai-codex", model_pattern="gpt-5.1-codex",
            reason="r2", trigger="soft",
        )
        all_events = self.auto.list_events()
        assert len(all_events) == 2

        filtered = self.auto.list_events(model_pattern="*")
        assert len(filtered) == 1
        assert filtered[0].reason == "r1"

    def test_event_to_dict(self):
        self.auto.record_event(
            provider="openai-codex", reason="test", trigger="manual",
        )
        events = self.auto.list_events()
        d = events[0].to_dict()
        assert "provider" in d
        assert "trigger" in d
        assert d["trigger"] == "manual"


# ══════════════════════════════════════════════════════════════
# 8. 冷却期测试
# ══════════════════════════════════════════════════════════════


class TestCooldown:
    """冷却期检测。"""

    def setup_method(self):
        self.db = _create_test_db()
        self.svc = _create_svc(self.db)
        self.auto = _create_auto_svc(self.db, self.svc)

    def teardown_method(self):
        self.db.close()

    def test_in_cooldown(self):
        """刚发生轮换，应在冷却期内。"""
        self.auto.record_event(
            provider="openai-codex", model_pattern="*",
            reason="test", trigger="soft",
        )
        assert self.auto._is_in_cooldown("openai-codex", "*", 300) is True

    def test_not_in_cooldown_no_events(self):
        """无轮换历史，不在冷却期。"""
        assert self.auto._is_in_cooldown("openai-codex", "*", 300) is False

    def test_soft_trigger_blocked_by_cooldown(self):
        """软触发在冷却期内应被阻止。"""
        acct_a = _make_account(self.svc, label="A")
        acct_b = _make_account(self.svc, label="B")
        self.svc.set_manual_active("openai-codex", "*", acct_a.id)
        self.svc.refresh_snapshots()

        # 制造 429 错误超阈值
        for _ in range(5):
            self.svc.log_usage(pool_account_id=acct_a.id, outcome="error", error_code="429")

        # 制造最近轮换事件（进入冷却期）
        self.auto.record_event(
            provider="openai-codex", model_pattern="*",
            reason="previous", trigger="soft",
        )

        self.auto.upsert_policy(rate_limit_threshold=3, cooldown_seconds=300)
        result = asyncio.get_event_loop().run_until_complete(
            self.auto.evaluate_scope("openai-codex", "*")
        )
        assert result["action"] == "none"
        assert "cooldown" in result["reason"]


# ══════════════════════════════════════════════════════════════
# 9. 配置测试
# ══════════════════════════════════════════════════════════════


class TestConfig:
    """配置字段和环境变量解析。"""

    def test_config_defaults(self):
        """ExcelManusConfig 应有自动轮换默认值。"""
        from excelmanus.config import ExcelManusConfig
        import dataclasses
        fields = {f.name for f in dataclasses.fields(ExcelManusConfig)}
        assert "pool_auto_enabled" in fields
        assert "pool_auto_interval_seconds" in fields
        assert "pool_auto_default_cooldown_seconds" in fields

    def test_pool_auto_independent(self):
        """pool_auto_enabled 独立于 pool_enabled。"""
        from excelmanus.config import ExcelManusConfig
        cfg = ExcelManusConfig(
            api_key="k", model="m", base_url="http://localhost",
            pool_enabled=True, pool_auto_enabled=False,
        )
        assert cfg.pool_enabled is True
        assert cfg.pool_auto_enabled is False


# ══════════════════════════════════════════════════════════════
# 10. 集成测试
# ══════════════════════════════════════════════════════════════


class TestIntegration:
    """端到端集成场景。"""

    def setup_method(self):
        self.db = _create_test_db()
        self.svc = _create_svc(self.db)
        self.auto = _create_auto_svc(self.db, self.svc)

    def teardown_method(self):
        self.db.close()

    def test_429_triggers_rotation_to_b(self):
        """A 出现 429 超阈值 → 自动切 B。"""
        acct_a = _make_account(self.svc, label="A")
        acct_b = _make_account(self.svc, label="B")
        self.svc.set_manual_active("openai-codex", "*", acct_a.id)
        self.svc.refresh_snapshots()

        # 制造 3 个 429 错误
        for _ in range(3):
            self.svc.log_usage(pool_account_id=acct_a.id, outcome="error", error_code="429")

        self.auto.upsert_policy(rate_limit_threshold=3)
        result = asyncio.get_event_loop().run_until_complete(
            self.auto.evaluate_scope("openai-codex", "*")
        )
        assert result["action"] == "rotate"
        assert result["to"] == acct_b.id
        # 验证映射已更新
        mapping = self.svc.get_manual_active("openai-codex", "*")
        assert mapping is not None
        assert mapping.pool_account_id == acct_b.id

    def test_b_depleted_then_fallback(self):
        """B depleted 且无其他可用号 → 回退原链路。"""
        acct_b = _make_account(self.svc, label="B")
        self.svc.set_manual_active("openai-codex", "*", acct_b.id)
        self.svc.update_health_signal(acct_b.id, "depleted", 0.9)
        self.svc.refresh_snapshots()

        self.auto.upsert_policy(fallback_to_default=True)
        result = asyncio.get_event_loop().run_until_complete(
            self.auto.evaluate_scope("openai-codex", "*")
        )
        assert result["action"] == "fallback"
        # 映射应已清空
        mapping = self.svc.get_manual_active("openai-codex", "*")
        assert mapping is None

    def test_recovery_reactivates(self):
        """回退后 A 恢复 → 重新激活。"""
        acct_a = _make_account(self.svc, label="A")
        # 初始无激活映射（回退状态）
        self.svc.refresh_snapshots()

        self.auto.upsert_policy()
        result = asyncio.get_event_loop().run_until_complete(
            self.auto.evaluate_scope("openai-codex", "*")
        )
        assert result["action"] == "rotate"
        assert result["to"] == acct_a.id

    def test_no_infinite_rotation_loop(self):
        """仅 1 个可用号时不会无限切换。"""
        acct_a = _make_account(self.svc, label="A")
        self.svc.set_manual_active("openai-codex", "*", acct_a.id)
        self.svc.refresh_snapshots()

        # A 健康正常，余额充足 → 不应触发
        self.auto.upsert_policy()
        result = asyncio.get_event_loop().run_until_complete(
            self.auto.evaluate_scope("openai-codex", "*")
        )
        assert result["action"] == "none"
        assert result["reason"] == "no_trigger"


# ══════════════════════════════════════════════════════════════
# 11. 模型序列化测试
# ══════════════════════════════════════════════════════════════


class TestModels:
    """新增数据模型序列化。"""

    def test_auto_policy_to_dict(self):
        from excelmanus.pool.models import PoolAutoPolicy
        policy = PoolAutoPolicy(
            id="p1", provider="openai-codex", enabled=True,
            low_watermark=0.15, cooldown_seconds=300,
        )
        d = policy.to_dict()
        assert d["id"] == "p1"
        assert d["low_watermark"] == 0.15
        assert d["enabled"] is True

    def test_rotation_event_to_dict(self):
        from excelmanus.pool.models import PoolRotationEvent
        event = PoolRotationEvent(
            id=1, provider="openai-codex",
            from_account_id="a1", to_account_id="a2",
            reason="depleted", trigger="hard", fallback_used=False,
        )
        d = event.to_dict()
        assert d["from_account_id"] == "a1"
        assert d["trigger"] == "hard"
        assert d["fallback_used"] is False


# ══════════════════════════════════════════════════════════════
# 12. 评分函数测试
# ══════════════════════════════════════════════════════════════


class TestScoring:
    """候选评分函数。"""

    def test_score_healthy_full_budget(self):
        from excelmanus.pool.models import PoolAccount, PoolBudgetSnapshot
        from excelmanus.pool.auto_rotate import PoolAutoRotateService
        acct = PoolAccount(
            id="a", health_signal="ok",
            daily_budget_tokens=100000, weekly_budget_tokens=500000,
        )
        snap = PoolBudgetSnapshot(
            pool_account_id="a",
            daily_remaining=100000, weekly_remaining=500000,
        )
        score = PoolAutoRotateService._score_candidate(acct, snap)
        # 0.7 * 1.0 + 0.3 * 1.0 = 1.0
        assert abs(score - 1.0) < 0.01

    def test_score_depleted_zero(self):
        from excelmanus.pool.models import PoolAccount, PoolBudgetSnapshot
        from excelmanus.pool.auto_rotate import PoolAutoRotateService
        acct = PoolAccount(
            id="a", health_signal="depleted",
            daily_budget_tokens=100000, weekly_budget_tokens=500000,
        )
        snap = PoolBudgetSnapshot(
            pool_account_id="a",
            daily_remaining=0, weekly_remaining=0,
        )
        score = PoolAutoRotateService._score_candidate(acct, snap)
        # 0.7 * 0.0 + 0.3 * 0.0 = 0.0
        assert abs(score - 0.0) < 0.01

    def test_score_rate_limited_half_budget(self):
        from excelmanus.pool.models import PoolAccount, PoolBudgetSnapshot
        from excelmanus.pool.auto_rotate import PoolAutoRotateService
        acct = PoolAccount(
            id="a", health_signal="rate_limited",
            daily_budget_tokens=100000, weekly_budget_tokens=500000,
        )
        snap = PoolBudgetSnapshot(
            pool_account_id="a",
            daily_remaining=50000, weekly_remaining=250000,
        )
        score = PoolAutoRotateService._score_candidate(acct, snap)
        # 0.7 * 0.5 + 0.3 * 0.6 = 0.35 + 0.18 = 0.53
        assert abs(score - 0.53) < 0.01
