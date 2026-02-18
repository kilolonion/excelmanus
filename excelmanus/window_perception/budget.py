"""窗口感知层预算分配器。"""

from __future__ import annotations

from collections.abc import Callable

from excelmanus.memory import TokenCounter

from .advisor import LifecyclePlan, WindowTier
from .domain import Window
from .models import DetailLevel, PerceptionBudget, WindowRenderAction, WindowSnapshot


class WindowBudgetAllocator:
    """在 token 预算内选择窗口渲染动作。"""

    def __init__(self, budget: PerceptionBudget) -> None:
        self._budget = budget

    def compute_window_full_max_rows(self, active_window_count: int) -> int:
        """根据 ACTIVE 窗口数量动态分配 FULL 行数。"""
        if active_window_count <= 1:
            return max(1, min(50, int(self._budget.window_full_max_rows) * 2))
        if active_window_count == 2:
            return max(1, int(self._budget.window_full_max_rows))
        return max(1, min(15, int(self._budget.window_full_max_rows)))

    def allocate(
        self,
        *,
        windows: list[Window],
        active_window_id: str | None,
        render_keep: Callable[[Window], str],
        render_minimized: Callable[[Window], str],
        render_background: Callable[[Window], str] | None = None,
        lifecycle_plan: LifecyclePlan | None = None,
    ) -> list[WindowSnapshot]:
        """分配窗口动作并返回快照。"""
        if not windows:
            return []

        ordered = sorted(
            windows,
            key=lambda item: (
                0 if item.id == active_window_id else 1,
                -item.last_access_seq,
            ),
        )
        max_windows = max(1, self._budget.max_windows)
        candidates = ordered[:max_windows]
        overflow = ordered[max_windows:]

        advice_by_id: dict[str, WindowTier] = {}
        summary_by_id: dict[str, str] = {}
        if lifecycle_plan is not None:
            for advice in lifecycle_plan.advices:
                advice_by_id[advice.window_id] = advice.tier
                if advice.custom_summary:
                    summary_by_id[advice.window_id] = advice.custom_summary

        remaining = max(0, self._budget.system_budget_tokens)
        snapshots: list[WindowSnapshot] = []
        render_background = render_background or render_minimized

        for item in candidates:
            if item.id in summary_by_id:
                item.summary = summary_by_id[item.id]

            desired_tier = advice_by_id.get(item.id)
            if desired_tier is None:
                desired_tier = self._tier_from_idle(item, active_window_id)
            if item.id == active_window_id:
                desired_tier = "active"

            snapshot = self._allocate_single(
                item=item,
                desired_tier=desired_tier,
                remaining=remaining,
                render_keep=render_keep,
                render_background=render_background,
                render_minimized=render_minimized,
                must_keep=item.id == active_window_id,
            )
            if snapshot.action != WindowRenderAction.CLOSE:
                remaining -= snapshot.estimated_tokens
            snapshots.append(snapshot)

        for item in overflow:
            snapshots.append(
                WindowSnapshot(
                    window_id=item.id,
                    action=WindowRenderAction.CLOSE,
                    rendered_text="",
                    estimated_tokens=0,
                )
            )

        return snapshots

    def _allocate_single(
        self,
        *,
        item: Window,
        desired_tier: WindowTier,
        remaining: int,
        render_keep: Callable[[Window], str],
        render_background: Callable[[Window], str],
        render_minimized: Callable[[Window], str],
        must_keep: bool,
    ) -> WindowSnapshot:
        active_text = render_keep(item)
        active_tokens = self._estimate_tokens(active_text)

        background_text = render_background(item)
        background_tokens = self._estimate_tokens(background_text)

        suspended_text = render_minimized(item)
        suspended_tokens = self._estimate_tokens(suspended_text)

        for tier in self._fallback_chain(desired_tier):
            if tier == "terminated":
                break

            if tier == "active" and active_tokens > 0 and active_tokens <= remaining:
                item.detail_level = DetailLevel.FULL
                return WindowSnapshot(
                    window_id=item.id,
                    action=WindowRenderAction.KEEP,
                    rendered_text=active_text,
                    estimated_tokens=active_tokens,
                )

            if tier == "background" and background_tokens > 0 and background_tokens <= remaining:
                item.detail_level = DetailLevel.SUMMARY
                return WindowSnapshot(
                    window_id=item.id,
                    action=WindowRenderAction.KEEP,
                    rendered_text=background_text,
                    estimated_tokens=background_tokens,
                )

            if tier == "suspended":
                can_render_suspended = suspended_tokens > 0 and suspended_tokens <= remaining
                if not can_render_suspended:
                    continue

                has_minimized_budget = remaining >= self._budget.minimized_tokens
                must_keep_floor = max(1, int(self._budget.minimized_tokens) // 2)
                can_use_must_keep_fallback = must_keep and remaining >= must_keep_floor

                if not (has_minimized_budget or can_use_must_keep_fallback):
                    continue

                # must_keep 兜底语义：
                # 预算不足 minimized_tokens 时，活跃窗口可在更低硬下限（minimized_tokens//2）内保留。
                item.detail_level = DetailLevel.ICON
                return WindowSnapshot(
                    window_id=item.id,
                    action=WindowRenderAction.MINIMIZE,
                    rendered_text=suspended_text,
                    estimated_tokens=suspended_tokens,
                )

        item.detail_level = DetailLevel.NONE
        return WindowSnapshot(
            window_id=item.id,
            action=WindowRenderAction.CLOSE,
            rendered_text="",
            estimated_tokens=0,
        )

    def _tier_from_idle(self, item: Window, active_window_id: str | None) -> WindowTier:
        if item.id == active_window_id:
            return "active"

        bg_after, suspend_after, terminate_after = self._normalize_thresholds()
        idle = max(0, int(item.idle_turns))
        if idle < bg_after:
            return "active"
        if idle < suspend_after:
            return "background"
        if idle < terminate_after:
            return "suspended"
        return "terminated"

    def _normalize_thresholds(self) -> tuple[int, int, int]:
        background_after = max(1, int(self._budget.background_after_idle))
        suspend_after = max(background_after + 1, int(self._budget.suspend_after_idle))
        terminate_after = max(suspend_after + 1, int(self._budget.terminate_after_idle))
        return background_after, suspend_after, terminate_after

    @staticmethod
    def _fallback_chain(tier: WindowTier) -> tuple[WindowTier, ...]:
        if tier == "active":
            return ("active", "background", "suspended", "terminated")
        if tier == "background":
            return ("background", "suspended", "terminated")
        if tier == "suspended":
            return ("suspended", "terminated")
        return ("terminated",)

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        return TokenCounter.count_message({"role": "system", "content": text})
