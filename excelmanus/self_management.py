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


_PROFILE_ALTERNATIVES = {
    "csv": "当前工作目录是 CSV-only profile（还没有 xlsx）：可直接用 apply_spreadsheet_changes(workbook_spec=...) "
           "新建 outputs/ 下的 xlsx（新建不依赖已有工作簿），或用 convert_spreadsheet 做真正的格式转换；"
           "依赖已有工作簿的工具要等 outputs/ 出现 xlsx 后下一轮才生效。",
    "docx": "当前工作目录是 docx-only profile：先在工作区生成/放入 xlsx 后再重试。",
}
_DEFAULT_PROFILE_ALTERNATIVE = ("先在工作区生成/放入该工具支持的文件族（例如 outputs/ 下的 xlsx）后重试，"
                                "或改用当前目录内已可用的工具。")
_NO_OP_REMEDIATION = {
    "not_paused": "该工具并未被暂停，已经在执行目录中：直接调用它即可；若模型可见目录里没有它，"
                  "先用 inspect_agent(section='capabilities') 核对，不要重复 configure_agent。",
    "gated_by_profile": "该工具被当前工作目录 profile 门控：用 apply_spreadsheet_changes(workbook_spec=...) 新建，"
                        "或用 convert_spreadsheet 在 outputs/ 生成 xlsx（或放入对应文件族）后重试，"
                        "或改用本目录内已可用的工具。",
    "unauthorized": "该工具被宿主授权排除：需要它请让用户在设置中放开授权；自我管理不能提升权限。",
    "already_disabled": "该工具已被本会话暂停，无需重复暂停；要恢复请用 enable_tools。",
    "already_unavailable": "该工具当前不在执行目录中（被 profile 或授权门控），暂停它不会改变现状。",
}


def _workspace_profile(engine: Any) -> str:
    """当前工作目录 profile（xlsx/csv/docx）。绑定过的引擎直接读缓存。"""
    profile = str(getattr(engine, "_catalog_profile", "") or "")
    if profile:
        return profile
    root = getattr(getattr(engine, "_config", None), "workspace_root", None)
    if not root:
        return ""
    try:
        from excelmanus.tools.catalog import inspect_workspace_catalog
        return str(inspect_workspace_catalog(str(root)).get("profile") or "")
    except Exception:  # 目录不可读时按"未知 profile"降级，不影响主判定
        return ""


def _name_reasons(engine: Any, *, disable: set[str], enable: set[str]) -> dict[str, dict[str, Any]]:
    """逐名说明每个工具请求为什么生效或不生效；只读，不改任何状态。"""
    from excelmanus.tools.catalog import execution_catalog_from_engine

    registered = set(engine._registry.get_tool_names())
    catalog = execution_catalog_from_engine(engine)
    available = set(catalog.names()) if catalog else set()
    paused = set(getattr(engine, "_self_disabled_tools", ()))
    # 只有宿主冻结的能力（_fixed_capability）才是授权口径；动态派生的
    # capability.disallowed_tools 就是本会话的暂停集合，用它会把"暂停"误报成"越权"。
    fixed = getattr(engine, "_fixed_capability", None)
    denied = set(getattr(fixed, "disallowed_tools", ()) or ())
    allowed = getattr(fixed, "allowed_tools", None)
    profile = _workspace_profile(engine)
    rows: dict[str, dict[str, Any]] = {}
    for name in sorted(disable | enable):
        wants_enable = name in enable
        row: dict[str, Any] = {"request": "enable" if wants_enable else "disable",
                               "registered": name in registered, "available_in_catalog": name in available,
                               "effective": False, "reason": "", "detail": "", "alternative": ""}
        if name not in registered:
            row.update(reason="unknown",
                       detail="该名不在本会话注册表中；此入口只调整已注册工具，不能安装新工具。",
                       alternative="用 inspect_agent(section='capabilities') 查看本会话真实可用的工具名，改用其中的工具。")
        elif name in denied or (allowed is not None and name not in allowed):
            row.update(reason="unauthorized", detail="宿主授权范围排除了该工具；自我管理不能提升权限。",
                       alternative=_NO_OP_REMEDIATION["unauthorized"])
        elif wants_enable and name in paused:
            row.update(reason="enabled", effective=True, detail="该工具此前被本会话暂停，本次恢复。")
        elif wants_enable and name in available:
            row.update(reason="not_paused",
                       detail="该工具并未被暂停，已经在当前执行目录中；enable_tools 不会改变任何状态。",
                       alternative=_NO_OP_REMEDIATION["not_paused"])
        elif wants_enable:
            row.update(reason="gated_by_profile",
                       detail=f"该工具已注册，但被当前工作目录 profile（{profile or '未知'}）门控，不在本会话执行目录中；自我管理不能绕过 profile。",
                       alternative=_PROFILE_ALTERNATIVES.get(profile, _DEFAULT_PROFILE_ALTERNATIVE))
        elif name in paused:
            row.update(reason="already_disabled",
                       detail="该工具此前已被本会话暂停；disable_tools 不会改变任何状态。",
                       alternative=_NO_OP_REMEDIATION["already_disabled"])
        elif name in available:
            row.update(reason="disabled", effective=True, detail="该工具将从本会话执行目录中移除。")
        else:
            row.update(reason="already_unavailable",
                       detail="该工具当前既未被暂停也不在执行目录中（被 profile 或授权门控），暂停它不会改变现状。",
                       alternative=_NO_OP_REMEDIATION["already_unavailable"])
        rows[name] = row
    return rows


