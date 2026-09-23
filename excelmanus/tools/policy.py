"""工具策略单一事实源（SSOT）。

集中维护：
1. 写入类工具的审批/审计分层；
2. 子代理默认工具域；
3. 审计目标路径映射与工作区扫描预算；
4. fallback 路由下的只读发现工具。
"""

from __future__ import annotations

# 常驻 schema 是面向表格任务的明确产品默认，不是调用频率统计或授权名单。
# 其余内置/MCP 能力通过 introspect_capability 按需披露；执行目录不裁剪。
DEFAULT_DISCLOSURE_CORE_TOOLS: frozenset[str] = frozenset({
    "inspect_spreadsheet", "analyze_spreadsheet", "edit_spreadsheet", "format_spreadsheet",
    "calculate_spreadsheet", "render_spreadsheet", "convert_spreadsheet",
    "validate_spreadsheet", "query_spreadsheet",
    "list_directory", "read_text_file", "read_image", "run_code", "introspect_capability",
    "ask_user", "show_workbook", "offer_download", "task_create", "task_update", "sleep", "skill",
    "write_plan", "exit_plan_mode",
})

# ── 只读安全白名单（低风险） ───────────────────────────────

# 仅显式白名单中的工具在只读模式下可直接执行。
# 默认模式下的确认/审计行为由写入分层（Tier A/Tier B）决定。
READ_ONLY_SAFE_TOOLS: frozenset[str] = frozenset(
    {
        "inspect_spreadsheet",
        "analyze_spreadsheet",
        "validate_spreadsheet",
        "compare_spreadsheets",
        "trace_spreadsheet_formulas",
        "read_word",
        "inspect_word",
        "search_word",
        "read_text_file",
        "list_directory",
        "memory_read_topic",
        "task_create",
        "task_update",
        "introspect_capability",
        "inspect_agent",
        "read_image",
        "parallel_search",
    }
)

# ── 可并行执行的只读工具 ──────────────────────────────────────
# READ_ONLY_SAFE_TOOLS 的子集，排除有特殊调度路径的元工具
# （task_create 有 plan 拦截、task_update 有 task list 事件、introspect_capability 极少出现）。
# 同一轮次中依赖已满足的只读调用按执行波次和配置的并发上限执行。
PARALLELIZABLE_READONLY_TOOLS: frozenset[str] = frozenset(
    {
        "inspect_spreadsheet",
        "analyze_spreadsheet",
        "validate_spreadsheet",
        "compare_spreadsheets",
        "trace_spreadsheet_formulas",
        "read_word",
        "inspect_word",
        "search_word",
        "read_text_file",
        "list_directory",
        "memory_read_topic",
        "read_image",
        "introspect_capability",
        "parallel_search",
    }
)

if not PARALLELIZABLE_READONLY_TOOLS <= READ_ONLY_SAFE_TOOLS:
    raise AssertionError("PARALLELIZABLE_READONLY_TOOLS 必须是 READ_ONLY_SAFE_TOOLS 子集")

# ── 写入类工具分层 ──────────────────────────────────────────

# Tier A：需要进入 /accept 门禁确认后才能执行
MUTATING_CONFIRM_TOOLS: frozenset[str] = frozenset(
    {
        "run_shell",
        "delete_file",
        # Skill installation/uninstallation changes the session's executable
        # surface and must use the same approval gate as other destructive
        # capabilities.  action=list is narrowed to read-only below.
        "manage_skills",
    }
)

# Tier B：不拦截确认，但必须纳入审计
MUTATING_AUDIT_ONLY_TOOLS: frozenset[str] = frozenset(
    {
        # 文本文件操作（沙盒守卫，低风险）
        "write_text_file",
        "edit_text_file",
        "rename_file",
        "copy_file",
        "write_word",
        "edit_spreadsheet",
        "calculate_spreadsheet", "render_spreadsheet", "convert_spreadsheet", "query_spreadsheet",
        "format_spreadsheet",
        "split_spreadsheet",
        "manage_spreadsheet_objects",
        "manage_spreadsheet_versions",
    }
)

