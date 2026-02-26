"""Protocol 接口定义 — 约束 engine_core 子组件对 Engine 的依赖边界。

每个 Protocol 按域关注点划分，子组件只依赖自己需要的窄接口，
而非整个 AgentEngine 引用。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from excelmanus.config import ExcelManusConfig
    from excelmanus.events import EventCallback
    from excelmanus.memory import ConversationMemory
    from excelmanus.skillpacks import SkillMatchResult


# ---------------------------------------------------------------------------
# 引擎配置（EngineConfig）— 只读配置访问
# ---------------------------------------------------------------------------

@runtime_checkable
class EngineConfig(Protocol):
    """只读配置与模型信息访问。"""

    @property
    def config(self) -> ExcelManusConfig: ...

    @property
    def active_model(self) -> str: ...

    @property
    def full_access_enabled(self) -> bool: ...

    @property
    def subagent_enabled(self) -> bool: ...

    @property
    def plan_mode_enabled(self) -> bool: ...


# ---------------------------------------------------------------------------
# 工具执行上下文（ToolExecutionContext）— ToolDispatcher 需要的工具执行上下文
# ---------------------------------------------------------------------------

@runtime_checkable
class ToolExecutionContext(Protocol):
    """ToolDispatcher 执行工具所需的上下文接口。"""

    @property
    def config(self) -> ExcelManusConfig: ...

    @property
    def registry(self) -> Any: ...

    @property
    def approval(self) -> Any: ...

    @property
    def state(self) -> Any: ...

    @property
    def sandbox_env(self) -> Any: ...

    @property
    def file_access_guard(self) -> Any: ...

    @property
    def transaction(self) -> Any: ...

    @property
    def workspace(self) -> Any: ...

    @property
    def full_access_enabled(self) -> bool: ...

    @property
    def window_perception(self) -> Any: ...

    def emit(self, on_event: EventCallback | None, event: Any) -> None: ...

    def record_write_action(self) -> None: ...

    def record_workspace_write_action(self, changed_files: list[str] | None = None) -> None: ...

    async def execute_tool_with_audit(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        tool_scope: Sequence[str] | None = None,
        approval_id: str | None = None,
        created_at_utc: str | None = None,
    ) -> tuple[str, bool, str | None, Any]: ...

    def format_pending_prompt(self, pending: Any) -> str: ...

    def emit_pending_approval_event(
        self,
        on_event: EventCallback | None,
        *,
        pending: Any,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> None: ...

    def get_tool_write_effect(self, tool_name: str) -> str: ...

    def redirect_backup_paths(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]: ...

    def pick_route_skill(self, route_result: SkillMatchResult) -> Any: ...

    def run_skill_hook(self, hook_event: Any, **kwargs: Any) -> Any: ...

    async def resolve_hook_result(self, raw: Any, **kwargs: Any) -> Any: ...

    def render_task_brief(self, task_brief: dict[str, Any]) -> str: ...


# ---------------------------------------------------------------------------
# VLM 上下文（VLMContext）— VLM/视觉相关能力
# ---------------------------------------------------------------------------

@runtime_checkable
class VLMContext(Protocol):
    """VLM/视觉相关能力接口。"""

    @property
    def is_vision_capable(self) -> bool: ...

    @property
    def vlm_enhance_available(self) -> bool: ...

    @property
    def vlm_client(self) -> Any: ...

    @property
    def vlm_model(self) -> str: ...


# ---------------------------------------------------------------------------
# 委托上下文（DelegationContext）— 子代理/技能委托
# ---------------------------------------------------------------------------

@runtime_checkable
class DelegationContext(Protocol):
    """子代理委托与技能激活接口。"""

    async def handle_activate_skill(self, name: str) -> str: ...

    async def delegate_to_subagent(
        self,
        *,
        task: str,
        tool_scope: Sequence[str] | None = None,
        on_event: EventCallback | None = None,
    ) -> Any: ...

    async def parallel_delegate_to_subagents(
        self,
        *,
        tasks: list[dict[str, Any]],
        tool_scope: Sequence[str] | None = None,
        on_event: EventCallback | None = None,
    ) -> Any: ...

    def handle_list_subagents(self) -> str: ...

    def handle_ask_user(
        self,
        question: str,
        *,
        on_event: EventCallback | None = None,
    ) -> tuple[str, str | None]: ...

    def enqueue_subagent_approval_question(
        self,
        *,
        pending: Any,
        delegate_outcome: Any,
    ) -> Any: ...


# ---------------------------------------------------------------------------
# 记忆访问（MemoryAccess）— 受控的 Memory 操作
# ---------------------------------------------------------------------------

@runtime_checkable
class MemoryAccess(Protocol):
    """外部消费者对 Memory 的合法操作接口。"""

    def add_image_message(
        self,
        *,
        base64_data: str,
        mime_type: str = "image/png",
        detail: str = "auto",
    ) -> None: ...

    def replace_tool_result(self, tool_call_id: str, content: str) -> bool: ...

    def remove_last_assistant_if(self, predicate: Callable[[str], bool]) -> bool: ...

    def get_messages(self, system_prompts: list[str] | None = None) -> list[dict]: ...

    def build_system_messages(self, system_prompts: list[str] | None = None) -> list[dict]: ...

    @property
    def messages(self) -> list[dict]: ...


# ---------------------------------------------------------------------------
# 工具处理器（ToolHandler）— 单个工具类型的执行策略
# ---------------------------------------------------------------------------

@runtime_checkable
class ToolHandler(Protocol):
    """单个工具类型的执行策略接口。

    ToolDispatcher 通过策略表遍历 handler 列表，
    第一个 can_handle 返回 True 的 handler 负责执行该工具。
    """

    def can_handle(
        self,
        tool_name: str,
        *,
        arguments: dict[str, Any] | None = None,
    ) -> bool:
        """判断此 handler 是否能处理指定工具。"""
        ...

    async def handle(
        self,
        tool_name: str,
        tool_call_id: str,
        arguments: dict[str, Any],
        *,
        tool_scope: Sequence[str] | None = None,
        on_event: Any = None,
        iteration: int = 0,
        route_result: Any = None,
    ) -> Any:
        """执行工具并返回 _ToolExecOutcome。"""
        ...
