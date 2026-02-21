"""CLI 审批确认流 — Claude Code 风格内联式确认。

提供 Yes / Yes for session / No 三选一内联确认，
支持 ↑↓ 箭头键导航、Enter 确认、Esc 取消、Shift+Tab 快捷全部授权。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from excelmanus.cli.theme import THEME

if TYPE_CHECKING:
    from excelmanus.approval import PendingApproval

logger = logging.getLogger(__name__)

# 审批选项常量
APPROVAL_ACCEPT = "执行"
APPROVAL_REJECT = "拒绝"
APPROVAL_FULLACCESS = "全部授权"

# 选项列表：(显示标签, 描述, 返回值)
_APPROVAL_OPTIONS: list[tuple[str, str, str]] = [
    (f"{THEME.SUCCESS} Yes", "确认并执行此操作", APPROVAL_ACCEPT),
    (f"{THEME.SUCCESS} Yes, allow all during this session", "开启 fullAccess", APPROVAL_FULLACCESS),
    (f"{THEME.FAILURE} No", "取消此操作", APPROVAL_REJECT),
]

# prompt_toolkit 可选依赖
_PT_ENABLED = False
try:
    from prompt_toolkit import Application
    from prompt_toolkit.formatted_text import FormattedText
    from prompt_toolkit.key_bindings import KeyBindings
    from prompt_toolkit.layout import HSplit, Layout, Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.styles import Style

    _PT_ENABLED = True
except ImportError:
    pass


def _is_interactive() -> bool:
    """判断当前终端是否支持交互式 UI。"""
    import sys
    return _PT_ENABLED and sys.stdin.isatty() and sys.stdout.isatty()


async def interactive_approval_select(
    pending: "PendingApproval",
) -> str | None:
    """Claude Code 风格内联审批选择器。

    ↑↓ 移动光标，Enter 确认，Esc 取消。
    Shift+Tab 快捷选择 "Yes for session"。

    返回 APPROVAL_ACCEPT / APPROVAL_REJECT / APPROVAL_FULLACCESS，
    或 None（不支持交互式或用户 Esc）。
    """
    if not _is_interactive():
        return None

    cursor = [0]
    result_holder: list[str | None] = []

    kb = KeyBindings()

    @kb.add("up")
    def _move_up(event) -> None:  # type: ignore[no-untyped-def]
        cursor[0] = (cursor[0] - 1) % len(_APPROVAL_OPTIONS)

    @kb.add("down")
    def _move_down(event) -> None:  # type: ignore[no-untyped-def]
        cursor[0] = (cursor[0] + 1) % len(_APPROVAL_OPTIONS)

    @kb.add("enter")
    def _confirm(event) -> None:  # type: ignore[no-untyped-def]
        result_holder.append(_APPROVAL_OPTIONS[cursor[0]][2])
        event.app.exit()

    @kb.add("escape")
    def _escape(event) -> None:  # type: ignore[no-untyped-def]
        result_holder.append(None)
        event.app.exit()

    @kb.add("s-tab")  # Shift+Tab 快捷选择 "Yes for session"
    def _shift_tab(event) -> None:  # type: ignore[no-untyped-def]
        result_holder.append(APPROVAL_FULLACCESS)
        event.app.exit()

    # 构建参数摘要
    args = pending.arguments or {}
    args_parts: list[str] = []
    for key in ("file_path", "sheet_name", "script", "command"):
        val = args.get(key)
        if val is not None:
            display = str(val)
            if len(display) > 60:
                display = display[:57] + "..."
            args_parts.append(f"{key}={display}")
    args_summary = ", ".join(args_parts) if args_parts else ""

    def _get_formatted_text() -> FormattedText:
        """生成 Claude Code 风格审批选择器文本。"""
        fragments: list[tuple[str, str]] = []
        # 工具调用信息
        tool_display = pending.tool_name or "未知工具"
        if args_summary:
            tool_display += f"({args_summary})"
        fragments.append(("class:header", f"  {THEME.AGENT_PREFIX} {tool_display}\n"))
        fragments.append(("class:separator", f"  {'─' * 50}\n"))
        fragments.append(("", "\n"))
        fragments.append(("class:text", "  Do you want to execute this tool?\n"))

        for i, (label, _desc, _value) in enumerate(_APPROVAL_OPTIONS):
            is_cursor = i == cursor[0]
            prefix = f"  {THEME.CURSOR} " if is_cursor else "    "
            line = f"{prefix}{label}\n"
            style = "class:selected" if is_cursor else "class:option"
            fragments.append((style, line))

        fragments.append(("", "\n"))
        fragments.append(("class:hint", "  Esc to cancel · shift+tab auto-accept\n"))
        return FormattedText(fragments)

    control = FormattedTextControl(_get_formatted_text)
    window = Window(content=control, always_hide_cursor=True)
    layout = Layout(HSplit([window]))

    style = Style.from_dict(
        {
            "header": f"bold {THEME.PRIMARY_LIGHT}",
            "separator": "dim",
            "text": "",
            "selected": f"bold {THEME.PRIMARY_LIGHT}",
            "option": "",
            "hint": f"italic {THEME.DIM}",
        }
    )

    app: Application[None] = Application(
        layout=layout,
        key_bindings=kb,
        style=style,
        full_screen=False,
    )

    await app.run_async()

    if not result_holder:
        return None
    return result_holder[0]
