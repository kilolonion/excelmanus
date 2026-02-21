"""CLI 输入提示符 — prompt_toolkit 配置与用户输入读取。

提供 › 前缀输入提示符，
支持斜杠命令补全、@ 提及补全、内联建议。
"""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING

from rich.console import Console

from excelmanus.cli.theme import THEME

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# prompt_toolkit 可选依赖
# ------------------------------------------------------------------

_PROMPT_TOOLKIT_ENABLED = False
try:
    from prompt_toolkit import ANSI, PromptSession
    from prompt_toolkit.auto_suggest import AutoSuggest, Suggestion
    from prompt_toolkit.filters import Condition
    from prompt_toolkit.history import InMemoryHistory
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.styles import Style

    _PROMPT_TOOLKIT_ENABLED = True
except ImportError:
    pass

# ------------------------------------------------------------------
# 模块级状态
# ------------------------------------------------------------------

# @ 提及补全器（在 repl 中 engine 可用后初始化）
_MENTION_COMPLETER: object | None = None

# 斜杠命令建议列表（从外部同步）
_SLASH_COMMAND_SUGGESTIONS: tuple[str, ...] = ()
_DYNAMIC_SKILL_SLASH_COMMANDS: tuple[str, ...] = ()

# 命令参数补全映射（从外部同步）
_COMMAND_ARGUMENT_MAP: dict[str, tuple[str, ...]] = {}

# console 实例（从外部注入，默认懒创建）
console: Console | None = None


def _get_console() -> Console:
    """获取 console 实例，若未注入则懒创建。"""
    global console
    if console is None:
        console = Console()
    return console

# ------------------------------------------------------------------
# 提示符构建
# ------------------------------------------------------------------


def build_prompt_badges(
    *,
    model_hint: str = "",
    turn_number: int = 0,
) -> str:
    """构建 prompt 徽章字符串。"""
    parts: list[str] = []
    if model_hint:
        parts.append(model_hint)
    if turn_number > 0:
        parts.append(f"#{turn_number}")
    return " ".join(parts)


def _list_known_slash_commands() -> tuple[str, ...]:
    """返回所有已知斜杠命令（保序去重）。"""
    ordered = list(_SLASH_COMMAND_SUGGESTIONS)
    ordered.extend(_DYNAMIC_SKILL_SLASH_COMMANDS)
    return tuple(dict.fromkeys(ordered))


def compute_inline_suggestion(user_input: str) -> str | None:
    """根据当前输入计算可追加的补全文本（返回后缀）。"""
    if not user_input.startswith("/"):
        return None

    command, separator, remainder = user_input.partition(" ")
    lowered_command = command.lower()

    # 先补全命令本体：如 /ful -> /fullaccess
    if not separator:
        for suggestion in _list_known_slash_commands():
            if suggestion.lower() == lowered_command:
                return None
            if suggestion.lower().startswith(lowered_command):
                return suggestion[len(user_input):]
        return None

    # 再补全控制命令参数
    available_arguments = _COMMAND_ARGUMENT_MAP.get(lowered_command)
    if available_arguments is None:
        return None

    current_arg = remainder.strip()
    if not current_arg:
        return available_arguments[0] if available_arguments else None
    if " " in current_arg:
        return None

    lowered_arg = current_arg.lower()
    for candidate in available_arguments:
        if candidate == lowered_arg:
            return None
        if candidate.startswith(lowered_arg):
            return candidate[len(current_arg):]
    return None


# ------------------------------------------------------------------
# prompt_toolkit 设置
# ------------------------------------------------------------------

_PROMPT_SESSION = None

