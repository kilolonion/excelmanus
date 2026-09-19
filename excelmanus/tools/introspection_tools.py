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

# ── 模块级 registry / 有效目录 ─────────────────────────────────

_registry: ToolRegistry | None = None
_call_catalog: ContextVar[Any] = ContextVar("introspection_call_catalog", default=None)

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
            "description": "工具名/分类/能力描述。tool_detail 写 工具名 或 工具名.字段路径，如 edit_spreadsheet.workbook_spec.sheets.styles.border。字段缺失时返回当前节点可查询字段。system_status 可留空",
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

_EXTENDED_CAPABILITIES: dict[str, str] = {
    "pivot_table": "数据透视表：analyze_spreadsheet mode=pivot 只读；edit_spreadsheet kind=pivot 写入目标表（非原生 PivotTable 对象）",
    "chart": "图表：manage_spreadsheet_objects operations.kind=chart",
    "conditional_format": "条件格式：format_spreadsheet operations.kind=conditional_format（rule.type 支持 cell_value/text/formula/duplicate/unique/top_n/bottom_n/color_scale/data_bar/icon_set，命中样式用 rule.font/fill/border）；workbook_spec 建簿时可用 sheets[].conditional_formats",
    "data_validation": "数据验证/下拉框：format_spreadsheet operations.kind=data_validation（rule.type=list 配 values 数组或 formula1 引用；数值/日期/时间区间用 operator+value/value2；remove=true 删除）",
    "merge_cells": "合并单元格：format_spreadsheet operations.kind=merge",
    "named_range": "命名范围：当前不可用。已有 xlsx 没有命名范围保存通路。",
    "auto_filter": "自动筛选：当前不可用。已有 xlsx 没有 auto_filter 保存通路。",
    "page_setup": "页面设置：当前不可用。已有 xlsx 没有打印区域保存通路。",
    "cell_style": "单元格样式：format_spreadsheet operations.kind=format",
    "batch_write": "批量写入：edit_spreadsheet operations.kind=write；大表规约再用 run_code 调 SDK",
    "formula": "公式写入：edit_spreadsheet operations.kind=write",
    "dataframe": "数据分析：run_code + pandas 处理 SDK 返回的数据；工作区表格写入仍走 em.*",
    "regex": "正则匹配/文本提取：通过 run_code + re 模块实现",
    "image_insert": "插入图片：当前不可用。已有 xlsx 没有图片保存通路。",
    "csv_json_convert": "CSV/JSON 转换：通过 run_code + pandas read_csv/to_csv/read_json/to_json（不要 to_excel 写工作区）",
    "multi_sheet_copy": "跨表复制：edit_spreadsheet kind=sheet action=copy，或 kind=copy 复制值。",
    "insert_rows_cols": "插入行列：edit_spreadsheet operations.kind=insert。有公式或图表的表会拒绝。",
    "delete_rows_cols": "删除行列：edit_spreadsheet operations.kind=delete_rows 或 delete_columns。at 从 1 起。有公式或图表的表会拒绝。",
}

# ── 子代理能力描述 ──────────────────────────────────────

_SUBAGENT_CAPABILITIES: dict[str, str] = {
    "explorer": "只读探索子代理：文件结构分析、数据预览与统计，不做任何写入",
    "subagent": "通用全能力子代理：工具域与主代理一致，适用于需要独立上下文的长任务",
}


# ── 工具常见错误与调用示例（按需查询，不注入每轮 schema） ───