MUTATING_ALL_TOOLS: frozenset[str] = MUTATING_CONFIRM_TOOLS | MUTATING_AUDIT_ONLY_TOOLS

if not MUTATING_CONFIRM_TOOLS.issubset(MUTATING_ALL_TOOLS):
    raise AssertionError("MUTATING_CONFIRM_TOOLS 必须是 MUTATING_ALL_TOOLS 子集")
if MUTATING_CONFIRM_TOOLS & MUTATING_AUDIT_ONLY_TOOLS:
    raise AssertionError("MUTATING_CONFIRM_TOOLS 与 MUTATING_AUDIT_ONLY_TOOLS 不允许交集")
if READ_ONLY_SAFE_TOOLS & MUTATING_ALL_TOOLS:
    raise AssertionError("READ_ONLY_SAFE_TOOLS 不允许包含写入工具")


# 同一工具内按 action 覆盖 write_effect。未列出的 action 沿用 ToolDef 声明。
READONLY_TOOL_ACTIONS: dict[str, frozenset[str]] = {
    "manage_spreadsheet_versions": frozenset({"list"}),
    # 技能目录查询只读；install/uninstall 沿用宿主 ToolDef 的写效应。
    "manage_skills": frozenset({"list"}),
}


# 目录可见性：无 args 时，这些声明视为写效应（与执行器 RESTRICTED_WRITE_EFFECTS 对齐）。
MUTATING_WRITE_EFFECTS: frozenset[str] = frozenset(
    {
        "workspace_write",
        "external_write",
        "dynamic",
        "unknown",
    }
)


def normalize_write_effect(declared: str | None) -> str:
    """空值 / 未识别声明回退 unknown（fail-closed）。"""
    effect = str(declared or "unknown").strip().lower()
    if effect in {"none", "workspace_write", "external_write", "dynamic", "unknown"}:
        return effect
    return "unknown"


def is_mutating_write_effect(declared: str | None) -> bool:
    """Catalog 级写判定：只看 ToolDef.write_effect，不看 per-call action。"""
    return normalize_write_effect(declared) in MUTATING_WRITE_EFFECTS

def has_readonly_action(tool_name: str) -> bool:
    """该工具是否至少声明了一个只读 action（供目录可见性判断）。"""
    return bool(READONLY_TOOL_ACTIONS.get(tool_name))


def is_catalog_visible(tool_name: str, declared: str | None) -> bool:
    """目录可见性（read/plan）：无写效应，或该工具含只读 action。

    执行期写拦截仍由 write_effect_for_call 按 action 判定，两者互不冲突。
    """
    if not is_mutating_write_effect(declared):
        return True
    return has_readonly_action(tool_name)


def write_effect_for_call(
    tool_name: str,
    args: dict[str, object] | None = None,
    *,
    declared: str = "unknown",
    actions: dict[str, object] | None = None,
) -> str:
    """Per-tool 声明 + per-action 覆盖（ToolDef.actions 优先）。"""
    action = ""
    if isinstance(args, dict):
        raw = args.get("action")
        if raw is not None:
            action = str(raw).strip().lower()
    specs = actions if isinstance(actions, dict) else None
    if specs and action:
        spec = specs.get(action)
        if spec is not None:
            effect = spec.get("write_effect") if isinstance(spec, dict) else getattr(spec, "write_effect", None)
            if effect is not None:
                return normalize_write_effect(str(effect))
    allowed = READONLY_TOOL_ACTIONS.get(tool_name)
    if allowed is not None and action in allowed:
        return "none"
    return normalize_write_effect(declared)


# ── 审计目标路径映射（SSOT） ───────────────────────────────