if _PROMPT_TOOLKIT_ENABLED:

    class _SlashCommandAutoSuggest(AutoSuggest):
        """基于斜杠命令的内联补全建议器。"""

        def get_suggestion(self, buffer, document):  # type: ignore[override]
            suffix = compute_inline_suggestion(document.text_before_cursor)
            if suffix is None:
                return None
            return Suggestion(suffix)

    _PROMPT_HISTORY = InMemoryHistory()
    _PROMPT_STYLE = Style.from_dict({
        "auto-suggestion": "ansibrightblack",
        # Excel 绿色系补全菜单
        "completion-menu":                     "bg:#f0f0f0 #333333",
        "completion-menu.completion":           "bg:#f0f0f0 #333333",
        "completion-menu.completion.current":   f"bg:{THEME.ACCENT} #ffffff bold",
        "completion-menu.meta.completion":      "bg:#f0f0f0 #888888 italic",
        "completion-menu.meta.completion.current": f"bg:{THEME.ACCENT} #ffffff italic",
        "scrollbar.background":                "bg:#e0e0e0",
        "scrollbar.button":                    f"bg:{THEME.PRIMARY}",
    })
    _SLASH_AUTO_SUGGEST = _SlashCommandAutoSuggest()
    _PROMPT_KEY_BINDINGS = KeyBindings()

    @Condition
    def _completion_menu_is_open() -> bool:
        """补全菜单是否打开。"""
        app = _PROMPT_SESSION and getattr(_PROMPT_SESSION, "app", None)
        if app is None:
            return False
        buf = app.current_buffer
        return buf.complete_state is not None

    def _accept_and_maybe_retrigger(event) -> None:
        """确认补全项，若结果以 @type: 或目录 / 结尾则自动触发下一级补全。"""
        buf = event.current_buffer
        buf.complete_state = None
        text = buf.text[:buf.cursor_position]
        import re as _re
        if _re.search(r"@(?:file|folder|skill|mcp):\s*$", text) or _re.search(r"@img\s+$", text):
            buf.start_completion()
        elif _re.search(r"@(?:file|folder):\S*/$", text) or _re.search(r"@img\s+\S*/$", text):
            buf.start_completion()

    @_PROMPT_KEY_BINDINGS.add("enter", filter=_completion_menu_is_open)
    def _accept_completion(event) -> None:
        """补全菜单打开时，回车选择当前补全项而非提交。"""
        _accept_and_maybe_retrigger(event)

    @_PROMPT_KEY_BINDINGS.add("@")
    def _trigger_mention_completion(event) -> None:
        """输入 @ 后立即插入字符并触发补全菜单。"""
        buf = event.current_buffer
        buf.insert_text("@")
        if _MENTION_COMPLETER is not None:
            buf.start_completion()

    @_PROMPT_KEY_BINDINGS.add("tab")
    def _accept_inline_suggestion(event) -> None:
        """按 Tab 接受灰色补全建议。"""
        buf = event.current_buffer
        if buf.complete_state is not None:
            _accept_and_maybe_retrigger(event)
            return
        suggestion = buf.suggestion
        if suggestion:
            buf.insert_text(suggestion.text)

    _PROMPT_SESSION = PromptSession(
        history=_PROMPT_HISTORY,
        auto_suggest=_SLASH_AUTO_SUGGEST,
        style=_PROMPT_STYLE,
        key_bindings=_PROMPT_KEY_BINDINGS,
    )


# ------------------------------------------------------------------
# 用户输入读取
# ------------------------------------------------------------------


async def read_user_input(
    *,
    model_hint: str = "",
    turn_number: int = 0,
) -> str:
    """读取用户输入：› 前缀提示符。"""
    badges = build_prompt_badges(
        model_hint=model_hint,
        turn_number=turn_number,
    )
    # 绿色 › 前缀
    if badges:
        ansi_prompt = f"\n \x1b[2;37m{badges}\x1b[0m \x1b[1;38;2;33;168;103m{THEME.USER_PREFIX}\x1b[0m "
        rich_prompt = f"\n [{THEME.DIM}]{badges}[/{THEME.DIM}] [{THEME.BOLD} {THEME.PRIMARY_LIGHT}]{THEME.USER_PREFIX}[/{THEME.BOLD} {THEME.PRIMARY_LIGHT}] "
    else:
        ansi_prompt = f"\n \x1b[1;38;2;33;168;103m{THEME.USER_PREFIX}\x1b[0m "
        rich_prompt = f"\n [{THEME.BOLD} {THEME.PRIMARY_LIGHT}]{THEME.USER_PREFIX}[/{THEME.BOLD} {THEME.PRIMARY_LIGHT}] "

    if (
        _PROMPT_TOOLKIT_ENABLED
        and _PROMPT_SESSION is not None
        and sys.stdin.isatty()
        and sys.stdout.isatty()
    ):
        try:
            return await _PROMPT_SESSION.prompt_async(
                ANSI(ansi_prompt),
                completer=_MENTION_COMPLETER,
                complete_while_typing=False,
            )
        except (KeyboardInterrupt, EOFError):
            raise
        except Exception as exc:  # pragma: no cover
            logger.warning("prompt_toolkit 输入失败，回退到基础输入：%s", exc)

    return _get_console().input(rich_prompt)


async def read_multiline_user_input() -> str:
    """读取多行输入：空行提交，返回换行拼接后的文本。"""
    lines: list[str] = []
    while True:
        line = await read_user_input()
        if not line.strip():
            break
        lines.append(line)
    return "\n".join(lines)


def is_interactive_terminal() -> bool:
    """判断当前终端是否支持交互式 UI。"""
    return (
        _PROMPT_TOOLKIT_ENABLED
        and sys.stdin.isatty()
        and sys.stdout.isatty()
    )
