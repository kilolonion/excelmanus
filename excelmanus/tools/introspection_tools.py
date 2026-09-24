"""统一认知门户与当前授权工具的按需详情查询。

除工具详情查询外，knowledge_index/search/read 提供按需产品认知：
- tool_detail: 查询工具完整参数 schema + 权限 + 分类
- category_tools: 查询分类下所有工具列表
- can_i_do: 基于关键词匹配的能力判断（覆盖内置工具 + 扩展能力 + 子代理）
- related_tools: 查询相关工具推荐（同分类）
- system_status: 查询当前运行时状态（工具数/MCP/子代理等）

注册为 READ_ONLY_SAFE_TOOLS，不修改业务数据。成功详情仅更新本轮披露集合，不授予权限。
"""

from __future__ import annotations

import json
import re
from contextvars import ContextVar
from typing import Any

from excelmanus.tools.policy import (
    MUTATING_AUDIT_ONLY_TOOLS,
    MUTATING_CONFIRM_TOOLS,
    READ_ONLY_SAFE_TOOLS,
    TOOL_CATEGORIES,
    TOOL_INTENT_ROUTES,
    TOOL_SHORT_DESCRIPTIONS,
)
from excelmanus.tools.registry import ToolDef, ToolRegistry
from excelmanus.knowledge.portal import QUERY_TYPES as KNOWLEDGE_QUERY_TYPES
from excelmanus.knowledge.portal import SCOPES as KNOWLEDGE_SCOPES

# ── 模块级 registry / 有效目录 ─────────────────────────────────

_registry: ToolRegistry | None = None
_call_catalog: ContextVar[Any] = ContextVar("introspection_call_catalog", default=None)
_call_engine: ContextVar[Any] = ContextVar("introspection_call_engine", default=None)

# ── 工具 Schema ──────────────────────────────────────────

_ALL_QUERY_TYPES = ["tool_detail", "category_tools", "can_i_do", "related_tools", "system_status", *KNOWLEDGE_QUERY_TYPES]

_KNOWLEDGE_PARAMETERS = {
    "scope": {"type": "string", "enum": list(KNOWLEDGE_SCOPES), "description": "目录/搜索的来源过滤，默认 all。"},
    "limit": {"type": "integer", "minimum": 1, "maximum": 20, "description": "列表每页最多条数；还受响应大小限制。"},
    "ref": {"type": "string", "description": "knowledge_find 的目标引用；query 填要查找的字面文本。"},
    "line_start": {"type": "integer", "minimum": 1, "description": "knowledge_read 的起始行（1-based）。"},
    "line_end": {"type": "integer", "minimum": 1, "description": "knowledge_read 的结束行（含）。"},
    "content_revision": {"type": "string", "description": "搜索命中或引用给出的正文版本；照 next_call 传递。"},
    "language": {"type": "string", "enum": ["all", "python", "json"], "description": "规范/示例代码语言。"},
    "examples_only": {"type": "boolean", "description": "knowledge_spec 仅返回调用示例。"},
}

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
            "description": "search: 问题；read/toc/related: 返回的 ref，可带 #章节锚点；find: 字面查找文本并用 ref 指定文档；spec: 工具名；examples: 工具名或关键词；index/system_status 可留空。旧 tool_detail 支持工具名.字段路径。",
        },
        **_KNOWLEDGE_PARAMETERS,
        "page": {"type": "integer", "minimum": 1, "default": 1, "description": "知识门户页码；优先原样使用返回的 next_call。"},
        "revision": {"type": "string", "description": "门户 next_call 提供的内容版本；内容变化时返回 restart_call。"},
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
                    "page": {"type": "integer", "minimum": 1, "default": 1},
                    "revision": {"type": "string"},
                    **_KNOWLEDGE_PARAMETERS,
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

