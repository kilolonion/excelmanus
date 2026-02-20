"""introspect_capability 工具：O(1) 查表的工具能力查询。

提供四种查询类型：
- tool_detail: 查询工具完整参数 schema + 权限 + 分类
- category_tools: 查询分类下所有工具列表
- can_i_do: 基于关键词匹配的能力判断
- related_tools: 查询相关工具推荐（同分类 + 预定义组合）

注册为 READ_ONLY_SAFE_TOOLS，纯查询无副作用。
"""

from __future__ import annotations

import json
import re

from excelmanus.tools.policy import (
    MUTATING_AUDIT_ONLY_TOOLS,
    MUTATING_CONFIRM_TOOLS,
    READ_ONLY_SAFE_TOOLS,
    TOOL_CATEGORIES,
    TOOL_SHORT_DESCRIPTIONS,
)
from excelmanus.tools.registry import ToolDef, ToolRegistry

# ── 模块级 registry 引用 ─────────────────────────────────

_registry: ToolRegistry | None = None

# ── 工具 Schema ──────────────────────────────────────────

INTROSPECT_CAPABILITY_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "query_type": {
            "type": "string",
            "enum": ["tool_detail", "category_tools", "can_i_do", "related_tools"],
            "description": "查询类型",
        },
        "query": {
            "type": "string",
            "description": "查询内容：工具名/分类名/能力描述",
        },
    },
    "required": ["query_type", "query"],
    "additionalProperties": False,
}

# ── 预定义工具组合 ────────────────────────────────────────

TOOL_COMBINATIONS: dict[str, list[str]] = {
    "write_excel": ["read_excel", "format_cells", "adjust_column_width"],
    "create_excel_chart": ["read_excel", "format_cells"],
    "format_cells": ["read_cell_styles", "merge_cells", "adjust_column_width"],
    "transform_data": ["read_excel", "analyze_data", "filter_data"],
}

# ── can_i_do 匹配阈值与上限 ──────────────────────────────

_MATCH_THRESHOLD = 0.3
_MAX_RESULTS = 5

# ── 中文分词正则 ──────────────────────────────────────────

_TOKEN_RE = re.compile(r"[\u4e00-\u9fff]+|[a-zA-Z_][a-zA-Z0-9_]*")


# ── 辅助函数 ──────────────────────────────────────────────


def _classify_permission(tool_name: str) -> str:
    """返回工具的权限级别文本描述。"""
    if tool_name in READ_ONLY_SAFE_TOOLS:
        return "🟢 只读安全"
    if tool_name in MUTATING_CONFIRM_TOOLS:
        return "🔴 需确认 (Tier A)"
    if tool_name in MUTATING_AUDIT_ONLY_TOOLS:
        return "🟡 审计记录 (Tier B)"
    return "🟡 审计记录"


def _find_category(tool_name: str) -> str | None:
    """查找工具所属分类，未找到返回 None。"""
    for cat, tools in TOOL_CATEGORIES.items():
        if tool_name in tools:
            return cat
    return None


def _extract_keywords(text: str) -> list[str]:
    """从文本中提取关键词（中文词组 + 英文标识符）。"""
    return _TOKEN_RE.findall(text.lower())


def _compute_match_score(keywords: list[str], tool_desc: str) -> float:
    """计算关键词与工具描述的匹配分数。

    分数 = 匹配关键词数 / 总关键词数。
    """
    if not keywords:
        return 0.0
    desc_lower = tool_desc.lower()
    matched = sum(1 for kw in keywords if kw in desc_lower)
    return matched / len(keywords)


# ── Handler 函数 ──────────────────────────────────────────


def _handle_tool_detail(tool_name: str) -> str:
    """从 ToolRegistry 获取 ToolDef，提取完整参数 schema + 权限 + 分类。"""
    assert _registry is not None

    tool_def = _registry.get_tool(tool_name)
    if tool_def is None:
        return (
            f"工具不存在: {tool_name}\n"
            "建议使用 category_tools 查询浏览可用工具分类。"
        )

    category = _find_category(tool_name) or "未分类"
    permission = _classify_permission(tool_name)
    desc = TOOL_SHORT_DESCRIPTIONS.get(tool_name, tool_def.description)
    schema_str = json.dumps(tool_def.input_schema, ensure_ascii=False, indent=2)

    # 查找预定义组合
    combos = TOOL_COMBINATIONS.get(tool_name, [])
    combo_line = f"\n常见组合工具: {', '.join(combos)}" if combos else ""

    return (
        f"工具: {tool_name}\n"
        f"分类: {category}\n"
        f"权限: {permission}\n"
        f"描述: {desc}\n\n"
        f"参数 Schema:\n{schema_str}"
        f"{combo_line}"
    )


