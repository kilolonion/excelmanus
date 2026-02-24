"""工具策略单一事实源（SSOT）。

集中维护：
1. 写入类工具的审批/审计分层；
2. 子代理默认工具域；
3. 审计目标路径映射与工作区扫描预算；
4. fallback 路由下的只读发现工具。
"""

from __future__ import annotations

# ── 只读安全白名单（低风险） ───────────────────────────────

# 仅显式白名单中的工具在 readOnly 模式下可直接执行。
# default 模式下的确认/审计行为由写入分层（Tier A/Tier B）决定。
READ_ONLY_SAFE_TOOLS: frozenset[str] = frozenset(
    {
        "read_excel",
        # analyze_data, group_aggregate, analyze_sheet_mapping: Batch 4 精简
        "filter_data",
        "list_sheets",
        # get_file_info, find_files, read_text_file: Batch 5 精简
        "list_directory",
        # read_cell_styles: Batch 2 精简
        "inspect_excel_files",
        "memory_read_topic",
        # 任务工具仅修改会话内存态，不触达工作区文件。
        "task_create",
        "task_update",
        # 自省工具：纯查询，无副作用
        "introspect_capability",
        # Vision 工具：读取图片，只读
        "read_image",
    }
)

# ── 可并行执行的只读工具 ──────────────────────────────────────
# READ_ONLY_SAFE_TOOLS 的子集，排除有特殊调度路径的元工具
# （task_create 有 plan 拦截、task_update 有 task list 事件、introspect_capability 极少出现）。
# 同一轮次中相邻的可并行工具将通过 asyncio.gather 并发执行。
PARALLELIZABLE_READONLY_TOOLS: frozenset[str] = frozenset(
    {
        "read_excel",
        "filter_data",
        "list_sheets",
        "list_directory",
        "inspect_excel_files",
        "memory_read_topic",
        "read_image",
    }
)

if not PARALLELIZABLE_READONLY_TOOLS <= READ_ONLY_SAFE_TOOLS:
    raise AssertionError("PARALLELIZABLE_READONLY_TOOLS 必须是 READ_ONLY_SAFE_TOOLS 子集")

# ── 写入类工具分层 ──────────────────────────────────────────

# Tier A：需要进入 /accept 门禁确认后才能执行
MUTATING_CONFIRM_TOOLS: frozenset[str] = frozenset(
    {
        "write_text_file",
        "run_shell",
        "delete_file",
        "rename_file",
        # write_excel, transform_data: Batch 1 精简
        # create_sheet, copy_sheet, rename_sheet, delete_sheet, copy_range_between_sheets: Batch 3 精简
    }
)

# Tier B：不拦截确认，但必须纳入审计
MUTATING_AUDIT_ONLY_TOOLS: frozenset[str] = frozenset(
    {
        "copy_file",
        # Macro 工具（声明式复合操作，审计不拦截）
        "vlookup_write",
        "computed_column",
        # Vision 工具（写文件，自动审批）
        "rebuild_excel_from_spec",
        "verify_excel_replica",
        # extract_table_from_image 已废弃（B+C 混合架构）
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
    "copy_file": ("destination",),
    "rename_file": ("source", "destination"),
    "delete_file": ("file_path",),
    "vlookup_write": ("file_path",),
    "computed_column": ("file_path",),
    "rebuild_excel_from_spec": ("output_path",),
    "verify_excel_replica": ("report_path",),
}

# mode=first：按字段优先级提取第一个非空路径
AUDIT_TARGET_ARG_RULES_FIRST: dict[str, tuple[str, ...]] = {
    # transform_data: Batch 1 精简
    # copy_range_between_sheets: Batch 3 精简
}

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
    "outputs/approvals",
)


# ── 工具分类映射（用于工具索引） ────

TOOL_CATEGORIES: dict[str, tuple[str, ...]] = {
    "data_read": (
        "read_excel", "inspect_excel_files", "filter_data",
        # analyze_data, group_aggregate, analyze_sheet_mapping: Batch 4 精简
    ),
    # data_write: Batch 1 精简
    # format: Batch 2 精简
    # advanced_format + chart + sheet写入: Batch 3 精简
    "sheet": ("list_sheets",),  # list_sheets 保留为只读结构发现
    "file": (
        "list_directory", "copy_file", "rename_file", "delete_file",
        # get_file_info, find_files, read_text_file: Batch 5 精简
    ),
    "code": ("write_text_file", "run_code", "run_shell"),
    "macro": ("vlookup_write", "computed_column"),
    "vision": ("read_image", "rebuild_excel_from_spec", "verify_excel_replica"),
}


# ── 工具简短描述（用于未激活工具索引，帮助 LLM 判断是否需要激活） ──

TOOL_SHORT_DESCRIPTIONS: dict[str, str] = {
    # data_read
    "read_excel": "读取 Excel 数据摘要与前10行预览，可按需附加样式/图表/公式等维度",
    "inspect_excel_files": "批量扫描目录下所有 Excel 文件概况，快速了解工作区全貌",
    # analyze_data, group_aggregate, analyze_sheet_mapping: Batch 4 精简
    "filter_data": "按条件筛选 Excel 数据行，支持多条件组合、排序和 Top-N",
    # data_write
    # write_excel, write_cells, transform_data, insert_rows, insert_columns: Batch 1 精简
    # Batch 2 精简（format 全部）
    # Batch 3 精简（advanced_format + chart + sheet写入）
    # sheet (list_sheets 保留)
    "list_sheets": "列出 Excel 文件中所有工作表的名称、行列数等概况信息",
    # file
    "list_directory": "列出指定目录下的文件和子目录，返回名称、类型和大小",
    # get_file_info, find_files, read_text_file: Batch 5 精简
    "copy_file": "复制文件到工作区内的新位置",
    "rename_file": "重命名或移动文件到工作区内的新位置",
    "delete_file": "安全删除文件（需二次确认），仅限文件不删目录",
    # code
    "write_text_file": "写入文本文件（常用于生成 Python 脚本），支持覆盖或新建",
    "run_code": "执行 Python 代码或脚本，适用于批量数据处理、复杂变换、跨表操作等场景（已配备安全沙盒）",
    "run_shell": "执行受限 shell 命令（仅白名单只读命令如 ls/grep/find）",
    # macro
    "vlookup_write": "跨表匹配写回：从源表查找/聚合数据写入目标表新列（类 VLOOKUP）",
    "computed_column": "新增计算列：用声明式表达式计算新列并写回",
    # vision
    "read_image": "读取本地图片文件并加载到视觉上下文，支持 png/jpg/gif/bmp/webp",
    "rebuild_excel_from_spec": "从 ReplicaSpec JSON 确定性编译为 Excel 文件",
    "verify_excel_replica": "验证 Excel 文件与 ReplicaSpec 的一致性，生成差异报告",
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

