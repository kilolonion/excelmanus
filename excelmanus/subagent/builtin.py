"""内置子代理定义。

提供三个内置子代理：

- ``subagent``：通用全能力子代理，工具域与主代理一致。
- ``explorer``：只读探索子代理，仅拥有只读工具，适用于文件结构分析与数据预览。
- ``verifier``：完成前验证子代理，只读工具，用于任务完成前自动校验质量。

用户仍可通过 project/user 目录的 .md 文件自定义子代理。
"""

from __future__ import annotations

from excelmanus.subagent.models import SubagentConfig

# explorer 探索工具白名单
# 基于 READ_ONLY_SAFE_TOOLS 子集 + run_code（分析性计算）+ read_text_file（非 Excel 文件）
_EXPLORER_TOOLS: list[str] = [
    "read_excel",
    "list_sheets",
    "inspect_excel_files",
    "filter_data",
    "list_directory",
    "read_image",
    "introspect_capability",
    "run_code",        # 分析性计算（pandas describe/value_counts 等），由 prompt + code_policy 约束只读
    "read_text_file",  # 读取非 Excel 文件（CSV header、README、配置文件等）
]

# verifier 验证工具白名单（探索 + 计算验证）
_VERIFIER_TOOLS: list[str] = [
    "read_excel",
    "list_sheets",
    "inspect_excel_files",
    "filter_data",
    "list_directory",
    "run_code",        # 计算验证（行数校验、聚合比对、公式检查等），由 prompt + code_policy 约束只读
    "read_text_file",  # 读取非 Excel 文件（CSV、日志等验证辅助）
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
    ),
    "explorer": SubagentConfig(
        name="explorer",
        description=(
            "数据探索子代理，用于文件结构分析、数据预览、统计概况与数据质量检测。"
            "适用于查看/分析/读取/统计/定位/profiling 等探索任务，"
            "支持 run_code 做分析性计算（pandas/openpyxl 只读操作）。"
        ),
        allowed_tools=_EXPLORER_TOOLS,
        permission_mode="readOnly",
        max_iterations=30,
        max_consecutive_failures=3,
        capability_mode="restricted",
        source="builtin",
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
            "完成前验证子代理，用于在任务完成前校验是否真正完成。"
            "检查输出文件是否存在、数据是否正确写入、关键指标是否符合预期。"
            "支持 run_code 做计算验证（行数校验、聚合比对、公式检查等）。"
        ),
        allowed_tools=_VERIFIER_TOOLS,
        permission_mode="readOnly",
        max_iterations=20,
        max_consecutive_failures=3,
        capability_mode="restricted",
        source="builtin",
        # system_prompt 作为 PromptComposer 回退（优先加载 prompts/subagent/verifier.md）
        system_prompt=(
            "你是验证子代理 `verifier`。\n"
            "职责：校验主代理声称已完成的任务是否真正完成。\n\n"
            "## 验证流程\n"
            "1. 根据任务描述和变更记录确定需要检查的文件和预期结果。\n"
            "2. 用只读工具检查输出文件是否存在、内容是否正确。\n"
            "3. 用 run_code 做计算验证（行数校验、聚合比对、公式检查等），代码严禁写入。\n"
            "4. 核对关键数字（行数、列数、数据值等）。\n\n"
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
