"""CLI 通用工具函数 — 文本截断、参数格式化、耗时显示等。"""

from __future__ import annotations

from typing import Any, Dict

from rich.console import Console

from excelmanus.cli.theme import THEME

# 截断阈值常量
RESULT_MAX_LEN = 200
THINKING_THRESHOLD = 500
THINKING_SUMMARY_LEN = 80
NARROW_TERMINAL_WIDTH = 60
SUBAGENT_SUMMARY_PREVIEW = 300
SUBAGENT_REASON_PREVIEW = 220
SUBAGENT_TOOL_PREVIEW = 180
SUBAGENT_TOOL_MAX_ITEMS = 8


def truncate(text: str, max_len: int) -> str:
    """截断文本，超过 max_len 时追加省略标记。"""
    if len(text) <= max_len:
        return text
    return text[:max_len] + "…"


def format_arguments(arguments: Dict[str, Any]) -> str:
    """将参数字典格式化为可读字符串。"""
    if not arguments:
        return ""
    parts = []
    for key, value in arguments.items():
        if isinstance(value, str):
            display = truncate(value, 60)
            parts.append(f'{key}="{display}"')
        else:
            parts.append(f"{key}={value}")
    return ", ".join(parts)


def format_elapsed(seconds: float) -> str:
    """格式化耗时为人类可读字符串。"""
    if seconds < 1:
        return f"{seconds * 1000:.0f}ms"
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = int(seconds // 60)
    secs = seconds % 60
    return f"{minutes}m{secs:.0f}s"


def separator_line(width: int = 50) -> str:
    """生成指定宽度的水平分隔线。"""
    return THEME.SEPARATOR * width


def is_narrow_terminal(console: Console) -> bool:
    """判断终端是否为窄终端（宽度 < 60）。"""
    explicit_width = getattr(console, "_width", None)
    if isinstance(explicit_width, int) and explicit_width > 0:
        return explicit_width < NARROW_TERMINAL_WIDTH
    return console.width < NARROW_TERMINAL_WIDTH


def format_subagent_tools(tools: list[str]) -> str:
    """格式化 subagent 工具列表，避免超长输出。"""
    if not tools:
        return "(无)"
    head = tools[:SUBAGENT_TOOL_MAX_ITEMS]
    rendered = ", ".join(head)
    extra = len(tools) - len(head)
    if extra > 0:
        rendered = f"{rendered}, ... (+{extra})"
    return truncate(rendered, SUBAGENT_TOOL_PREVIEW)
