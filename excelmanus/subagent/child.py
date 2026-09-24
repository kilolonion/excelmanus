"""子代理组合窗口：一次固定权限、工具可见性与深度。"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from excelmanus.logger import get_logger
from excelmanus.subagent.errors import SubagentError
from excelmanus.subagent.guard import reject_readonly_write
from excelmanus.subagent.models import SubagentConfig

logger = get_logger("subagent.child")

DEFAULT_MAX_DEPTH = 1

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


def resolve_child_depth(parent: Any, max_depth: int = DEFAULT_MAX_DEPTH) -> int:
    """父深度 + 1，超过上限 fail-loud。"""
    parent_depth = int(getattr(parent, "_delegation_depth", 0) or 0)
    child_depth = parent_depth + 1
    if child_depth > max_depth:
        raise SubagentError(
            "DEPTH_EXCEEDED",
            f"子代理深度 {child_depth} 超过上限 {max_depth}。",
        )
    return child_depth


def resolve_child_runtime(parent: Any, config: SubagentConfig) -> tuple[str, str, str]:
    """一次解析子会话的 model / api_key / base_url。配错即失败，不重试。"""
    if config.model:
        return (
            config.model,
            config.api_key or parent._active_api_key,
            config.base_url or parent._active_base_url,
        )
    return parent._active_model, parent._active_api_key, parent._active_base_url


def compose_child_prompt(child: Any, config: SubagentConfig) -> str:
    """只返回角色补充；通用 core、条件策略和工具目录使用共享组装入口。"""
    from excelmanus.prompt.assemble import prompt_variables
    from excelmanus.subagent.builtin import BUILTIN_SUBAGENTS

    composer = getattr(child, "_prompt_composer", None)
    if composer is None:
        raise ValueError("子代理提示词组装器未初始化")
    builtin = config.source == "builtin" and config.name in BUILTIN_SUBAGENTS
    if not builtin and not config.system_prompt.strip():
        raise ValueError(f"自定义子代理 {config.name} 缺少角色正文")
    body = composer.compose_for_subagent(
        config.name,
        variables=prompt_variables(child),
        role_text=None if builtin else config.system_prompt,
    )
    if not body:
        raise ValueError(f"子代理 {config.name} 缺少专用提示词文件")
    return body


def child_extra_disallowed(config: SubagentConfig) -> list[str]:
    """子代理额外屏蔽的元工具。full 仍禁止再委派，除非显式放开。"""
    return list(_FULL_META if config.capability_mode == "full" else _RESTRICTED_META)


def child_capability(parent: Any, config: SubagentConfig) -> Any:
    """父约束 ∩ 子配置。compose / 并行 / plan 门禁共用。"""
    from excelmanus.tools.context import capability_from_engine, intersect_capability

    parent_cap = getattr(parent, "_fixed_capability", None) or capability_from_engine(parent)
    return intersect_capability(
        parent_cap,
        permission_mode=str(getattr(config, "permission_mode", "default") or "default"),
        allowed_tools=list(config.allowed_tools) if config.allowed_tools else None,
        disallowed_tools=list(getattr(config, "disallowed_tools", None) or []),
        extra_disallowed=child_extra_disallowed(config),
    )


def assert_child_capability_subset(parent: Any, child: Any) -> None:
    """compose 之后、发布之前：child 只能是父能力子集。"""
    from excelmanus.tools.context import capability_from_engine

    parent_cap = getattr(parent, "_fixed_capability", None) or capability_from_engine(parent)
    child_cap = getattr(child, "_fixed_capability", None) or capability_from_engine(child)
    rank = {"read": 0, "plan": 1, "write": 2}
    if child_cap.catalog_mode not in rank or parent_cap.catalog_mode not in rank:
        raise SubagentError("PERMISSION_DENIED", "父子代理必须使用有效的 read/plan/write 权限模式。")
    if rank[child_cap.catalog_mode] > rank[parent_cap.catalog_mode]:
        raise SubagentError(
            "PERMISSION_DENIED",
            "子代理能力不能超过父会话（mode 被放大）。",
        )
    if parent_cap.tool_access == "read_only" and child_cap.tool_access != "read_only":
        raise SubagentError(
            "PERMISSION_DENIED",
            "子代理能力不能超过父会话（只读被抬高）。",
        )
    if parent_cap.allowed_tools is not None and child_cap.allowed_tools is not None:
        extra = child_cap.allowed_tools - parent_cap.allowed_tools
        if extra:
            raise SubagentError(
                "PERMISSION_DENIED",
                f"子代理工具超出父目录：{', '.join(sorted(extra)[:8])}",
            )
    if not set(child_cap.disallowed_tools) >= set(parent_cap.disallowed_tools):
        raise SubagentError(
            "PERMISSION_DENIED",
            "子代理未能继承父会话的禁用工具。",
        )


def compose_child(
    parent: Any,
    config: SubagentConfig,
    *,
    max_depth: int = DEFAULT_MAX_DEPTH,
) -> Any:
    """组合窗口：fork 目录、restrict、共享工作区/审批/MCP、钉死只读守卫。"""
    from excelmanus.agent.session import AgentEngine
    from excelmanus.config import ExcelManusConfig

    child_depth = resolve_child_depth(parent, max_depth=max_depth)
    model, api_key, base_url = resolve_child_runtime(parent, config)
    child_cap = child_capability(parent, config)
    # 在创建客户端/发布 run 之前校验，不以 config.system_prompt 掩盖损坏的内置角色。
    from types import SimpleNamespace
    from excelmanus.prompt.load import PromptComposer
    from excelmanus.prompt.registry import AssembleContext
    from excelmanus.tools.catalog import inspect_workspace_catalog

    try:
        parent_composer = getattr(parent, "_prompt_composer", None)
        if not isinstance(parent_composer, PromptComposer):
            raise ValueError("父会话缺少 PromptComposer")
        composer = parent_composer.fork()
        compose_child_prompt(SimpleNamespace(
            _prompt_composer=composer, config=parent._config, active_model=model,
            _runtime_vars={"workspace_root": str(parent._workspace.root_dir)},
        ), config)
        unknown = set(config.inherit_strategies) - {seg.name for seg in composer.strategy_segments}
        if unknown:
            raise ValueError(f"子代理继承了未注册策略: {', '.join(sorted(unknown))}")
        # 同时检查将实际注入的 core/策略变量，避免创建 client 后才发现残缺模板。
        visible = {
            tool.name for tool in parent.registry.get_all_tools()
            if tool.name not in child_cap.disallowed_tools
            and (child_cap.allowed_tools is None or tool.name in child_cap.allowed_tools)
        }
        composer.registry.assemble(AssembleContext(
            variables={"workspace_root": str(parent._workspace.root_dir), "model": model},
            chat_mode=child_cap.catalog_mode, plan_active=child_cap.catalog_mode == "plan",
            visible_tools=frozenset(visible),
            full_access=bool(getattr(parent, "_full_access_enabled", False)),
            new_workbook=bool(inspect_workspace_catalog(str(parent._workspace.root_dir))["new_workbook"]),
            strategy_names=frozenset(config.inherit_strategies) if config.inherit_strategies else None,
        ))
    except (ValueError, OSError) as exc:
        raise SubagentError("PROMPT_INVALID", str(exc)) from exc
    child_cfg: ExcelManusConfig = replace(
        parent._config,
        model=model,
        api_key=api_key,
        base_url=base_url,
        max_iterations=config.max_iterations,
        max_consecutive_failures=config.max_consecutive_failures,
    )
    registry = parent.registry.fork()
    registry.remove_tools(_REINIT_TOOL_NAMES)

    child = AgentEngine(
        child_cfg,
        registry,
        skill_router=None,
        mcp_manager=getattr(parent, "_mcp_manager", None),
        own_mcp_manager=False,
        database=None,
        workspace=parent._workspace,
        workspace_ref=getattr(parent, "_workspace_ref", None),
        role="child",
    )
    from excelmanus.tools.introspection_tools import register_introspection_tools

    register_introspection_tools(child.registry, engine=child)
    allowed = list(child_cap.allowed_tools) if child_cap.allowed_tools is not None else None
    child.registry.restrict(allowed=allowed, disallowed=list(child_cap.disallowed_tools))

    child._approval = parent._approval
    child._file_registry = parent._file_registry
    child._state._file_registry = parent._file_registry
    child._file_access_guard = parent._file_access_guard
    child._sandbox_env = parent._sandbox_env
    child._prompt_composer = composer
    child._bind_prompt_registry_runtime()
    child._full_access_enabled = bool(getattr(parent, "_full_access_enabled", False))
    child._subagent_config = config
    child._last_guard_deny = None
    child._delegation_depth = child_depth
    child._subagent_enabled = False
    child._fixed_capability = child_cap
    # Synchronous delegation shares the active parent turn budget.  Background
    # runs are composed while idle and therefore start their own budget later.
    child._inherited_turn_budget = getattr(parent, "_turn_budget", None)
    child._current_chat_mode = child_cap.catalog_mode
    # 每个 child 一个独立 prompt_cache_key：并行子代理前缀互异，
    # 共享 "em_session" 会让 KV 缓存互相踩踏（命中率归零）。
    import uuid as _uuid

    parent_sid = getattr(parent, "_session_id", None) or "anon"
    child._session_id = f"{parent_sid}-child-{_uuid.uuid4().hex[:12]}"

    from excelmanus.prompt.assemble import build_stable_system_prompt

    child._memory.system_prompt = build_stable_system_prompt(child)

    def _readonly_pre(token: Any) -> str:
        parent_call = getattr(token, "parent", None)
        args = getattr(token, "arguments", None)
        tool_def = None
        getter = getattr(child.registry, "get_tool", None)
        if callable(getter):
            tool_def = getter(token.name)
        reason = reject_readonly_write(
            config,
            token.name,
            parent_call=parent_call,
            arguments=args if isinstance(args, dict) else None,
            tool_def=tool_def,
        )
        if reason:
            child._last_guard_deny = reason
            return "deny"
        return "allow"

    child._tool_runtime.add_pre_execute(_readonly_pre)
    return child
