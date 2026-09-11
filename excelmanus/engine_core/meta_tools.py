"""元工具构建 — 从 AgentEngine 提取的 LLM-Native 工具 schema 构建逻辑。

包括：
- _build_meta_tools: 构建 activate_skill / manage_skills / delegate / list_subagents / ask_user / finish_task
- _build_v5_tools: 带脏标记缓存的工具 schema 构建
- _build_v5_tools_impl: 实际构建逻辑（read_only 过滤）
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from excelmanus.engine_utils import (
    _ALWAYS_AVAILABLE_TOOLS_READONLY_SET,
)
from excelmanus.logger import get_logger

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

        构建 activate_skill + delegate + list_subagents + ask_user。
        """
        e = self._engine

        # ── 构建 skill catalog ──
        skill_catalog = "当前无可用技能。"
        skill_names: list[str] = []
        if e._skill_router is not None:
            blocked = e._skill_resolver.blocked_skillpacks()
            build_catalog = getattr(e._skill_router, "build_skill_catalog", None)
            built: Any = None
            if callable(build_catalog):
                built = build_catalog(blocked_skillpacks=blocked)

            if isinstance(built, tuple) and len(built) == 2:
                catalog_text, names = built
                if isinstance(catalog_text, str) and catalog_text.strip():
                    skill_catalog = catalog_text.strip()
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
                    if skill_names:
                        lines = ["可用技能：\n"]
                        for name in skill_names:
                            skill = skillpacks[name]
                            description = str(getattr(skill, "description", "")).strip()
                            if blocked and name in blocked:
                                suffix = " [⚠️ 需要 fullaccess 权限，使用 /fullaccess on 开启]"
                            else:
                                suffix = ""
                            if description:
                                lines.append(f"- {name}：{description}{suffix}")
                            else:
                                lines.append(f"- {name}{suffix}")
                        skill_catalog = "\n".join(lines)

        activate_skill_description = (
            "激活技能获取专业操作指引。技能提供特定领域的最佳实践和步骤指导。\n"
            "适用场景：执行复杂任务、不确定最佳方案时，激活对应技能获取指引。\n"
            "不适用：简单读取/写入/回答问题——直接用对应工具即可。\n"
            "⚠️ 不要向用户提及技能名称或工具名称等内部概念。\n"
            "调用后立即执行任务，不要仅输出计划。\n\n"
            f"{skill_catalog}"
        )
        subagent_catalog, subagent_names = e._subagent_registry.build_catalog()
        delegate_description = (
            "委派子任务给子代理执行。\n"
            "适用场景：需要 20+ 次工具调用的大规模后台任务（多文件批量变换、"
            "复杂多步骤修改、长链条数据管线）。\n"
            "不适用：单文件读取/探查、简单写入/格式化、单步分析——直接用对应工具。\n"
            "参数模式（三选一）：task 字符串 / task_brief 结构化对象 / tasks 并行数组。\n"
            "并行模式（tasks 数组）要求彼此独立、不操作同一文件，2~5 个。\n"
            "委派即执行，不要先描述你将要委派什么，直接调用。\n\n"
            "Subagent_Catalog:\n"
            f"{subagent_catalog or '当前无可用子代理。'}"
        )
        list_subagents_description = "列出当前可用的全部 subagent 及职责。"
        ask_user_description = (
            "向用户提问并获取回答。这是与用户进行结构化交互的唯一方式。"
            "适用场景：需要用户做选择、确认意图、做决定或补充缺失信息时。"
            "不适用：信息已足够明确时——直接执行，不要多余询问。"
            "必须调用本工具提问，不要在文本回复中列出编号选项让用户回复。"
            "选项应具体（列出实际文件名/方案名），不要泛泛而问。"
            "需要收集多个维度信息时，在 questions 数组中传多个问题（1~8个），系统逐个展示。"
            "调用后暂停执行，等待用户回答后继续。"
        )
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "activate_skill",
                    "description": activate_skill_description,
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "skill_name": {
                                "type": "string",
                                "description": "要激活的技能名称",
                                **({"enum": skill_names} if skill_names else {}),
                            },
                        },
                        "required": ["skill_name"],
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
            {
                "type": "function",
                "function": {
                    "name": "suggest_mode_switch",
                    "description": (
                        "当前模式不适合用户任务时，向用户建议切换到更合适的模式。"
                        "仅在明确检测到模式不匹配时调用。典型场景："
                        "read 模式下用户需要写入→建议 write；"
                        "plan 模式下用户要求立即执行→建议 write；"
                        "write 模式下用户只需分析/统计→建议 read；"
                        "write 模式下任务复杂需先规划→建议 plan。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "target_mode": {
                                "type": "string",
                                "enum": ["write", "read", "plan"],
                                "description": "建议切换到的目标模式",
                            },
                            "reason": {
                                "type": "string",
                                "description": "向用户解释为什么建议切换",
                            },
                        },
                        "required": ["target_mode", "reason"],
                        "additionalProperties": False,
                    },
                },
            },
        ]

        # ── manage_skills：技能搜索 / 安装 / 卸载 ──
        manage_skills_description = (
            "管理技能包：搜索 ClawHub 技能市场、安装/卸载技能、查看已安装技能或技能详情。\n"
            "适用场景：用户需要新技能、想查找特定领域的技能、或管理已有技能时。\n"
            "安装后的技能可通过 activate_skill 激活使用。\n"
            "⚠️ 安装/卸载操作需要关闭安全模式（external_safe_mode=false）。"
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

        # ── finish_task：agent 主动终止任务的逃逸出口 ──
        if getattr(e, "_bench_mode", False):
            finish_task_tool = {
                "type": "function",
                "function": {
                    "name": "finish_task",
                    "description": (
                        "本轮结果报告；不触发自动验收或额外模型调用。"
                        "只需一句话概括即可，不要详细展开。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "summary": {
                                "type": "string",
                                "description": "一句话完成摘要",
                            },
                        },
                        "required": ["summary"],
                        "additionalProperties": False,
                    },
                },
            }
        else:
            finish_task_tool = {
                "type": "function",
                "function": {
                    "name": "finish_task",
                    "description": (
                        "本轮结果报告。不触发自动验收或额外模型调用。"
                        "用 status 标明完成程度：completed（完成）、partial（部分完成）、"
                        "stopped（停止但未完成）。outputs 列出产出文件；"
                        "warnings / incomplete 如实说明问题与未完成项。"
                        "计划模式下若只需简短澄清或单步查询，也可直接 finish_task 收束本轮。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "status": {
                                "type": "string",
                                "enum": ["completed", "partial", "stopped"],
                                "description": (
                                    "完成程度：completed=完成，partial=部分完成，"
                                    "stopped=停止但未完成。有未完成项时应标 partial 或 stopped。"
                                ),
                            },
                            "summary": {
                                "type": "string",
                                "description": "用自然语言汇报：做了什么、关键结果。不要套模板。",
                            },
                            "outputs": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "path": {
                                            "type": "string",
                                            "description": "产出文件路径",
                                        },
                                        "changed_ranges": {
                                            "type": "array",
                                            "items": {"type": "string"},
                                            "description": "修改范围，如 Sheet1!A1:B10",
                                        },
                                        "content_version": {
                                            "type": "string",
                                            "description": "内容版本（如文件哈希）；未知可省略",
                                        },
                                    },
                                    "required": ["path"],
                                    "additionalProperties": False,
                                },
                                "description": "本轮产出文件列表",
                            },
                            "warnings": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "警告（截断、推断、未校验等）",
                            },
                            "incomplete": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "未完成事项；有则 status 应为 partial 或 stopped",
                            },
                        },
                        "required": [],
                        "additionalProperties": False,
                    },
                },
            }
        tools.append(finish_task_tool)

        return tools

    def build_v5_tools(
        self,
        *,
        tool_access: str = "unknown",
    ) -> list[dict[str, Any]]:
        """构建工具 schema + 元工具（带脏标记缓存）。"""
        e = self._engine
        cache_key = (
            tool_access,
            getattr(e, "_current_chat_mode", "write"),
            frozenset(s.name for s in e._active_skills),
            getattr(e, "_bench_mode", False),
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

        tool_access == "read_only" 时仅暴露只读工具子集 + run_code + 元工具。
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
                _allowed = _allowed | {"write_plan"}
            filtered_domain = [
                s for s in filtered_domain
                if s.get("function", {}).get("name", "") in _allowed
            ]
            _meta_blocked = {"delegate", "delegate_to_subagent", "parallel_delegate"}
            meta_schemas = [
                s for s in meta_schemas
                if s.get("function", {}).get("name", "") not in _meta_blocked
            ]

        return meta_schemas + filtered_domain
