"""号池数据模型。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class PoolAccount:
    """池账号记录。"""

    id: str
    label: str = ""
    provider: str = "openai-codex"
    account_id: str = ""
    plan_type: str = ""
    status: str = "active"  # active / disabled / depleted
    daily_budget_tokens: int = 0
    weekly_budget_tokens: int = 0
    timezone: str = "Asia/Shanghai"
    health_signal: str = "ok"  # ok / depleted / rate_limited / transient
    health_confidence: float = 0.0
    health_updated_at: str = ""
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "provider": self.provider,
            "account_id": self.account_id,
            "plan_type": self.plan_type,
            "status": self.status,
            "daily_budget_tokens": self.daily_budget_tokens,
            "weekly_budget_tokens": self.weekly_budget_tokens,
            "timezone": self.timezone,
            "health_signal": self.health_signal,
            "health_confidence": self.health_confidence,
            "health_updated_at": self.health_updated_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class PoolBudgetSnapshot:
    """预算快照。"""

    pool_account_id: str
    day_window_tokens: int = 0
    week_window_tokens: int = 0
    daily_remaining: int = 0
    weekly_remaining: int = 0
    snapshot_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "pool_account_id": self.pool_account_id,
            "day_window_tokens": self.day_window_tokens,
            "week_window_tokens": self.week_window_tokens,
            "daily_remaining": self.daily_remaining,
            "weekly_remaining": self.weekly_remaining,
            "snapshot_at": self.snapshot_at,
        }


@dataclass
class PoolManualActive:
    """人工激活映射。"""

    provider: str
    model_pattern: str = "*"
    pool_account_id: str = ""
    activated_by: str = ""
    activated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model_pattern": self.model_pattern,
            "pool_account_id": self.pool_account_id,
            "activated_by": self.activated_by,
            "activated_at": self.activated_at,
        }


@dataclass
class PoolAutoPolicy:
    """自动轮换策略。"""

    id: str
    provider: str = "openai-codex"
    model_pattern: str = "*"
    enabled: bool = True
    low_watermark: float = 0.15
    rate_limit_threshold: int = 3
    transient_threshold: int = 5
    error_window_minutes: int = 5
    cooldown_seconds: int = 300
    fallback_to_default: bool = True
    hysteresis_delta: float = 0.12
    min_dwell_seconds: int = 180
    breaker_open_seconds: int = 120
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "provider": self.provider,
            "model_pattern": self.model_pattern,
            "enabled": self.enabled,
            "low_watermark": self.low_watermark,
            "rate_limit_threshold": self.rate_limit_threshold,
            "transient_threshold": self.transient_threshold,
            "error_window_minutes": self.error_window_minutes,
            "cooldown_seconds": self.cooldown_seconds,
            "fallback_to_default": self.fallback_to_default,
            "hysteresis_delta": self.hysteresis_delta,
            "min_dwell_seconds": self.min_dwell_seconds,
            "breaker_open_seconds": self.breaker_open_seconds,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class PoolRotationEvent:
    """轮换审计事件。"""

    id: int = 0
    provider: str = "openai-codex"
    model_pattern: str = "*"
    from_account_id: str = ""
    to_account_id: str = ""
    reason: str = ""
    trigger: str = "hard"  # hard / soft / manual / fallback
    fallback_used: bool = False
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "provider": self.provider,
            "model_pattern": self.model_pattern,
            "from_account_id": self.from_account_id,
            "to_account_id": self.to_account_id,
            "reason": self.reason,
            "trigger": self.trigger,
            "fallback_used": self.fallback_used,
            "created_at": self.created_at,
        }


@dataclass
class PoolScopeState:
    """Scope 当前运行状态。"""

    provider: str = "openai-codex"
    model_pattern: str = "*"
    mode: str = "auto"  # auto / manual_locked / frozen
    current_account_id: str = ""
    current_score: float = 0.0
    activated_at: str = ""
    cooldown_until: str = ""
    last_rotation_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model_pattern": self.model_pattern,
            "mode": self.mode,
            "current_account_id": self.current_account_id,
            "current_score": self.current_score,
            "activated_at": self.activated_at,
            "cooldown_until": self.cooldown_until,
            "last_rotation_at": self.last_rotation_at,
            "updated_at": self.updated_at,
        }


@dataclass
class PoolAccountBreaker:
    """账号熔断状态。"""

    pool_account_id: str = ""
    consecutive_failures: int = 0
    breaker_state: str = "closed"  # closed / open / half_open
    open_until: str = ""
    last_failure_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "pool_account_id": self.pool_account_id,
            "consecutive_failures": self.consecutive_failures,
            "breaker_state": self.breaker_state,
            "open_until": self.open_until,
            "last_failure_at": self.last_failure_at,
            "updated_at": self.updated_at,
        }


@dataclass
class PoolRotationMetric:
    """分钟级指标聚合。"""

    minute_bucket: str = ""
    provider: str = "openai-codex"
    model_pattern: str = "*"
    total_requests: int = 0
    success_count: int = 0
    error_429: int = 0
    error_5xx: int = 0
    rotations: int = 0
    fallbacks: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "minute_bucket": self.minute_bucket,
            "provider": self.provider,
            "model_pattern": self.model_pattern,
            "total_requests": self.total_requests,
            "success_count": self.success_count,
            "error_429": self.error_429,
            "error_5xx": self.error_5xx,
            "rotations": self.rotations,
            "fallbacks": self.fallbacks,
        }


@dataclass
class PoolAccountSummary:
    """号池总览中的单账号摘要（含预算快照）。"""

    account: PoolAccount
    snapshot: PoolBudgetSnapshot | None = None
    is_active: bool = False  # 是否为当前激活账号

    def to_dict(self) -> dict[str, Any]:
        d = self.account.to_dict()
        if self.snapshot:
            d["budget"] = self.snapshot.to_dict()
        else:
            d["budget"] = None
        d["is_active"] = self.is_active
        return d