def _no_op_result(engine: Any, *, reason: str, name_reasons: dict[str, dict[str, Any]],
                  unchanged_settings: list[str] | None = None) -> Any:
    """请求不会生效时的非成功回执：逐名原因 + 可执行替代，绝不伪装成 success。"""
    if name_reasons:
        summary = "、".join(f"{name}（{row['reason']}）" for name, row in list(name_reasons.items())[:10])
        message = f"configure_agent 未生效，请求未做任何改动：{summary}。"
        skipped = [row["reason"] for row in name_reasons.values() if not row["effective"]]
    else:
        message = ("configure_agent 未生效，请求未做任何改动：指定配置与当前值相同（"
                   + "、".join(unchanged_settings or []) + "）。")
        skipped = ["unchanged"]
    for key in ("gated_by_profile", "unauthorized", "not_paused", "already_unavailable", "already_disabled"):
        if key in skipped:
            remediation = _NO_OP_REMEDIATION[key]
            break
    else:
        remediation = "先用 inspect_agent 确认当前配置与可用工具，再决定是否还需要修改。"
    return error_result(
        message, code="NOOP", remediation=remediation,
        fields={"outcome": "no_op", "scope": "session", "persisted": False, "reason": reason.strip(),
                "changes": {}, "changed_settings": [], "unchanged_settings": list(unchanged_settings or []),
                "enabled_tools": [],
                "disabled_tools": sorted(getattr(engine, "_self_disabled_tools", ())),
                "name_reasons": name_reasons,
                "detail": "该回执表示没有产生任何有效变更；不要原样重试，请按 alternative 更换工具、参数或路径。"},
    )


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
    name_reasons = _name_reasons(engine, disable=disable, enable=enable)
    unknown = sorted(name for name, row in name_reasons.items() if row["reason"] == "unknown")
    if unknown:
        return error_result(
            "以下工具不在当前会话注册表中：" + "、".join(unknown[:10])
            + "。此入口只调整已注册工具，不能安装新工具；请求未做任何改动。",
            code="INVALID_ARGS",
            fields={"scope": "session", "persisted": False, "reason": reason.strip(),
                    "unknown_tools": unknown[:10], "name_reasons": name_reasons,
                    "disabled_tools": sorted(getattr(engine, "_self_disabled_tools", ())),
                    "enabled_tools": []},
        )
    if "thinking_effort" in changes and changes["thinking_effort"] not in engine._config.thinking_effort_options:
        return error_result("推理等级不在用户允许的选项中。", code="INVALID_ARGS")
    if any(not row["effective"] for row in name_reasons.values()):
        # 只要有一个工具名不会生效，整条请求就不生效：不做部分生效，也不返回
        # "成功但没有效果"的误导性回执（真实会话里 agent 正是这样误判的）。
        return _no_op_result(engine, reason=reason, name_reasons=name_reasons)

    # All validation precedes mutation. Copy the immutable config so engines
    # constructed with the same config never change one another's defaults.
    before = _values(engine)
    original_config = engine._config
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
    changed_settings = [key for key in changes if before[key] != after[key]]
    if not changed_settings and not name_reasons:
        # 设置值与现值相同，且没有任何工具变更：还原 config 对象，保证无副作用。
        engine._config = original_config
        if router is not None:
            router._config = engine._config
            router._loader._config = engine._config
        return _no_op_result(engine, reason=reason, name_reasons={},
                             unchanged_settings=sorted(changes))
    return ok_result({
        "scope": "session", "persisted": False, "reason": reason.strip(),
        "changes": {key: {"before": before[key], "after": after[key],
                           "effective": "next_turn" if key == "max_iterations" else "next_call"}
                    for key in changes},
        "changed_settings": sorted(changed_settings),
        "disabled_tools": sorted(engine._self_disabled_tools),
        "enabled_tools": sorted(enable & set(catalog.names()) if catalog else set()),
        "name_reasons": name_reasons,
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