def _handle_category_tools(category: str) -> str:
    """查 TOOL_CATEGORIES 返回分类下所有工具及描述。"""
    tools = TOOL_CATEGORIES.get(category)
    if tools is None:
        all_cats = ", ".join(sorted(TOOL_CATEGORIES.keys()))
        return (
            f"分类不存在: {category}\n"
            f"可用分类: {all_cats}"
        )

    lines = [f"分类: {category}"]
    for tool_name in tools:
        desc = TOOL_SHORT_DESCRIPTIONS.get(tool_name, "")
        permission = _classify_permission(tool_name)
        lines.append(f"  - {permission} {tool_name} — {desc}")
    return "\n".join(lines)


def _handle_can_i_do(description: str) -> str:
    """关键词匹配 TOOL_SHORT_DESCRIPTIONS，返回匹配工具或建议委派。"""
    keywords = _extract_keywords(description)
    if not keywords:
        return (
            "能力判断: 无直接工具支持\n"
            "建议: 委派 introspector 子代理深入分析，"
            "或考虑使用 run_code 通过 Python 实现"
        )

    scores: list[tuple[str, float]] = []
    for tool_name, tool_desc in TOOL_SHORT_DESCRIPTIONS.items():
        score = _compute_match_score(keywords, tool_desc)
        if score > 0:
            scores.append((tool_name, score))

    # 按分数降序排列
    scores.sort(key=lambda x: x[1], reverse=True)

    top_matches = [
        (name, s) for name, s in scores[:_MAX_RESULTS] if s >= _MATCH_THRESHOLD
    ]

    if top_matches:
        lines = ["能力判断: 支持", "匹配工具:"]
        for name, score in top_matches:
            desc = TOOL_SHORT_DESCRIPTIONS.get(name, "")
            lines.append(f"  - {name} — {desc}")
        return "\n".join(lines)

    return (
        "能力判断: 无直接工具支持\n"
        "建议: 委派 introspector 子代理深入分析，"
        "或考虑使用 run_code 通过 Python 实现"
    )


def _handle_related_tools(tool_name: str) -> str:
    """基于 TOOL_CATEGORIES 同分类 + TOOL_COMBINATIONS 返回相关工具。"""
    lines = [f"相关工具: {tool_name}"]

    # 同分类工具
    category = _find_category(tool_name)
    if category:
        siblings = [t for t in TOOL_CATEGORIES[category] if t != tool_name]
        if siblings:
            lines.append(f"\n同分类 ({category}):")
            for t in siblings:
                desc = TOOL_SHORT_DESCRIPTIONS.get(t, "")
                lines.append(f"  - {t} — {desc}")

    # 预定义组合
    combos = TOOL_COMBINATIONS.get(tool_name, [])
    if combos:
        lines.append("\n预定义组合:")
        for t in combos:
            desc = TOOL_SHORT_DESCRIPTIONS.get(t, "")
            lines.append(f"  - {t} — {desc}")

    if len(lines) == 1:
        lines.append("无相关工具推荐")

    return "\n".join(lines)


# ── 主函数 ────────────────────────────────────────────────


def introspect_capability(query_type: str, query: str) -> str:
    """查询自身工具能力详情，用于决策时确认能力边界。

    Args:
        query_type: 查询类型（tool_detail/category_tools/can_i_do/related_tools）
        query: 查询内容（工具名/分类名/能力描述）

    Returns:
        结构化的查询结果文本（始终非空）
    """
    if _registry is None:
        return "工具注册表尚未初始化"

    handlers = {
        "tool_detail": _handle_tool_detail,
        "category_tools": _handle_category_tools,
        "can_i_do": _handle_can_i_do,
        "related_tools": _handle_related_tools,
    }

    handler = handlers.get(query_type)
    if handler is None:
        valid = ", ".join(sorted(handlers.keys()))
        return f"不支持的查询类型: {query_type}，可用类型: {valid}"

    return handler(query)


# ── 注册函数 ──────────────────────────────────────────────


def register_introspection_tools(registry: ToolRegistry) -> None:
    """将 introspect_capability 注册到工具注册表。"""
    global _registry
    _registry = registry

    registry.register_tool(
        ToolDef(
            name="introspect_capability",
            description="查询自身工具能力详情，用于决策时确认能力边界。",
            input_schema=INTROSPECT_CAPABILITY_SCHEMA,
            func=introspect_capability,
        )
    )
