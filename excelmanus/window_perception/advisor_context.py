"""窗口生命周期顾问上下文。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AdvisorContext:
    """传递给 Advisor 的决策上下文。"""

    turn_number: int = 0
    is_new_task: bool = False
    window_count_changed: bool = False
    user_intent_summary: str = ""
    agent_recent_output: str = ""
    task_type: str = "GENERAL_BROWSE"

