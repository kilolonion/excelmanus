"""窗口生命周期顾问测试。"""

from excelmanus.window_perception.advisor import RuleBasedAdvisor
from excelmanus.window_perception.advisor_context import AdvisorContext
from excelmanus.window_perception.models import PerceptionBudget, WindowState, WindowType


class TestRuleBasedAdvisor:
    """规则顾问分层决策测试。"""

    def test_tier_assignment_by_idle_turns(self) -> None:
        advisor = RuleBasedAdvisor()
        budget = PerceptionBudget(
            background_after_idle=1,
            suspend_after_idle=3,
            terminate_after_idle=5,
        )
        windows = [
            WindowState(id="w0", type=WindowType.SHEET, title="A", idle_turns=0),
            WindowState(id="w1", type=WindowType.SHEET, title="B", idle_turns=1),
            WindowState(id="w3", type=WindowType.SHEET, title="C", idle_turns=3),
            WindowState(id="w5", type=WindowType.SHEET, title="D", idle_turns=5),
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

    def test_active_window_always_active(self) -> None:
        advisor = RuleBasedAdvisor()
        budget = PerceptionBudget()
        window = WindowState(id="w1", type=WindowType.SHEET, title="A", idle_turns=999)

        plan = advisor.advise(
            windows=[window],
            active_window_id="w1",
            budget=budget,
            context=AdvisorContext(turn_number=10),
        )

        assert plan.advices[0].tier == "active"