# mode=all：提取所有非空字段作为目标文件
AUDIT_TARGET_ARG_RULES_ALL: dict[str, tuple[str, ...]] = {
    "write_text_file": ("file_path",),
    "edit_text_file": ("file_path",),
    "copy_file": ("destination",),
    "rename_file": ("source", "destination"),
    "write_word": ("file_path",),
    "delete_file": ("file_path",),
    "edit_spreadsheet": ("file_path",),
    "format_spreadsheet": ("file_path",),
    "split_spreadsheet": ("file_path", "output_dir"),
    "manage_spreadsheet_objects": ("file_path",),
    "manage_spreadsheet_versions": ("file_path",),
    "calculate_spreadsheet": ("file_path", "output_path"),
    "render_spreadsheet": ("output_path",),
    "convert_spreadsheet": ("output_path",),
    "query_spreadsheet": ("output_path",),
    "manage_skills": ("skill",),
}

# mode=first：按字段优先级提取第一个非空路径
AUDIT_TARGET_ARG_RULES_FIRST: dict[str, tuple[str, ...]] = {}

# run_code 使用动态策略引擎分级，不在静态 CONFIRM/AUDIT 集合中
CODE_POLICY_DYNAMIC_TOOLS: frozenset[str] = frozenset({"run_code"})


def is_concurrency_safe(
    tool_name: str,
    args: dict[str, object] | None = None,
    *,
    extra_safe: frozenset[str] | None = None,
) -> bool:
    """滚动池分类器。只有确切 True 才可并行；缺省 / 抛错 / False → 独占。

    写入与 ``run_code`` 永远独占，含 Code Mode 子调用。
    ``args`` 预留给按参数重分类；当前与工具名集合一致。
    """
    try:
        if not tool_name:
            return False
        if tool_name in MUTATING_ALL_TOOLS or tool_name in CODE_POLICY_DYNAMIC_TOOLS:
            return False
        # MCP 默认独占。autoApprove / 默认放行不授予并行。
        if tool_name.startswith("mcp_"):
            return False
        if extra_safe and tool_name in extra_safe:
            return True
        return tool_name in PARALLELIZABLE_READONLY_TOOLS
    except Exception:
        return False


_PATH_RULED_TOOLS = set(AUDIT_TARGET_ARG_RULES_ALL) | set(AUDIT_TARGET_ARG_RULES_FIRST)
_EXPECTED_PATH_RULED_TOOLS = set(MUTATING_ALL_TOOLS) - {"run_code", "run_shell"}
if _PATH_RULED_TOOLS != _EXPECTED_PATH_RULED_TOOLS:
    missing = sorted(_EXPECTED_PATH_RULED_TOOLS - _PATH_RULED_TOOLS)
    extra = sorted(_PATH_RULED_TOOLS - _EXPECTED_PATH_RULED_TOOLS)
    raise AssertionError(
        f"审计路径映射不完整或存在冗余：missing={missing}, extra={extra}"
    )


# ── 工作区补偿审计预算（run_code/run_shell） ───────────────

WORKSPACE_SCAN_MAX_FILES: int = 20000
WORKSPACE_SCAN_MAX_HASH_BYTES: int = 256 * 1024 * 1024
WORKSPACE_SCAN_EXCLUDE_PREFIXES: tuple[str, ...] = (
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "dist",
    "build",
    ".worktrees",
    "outputs",
    "scripts",
    ".tmp",
)


# ── 工具分类映射（用于工具索引） ────

TOOL_CATEGORIES: dict[str, tuple[str, ...]] = {
    "inspect": ("inspect_spreadsheet",),
    "analyze": ("analyze_spreadsheet",),
    "compare": ("compare_spreadsheets",),
    "edit": ("edit_spreadsheet",),
    "format": ("format_spreadsheet",),
    "split": ("split_spreadsheet",),
    "objects": ("manage_spreadsheet_objects",),
    "spreadsheet_engine": ("calculate_spreadsheet", "render_spreadsheet", "convert_spreadsheet", "query_spreadsheet", "validate_spreadsheet"),
    "formula_trace": ("trace_spreadsheet_formulas",),
    "versions": ("manage_spreadsheet_versions",),
    "word": ("read_word", "inspect_word", "search_word", "write_word"),
    "file": (
        "read_text_file", "list_directory", "copy_file", "rename_file", "delete_file",
    ),
    "code": ("write_text_file", "edit_text_file", "run_code", "run_shell"),
    "vision": ("read_image",),
    "interaction": ("ask_user", "show_workbook", "offer_download"),
    "skills": ("skill", "manage_skills"),
    "tasks": ("task_create", "task_update", "sleep", "write_plan", "exit_plan_mode"),
    "agents": ("delegate", "list_subagents"),
    "memory": ("memory_read_topic",),
    "web": ("parallel_search",),
    "self_management": ("inspect_agent", "configure_agent"),
    # 扩展工具的名称来自调用级有效目录，不在这里固化服务器名单。
    "mcp": (),
    "other": (),
}

