"""窗口生命周期顾问测试。"""

from excelmanus.window_perception.advisor import HybridAdvisor, LifecyclePlan, RuleBasedAdvisor, WindowAdvice
from excelmanus.window_perception.advisor_context import AdvisorContext
from excelmanus.window_perception.models import PerceptionBudget, WindowType
from tests.window_factories import make_window


class TestRuleBasedAdvisor:
    """规则顾问分层决策测试。"""

    def test_tier_assignment_by_idle_turns_with_default_258(self) -> None:
        advisor = RuleBasedAdvisor()
        budget = PerceptionBudget(
            background_after_idle=2,
            suspend_after_idle=5,
            terminate_after_idle=8,
        )
        windows = [
            make_window(id="w1", type=WindowType.SHEET, title="A", idle_turns=1),
            make_window(id="w2", type=WindowType.SHEET, title="B", idle_turns=2),
            make_window(id="w5", type=WindowType.SHEET, title="C", idle_turns=5),
            make_window(id="w8", type=WindowType.SHEET, title="D", idle_turns=8),
        ]

        plan = advisor.advise(
            windows=windows,
            active_window_id=None,
            budget=budget,
            context=AdvisorContext(turn_number=2),
        )
        tiers = {item.window_id: item.tier for item in plan.advices}

        assert tiers["w1"] == "active"
        assert tiers["w2"] == "background"
        assert tiers["w5"] == "suspended"
        assert tiers["w8"] == "terminated"

    def test_tier_assignment_by_idle_turns(self) -> None:
        advisor = RuleBasedAdvisor()
        budget = PerceptionBudget(
            background_after_idle=1,
            suspend_after_idle=3,
            terminate_after_idle=5,
        )
        windows = [
            make_window(id="w0", type=WindowType.SHEET, title="A", idle_turns=0),
            make_window(id="w1", type=WindowType.SHEET, title="B", idle_turns=1),
            make_window(id="w3", type=WindowType.SHEET, title="C", idle_turns=3),
            make_window(id="w5", type=WindowType.SHEET, title="D", idle_turns=5),
        ]

        plan = advisor.advise(
            windows=windows,
            active_window_id=None,
            budget=budget,
            context=AdvisorContext(turn_number=2),
        )
        tiers = {item.window_id: item.tier for item in plan.advices}

        assert tiers["w0"] == "active"
        assert tiers["w1"] == "background"
        assert tiers["w3"] == "suspended"
        assert tiers["w5"] == "terminated"

    def test_relax_thresholds_when_task_tags_include_cross_sheet(self) -> None:
        advisor = RuleBasedAdvisor()
        budget = PerceptionBudget(
            background_after_idle=2,
            suspend_after_idle=5,
            terminate_after_idle=8,
        )
        windows = [
            make_window(id="w3", type=WindowType.SHEET, title="A", idle_turns=3),
            make_window(id="w6", type=WindowType.SHEET, title="B", idle_turns=6),
            make_window(id="w10", type=WindowType.SHEET, title="C", idle_turns=10),
        ]

        plan = advisor.advise(
            windows=windows,
            active_window_id=None,
            budget=budget,
            context=AdvisorContext(turn_number=2, task_tags=("cross_sheet",)),
        )
        tiers = {item.window_id: item.tier for item in plan.advices}

        assert tiers["w3"] == "active"
        assert tiers["w6"] == "background"
        assert tiers["w10"] == "suspended"

    def test_relax_thresholds_do_not_stack_for_multiple_task_tags(self) -> None:
        advisor = RuleBasedAdvisor()
        budget = PerceptionBudget(
            background_after_idle=2,
            suspend_after_idle=5,
            terminate_after_idle=8,
        )
        window = make_window(id="w11", type=WindowType.SHEET, title="A", idle_turns=11)

        plan = advisor.advise(
            windows=[window],
            active_window_id=None,
            budget=budget,
            context=AdvisorContext(turn_number=2, task_tags=("cross_sheet", "large_data")),
        )

        assert plan.advices[0].tier == "terminated"

    def test_active_window_always_active(self) -> None:
        advisor = RuleBasedAdvisor()
        budget = PerceptionBudget()
        window = make_window(id="w1", type=WindowType.SHEET, title="A", idle_turns=999)

        plan = advisor.advise(
            windows=[window],
            active_window_id="w1",
            budget=budget,
            context=AdvisorContext(turn_number=10),
        )

        assert plan.advices[0].tier == "active"


