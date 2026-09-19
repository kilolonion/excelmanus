"""子代理数据模型。one-shot Run 的发布身份与终态契约。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

SubagentPermissionMode = Literal["default", "acceptEdits", "readOnly", "dontAsk"]
SubagentCapabilityMode = Literal["restricted", "full"]
SubagentMemoryScope = Literal["user", "project"]
SubagentSource = Literal["builtin", "user", "project"]
SubagentStopReason = Literal["completed", "aborted", "error", "max-tokens", "refusal"]
SubagentMode = Literal["one-shot"]


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
    max_tokens: int | None = None
    inherit_strategies: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SubagentDescriptor:
    """本地 one-shot 的发布身份。run_id 等于 child conversation_id。"""

    run_id: str
    agent_name: str
    parent_session_id: str = ""
    mode: SubagentMode = "one-shot"
    delegation_depth: int = 1
    provider: str = "in-process"
    local: bool = True


@dataclass
class SubagentStartRequest:
    """one-shot 启动请求。发布前校验，失败不发 start、不返回 id。"""

    task: str
    agent_name: str | None = None
    file_paths: list[str] = field(default_factory=list)
    label: str = ""
    on_event: object | None = None


@dataclass
class SubagentResult:
    """子代理终态。不因子代理失败而抛错；用 stop_reason 表达。"""

    stop_reason: SubagentStopReason
    output: str
    subagent_name: str
    permission_mode: SubagentPermissionMode
    conversation_id: str
    diagnostic: str | None = None
    iterations: int = 0
    tool_calls_count: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    structured_changes: list[SubagentFileChange] = field(default_factory=list)
    observed_files: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return self.stop_reason == "completed"

    @property
    def summary(self) -> str:
        return self.output

    @property
    def error(self) -> str | None:
        if self.stop_reason == "completed":
            return None
        return self.diagnostic or self.output or self.stop_reason

    @property
    def file_changes(self) -> list[str]:
        seen: set[str] = set()
        paths: list[str] = []
        for change in self.structured_changes:
            if change.path not in seen:
                seen.add(change.path)
                paths.append(change.path)
        return paths


class SubagentRun:
    """可销毁的 one-shot 句柄。result 在 settle 后可 await，失败不 reject。"""

    def __init__(self, run_id: str) -> None:
        import asyncio

        self.id = run_id
        self._future: asyncio.Future[SubagentResult] = asyncio.get_running_loop().create_future()
        self._dispose = None
        self._disposed = False

    @property
    def result(self) -> object:
        return self._future

    def set_result(self, result: SubagentResult) -> None:
        if not self._future.done():
            self._future.set_result(result)

    def set_dispose(self, dispose) -> None:
        self._dispose = dispose

    async def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        if self._dispose is not None:
            await self._dispose()