# 产品术语不是工具描述的子串。先用稳定的意图词表路由，再把词袋匹配
# 作为未知表达的兜底，避免“diff/选区/审批”这类短问句被误判为无能力。
_INTENT_TOOL_MAP: dict[str, tuple[str, ...]] = {
    **{phrase: names for phrases, names in TOOL_INTENT_ROUTES.items() for phrase in phrases.split("/")},
    "审批": ("run_shell", "delete_file"),
    "权限": ("introspect_capability",),
}

# ── 中文分词正则 ──────────────────────────────────────────

_TOKEN_RE = re.compile(r"[\u4e00-\u9fff]+|[a-zA-Z_][a-zA-Z0-9_]*")

# ── 扩展能力描述（run_code + Python 库能实现但无内置工具的能力）────

def bind_introspection_catalog(catalog: Any) -> None:
    """兼容测试：写入调用级 catalog，不再写模块全局。"""
    _call_catalog.set(catalog)


def _source_tools() -> dict[str, ToolDef]:
    """只信调用级 catalog；缺失则空表，不回退 registry 全表。"""
    catalog = _call_catalog.get()
    if catalog is not None:
        source = getattr(catalog, "introspection_source", None)
        if callable(source):
            raw = source()
            if isinstance(raw, dict):
                return {
                    str(name): tool
                    for name, tool in raw.items()
                    if isinstance(name, str) and name
                }
    return {}


def _tool_unavailable(tool_name: str) -> str:
    from difflib import get_close_matches

    names = sorted(_source_tools())
    candidates = get_close_matches(tool_name, names, n=5, cutoff=0.4) or names[:5]
    return (
        f"工具不可用: {tool_name}\n"
        "工具不存在于当前目录。建议使用 category_tools 或 can_i_do 查询可见能力。\n"
        f"当前可查询候选: {', '.join(candidates) or '无'}"
    )


def _short_desc(tool_name: str, tool: ToolDef | None = None) -> str:
    if tool is not None and tool.description:
        return str(tool.description)
    if tool_name in TOOL_SHORT_DESCRIPTIONS:
        return TOOL_SHORT_DESCRIPTIONS[tool_name]
    if tool is not None:
        return str(tool.description or "")
    return ""


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
    if tool_name.startswith("mcp_"):
        return "mcp"
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


def _schema_kind_enums(tool: ToolDef) -> dict[str, list[str]]:
    from excelmanus.tools.reference_contract import augment_reference_schema

    schema = augment_reference_schema(tool.input_schema) if isinstance(getattr(tool, "input_schema", None), dict) else {}
    props = schema.get("properties") or {}
    found: dict[str, list[str]] = {}
    mode_spec = props.get("mode")
    if isinstance(mode_spec, dict) and mode_spec.get("enum"):
        found["mode"] = [str(item) for item in mode_spec["enum"]]
    operations = props.get("operations")
    if isinstance(operations, dict):
        items = operations.get("items") if isinstance(operations.get("items"), dict) else {}
        branches = items.get("oneOf") or []
        if branches:
            found["operations.kind"] = [b["properties"]["kind"]["enum"][0] for b in branches if b.get("properties", {}).get("kind", {}).get("enum")]
        item_props = items.get("properties") if isinstance(items.get("properties"), dict) else {}
        kind_spec = item_props.get("kind")
        if isinstance(kind_spec, dict) and kind_spec.get("enum"):
            found["operations.kind"] = [str(item) for item in kind_spec["enum"]]
        found["operations.keys"] = [str(key) for key in item_props]
    return found


