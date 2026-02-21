"""CLI 问题选择器 — Claude Code 风格内联式选择。

提供 › 光标导航的内联选择器，支持单选/多选、Other 文本输入。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from excelmanus.cli.theme import THEME

if TYPE_CHECKING:
    from excelmanus.approval import PendingQuestion

logger = logging.getLogger(__name__)

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


class InteractiveSelectResult:
    """交互式选择器的返回结果。"""

    def __init__(
        self,
        *,
        selected_indices: list[int] | None = None,
        other_text: str | None = None,
        escaped: bool = False,
    ) -> None:
        self.selected_indices = selected_indices or []
        self.other_text = other_text
        self.escaped = escaped


def build_answer_from_select(
    question: "PendingQuestion",
    result: InteractiveSelectResult,
) -> str:
    """将交互式选择结果转换为引擎可识别的回答文本。"""
    if result.other_text is not None:
        if question.multi_select:
            parts = [str(idx + 1) for idx in result.selected_indices]
            other_text = result.other_text.strip()
            if other_text:
                parts.append(other_text)
            return "\n".join(parts)
        return result.other_text

    if not result.selected_indices:
        return ""

    parts = [str(idx + 1) for idx in result.selected_indices]
    if question.multi_select:
        return "\n".join(parts)
    return parts[0]


def _is_interactive() -> bool:
    """判断当前终端是否支持交互式 UI。"""
    import sys
    return _PT_ENABLED and sys.stdin.isatty() and sys.stdout.isatty()


async def interactive_question_select(
    question: "PendingQuestion",
) -> InteractiveSelectResult | None:
    """Claude Code 风格内联问题选择器。

    单选：↑↓ 移动光标，Enter 确认。
    多选：↑↓ 移动光标，Space 切换选中，Enter 提交。
    Other 选项：选中后 Enter 进入文本输入。
    Esc：退出选择器。

    返回 None 表示不支持交互式选择。
    """
    if not _is_interactive():
        return None

    options = question.options
    if not options:
        return None

    multi = question.multi_select
    cursor = [0]
    checked: set[int] = set()
    result_holder: list[InteractiveSelectResult] = []

    kb = KeyBindings()

    @kb.add("up")
    def _move_up(event) -> None:  # type: ignore[no-untyped-def]
        cursor[0] = (cursor[0] - 1) % len(options)

    @kb.add("down")
    def _move_down(event) -> None:  # type: ignore[no-untyped-def]
        cursor[0] = (cursor[0] + 1) % len(options)

    @kb.add("space")
    def _toggle(event) -> None:  # type: ignore[no-untyped-def]
        if multi:
            idx = cursor[0]
            if options[idx].is_other:
                return
            if idx in checked:
                checked.discard(idx)
            else:
                checked.add(idx)

    @kb.add("enter")
    def _confirm(event) -> None:  # type: ignore[no-untyped-def]
        idx = cursor[0]
        opt = options[idx]
        if opt.is_other:
            result_holder.append(
                InteractiveSelectResult(
                    selected_indices=sorted(checked) if multi else [],
                    other_text="__NEED_INPUT__",
                )
            )
            event.app.exit()
            return
        if multi:
            if idx not in checked:
                checked.add(idx)
            result_holder.append(
                InteractiveSelectResult(selected_indices=sorted(checked))
            )
        else:
            result_holder.append(
                InteractiveSelectResult(selected_indices=[idx])
            )
        event.app.exit()

    @kb.add("escape")
    def _escape(event) -> None:  # type: ignore[no-untyped-def]
        result_holder.append(InteractiveSelectResult(escaped=True))
        event.app.exit()

    def _get_formatted_text() -> FormattedText:
        """生成 Claude Code 风格选择器文本。"""
        fragments: list[tuple[str, str]] = []
        header = question.header or "待确认"
        fragments.append(("class:header", f"  {THEME.AGENT_PREFIX} {header}\n"))
        fragments.append(("class:separator", f"  {'─' * 50}\n"))
        if question.text:
            fragments.append(("class:text", f"  {question.text}\n"))
        fragments.append(("", "\n"))

        for i, opt in enumerate(options):
            is_cursor = i == cursor[0]
            is_checked = i in checked

            if multi:
                marker = "◉" if is_checked else "○"
                prefix = f"  {THEME.CURSOR} {marker} " if is_cursor else f"    {marker} "
            else:
                prefix = f"  {THEME.CURSOR} " if is_cursor else "    "

            label = opt.label
            desc = f" {opt.description}" if opt.description else ""
            line = f"{prefix}{i + 1}. {label}{desc}\n"

            if is_cursor:
                style = "class:selected"
            elif is_checked:
                style = "class:checked"
            else:
                style = "class:option"
            fragments.append((style, line))

        fragments.append(("", "\n"))
        if multi:
            fragments.append(
                ("class:hint", "  ↑↓ 移动 · Space 选中 · Enter 提交 · Esc 取消\n")
            )
        else:
            fragments.append(
                ("class:hint", "  Esc to cancel · Tab to amend\n")
            )
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
            "checked": f"bold {THEME.PRIMARY_LIGHT}",
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
        return InteractiveSelectResult(escaped=True)

    result = result_holder[0]

    # 处理 Other 选项：需要文本输入
    if result.other_text == "__NEED_INPUT__":
        from excelmanus.cli.prompt import read_user_input
        from rich.console import Console
        _console = Console()
        _console.print(f"  [{THEME.DIM}]请输入自定义内容：[/{THEME.DIM}]")
        try:
            other_input = (await read_user_input()).strip()
        except (KeyboardInterrupt, EOFError):
            return InteractiveSelectResult(escaped=True)
        if not other_input:
            return InteractiveSelectResult(escaped=True)
        return InteractiveSelectResult(
            selected_indices=result.selected_indices,
            other_text=other_input,
        )

    return result
