"""子 Driver：独立 inbox + 共享 step 循环。审批钉在父会话上。"""

from __future__ import annotations

from dataclasses import replace
from typing import Any
from uuid import uuid4

from excelmanus.agent.inbox import Inbox
from excelmanus.events import EventType, ToolCallEvent
from excelmanus.logger import get_logger
from excelmanus.subagent.guard import reject_readonly_write
from excelmanus.subagent.models import SubagentConfig, SubagentResult

logger = get_logger("subagent.child")

_REINIT_TOOL_NAMES = (
    "task_create",
    "task_update",
    "write_plan",
    "exit_plan_mode",
    "introspect_capability",
)
_RESTRICTED_META = frozenset(
    {
        "skill",
        "activate_skill",
        "delegate",
        "delegate_to_subagent",
        "parallel_delegate",
        "list_subagents",
    }
)
_FULL_META = frozenset(
    {"delegate", "delegate_to_subagent", "parallel_delegate", "list_subagents"}
)


def resolve_child_runtime(parent: Any, config: SubagentConfig) -> tuple[str, str, str]:
    """一次解析子会话的 model / api_key / base_url。配错即失败，不重试。"""
    if config.model:
        return (
            config.model,
            config.api_key or parent._active_api_key,
            config.base_url or parent._active_base_url,
        )
    return parent._active_model, parent._active_api_key, parent._active_base_url


def _compose_child_prompt(parent: Any, config: SubagentConfig) -> str:
    composer = getattr(parent, "_prompt_composer", None)
    variables = dict(getattr(parent, "_runtime_vars", None) or {})
    if composer is not None:
        try:
            text = composer.compose_for_subagent(
                config.name,
                inherit_strategies=config.inherit_strategies or None,
                variables=variables or None,
            )
            if text and text.strip():
                return text.strip()
        except Exception:
            logger.debug("子代理提示词组装失败，使用 config.system_prompt", exc_info=True)
    if config.system_prompt.strip():
        return config.system_prompt.strip()
    return (
        f"你是子代理 `{config.name}`。\n"
        f"职责：{config.description}\n"
        "忠于工具结果，只在授权范围内操作，路径相对工作区。"
    )


def spawn_child_engine(parent: Any, config: SubagentConfig) -> Any:
    """为子代理造一份 scoped 会话：共享工作区与审批，独立 memory / Driver / 目录。"""
    from excelmanus.agent.session import AgentEngine
    from excelmanus.config import ExcelManusConfig

    model, api_key, base_url = resolve_child_runtime(parent, config)
    child_cfg: ExcelManusConfig = replace(
        parent._config,
        model=model,
        api_key=api_key,
        base_url=base_url,
        max_iterations=config.max_iterations,
        max_consecutive_failures=config.max_consecutive_failures,
    )
    registry = parent.registry.fork()
    for name in _REINIT_TOOL_NAMES:
        registry._tools.pop(name, None)

    child = AgentEngine(
        child_cfg,
        registry,
        skill_router=None,
        mcp_manager=getattr(parent, "_mcp_manager", None),
        own_mcp_manager=False,
        database=None,
        workspace=parent._workspace,
        role="child",
    )
    blocked = set(config.disallowed_tools)
    blocked.update(_FULL_META if config.capability_mode == "full" else _RESTRICTED_META)
    allowed = list(config.allowed_tools) if config.allowed_tools else None
    child.registry.restrict(allowed=allowed, disallowed=blocked)

    child._approval = parent._approval
    child._file_registry = parent._file_registry
    child._state._file_registry = parent._file_registry
    child._file_access_guard = parent._file_access_guard
    child._sandbox_env = parent._sandbox_env
    child._prompt_composer = getattr(parent, "_prompt_composer", None)
    child._full_access_enabled = bool(getattr(parent, "_full_access_enabled", False))
    child._child_system_prompt = _compose_child_prompt(parent, config)
    child._memory.system_prompt = child._child_system_prompt
    child._subagent_config = config
    child._last_guard_deny = None
    child._present_as = "native"
    if config.permission_mode == "readOnly":
        child._current_chat_mode = "read"
    elif getattr(parent, "_current_chat_mode", "write") == "read":
        child._current_chat_mode = "read"

    def _readonly_pre(token: Any) -> str:
        reason = reject_readonly_write(config, token.name)
        if reason:
            child._last_guard_deny = reason
            return "deny"
        return "allow"

    child._tool_runtime.add_pre_execute(_readonly_pre)
    return child


