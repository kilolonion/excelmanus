"""CLI 配色主题 — Excel 绿色系亮色主题 + 极简风格符号。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Theme:
    """CLI 配色与符号常量。

    主色取自 Microsoft Excel 品牌色板（绿色系），
    符号采用极简交互风格。
    """

    # 主色 — Excel 绿色系
    PRIMARY: str = "#217346"
    PRIMARY_LIGHT: str = "#33a867"
    ACCENT: str = "#107c41"

    # 辅助色
    CYAN: str = "#0078d4"
    GOLD: str = "#e5a100"
    RED: str = "#d13438"
    GREEN_DIFF: str = "#c6efce"
    RED_DIFF: str = "#ffc7ce"

    # 文本样式关键字（用于 Rich markup）
    DIM: str = "dim"
    BOLD: str = "bold"

    # 极简风格符号
    USER_PREFIX: str = "›"
    AGENT_PREFIX: str = "●"
    SEPARATOR: str = "─"
    TREE_MID: str = "├"
    TREE_END: str = "└"
    CURSOR: str = "›"
    SUCCESS: str = "✓"
    FAILURE: str = "✗"


# 全局单例
THEME = Theme()
