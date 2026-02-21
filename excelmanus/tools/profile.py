"""工具呈现层（ToolProfile）：工具元数据与分类。

v5.2 三层正交架构之 Layer 1。
与 Skill 层完全解耦（Skill 只负责知识注入）。
与 ToolPolicy 完全解耦（ToolPolicy 只负责安全拦截）。

v5.2: 废弃 core/extended 分层，所有工具始终暴露完整 schema。
"""
from __future__ import annotations

# ── 所有工具（v5.2: 统一暴露完整 schema） ──────────────────
CORE_TOOLS: frozenset[str] = frozenset({
    # 数据读取
    "read_excel", "filter_data", "inspect_excel_files",
    # analyze_data, group_aggregate, analyze_sheet_mapping: Batch 4 精简
    # 结构发现
    "list_sheets", "list_directory",
    # get_file_info, find_files, read_text_file: Batch 5 精简
    # read_cell_styles: Batch 2 精简
    # 元工具（由 engine 注册，此处仅声明 tier）
    "activate_skill",
    "focus_window", "task_create", "task_update",
    "finish_task", "ask_user",
    "delegate_to_subagent", "list_subagents",
    "memory_save", "memory_read_topic",
    # 数据写入（Batch 1 精简：write_excel/write_cells/transform_data/insert_rows/insert_columns 已删除，由 run_code 替代）
    # 格式化（Batch 2 精简）
    # 高级格式化 + 图表 + 工作表管理（Batch 3 精简，全部由 run_code 替代）
    # 代码执行
    "write_text_file", "run_code", "run_shell",
    # 文件操作
    "copy_file", "rename_file", "delete_file",
    # Macro 工具
    "vlookup_write", "computed_column",
    # Vision 工具
    "read_image", "rebuild_excel_from_spec", "verify_excel_replica",
})

# ── 全量 ToolProfile 定义 ─────────────────────────────────
TOOL_PROFILES: dict[str, dict] = {}

# v5.2: 按类别设置 category（tier 统一为 core）
_TOOL_CATEGORY_MAP: dict[str, tuple[str, ...]] = {
    "data_read": ("read_excel", "filter_data", "inspect_excel_files"),
    "structure": ("list_sheets", "list_directory"),
    "code": ("write_text_file", "run_code", "run_shell"),
    "file_ops": ("copy_file", "rename_file", "delete_file"),
    "macro": ("vlookup_write", "computed_column"),
    "vision": ("read_image", "rebuild_excel_from_spec", "verify_excel_replica"),
}

# 先将有明确分类的工具写入 profile
for _category, _tools in _TOOL_CATEGORY_MAP.items():
    for _tool_name in _tools:
        TOOL_PROFILES[_tool_name] = {"tier": "core", "category": _category}

# 其余工具（元工具）统一为 meta 分类
for _name in CORE_TOOLS:
    if _name not in TOOL_PROFILES:
        TOOL_PROFILES[_name] = {"tier": "core", "category": "meta"}


def get_category(tool_name: str) -> str | None:
    """返回工具的 category。"""
    profile = TOOL_PROFILES.get(tool_name)
    return profile["category"] if profile else None


def get_tools_in_category(category: str) -> list[str]:
    """返回指定 category 中的所有工具名。"""
    return [
        name for name, profile in TOOL_PROFILES.items()
        if profile.get("category") == category
    ]
