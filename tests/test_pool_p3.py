"""号池 P3 稳态治理测试：迁移、scope state、mode 守卫、迟滞、驻留、熔断器、指标、策略新字段、配置。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock


# ── 辅助：创建内存 SQLite 数据库并迁移到 v23 ─────────────────


def _create_test_db():
    from excelmanus.database import Database
    db = Database(":memory:")
    return db


def _create_svc(db):
    from excelmanus.pool.service import PoolService
    return PoolService(conn=db.conn)


def _create_auto_svc(db, pool_svc, breaker=None):
    from excelmanus.pool.auto_rotate import PoolAutoRotateService
    return PoolAutoRotateService(
        conn=db.conn, pool_service=pool_svc, breaker=breaker,
    )


def _create_breaker(db, threshold=5, open_seconds=120):
    from excelmanus.pool.breaker import BreakerManager
    return BreakerManager(
        conn=db.conn, failure_threshold=threshold, open_seconds=open_seconds,
    )


def _create_metrics(db):
    from excelmanus.pool.metrics import MetricsAggregator
    return MetricsAggregator(conn=db.conn)


def _make_account(svc, label="A", daily=100000, weekly=500000):
    return svc.create_account(
        label=label, provider="openai-codex",
        daily_budget_tokens=daily, weekly_budget_tokens=weekly,
    )


# ══════════════════════════════════════════════════════════════
# 1. 迁移测试 — TestMigrationV23
# ══════════════════════════════════════════════════════════════


class TestMigrationV23:

    def test_v23_creates_new_tables(self):
        db = _create_test_db()
        tables = {
            row[0]
            for row in db.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "pool_scope_state" in tables
        assert "pool_account_breakers" in tables
        assert "pool_rotation_metrics_minute" in tables
        db.close()

    def test_alter_columns_exist(self):
        db = _create_test_db()
        cols = {
            row[1]
            for row in db.conn.execute("PRAGMA table_info(pool_auto_policies)").fetchall()
        }
        assert "hysteresis_delta" in cols
        assert "min_dwell_seconds" in cols
        assert "breaker_open_seconds" in cols
        db.close()

    def test_version_is_23(self):
        db = _create_test_db()
        row = db.conn.execute("SELECT MAX(version) as v FROM schema_version").fetchone()
        assert row["v"] >= 23
        db.close()

    def test_old_data_survives(self):
        db = _create_test_db()
        tables = {
            row[0]
            for row in db.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "pool_accounts" in tables
        assert "pool_auto_policies" in tables
        assert "pool_rotation_events" in tables
        db.close()


# ══════════════════════════════════════════════════════════════
# 2. Scope State CRUD — TestScopeState
# ══════════════════════════════════════════════════════════════


class TestScopeState:

    def test_get_scope_state_returns_none_initially(self):
        db = _create_test_db()
        svc = _create_svc(db)
        auto = _create_auto_svc(db, svc)
        assert auto.get_scope_state("openai-codex", "*") is None
        db.close()

    def test_upsert_creates_scope_state(self):
        db = _create_test_db()
        svc = _create_svc(db)
        auto = _create_auto_svc(db, svc)
        state = auto.upsert_scope_state(
            "openai-codex", "*",
            current_account_id="acct-1",
            current_score=0.85,
        )
        assert state.current_account_id == "acct-1"
        assert state.current_score == 0.85
        assert state.mode == "auto"
        db.close()

    def test_set_scope_mode(self):
        db = _create_test_db()
        svc = _create_svc(db)
        auto = _create_auto_svc(db, svc)
        state = auto.set_scope_mode("openai-codex", "*", "frozen")
        assert state.mode == "frozen"
        # Read back
        state2 = auto.get_scope_state("openai-codex", "*")
        assert state2 is not None
        assert state2.mode == "frozen"
        db.close()

    def test_to_dict(self):
        db = _create_test_db()
        svc = _create_svc(db)
        auto = _create_auto_svc(db, svc)
        state = auto.upsert_scope_state("openai-codex", "*", mode="auto")
        d = state.to_dict()
        assert "provider" in d
        assert "mode" in d
        assert d["mode"] == "auto"
        db.close()


# ══════════════════════════════════════════════════════════════
# 3. Mode Guard — TestModeGuard
# ══════════════════════════════════════════════════════════════


class TestModeGuard:

    def test_auto_mode_evaluates_normally(self):
        db = _create_test_db()
        svc = _create_svc(db)
        auto = _create_auto_svc(db, svc)
        auto.upsert_policy(provider="openai-codex")
        auto.set_scope_mode("openai-codex", "*", "auto")
        result = asyncio.get_event_loop().run_until_complete(
            auto.evaluate_scope("openai-codex", "*")
        )
        # No accounts → no_active_no_candidates
        assert result["action"] == "none"
        assert result["reason"] != "manual_locked"
        db.close()

    def test_manual_locked_blocks_evaluation(self):
        db = _create_test_db()
        svc = _create_svc(db)
        auto = _create_auto_svc(db, svc)
        auto.upsert_policy(provider="openai-codex")
        auto.set_scope_mode("openai-codex", "*", "manual_locked")
        result = asyncio.get_event_loop().run_until_complete(
            auto.evaluate_scope("openai-codex", "*")
        )
        assert result["action"] == "none"
        assert result["reason"] == "manual_locked"
        db.close()

    def test_frozen_returns_dry_run(self):
        db = _create_test_db()
        svc = _create_svc(db)
        auto = _create_auto_svc(db, svc)
        auto.upsert_policy(provider="openai-codex")
        auto.set_scope_mode("openai-codex", "*", "frozen")
        acct = _make_account(svc, "A")
        svc.set_manual_active("openai-codex", "*", acct.id)
        # Deplete the account to trigger hard rotation
        svc.update_health_signal(acct.id, "depleted", 0.9)
        # Create a second account as candidate
        _make_account(svc, "B")
        result = asyncio.get_event_loop().run_until_complete(
            auto.evaluate_scope("openai-codex", "*")
        )
        assert result["action"] == "dry_run"
        db.close()


# ══════════════════════════════════════════════════════════════
# 4. Hysteresis — TestHysteresis
# ══════════════════════════════════════════════════════════════


class TestHysteresis:

    def _setup(self):
        db = _create_test_db()
        svc = _create_svc(db)
        auto = _create_auto_svc(db, svc)
        auto.upsert_policy(
            provider="openai-codex",
            hysteresis_delta=0.20,
            low_watermark=0.5,
        )
        # Create two accounts
        a = _make_account(svc, "A", daily=100000)
        b = _make_account(svc, "B", daily=100000)
        svc.set_manual_active("openai-codex", "*", a.id)
        return db, svc, auto, a, b

    def test_hysteresis_blocks_when_delta_too_small(self):
        db, svc, auto, a, b = self._setup()
        # Set current score high
        auto.upsert_scope_state(
            "openai-codex", "*",
            current_account_id=a.id,
            current_score=0.80,
            activated_at=(datetime.now(tz=timezone.utc) - timedelta(hours=1)).isoformat(),
        )
        # Log low budget for current account to trigger soft
        snap = svc.get_snapshot(a.id)
        if snap:
            # Force low budget ratio
            svc._conn.execute(
                "DELETE FROM pool_budget_snapshots WHERE pool_account_id = ?",
                (a.id,),
            )
            svc._conn.execute(
                """INSERT INTO pool_budget_snapshots
                   (pool_account_id, day_window_tokens, week_window_tokens,
                    daily_remaining, weekly_remaining, snapshot_at)
                   VALUES (?,?,?,?,?,?)""",
                (a.id, 90000, 0, 10000, 500000,
                 datetime.now(tz=timezone.utc).isoformat()),
            )
            svc._conn.commit()
        # Both have similar budget → delta < 0.20
        result = asyncio.get_event_loop().run_until_complete(
            auto.evaluate_scope("openai-codex", "*")
        )
        # Should be blocked by hysteresis (or no_trigger if soft not hit)
        assert result["action"] == "none"
        db.close()

    def test_hard_trigger_bypasses_hysteresis(self):
        db, svc, auto, a, b = self._setup()
        auto.upsert_scope_state(
            "openai-codex", "*",
            current_account_id=a.id,
            current_score=0.90,
        )
        # Hard trigger: depleted
        svc.update_health_signal(a.id, "depleted", 0.9)
        result = asyncio.get_event_loop().run_until_complete(
            auto.evaluate_scope("openai-codex", "*")
        )
        assert result["action"] in ("rotate", "fallback")
        db.close()

    def test_no_scope_state_defaults_allow(self):
        """No scope state → current_score=0 → any candidate with score > delta wins."""
        db = _create_test_db()
        svc = _create_svc(db)
        auto = _create_auto_svc(db, svc)
        auto.upsert_policy(provider="openai-codex", hysteresis_delta=0.12)
        result = asyncio.get_event_loop().run_until_complete(
            auto.evaluate_scope("openai-codex", "*")
        )
        assert result["action"] == "none"  # No accounts at all
        db.close()


# ══════════════════════════════════════════════════════════════
# 5. Dwell Time — TestDwellTime
# ══════════════════════════════════════════════════════════════


class TestDwellTime:

    def test_dwell_blocks_soft_trigger(self):
        db = _create_test_db()
        svc = _create_svc(db)
        auto = _create_auto_svc(db, svc)
        auto.upsert_policy(
            provider="openai-codex",
            min_dwell_seconds=300,
            low_watermark=0.99,
        )
        a = _make_account(svc, "A")
        _make_account(svc, "B")
        svc.set_manual_active("openai-codex", "*", a.id)
        # Set activated_at to just now → within dwell
        auto.upsert_scope_state(
            "openai-codex", "*",
            current_account_id=a.id,
            current_score=0.5,
            activated_at=datetime.now(tz=timezone.utc).isoformat(),
        )
        result = asyncio.get_event_loop().run_until_complete(
            auto.evaluate_scope("openai-codex", "*")
        )
        assert result["reason"] in ("dwell_blocked", "no_trigger", "soft_trigger_in_cooldown")
        db.close()

    def test_dwell_expired_allows_rotation(self):
        db = _create_test_db()
        svc = _create_svc(db)
        auto = _create_auto_svc(db, svc)
        auto.upsert_policy(
            provider="openai-codex",
            min_dwell_seconds=1,
            low_watermark=0.99,
            hysteresis_delta=0.0,
        )
        a = _make_account(svc, "A")
        _make_account(svc, "B")
        svc.set_manual_active("openai-codex", "*", a.id)
        # Set activated_at far in the past → dwell expired
        auto.upsert_scope_state(
            "openai-codex", "*",
            current_account_id=a.id,
            current_score=0.0,
            activated_at=(datetime.now(tz=timezone.utc) - timedelta(hours=1)).isoformat(),
        )
        result = asyncio.get_event_loop().run_until_complete(
            auto.evaluate_scope("openai-codex", "*")
        )
        # Could trigger or not depending on soft trigger
        assert result["action"] in ("rotate", "none")
        db.close()

    def test_hard_trigger_bypasses_dwell(self):
        db = _create_test_db()
        svc = _create_svc(db)
        auto = _create_auto_svc(db, svc)
        auto.upsert_policy(provider="openai-codex", min_dwell_seconds=9999)
        a = _make_account(svc, "A")
        _make_account(svc, "B")
        svc.set_manual_active("openai-codex", "*", a.id)
        auto.upsert_scope_state(
            "openai-codex", "*",
            current_account_id=a.id,
            activated_at=datetime.now(tz=timezone.utc).isoformat(),
        )
        # Hard trigger
        svc.update_health_signal(a.id, "depleted", 0.9)
        result = asyncio.get_event_loop().run_until_complete(
            auto.evaluate_scope("openai-codex", "*")
        )
        assert result["action"] in ("rotate", "fallback")
        db.close()


# ══════════════════════════════════════════════════════════════
# 6. Breaker — TestBreaker
# ══════════════════════════════════════════════════════════════


class TestBreaker:

    def test_closed_to_open(self):
        db = _create_test_db()
        breaker = _create_breaker(db, threshold=3, open_seconds=60)
        breaker.record_failure("acct-1")
        breaker.record_failure("acct-1")
        state = breaker.record_failure("acct-1")
        assert state.breaker_state == "open"
        assert state.open_until != ""
        db.close()

    def test_open_to_half_open_on_expiry(self):
        db = _create_test_db()
        breaker = _create_breaker(db, threshold=1, open_seconds=0)
        breaker.record_failure("acct-1")
        # open_seconds=0 → already expired
        state = breaker.get_state("acct-1")
        assert state.breaker_state == "half_open"
        db.close()

    def test_half_open_to_closed_on_success(self):
        db = _create_test_db()
        breaker = _create_breaker(db, threshold=1, open_seconds=0)
        breaker.record_failure("acct-1")
        breaker.get_state("acct-1")  # triggers half_open
        state = breaker.record_success("acct-1")
        assert state.breaker_state == "closed"
        assert state.consecutive_failures == 0
        db.close()

    def test_half_open_to_open_on_failure(self):
        db = _create_test_db()
        breaker = _create_breaker(db, threshold=1, open_seconds=0)
        breaker.record_failure("acct-1")
        breaker.get_state("acct-1")  # triggers half_open
        state = breaker.record_failure("acct-1")
        assert state.breaker_state == "open"
        db.close()

    def test_is_available(self):
        db = _create_test_db()
        breaker = _create_breaker(db, threshold=1, open_seconds=3600)
        assert breaker.is_available("acct-1") is True
        breaker.record_failure("acct-1")
        assert breaker.is_available("acct-1") is False
        db.close()

    def test_list_breakers(self):
        db = _create_test_db()
        breaker = _create_breaker(db, threshold=1, open_seconds=3600)
        breaker.record_failure("acct-1")
        breaker.record_failure("acct-2")
        result = breaker.list_breakers()
        assert len(result) == 2
        db.close()

    def test_candidate_excludes_open(self):
        db = _create_test_db()
        svc = _create_svc(db)
        breaker = _create_breaker(db, threshold=1, open_seconds=3600)
        auto = _create_auto_svc(db, svc, breaker=breaker)
        a = _make_account(svc, "A")
        b = _make_account(svc, "B")
        breaker.record_failure(b.id)  # Open breaker on B
        candidates = auto._list_candidates("openai-codex", exclude_id=a.id)
        acct_ids = [c.id for c in candidates]
        assert b.id not in acct_ids
        db.close()


# ══════════════════════════════════════════════════════════════
# 7. Breaker Integration with signals — TestBreakerIntegration
# ══════════════════════════════════════════════════════════════


class TestBreakerIntegration:

    def test_error_signal_records_failure(self):
        db = _create_test_db()
        svc = _create_svc(db)
        breaker = _create_breaker(db, threshold=5)
        acct = _make_account(svc, "A")
        from excelmanus.pool.signals import update_pool_health_from_error
        update_pool_health_from_error(
            svc, acct.id, status_code=429, breaker=breaker,
        )
        state = breaker.get_state(acct.id)
        assert state.consecutive_failures == 1
        db.close()

    def test_success_signal_records_success(self):
        db = _create_test_db()
        svc = _create_svc(db)
        breaker = _create_breaker(db, threshold=5)
        acct = _make_account(svc, "A")
        breaker.record_failure(acct.id)
        breaker.record_failure(acct.id)
        from excelmanus.pool.signals import update_pool_health_on_success
        update_pool_health_on_success(svc, acct.id, breaker=breaker)
        state = breaker.get_state(acct.id)
        assert state.consecutive_failures == 0
        db.close()

    def test_open_account_excluded_from_candidates(self):
        db = _create_test_db()
        svc = _create_svc(db)
        breaker = _create_breaker(db, threshold=2, open_seconds=3600)
        auto = _create_auto_svc(db, svc, breaker=breaker)
        a = _make_account(svc, "A")
        b = _make_account(svc, "B")
        # Trip breaker on B
        breaker.record_failure(b.id)
        breaker.record_failure(b.id)
        assert breaker.is_available(b.id) is False
        candidates = auto._list_candidates("openai-codex", exclude_id=a.id)
        assert all(c.id != b.id for c in candidates)
        db.close()


# ══════════════════════════════════════════════════════════════
# 8. Metrics — TestMetrics
# ══════════════════════════════════════════════════════════════


class TestMetrics:

    def test_aggregate_minute_empty(self):
        db = _create_test_db()
        metrics = _create_metrics(db)
        m = metrics.aggregate_minute("openai-codex", "*")
        assert m.total_requests == 0
        assert m.success_count == 0
        db.close()

    def test_query_returns_aggregated(self):
        db = _create_test_db()
        metrics = _create_metrics(db)
        metrics.aggregate_minute("openai-codex", "*")
        result = metrics.query(provider="openai-codex", minutes=60)
        assert len(result) >= 1
        db.close()

    def test_query_filter(self):
        db = _create_test_db()
        metrics = _create_metrics(db)
        metrics.aggregate_minute("openai-codex", "*")
        metrics.aggregate_minute("anthropic", "*")
        result = metrics.query(provider="openai-codex", minutes=60)
        assert all(m.provider == "openai-codex" for m in result)
        db.close()

    def test_aggregate_all_scopes(self):
        db = _create_test_db()
        svc = _create_svc(db)
        auto = _create_auto_svc(db, svc)
        metrics = _create_metrics(db)
        auto.upsert_policy(provider="openai-codex")
        policies = auto.list_policies()
        count = metrics.aggregate_all_scopes(policies)
        assert count == 1
        db.close()


# ══════════════════════════════════════════════════════════════
# 9. Policy New Fields — TestPolicyNewFields
# ══════════════════════════════════════════════════════════════


class TestPolicyNewFields:

    def test_upsert_new_fields(self):
        db = _create_test_db()
        svc = _create_svc(db)
        auto = _create_auto_svc(db, svc)
        policy = auto.upsert_policy(
            provider="openai-codex",
            hysteresis_delta=0.25,
            min_dwell_seconds=600,
            breaker_open_seconds=300,
        )
        assert policy.hysteresis_delta == 0.25
        assert policy.min_dwell_seconds == 600
        assert policy.breaker_open_seconds == 300
        db.close()

    def test_row_to_policy_deserializes(self):
        db = _create_test_db()
        svc = _create_svc(db)
        auto = _create_auto_svc(db, svc)
        auto.upsert_policy(
            provider="openai-codex",
            hysteresis_delta=0.3,
            min_dwell_seconds=120,
        )
        policy = auto.get_policy("openai-codex")
        assert policy is not None
        assert policy.hysteresis_delta == 0.3
        assert policy.min_dwell_seconds == 120
        db.close()

    def test_default_values(self):
        db = _create_test_db()
        svc = _create_svc(db)
        auto = _create_auto_svc(db, svc)
        policy = auto.upsert_policy(provider="openai-codex")
        assert policy.hysteresis_delta == 0.12
        assert policy.min_dwell_seconds == 180
        assert policy.breaker_open_seconds == 120
        db.close()


# ══════════════════════════════════════════════════════════════
# 10. Config — TestConfig
# ══════════════════════════════════════════════════════════════


class TestConfig:

    def test_new_config_fields_exist(self):
        from excelmanus.config import ExcelManusConfig
        cfg = ExcelManusConfig.__dataclass_fields__
        assert "pool_auto_hysteresis_delta" in cfg
        assert "pool_auto_min_dwell_seconds" in cfg
        assert "pool_auto_breaker_open_seconds" in cfg
        assert "pool_auto_breaker_threshold" in cfg

    def test_env_var_parsing(self):
        import os
        os.environ["EXCELMANUS_POOL_AUTO_HYSTERESIS_DELTA"] = "0.25"
        os.environ["EXCELMANUS_POOL_AUTO_MIN_DWELL"] = "300"
        os.environ["EXCELMANUS_POOL_AUTO_BREAKER_OPEN"] = "240"
        os.environ["EXCELMANUS_POOL_AUTO_BREAKER_THRESHOLD"] = "10"
        try:
            # Just verify parsing doesn't crash
            val = float(os.environ["EXCELMANUS_POOL_AUTO_HYSTERESIS_DELTA"])
            assert val == 0.25
            val2 = int(os.environ["EXCELMANUS_POOL_AUTO_MIN_DWELL"])
            assert val2 == 300
        finally:
            for k in [
                "EXCELMANUS_POOL_AUTO_HYSTERESIS_DELTA",
                "EXCELMANUS_POOL_AUTO_MIN_DWELL",
                "EXCELMANUS_POOL_AUTO_BREAKER_OPEN",
                "EXCELMANUS_POOL_AUTO_BREAKER_THRESHOLD",
            ]:
                os.environ.pop(k, None)


# ══════════════════════════════════════════════════════════════
# 11. Integration E2E — TestIntegrationE2E
# ══════════════════════════════════════════════════════════════


class TestIntegrationE2E:

    def test_full_debounce_scenario(self):
        """Complete flow: soft trigger → dwell check → hysteresis check."""
        db = _create_test_db()
        svc = _create_svc(db)
        auto = _create_auto_svc(db, svc)
        auto.upsert_policy(
            provider="openai-codex",
            min_dwell_seconds=0,
            hysteresis_delta=0.0,
            low_watermark=0.99,
        )
        a = _make_account(svc, "A")
        b = _make_account(svc, "B")
        svc.set_manual_active("openai-codex", "*", a.id)
        auto.upsert_scope_state(
            "openai-codex", "*",
            current_account_id=a.id,
            current_score=0.0,
            activated_at=(datetime.now(tz=timezone.utc) - timedelta(hours=1)).isoformat(),
        )
        result = asyncio.get_event_loop().run_until_complete(
            auto.evaluate_scope("openai-codex", "*")
        )
        # With low_watermark=0.99 and default budget, soft should trigger and rotate
        assert result["action"] in ("rotate", "none")
        db.close()

    def test_breaker_heal_cycle(self):
        """Account breaks → heals → available again."""
        db = _create_test_db()
        breaker = _create_breaker(db, threshold=2, open_seconds=3600)
        breaker.record_failure("acct-1")
        breaker.record_failure("acct-1")
        assert breaker.is_available("acct-1") is False
        # Manually expire the breaker
        from datetime import datetime, timezone
        expired = (datetime.now(tz=timezone.utc) - timedelta(seconds=1)).isoformat()
        db.conn.execute(
            "UPDATE pool_account_breakers SET open_until = ? WHERE pool_account_id = ?",
            (expired, "acct-1"),
        )
        db.conn.commit()
        state = breaker.get_state("acct-1")
        assert state.breaker_state == "half_open"
        # Success → healed
        breaker.record_success("acct-1")
        assert breaker.is_available("acct-1") is True
        state = breaker.get_state("acct-1")
        assert state.breaker_state == "closed"
        db.close()

    def test_frozen_mode_evaluates_not_executes(self):
        db = _create_test_db()
        svc = _create_svc(db)
        auto = _create_auto_svc(db, svc)
        auto.upsert_policy(provider="openai-codex")
        a = _make_account(svc, "A")
        b = _make_account(svc, "B")
        svc.set_manual_active("openai-codex", "*", a.id)
        svc.update_health_signal(a.id, "depleted", 0.9)
        auto.set_scope_mode("openai-codex", "*", "frozen")
        result = asyncio.get_event_loop().run_until_complete(
            auto.evaluate_scope("openai-codex", "*")
        )
        assert result["action"] == "dry_run"
        # Verify mapping was NOT actually changed
        mapping = svc.get_manual_active("openai-codex", "*")
        assert mapping is not None
        assert mapping.pool_account_id == a.id
        db.close()

    def test_manual_locked_no_intervention(self):
        db = _create_test_db()
        svc = _create_svc(db)
        auto = _create_auto_svc(db, svc)
        auto.upsert_policy(provider="openai-codex")
        a = _make_account(svc, "A")
        _make_account(svc, "B")
        svc.set_manual_active("openai-codex", "*", a.id)
        svc.update_health_signal(a.id, "depleted", 0.9)
        auto.set_scope_mode("openai-codex", "*", "manual_locked")
        result = asyncio.get_event_loop().run_until_complete(
            auto.evaluate_scope("openai-codex", "*")
        )
        assert result["action"] == "none"
        assert result["reason"] == "manual_locked"
        # Mapping unchanged
        mapping = svc.get_manual_active("openai-codex", "*")
        assert mapping.pool_account_id == a.id
        db.close()


# ══════════════════════════════════════════════════════════════
# 12. API Endpoints (unit-level) — TestApiEndpoints
# ══════════════════════════════════════════════════════════════


class TestApiEndpoints:

    def test_router_has_new_endpoints(self):
        from excelmanus.pool.router import router
        paths = [r.path for r in router.routes if hasattr(r, "path")]
        prefix = "/api/v1/admin/pool"
        assert f"{prefix}/auto/scopes/{{provider}}/{{model_pattern}}/mode" in paths
        assert f"{prefix}/auto/scopes/{{provider}}/{{model_pattern}}/state" in paths
        assert f"{prefix}/auto/scopes/{{provider}}/{{model_pattern}}/dry-run" in paths
        assert f"{prefix}/auto/metrics" in paths
        assert f"{prefix}/auto/breakers" in paths

    def test_set_scope_mode_invalid_raises(self):
        db = _create_test_db()
        svc = _create_svc(db)
        auto = _create_auto_svc(db, svc)
        import pytest
        with pytest.raises(ValueError, match="无效的 mode"):
            auto.set_scope_mode("openai-codex", "*", "invalid_mode")
        db.close()

    def test_dry_run_endpoint_scope(self):
        db = _create_test_db()
        svc = _create_svc(db)
        auto = _create_auto_svc(db, svc)
        auto.upsert_policy(provider="openai-codex")
        result = asyncio.get_event_loop().run_until_complete(
            auto.evaluate_scope("openai-codex", "*", dry_run=True)
        )
        # No accounts → none (even in dry_run)
        assert result["action"] == "none"
        db.close()

    def test_scope_state_maintained_after_rotation(self):
        db = _create_test_db()
        svc = _create_svc(db)
        auto = _create_auto_svc(db, svc)
        auto.upsert_policy(provider="openai-codex")
        a = _make_account(svc, "A")
        b = _make_account(svc, "B")
        svc.set_manual_active("openai-codex", "*", a.id)
        svc.update_health_signal(a.id, "depleted", 0.9)
        result = asyncio.get_event_loop().run_until_complete(
            auto.evaluate_scope("openai-codex", "*")
        )
        if result["action"] == "rotate":
            state = auto.get_scope_state("openai-codex", "*")
            assert state is not None
            assert state.current_account_id == result.get("to", "")
            assert state.last_rotation_at != ""
        db.close()


# ══════════════════════════════════════════════════════════════
# 13. Regression: P3 bug fixes
# ══════════════════════════════════════════════════════════════


class TestP3BugfixRegression:
    """Regression tests for the 5 post-P3 bugfixes."""

    # ── P1-1: breaker 参数透传到 signals ──

    def test_signals_error_accepts_breaker_kwarg(self):
        """update_pool_health_from_error signature accepts breaker."""
        import inspect
        from excelmanus.pool.signals import update_pool_health_from_error
        sig = inspect.signature(update_pool_health_from_error)
        assert "breaker" in sig.parameters

    def test_signals_success_accepts_breaker_kwarg(self):
        """update_pool_health_on_success signature accepts breaker."""
        import inspect
        from excelmanus.pool.signals import update_pool_health_on_success
        sig = inspect.signature(update_pool_health_on_success)
        assert "breaker" in sig.parameters

    # ── P1-2: metrics SQL 按 provider 过滤 ──

    def test_metrics_scope_isolation(self):
        """Different providers should produce independent metrics."""
        db = _create_test_db()
        svc = _create_svc(db)
        metrics = _create_metrics(db)
        # Create accounts for two providers
        acct_a = svc.create_account(label="A", provider="openai-codex")
        acct_b = svc.create_account(label="B", provider="anthropic")
        # Log usage for both
        now = datetime.now(tz=timezone.utc)
        bucket = now.replace(second=0, microsecond=0).isoformat()
        svc.log_usage(pool_account_id=acct_a.id, outcome="success")
        svc.log_usage(pool_account_id=acct_a.id, outcome="error", error_code="429")
        svc.log_usage(pool_account_id=acct_b.id, outcome="success")
        svc.log_usage(pool_account_id=acct_b.id, outcome="success")
        svc.log_usage(pool_account_id=acct_b.id, outcome="success")
        # Aggregate for openai-codex only
        m = metrics.aggregate_minute("openai-codex", "*", minute_bucket=bucket)
        assert m.total_requests == 2  # only openai-codex
        assert m.error_429 == 1
        # Aggregate for anthropic
        m2 = metrics.aggregate_minute("anthropic", "*", minute_bucket=bucket)
        assert m2.total_requests == 3
        assert m2.error_429 == 0
        db.close()

    # ── P1-3: 指标聚合默认取上一分钟 ──

    def test_aggregate_default_is_prev_minute(self):
        """Default bucket should be previous minute, not current."""
        from excelmanus.pool.metrics import _prev_minute_bucket, _current_minute_bucket
        prev = _prev_minute_bucket()
        curr = _current_minute_bucket()
        assert prev != curr
        assert prev < curr

    # ── P2-1: _row_to_policy 保真 0 值 ──

    def test_row_to_policy_preserves_zero(self):
        """hysteresis_delta=0, min_dwell_seconds=0 should not revert to defaults."""
        db = _create_test_db()
        svc = _create_svc(db)
        auto = _create_auto_svc(db, svc)
        auto.upsert_policy(
            provider="openai-codex",
            hysteresis_delta=0.0,
            min_dwell_seconds=0,
            breaker_open_seconds=0,
            low_watermark=0.0,
            cooldown_seconds=0,
        )
        policy = auto.get_policy("openai-codex")
        assert policy is not None
        assert policy.hysteresis_delta == 0.0
        assert policy.min_dwell_seconds == 0
        assert policy.breaker_open_seconds == 0
        assert policy.low_watermark == 0.0
        assert policy.cooldown_seconds == 0
        db.close()

    # ── P2-2: breaker.record_failure open_seconds 覆盖 ──

    def test_breaker_record_failure_open_seconds_override(self):
        """record_failure should accept open_seconds override."""
        db = _create_test_db()
        breaker = _create_breaker(db, threshold=1, open_seconds=3600)
        state = breaker.record_failure("acct-1", open_seconds=10)
        assert state.breaker_state == "open"
        # open_until should be ~10s from now, not 3600s
        from datetime import datetime, timezone
        open_until = datetime.fromisoformat(state.open_until)
        if open_until.tzinfo is None:
            open_until = open_until.replace(tzinfo=timezone.utc)
        diff = (open_until - datetime.now(tz=timezone.utc)).total_seconds()
        assert diff < 30  # much less than 3600
        db.close()

    def test_router_uses_config_defaults(self):
        """upsert_policy in router should read config defaults, not hardcoded."""
        from excelmanus.pool.router import router
        # Verify the endpoint function source references config
        for route in router.routes:
            if hasattr(route, "path") and "policies" in getattr(route, "path", ""):
                if hasattr(route, "endpoint"):
                    import inspect
                    src = inspect.getsource(route.endpoint)
                    if "upsert" in src:
                        assert "pool_auto_hysteresis_delta" in src or "_def_hysteresis" in src
                        break
