"""窗口感知层导出。"""

from .advisor import LifecyclePlan, RuleBasedAdvisor, WindowAdvice, WindowLifecycleAdvisor
from .advisor_context import AdvisorContext
from .manager import WindowPerceptionManager
from .models import PerceptionBudget, Viewport, WindowSnapshot, WindowState, WindowType

__all__ = [
    "AdvisorContext",
    "LifecyclePlan",
    "PerceptionBudget",
    "RuleBasedAdvisor",
    "Viewport",
    "WindowAdvice",
    "WindowLifecycleAdvisor",
    "WindowSnapshot",
    "WindowState",
    "WindowType",
    "WindowPerceptionManager",
]
