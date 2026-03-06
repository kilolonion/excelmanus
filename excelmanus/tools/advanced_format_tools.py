"""高级格式工具：已由 run_code 替代，仅保留 get_tools() 空壳。"""

from __future__ import annotations

from excelmanus.tools.registry import ToolDef


def get_tools() -> list[ToolDef]:
    """返回高级格式工具定义。

    Batch 3 精简：全部 9 个高级格式化工具已删除，由 run_code 替代。
    """
    return []
