"""Shared execution budget for one Agent turn.

The budget is deliberately small and process-local.  A child agent receives the
same object as its parent, so LLM usage and nested tool calls cannot silently
escape the parent turn's limits.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


class TurnBudgetExceeded(RuntimeError):
    """Raised when a shared turn budget can no longer admit work."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


def _number(value: Any) -> float:
    try:
        return max(0.0, float(value or 0))
    except (TypeError, ValueError):
        return 0.0


@dataclass
class TurnBudget:
    """Mutable counters shared by the parent turn and nested executions."""

    deadline_mono: float | None = None
    max_tokens: int = 0
    max_cost_usd: float = 0.0
    input_cost_per_1k_usd: float = 0.0
    output_cost_per_1k_usd: float = 0.0
    started_at: float = field(default_factory=time.monotonic)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    tool_calls: int = 0
    cost_usd: float = 0.0
    exhausted_reason: str = ""

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def remaining_seconds(self) -> float | None:
        if self.deadline_mono is None:
            return None
        return max(0.0, self.deadline_mono - time.monotonic())

    def ensure_time(self) -> None:
        remaining = self.remaining_seconds()
        if remaining is not None and remaining <= 0:
            self.exhausted_reason = self.exhausted_reason or "wall_clock"
            raise TurnBudgetExceeded("wall_clock", "本轮 wall-clock 预算已耗尽")

    def reserve_tool(self) -> None:
        self.ensure_time()
        self.tool_calls += 1

    def record_usage(self, usage: Any) -> None:
        prompt = int(_number(_usage_value(usage, "prompt_tokens")))
        completion = int(_number(_usage_value(usage, "completion_tokens")))
        explicit_cost = _number(
            _usage_value(usage, "cost_usd")
            or _usage_value(usage, "total_cost_usd")
            or _usage_value(usage, "cost")
        )
        self.prompt_tokens += prompt
        self.completion_tokens += completion
        self.cost_usd += explicit_cost
        if not explicit_cost and (prompt or completion):
            self.cost_usd += (
                prompt / 1000.0 * max(0.0, self.input_cost_per_1k_usd)
                + completion / 1000.0 * max(0.0, self.output_cost_per_1k_usd)
            )
        if self.max_tokens > 0 and self.total_tokens >= self.max_tokens:
            self.exhausted_reason = self.exhausted_reason or "tokens"
        if self.max_cost_usd > 0 and self.cost_usd >= self.max_cost_usd:
            self.exhausted_reason = self.exhausted_reason or "cost"

    def exhausted(self) -> bool:
        self.ensure_time()
        return bool(self.exhausted_reason)

    def snapshot(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at,
            "deadline_mono": self.deadline_mono,
            "remaining_seconds": self.remaining_seconds(),
            "max_tokens": self.max_tokens,
            "max_cost_usd": self.max_cost_usd,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "tool_calls": self.tool_calls,
            "cost_usd": round(self.cost_usd, 8),
            "exhausted_reason": self.exhausted_reason,
        }


def _usage_value(usage: Any, key: str) -> Any:
    if isinstance(usage, dict):
        return usage.get(key)
    return getattr(usage, key, None)

