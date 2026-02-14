"""内置子代理定义。"""

from __future__ import annotations

from excelmanus.subagent.models import SubagentConfig
from excelmanus.tools.policy import (
    SUBAGENT_ANALYSIS_EXTRA_TOOLS,
    SUBAGENT_READ_ONLY_TOOLS,
    SUBAGENT_WRITE_EXTRA_TOOLS,
)

def _merge_tools(*groups: tuple[str, ...] | list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for tool_name in group:
            if tool_name in seen:
                continue
            seen.add(tool_name)
            merged.append(tool_name)
    return merged


_READ_ONLY_TOOLS = list(SUBAGENT_READ_ONLY_TOOLS)

_ANALYSIS_TOOLS = _merge_tools(
    SUBAGENT_READ_ONLY_TOOLS,
    SUBAGENT_ANALYSIS_EXTRA_TOOLS,
)

_WRITE_TOOLS = _merge_tools(
    SUBAGENT_READ_ONLY_TOOLS,
    SUBAGENT_WRITE_EXTRA_TOOLS,
)

_CODER_TOOLS = [
    "run_code",
    "run_shell",
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
    "planner": SubagentConfig(
        name="planner",
        description="生成可审批的 Markdown 计划文档与任务清单。",
        allowed_tools=_READ_ONLY_TOOLS,
        permission_mode="readOnly",
        max_iterations=4,
        max_consecutive_failures=2,
        source="builtin",
        system_prompt=(
            "你是计划子代理，只负责生成计划文档，不执行写入操作。\n\n"
            "## 输出约束（必须严格遵守）\n"
            "- 只输出一份 Markdown 文档，不要输出额外说明。\n"
            "- 文档必须包含一级标题（# ...）。\n"
            "- 文档必须包含 `## 任务清单` 章节，且使用 `- [ ]` 复选项列出子任务。\n"
            "- 文档必须包含一个 fenced code block，语言标识为 `tasklist-json`。\n"
            "- 该 JSON 必须是对象，格式："
            '{"title":"任务标题","subtasks":["子任务1","子任务2"]}。\n'
            "- `subtasks` 数量限制 1-20，任务描述短句、可执行、无重复。\n\n"
            "## 质量要求\n"
            "- 计划应贴合当前项目目标，避免空泛任务。\n"
            "- 子任务按执行顺序组织，可直接交给主代理执行。"
        ),
    ),
    "explorer": SubagentConfig(
        name="explorer",
        description="只读探查 Excel 与文件夹目录结构，输出结构化总结。",
        allowed_tools=_READ_ONLY_TOOLS,
        permission_mode="readOnly",
        max_iterations=4,
        max_consecutive_failures=2,
        source="builtin",
        system_prompt=(
            "你是 ExcelManus 的只读探查子代理。\n\n"
            "## 工作流程（必须按顺序执行）\n"
            "1. **第一步**：调用 `scan_excel_files` 批量获取工作区所有 Excel 文件概览"
            "（sheet 列表、行列数、列名、预览行）。这是最高效的起步方式，"
            "一次调用即可了解全局，避免逐个文件调用 `read_excel` 或 `list_sheets`。\n"
            "2. **第二步**：根据概览结果，仅对需要深入了解的文件使用 "
            "`read_excel`（查看更多行）或 `analyze_data`（统计分析）。\n"
            "3. **第三步**：输出结构化总结报告。\n\n"
            "## 职责\n"
            "识别目录结构、文件分布、sheet 列表、关键字段、样本数据和数据质量风险。\n"
            "严格禁止写入、重命名、删除、覆盖等修改操作。\n\n"
            "## 完成标准\n"
            "输出必须包含：\n"
            "- 目录/文件结构摘要（按类型归类，并标注可疑大文件）\n"
            "- 每个 Excel 文件的 sheet 结构（sheet 名、行列数、列名）\n"
            "- 关键字段列表与样本数据\n"
            "- 发现的数据质量问题与风险\n\n"
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
