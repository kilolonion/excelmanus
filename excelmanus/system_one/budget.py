"""Per-turn budget for Jev decision-plane calls.

Jev is an auxiliary decision plane. It must not turn a long Agent turn into an
unbounded series of remote calls, while security packs still get a chance to
fail closed when the optimization budget is exhausted.
"""

from __future__ import annotations

import time
import math
from dataclasses import dataclass, field
from typing import Any


DEFAULT_MAX_EVALUATIONS = 8
DEFAULT_MAX_LATENCY_MS = 3_000.0


@dataclass
class JevTurnBudget:
    max_evaluations: int = DEFAULT_MAX_EVALUATIONS
    max_latency_ms: float = DEFAULT_MAX_LATENCY_MS
    started_at: float = field(default_factory=time.monotonic)
    evaluations: int = 0
    spent_latency_ms: float = 0.0
    reserved_latency_ms: float = 0.0
    exhausted_reason: str = ""
    security_evaluations: int = 0
    security_latency_ms: float = 0.0

    def available_latency_ms(self, *, keep_latency_ms: float = 0.0) -> float:
        return max(0.0, self.max_latency_ms - self.spent_latency_ms - self.reserved_latency_ms - keep_latency_ms)

    def reserve(
        self, *, security: bool = False, latency_ms: float = 0.0,
        keep_evaluations: int = 0, keep_latency_ms: float = 0.0,
    ) -> bool:
        """Reserve one evaluation.

        Security packs bypass the optimization budget so a busy turn cannot
        accidentally turn an approval check into an implicit allow.
        """
        if security:
            self.security_evaluations += 1
            return True
        if self.evaluations >= max(0, int(self.max_evaluations) - keep_evaluations):
            self.exhausted_reason = "evaluation_limit"
            return False
        # Main-model and tool latency must not consume Jev's own allowance.
        available = self.available_latency_ms(keep_latency_ms=keep_latency_ms)
        if available <= 0 or latency_ms > available:
            self.exhausted_reason = "latency_limit"
            return False
        self.evaluations += 1
        self.reserved_latency_ms += max(0.0, latency_ms)
        self.exhausted_reason = ""
        return True

    def record(self, latency_ms: float, *, reserved_ms: float = 0.0, security: bool = False) -> None:
        self.reserved_latency_ms = max(0.0, self.reserved_latency_ms - reserved_ms)
        try:
            elapsed = float(latency_ms or 0.0)
            if math.isfinite(elapsed):
                if security:
                    self.security_latency_ms += max(0.0, elapsed)
                else:
                    self.spent_latency_ms += max(0.0, elapsed)
        except (TypeError, ValueError):
            return

    def snapshot(self) -> dict[str, Any]:
        return {
            "max_evaluations": self.max_evaluations,
            "max_latency_ms": self.max_latency_ms,
            "evaluations": self.evaluations,
            "spent_latency_ms": round(self.spent_latency_ms, 1),
            "reserved_latency_ms": round(self.reserved_latency_ms, 1),
            "exhausted_reason": self.exhausted_reason,
            "security_evaluations": self.security_evaluations,
            "security_latency_ms": round(self.security_latency_ms, 1),
        }


def turn_budget(engine: Any) -> JevTurnBudget:
    budget = getattr(engine, "_jev_turn_budget", None)
    if isinstance(budget, JevTurnBudget):
        return budget
    budget = JevTurnBudget()
    try:
        engine._jev_turn_budget = budget
    except Exception:
        pass
    return budget


def reset_turn_budget(engine: Any) -> JevTurnBudget:
    budget = JevTurnBudget()
    try:
        engine._jev_turn_budget = budget
    except Exception:
        pass
    return budget
