"""内置子代理定义。"""

from __future__ import annotations

from excelmanus.subagent.models import SubagentConfig

_READ_ONLY_TOOLS = [
    "read_excel",
    "analyze_data",
    "filter_data",
    "list_sheets",
    "get_file_info",
    "search_files",
    "list_directory",
    "read_text_file",
    "read_cell_styles",
]

_ANALYSIS_TOOLS = [
    *_READ_ONLY_TOOLS,
    "run_code",
    "write_text_file",
]

_WRITE_TOOLS = [
    *_READ_ONLY_TOOLS,
    "write_excel",
    "transform_data",
    "format_cells",
    "adjust_column_width",
    "adjust_row_height",
    "merge_cells",
    "unmerge_cells",
    "create_chart",
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
]

_CODER_TOOLS = [
    "run_code",
    "write_text_file",
    "read_text_file",
    "search_files",
    "list_directory",
    "get_file_info",
    "read_excel",
    "write_excel",
    "analyze_data",
    "filter_data",
]


BUILTIN_SUBAGENTS: dict[str, SubagentConfig] = {
    "explorer": SubagentConfig(
        name="explorer",
        description="只读探查 Excel 结构与数据概览。",
        allowed_tools=_READ_ONLY_TOOLS,
        permission_mode="readOnly",
        max_iterations=4,
        max_consecutive_failures=2,
        source="builtin",
        system_prompt=(
            "你是 ExcelManus 的只读探查子代理。\n\n"
            "## 职责\n"
            "识别文件结构、sheet 列表、关键字段、样本数据和数据质量风险。\n"
            "严格禁止写入、重命名、删除、覆盖等修改操作。\n\n"
            "## 完成标准\n"
            "输出必须包含：文件/sheet 结构摘要、关键字段列表、数据行数与样本、发现的质量问题。\n\n"
            "## 失败策略\n"
            "文件不存在或格式异常时，立即报告错误信息，不要重复尝试。"
        ),
    ),
    "analyst": SubagentConfig(
        name="analyst",
        description="执行数据分析、统计与异常定位。",
        allowed_tools=_ANALYSIS_TOOLS,
        permission_mode="default",
        max_iterations=8,
        max_consecutive_failures=2,
        source="builtin",
        system_prompt=(
            "你是数据分析子代理。\n\n"
            "## 职责\n"
            "先理解数据结构，再给出统计结论与风险点；结论必须附可核验数字。\n\n"
            "## 完成标准\n"
            "输出必须包含：分析方法说明、关键统计值（附来源单元格/范围）、风险与异常点。\n\n"
            "## 失败策略\n"
            "数据不足或格式异常时，汇报已获信息和阻塞原因，不编造数据。"
        ),
    ),
    "writer": SubagentConfig(
        name="writer",
        description="执行表格写入、格式化与图表生成。",
        allowed_tools=_WRITE_TOOLS,
        permission_mode="acceptEdits",
        max_iterations=10,
        max_consecutive_failures=2,
        source="builtin",
        system_prompt=(
            "你是写入与格式化子代理。\n\n"
            "## 工作规范\n"
            "- 修改前先读取目标区域确认当前状态。\n"
            "- 尽量批量写入，减少工具调用次数。\n"
            "- 输出应可回滚。\n\n"
            "## 完成标准\n"
            "输出必须包含：修改的文件路径、sheet 名、影响的单元格范围、修改前后对比。\n\n"
            "## 失败策略\n"
            "写入失败时报告错误原因和已完成的部分，不要静默跳过。"
        ),
    ),
    "coder": SubagentConfig(
        name="coder",
        description="通过 Python 脚本处理复杂数据任务。",
        allowed_tools=_CODER_TOOLS,
        permission_mode="dontAsk",
        max_iterations=6,
        max_consecutive_failures=2,
        source="builtin",
        system_prompt=(
            "你是 Python 数据处理子代理。\n\n"
            "## 工作规范\n"
            "- 优先生成小步可验证脚本。\n"
            "- 每个脚本做一件事，执行后立即检查结果。\n"
            "- 操作文件前先备份或使用临时文件。\n\n"
            "## 完成标准\n"
            "输出必须包含：执行的脚本摘要、关键结果数字、产物文件路径。\n\n"
            "## 失败策略\n"
            "脚本报错时先分析错误原因，修复后重试一次；仍失败则汇报错误和已有进展。"
        ),
    ),
}
