"""内置子代理定义。

提供三个内置子代理：

- ``subagent``：通用全能力子代理，工具域与主代理一致。
- ``explorer``：只读探索子代理，仅拥有只读工具，适用于文件结构分析与数据预览。
- ``verifier``：只读检查子代理，可由主代理通过 ``delegate`` 手动调用，不接入自动验收。

用户仍可通过 project/user 目录的 .md 文件自定义子代理。
"""

from __future__ import annotations

from excelmanus.subagent.models import SubagentConfig

# explorer 探索工具白名单
# 基于 READ_ONLY_SAFE_TOOLS 子集 + run_code（分析性计算）+ read_text_file（非 Excel 文件）
_EXPLORER_TOOLS: list[str] = [
    "inspect_spreadsheet",
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

_VERIFIER_TOOLS: list[str] = [
    "inspect_spreadsheet",
    "analyze_spreadsheet",
    "compare_spreadsheets",
    "trace_spreadsheet_formulas",
    "read_word",
    "inspect_word",
    "search_word",
    "list_directory",
    "run_code",
    "read_text_file",
]


BUILTIN_SUBAGENTS: dict[str, SubagentConfig] = {
    "subagent": SubagentConfig(
        name="subagent",
        description="通用全能力子代理，工具域与主代理一致，适用于需要独立上下文的长任务。",
        allowed_tools=[],
        permission_mode="acceptEdits",
        max_iterations=120,
        max_consecutive_failures=3,
        capability_mode="full",
        source="builtin",
        inherit_strategies=["__all__"],
    ),
    "explorer": SubagentConfig(
        name="explorer",
        description=(
            "数据上下文快速收集器，用于文件结构分析、数据 schema 扫描、统计概况与数据质量检测。"
            "优先使用 inspect_spreadsheet / analyze_spreadsheet 获取文件全貌与定位。"
            "支持 run_code 做分析性计算（pandas/openpyxl 只读操作）。"
        ),
        allowed_tools=_EXPLORER_TOOLS,
        permission_mode="readOnly",
        max_iterations=30,
        max_consecutive_failures=3,
        capability_mode="restricted",
        source="builtin",
        max_tokens=8192,
        inherit_strategies=["spreadsheet:workflow", "tool:run_code"],
        system_prompt=(
            "你是只读探索子代理 `explorer`。\n"
            "职责：分析文件结构、预览数据、统计概况、定位目标内容。\n\n"
            "## 工作规范\n"
            "- 仅使用只读工具，不做任何写入操作。\n"
            "- 优先给出结构化、可引用的结果摘要。\n"
            "- 包含关键数字（行数、列数、数据范围、匹配数等）。\n"
            "- 完成后输出简洁的发现摘要，供主代理决策使用。\n\n"
            "## 效率优先\n"
            "- 简单任务不要强制拆解多步，一次工具调用能完成就直接输出结论。\n"
            "- 如果上下文已提供足够信息，无需额外探索即可直接汇报。\n"
            "- 避免重复读取已知信息。"
        ),
    ),
    "verifier": SubagentConfig(
        name="verifier",
        description=(
            "只读检查子代理。不在默认 delegate 推荐里；仅当主代理显式指定 "
            "agent_name=verifier 时运行。核对文件与数据，结论仅供参考，不接入自动门。"
        ),
        allowed_tools=_VERIFIER_TOOLS,
        permission_mode="readOnly",
        max_iterations=12,
        max_consecutive_failures=3,
        capability_mode="restricted",
        source="builtin",
        max_tokens=1024,
        inherit_strategies=["spreadsheet:workflow", "tool:run_code"],
        # system_prompt 作为 PromptComposer 回退（优先加载 prompts/subagent/verifier.md）
        system_prompt=(
            "你是只读检查子代理 `verifier`。\n"
            "职责：按主代理显式下达的检查目标，用只读工具核对文件与数据。\n"
            "你不是自动验收门：结论只供主代理参考，不要改写任务完成状态。\n\n"
            "## 检查流程\n"
            "1. 根据委托说明确定要看的文件和预期。\n"
            "2. 用 inspect_spreadsheet / analyze_spreadsheet 核对。\n"
            "3. 需要计算时用 run_code，代码严禁写入。\n\n"
            "## 输出格式\n"
            "最终输出必须是以下 JSON（不要包裹 markdown code fence）：\n"
            '{"verdict":"pass","confidence":"high","checks":["文件存在","数据行数正确"]}\n'
            "或\n"
            '{"verdict":"fail","confidence":"high","issues":["输出文件不存在"],'
            '"checks":["文件存在性检查"]}\n\n'
            "verdict 只能是 pass / fail / unknown。\n"
            "confidence 只能是 high / medium / low。"
        ),
    ),
}
