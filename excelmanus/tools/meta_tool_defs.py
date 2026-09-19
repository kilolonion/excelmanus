"""元工具的可执行 ToolDef。schema / 可见性 / 副作用与领域工具同一合同。"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from excelmanus.prompt.canonical import TOOL_DESCRIPTIONS
from excelmanus.tools.registry import ToolDef

_META_NAMES = frozenset(
    {"skill", "manage_skills", "delegate", "list_subagents", "ask_user"}
)


def _stub(**_kwargs: Any) -> str:
    raise RuntimeError("元工具由 ToolDispatcher handlers 执行，不能直接走 registry.func")


def get_meta_tools() -> list[ToolDef]:
    return [
        ToolDef(
            name="skill",
            description=TOOL_DESCRIPTIONS["skill"],
            input_schema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "会话技能目录中的准确名称",
                    },
                },
                "required": ["name"],
                "additionalProperties": False,
            },
            func=_stub,
            write_effect="none",
            visibility="always",
        ),
        ToolDef(
            name="manage_skills",
            description="安装、卸载或查看会话可用的技能包。",
            input_schema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["install", "list", "uninstall"],
                        "description": (
                            "操作类型: install=从 GitHub URL 或本地 SKILL.md 安装, "
                            "list=列出已安装技能, uninstall=卸载技能"
                        ),
                    },
                    "slug": {
                        "type": "string",
                        "description": (
                            "GitHub URL 或本地 SKILL.md 路径（action=install），"
                            "或技能名称（action=uninstall）"
                        ),
                    },
                    "overwrite": {
                        "type": "boolean",
                        "description": "安装时是否覆盖已存在的同名技能（默认 false）",
                    },
                },
                "required": ["action"],
                "additionalProperties": False,
            },
            func=_stub,
            write_effect="workspace_write",
            visibility="hide_in_read",
            actions={"list": {"write_effect": "none"}},
        ),
        ToolDef(
            name="delegate",
            description=TOOL_DESCRIPTIONS["delegate"],
            input_schema=_delegate_schema(),
            func=_stub,
            write_effect="dynamic",
            visibility="hide_in_read",
        ),
        ToolDef(
            name="list_subagents",
            description=TOOL_DESCRIPTIONS["list_subagents"],
            input_schema={
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
            func=_stub,
            write_effect="none",
            visibility="hide_in_read",
        ),
        ToolDef(
            name="ask_user",
            description=TOOL_DESCRIPTIONS["ask_user"],
            input_schema={
                "type": "object",
                "properties": {
                    "questions": {
                        "type": "array",
                        "description": "问题列表，单问题传 1 个元素，多问题传多个（系统逐个展示）。",
                        "minItems": 1,
                        "maxItems": 8,
                        "items": {
                            "type": "object",
                            "properties": {
                                "text": {"type": "string", "description": "问题正文"},
                                "header": {"type": "string", "description": "短标题（建议 <= 12 字符）"},
                                "options": {
                                    "type": "array",
                                    "description": "候选项（1-4个），系统会自动追加 Other。",
                                    "minItems": 1,
                                    "maxItems": 4,
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "label": {"type": "string", "description": "选项名称"},
                                            "description": {"type": "string", "description": "该选项的权衡说明"},
                                        },
                                        "required": ["label"],
                                        "additionalProperties": False,
                                    },
                                },
                                "multiSelect": {"type": "boolean", "description": "是否允许多选"},
                            },
                            "required": ["text", "options"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["questions"],
                "additionalProperties": False,
            },
            func=_stub,
            write_effect="none",
            visibility="always",
        ),
    ]


def _delegate_schema(
    *,
    subagent_names: list[str] | None = None,
) -> dict[str, Any]:
    agent_name: dict[str, Any] = {
        "type": "string",
        "description": "子代理名称；省略则使用通用 subagent（仅单任务模式）",
    }
    if subagent_names:
        agent_name["enum"] = list(subagent_names)
    task_agent: dict[str, Any] = {
        "type": "string",
        "description": "子代理名称；省略则使用通用 subagent",
    }
    if subagent_names:
        task_agent["enum"] = list(subagent_names)
    return {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": "单任务描述（与 tasks 二选一；与 task_brief 二选一）",
            },
            "task_brief": {
                "type": "object",
                "description": "结构化任务描述，适用于复杂任务（与 task 二选一）",
                "properties": {
                    "title": {"type": "string"},
                    "background": {"type": "string"},
                    "objectives": {"type": "array", "items": {"type": "string"}},
                    "constraints": {"type": "array", "items": {"type": "string"}},
                    "deliverables": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["title"],
                "additionalProperties": False,
            },
            "tasks": {
                "type": "array",
                "description": "并行子任务列表（与 task/task_brief 二选一），2-5 个独立任务",
                "minItems": 2,
                "maxItems": 5,
                "items": {
                    "type": "object",
                    "properties": {
                        "task": {"type": "string"},
                        "agent_name": task_agent,
                        "file_paths": {
                            "type": "array",
                            "description": "该子任务涉及的文件路径（说明，不是锁）",
                            "items": {"type": "string"},
                        },
                    },
                    "required": ["task"],
                    "additionalProperties": False,
                },
            },
            "agent_name": agent_name,
            "file_paths": {
                "type": "array",
                "description": "可选，相关文件路径列表（仅单任务模式；说明，不是锁）",
                "items": {"type": "string"},
            },
        },
        "required": [],
        "additionalProperties": False,
    }


def _session_skill_names(engine: Any) -> list[str]:
    router = getattr(engine, "_skill_router", None)
    if router is None:
        return []
    resolver = getattr(engine, "_skill_resolver", None)
    blocked = ()
    if resolver is not None:
        getter = getattr(resolver, "blocked_skillpacks", None)
        if callable(getter):
            try:
                blocked = getter()
            except Exception:
                blocked = ()
    list_names = getattr(router, "list_skill_names", None)
    if not callable(list_names):
        return []
    try:
        names = list_names(blocked_skillpacks=blocked)
    except TypeError:
        try:
            names = list_names()
        except Exception:
            return []
    except Exception:
        return []
    if isinstance(names, Iterable) and not isinstance(names, (str, bytes)):
        return [str(n) for n in names]
    return []


def _session_subagent_names(engine: Any) -> list[str]:
    sub_reg = getattr(engine, "_subagent_registry", None)
    if sub_reg is None:
        return []
    build = getattr(sub_reg, "build_catalog", None)
    if not callable(build):
        return []
    try:
        _catalog, names = build()
    except Exception:
        return []
    if isinstance(names, Iterable) and not isinstance(names, (str, bytes)):
        return [str(n) for n in names]
    return []


def apply_meta_schema_enums(engine: Any, tool: ToolDef) -> None:
    """把会话内技能名 / 子代理名写进这份 ToolDef（同一对象）。"""
    if tool.name == "skill":
        skill_names = _session_skill_names(engine)
        props = dict((tool.input_schema or {}).get("properties") or {})
        name_schema = dict(props.get("name") or {"type": "string"})
        if skill_names:
            name_schema["enum"] = skill_names
        else:
            name_schema.pop("enum", None)
        props["name"] = name_schema
        tool.input_schema = {**(tool.input_schema or {}), "properties": props}
    elif tool.name == "delegate":
        names = _session_subagent_names(engine)
        tool.input_schema = _delegate_schema(subagent_names=names or None)


def refresh_meta_tool_schemas(engine: Any) -> None:
    """把会话内技能名 / 子代理名写回已注册 ToolDef（同一对象，不另造 schema）。"""
    registry = getattr(engine, "_registry", None) or getattr(engine, "registry", None)
    if registry is None:
        return
    for name in ("skill", "delegate"):
        tool = registry.get_tool(name)
        if tool is not None:
            apply_meta_schema_enums(engine, tool)


def is_meta_tool(name: str) -> bool:
    return str(name) in _META_NAMES
