"""introspect_capability 工具：O(1) 查表的工具能力查询。

提供五种查询类型：
- tool_detail: 查询工具完整参数 schema + 权限 + 分类
- category_tools: 查询分类下所有工具列表
- can_i_do: 基于关键词匹配的能力判断（覆盖内置工具 + 扩展能力 + 子代理）
- related_tools: 查询相关工具推荐（同分类）
- system_status: 查询当前运行时状态（工具数/MCP/子代理等）

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

_ALL_QUERY_TYPES = ["tool_detail", "category_tools", "can_i_do", "related_tools", "system_status"]

INTROSPECT_CAPABILITY_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "query_type": {
            "type": "string",
            "enum": _ALL_QUERY_TYPES,
            "description": "查询类型（单条查询时使用）",
        },
        "query": {
            "type": "string",
            "description": "查询内容：工具名/分类名/能力描述（system_status 时可留空）",
        },
        "queries": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "query_type": {
                        "type": "string",
                        "enum": _ALL_QUERY_TYPES,
                    },
                    "query": {"type": "string"},
                },
                "required": ["query_type", "query"],
            },
            "description": "批量查询（一次传入多个查询，减少迭代次数）",
        },
    },
    "additionalProperties": False,
}

# ── can_i_do 匹配阈值与上限 ──────────────────────────────

_MATCH_THRESHOLD = 0.3
_MAX_RESULTS = 5

# ── 中文分词正则 ──────────────────────────────────────────

_TOKEN_RE = re.compile(r"[\u4e00-\u9fff]+|[a-zA-Z_][a-zA-Z0-9_]*")

# ── 扩展能力描述（run_code + Python 库能实现但无内置工具的能力）────

_EXTENDED_CAPABILITIES: dict[str, str] = {
    "pivot_table": "数据透视表：通过 run_code + pandas pivot_table() 计算并写入新 sheet（非原生 PivotTable 对象）",
    "chart": "图表：manage_spreadsheet_objects operations.kind=chart",
    "conditional_format": "条件格式：当前不可用。已有 xlsx 没有条件格式保存通路。",
    "data_validation": "数据验证：当前不可用。已有 xlsx 没有数据验证保存通路。",
    "merge_cells": "合并单元格：format_spreadsheet operations.kind=merge",
    "named_range": "命名范围：当前不可用。已有 xlsx 没有命名范围保存通路。",
    "freeze_panes": "冻结窗格：已有文件不可用。仅新建 WorkbookSpec 的 freeze_panes 子集。",
    "auto_filter": "自动筛选：当前不可用。已有 xlsx 没有 auto_filter 保存通路。",
    "page_setup": "页面设置：当前不可用。已有 xlsx 没有打印区域保存通路。",
    "cell_style": "单元格样式：format_spreadsheet operations.kind=format",
    "batch_write": "批量写入：edit_spreadsheet operations.kind=write；大表规约再用 run_code 调 SDK",
    "formula": "公式写入：edit_spreadsheet operations.kind=write",
    "dataframe": "数据分析：通过 run_code + pandas DataFrame 做复杂数据变换/统计/透视",
    "regex": "正则匹配/文本提取：通过 run_code + re 模块实现",
    "image_insert": "插入图片：当前不可用。已有 xlsx 没有图片保存通路。",
    "csv_json_convert": "CSV/JSON 转换：通过 run_code + pandas read_csv/to_csv/read_json/to_json",
    "multi_sheet_copy": "跨表复制：edit_spreadsheet kind=sheet action=copy，或 kind=copy 复制值。",
    "insert_rows_cols": "插入行列：edit_spreadsheet operations.kind=insert。有公式或图表的表会拒绝。",
}

# ── 子代理能力描述 ──────────────────────────────────────

_SUBAGENT_CAPABILITIES: dict[str, str] = {
    "explorer": "只读探索子代理：文件结构分析、数据预览与统计，不做任何写入",
    "subagent": "通用全能力子代理：工具域与主代理一致，适用于需要独立上下文的长任务",
}


# ── 工具常见错误与调用示例（按需查询，不注入每轮 schema） ───

_TOOL_COMMON_ERRORS: dict[str, list[str]] = {
    "inspect_spreadsheet": [
        "文件不存在：确认相对路径，用 mode=overview 或 list_directory 查找",
        "sheet 不存在：先 overview 确认实际 sheet 名称",
        "range 格式错误：A1:F20，或 表名!A1:F20；表名也可放在 sheet_name",
    ],
    "analyze_spreadsheet": [
        "filter 需要 column/operator/value 或 conditions",
        "profile/quality 需要 file_path",
    ],
    "run_code": [
        "缺少 try/except：代码必须包含顶层 try/except，错误 print 到 stderr",
        "超时：默认 120s，大数据操作可调大 timeout_seconds（最大 300）",
        "ModuleNotFoundError：仅支持沙箱内的库（openpyxl/pandas/numpy 等），不支持 pip install",
        "禁止调用：sys.exit()/exec()/eval()/os.system()",
    ],
    "write_text_file": [
        "文件已存在且 overwrite=false：默认 overwrite=true，显式传 false 时文件已存在会报错",
    ],
    "manage_spreadsheet_objects": [
        "chart 需要 chart_type 与 data_range；kind 写在 operations 项内",
        "图表类型仅支持 bar/line/pie/scatter/area（column 视为 bar）",
        "data_range 写 A1:B12，也接受 数据!A1:B12",
    ],
    "format_spreadsheet": [
        "range 写 A1:C5，也接受 区域汇总!A5:C5；多表请带 sheet",
        "kind=size 的 columns 是 {\"A\":18} 或 [18,12]",
        "缺 sheet 且 range 无表名时会 INVALID_ARGS",
    ],
}

_TOOL_USAGE_EXAMPLES: dict[str, str] = {
    "inspect_spreadsheet": '{"mode": "range", "file_path": "data.xlsx", "sheet_name": "Sheet1", "range": "A1:F20"}',
    "analyze_spreadsheet": '{"mode": "filter", "file_path": "data.xlsx", "column": "部门", "operator": "eq", "value": "销售部"}',
    "edit_spreadsheet": '{"file_path": "data.xlsx", "expected_version": "sha256:...", "operations": [{"kind": "write", "sheet": "Sheet1", "start_cell": "B2", "values": [[100]]}]}',
    "format_spreadsheet": '{"file_path": "data.xlsx", "expected_version": "sha256:...", "operations": [{"kind": "format", "sheet": "Sheet1", "range": "A1:B1", "font": {"bold": true}}]}',
    "run_code": '{"code": "from em import inspect_spreadsheet\\ntry:\\n    print(inspect_spreadsheet(mode=\\"overview\\", file_path=\\"data.xlsx\\"))\\nexcept Exception as e:\\n    import sys; print(e, file=sys.stderr)"}',
    "manage_spreadsheet_objects": '{"file_path": "data.xlsx", "expected_version": "sha256:...", "operations": [{"kind": "chart", "sheet": "Sheet1", "chart_type": "bar", "data_range": "B1:B20", "categories_range": "A2:A20"}]}',
}


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

    lines = [
        f"工具: {tool_name}",
        f"分类: {category}",
        f"权限: {permission}",
        f"描述: {desc}",
        f"\n参数 Schema:\n{schema_str}",
    ]

    errors = _TOOL_COMMON_ERRORS.get(tool_name)
    if errors:
        lines.append("\n常见错误:")
        for err in errors:
            lines.append(f"  - {err}")

    example = _TOOL_USAGE_EXAMPLES.get(tool_name)
    if example:
        lines.append(f"\n调用示例: {example}")

    return "\n".join(lines)


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
    """关键词匹配内置工具 + 扩展能力 + 子代理，返回匹配结果。"""
    keywords = _extract_keywords(description)
    if not keywords:
        return (
            "能力判断: 无直接工具支持\n"
            "建议: 委派 explorer 子代理做只读探查，"
            "或改用已有 inspect/analyze/edit SDK"
        )

    lines: list[str] = []

    # 层级 1：内置工具匹配
    scores: list[tuple[str, float]] = []
    for tool_name, tool_desc in TOOL_SHORT_DESCRIPTIONS.items():
        score = _compute_match_score(keywords, tool_desc)
        if score > 0:
            scores.append((tool_name, score))
    scores.sort(key=lambda x: x[1], reverse=True)
    top_builtin = [
        (name, s) for name, s in scores[:_MAX_RESULTS] if s >= _MATCH_THRESHOLD
    ]
    if top_builtin:
        lines.append("内置工具匹配:")
        for name, _s in top_builtin:
            desc = TOOL_SHORT_DESCRIPTIONS.get(name, "")
            lines.append(f"  - {name} — {desc}")

    # 层级 2：扩展能力匹配（run_code + Python 库）
    ext_scores: list[tuple[str, float]] = []
    for cap_name, cap_desc in _EXTENDED_CAPABILITIES.items():
        score = _compute_match_score(keywords, cap_desc)
        if score > 0:
            ext_scores.append((cap_name, score))
    ext_scores.sort(key=lambda x: x[1], reverse=True)
    top_ext = [
        (name, s) for name, s in ext_scores[:_MAX_RESULTS] if s >= _MATCH_THRESHOLD
    ]
    if top_ext:
        lines.append("扩展能力 (run_code + Python):")
        for name, _s in top_ext:
            lines.append(f"  - {_EXTENDED_CAPABILITIES[name]}")

    # 层级 3：子代理能力匹配
    sub_matches: list[str] = []
    for sub_name, sub_desc in _SUBAGENT_CAPABILITIES.items():
        score = _compute_match_score(keywords, sub_desc)
        if score >= _MATCH_THRESHOLD:
            sub_matches.append(f"  - {sub_name} — {sub_desc}")
    if sub_matches:
        lines.append("子代理:")
        lines.extend(sub_matches)

    # 层级 4：MCP 扩展工具匹配
    if _registry is not None:
        mcp_matches: list[str] = []
        for tool in _registry.get_all_tools():
            if not tool.name.startswith("mcp_"):
                continue
            score = _compute_match_score(keywords, tool.description or "")
            if score >= _MATCH_THRESHOLD:
                mcp_matches.append(f"  - {tool.name} — {tool.description}")
        if mcp_matches:
            lines.append("MCP 扩展工具:")
            lines.extend(mcp_matches[:_MAX_RESULTS])

    if lines:
        return "能力判断: 支持\n" + "\n".join(lines)

    return (
        "能力判断: 无直接工具支持\n"
        "建议: 委派 explorer 子代理做只读探查，"
        "或改用已有 inspect/analyze/edit SDK"
    )


def _handle_related_tools(tool_name: str) -> str:
    """基于 TOOL_CATEGORIES 同分类返回相关工具。"""
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

    if len(lines) == 1:
        lines.append("无相关工具推荐")

    return "\n".join(lines)


def _handle_system_status(_query: str = "") -> str:
    """返回当前运行时状态概览。"""
    assert _registry is not None

    all_tools = list(_registry.get_all_tools())
    builtin_count = sum(1 for t in all_tools if not t.name.startswith("mcp_"))
    mcp_tools = [t for t in all_tools if t.name.startswith("mcp_")]

    lines = [
        "系统状态概览:",
        f"  内置工具: {builtin_count}",
        f"  MCP 扩展工具: {len(mcp_tools)}",
        f"  工具分类: {', '.join(sorted(TOOL_CATEGORIES.keys()))}",
        f"  扩展能力 (run_code): {len(_EXTENDED_CAPABILITIES)} 项",
        f"  内置子代理: {', '.join(sorted(_SUBAGENT_CAPABILITIES.keys()))}",
    ]

    if mcp_tools:
        lines.append("  MCP 工具列表:")
        for t in mcp_tools[:15]:
            desc_short = (t.description or "")[:60]
            lines.append(f"    - {t.name} — {desc_short}")
        if len(mcp_tools) > 15:
            lines.append(f"    (+{len(mcp_tools) - 15} more)")

    return "\n".join(lines)


# ── 主函数 ────────────────────────────────────────────────


def introspect_capability(query_type: str = "", query: str = "", queries: list | None = None) -> str:
    """查询自身工具能力详情，用于决策时确认能力边界。

    支持单条查询（query_type + query）或批量查询（queries 数组）。
    批量查询时一次返回所有结果，减少迭代次数。

    查询类型：
    - tool_detail: 查工具完整参数 schema + 权限 + 分类
    - category_tools: 查分类下所有工具
    - can_i_do: 能力判断（搜索内置工具 + 扩展能力 + 子代理 + MCP）
    - related_tools: 同分类相关工具
    - system_status: 当前运行时状态概览

    Args:
        query_type: 查询类型（单条模式）
        query: 查询内容（单条模式）
        queries: 批量查询列表，每项含 query_type 和 query

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
        "system_status": _handle_system_status,
    }

    # 批量查询模式
    if queries is not None and isinstance(queries, list):
        results = []
        for i, q in enumerate(queries[:10], 1):  # 最多 10 条
            qt = q.get("query_type", "")
            qv = q.get("query", "")
            handler = handlers.get(qt)
            if handler is None:
                valid = ", ".join(sorted(handlers.keys()))
                results.append(f"[{i}] 不支持的查询类型: {qt}，可用类型: {valid}")
            else:
                result_text = handler(qv)
                results.append(f"[{i}] {qt}({qv}): {result_text}")
        sep = "\n\n"
        return sep.join(results) if results else "未提供有效查询"

    # 单条查询模式
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
            write_effect="none",
        )
    )
