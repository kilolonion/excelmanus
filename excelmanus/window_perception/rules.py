"""窗口感知层规则引擎。"""

from __future__ import annotations

from dataclasses import dataclass

from .models import WindowType
from .rule_registry import classify_tool_meta


@dataclass(frozen=True)
class ToolClassification:
    """工具分类结果。"""

    canonical_name: str
    window_type: WindowType | None



def classify_tool(tool_name: str) -> ToolClassification:
    """将工具归类到窗口类型。"""
    meta = classify_tool_meta(tool_name)
    return ToolClassification(
        canonical_name=meta.canonical_name,
        window_type=meta.window_type,
    )



def is_window_relevant_tool(tool_name: str) -> bool:
    """判断工具是否属于窗口感知范围。"""
    return classify_tool(tool_name).window_type is not None