def _summarize_tool_schema(tool: ToolDef) -> str:
    from excelmanus.tools.reference_contract import augment_reference_schema

    schema = augment_reference_schema(tool.input_schema) if isinstance(getattr(tool, "input_schema", None), dict) else {}
    props = schema.get("properties") or {}
    required = schema.get("required") or []
    lines = [f"必填: {', '.join(str(item) for item in required) or '无'}"]
    enums = _schema_kind_enums(tool)
    if "mode" in enums:
        lines.append("mode: " + "|".join(enums["mode"]))
        lines.append("参数适用的 mode 见各字段说明；用 tool_detail 工具名.字段 查询当前 schema。")
    if "operations.kind" in enums:
        lines.append("operations.kind: " + "|".join(enums["operations.kind"]))
        keys = [k for k in enums.get("operations.keys", []) if k not in {"kind"}]
        if keys:
            lines.append("operations 键: " + ", ".join(keys[:16]))
    top = [key for key in props if key not in {"operations", "request"}]
    if top:
        lines.append("顶层参数: " + ", ".join(top[:16]))
    return "\n".join(lines)


def split_tool_query(query: str, source: dict[str, Any]) -> tuple[str, str]:
    """Prefer registered names, including plugin names containing dots."""
    name = next((name for name in sorted(source, key=len, reverse=True)
                 if query == name or query.startswith(name + ".")), None)
    if name is not None:
        return name, query[len(name):].lstrip(".")
    name, _, field = query.partition(".")
    return name, field


