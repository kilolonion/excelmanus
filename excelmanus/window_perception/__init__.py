"""窗口感知层导出。"""

from .advisor import HybridAdvisor, LifecyclePlan, RuleBasedAdvisor, WindowAdvice, WindowLifecycleAdvisor
from .advisor_context import AdvisorContext
from .adaptive import AdaptiveModeSelector
from .confirmation import (
    ConfirmationRecord,
    build_confirmation_record,
    parse_confirmation,
    serialize_confirmation,
)
from .focus import FocusService
from .manager import WindowPerceptionManager
from .models import (
    CachedRange,
    ChangeRecord,
    ColumnDef,
    DetailLevel,
    IntentTag,
    OpEntry,
    PerceptionBudget,
    Viewport,
    WindowSnapshot,
    WindowState,
    WindowType,
)
from .repeat_detector import RepeatDetector
from .rule_registry import (
    IntentDecision,
    ToolMeta,
    classify_tool_meta,
    is_read_like_tool,
    is_write_like_tool,
    repeat_threshold,
    resolve_intent_decision,
    task_type_from_intent,
)
from .small_model import TASK_TYPES, build_advisor_messages, parse_small_model_plan

__all__ = [
    "AdvisorContext",
    "AdaptiveModeSelector",
    "build_confirmation_record",
    "build_advisor_messages",
    "CachedRange",
    "ChangeRecord",
    "ColumnDef",
    "ConfirmationRecord",
    "DetailLevel",
    "IntentTag",
    "IntentDecision",
    "HybridAdvisor",
    "LifecyclePlan",
    "OpEntry",
    "parse_confirmation",
    "PerceptionBudget",
    "parse_small_model_plan",
    "repeat_threshold",
    "resolve_intent_decision",
    "serialize_confirmation",
    "FocusService",
    "RepeatDetector",
    "RuleBasedAdvisor",
    "classify_tool_meta",
    "is_read_like_tool",
    "is_write_like_tool",
    "TASK_TYPES",
    "task_type_from_intent",
    "ToolMeta",
    "Viewport",
    "WindowAdvice",
    "WindowLifecycleAdvisor",
    "WindowSnapshot",
    "WindowState",
    "WindowType",
    "WindowPerceptionManager",
]
