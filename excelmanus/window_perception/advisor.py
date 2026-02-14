"""窗口生命周期决策接口与规则实现。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from .advisor_context import AdvisorContext
from .models import PerceptionBudget, WindowState

WindowTier = Literal["active", "background", "suspended", "terminated"]


@dataclass
class WindowAdvice:
    """单个窗口的生命周期建议。"""

    window_id: str
    tier: WindowTier
    reason: str = ""
    custom_summary: str | None = None


@dataclass
class LifecyclePlan:
    """一轮窗口渲染计划。"""

    advices: list[WindowAdvice]
    source: Literal["rules", "small_model", "hybrid"] = "rules"


class WindowLifecycleAdvisor(Protocol):
    """窗口生命周期顾问协议。"""

    def advise(
        self,
        *,
        windows: list[WindowState],
        active_window_id: str | None,
        budget: PerceptionBudget,
        context: AdvisorContext,
    ) -> LifecyclePlan:
        """输出窗口生命周期计划。"""


class RuleBasedAdvisor:
    """基于空闲轮次的确定性生命周期降级。"""

    def advise(
        self,
        *,
        windows: list[WindowState],
        active_window_id: str | None,
        budget: PerceptionBudget,
        context: AdvisorContext,
    ) -> LifecyclePlan:
        del context

        bg_after, suspend_after, terminate_after = self._normalize_thresholds(budget)
        advices: list[WindowAdvice] = []

        for window in windows:
            if window.id == active_window_id or window.idle_turns < bg_after:
                tier: WindowTier = "active"
            elif window.idle_turns < suspend_after:
                tier = "background"
            elif window.idle_turns < terminate_after:
                tier = "suspended"
            else:
                tier = "terminated"

            advices.append(
                WindowAdvice(
                    window_id=window.id,
                    tier=tier,
                    reason=f"idle={window.idle_turns}",
                )
            )

        return LifecyclePlan(advices=advices, source="rules")

    @staticmethod
    def _normalize_thresholds(budget: PerceptionBudget) -> tuple[int, int, int]:
        background_after = max(1, int(budget.background_after_idle))
        suspend_after = max(background_after + 1, int(budget.suspend_after_idle))
        terminate_after = max(suspend_after + 1, int(budget.terminate_after_idle))
        return background_after, suspend_after, terminate_after