class TestHybridAdvisor:
    """混合顾问测试。"""

    def test_uses_small_model_plan_when_fresh_and_valid(self) -> None:
        advisor = HybridAdvisor()
        budget = PerceptionBudget()
        windows = [
            make_window(id="w1", type=WindowType.SHEET, title="A", idle_turns=0),
            make_window(id="w2", type=WindowType.SHEET, title="B", idle_turns=2),
        ]

        plan = advisor.advise(
            windows=windows,
            active_window_id="w1",
            budget=budget,
            context=AdvisorContext(turn_number=5, task_type="GENERAL_BROWSE"),
            small_model_plan=LifecyclePlan(
                advices=[
                    WindowAdvice(window_id="w2", tier="suspended", reason="已完成"),
                ],
                source="small_model",
                task_type="DATA_COMPARISON",
                generated_turn=4,
            ),
            plan_ttl_turns=2,
        )
        tiers = {item.window_id: item.tier for item in plan.advices}
        assert plan.source == "hybrid"
        assert tiers["w1"] == "active"
        assert tiers["w2"] == "suspended"

    def test_invalid_small_model_plan_falls_back_to_rules(self) -> None:
        advisor = HybridAdvisor()
        budget = PerceptionBudget()
        windows = [
            make_window(id="w1", type=WindowType.SHEET, title="A", idle_turns=3),
        ]

        plan = advisor.advise(
            windows=windows,
            active_window_id=None,
            budget=budget,
            context=AdvisorContext(turn_number=6, task_type="GENERAL_BROWSE"),
            small_model_plan=LifecyclePlan(
                advices=[WindowAdvice(window_id="unknown", tier="active")],
                source="small_model",
                task_type="UNKNOWN_TASK",
                generated_turn=5,
            ),
            plan_ttl_turns=2,
        )
        assert plan.source == "rules"
        assert plan.advices[0].tier == "background"

    def test_expired_small_model_plan_falls_back_to_rules(self) -> None:
        advisor = HybridAdvisor()
        budget = PerceptionBudget()
        windows = [
            make_window(id="w1", type=WindowType.SHEET, title="A", idle_turns=1),
        ]

        plan = advisor.advise(
            windows=windows,
            active_window_id=None,
            budget=budget,
            context=AdvisorContext(turn_number=10, task_type="GENERAL_BROWSE"),
            small_model_plan=LifecyclePlan(
                advices=[WindowAdvice(window_id="w1", tier="terminated")],
                source="small_model",
                task_type="GENERAL_BROWSE",
                generated_turn=2,
            ),
            plan_ttl_turns=2,
        )
        assert plan.source == "rules"
        assert plan.advices[0].tier == "active"

    def test_valid_plan_with_only_unknown_windows_keeps_base_active_window(self) -> None:
        advisor = HybridAdvisor()
        budget = PerceptionBudget()
        windows = [
            make_window(id="w1", type=WindowType.SHEET, title="A", idle_turns=999),
            make_window(id="w2", type=WindowType.SHEET, title="B", idle_turns=999),
        ]

        plan = advisor.advise(
            windows=windows,
            active_window_id="w1",
            budget=budget,
            context=AdvisorContext(turn_number=8, task_type="GENERAL_BROWSE"),
            small_model_plan=LifecyclePlan(
                advices=[WindowAdvice(window_id="unknown", tier="terminated")],
                source="small_model",
                task_type="GENERAL_BROWSE",
                generated_turn=7,
            ),
            plan_ttl_turns=2,
        )

        tiers = {item.window_id: item.tier for item in plan.advices}
        assert plan.source == "rules"
        assert tiers["w1"] == "active"
