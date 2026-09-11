"""工具策略单一事实源（SSOT）。

集中维护：
1. 写入类工具的审批/审计分层；
2. 子代理默认工具域；
3. 审计目标路径映射与工作区扫描预算；
4. fallback 路由下的只读发现工具。
"""

from __future__ import annotations

# ── 只读安全白名单（低风险） ───────────────────────────────

# 仅显式白名单中的工具在只读模式下可直接执行。
# 默认模式下的确认/审计行为由写入分层（Tier A/Tier B）决定。
READ_ONLY_SAFE_TOOLS: frozenset[str] = frozenset(
    {
        "inspect_spreadsheet",
        "analyze_spreadsheet",
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
        "read_image",
        "parallel_search",
    }
)

# ── 可并行执行的只读工具 ──────────────────────────────────────
# READ_ONLY_SAFE_TOOLS 的子集，排除有特殊调度路径的元工具
# （task_create 有 plan 拦截、task_update 有 task list 事件、introspect_capability 极少出现）。
# 同一轮次中相邻的可并行工具将通过 asyncio.gather 并发执行。
PARALLELIZABLE_READONLY_TOOLS: frozenset[str] = frozenset(
    {
        "inspect_spreadsheet",
        "analyze_spreadsheet",
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
        "format_spreadsheet",
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
    "manage_spreadsheet_objects": ("file_path",),
    "manage_spreadsheet_versions": ("file_path",),
}

# mode=first：按字段优先级提取第一个非空路径
AUDIT_TARGET_ARG_RULES_FIRST: dict[str, tuple[str, ...]] = {}

# run_code 使用动态策略引擎分级，不在静态 CONFIRM/AUDIT 集合中
CODE_POLICY_DYNAMIC_TOOLS: frozenset[str] = frozenset({"run_code"})

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
    "objects": ("manage_spreadsheet_objects",),
    "formula_trace": ("trace_spreadsheet_formulas",),
    "versions": ("manage_spreadsheet_versions",),
    "word": ("read_word", "inspect_word", "search_word", "write_word"),
    "file": (
        "read_text_file", "list_directory", "copy_file", "rename_file", "delete_file",
    ),
    "code": ("write_text_file", "edit_text_file", "run_code", "run_shell"),
    "vision": ("read_image",),
}


# ── 工具简短描述（用于未激活工具索引，帮助 LLM 判断是否需要激活） ──

TOOL_SHORT_DESCRIPTIONS: dict[str, str] = {
    "inspect_spreadsheet": "只读探查 Excel 数据：overview 看结构，range 读取区域，search 搜值，capabilities 查能力",
    "analyze_spreadsheet": "只读分析：profile/quality 全貌，filter 筛选，relationships 跨文件关联，files 扫目录",
    "compare_spreadsheets": "只读对比两个工作簿或同簿两表，position 按坐标，key 按关键列",
    "edit_spreadsheet": "原子编辑：写值/公式、插行列、改表结构，或编译 WorkbookSpec",
    "format_spreadsheet": "原子改外观：字体/填充/边框/对齐、合并、行列尺寸",
    "manage_spreadsheet_objects": "富对象：插入原生 Excel 图表",
    "trace_spreadsheet_formulas": "只读公式分析：map 全景、trace 单元格、impact 影响面",
    "manage_spreadsheet_versions": "列出当前版本与检查点、打快照、按 revision 恢复",
    "read_word": "读取 Word (.docx) 文档的段落内容和表格，支持分页和行内格式",
    "inspect_word": "检查 Word 文档的结构概览（标题树、段落数、表格数、节数、页面设置）",
    "search_word": "在 Word 文档中全文搜索，支持包含/精确/正则/前缀匹配",
    "write_word": "对 Word 文档执行段落写入操作（替换/插入/追加/删除）",
    "read_text_file": "读取文本文件内容（md/txt/py/json/csv/yaml 等），查看脚本源码、配置、文档、日志",
    "list_directory": "列出指定目录下的文件和子目录，返回名称、类型和大小",
    "copy_file": "复制文件到工作区内的新位置",
    "rename_file": "重命名或移动文件到工作区内的新位置",
    "delete_file": "安全删除文件（需二次确认），仅限文件不删目录",
    "write_text_file": "写入文本文件（常用于生成 Python 脚本），支持覆盖或新建",
    "edit_text_file": "精准编辑文本文件：查找替换指定片段，无需重写整个文件",
    "run_code": "组合已注册 SDK 或处理领域工具盖不住的批量变换；不要用它默认写 Excel",
    "run_shell": "执行受限 shell 命令（仅白名单只读命令如 ls/grep/find）",
    "read_image": "读取本地图片并加载到视觉上下文；复刻后用 edit_spreadsheet(workbook_spec=) 编译",
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

