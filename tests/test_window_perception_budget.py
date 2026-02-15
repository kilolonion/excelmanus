"""窗口感知预算分配测试。"""

from excelmanus.window_perception.advisor import LifecyclePlan, WindowAdvice
from excelmanus.window_perception.budget import WindowBudgetAllocator
from excelmanus.window_perception.models import PerceptionBudget, WindowRenderAction, WindowState, WindowType


class TestWindowBudget:
    """预算分配逻辑测试。"""

    def test_active_window_kept_when_budget_sufficient(self) -> None:
        budget = PerceptionBudget(system_budget_tokens=1000, max_windows=3)
        allocator = WindowBudgetAllocator(budget)
        w1 = WindowState(id="w1", type=WindowType.SHEET, title="A", last_access_seq=10)
        w2 = WindowState(id="w2", type=WindowType.SHEET, title="B", last_access_seq=9)

        snapshots = allocator.allocate(
            windows=[w1, w2],
            active_window_id="w2",
            render_keep=lambda _: "x" * 120,
            render_minimized=lambda _: "mini",
        )

        target = [item for item in snapshots if item.window_id == "w2"][0]
        assert target.action in {WindowRenderAction.KEEP, WindowRenderAction.MINIMIZE}

    def test_non_active_windows_can_be_closed_under_tight_budget(self) -> None:
        budget = PerceptionBudget(system_budget_tokens=1, max_windows=2)
        allocator = WindowBudgetAllocator(budget)
        w1 = WindowState(id="w1", type=WindowType.SHEET, title="A", last_access_seq=2)
        w2 = WindowState(id="w2", type=WindowType.SHEET, title="B", last_access_seq=1)

        snapshots = allocator.allocate(
            windows=[w1, w2],
            active_window_id="w1",
            render_keep=lambda _: "非常长" * 200,
            render_minimized=lambda _: "mini",
        )
        closed = [item for item in snapshots if item.action == WindowRenderAction.CLOSE]
        assert len(closed) >= 1

    def test_background_tier_uses_background_renderer(self) -> None:
        budget = PerceptionBudget(system_budget_tokens=1000, max_windows=2)
        allocator = WindowBudgetAllocator(budget)
        w1 = WindowState(id="w1", type=WindowType.SHEET, title="A", idle_turns=2)

        snapshots = allocator.allocate(
            windows=[w1],
            active_window_id=None,
            render_keep=lambda _: "ACTIVE",
            render_background=lambda _: "BACKGROUND",
            render_minimized=lambda _: "SUSPENDED",
            lifecycle_plan=LifecyclePlan(
                advices=[WindowAdvice(window_id="w1", tier="background")],
                source="rules",
            ),
        )

        assert snapshots[0].action == WindowRenderAction.KEEP
        assert snapshots[0].rendered_text == "BACKGROUND"

    def test_terminated_tier_closes_window(self) -> None:
        budget = PerceptionBudget(system_budget_tokens=1000, max_windows=2)
        allocator = WindowBudgetAllocator(budget)
        w1 = WindowState(id="w1", type=WindowType.SHEET, title="A", idle_turns=6)

        snapshots = allocator.allocate(
            windows=[w1],
            active_window_id=None,
            render_keep=lambda _: "ACTIVE",
            render_background=lambda _: "BACKGROUND",
            render_minimized=lambda _: "SUSPENDED",
            lifecycle_plan=LifecyclePlan(
                advices=[WindowAdvice(window_id="w1", tier="terminated")],
                source="rules",
            ),
        )

        assert snapshots[0].action == WindowRenderAction.CLOSE

    def test_hybrid_lifecycle_plan_uses_advised_tier(self) -> None:
        budget = PerceptionBudget(system_budget_tokens=1000, max_windows=2)
        allocator = WindowBudgetAllocator(budget)
        w1 = WindowState(id="w1", type=WindowType.SHEET, title="A", idle_turns=1)

        snapshots = allocator.allocate(
            windows=[w1],
            active_window_id=None,
            render_keep=lambda _: "ACTIVE",
            render_background=lambda _: "BACKGROUND",
            render_minimized=lambda _: "SUSPENDED",
            lifecycle_plan=LifecyclePlan(
                advices=[WindowAdvice(window_id="w1", tier="suspended")],
                source="hybrid",
            ),
        )

        assert snapshots[0].action == WindowRenderAction.MINIMIZE
        assert snapshots[0].rendered_text == "SUSPENDED"
