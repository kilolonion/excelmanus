"""窗口生命周期决策接口与规则实现。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from .advisor_context import AdvisorContext
from .domain import Window
from .models import IntentTag, PerceptionBudget

WindowTier = Literal["active", "background", "suspended", "terminated"]
_VALID_TASK_TYPES = {
    "DATA_COMPARISON",
    "FORMAT_CHECK",
    "FORMULA_DEBUG",
    "DATA_ENTRY",
    "ANOMALY_SEARCH",
    "GENERAL_BROWSE",
}
_VALID_TIERS: set[str] = {"active", "background", "suspended", "terminated"}


@dataclass
class WindowAdvice:
    """单个窗口的生命周期建议。"""

    window_id: str
    tier: WindowTier
    reason: str = ""
    reason_code: str = ""
    custom_summary: str | None = None


@dataclass
class LifecyclePlan:
    """一轮窗口渲染计划。"""

    advices: list[WindowAdvice]
    source: Literal["rules", "small_model", "hybrid"] = "rules"
    task_type: str = "GENERAL_BROWSE"
    generated_turn: int = 0


class WindowLifecycleAdvisor(Protocol):
    """窗口生命周期顾问协议。"""

    def advise(
        self,
        *,
        windows: list[Window],
        active_window_id: str | None,
        budget: PerceptionBudget,
        context: AdvisorContext,
        small_model_plan: LifecyclePlan | None = None,
        plan_ttl_turns: int = 2,
    ) -> LifecyclePlan:
        """输出窗口生命周期计划。"""


class RuleBasedAdvisor:
    """基于空闲轮次的确定性生命周期降级。"""

    def advise(
        self,
        *,
        windows: list[Window],
        active_window_id: str | None,
        budget: PerceptionBudget,
        context: AdvisorContext,
        small_model_plan: LifecyclePlan | None = None,
        plan_ttl_turns: int = 2,
    ) -> LifecyclePlan:
        del small_model_plan, plan_ttl_turns

        bg_after, suspend_after, terminate_after = self._normalize_thresholds(budget)
        advices: list[WindowAdvice] = []

        for window in windows:
            reason_code = ""
            if window.id == active_window_id:
                tier = "active"
                reason_code = "active_window"
            elif window.idle_turns < bg_after:
                tier: WindowTier = "active"
                reason_code = "idle_active"
            elif window.idle_turns < suspend_after:
                tier = "background"
                reason_code = "idle_background"
            elif window.idle_turns < terminate_after:
                tier = "suspended"
                reason_code = "idle_suspended"
            else:
                tier = "terminated"
                reason_code = "idle_terminated"

            promoted_tier = self._promote_tier_for_intent(
                tier=tier,
                intent_tag=window.intent_tag,
            )
            if promoted_tier != tier:
                tier = promoted_tier
                reason_code = f"priority_promote_{window.intent_tag.value}"

            advices.append(
                WindowAdvice(
                    window_id=window.id,
                    tier=tier,
                    reason=f"idle={window.idle_turns}",
                    reason_code=reason_code,
                )
            )

        return LifecyclePlan(
            advices=advices,
            source="rules",
            task_type=context.task_type or "GENERAL_BROWSE",
            generated_turn=context.turn_number,
        )

    @staticmethod
    def _normalize_thresholds(budget: PerceptionBudget) -> tuple[int, int, int]:
        background_after = max(1, int(budget.background_after_idle))
        suspend_after = max(background_after + 1, int(budget.suspend_after_idle))
        terminate_after = max(suspend_after + 1, int(budget.terminate_after_idle))
        return background_after, suspend_after, terminate_after

    @staticmethod
    def _promote_tier_for_intent(*, tier: WindowTier, intent_tag: IntentTag) -> WindowTier:
        """高优先意图在生命周期上前移一档，减少过早回收。"""
        if intent_tag not in {IntentTag.VALIDATE, IntentTag.FORMULA}:
            return tier
        promote_map: dict[WindowTier, WindowTier] = {
            "terminated": "suspended",
            "suspended": "background",
            "background": "active",
            "active": "active",
        }
        return promote_map.get(tier, tier)


class HybridAdvisor:
    """规则顾问基线 + 小模型计划覆盖。"""

    def __init__(self, rule_advisor: WindowLifecycleAdvisor | None = None) -> None:
        self._rule_advisor = rule_advisor or RuleBasedAdvisor()

    def advise(
        self,
        *,
        windows: list[Window],
        active_window_id: str | None,
        budget: PerceptionBudget,
        context: AdvisorContext,
        small_model_plan: LifecyclePlan | None = None,
        plan_ttl_turns: int = 2,
    ) -> LifecyclePlan:
        base_plan = self._rule_advisor.advise(
            windows=windows,
            active_window_id=active_window_id,
            budget=budget,
            context=context,
        )
        if small_model_plan is None:
            return base_plan
        if not self._is_plan_fresh(
            plan=small_model_plan,
            current_turn=context.turn_number,
            plan_ttl_turns=plan_ttl_turns,
        ):
            return base_plan
        if not self._is_valid_task_type(small_model_plan.task_type):
            return base_plan
        if not small_model_plan.advices:
            return base_plan

        known_window_ids = {window.id for window in windows}
        merged: dict[str, WindowAdvice] = {
            advice.window_id: WindowAdvice(
                window_id=advice.window_id,
                tier=advice.tier,
                reason=advice.reason,
                reason_code=advice.reason_code,
                custom_summary=advice.custom_summary,
            )
            for advice in base_plan.advices
        }

        applied = 0
        for advice in small_model_plan.advices:
            if advice.window_id not in known_window_ids:
                continue
            if advice.tier not in _VALID_TIERS:
                continue
            merged[advice.window_id] = WindowAdvice(
                window_id=advice.window_id,
                tier=advice.tier,
                reason=advice.reason,
                reason_code=advice.reason_code or "small_model_override",
                custom_summary=advice.custom_summary,
            )
            applied += 1

        if applied == 0:
            # small_model_plan 未成功覆盖任何已知窗口时，merged 与 base_plan 等价。
            # 此时 active window 已由 RuleBasedAdvisor 保证为 active，无需额外强制。
            return base_plan

        if active_window_id and active_window_id in merged:
            active_advice = merged[active_window_id]
            merged[active_window_id] = WindowAdvice(
                window_id=active_window_id,
                tier="active",
                reason=active_advice.reason or "active_window_forced",
                reason_code=active_advice.reason_code or "active_window_forced",
                custom_summary=active_advice.custom_summary,
            )

        ordered_advices = [merged[advice.window_id] for advice in base_plan.advices]
        return LifecyclePlan(
            advices=ordered_advices,
            source="hybrid",
            task_type=small_model_plan.task_type,
            generated_turn=context.turn_number,
        )

    @staticmethod
    def _is_valid_task_type(task_type: str) -> bool:
        return (task_type or "").strip() in _VALID_TASK_TYPES

    @staticmethod
    def _is_plan_fresh(
        *,
        plan: LifecyclePlan,
        current_turn: int,
        plan_ttl_turns: int,
    ) -> bool:
        ttl = max(0, int(plan_ttl_turns))
        generated_turn = int(plan.generated_turn)
        if generated_turn <= 0:
            return False
        return current_turn - generated_turn <= ttl
