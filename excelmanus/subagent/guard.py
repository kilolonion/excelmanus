"""子代理写入守卫。只读子代理不得写入，不靠提示词。"""

from __future__ import annotations

from typing import Any

from excelmanus.tools.policy import MUTATING_ALL_TOOLS


def reject_readonly_write(config: Any, tool_name: str) -> str | None:
    """readOnly 子代理调用写入工具时返回拒绝理由，否则 None。"""
    from excelmanus.security.policy import resolve_execution_policy

    class _Probe:
        _current_chat_mode = "write"
        _subagent_config = config

    if resolve_execution_policy(_Probe()).mode != "read-only":
        return None
    if tool_name in MUTATING_ALL_TOOLS:
        return f"只读子代理拒绝写入：{tool_name}"
    return None