def _handle_tool_detail(tool_name: str, *, disclose: bool = True) -> str:
    """从当前有效目录获取 ToolDef。默认短摘要；点名字段才展开该节点。"""
    if str(tool_name).startswith("schema_v1_"):
        from excelmanus.tools.schema_registry import get_schema

        bundled = get_schema(str(tool_name))
        if bundled is None:
            return f"未知 schema 引用: {tool_name}；请重新查询对应工具的 tool_detail。"
        return (
            f"Schema 引用: {tool_name}\n"
            "这是当前进程的只读参数合同，不授予额外工具权限。\n"
            + json.dumps(bundled, ensure_ascii=False, indent=2, default=str)
        )
    source = _source_tools()
    tool_name, field_path = split_tool_query(tool_name, source)
    tool_def = source.get(tool_name)
    if tool_def is None:
        return _tool_unavailable(tool_name)

    def record_loaded() -> None:
        if disclose:
            _record_loaded_tool(tool_def)

    category = _find_category(tool_name) or "未分类"
    permission = _classify_permission(tool_name)
    desc = _short_desc(tool_name, tool_def)
    lines = [
        f"工具: {tool_name}",
    ]
    if not field_path:
        lines.extend([f"分类: {category}", f"权限: {permission}", f"描述: {desc}"])
        if tool_name == "preview_spreadsheet":
            from excelmanus.workbook.render_environment import renderer_capabilities
            lines.append("渲染运行环境: " + json.dumps(renderer_capabilities(), ensure_ascii=False))
    if field_path == "output" or field_path.startswith("output."):
        from excelmanus.tools.output_contracts import contract_summary, output_schema_for

        summary = contract_summary(tool_name, tool_def=tool_def)
        if summary is None:
            return f"未知输出合同: {tool_name}.output（未声明，不编造）"
        if field_path != "output":
            from excelmanus.tools.schema_walk import compact_node, walk_schema_path

            schema = output_schema_for(tool_name, tool_def=tool_def)
            assert schema is not None
            node, available, _ = walk_schema_path(schema, field_path.removeprefix("output."))
            if node is None:
                return f"未知输出字段: {tool_name}.{field_path}；可查字段: {', '.join(available)}"
            # The raw property preserves nullable / array types at the leaf.
            parent_path, _, leaf = field_path.removeprefix("output.").rpartition(".")
            parent, _, _ = walk_schema_path(schema, parent_path)
            raw = (parent or {}).get("properties", {}).get(leaf)
            lines.append("\n输出字段 schema:\n" + json.dumps(compact_node(raw if isinstance(raw, dict) else node), ensure_ascii=False))
            record_loaded()
            return "\n".join(lines)
        lines.append("\n输出合同:\n" + summary)
        record_loaded()
        return "\n".join(lines)
    if not field_path:
        from excelmanus.code_mode import _sdk_signature_line
        from excelmanus.tools.output_contracts import contract_summary
        from excelmanus.tools.catalog import _schema_ref_id
        from excelmanus.tools.reference_contract import augment_reference_schema

        lines.append("\n" + _summarize_tool_schema(tool_def))
        full_schema = augment_reference_schema(tool_def.input_schema) if isinstance(tool_def.input_schema, dict) else {}
        try:
            schema_size = len(json.dumps(full_schema, ensure_ascii=False, default=str))
        except (TypeError, ValueError):
            schema_size = 0
        if schema_size > 7500:
            lines.append(
                f"\nSchema 引用: {_schema_ref_id(tool_name, full_schema)}；"
                f"完整字段按 tool_detail {tool_name}.字段路径继续查询。"
            )
        if tool_name != "run_code":
            lines.append("\nPython SDK（import em）:\n- em." + _sdk_signature_line(tool_def).removeprefix("- "))
            lines.append("签名中的 ... 表示省略参数；None 仅在合同含 null 时表示显式空值。")
            lines.append("类型签名为速查；oneOf/allOf、嵌套必填及其他约束请查具体参数 schema。")
        summary = contract_summary(tool_name, tool_def=tool_def)
        lines.append("\n输出合同:\n" + (summary or "未声明，不推断返回类型；按实际返回值处理。"))
        lines.append("可用 tool_detail 查询 工具名.字段 缩小范围；不要把 A1 语法整段当参数。")
    else:
        from excelmanus.tools.reference_contract import augment_reference_schema

        schema = augment_reference_schema(tool_def.input_schema) if isinstance(tool_def.input_schema, dict) else {}
        from excelmanus.tools.schema_walk import compact_node, walk_schema_path
        from excelmanus.workbook.refs import describe_for_schema

        if tool_name == "apply_spreadsheet_changes" and field_path.startswith("operations."):
            from excelmanus.workbook.contracts import OPERATION_SCHEMAS
            path = field_path.removeprefix("operations.")
            if path == "kind":
                record_loaded()
                return json.dumps({"type": "string", "enum": list(OPERATION_SCHEMAS)}, ensure_ascii=False)
            kind = next((key for key in sorted(OPERATION_SCHEMAS, key=len, reverse=True) if path == key or path.startswith(key + ".")), None)
            if kind:
                operation = OPERATION_SCHEMAS[kind]
                suffix = path[len(kind):].lstrip(".")
                node, available, _ = walk_schema_path(operation, suffix) if suffix else (operation, [], None)
                if node is None:
                    return f"字段不存在；可查字段: {available}"
                record_loaded()
                return f"参数 {field_path}:\n" + json.dumps(node, ensure_ascii=False)

        node, available, _err = walk_schema_path(schema, field_path)
        if node is None:
            available_text = ", ".join(available) if available else "（无嵌套字段）"
            return (
                f"字段不存在: {tool_name}.{field_path}；当前可查字段: {available_text}"
            )
        # walker 为继续向下走会解包 nullable/array。详情保留终点的原始
        # schema，避免把 array|string、anyOf|null 误披露为单一分支。
        parent_path, _, final_key = field_path.rpartition(".")
        parent = schema
        if parent_path:
            parent, _, _ = walk_schema_path(schema, parent_path)
        props = parent.get("properties", {}) if isinstance(parent, dict) else {}
        raw_node = props.get(final_key) if isinstance(props, dict) else None
        if parent_path in {"$defs", "definitions"}:
            # A named nullable/union definition is itself the raw endpoint.
            # It is not a property of the unwrapped definition container.
            raw_node = (schema.get(parent_path) or {}).get(final_key)
        if isinstance(raw_node, dict) and "$ref" in raw_node:
            from excelmanus.tools.schema_walk import resolve_local_ref

            resolved = resolve_local_ref(schema, raw_node["$ref"])
            if resolved:
                raw_node = {**resolved, **{key: value for key, value in raw_node.items() if key != "$ref"}}
        compact = compact_node(raw_node if isinstance(raw_node, dict) else node)
        if isinstance(raw_node, dict):
            for key in ("anyOf", "oneOf", "allOf", "not", "const"):
                if key in raw_node:
                    compact[key] = raw_node[key]
        schema_str = json.dumps(compact, ensure_ascii=False, separators=(",", ":"), default=str)
        lines.append(f"\n参数 {field_path}:\n{schema_str}")
        if available:
            lines.append("当前节点可查: " + ", ".join(available))
        if tool_name == "apply_spreadsheet_changes" and field_path == "workbook_spec":
            from excelmanus.tools.workbook_examples import workbook_creation_example

            lines.append("可执行示例（workbook_spec 值）:\n" + json.dumps(
                workbook_creation_example(), ensure_ascii=False, separators=(",", ":"),
            ))
            lines.append("uncertainties 非空时每项必填 location/reason；类型定义可查 apply_spreadsheet_changes.$defs.StyleClass。")
        tail = field_path.rsplit(".", 1)[-1]
        if tail in {"range", "start_cell", "source_range", "target_start", "cell_range"}:
            hint = describe_for_schema()
            lines.append(
                "引用语法：" + hint["syntax"]
                + f" 示例：{hint['examples']}。"
                + hint["common_errors"]
            )
            execution = hint.get("execution") or ""
            if execution:
                lines.append(execution)

    record_loaded()
    return "\n".join(lines)


