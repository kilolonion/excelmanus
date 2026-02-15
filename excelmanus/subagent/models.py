"""Subagent 数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

SubagentPermissionMode = Literal["default", "acceptEdits", "readOnly", "dontAsk"]
SubagentMemoryScope = Literal["user", "project"]
SubagentSource = Literal["builtin", "user", "project"]


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
    system_prompt: str = ""


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
    error: str | None = None
    pending_approval_id: str | None = None
    file_changes: list[str] = field(default_factory=list)
    observed_files: list[str] = field(default_factory=list)