# 产品术语 → 工具路由。EffectiveToolCatalog、prompt 工具索引和
# introspect_capability 共用这张表，避免每个入口维护一套“模型应该用谁”。
TOOL_INTENT_ROUTES: dict[str, tuple[str, ...]] = {
    "diff/差异/对比/比较": ("compare_spreadsheets",),
    "结构/工作表/区域/选区": ("inspect_spreadsheet",),
    "分析/统计/筛选/透视": ("analyze_spreadsheet",),
    "编辑/写入/改值/公式/去重/清洗": ("edit_spreadsheet",),
    "拆分文件/分文件/按列拆成多个文件": ("split_spreadsheet",),
    "格式/样式/合并": ("format_spreadsheet",),
    "打印/打印区域/分页/页面设置/page_setup": ("format_spreadsheet",),
    "冻结/冻结窗格/首行/freeze": ("format_spreadsheet",),
    "下拉框/数据验证/下拉/validation": ("format_spreadsheet",),
    "图表": ("manage_spreadsheet_objects",),
    "计算/重算/公式错误": ("calculate_spreadsheet",),
    "预览/渲染/PDF/PNG": ("render_spreadsheet",),
    "转换/xls/xlsb": ("convert_spreadsheet",),
    "校验/验收/主键/合计": ("validate_spreadsheet",),
    "SQL/多表查询/大数据": ("query_spreadsheet",),
    "公式依赖/影响面": ("trace_spreadsheet_formulas",),
    "版本/检查点/恢复": ("manage_spreadsheet_versions",),
    "目录/查找文件": ("list_directory",),
    "文本文件/日志": ("read_text_file",),
    "Word文档/文档表格": ("read_word", "inspect_word", "write_word"),
    "图片/看图": ("read_image",),
    "批量计算/代码": ("run_code",),
    "能力/参数/工具详情": ("introspect_capability",),
    "自身配置/自我管理/推理设置/工具开关": ("inspect_agent", "configure_agent"),
}


# ── 工具简短描述（用于未激活工具索引，帮助 LLM 判断是否需要激活） ──