def _record_loaded_tool(tool: ToolDef) -> None:
    """仅在成功详情路径记录授权目录的可信标识，不解析模型可见文本。"""
    from excelmanus.tools.context import current_call

    ctx = current_call()
    if ctx is not None and ctx.loaded_tool_names is not None:
        ctx.loaded_tool_names.add(tool.name)


def _handle_category_tools(category: str) -> str:
    """查 TOOL_CATEGORIES，只返回当前目录里有的工具。"""
    tools = TOOL_CATEGORIES.get(category)
    if tools is None:
        all_cats = ", ".join(sorted(TOOL_CATEGORIES.keys()))
        return (
            f"分类不存在: {category}\n"
            f"可用分类: {all_cats}"
        )

    source = _source_tools()
    if category == "mcp":
        tools = tuple(name for name in source if name.startswith("mcp_"))
    elif category == "other":
        tools = tuple(name for name in source if _find_category(name) is None)
    lines = [f"分类: {category}"]
    listed = 0
    for tool_name in tools:
        tool = source.get(tool_name)
        if tool is None:
            continue
        desc = _short_desc(tool_name, tool)
        permission = _classify_permission(tool_name)
        lines.append(f"  - {permission} {tool_name} — {desc}")
        listed += 1
    if listed == 0:
        lines.append("  （当前目录下该分类无可用工具）")
    return "\n".join(lines)








def _handle_can_i_do(description: str) -> str:
    """Suggest tools; a language search miss is never a capability denial."""
    source = _source_tools()
    query = description.lower()
    if description in source:
        return "能力判断: available\n" + description + ": " + _short_desc(description, source[description])
    candidates = []
    for phrases, names in TOOL_INTENT_ROUTES.items():
        if any(phrase.lower() in query for phrase in phrases.split("/")):
            candidates.extend(name for name in names if name in source)
    candidates = list(dict.fromkeys(candidates))
    if not candidates:
        from excelmanus.knowledge.documents import match_score

        scored = [(match_score(description, name + " " + _short_desc(name, tool)), name)
                  for name, tool in source.items()]
        candidates = [name for score, name in sorted(scored, key=lambda item: (-item[0], item[1]))
                      if score >= _MATCH_THRESHOLD][:_MAX_RESULTS]
    if candidates:
        return "能力判断: available（候选工具，具体输入和引擎支持见合同）\n" + "\n".join(f"- {name}: {_short_desc(name, source[name])}" for name in candidates)
    return ("能力判断: unknown（未匹配表达，不表示能力不存在）\n"
            "可用 knowledge_search 搜索产品说明，knowledge_index 查看统一认知目录。\n当前授权目录: "
            + ", ".join(sorted(source)))


