"""元工具构建 — 从 AgentEngine 提取的 LLM-Native 工具 schema 构建逻辑。

包括：
- _build_meta_tools: 构建 skill / manage_skills / delegate / list_subagents / ask_user
- _build_v5_tools: 带脏标记缓存的工具 schema 构建
- _build_v5_tools_impl: 实际构建逻辑（read_only 过滤）
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from excelmanus.engine_utils import (
    _ALWAYS_AVAILABLE_TOOLS_READONLY_SET,
)
from excelmanus.logger import get_logger
from excelmanus.prompt.canonical import TOOL_DESCRIPTIONS

if TYPE_CHECKING:
    from excelmanus.engine import AgentEngine

logger = get_logger("meta_tools")


class MetaToolBuilder:
    """元工具 schema 构建器。

    通过 ``self._engine`` 引用访问 AgentEngine 的路由器、注册表和技能状态。
    """

    def __init__(self, engine: "AgentEngine") -> None:
        self._engine = engine

    def build_meta_tools(self) -> list[dict[str, Any]]:
        """构建 LLM-Native 元工具定义。

        构建 skill + delegate + list_subagents + ask_user。
        """
        e = self._engine

        # ── 技能名 enum（目录正文不进 description，避免抖 tools 数组）──
        skill_names: list[str] = []
        if e._skill_router is not None:
            _blocked = e._skill_resolver.blocked_skillpacks()
            list_names = getattr(e._skill_router, "list_skill_names", None)
            if callable(list_names):
                skill_names = [str(name) for name in (list_names(blocked_skillpacks=_blocked) or [])]
            if not skill_names:
                build_catalog = getattr(e._skill_router, "build_skill_catalog", None)
                built: Any = None
                if callable(build_catalog):
                    built = build_catalog(blocked_skillpacks=_blocked)
                if isinstance(built, tuple) and len(built) == 2:
                    _catalog_text, names = built
                    del _catalog_text
                    if isinstance(names, list):
                        skill_names = [str(name) for name in names]

            if not skill_names:
                loader = getattr(e._skill_router, "_loader", None)
                get_skillpacks = getattr(loader, "get_skillpacks", None)
                load_all = getattr(loader, "load_all", None)
                if callable(get_skillpacks):
                    skillpacks = get_skillpacks()
                else:
                    skillpacks = {}
                if not skillpacks and callable(load_all):
                    skillpacks = load_all()
                if isinstance(skillpacks, dict):
                    skill_names = sorted(
                        [
                            name
                            for name, skill in skillpacks.items()
                            if not bool(
                                getattr(skill, "disable_model_invocation", False)
                            )
                        ]
                    )

        _subagent_catalog, subagent_names = e._subagent_registry.build_catalog()
        skill_description = TOOL_DESCRIPTIONS["skill"]
        delegate_description = TOOL_DESCRIPTIONS["delegate"]
        list_subagents_description = TOOL_DESCRIPTIONS["list_subagents"]
        ask_user_description = TOOL_DESCRIPTIONS["ask_user"]
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "skill",
                    "description": skill_description,
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {
                                "type": "string",
                                "description": "会话技能目录中的准确名称",
                                **({"enum": skill_names} if skill_names else {}),
                            },
                        },
                        "required": ["name"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "delegate",
                    "description": delegate_description,
                    "parameters": {
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
                                    "title": {
                                        "type": "string",
                                        "description": "任务标题（一句话概括）",
                                    },
                                    "background": {
                                        "type": "string",
                                        "description": "任务背景与上下文",
                                    },
                                    "objectives": {
                                        "type": "array",
                                        "description": "目标列表",
                                        "items": {"type": "string"},
                                    },
                                    "constraints": {
                                        "type": "array",
                                        "description": "约束条件",
                                        "items": {"type": "string"},
                                    },
                                    "deliverables": {
                                        "type": "array",
                                        "description": "期望交付物",
                                        "items": {"type": "string"},
                                    },
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
                                        "task": {
                                            "type": "string",
                                            "description": "子任务描述",
                                        },
                                        "agent_name": {
                                            "type": "string",
                                            "description": "子代理名称；省略则使用通用 subagent",
                                            **({
                                                "enum": subagent_names,
                                            } if subagent_names else {}),
                                        },
                                        "file_paths": {
                                            "type": "array",
                                            "description": "该子任务涉及的文件路径",
                                            "items": {"type": "string"},
                                        },
                                    },
                                    "required": ["task"],
                                    "additionalProperties": False,
                                },
                            },
                            "agent_name": {
                                "type": "string",
                                "description": "子代理名称；省略则使用通用 subagent（仅单任务模式）",
                                **({"enum": subagent_names} if subagent_names else {}),
                            },
                            "file_paths": {
                                "type": "array",
                                "description": "可选，相关文件路径列表（仅单任务模式）",
                                "items": {"type": "string"},
                            },
                        },
                        "required": [],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "list_subagents",
                    "description": list_subagents_description,
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "required": [],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "ask_user",
                    "description": ask_user_description,
                    "parameters": {
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
                                        "text": {
                                            "type": "string",
                                            "description": "问题正文",
                                        },
                                        "header": {
                                            "type": "string",
                                            "description": "短标题（建议 <= 12 字符）",
                                        },
                                        "options": {
                                            "type": "array",
                                            "description": "候选项（1-4个），系统会自动追加 Other。",
                                            "minItems": 1,
                                            "maxItems": 4,
                                            "items": {
                                                "type": "object",
                                                "properties": {
                                                    "label": {
                                                        "type": "string",
                                                        "description": "选项名称",
                                                    },
                                                    "description": {
                                                        "type": "string",
                                                        "description": "该选项的权衡说明",
                                                    },
                                                },
                                                "required": ["label"],
                                                "additionalProperties": False,
                                            },
                                        },
                                        "multiSelect": {
                                            "type": "boolean",
                                            "description": "是否允许多选",
                                        },
                                    },
                                    "required": ["text", "options"],
                                    "additionalProperties": False,
                                },
                            },
                        },
                        "required": ["questions"],
                        "additionalProperties": False,
                    },
                },
            },
        ]

        # ── manage_skills：技能搜索 / 安装 / 卸载 ──
        manage_skills_description = (
            "搜索、安装、卸载或查看会话可用的技能包。"
        )
        tools.append({
            "type": "function",
            "function": {
                "name": "manage_skills",
                "description": manage_skills_description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["search", "install", "detail", "list", "uninstall", "update"],
                            "description": (
                                "操作类型: search=搜索技能市场, install=安装技能, "
                                "detail=查看技能详情, list=列出已安装技能, "
                                "uninstall=卸载技能, update=检查/执行更新"
                            ),
                        },
                        "query": {
                            "type": "string",
                            "description": "搜索关键词（action=search 时必填）",
                        },
                        "slug": {
                            "type": "string",
                            "description": "技能标识符或 GitHub URL（action=install/detail/uninstall 时必填）",
                        },
                        "overwrite": {
                            "type": "boolean",
                            "description": "安装时是否覆盖已存在的同名技能（默认 false）",
                        },
                    },
                    "required": ["action"],
                    "additionalProperties": False,
                },
            },
        })

        return tools

    def build_v5_tools(
        self,
        *,
        tool_access: str = "unknown",
    ) -> list[dict[str, Any]]:
        """构建工具 schema + 元工具（带脏标记缓存）。"""
        from excelmanus.tools.runtime import present_as_of

        e = self._engine
        present_as = present_as_of(e)
        cache_key = (
            tool_access,
            getattr(e, "_current_chat_mode", "write"),
            frozenset(s.name for s in e._active_skills),
            getattr(e, "_bench_mode", False),
            present_as,
        )
        if e._tools_cache is not None and e._tools_cache_key == cache_key:
            return e._tools_cache
        tools = self.build_v5_tools_impl(
            tool_access=tool_access,
        )
        e._tools_cache = tools
        e._tools_cache_key = cache_key
        return tools

    def build_v5_tools_impl(
        self,
        *,
        tool_access: str = "unknown",
    ) -> list[dict[str, Any]]:
        """构建工具 schema + 元工具。

        ``tool_access == "read_only"`` 是显式 ``restrict()``，与 plan/read
        会话模式无关。默认路径始终 ``may_write``，只读靠执行器拒绝写入。
        """
        from excelmanus.tools.policy import READ_ONLY_SAFE_TOOLS, CODE_POLICY_DYNAMIC_TOOLS

        e = self._engine
        domain_schemas = e._registry.get_tiered_schemas(
            mode="chat_completions",
        )
        meta_schemas = self.build_meta_tools()
        # 去除与 domain 重复的元工具（元工具优先）
        meta_names = {s.get("function", {}).get("name") for s in meta_schemas}
        filtered_domain = [s for s in domain_schemas if s.get("function", {}).get("name") not in meta_names]

        if tool_access == "read_only":
            _allowed = READ_ONLY_SAFE_TOOLS | CODE_POLICY_DYNAMIC_TOOLS | _ALWAYS_AVAILABLE_TOOLS_READONLY_SET
            _chat_mode = getattr(e, "_current_chat_mode", "write")
            if _chat_mode == "plan":
                _allowed = _allowed | {"write_plan", "exit_plan_mode"}
            filtered_domain = [
                s for s in filtered_domain
                if s.get("function", {}).get("name", "") in _allowed
            ]
            _meta_blocked = {"delegate", "delegate_to_subagent", "parallel_delegate"}
            meta_schemas = [
                s for s in meta_schemas
                if s.get("function", {}).get("name", "") not in _meta_blocked
            ]

        from excelmanus.tools.runtime import collapse_schemas, present_as_of

        return collapse_schemas(meta_schemas + filtered_domain, present_as_of(e))
