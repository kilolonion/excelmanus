"""CLI 帮助页 — 分区帮助展示。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Console

from excelmanus.cli.commands import load_skill_command_rows
from excelmanus.cli.theme import THEME
from excelmanus.cli.utils import separator_line

if TYPE_CHECKING:
    from excelmanus.engine import AgentEngine


def render_help(
    console: Console,
    engine: "AgentEngine | None" = None,
    *,
    version: str = "",
) -> None:
    """渲染帮助页。

    布局：用 ─ 分隔线包围，Shortcuts + Commands 双列展示。
    """
    width = min(console.width, 70)
    sep = separator_line(width)

    title = "ExcelManus"
    if version:
        title += f" v{version}"

    console.print()
    console.print(f"  [{THEME.DIM}]{sep}[/{THEME.DIM}]")
    console.print(f"  [{THEME.BOLD} {THEME.PRIMARY}]{title}[/{THEME.BOLD} {THEME.PRIMARY}] · 帮助")
    console.print()

    # Shortcuts
    console.print(f"  [{THEME.BOLD}]Shortcuts[/{THEME.BOLD}]")
    shortcuts = [
        ("/ for commands", "exit to quit"),
        ("@ for mentions", "shift+tab auto-accept"),
        ("? for shortcuts", "ctrl+c to exit"),
    ]
    for left, right in shortcuts:
        console.print(
            f"  [{THEME.PRIMARY_LIGHT}]{left:<26}[/{THEME.PRIMARY_LIGHT}]"
            f"[{THEME.DIM}]{right}[/{THEME.DIM}]"
        )
    console.print()

    # Commands
    console.print(f"  [{THEME.BOLD}]Commands[/{THEME.BOLD}]")
    commands = [
        ("/help", "显示帮助", "/skills", "查看技能包"),
        ("/history", "对话历史摘要", "/model", "查看/切换模型"),
        ("/clear", "清除对话历史", "/mcp", "MCP Server 状态"),
        ("/save [路径]", "保存对话记录", "/config", "环境变量配置"),
        ("/subagent", "子代理控制", "/fullaccess", "权限控制"),
        ("/backup", "备份沙盒控制", "/plan", "计划模式"),
        ("/accept <id>", "确认操作", "/reject <id>", "拒绝操作"),
        ("/undo <id>", "回滚操作", "", ""),
    ]
    for row in commands:
        cmd1, desc1, cmd2, desc2 = row
        left = f"[{THEME.PRIMARY_LIGHT}]{cmd1:<16}[/{THEME.PRIMARY_LIGHT}][{THEME.DIM}]{desc1}[/{THEME.DIM}]"
        if cmd2:
            right = f"[{THEME.PRIMARY_LIGHT}]{cmd2:<16}[/{THEME.PRIMARY_LIGHT}][{THEME.DIM}]{desc2}[/{THEME.DIM}]"
        else:
            right = ""
        console.print(f"  {left}  {right}")

    # 技能命令
    skill_rows = load_skill_command_rows(engine) if engine is not None else []
    if skill_rows:
        console.print()
        console.print(f"  [{THEME.BOLD}]Skills[/{THEME.BOLD}]")
        for name, hint in skill_rows:
            hint_text = hint if hint else ""
            console.print(
                f"  [{THEME.PRIMARY_LIGHT}]/{name:<16}[/{THEME.PRIMARY_LIGHT}]"
                f"[{THEME.DIM}]{hint_text}[/{THEME.DIM}]"
            )

    console.print()
    console.print(f"  [{THEME.DIM}]{sep}[/{THEME.DIM}]")
    console.print()
    console.print(f"  [{THEME.DIM}]Esc to cancel[/{THEME.DIM}]")
    console.print()


