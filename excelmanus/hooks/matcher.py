"""Hook 规则匹配器。"""

from __future__ import annotations

from fnmatch import fnmatch


def match_tool(matcher: str | None, tool_name: str) -> bool:
    """基于 glob 模式匹配工具名。"""
    if matcher is None:
        return True
    pattern = matcher.strip()
    if not pattern:
        return True
    return fnmatch(tool_name, pattern)
