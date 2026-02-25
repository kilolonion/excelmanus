"""CLI 通用工具函数 — 文本截断、参数格式化、耗时显示等。"""

from __future__ import annotations

import json
from typing import Any, Dict

from rich.console import Console
from rich.syntax import Syntax
from rich.text import Text

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


# ------------------------------------------------------------------
# 语法高亮
# ------------------------------------------------------------------

# 高亮代码块最大行数（超出截断）
SYNTAX_MAX_LINES = 25
SYNTAX_MAX_CHARS = 2000

# ------------------------------------------------------------------
# 括号分级着色（Rainbow Brackets）
# 三类括号使用不同色系，各色系按嵌套深度循环
# ------------------------------------------------------------------

_CURLY_COLORS = ("#e5a100", "#d7ba7d", "#c08b30", "#ffd700")   # {} gold/amber
_ROUND_COLORS = ("#569cd6", "#4fc1ff", "#0078d4", "#9cdcfe")   # () blue
_SQUARE_COLORS = ("#c586c0", "#da70d6", "#d16969", "#ce9178")  # [] pink/magenta

_OPEN_BRACKET_COLORS: dict[str, tuple[str, ...]] = {
    "{": _CURLY_COLORS, "(": _ROUND_COLORS, "[": _SQUARE_COLORS,
}
_CLOSE_BRACKET_COLORS: dict[str, tuple[str, ...]] = {
    "}": _CURLY_COLORS, ")": _ROUND_COLORS, "]": _SQUARE_COLORS,
}
_CLOSE_TO_OPEN: dict[str, str] = {"}": "{", ")": "(", "]": "["}


def colorize_brackets(rich_text: Text, code: str) -> None:
    """在 Rich Text 对象上原地叠加括号分级着色。

    跳过字符串内的括号，仅对结构性括号上色。
    """
    depths: dict[str, int] = {"{": 0, "(": 0, "[": 0}
    in_string = False
    string_char = ""
    escape_next = False

    for i, ch in enumerate(code):
        if escape_next:
            escape_next = False
            continue
        if ch == "\\":
            if in_string:
                escape_next = True
            continue
        # 字符串边界检测（支持 " 和 '）
        if ch in ('"', "'"):
            if in_string:
                if ch == string_char:
                    in_string = False
            else:
                in_string = True
                string_char = ch
            continue
        if in_string:
            continue

        if ch in _OPEN_BRACKET_COLORS:
            colors = _OPEN_BRACKET_COLORS[ch]
            depth = depths[ch]
            color = colors[depth % len(colors)]
            rich_text.stylize(f"bold {color}", i, i + 1)
            depths[ch] += 1
        elif ch in _CLOSE_BRACKET_COLORS:
            open_ch = _CLOSE_TO_OPEN[ch]
            depths[open_ch] = max(0, depths[open_ch] - 1)
            colors = _CLOSE_BRACKET_COLORS[ch]
            depth = depths[open_ch]
            color = colors[depth % len(colors)]
            rich_text.stylize(f"bold {color}", i, i + 1)


def looks_like_json(text: str) -> bool:
    """快速判断文本是否像 JSON（以 { 或 [ 开头且能解析）。"""
    stripped = text.strip()
    if not stripped or stripped[0] not in ("{", "["):
        return False
    try:
        json.loads(stripped)
        return True
    except (json.JSONDecodeError, ValueError):
        return False


def detect_language(text: str, *, tool_name: str = "") -> str | None:
    """根据文本内容和工具名推断语言。返回 None 表示不高亮。"""
    if tool_name == "run_code":
        return "python"
    if looks_like_json(text):
        return "json"
    return None


def render_syntax_block(
    console: Console,
    text: str,
    language: str,
    *,
    indent: int = 4,
    max_lines: int = SYNTAX_MAX_LINES,
    max_chars: int = SYNTAX_MAX_CHARS,
    theme: str = "monokai",
) -> None:
    """在终端中渲染带语法高亮 + 括号分级着色的代码块，含截断保护。"""
    display = text
    truncated = False
    if len(display) > max_chars:
        display = display[:max_chars]
        truncated = True

    lines = display.splitlines()
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        truncated = True
        display = "\n".join(lines)

    show_line_numbers = language == "python" and len(lines) > 3

    # 使用 Syntax.highlight() 获取 Rich Text，然后叠加括号着色
    syntax = Syntax(
        display,
        language,
        theme=theme,
        line_numbers=False,
        word_wrap=True,
        padding=(0, 0),
    )
    highlighted = syntax.highlight(display)
    colorize_brackets(highlighted, display)

    if show_line_numbers:
        # 手动添加行号
        code_lines = highlighted.split("\n")
        gutter_width = len(str(len(code_lines))) + 1
        pad = " " * indent
        for line_no, line_text in enumerate(code_lines, 1):
            gutter = Text(f"{pad}{line_no:>{gutter_width}} ", style="dim")
            console.print(gutter + line_text, highlight=False)
    else:
        pad = Text(" " * indent)
        for line_text in highlighted.split("\n"):
            console.print(pad + line_text, highlight=False)

    if truncated:
        console.print(
            Text(f"{' ' * indent}… (已截断)", style=f"{THEME.DIM}"),
        )


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
