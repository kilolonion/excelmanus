"""内置子代理定义。

提供三个内置子代理：

- ``subagent``：通用全能力子代理，工具域与主代理一致。
- ``explorer``：只读探索子代理，仅拥有只读工具，适用于文件结构分析与数据预览。
- ``verifier``：完成前验证子代理，只读工具，用于 finish_task 前自动校验任务完成质量。

用户仍可通过 project/user 目录的 .md 文件自定义子代理。
"""

from __future__ import annotations

from excelmanus.subagent.models import SubagentConfig

# explorer 只读工具白名单（与 policy.READ_ONLY_SAFE_TOOLS 子集对齐）
_EXPLORER_TOOLS: list[str] = [
    "read_excel",
    "list_sheets",
    "inspect_excel_files",
    "filter_data",
    "list_directory",
    "read_image",
    "introspect_capability",
]

# verifier 只读工具白名单（探索 + 验证所需的最小集合）
_VERIFIER_TOOLS: list[str] = [
    "read_excel",
    "list_sheets",
    "inspect_excel_files",
    "filter_data",
    "list_directory",
]


BUILTIN_SUBAGENTS: dict[str, SubagentConfig] = {
    "subagent": SubagentConfig(
        name="subagent",
        description="通用全能力子代理，工具域与主代理一致，适用于需要独立上下文的长任务。",
        allowed_tools=[],
        permission_mode="acceptEdits",
        max_iterations=120,
        max_consecutive_failures=2,
        capability_mode="full",
        source="builtin",
    ),
    "explorer": SubagentConfig(
        name="explorer",
        description=(
            "只读探索子代理，用于文件结构分析、数据预览与统计。"
            "适用于查看/分析/读取/统计/定位等不涉及写入的探索任务。"
        ),
        allowed_tools=_EXPLORER_TOOLS,
        permission_mode="readOnly",
        max_iterations=30,
        max_consecutive_failures=2,
        capability_mode="restricted",
        source="builtin",
        system_prompt=(
            "你是只读探索子代理 `explorer`。\n"
            "职责：分析文件结构、预览数据、统计概况、定位目标内容。\n\n"
            "## 工作规范\n"
            "- 仅使用只读工具，不做任何写入操作。\n"
            "- 优先给出结构化、可引用的结果摘要。\n"
            "- 包含关键数字（行数、列数、数据范围、匹配数等）。\n"
            "- 完成后输出简洁的发现摘要，供主代理决策使用。"
        ),
    ),
    "verifier": SubagentConfig(
        name="verifier",
        description=(
            "完成前验证子代理，用于在 finish_task 前校验任务是否真正完成。"
            "检查输出文件是否存在、数据是否正确写入、关键指标是否符合预期。"
        ),
        allowed_tools=_VERIFIER_TOOLS,
        permission_mode="readOnly",
        max_iterations=15,
        max_consecutive_failures=2,
        capability_mode="restricted",
        source="builtin",
        system_prompt=(
            "你是验证子代理 `verifier`。\n"
            "职责：校验主代理声称已完成的任务是否真正完成。\n\n"
            "## 验证流程\n"
            "1. 根据任务描述确定需要检查的文件和预期结果。\n"
            "2. 用只读工具检查输出文件是否存在、内容是否正确。\n"
            "3. 核对关键数字（行数、列数、数据值等）。\n\n"
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
