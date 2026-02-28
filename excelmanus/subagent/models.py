"""Subagent 数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

SubagentPermissionMode = Literal["default", "acceptEdits", "readOnly", "dontAsk"]
SubagentCapabilityMode = Literal["restricted", "full"]
SubagentMemoryScope = Literal["user", "project"]
SubagentSource = Literal["builtin", "user", "project"]


@dataclass(frozen=True)
class SubagentFileChange:
    """子代理单次文件变更的结构化描述。"""

    path: str
    tool_name: str
    change_type: str = "write"  # 取值：write | format | delete | create | code_modified
    sheets_affected: tuple[str, ...] = ()


@dataclass(frozen=True)
class SubagentConfig:
    """子代理配置定义。"""

    name: str
    description: str
    model: str | None = None
    api_key: str | None = None
    base_url: str | None = None
    allowed_tools: list[str] = field(default_factory=list)
    disallowed_tools: list[str] = field(default_factory=list)
    permission_mode: SubagentPermissionMode = "default"
    max_iterations: int = 120
    max_consecutive_failures: int = 2
    skills: list[str] = field(default_factory=list)
    memory_scope: SubagentMemoryScope | None = None
    source: SubagentSource = "builtin"
    capability_mode: SubagentCapabilityMode = "restricted"
    system_prompt: str = ""
    max_tokens: int | None = None  # LLM 生成上限（None=模型默认）


@dataclass
class SubagentResult:
    """子代理执行结果。"""

    success: bool
    summary: str
    subagent_name: str
    permission_mode: SubagentPermissionMode
    conversation_id: str
    iterations: int = 0
    tool_calls_count: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    error: str | None = None
    pending_approval_id: str | None = None
    structured_changes: list[SubagentFileChange] = field(default_factory=list)
    observed_files: list[str] = field(default_factory=list)

    @property
    def file_changes(self) -> list[str]:
        """返回去重的变更路径列表。"""
        seen: set[str] = set()
        paths: list[str] = []
        for change in self.structured_changes:
            if change.path not in seen:
                seen.add(change.path)
                paths.append(change.path)
        return paths