TOOL_SHORT_DESCRIPTIONS: dict[str, str] = {
    "inspect_spreadsheet": "只读探查 Excel 数据：overview 看结构，range 读取区域，search 搜值，capabilities 查能力",
    "analyze_spreadsheet": "只读分析：profile/quality 全貌，filter 筛选，aggregate 汇总，pivot 透视，relationships 跨文件关联，files 扫目录",
    "compare_spreadsheets": "只读表格数据对比（diff）；两个工作簿或两个工作表；未指定 sheet 时只比较第一张表；position 按坐标，key 按关键列",
    "edit_spreadsheet": "原子编辑：写值/公式、selection 写回、插删行列、改表结构、透视写入、清洗变换，或编译 WorkbookSpec",
    "format_spreadsheet": "改外观：字体/填充/边框/对齐/数字格式、合并、列宽(auto_fit)、冻结窗格、打印布局(print_layout)、条件格式、数据验证(下拉框)",
    "split_spreadsheet": "按某列取值把一个表拆成每组一个新 xlsx（by_column 必填，如按省拆分）；只新建不覆盖",
    "manage_spreadsheet_objects": "富对象：图表、Table、名称、批注、超链接、图片和原生透视表",
    "calculate_spreadsheet": "显式调用计算引擎重算公式，检查错误后原子发布",
    "render_spreadsheet": "将工作表或打印区域渲染为 PDF/分页 PNG，返回页数和引擎状态",
    "convert_spreadsheet": "将 xls/xlsb 等转换为 xlsx，并返回转换前后对象损失报告",
    "validate_spreadsheet": "按唯一性、主键、逐行公式、合计和公式错误规则确定性校验工作簿",
    "query_spreadsheet": "把多个 Excel/CSV 源流入临时 SQLite，执行只读 SQL 并可导出完整结果",
    "trace_spreadsheet_formulas": "只读公式分析：map 全景、trace 单元格、impact 影响面",
    "manage_spreadsheet_versions": "只读列出当前版本与检查点；打快照与按 revision 恢复会写入",
    "read_word": "读取 Word (.docx) 文档的段落内容和表格，支持分页和行内格式",
    "inspect_word": "检查 Word 文档的结构概览（标题树、段落数、表格数、节数、页面设置）",
    "search_word": "在 Word 文档中全文搜索，支持包含/精确/正则/前缀匹配",
    "write_word": "对 Word 文档执行写入：段落四则 + replace_table（工作簿 range 整表替换）/ fill_template（占位符与书签）/ extract_table（抽表到 xlsx）",
    "read_text_file": "读取文本文件内容（md/txt/py/json/csv/yaml 等），查看脚本源码、配置、文档、日志",
    "list_directory": "列出指定目录下的文件和子目录，返回名称、类型和大小",
    "copy_file": "复制文件到工作区内的新位置",
    "rename_file": "重命名或移动文件到工作区内的新位置",
    "delete_file": "安全删除文件（需二次确认），仅限文件不删目录",
    "write_text_file": "写入文本文件（常用于生成 Python 脚本），支持覆盖或新建",
    "edit_text_file": "精准编辑文本文件：查找替换指定片段，无需重写整个文件",
    "run_code": "组合已注册 SDK 或处理领域工具盖不住的批量变换；不要用它默认写 Excel",
    "run_shell": "执行受限 shell 命令（仅当前主机实际可用的白名单只读命令）。文件浏览优先使用 list_directory；不要假设 PowerShell 别名或 Unix 命令存在",
    "read_image": "把工作区图片加载到当前视觉上下文。不要做 OCR，不要另开视觉模型。",
}


# ── 审批详情辅助函数 ───────────────────────────────────────


import os as _os


def get_tool_risk_level(tool_name: str) -> str:
    """根据工具分层返回风险等级：high / medium / low。"""
    if tool_name in MUTATING_CONFIRM_TOOLS or tool_name in CODE_POLICY_DYNAMIC_TOOLS:
        return "high"
    if tool_name in MUTATING_AUDIT_ONLY_TOOLS:
        return "medium"
    return "low"


# 路径类参数键（仅保留文件名）
_PATH_ARG_KEYS: frozenset[str] = frozenset({
    "file_path", "source", "destination", "output_path", "report_path",
})

# 长文本参数键（截断到 80 字符）
_LONG_TEXT_ARG_KEYS: frozenset[str] = frozenset({
    "command", "script", "code", "content",
})


def sanitize_approval_args_summary(
    args: dict[str, object],
    *,
    path_max: int = 60,
    long_max: int = 80,
    default_max: int = 60,
) -> dict[str, str]:
    """对审批参数做脱敏摘要，用于 SSE 和前端展示。

    - 路径类参数仅保留文件名
    - 长文本截断
    - 其他字符串截断
    """
    summary: dict[str, str] = {}
    for key, val in args.items():
        if val is None:
            continue
        s = str(val)
        if not s:
            continue
        if key in _PATH_ARG_KEYS:
            s = _os.path.basename(s)
            if len(s) > path_max:
                s = s[: path_max - 3] + "..."
        elif key in _LONG_TEXT_ARG_KEYS:
            if len(s) > long_max:
                s = s[: long_max - 3] + "..."
        else:
            if len(s) > default_max:
                s = s[: default_max - 3] + "..."
        summary[key] = s
    return summary
