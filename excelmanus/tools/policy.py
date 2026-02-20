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
        "analyze_data",
        "filter_data",
        "group_aggregate",
        "analyze_sheet_mapping",
        "list_sheets",
        "get_file_info",
        "find_files",
        "list_directory",
        "read_text_file",
        "read_cell_styles",
        "inspect_excel_files",
        "memory_read_topic",
        # 任务工具仅修改会话内存态，不触达工作区文件。
        "task_create",
        "task_update",
        # 自省工具：纯查询，无副作用
        "introspect_capability",
    }
)

# ── 写入类工具分层 ──────────────────────────────────────────

# Tier A：需要进入 /accept 门禁确认后才能执行
MUTATING_CONFIRM_TOOLS: frozenset[str] = frozenset(
    {
        "write_text_file",
        "run_shell",
        "delete_file",
        "rename_file",
        "write_excel",
        "transform_data",
        "create_sheet",
        "copy_sheet",
        "rename_sheet",
        "delete_sheet",
        "copy_range_between_sheets",
    }
)

# Tier B：不拦截确认，但必须纳入审计
MUTATING_AUDIT_ONLY_TOOLS: frozenset[str] = frozenset(
    {
        "copy_file",
        "create_chart",
        "create_excel_chart",
        "write_cells",
        "insert_rows",
        "insert_columns",
        "format_cells",
        "adjust_column_width",
        "adjust_row_height",
        "merge_cells",
        "unmerge_cells",
        "apply_threshold_icon_format",
        "style_card_blocks",
        "scale_range_unit",
        "apply_dashboard_dark_theme",
        "add_color_scale",
        "add_data_bar",
        "add_conditional_rule",
        "set_print_layout",
        "set_page_header_footer",
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
    "write_excel": ("file_path",),
    "format_cells": ("file_path",),
    "adjust_column_width": ("file_path",),
    "adjust_row_height": ("file_path",),
    "merge_cells": ("file_path",),
    "unmerge_cells": ("file_path",),
    "create_sheet": ("file_path",),
    "copy_sheet": ("file_path",),
    "rename_sheet": ("file_path",),
    "delete_sheet": ("file_path",),
    "create_excel_chart": ("file_path",),
    "write_cells": ("file_path",),
    "insert_rows": ("file_path",),
    "insert_columns": ("file_path",),
    "apply_threshold_icon_format": ("file_path",),
    "style_card_blocks": ("file_path",),
    "scale_range_unit": ("file_path",),
    "apply_dashboard_dark_theme": ("file_path",),
    "add_color_scale": ("file_path",),
    "add_data_bar": ("file_path",),
    "add_conditional_rule": ("file_path",),
    "set_print_layout": ("file_path",),
    "set_page_header_footer": ("file_path",),
    "create_chart": ("output_path",),
}

# mode=first：按字段优先级提取第一个非空路径
AUDIT_TARGET_ARG_RULES_FIRST: dict[str, tuple[str, ...]] = {
    "transform_data": ("output_path", "file_path"),
    "copy_range_between_sheets": ("target_file", "source_file"),
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
    "__pycache__",
    "outputs/approvals",
)


# ── Subagent 工具域 ───────────────────────────────────────

SUBAGENT_READ_ONLY_TOOLS: tuple[str, ...] = (
    "read_excel",
    "analyze_data",
    "filter_data",
    "group_aggregate",
    "analyze_sheet_mapping",
    "list_sheets",
    "get_file_info",
    "find_files",
    "list_directory",
    "read_text_file",
    "read_cell_styles",
    "inspect_excel_files",
)

SUBAGENT_ANALYSIS_EXTRA_TOOLS: tuple[str, ...] = (
    "run_code",
    "run_shell",
    "write_text_file",
)

SUBAGENT_WRITE_EXTRA_TOOLS: tuple[str, ...] = (
    "write_excel",
    "transform_data",
    "format_cells",
    "adjust_column_width",
    "adjust_row_height",
    "merge_cells",
    "unmerge_cells",
    "create_chart",
    "create_excel_chart",
    "create_sheet",
    "copy_sheet",
    "rename_sheet",
    "delete_sheet",
    "copy_range_between_sheets",
    "copy_file",
    "rename_file",
    "delete_file",
    "write_text_file",
    "run_code",
    "write_cells",
    "insert_rows",
    "insert_columns",
    "apply_threshold_icon_format",
    "style_card_blocks",
    "scale_range_unit",
    "apply_dashboard_dark_theme",
    "add_color_scale",
    "add_data_bar",
    "add_conditional_rule",
    "set_print_layout",
    "set_page_header_footer",
)


# ── 工具分类映射（用于工具索引和 expand_tools 元工具） ────

TOOL_CATEGORIES: dict[str, tuple[str, ...]] = {
    "data_read": (
        "read_excel", "inspect_excel_files", "analyze_data",
        "filter_data", "group_aggregate", "analyze_sheet_mapping",
    ),
    "data_write": (
        "write_excel", "write_cells", "transform_data",
        "insert_rows", "insert_columns",
    ),
    "format": (
        "format_cells", "adjust_column_width", "adjust_row_height",
        "read_cell_styles", "merge_cells", "unmerge_cells",
    ),
    "advanced_format": (
        "apply_threshold_icon_format", "style_card_blocks",
        "scale_range_unit", "apply_dashboard_dark_theme",
        "add_color_scale", "add_data_bar", "add_conditional_rule",
        "set_print_layout", "set_page_header_footer",
    ),
    "chart": ("create_chart", "create_excel_chart"),
    "sheet": (
        "list_sheets", "create_sheet", "copy_sheet",
        "rename_sheet", "delete_sheet", "copy_range_between_sheets",
    ),
    "file": (
        "list_directory", "get_file_info", "find_files",
        "read_text_file", "copy_file", "rename_file", "delete_file",
    ),
    "code": ("write_text_file", "run_code", "run_shell"),
}


# ── 工具简短描述（用于未激活工具索引，帮助 LLM 判断是否需要激活） ──

TOOL_SHORT_DESCRIPTIONS: dict[str, str] = {
    # data_read
    "read_excel": "读取 Excel 数据摘要与前10行预览，可按需附加样式/图表/公式等维度",
    "inspect_excel_files": "批量扫描目录下所有 Excel 文件概况，快速了解工作区全貌",
    "analyze_data": "对 Excel 数据做描述性统计分析，输出均值/中位数/缺失值等",
    "filter_data": "按条件筛选 Excel 数据行，支持多条件组合、排序和 Top-N",
    "group_aggregate": "按列分组聚合统计（SUM/COUNT/MEAN 等），适用于汇总报表",
    "analyze_sheet_mapping": "分析两个工作表键字段的映射覆盖率，跨表写回前做口径校验",
    # data_write
    "write_excel": "将行数据批量写入 Excel 工作表，已有文件仅替换指定 sheet",
    "write_cells": "向指定单元格或范围写入值/公式，不影响其他区域数据",
    "transform_data": "对 Excel 数据执行列级转换（重命名、增删列、排序）",
    "insert_rows": "在 Excel 指定行号前插入空行，已有数据自动下移",
    "insert_columns": "在 Excel 指定列前插入空列，已有数据自动右移",
    # format
    "format_cells": "对单元格范围应用格式化样式（字体、填充、边框、对齐、数字格式）",
    "adjust_column_width": "调整 Excel 列宽，支持手动指定或自动适配内容",
    "adjust_row_height": "调整 Excel 行高，支持手动指定或自动适配",
    "read_cell_styles": "读取单元格范围的现有样式信息（字体、颜色、边框、合并状态）",
    "merge_cells": "合并 Excel 指定范围的单元格",
    "unmerge_cells": "取消合并 Excel 指定范围的单元格",
    # advanced_format
    "apply_threshold_icon_format": "三段阈值图标化显示（↑—↓），适用于 KPI 达标率等指标",
    "style_card_blocks": "批量卡片化区域样式（粗边框+圆角+阴影），适用于仪表盘布局",
    "scale_range_unit": "按除数缩放数值并统一单位格式（如元转万），适用于报表单位换算",
    "apply_dashboard_dark_theme": "一键应用暗色仪表盘主题（底色+卡片+指标高亮+图表样式）",
    "add_color_scale": "添加二色或三色渐变色阶条件格式，适用于热力图效果",
    "add_data_bar": "添加数据条条件格式，单元格内按比例显示彩色条",
    "add_conditional_rule": "添加通用条件格式规则（值比较高亮、公式条件、图标集）",
    "set_print_layout": "设置打印布局（打印区域、纸张方向/大小、缩放、重复表头行）",
    "set_page_header_footer": "设置页眉页脚内容，支持页码/日期/工作表名等占位符",
    # chart
    "create_chart": "从 Excel 数据生成图表并保存为 PNG 图片（柱/折/饼/散/雷达）",
    "create_excel_chart": "在 Excel 中插入原生嵌入式图表对象（柱/折/饼/散/面积）",
    # sheet
    "list_sheets": "列出 Excel 文件中所有工作表的名称、行列数等概况信息",
    "create_sheet": "在已有 Excel 文件中新建空白工作表",
    "copy_sheet": "复制工作表（同文件内），生成副本",
    "rename_sheet": "重命名 Excel 文件中的工作表",
    "delete_sheet": "删除 Excel 文件中的工作表（需二次确认）",
    "copy_range_between_sheets": "从源工作表复制指定范围数据到目标工作表，支持跨文件",
    # file
    "list_directory": "列出指定目录下的文件和子目录，返回名称、类型和大小",
    "get_file_info": "获取文件或目录的详细信息（大小、修改时间、扩展名等）",
    "find_files": "按 glob 模式在工作区内搜索文件（如 *.xlsx、**/*.csv）",
    "read_text_file": "读取非 Excel 文本文件内容（CSV、TXT、JSON 等）",
    "copy_file": "复制文件到工作区内的新位置",
    "rename_file": "重命名或移动文件到工作区内的新位置",
    "delete_file": "安全删除文件（需二次确认），仅限文件不删目录",
    # code
    "write_text_file": "写入文本文件（常用于生成 Python 脚本），支持覆盖或新建",
    "run_code": "执行 Python 代码或脚本，适用于批量数据处理、复杂变换、跨表操作等场景（已配备安全沙盒）",
    "run_shell": "执行受限 shell 命令（仅白名单只读命令如 ls/grep/find）",
}
