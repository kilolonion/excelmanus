"""Hooks 数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal


class HookEvent(str, Enum):
    SESSION_START = "SessionStart"
    USER_PROMPT_SUBMIT = "UserPromptSubmit"
    PRE_TOOL_USE = "PreToolUse"
    POST_TOOL_USE = "PostToolUse"
    POST_TOOL_USE_FAILURE = "PostToolUseFailure"
    SUBAGENT_START = "SubagentStart"
    SUBAGENT_STOP = "SubagentStop"
    STOP = "Stop"
    SESSION_END = "SessionEnd"


class HookDecision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"
    CONTINUE = "continue"


@dataclass
class HookAgentAction:
    """agent hook 的执行动作定义。"""

    task: str
    agent_name: str | None = None
    on_failure: Literal["continue", "deny"] = "continue"
    inject_summary_as_context: bool = True


@dataclass
class HookResult:
    decision: HookDecision = HookDecision.CONTINUE
    reason: str = ""
    updated_input: dict[str, Any] | None = None
    additional_context: str = ""
    agent_action: HookAgentAction | None = None
    raw_output: dict[str, Any] = field(default_factory=dict)


@dataclass
class HookCallContext:
    event: HookEvent
    skill_name: str
    payload: dict[str, Any] = field(default_factory=dict)
    tool_name: str = ""
    full_access_enabled: bool = False