def _handle_related_tools(tool_name: str) -> str:
    """基于 TOOL_CATEGORIES 同分类返回当前目录里的相关工具。"""
    source = _source_tools()
    lines = [f"相关工具: {tool_name}"]

    category = _find_category(tool_name)
    if category:
        siblings = [
            t for t in TOOL_CATEGORIES[category]
            if t != tool_name and t in source
        ]
        if siblings:
            lines.append(f"\n同分类 ({category}):")
            for name in siblings:
                lines.append(f"  - {name} — {_short_desc(name, source.get(name))}")

    if len(lines) == 1:
        lines.append("无相关工具推荐")

    return "\n".join(lines)


def _handle_system_status(_query: str = "") -> str:
    """返回当前有效目录的运行时状态概览。"""
    source = _source_tools()
    all_tools = list(source.values())
    builtin_count = sum(1 for t in all_tools if not t.name.startswith("mcp_"))
    mcp_tools = [t for t in all_tools if t.name.startswith("mcp_")]

    lines = [
        "系统状态概览:",
        f"  内置工具: {builtin_count}",
        f"  MCP 扩展工具: {len(mcp_tools)}",
        f"  工具分类: {', '.join(sorted(TOOL_CATEGORIES.keys()))}",
    ]
    catalog = _call_catalog.get()
    if catalog is not None:
        lines.append(f"  当前目录模式: {catalog.mode}；目录版本: {catalog.digest()}")
        lines.append(catalog.tool_index_text())
    if "run_shell" in source:
        import platform
        import shutil
        from excelmanus.tools.shell_tools import ALLOWED_COMMANDS

        installed = [name for name in sorted(ALLOWED_COMMANDS) if shutil.which(name)]
        lines.append(f"  主机: {platform.system()}；当前安装的白名单可执行文件: {', '.join(installed) or '无'}")

    if "run_code" in source or "run_shell" in source:
        from excelmanus.runtime_capabilities import environment_text
        from excelmanus.tools.context import call_has_full_access

        lines.append(environment_text(full_access=call_has_full_access()))

    if mcp_tools:
        lines.append("  MCP 工具列表:")
        for t in mcp_tools:
            desc_short = (t.description or "")[:60]
            lines.append(f"    - {t.name} — {desc_short}")

    return "\n".join(lines)


# ── 主函数 ────────────────────────────────────────────────