_TOOL_COMMON_ERRORS: dict[str, list[str]] = {
    "inspect_spreadsheet": [
        "文件不存在：核对相对路径拼写；任务允许查找时用 list_directory 或 analyze_spreadsheet(mode=files)；不要擅自换另一份表",
        "sheet 不存在：先 overview 确认实际 sheet 名称",
        "range 格式错误：A1:F20，或 表名!A1:F20；表名也可放在 sheet_name",
    ],
    "analyze_spreadsheet": [
        "mode 枚举：profile|quality|filter|aggregate|distinct|pivot|relationships|files；overview/range/search 属于 inspect_spreadsheet",
        "filter 需要 column/operator/value 或 conditions",
        "profile/quality 需要 file_path",
        "多表省略 sheet 时：只有一张可见表，或筛选/分组/聚合列只在唯一一张可见数据表全量出现，会自动绑定并在 resolved_sheet + warnings 显式声明；隐藏表列命中碰撞或其他歧义仍 SHEET_REQUIRED，需按 available_sheets 显式指定。别名、表单表、空表不参与列匹配",
    ],
    "compare_spreadsheets": [
        "diff/差异对比需要两个文件，或同一文件中的两个不同工作表；它不读取 Git diff 文件",
        "alignment=position 不能同时提供 key_columns；按键对齐请明确使用 alignment=key",
    ],
    "run_code": [
        "缺少 try/except：代码必须包含顶层 try/except，错误 print 到 stderr",
        "超时：默认 900s，可调大 timeout_seconds（最大 1800）；CPU 限额仍最多 300s",
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
    "edit_spreadsheet": [
        "workbook_spec 只用于创建新文件；sheets[] 每项必须含 dimensions={\"rows\":N,\"cols\":M}，value_blocks[].start 是字符串单元格（如 \"A1\"）",
        "workbook_spec 顶层必须带 uncertainties（无不确定项传 []）",
        "已有文件编辑用 operations；expected_version 必须精确等于当前 content_version",
    ],
    "format_spreadsheet": [
        "range 写 A1:C5 或整列 B:B；单表可省略 sheet，多表请带 sheet 或 表!A1",
        "kind=size 的 columns 是 {\"A\":18} 或 [18,12]，可 auto_fit=true；不要用 width",
        "kind=freeze 用 freeze_panes=A2 冻结首行",
        "kind=data_validation: rule.type=list 配 values 数组；数值类配 operator+value/value2；remove=true 删除",
        "多表缺 sheet 且 range 无表名时 SHEET_REQUIRED（含 available_sheets）；单表自动绑定",
    ],
    "split_spreadsheet": [
        "by_column 必填（按哪列拆，如 '省份'）；文件名模板用 {key}/{stem} 占位",
        "目标文件已存在则整批取消（VERSION_CONFLICT）：换 output_dir 或先清理旧文件",
        "拆分组数超过 max_files（默认 50）会拒绝：改用更粗粒度列或显式调大上限",
    ],
}

_TOOL_USAGE_EXAMPLES: dict[str, str] = {
    "inspect_spreadsheet": '{"mode": "range", "file_path": "data.xlsx", "sheet_name": "Sheet1", "range": "A1:F20"}',
    "analyze_spreadsheet": '{"mode": "filter", "file_path": "data.xlsx", "column": "部门", "operator": "eq", "value": "销售部"}',
    "edit_spreadsheet": (
        '编辑: {"file_path": "data.xlsx", "expected_version": "sha256:...", "operations": [{"kind": "write", "sheet": "Sheet1", "start_cell": "B2", "values": [[100]]}]}'
        ' 创建: {"file_path": "new.xlsx", "workbook_spec": {"sheets": [{"name": "S1", "dimensions": {"rows": 3, "cols": 2}, "value_blocks": [{"start": "A1", "values": [["h1","h2"],[1,2]]}]}], "uncertainties": []}}'
    ),
    "format_spreadsheet": '{"file_path": "data.xlsx", "expected_version": "sha256:...", "operations": [{"kind": "format", "sheet": "Sheet1", "range": "A1:B1", "font": {"bold": true}}]}',
    "run_code": '{"code": "from em import inspect_spreadsheet\\ntry:\\n    print(inspect_spreadsheet(mode=\\"overview\\", file_path=\\"data.xlsx\\"))\\nexcept Exception as e:\\n    import sys; print(e, file=sys.stderr)"}',
    "manage_spreadsheet_objects": '{"file_path": "data.xlsx", "expected_version": "sha256:...", "operations": [{"kind": "chart", "sheet": "Sheet1", "chart_type": "bar", "data_range": "B1:B20", "categories_range": "A2:A20"}]}',
    "split_spreadsheet": '{"file_path": "orders.xlsx", "by_column": "省份", "output_dir": "outputs", "filename_template": "{stem}_{key}"}',
}

# operations.<kind> 不是 schema 属性，walk 必失败；按 kind 枚举合成速查。
_FORMAT_KIND_ALIASES: dict[str, str] = {
    "conditionalformat": "conditional_format",
    "cf": "conditional_format",
    "datavalidation": "data_validation",
    "dv": "data_validation",
}
_FORMAT_KIND_CHEATSHEETS: dict[str, str] = {
    "format": (
        "kind=format 形状：range（A1:K1 或 表!A1:K1）+ font/fill/border/alignment/number_format。"
        "sheet 或地址里的 表!A1；单表可省略 sheet。"
        "示例：{\"kind\":\"format\",\"sheet\":\"销售额汇总\",\"range\":\"A1:K1\",\"font\":{\"bold\":true}}"
    ),
    "size": (
        "kind=size 形状：columns/rows 字典（{\"A\":18}）或 auto_fit=true；不要用 width。"
        "sheet 必填，除非单表自动绑定；range 可选。"
        "示例：{\"kind\":\"size\",\"sheet\":\"区域月度汇总\",\"columns\":{\"A\":18,\"B\":12}}"
    ),
    "freeze": (
        "kind=freeze 形状：freeze_panes=A2 冻结首行（空字符串取消）。"
        "sheet 必填，除非单表自动绑定；range 可选。"
        "示例：{\"kind\":\"freeze\",\"sheet\":\"Sheet1\",\"freeze_panes\":\"A2\"}"
    ),
    "merge": (
        "kind=merge 形状：range 一个矩形（不要并集）。sheet 或 表!A1。"
        "示例：{\"kind\":\"merge\",\"sheet\":\"Sheet1\",\"range\":\"A1:B1\"}"
    ),
    "unmerge": (
        "kind=unmerge 形状：range 一个矩形。sheet 或 表!A1。"
        "示例：{\"kind\":\"unmerge\",\"sheet\":\"Sheet1\",\"range\":\"A1:B1\"}"
    ),
    "conditional_format": (
        "kind=conditional_format 形状：range + rule（type/operator/value/font/fill）；remove=true 删除相交规则。"
        "sheet 或 表!A1。"
        "示例：{\"kind\":\"conditional_format\",\"sheet\":\"Sheet1\",\"range\":\"A2:A20\",\"rule\":{\"type\":\"cell_value\",\"operator\":\"greaterThan\",\"value\":0}}"
    ),
    "data_validation": (
        "kind=data_validation 形状：range + rule（type=list 配 values，或 operator+value/value2）；remove=true 删除。"
        "sheet 或 表!A1。"
        "示例：{\"kind\":\"data_validation\",\"sheet\":\"Sheet1\",\"range\":\"A2:A10\",\"rule\":{\"type\":\"list\",\"values\":[\"是\",\"否\"]}}"
    ),
}


def _format_kind_from_query(field_path: str, kind_enum: list[str]) -> str | None:
    """format_spreadsheet.operations.size / format_spreadsheet.freeze → kind 名。"""
    tokens = [part for part in str(field_path or "").split(".") if part]
    if not tokens:
        return None
    if tokens[0] == "operations" and len(tokens) >= 2:
        token = tokens[1]
    elif len(tokens) == 1:
        token = tokens[0]
    else:
        return None
    kind = _FORMAT_KIND_ALIASES.get(token, token)
    if kind not in _FORMAT_KIND_CHEATSHEETS:
        return None
    allowed = set(kind_enum) if kind_enum else set(_FORMAT_KIND_CHEATSHEETS)
    if kind in allowed or token in allowed:
        return kind
    return None


def _synthesize_format_kind_detail(kind: str) -> str:
    body = _FORMAT_KIND_CHEATSHEETS.get(kind) or ""
    return f"参数 operations.{kind}（kind 枚举，不是 schema 字段）:\n{body}"


# ── 辅助函数 ──────────────────────────────────────────────


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
    return (
        f"工具不可用: {tool_name}\n"
        "工具不存在于当前目录。建议使用 category_tools 或 can_i_do 查询可见能力。"
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
    schema = tool.input_schema if isinstance(getattr(tool, "input_schema", None), dict) else {}
    props = schema.get("properties") or {}
    found: dict[str, list[str]] = {}
    mode_spec = props.get("mode")
    if isinstance(mode_spec, dict) and mode_spec.get("enum"):
        found["mode"] = [str(item) for item in mode_spec["enum"]]
    operations = props.get("operations")
    if isinstance(operations, dict):
        items = operations.get("items") if isinstance(operations.get("items"), dict) else {}
        item_props = items.get("properties") if isinstance(items.get("properties"), dict) else {}
        kind_spec = item_props.get("kind")
        if isinstance(kind_spec, dict) and kind_spec.get("enum"):
            found["operations.kind"] = [str(item) for item in kind_spec["enum"]]
        found["operations.keys"] = [str(key) for key in item_props]
    return found


def _summarize_tool_schema(tool: ToolDef) -> str:
    schema = tool.input_schema if isinstance(getattr(tool, "input_schema", None), dict) else {}
    props = schema.get("properties") or {}
    required = schema.get("required") or []
    lines = [f"必填: {', '.join(str(item) for item in required) or '无'}"]
    enums = _schema_kind_enums(tool)
    if "mode" in enums:
        lines.append("mode: " + "|".join(enums["mode"]))
    if "operations.kind" in enums:
        lines.append("operations.kind: " + "|".join(enums["operations.kind"]))
        keys = [k for k in enums.get("operations.keys", []) if k not in {"kind"}]
        if keys:
            lines.append("operations 键: " + ", ".join(keys[:16]))
    top = [key for key in props if key not in {"operations", "request"}]
    if top:
        lines.append("顶层参数: " + ", ".join(top[:16]))
    return "\n".join(lines)


def _handle_tool_detail(tool_name: str) -> str:
    """从当前有效目录获取 ToolDef。默认短摘要；点名字段才展开该节点。"""
    tool_name, _, field_path = tool_name.partition(".")
    source = _source_tools()
    tool_def = source.get(tool_name)
    if tool_def is None:
        return _tool_unavailable(tool_name)

    category = _find_category(tool_name) or "未分类"
    permission = _classify_permission(tool_name)
    desc = _short_desc(tool_name, tool_def)
    lines = [
        f"工具: {tool_name}",
        f"分类: {category}",
        f"权限: {permission}",
        f"描述: {desc}",
    ]
    if field_path == "output" or field_path.startswith("output."):
        from excelmanus.tools.output_contracts import contract_summary

        summary = contract_summary(tool_name)
        if summary is None:
            return f"未知输出合同: {tool_name}.output（未声明，不编造）"
        if field_path != "output":
            return (
                f"未知输出字段: {tool_name}.{field_path}；"
                "只声明顶层键，请查 工具名.output"
            )
        lines.append("\n输出合同:\n" + summary)
        return "\n".join(lines)
    if not field_path:
        lines.append("\n" + _summarize_tool_schema(tool_def))
        lines.append("可用 tool_detail 查询 工具名.字段 缩小范围；不要把 A1 语法整段当参数。")
    else:
        schema = tool_def.input_schema if isinstance(tool_def.input_schema, dict) else {}
        from excelmanus.tools.schema_walk import compact_node, walk_schema_path
        from excelmanus.workbook.refs import describe_for_schema

        kind_enum = _schema_kind_enums(tool_def).get("operations.kind") or []
        if tool_name == "format_spreadsheet":
            kind = _format_kind_from_query(field_path, kind_enum)
            if kind:
                lines.append("\n" + _synthesize_format_kind_detail(kind))
                errors = _TOOL_COMMON_ERRORS.get(tool_name)
                if errors:
                    lines.append("\n常见错误:")
                    for err in errors:
                        lines.append(f"  - {err}")
                example = _TOOL_USAGE_EXAMPLES.get(tool_name)
                if example:
                    lines.append(f"\n调用示例: {example}")
                return "\n".join(lines)

        node, available, _err = walk_schema_path(schema, field_path)
        if node is None:
            available_text = ", ".join(available) if available else "（无嵌套字段）"
            return (
                f"字段不存在: {tool_name}.{field_path}；当前可查字段: {available_text}"
            )
        compact = compact_node(node)
        schema_str = json.dumps(compact, ensure_ascii=False, separators=(",", ":"), default=str)
        lines.append(f"\n参数 {field_path}:\n{schema_str}")
        if available:
            lines.append("当前节点可查: " + ", ".join(available))
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
    """查 TOOL_CATEGORIES，只返回当前目录里有的工具。"""
    tools = TOOL_CATEGORIES.get(category)
    if tools is None:
        all_cats = ", ".join(sorted(TOOL_CATEGORIES.keys()))
        return (
            f"分类不存在: {category}\n"
            f"可用分类: {all_cats}"
        )

    source = _source_tools()
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


def _capability_available(cap_desc: str, visible: set[str]) -> bool:
    """扩展能力若点名了不在当前目录的工具，则不推荐。"""
    mentioned = [name for name in TOOL_SHORT_DESCRIPTIONS if name in cap_desc]
    if not mentioned:
        return "run_code" in visible
    return all(name in visible for name in mentioned)


def _schema_route_hits(query: str, source: dict[str, ToolDef]) -> list[str]:
    text = str(query or "")
    lowered = text.lower()
    hits: list[str] = []
    for name, tool in source.items():
        enums = _schema_kind_enums(tool)
        kinds = set(enums.get("operations.kind") or [])
        modes = set(enums.get("mode") or [])
        props = ((tool.input_schema or {}).get("properties") or {}) if isinstance(tool.input_schema, dict) else {}
        op_keys = set(enums.get("operations.keys") or [])
        if any(token in text for token in ("冻结", "冻结窗格", "首行")) or "freeze" in lowered:
            if "freeze" in kinds:
                hits.append(f"{name} kind=freeze（freeze_panes=A2 冻结首行）")
        if any(token in text for token in ("自适应", "列宽", "auto_fit")) or "autofit" in lowered.replace("_", ""):
            if "size" in kinds or "auto_fit" in op_keys:
                hits.append(f"{name} kind=size auto_fit")
        if any(token in text for token in ("透视", "pivot")):
            if "pivot" in modes:
                hits.append(f"{name} mode=pivot")
            if "pivot" in kinds:
                hits.append(f"{name} kind=pivot")
        if any(token in text for token in ("去重", "拆列", "清洗", "归一", "transform")):
            if "transform" in kinds:
                hits.append(f"{name} kind=transform")
        if any(token in text for token in ("验证", "下拉", "validation", "dropdown")):
            if "data_validation" in kinds:
                hits.append(f"{name} kind=data_validation")
        if "auto_fit" in props and "auto_fit" in lowered:
            hits.append(f"{name} auto_fit")
    # 去重保序
    seen: set[str] = set()
    ordered: list[str] = []
    for item in hits:
        if item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return ordered


def _handle_can_i_do(description: str) -> str:
    """判决只有「可见工具可做」或「当前不可用」。从 ToolDef schema 推导 kind。"""
    keywords = _extract_keywords(description)
    if not keywords:
        return (
            "能力判断: 当前不可用\n"
            "建议: 委派 explorer 子代理做只读探查，"
            "或改用已有 inspect/analyze/edit SDK"
        )

    source = _source_tools()
    visible = set(source)
    lines: list[str] = []
    normalized = str(description or "").strip().lower()

    unavailable = [
        desc for name, desc in _EXTENDED_CAPABILITIES.items()
        if "当前不可用" in desc and (
            name in normalized or desc.split("：", 1)[0] in normalized
        )
    ]
    if unavailable:
        return "能力判断: 当前不可用\n" + "\n".join(unavailable)

    schema_hits = _schema_route_hits(description, source)
    if schema_hits:
        lines.append("可见工具:")
        lines.extend(f"  - {item}" for item in schema_hits[:_MAX_RESULTS])
        return "能力判断: 可见工具可做\n" + "\n".join(lines)

    if any(word in normalized for word in ("审批", "权限", "approval", "permission")):
        confirm = sorted(visible & MUTATING_CONFIRM_TOOLS)
        audited = sorted(visible & MUTATING_AUDIT_ONLY_TOOLS)
        return (
            "能力判断: 可见工具可做\n"
            "审批由执行策略决定，批准不能使不合法参数或不存在的命令变得可执行。\n"
            f"当前目录需确认的工具（策略为 ask 时）：{', '.join(confirm) or '无'}\n"
            f"常规本地操作只审计：{', '.join(audited) or '无'}"
        )
    intent_hits: list[str] = []
    for phrase, names in _INTENT_TOOL_MAP.items():
        if phrase.lower() not in normalized:
            continue
        for name in names:
            if name in visible and name not in intent_hits:
                intent_hits.append(name)
    if intent_hits:
        lines.append("产品意图匹配:")
        for name in intent_hits[:_MAX_RESULTS]:
            lines.append(f"  - {name} — {_short_desc(name, source.get(name))}")
        return "能力判断: 可见工具可做\n" + "\n".join(lines)

    scores: list[tuple[str, float]] = []
    for tool_name, tool in source.items():
        desc = _short_desc(tool_name, tool)
        score = _compute_match_score(keywords, desc)
        if score > 0:
            scores.append((tool_name, score))
    scores.sort(key=lambda x: x[1], reverse=True)
    top_builtin = [
        (name, s) for name, s in scores[:_MAX_RESULTS] if s >= _MATCH_THRESHOLD
    ]
    if top_builtin:
        lines.append("内置工具匹配:")
        for name, _s in top_builtin:
            tool = source.get(name)
            lines.append(f"  - {name} — {_short_desc(name, tool)}")
        return "能力判断: 可见工具可做\n" + "\n".join(lines)

    ext_scores: list[tuple[str, float]] = []
    for cap_name, cap_desc in _EXTENDED_CAPABILITIES.items():
        if "当前不可用" in cap_desc:
            continue
        if not _capability_available(cap_desc, visible):
            continue
        score = _compute_match_score(keywords, cap_desc)
        if score > 0:
            ext_scores.append((cap_name, score))
    ext_scores.sort(key=lambda x: x[1], reverse=True)
    top_ext = [
        (name, s) for name, s in ext_scores[:_MAX_RESULTS] if s >= _MATCH_THRESHOLD
    ]
    if top_ext:
        lines.append("可见工具 / 纯内存计算:")
        for name, _s in top_ext:
            lines.append(f"  - {_EXTENDED_CAPABILITIES[name]}")
        return "能力判断: 可见工具可做\n" + "\n".join(lines)

    return (
        "能力判断: 当前不可用\n"
        "建议: 委派 explorer 子代理做只读探查，"
        "或改用已有 inspect/analyze/edit SDK"
    )


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
        f"  扩展能力 (run_code): {len(_EXTENDED_CAPABILITIES)} 项",
        f"  内置子代理: {', '.join(sorted(_SUBAGENT_CAPABILITIES.keys()))}",
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
    if _registry is None and _call_catalog.get() is None:
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
            description=(
                "查询自身工具能力详情，用于决策时确认能力边界。"
                "tool_detail 写工具名或顶层参数；嵌套无 JSON properties 时读返回说明并停，"
                "不要连查 WorkbookSpec 样式字段。"
            ),
            input_schema=INTROSPECT_CAPABILITY_SCHEMA,
            func=introspect_capability,
            write_effect="none",
            max_result_chars=0,
        )
    )
