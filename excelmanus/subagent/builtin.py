"""内置子代理定义。

v6: 移除所有分角色子代理（explorer/planner/analyst/writer/coder/full），
只保留一个通用全能力子代理 ``subagent``。
工具域与主代理一致，capability_mode=full，permission_mode=acceptEdits。
用户仍可通过 project/user 目录的 .md 文件自定义子代理。
"""

from __future__ import annotations

from excelmanus.subagent.models import SubagentConfig


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
}