def introspect_capability(query_type: str = "", query: str = "", queries: list | None = None,
                          page: int = 1, revision: str = "", scope: str = "all", limit: int = 8,
                          ref: str = "", line_start: int | None = None, line_end: int | None = None,
                          content_revision: str = "", language: str = "all", examples_only: bool = False) -> str:
    """查询自身工具能力详情，用于决策时确认能力边界。

    支持单条查询（query_type + query）或批量查询（queries 数组）。
    批量查询时一次返回所有结果，减少迭代次数。

    查询类型：
    - tool_detail: 查工具完整参数 schema + 权限 + 分类
    - category_tools: 查分类下所有工具
    - can_i_do: 能力判断（搜索内置工具 + 扩展能力 + 子代理 + MCP）
    - related_tools: 同分类相关工具
    - system_status: 当前运行时状态概览
    - knowledge_index / knowledge_search / knowledge_read: 统一产品认知目录、搜索与分页阅读

    Args:
        query_type: 查询类型（单条模式）
        query: 查询内容（单条模式）
        queries: 批量查询列表，每项含 query_type 和 query
        page: 知识资源的页码
        revision: 分页响应给出的内容版本

    Returns:
        结构化的查询结果文本（始终非空）
    """
    if _registry is None and _call_catalog.get() is None:
        return "工具注册表尚未初始化"

    handlers = {
        "tool_detail": _handle_tool_detail,
        "category_tools": _handle_category_tools,
        "can_i_do": _handle_can_i_do,
        "related_tools": _handle_related_tools,
        "system_status": _handle_system_status,
    }

    def dispatch(qt: str, qv: str, page_number: int = 1, result_revision: str = "", **options: Any) -> str:
        if qt in KNOWLEDGE_QUERY_TYPES:
            from excelmanus.knowledge.portal import query_knowledge

            return query_knowledge(qt, qv, catalog=_call_catalog.get(), engine=_call_engine.get(),
                                   page=page_number, revision=result_revision, **options)
        handler = handlers.get(qt)
        if handler is None:
            return f"不支持的查询类型: {qt}，可用类型: {', '.join(_ALL_QUERY_TYPES)}"
        return handler(qv)

    # 批量查询模式
    if queries is not None and isinstance(queries, list):
        results = []
        for i, q in enumerate(queries[:10], 1):  # 最多 10 条
            if not isinstance(q, dict) or not isinstance(q.get("query_type", ""), str) or not isinstance(q.get("query", ""), str):
                results.append(f"[{i}] 无效查询：每项需要字符串 query_type 与 query")
                continue
            qt = q.get("query_type", "")
            qv = q.get("query", "")
            result_text = dispatch(qt, qv, q.get("page", 1), q.get("revision", ""),
                                   **{key: q[key] for key in _KNOWLEDGE_PARAMETERS if key in q})
            results.append(f"[{i}] {qt}({qv}): {result_text}")
        sep = "\n\n"
        return sep.join(results) if results else "未提供有效查询"

    # 单条查询模式
    return dispatch(query_type, query, page, revision, scope=scope, limit=limit, ref=ref,
                    line_start=line_start, line_end=line_end, content_revision=content_revision,
                    language=language, examples_only=examples_only)


# ── 注册函数 ──────────────────────────────────────────────


def register_introspection_tools(registry: ToolRegistry, *, engine: Any = None) -> None:
    """将 introspect_capability 注册到工具注册表。"""
    global _registry
    _registry = registry

    def query_bound(query_type: str = "", query: str = "", queries: list | None = None,
                    page: int = 1, revision: str = "", scope: str = "all", limit: int = 8,
                    ref: str = "", line_start: int | None = None, line_end: int | None = None,
                    content_revision: str = "", language: str = "all", examples_only: bool = False) -> str:
        token = _call_engine.set(engine)
        try:
            return introspect_capability(query_type, query, queries, page, revision, scope, limit,
                                         ref, line_start, line_end, content_revision, language, examples_only)
        finally:
            _call_engine.reset(token)

    registry.register_tool(
        ToolDef(
            name="introspect_capability",
            description=(
                "ExcelManus 统一认知入口。需要了解产品能力、工作流程、设计、配置、权限或错误时，"
                "用 knowledge_index 浏览、knowledge_search 搜索后 knowledge_read 取准确正文；按 next_call 继续。"
                "knowledge_toc 查章节，knowledge_find 文档内查找，knowledge_related 看关联；knowledge_spec 读工具规范，knowledge_examples 查 JSON/Python 示例。"
                "工具和配置来自当前会话，文档随版本发布；查询只读，无需启用自我管理。"
                "普通内置工具与 MCP 扩展工具均可能按需隐藏；先 can_i_do 按能力搜索、category_tools 按分类查找，或 system_status 查看完整授权目录。"
                "tool_detail 返回 Python SDK 签名与输出合同；成功查询后下一步可直接调用该工具。"
                "tool_detail 支持工具名、嵌套字段、$defs 类型和 output；字段缺失时按返回的可查询字段继续。"
            ),
            input_schema=INTROSPECT_CAPABILITY_SCHEMA,
            func=query_bound,
            write_effect="none",
            max_result_chars=0,
        )
    )
