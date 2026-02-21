"""CLI 欢迎横幅 — Claude Code 风格 + Excel 电子表格 ASCII art。"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from rich.console import Console
from rich.text import Text

from excelmanus.cli.theme import THEME
from excelmanus.cli.utils import separator_line

if TYPE_CHECKING:
    from excelmanus.config import ExcelManusConfig

# Excel 电子表格 ASCII art
_SPREADSHEET_ART = [
    "  ┌──┬──┬──┐",
    "  │A1│B1│C1│",
    "  ├──┼──┼──┤",
    "  │  │  │  │",
    "  └──┴──┴──┘",
]


def render_welcome(
    console: Console,
    config: "ExcelManusConfig",
    *,
    version: str = "",
    skill_count: int = 0,
    mcp_count: int = 0,
) -> None:
    """渲染 Claude Code 风格欢迎横幅。

    布局：
    ── ExcelManus vX.Y.Z ──────────────────────
         ┌──┬──┬──┐        Tips
         │A1│B1│C1│        输入自然语言指令即可开始
         ├──┼──┼──┤
         │  │  │  │        模型 · 工作目录
         └──┴──┴──┘
    ─────────────────────────────────────────────
    """
    width = min(console.width, 70)
    sep = separator_line(width)

    # 标题行
    title = f"ExcelManus"
    if version:
        title += f" v{version}"
    pad = width - len(title) - 4  # 4 = "── " + " "
    if pad < 4:
        pad = 4
    header = f"{THEME.SEPARATOR}{THEME.SEPARATOR} {title} " + THEME.SEPARATOR * pad

    console.print()
    console.print(f"  [{THEME.PRIMARY}]{header}[/{THEME.PRIMARY}]")

    # 左侧 ASCII art + 右侧 Tips
    model_name = getattr(config, "model", "unknown")
    workspace = os.path.abspath(getattr(config, "workspace_root", "."))
    # 缩短 home 目录
    home = os.path.expanduser("~")
    if workspace.startswith(home):
        workspace = "~" + workspace[len(home):]

    right_lines = [
        f"[{THEME.BOLD}]Tips[/{THEME.BOLD}]",
        f"[{THEME.DIM}]输入自然语言指令即可开始[/{THEME.DIM}]",
        f"[{THEME.DIM}]/help 查看命令  ? 快捷键[/{THEME.DIM}]",
        "",
        f"[{THEME.DIM}]{model_name}[/{THEME.DIM}]",
        f"[{THEME.DIM}]{workspace}[/{THEME.DIM}]",
    ]

    # 合并左右两栏
    art_width = 16  # ASCII art 占位宽度
    for i in range(max(len(_SPREADSHEET_ART), len(right_lines))):
        left = _SPREADSHEET_ART[i] if i < len(_SPREADSHEET_ART) else ""
        right = right_lines[i] if i < len(right_lines) else ""
        padding = " " * max(1, art_width - len(left))
        console.print(f"  [{THEME.PRIMARY_LIGHT}]{left}[/{THEME.PRIMARY_LIGHT}]{padding}{right}")

    # 底部分隔线
    console.print(f"  [{THEME.PRIMARY}]{sep}[/{THEME.PRIMARY}]")
    console.print()