def _chat_to_subagent_result(
    chat: Any,
    *,
    config: SubagentConfig,
    conversation_id: str,
) -> SubagentResult:
    tool_calls = list(getattr(chat, "tool_calls", None) or [])
    denied = [
        tc
        for tc in tool_calls
        if (getattr(tc, "error", None) == "PRE_EXECUTE_DENIED")
        or ("拒绝写入" in str(getattr(tc, "result", "") or ""))
    ]
    reply = str(getattr(chat, "reply", "") or "")
    truncated = bool(getattr(chat, "truncated", False))
    if denied:
        last = denied[-1]
        err = str(getattr(last, "result", None) or getattr(last, "error", "") or "PRE_EXECUTE_DENIED")
        return SubagentResult(
            success=False,
            summary=reply or err,
            error=err,
            subagent_name=config.name,
            permission_mode=config.permission_mode,
            conversation_id=conversation_id,
            iterations=int(getattr(chat, "iterations", 0) or 0),
            tool_calls_count=len(tool_calls),
            prompt_tokens=int(getattr(chat, "prompt_tokens", 0) or 0),
            completion_tokens=int(getattr(chat, "completion_tokens", 0) or 0),
        )
    return SubagentResult(
        success=not truncated,
        summary=reply,
        error=reply if truncated else None,
        subagent_name=config.name,
        permission_mode=config.permission_mode,
        conversation_id=conversation_id,
        iterations=int(getattr(chat, "iterations", 0) or 0),
        tool_calls_count=len(tool_calls),
        prompt_tokens=int(getattr(chat, "prompt_tokens", 0) or 0),
        completion_tokens=int(getattr(chat, "completion_tokens", 0) or 0),
    )


class ChildDriver:
    """独立 inbox 的子会话，一步执行走与父会话相同的 ``run_tool_loop``。"""

    def __init__(
        self,
        parent: Any,
        *,
        agent_name: str,
        config: SubagentConfig | None = None,
    ) -> None:
        self.parent = parent
        self.agent_name = agent_name
        self.config = config
        self.inbox = Inbox()

    async def run(self, task: str, *, on_event: Any = None) -> SubagentResult:
        parent = self.parent
        config = self.config
        if config is None:
            registry = getattr(parent, "_subagent_registry", None)
            config = registry.get(self.agent_name) if registry is not None else None
        if config is None:
            return SubagentResult(
                success=False,
                summary=f"未找到子代理: {self.agent_name}",
                error=f"SubagentNotFound: {self.agent_name}",
                subagent_name=self.agent_name,
                permission_mode="default",
                conversation_id="",
            )

        item = self.inbox.push_followup(task)
        claimed = self.inbox.claim("next-turn", turn=1, step=1)
        if not claimed:
            return SubagentResult(
                success=False,
                summary="子 Driver 没有认领到任务。",
                error="子 Driver 没有认领到任务。",
                subagent_name=config.name,
                permission_mode=config.permission_mode,
                conversation_id="",
            )

        conversation_id = str(uuid4())
        emit = getattr(parent, "_emit", None)
        if callable(emit):
            emit(
                on_event,
                ToolCallEvent(
                    event_type=EventType.SUBAGENT_START,
                    subagent_name=config.name,
                    subagent_reason=str(item.content or task),
                    subagent_permission_mode=config.permission_mode,
                    subagent_conversation_id=conversation_id,
                ),
            )

        child = spawn_child_engine(parent, config)
        child_item = child._driver.enqueue_followup(
            str(item.content or task),
            extra={"on_event": on_event},
        )
        try:
            await child._driver.kick()
            chat = child_item.result
            if chat is None:
                result = SubagentResult(
                    success=False,
                    summary="子 Driver 没有返回结果。",
                    error="子 Driver 没有返回结果。",
                    subagent_name=config.name,
                    permission_mode=config.permission_mode,
                    conversation_id=conversation_id,
                )
            else:
                result = _chat_to_subagent_result(
                    chat,
                    config=config,
                    conversation_id=conversation_id,
                )
        except Exception as exc:
            logger.warning("子 Driver 执行失败: %s", exc, exc_info=True)
            result = SubagentResult(
                success=False,
                summary=str(exc),
                error=str(exc),
                subagent_name=config.name,
                permission_mode=config.permission_mode,
                conversation_id=conversation_id,
            )

        if callable(emit):
            emit(
                on_event,
                ToolCallEvent(
                    event_type=EventType.SUBAGENT_END,
                    subagent_name=config.name,
                    subagent_success=result.success,
                    subagent_conversation_id=conversation_id,
                    subagent_iterations=result.iterations,
                    subagent_tool_calls=result.tool_calls_count,
                ),
            )
        return result


async def start_child_driver(
    parent: Any,
    *,
    task: str,
    agent_name: str,
    on_event: Any = None,
) -> SubagentResult:
    """启动子 Driver → 等结束。``AgentEngine.run_subagent`` 是对外入口。"""
    child = ChildDriver(parent, agent_name=agent_name)
    return await child.run(task, on_event=on_event)
