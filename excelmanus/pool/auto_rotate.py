"""PoolAutoRotateService：号池自动轮换决策与执行。"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from excelmanus.pool.models import (
    PoolAutoPolicy,
    PoolRotationEvent,
    PoolScopeState,
)

if TYPE_CHECKING:
    from excelmanus.db_adapter import ConnectionAdapter
    from excelmanus.pool.breaker import BreakerManager
    from excelmanus.pool.service import PoolService

logger = logging.getLogger(__name__)

# 健康信号 → 评分映射
_HEALTH_SCORES: dict[str, float] = {
    "ok": 1.0,
    "transient": 0.7,
    "rate_limited": 0.6,
    "depleted": 0.0,
}


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _row_to_dict(row: Any) -> dict[str, Any]:
    if isinstance(row, dict):
        return row
    if hasattr(row, "keys"):
        return dict(row)
    return {}


class PoolAutoRotateService:
    """号池自动轮换服务。

    负责：策略管理、触发评估、候选筛选打分、轮换执行、审计记录。
    """

    def __init__(
        self,
        conn: "ConnectionAdapter",
        pool_service: "PoolService",
        default_cooldown_seconds: int = 300,
        breaker: "BreakerManager | None" = None,
    ) -> None:
        self._conn = conn
        self._pool = pool_service
        self._default_cooldown = default_cooldown_seconds
        self._breaker = breaker
        self._eval_lock = asyncio.Lock()
        self._last_eval_time: float = 0.0  # monotonic，用于防抖

    # ── 策略 CRUD ─────────────────────────────────────────────

    def upsert_policy(
        self,
        *,
        provider: str = "openai-codex",
        model_pattern: str = "*",
        enabled: bool = True,
        low_watermark: float = 0.15,
        rate_limit_threshold: int = 3,
        transient_threshold: int = 5,
        error_window_minutes: int = 5,
        cooldown_seconds: int | None = None,
        fallback_to_default: bool = True,
        hysteresis_delta: float = 0.12,
        min_dwell_seconds: int = 180,
        breaker_open_seconds: int = 120,
    ) -> PoolAutoPolicy:
        """创建或更新策略（DELETE+INSERT 保证 SQLite/PG 兼容）。"""
        if cooldown_seconds is None:
            cooldown_seconds = self._default_cooldown
        now = _now_iso()
        # 查找是否已有
        existing = self.get_policy(provider, model_pattern)
        pid = existing.id if existing else str(uuid.uuid4())

        self._conn.execute(
            "DELETE FROM pool_auto_policies WHERE provider = ? AND model_pattern = ?",
            (provider, model_pattern),
        )
        self._conn.execute(
            """INSERT INTO pool_auto_policies
               (id, provider, model_pattern, enabled, low_watermark,
                rate_limit_threshold, transient_threshold, error_window_minutes,
                cooldown_seconds, fallback_to_default,
                hysteresis_delta, min_dwell_seconds, breaker_open_seconds,
                created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                pid, provider, model_pattern, int(enabled), low_watermark,
                rate_limit_threshold, transient_threshold, error_window_minutes,
                cooldown_seconds, int(fallback_to_default),
                hysteresis_delta, min_dwell_seconds, breaker_open_seconds,
                existing.created_at if existing else now, now,
            ),
        )
        self._conn.commit()
        return PoolAutoPolicy(
            id=pid, provider=provider, model_pattern=model_pattern,
            enabled=enabled, low_watermark=low_watermark,
            rate_limit_threshold=rate_limit_threshold,
            transient_threshold=transient_threshold,
            error_window_minutes=error_window_minutes,
            cooldown_seconds=cooldown_seconds,
            fallback_to_default=fallback_to_default,
            hysteresis_delta=hysteresis_delta,
            min_dwell_seconds=min_dwell_seconds,
            breaker_open_seconds=breaker_open_seconds,
            created_at=existing.created_at if existing else now,
            updated_at=now,
        )

    def get_policy(
        self, provider: str, model_pattern: str = "*",
    ) -> PoolAutoPolicy | None:
        """获取策略。"""
        row = self._conn.execute(
            "SELECT * FROM pool_auto_policies WHERE provider = ? AND model_pattern = ?",
            (provider, model_pattern),
        ).fetchone()
        if not row:
            return None
        return self._row_to_policy(row)

    def list_policies(self) -> list[PoolAutoPolicy]:
        """列出所有策略。"""
        rows = self._conn.execute(
            "SELECT * FROM pool_auto_policies ORDER BY created_at DESC",
        ).fetchall()
        return [self._row_to_policy(r) for r in rows]

    # ── 评估入口 ──────────────────────────────────────────────

    async def evaluate_all_policies(self) -> list[dict[str, Any]]:
        """扫描所有启用策略并逐一评估。返回每个 scope 的评估结果。"""
        policies = self.list_policies()
        results: list[dict[str, Any]] = []
        for policy in policies:
            if not policy.enabled:
                continue
            try:
                result = await self.evaluate_scope(
                    policy.provider, policy.model_pattern, trigger="periodic",
                )
                results.append(result)
            except Exception:
                logger.warning(
                    "策略评估异常: provider=%s, pattern=%s",
                    policy.provider, policy.model_pattern, exc_info=True,
                )
                results.append({
                    "action": "error",
                    "reason": "evaluate_exception",
                    "provider": policy.provider,
                    "model_pattern": policy.model_pattern,
                })
        return results

    async def evaluate_scope(
        self,
        provider: str,
        model_pattern: str = "*",
        trigger: str = "periodic",
        *,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """单 scope 评估与决策。

        返回 {"action": "none"|"rotate"|"fallback"|"dry_run", "reason": ..., ...}
        """
        policy = self.get_policy(provider, model_pattern)
        if policy is None or not policy.enabled:
            return {"action": "none", "reason": "no_active_policy"}

        # 5a: Mode 守卫
        scope_state = self.get_scope_state(provider, model_pattern)
        mode = scope_state.mode if scope_state else "auto"
        if mode == "manual_locked":
            return {"action": "none", "reason": "manual_locked"}
        if mode == "frozen" or dry_run:
            dry_run = True  # frozen 模式强制 dry_run

        # 刷新快照以获取最新余额
        self._pool.refresh_snapshots()

        # 获取当前激活账号
        current = self._pool.resolve_active_account(provider, model_pattern)
        # 检查是否存在映射但账号不可用（depleted/disabled）
        _mapping = self._pool.get_manual_active(provider, model_pattern)
        _has_stale_mapping = _mapping is not None and current is None

        if current is None:
            if _has_stale_mapping and policy.fallback_to_default:
                # 映射指向不可用账号 → 尝试切换或回退
                _stale_id = _mapping.pool_account_id if _mapping else ""
                candidates = self._list_candidates(provider, exclude_id=_stale_id)
                if candidates:
                    best = self._select_best(candidates, provider, model_pattern)
                    if best:
                        if dry_run:
                            return {
                                "action": "dry_run", "reason": "stale_mapping_replaced",
                                "from": _stale_id, "to": best.id,
                            }
                        self.apply_rotation(
                            provider, model_pattern,
                            to_account_id=best.id,
                            from_account_id=_stale_id,
                            reason="stale_mapping_replaced",
                            trigger=trigger,
                        )
                        return {
                            "action": "rotate", "reason": "stale_mapping_replaced",
                            "from": _stale_id, "to": best.id,
                        }
                # 无候选 → 回退
                if dry_run:
                    return {
                        "action": "dry_run", "reason": "stale_mapping_no_candidates",
                        "from": _stale_id,
                    }
                self.clear_rotation(
                    provider, model_pattern,
                    from_account_id=_stale_id,
                    reason="stale_mapping_no_candidates",
                    trigger="fallback",
                )
                return {
                    "action": "fallback", "reason": "stale_mapping_no_candidates",
                    "from": _stale_id,
                }

            # 无映射 → 检查是否有可用候选可以激活
            candidates = self._list_candidates(provider, exclude_id=None)
            if candidates:
                best = self._select_best(candidates, provider, model_pattern)
                if best:
                    if dry_run:
                        return {
                            "action": "dry_run", "reason": "no_active_account_recovered",
                            "to": best.id,
                        }
                    self.apply_rotation(
                        provider, model_pattern,
                        to_account_id=best.id,
                        from_account_id="",
                        reason="no_active_account_recovered",
                        trigger=trigger,
                    )
                    return {
                        "action": "rotate", "reason": "no_active_account_recovered",
                        "to": best.id,
                    }
            return {"action": "none", "reason": "no_active_no_candidates"}

        snapshot = self._pool.get_snapshot(current.id)

        # 1. 硬触发检查
        hard_reason = self._check_hard_trigger(current, snapshot)
        if hard_reason:
            return await self._try_rotate(
                provider, model_pattern, current.id,
                reason=hard_reason, trigger="hard",
                skip_cooldown=True, policy=policy,
                dry_run=dry_run,
            )

        # 2. 软触发检查
        soft_reason = self._check_soft_trigger(current, snapshot, policy)
        if soft_reason:
            if self._is_in_cooldown(provider, model_pattern, policy.cooldown_seconds):
                return {"action": "none", "reason": "soft_trigger_in_cooldown"}

            # 5c: 最短驻留时间（软触发受限）
            if scope_state and scope_state.activated_at:
                try:
                    act_at = datetime.fromisoformat(scope_state.activated_at)
                    if act_at.tzinfo is None:
                        act_at = act_at.replace(tzinfo=timezone.utc)
                    elapsed = (datetime.now(tz=timezone.utc) - act_at).total_seconds()
                    if elapsed < policy.min_dwell_seconds:
                        return {"action": "none", "reason": "dwell_blocked"}
                except (ValueError, TypeError):
                    pass

            # 5b: 迟滞防抖（软触发受限）
            candidates = self._list_candidates(provider, exclude_id=current.id)
            if candidates:
                best = self._select_best(candidates, provider, model_pattern)
                if best:
                    best_score = self._score_candidate(
                        best, self._pool.get_snapshot(best.id),
                    )
                    current_score = scope_state.current_score if scope_state else 0.0
                    if best_score - current_score < policy.hysteresis_delta:
                        return {"action": "none", "reason": "hysteresis_blocked"}

            return await self._try_rotate(
                provider, model_pattern, current.id,
                reason=soft_reason, trigger="soft",
                skip_cooldown=False, policy=policy,
                dry_run=dry_run,
            )

        return {"action": "none", "reason": "no_trigger"}

    async def evaluate_on_error(
        self, provider: str, model_pattern: str = "*",
    ) -> dict[str, Any] | None:
        """错误即时评估（带锁防抖，最小间隔 5s）。"""
        import time
        now_mono = time.monotonic()
        if now_mono - self._last_eval_time < 5.0:
            return None  # 防抖
        async with self._eval_lock:
            # double-check
            now_mono = time.monotonic()
            if now_mono - self._last_eval_time < 5.0:
                return None
            self._last_eval_time = now_mono
            try:
                return await self.evaluate_scope(provider, model_pattern, trigger="error")
            except Exception:
                logger.debug("即时评估失败", exc_info=True)
                return None

    # ── 触发检查 ──────────────────────────────────────────────

    @staticmethod
    def _check_hard_trigger(
        account: Any, snapshot: Any,
    ) -> str:
        """硬触发：必须立即切换的条件。返回 reason 或空字符串。"""
        if account.health_signal == "depleted":
            return "health_depleted"
        if account.status == "depleted":
            return "status_depleted"
        if snapshot is not None:
            if snapshot.daily_remaining <= 0 and account.daily_budget_tokens > 0:
                return "daily_budget_exhausted"
            if snapshot.weekly_remaining <= 0 and account.weekly_budget_tokens > 0:
                return "weekly_budget_exhausted"
        return ""

    def _check_soft_trigger(
        self,
        account: Any,
        snapshot: Any,
        policy: PoolAutoPolicy,
    ) -> str:
        """软触发：余额低或错误频繁。返回 reason 或空字符串。"""
        # 余额比率低于水位线
        if snapshot is not None:
            budget_ratio = self._calc_budget_ratio(account, snapshot)
            if budget_ratio < policy.low_watermark:
                return f"low_budget_ratio:{budget_ratio:.2f}"

        # 429 错误计数
        rate_limit_count = self._count_recent_errors(
            account.id, policy.error_window_minutes, ("429",),
        )
        if rate_limit_count >= policy.rate_limit_threshold:
            return f"rate_limit_errors:{rate_limit_count}"

        # 5xx / transient 错误计数
        transient_count = self._count_recent_errors(
            account.id, policy.error_window_minutes,
            ("500", "502", "503", "504", "transient"),
        )
        if transient_count >= policy.transient_threshold:
            return f"transient_errors:{transient_count}"

        return ""

    # ── 候选筛选与评分 ────────────────────────────────────────

    def _list_candidates(
        self, provider: str, exclude_id: str | None,
    ) -> list[Any]:
        """列出所有可用候选（status=active, health!=depleted, 余额>0, 有凭证）。"""
        accounts = self._pool.list_accounts()
        candidates = []
        for acct in accounts:
            if acct.provider != provider:
                continue
            if acct.id == exclude_id:
                continue
            if acct.status != "active":
                continue
            if acct.health_signal == "depleted":
                continue
            snapshot = self._pool.get_snapshot(acct.id)
            if snapshot is not None:
                if acct.daily_budget_tokens > 0 and snapshot.daily_remaining <= 0:
                    continue
                if acct.weekly_budget_tokens > 0 and snapshot.weekly_remaining <= 0:
                    continue
            # 检查有可用凭证
            if not self._has_credential(acct.id, acct.provider):
                continue
            # 5e: 熔断器过滤（open 状态排除，half_open 允许作为探测）
            if self._breaker is not None and not self._breaker.is_available(acct.id):
                continue
            candidates.append(acct)
        return candidates

    def _has_credential(self, account_id: str, provider: str = "openai-codex") -> bool:
        """检查池账号是否有可用 OAuth 凭证。"""
        if self._pool._cred_store is None:
            return True  # 无 store 时假设可用（测试环境）
        from excelmanus.pool.service import POOL_USER_ID
        profile_name = self._pool.get_pool_profile_name(account_id)
        _get_by_name = getattr(self._pool._cred_store, "get_profile_by_name", None)
        if _get_by_name is not None:
            profile = _get_by_name(POOL_USER_ID, provider, profile_name)
        else:
            profile = self._pool._cred_store.get_active_profile(
                POOL_USER_ID, provider,
            )
            if profile is not None and profile.profile_name != profile_name:
                profile = None
        return profile is not None and bool(getattr(profile, "access_token", None))

    def _select_best(
        self,
        candidates: list[Any],
        provider: str,
        model_pattern: str,
    ) -> Any | None:
        """从候选列表中选出最优账号。"""
        if not candidates:
            return None

        scored: list[tuple[float, int, Any]] = []
        for acct in candidates:
            snapshot = self._pool.get_snapshot(acct.id)
            score = self._score_candidate(acct, snapshot)
            activation_count = self._count_activations(acct.id)
            scored.append((score, -activation_count, acct))

        # 按 score 降序、activation_count 升序（-count 降序 → 实际 count 升序）
        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
        return scored[0][2] if scored else None

    @staticmethod
    def _score_candidate(account: Any, snapshot: Any) -> float:
        """候选评分：0.7 * budget_ratio + 0.3 * health_score。"""
        health_score = _HEALTH_SCORES.get(account.health_signal, 0.5)

        if snapshot is None:
            budget_ratio = 1.0
        else:
            daily_ratio = (
                snapshot.daily_remaining / max(account.daily_budget_tokens, 1)
                if account.daily_budget_tokens > 0
                else 1.0
            )
            weekly_ratio = (
                snapshot.weekly_remaining / max(account.weekly_budget_tokens, 1)
                if account.weekly_budget_tokens > 0
                else 1.0
            )
            budget_ratio = min(daily_ratio, weekly_ratio)

        return 0.7 * budget_ratio + 0.3 * health_score

    @staticmethod
    def _calc_budget_ratio(account: Any, snapshot: Any) -> float:
        """计算当前账号的余额比率。"""
        if snapshot is None:
            return 1.0
        daily_ratio = (
            snapshot.daily_remaining / max(account.daily_budget_tokens, 1)
            if account.daily_budget_tokens > 0
            else 1.0
        )
        weekly_ratio = (
            snapshot.weekly_remaining / max(account.weekly_budget_tokens, 1)
            if account.weekly_budget_tokens > 0
            else 1.0
        )
        return min(daily_ratio, weekly_ratio)

    def _count_activations(self, account_id: str) -> int:
        """统计账号被激活的历史次数（防过热）。"""
        row = self._conn.execute(
            "SELECT COUNT(*) as cnt FROM pool_rotation_events WHERE to_account_id = ?",
            (account_id,),
        ).fetchone()
        if row is None:
            return 0
        d = _row_to_dict(row)
        return int(d.get("cnt", 0) or 0)

    # ── 轮换执行 ──────────────────────────────────────────────

    async def _try_rotate(
        self,
        provider: str,
        model_pattern: str,
        from_account_id: str,
        reason: str,
        trigger: str,
        skip_cooldown: bool,
        policy: PoolAutoPolicy,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """尝试轮换：找候选 → 切换或回退。"""
        candidates = self._list_candidates(provider, exclude_id=from_account_id)
        if candidates:
            best = self._select_best(candidates, provider, model_pattern)
            if best:
                if dry_run:
                    return {
                        "action": "dry_run", "reason": reason,
                        "from": from_account_id, "to": best.id,
                    }
                self.apply_rotation(
                    provider, model_pattern,
                    to_account_id=best.id,
                    from_account_id=from_account_id,
                    reason=reason,
                    trigger=trigger,
                )
                logger.info(
                    "号池自动轮换: %s/%s  %s → %s  reason=%s trigger=%s",
                    provider, model_pattern, from_account_id, best.id, reason, trigger,
                )
                return {
                    "action": "rotate", "reason": reason,
                    "from": from_account_id, "to": best.id,
                }

        # 无可用候选 → 回退
        if policy.fallback_to_default:
            if dry_run:
                return {
                    "action": "dry_run", "reason": reason,
                    "from": from_account_id, "would_fallback": True,
                }
            self.clear_rotation(
                provider, model_pattern,
                from_account_id=from_account_id,
                reason=reason,
                trigger="fallback",
            )
            logger.warning(
                "号池无可用候选，回退原链路: %s/%s  reason=%s",
                provider, model_pattern, reason,
            )
            return {
                "action": "fallback", "reason": reason,
                "from": from_account_id,
            }

        return {"action": "none", "reason": "no_candidates_no_fallback"}

    def apply_rotation(
        self,
        provider: str,
        model_pattern: str,
        to_account_id: str,
        from_account_id: str = "",
        reason: str = "",
        trigger: str = "hard",
    ) -> None:
        """写入激活映射 + 记录审计事件 + 更新 scope state。"""
        self._pool.set_manual_active(
            provider, model_pattern, to_account_id,
            activated_by="auto_rotate",
        )
        self.record_event(
            provider=provider, model_pattern=model_pattern,
            from_account_id=from_account_id, to_account_id=to_account_id,
            reason=reason, trigger=trigger, fallback_used=False,
        )
        # 5d: 维护 scope state
        now = _now_iso()
        to_acct = self._pool.get_account(to_account_id)
        to_snapshot = self._pool.get_snapshot(to_account_id) if to_acct else None
        score = self._score_candidate(to_acct, to_snapshot) if to_acct else 0.0
        self.upsert_scope_state(
            provider, model_pattern,
            current_account_id=to_account_id,
            current_score=score,
            activated_at=now,
            last_rotation_at=now,
        )

    def clear_rotation(
        self,
        provider: str,
        model_pattern: str,
        from_account_id: str = "",
        reason: str = "",
        trigger: str = "fallback",
    ) -> None:
        """清空激活映射（回退原链路）+ 记录审计事件 + 清空 scope state。"""
        self._conn.execute(
            "DELETE FROM pool_manual_active WHERE provider = ? AND model_pattern = ?",
            (provider, model_pattern),
        )
        self._conn.commit()
        self.record_event(
            provider=provider, model_pattern=model_pattern,
            from_account_id=from_account_id, to_account_id="",
            reason=reason, trigger=trigger, fallback_used=True,
        )
        # 5d: 清空 scope state 的 current_account_id + current_score
        self.upsert_scope_state(
            provider, model_pattern,
            current_account_id="",
            current_score=0.0,
            last_rotation_at=_now_iso(),
        )

    # ── Scope State CRUD ─────────────────────────────────────────

    def get_scope_state(
        self, provider: str, model_pattern: str = "*",
    ) -> PoolScopeState | None:
        """读取 scope 状态。"""
        row = self._conn.execute(
            "SELECT * FROM pool_scope_state WHERE provider = ? AND model_pattern = ?",
            (provider, model_pattern),
        ).fetchone()
        if row is None:
            return None
        d = _row_to_dict(row)
        return PoolScopeState(
            provider=d.get("provider", provider),
            model_pattern=d.get("model_pattern", model_pattern),
            mode=d.get("mode", "auto") or "auto",
            current_account_id=d.get("current_account_id", "") or "",
            current_score=float(d.get("current_score", 0.0) or 0.0),
            activated_at=d.get("activated_at", "") or "",
            cooldown_until=d.get("cooldown_until", "") or "",
            last_rotation_at=d.get("last_rotation_at", "") or "",
            updated_at=d.get("updated_at", "") or "",
        )

    def upsert_scope_state(
        self,
        provider: str,
        model_pattern: str = "*",
        *,
        current_account_id: str | None = None,
        current_score: float | None = None,
        activated_at: str | None = None,
        cooldown_until: str | None = None,
        last_rotation_at: str | None = None,
        mode: str | None = None,
    ) -> PoolScopeState:
        """创建或更新 scope 状态（仅更新提供的字段）。"""
        existing = self.get_scope_state(provider, model_pattern)
        now = _now_iso()
        state = existing or PoolScopeState(
            provider=provider, model_pattern=model_pattern, updated_at=now,
        )
        if current_account_id is not None:
            state.current_account_id = current_account_id
        if current_score is not None:
            state.current_score = current_score
        if activated_at is not None:
            state.activated_at = activated_at
        if cooldown_until is not None:
            state.cooldown_until = cooldown_until
        if last_rotation_at is not None:
            state.last_rotation_at = last_rotation_at
        if mode is not None:
            state.mode = mode
        state.updated_at = now

        self._conn.execute(
            "DELETE FROM pool_scope_state WHERE provider = ? AND model_pattern = ?",
            (provider, model_pattern),
        )
        self._conn.execute(
            """INSERT INTO pool_scope_state
               (provider, model_pattern, mode, current_account_id, current_score,
                activated_at, cooldown_until, last_rotation_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                state.provider, state.model_pattern, state.mode,
                state.current_account_id, state.current_score,
                state.activated_at, state.cooldown_until,
                state.last_rotation_at, state.updated_at,
            ),
        )
        self._conn.commit()
        return state

    def set_scope_mode(
        self,
        provider: str,
        model_pattern: str = "*",
        mode: str = "auto",
    ) -> PoolScopeState:
        """切换 scope 模式。"""
        if mode not in ("auto", "manual_locked", "frozen"):
            raise ValueError(f"无效的 mode: {mode}")
        return self.upsert_scope_state(provider, model_pattern, mode=mode)

    # ── 审计事件 ──────────────────────────────────────────────

    def record_event(
        self,
        *,
        provider: str,
        model_pattern: str = "*",
        from_account_id: str = "",
        to_account_id: str = "",
        reason: str = "",
        trigger: str = "hard",
        fallback_used: bool = False,
    ) -> None:
        """记录轮换审计事件。"""
        try:
            self._conn.execute(
                """INSERT INTO pool_rotation_events
                   (provider, model_pattern, from_account_id, to_account_id,
                    reason, trigger, fallback_used, created_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    provider, model_pattern, from_account_id, to_account_id,
                    reason, trigger, int(fallback_used), _now_iso(),
                ),
            )
            self._conn.commit()
        except Exception:
            logger.debug("记录轮换事件失败", exc_info=True)

    def list_events(
        self,
        limit: int = 50,
        provider: str | None = None,
        model_pattern: str | None = None,
    ) -> list[PoolRotationEvent]:
        """查询轮换审计事件。"""
        sql = "SELECT * FROM pool_rotation_events"
        params: list[Any] = []
        conditions: list[str] = []
        if provider is not None:
            conditions.append("provider = ?")
            params.append(provider)
        if model_pattern is not None:
            conditions.append("model_pattern = ?")
            params.append(model_pattern)
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_event(r) for r in rows]

    # ── 内部工具 ──────────────────────────────────────────────

    def _count_recent_errors(
        self, account_id: str, window_minutes: int, error_codes: tuple[str, ...],
    ) -> int:
        """统计时间窗口内指定错误码的数量。"""
        since = (
            datetime.now(tz=timezone.utc) - timedelta(minutes=window_minutes)
        ).isoformat()
        placeholders = ",".join("?" for _ in error_codes)
        row = self._conn.execute(
            f"SELECT COUNT(*) as cnt FROM pool_usage_ledger "
            f"WHERE pool_account_id = ? AND created_at >= ? "
            f"AND outcome = 'error' AND error_code IN ({placeholders})",
            (account_id, since, *error_codes),
        ).fetchone()
        if row is None:
            return 0
        d = _row_to_dict(row)
        return int(d.get("cnt", 0) or 0)

    def _is_in_cooldown(
        self, provider: str, model_pattern: str, cooldown_seconds: int,
    ) -> bool:
        """检查是否在冷却期内。"""
        row = self._conn.execute(
            "SELECT created_at FROM pool_rotation_events "
            "WHERE provider = ? AND model_pattern = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (provider, model_pattern),
        ).fetchone()
        if row is None:
            return False
        d = _row_to_dict(row)
        last_at_str = d.get("created_at", "")
        if not last_at_str:
            return False
        try:
            last_at = datetime.fromisoformat(last_at_str)
            if last_at.tzinfo is None:
                last_at = last_at.replace(tzinfo=timezone.utc)
            elapsed = (datetime.now(tz=timezone.utc) - last_at).total_seconds()
            return elapsed < cooldown_seconds
        except (ValueError, TypeError):
            return False

    @staticmethod
    def _row_to_policy(row: Any) -> PoolAutoPolicy:
        d = _row_to_dict(row)
        return PoolAutoPolicy(
            id=d["id"],
            provider=d.get("provider", "openai-codex"),
            model_pattern=d.get("model_pattern", "*"),
            enabled=bool(int(d["enabled"])) if d.get("enabled") is not None else True,
            low_watermark=float(d["low_watermark"]) if d.get("low_watermark") is not None else 0.15,
            rate_limit_threshold=int(d["rate_limit_threshold"]) if d.get("rate_limit_threshold") is not None else 3,
            transient_threshold=int(d["transient_threshold"]) if d.get("transient_threshold") is not None else 5,
            error_window_minutes=int(d["error_window_minutes"]) if d.get("error_window_minutes") is not None else 5,
            cooldown_seconds=int(d["cooldown_seconds"]) if d.get("cooldown_seconds") is not None else 300,
            fallback_to_default=bool(int(d["fallback_to_default"])) if d.get("fallback_to_default") is not None else True,
            hysteresis_delta=float(d["hysteresis_delta"]) if d.get("hysteresis_delta") is not None else 0.12,
            min_dwell_seconds=int(d["min_dwell_seconds"]) if d.get("min_dwell_seconds") is not None else 180,
            breaker_open_seconds=int(d["breaker_open_seconds"]) if d.get("breaker_open_seconds") is not None else 120,
            created_at=d.get("created_at", ""),
            updated_at=d.get("updated_at", ""),
        )

    @staticmethod
    def _row_to_event(row: Any) -> PoolRotationEvent:
        d = _row_to_dict(row)
        return PoolRotationEvent(
            id=int(d.get("id", 0) or 0),
            provider=d.get("provider", ""),
            model_pattern=d.get("model_pattern", "*"),
            from_account_id=d.get("from_account_id", ""),
            to_account_id=d.get("to_account_id", ""),
            reason=d.get("reason", ""),
            trigger=d.get("trigger", "hard"),
            fallback_used=bool(int(d.get("fallback_used", 0) or 0)),
            created_at=d.get("created_at", ""),
        )
