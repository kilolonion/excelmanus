"""引用关系图摘要。默认路径不注入；仅供调试/测试读取。"""

from __future__ import annotations

from typing import Any


def build_ref_graph_notice(cache: Any) -> str:
    """从 RefCache 构建引用关系图文本。"""
    all_tier1 = cache.all_tier1() if hasattr(cache, "all_tier1") else {}
    if not all_tier1:
        return ""
    summaries: list[str] = []
    for _fp, index in all_tier1.items():
        text = index.render_summary()
        if text:
            summaries.append(text)
    if not summaries:
        return ""
    return "### 引用关系图\n" + "\n".join(summaries)
