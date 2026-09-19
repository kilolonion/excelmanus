"""子代理写入守卫。只读子代理不得写入，不靠提示词。"""

from __future__ import annotations

from typing import Any

from excelmanus.security.policy import RESTRICTED_WRITE_EFFECTS
from excelmanus.tools.policy import (
    MUTATING_ALL_TOOLS,
    READ_ONLY_SAFE_TOOLS,
    write_effect_for_call,
)


def reject_readonly_write(
    config: Any,
    tool_name: str,
    *,
    parent_call: str | None = None,
    arguments: dict[str, Any] | None = None,
    tool_def: Any = None,
) -> str | None:
    """readOnly 子代理调用写入动作时返回拒绝理由，否则 None。

    ``run_code`` 本身允许只读计算。其写入子调用以及直接写 action
    一律按 ``write_effect_for_call`` 判定；``versions.list`` 不得因工具名误拒。
    """
    from excelmanus.security.policy import resolve_execution_policy

    class _Probe:
        _current_chat_mode = "write"
        _subagent_config = config

    if resolve_execution_policy(_Probe()).mode != "read-only":
        return None
    if not parent_call and tool_name == "run_code":
        return None
    declared = "unknown"
    actions = None
    if tool_def is not None:
        raw_effect = getattr(tool_def, "write_effect", None)
        if isinstance(raw_effect, str) and raw_effect.strip():
            declared = raw_effect
        raw_actions = getattr(tool_def, "actions", None)
        if isinstance(raw_actions, dict):
            actions = raw_actions
    elif tool_name in READ_ONLY_SAFE_TOOLS:
        declared = "none"
    elif tool_name in MUTATING_ALL_TOOLS:
        declared = "workspace_write"
    effect = write_effect_for_call(
        tool_name,
        arguments,
        declared=declared,
        actions=actions,
    )
    if effect in RESTRICTED_WRITE_EFFECTS:
        return f"只读子代理拒绝写入：{tool_name}"
    return None
