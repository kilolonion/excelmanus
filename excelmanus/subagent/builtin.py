"""内置子代理定义。

- ``subagent``：通用全能力子代理，工具域与主代理一致。
- ``explorer``：只读探索子代理，仅拥有只读工具。

需要核对时主代理自己 inspect/compare/trace，或显式委托 explorer。
"""

from __future__ import annotations

from excelmanus.subagent.models import SubagentConfig

_EXPLORER_TOOLS: list[str] = [
    "observe_spreadsheet",
    "preview_spreadsheet",
    "analyze_spreadsheet",
    "compare_spreadsheets",
    "trace_spreadsheet_formulas",
    "read_word",
    "inspect_word",
    "search_word",
    "list_directory",
    "read_image",
    "introspect_capability",
    "run_code",
    "read_text_file",
]


BUILTIN_SUBAGENTS: dict[str, SubagentConfig] = {
    "subagent": SubagentConfig(
        name="subagent",
        description="通用全能力子代理，工具域与主代理一致，适用于需要独立上下文的长任务。",
        allowed_tools=[],
        permission_mode="acceptEdits",
        max_iterations=0,
        max_consecutive_failures=3,
        capability_mode="full",
        source="builtin",
        inherit_strategies=[
            "tool:observe",
            "tool:analyze",
            "tool:changes",
            "tool:preview",
            "spreadsheet:document",
            "spreadsheet:bootstrap",
            "tool:run_code",
        ],
    ),
    "explorer": SubagentConfig(
        name="explorer",
        description=(
            "只读探索子代理：文件结构分析、数据预览与统计。"
            "overview / range / search / 分析按需要选用；run_code 只读。"
        ),
        allowed_tools=_EXPLORER_TOOLS,
        permission_mode="readOnly",
        max_iterations=0,
        max_consecutive_failures=3,
        capability_mode="restricted",
        source="builtin",
        max_tokens=8192,
        inherit_strategies=["tool:observe", "tool:analyze", "tool:run_code"],
        system_prompt=(
            "你是只读探索子代理 explorer。\n"
            "overview / range / search / 分析按需要选用。"
            "run_code 只做只读计算。用自然语言和必要数字交代发现。"
        ),
    ),
}
