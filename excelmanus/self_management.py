"""Default-on, session-local agent configuration. No process settings or secrets.

The settings UI owns the master switch. Tool execution rechecks it, caller
identity, chat mode and skill activation, including calls via the Python SDK.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from jsonschema import Draft202012Validator

from excelmanus.engine_core.tool_result import error_result, ok_result
from excelmanus.tools.context import current_call
from excelmanus.tools.registry import ToolDef

SKILL_NAME = "agent_self_management"
TOOL_NAMES = frozenset({"inspect_agent", "configure_agent"})
_PROTECTED_TOOLS = TOOL_NAMES | {"skill", "introspect_capability", "ask_user"}

# Explicit public contract: never serialize config.__dict__, profiles or MCP
# connection records, which can contain credentials and authenticated URLs.
SETTING_SCHEMAS: dict[str, dict[str, Any]] = {
    "thinking_effort": {"type": "string", "enum": ["none", "minimal", "low", "medium", "high", "xhigh", "max"], "description": "推理深度；还需属于当前设置允许的等级。"},
    "thinking_budget": {"type": "integer", "minimum": 0, "maximum": 1000000, "description": "推理 token 预算；0 使用等级换算。"},
    "subagent_enabled": {"type": "boolean", "description": "本会话是否允许委派子任务。"},
    "parallel_readonly_tools": {"type": "boolean", "description": "是否并发执行独立的只读工具。"},
    "parallel_tool_max": {"type": "integer", "minimum": 1, "maximum": 32, "description": "只读工具并发上限。"},
    "max_iterations": {"type": "integer", "minimum": 0, "description": "从下一轮用户请求开始使用的调用上限；0 表示不限制。"},
    "max_context_tokens": {"type": "integer", "minimum": 1000, "maximum": 10000000, "description": "上下文窗口上限；需符合当前模型容量。"},
    "compaction_enabled": {"type": "boolean", "description": "是否自动压缩上下文。"},
    "tool_result_hard_cap_chars": {"type": "integer", "minimum": 1000, "maximum": 100000, "description": "工具结果字符上限。"},
    "skills_context_char_budget": {"type": "integer", "minimum": 1000, "maximum": 100000, "description": "技能正文字符预算。"},
}
_READ_ONLY_SETTINGS = (
    "agent_self_management_enabled", "protocol", "memory_enabled",
    "memory_auto_load_lines", "memory_expire_days", "max_consecutive_failures",
    "turn_timeout_seconds", "turn_token_budget", "turn_cost_budget_usd",
    "subagent_max_iterations", "subagent_timeout_seconds", "parallel_subagent_max",
    "prompt_cache_key_enabled", "prompt_cache_retention",
    "compaction_threshold_ratio", "compaction_keep_recent_turns",
    "main_model_vision", "image_pixel_budget", "image_max_bytes", "image_files_api",
    "skills_discovery_enabled", "exa_search_enabled", "search_default_provider",
    "code_policy_enabled", "code_policy_green_auto_approve", "code_policy_yellow_auto_approve",
    "hooks_command_enabled", "tool_schema_validation_mode", "jev_enabled",
)
_CHANGES_SCHEMA = {
    "type": "object", "properties": SETTING_SCHEMAS,
    "additionalProperties": False,
}


def enabled(engine: Any) -> bool:
    return (getattr(engine, "_is_host_session", True) is True
            and getattr(getattr(engine, "_config", None), "agent_self_management_enabled", False) is True)


def disallowed_tools(engine: Any) -> set[str]:
    """Applied to both discovery and execution, also for fixed child scopes."""
    names = set(getattr(engine, "_self_disabled_tools", ()))
    if not enabled(engine):
        names.update(TOOL_NAMES)
    return names


def set_enabled(engine: Any, value: bool) -> None:
    """Host settings entry point; deliberately absent from the tool contract."""
    engine._config = replace(engine._config, agent_self_management_enabled=value)
    router = getattr(engine, "_skill_router", None)
    if router is not None:
        router._config = engine._config
        router._loader._config = engine._config
    if not value:
        engine._active_skills = [s for s in engine._active_skills if s.name != SKILL_NAME]
        engine._loaded_skill_names.pop(SKILL_NAME, None)
    engine._invalidate_tool_catalog()
    from excelmanus.tools.catalog import bind_engine_catalog
    bind_engine_catalog(engine)


def _check_access(engine: Any, *, write: bool = False):
    if not enabled(engine):
        return error_result("Agent 自我管理未启用。请由用户在设置 → 系统 → 能力中开启。", code="SELF_MANAGEMENT_DISABLED")
    call = current_call()
    if (call is None or call.binding.actor != "host"
            or call.binding.session_id != str(getattr(engine, "_session_id", None) or "")):
        return error_result("自我管理仅限当前主会话调用。", code="SELF_MANAGEMENT_FORBIDDEN")
    name = "configure_agent" if write else "inspect_agent"
    cap = call.binding.capability
    if name in cap.disallowed_tools or (cap.allowed_tools is not None and name not in cap.allowed_tools):
        return error_result("当前调用未获授此自我管理工具。", code="SELF_MANAGEMENT_FORBIDDEN")
    if not any(s.name == SKILL_NAME for s in engine._active_skills):
        return error_result(f"请先通过 skill 工具加载 {SKILL_NAME}。", code="SELF_MANAGEMENT_SKILL_REQUIRED")
    if write and (call.binding.capability.catalog_mode != "write"
                  or call.binding.capability.tool_access == "read_only"
                  or getattr(engine, "_current_chat_mode", "write") != "write"):
        return error_result("只读或计划模式下不可修改自身配置。", code="SELF_MANAGEMENT_READ_ONLY")
    return None


def _values(engine: Any) -> dict[str, Any]:
    values = {key: getattr(engine._config, key) for key in (*SETTING_SCHEMAS, *_READ_ONLY_SETTINGS)}
    values.update(thinking_effort=engine.thinking_config.effort,
                  thinking_budget=engine.thinking_config.budget_tokens,
                  subagent_enabled=engine.subagent_enabled,
                  max_context_tokens=engine.max_context_tokens)
    return values


def settings_snapshot(engine: Any) -> dict[str, dict[str, Any]]:
    """Public setting allowlist shared with the read-only knowledge portal."""
    from copy import deepcopy

    return {
        name: {"value": value, "writable": name in SETTING_SCHEMAS,
               "schema": deepcopy(SETTING_SCHEMAS.get(name)),
               "effective": ("host_managed" if name not in SETTING_SCHEMAS
                             else "next_turn" if name == "max_iterations" else "next_call")}
        for name, value in _values(engine).items()
    }


def inspect_agent(engine: Any, section: str = "all"):
    denied = _check_access(engine)
    if denied is not None:
        return denied
    if section not in {"all", "capabilities", "settings"}:
        return error_result("section 必须为 all、capabilities 或 settings。", code="INVALID_ARGS")
    data: dict[str, Any] = {"scope": "session", "persisted": False}
    if section in {"all", "settings"}:
        data["settings"] = settings_snapshot(engine)
        data["thinking_effort_options"] = list(engine._config.thinking_effort_options)
        data["omitted"] = "凭证、模型档案、连接地址、命令和文件路径不向模型披露。未列出的配置不支持此工具修改。"
    if section in {"all", "capabilities"}:
        from excelmanus.tools.catalog import execution_catalog_from_engine
        catalog = execution_catalog_from_engine(engine)
        call = current_call()
        active = {s.name for s in engine._active_skills}
        loader = getattr(getattr(engine, "_skill_router", None), "_loader", None)
        skills = loader.get_skillpacks() if loader is not None else {}
        blocked = engine._skill_resolver.blocked_skillpacks() or set()
        data["capabilities"] = {
            "model": engine.current_model,
            "vision": bool(engine._is_vision_capable),
            "chat_mode": call.binding.capability.catalog_mode,
            "approval": call.binding.capability.approval,
            "full_access": call.binding.capability.full_access,
            "subagent_enabled": engine.subagent_enabled,
            "tools": sorted(catalog.names()) if catalog else [],
            "disabled_tools": sorted(getattr(engine, "_self_disabled_tools", ())),
            "protected_tools": sorted(_PROTECTED_TOOLS),
            "skills": [{"name": name, "active": name in active,
                        "available": name not in blocked,
                        "model_invocable": not skill.disable_model_invocation}
                       for name, skill in sorted(skills.items())],
        }
    return ok_result(data)


def configure_agent(engine: Any, *, changes: dict | None = None,
                    disable_tools: list[str] | None = None,
                    enable_tools: list[str] | None = None, reason: str = ""):
    denied = _check_access(engine, write=True)
    if denied is not None:
        return denied
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 1000:
        return error_result("请提供 1–1000 字符的修改原因。", code="INVALID_ARGS")
    changes = {} if changes is None else changes
    errors = list(Draft202012Validator(_CHANGES_SCHEMA).iter_errors(changes))
    if errors:
        # Do not echo potentially secret values supplied under unknown keys.
        return error_result("配置字段、类型或范围无效；请按 inspect_agent 返回的 schema 修改。", code="INVALID_ARGS")
    lists = [disable_tools, enable_tools]
    if any(v is not None and (not isinstance(v, list) or len(v) > 200
                             or any(not isinstance(n, str) for n in v)) for v in lists):
        return error_result("工具名单必须为不超过 200 项的字符串数组。", code="INVALID_ARGS")
    disable, enable = set(disable_tools or []), set(enable_tools or [])
    if not changes and not disable and not enable:
        return error_result("至少提供一个配置或工具变更。", code="INVALID_ARGS")
    if disable & enable or disable & _PROTECTED_TOOLS:
        return error_result("不可同时启停同一工具，也不可禁用自我管理、技能或用户交互入口。", code="INVALID_ARGS")
    registered = set(engine._registry.get_tool_names())
    if (disable | enable) - registered:
        return error_result("只能调整当前会话已注册的工具；此入口不能安装新工具。", code="INVALID_ARGS")
    if "thinking_effort" in changes and changes["thinking_effort"] not in engine._config.thinking_effort_options:
        return error_result("推理等级不在用户允许的选项中。", code="INVALID_ARGS")

    # All validation precedes mutation. Copy the immutable config so engines
    # constructed with the same config never change one another's defaults.
    before = _values(engine)
    old_disabled = set(getattr(engine, "_self_disabled_tools", ()))
    engine._config = replace(engine._config, **changes)
    engine.set_thinking_config(effort=changes.get("thinking_effort"), budget=changes.get("thinking_budget"))
    if "subagent_enabled" in changes:
        engine._subagent_enabled = changes["subagent_enabled"]
    engine.apply_context_optimization(**{key: changes[key] for key in ("max_context_tokens", "compaction_enabled") if key in changes})
    router = getattr(engine, "_skill_router", None)
    if router is not None:
        router._config = engine._config
        router._loader._config = engine._config
    engine._self_disabled_tools = (old_disabled | disable) - enable
    engine._invalidate_tool_catalog()
    from excelmanus.tools.catalog import bind_engine_catalog
    catalog = bind_engine_catalog(engine)
    after = _values(engine)
    return ok_result({
        "scope": "session", "persisted": False, "reason": reason.strip(),
        "changes": {key: {"before": before[key], "after": after[key],
                           "effective": "next_turn" if key == "max_iterations" else "next_call"}
                    for key in changes},
        "disabled_tools": sorted(engine._self_disabled_tools),
        "enabled_tools": sorted(enable & set(catalog.names()) if catalog else set()),
        "note": "仅当前内存会话生效，重建会话后恢复默认；启用工具仍受当前模式、工作区及宿主授权限制。",
    })


def get_tools(engine: Any) -> list[ToolDef]:
    def inspect(section: str = "all"):
        return inspect_agent(engine, section)

    def configure(changes: dict | None = None, disable_tools: list[str] | None = None,
                  enable_tools: list[str] | None = None, reason: str = ""):
        return configure_agent(engine, changes=changes, disable_tools=disable_tools,
                               enable_tools=enable_tools, reason=reason)

    # Stay on the host event loop: a settings-page revocation and a complete
    # configuration update cannot interleave across a worker-thread boundary.
    async def inspect_async(section: str = "all"):
        return inspect(section)

    async def configure_async(changes: dict | None = None, disable_tools: list[str] | None = None,
                              enable_tools: list[str] | None = None, reason: str = ""):
        return configure(changes, disable_tools, enable_tools, reason)

    output_schema = {"type": "object", "additionalProperties": True}
    return [
        ToolDef(name="inspect_agent", description=f"查询本会话的真实能力、技能和可配置项；先加载 {SKILL_NAME} 技能。凭证不披露。",
                input_schema={"type": "object", "properties": {"section": {"type": "string", "enum": ["all", "capabilities", "settings"]}}, "additionalProperties": False},
                func=inspect, async_func=inspect_async, write_effect="none", max_result_chars=0, output_schema=output_schema),
        ToolDef(name="configure_agent", description=f"修改本会话的运行配置或停用/恢复已注册工具；先加载 {SKILL_NAME} 并 inspect_agent。不会保存为全局默认或提升权限。",
                input_schema={"type": "object", "properties": {
                    "changes": _CHANGES_SCHEMA,
                    "disable_tools": {"type": "array", "items": {"type": "string"}, "maxItems": 200},
                    "enable_tools": {"type": "array", "items": {"type": "string"}, "maxItems": 200},
                    "reason": {"type": "string", "minLength": 1, "maxLength": 1000},
                }, "required": ["reason"], "additionalProperties": False},
                func=configure, async_func=configure_async, write_effect="dynamic", max_result_chars=0, output_schema=output_schema),
    ]
