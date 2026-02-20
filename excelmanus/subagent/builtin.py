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
    "find_files",
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
        max_iterations=60,
        max_consecutive_failures=2,
        source="builtin",
    ),
    "explorer": SubagentConfig(
        name="explorer",
        description="只读探查 Excel 与文件夹目录结构，输出结构化总结。",
        allowed_tools=_READ_ONLY_TOOLS,
        permission_mode="readOnly",
        max_iterations=60,
        max_consecutive_failures=2,
        source="builtin",
    ),
    "analyst": SubagentConfig(
        name="analyst",
        description="执行数据分析、统计与异常定位。",
        allowed_tools=_ANALYSIS_TOOLS,
        permission_mode="default",
        max_iterations=120,
        max_consecutive_failures=2,
        source="builtin",
    ),
    "writer": SubagentConfig(
        name="writer",
        description="执行表格写入、格式化与图表生成。",
        allowed_tools=_WRITE_TOOLS,
        permission_mode="acceptEdits",
        max_iterations=120,
        max_consecutive_failures=2,
        source="builtin",
    ),
    "coder": SubagentConfig(
        name="coder",
        description="通过 Python 脚本处理复杂数据任务。",
        allowed_tools=_CODER_TOOLS,
        permission_mode="dontAsk",
        max_iterations=120,
        max_consecutive_failures=2,
        source="builtin",
    ),
    "full": SubagentConfig(
        name="full",
        description="全能力子代理，工具域与主代理一致，适用于复杂研究与多步骤修改任务。",
        allowed_tools=[],
        permission_mode="acceptEdits",
        max_iterations=120,
        max_consecutive_failures=2,
        capability_mode="full",
        source="builtin",
    ),
}
