"""Agent 核心引擎：Skillpack 路由 + Tool Calling 循环。"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import openai

from excelmanus.approval import AppliedApprovalRecord, ApprovalManager, PendingApproval
from excelmanus.config import ExcelManusConfig
from excelmanus.events import EventCallback, EventType, ToolCallEvent
from excelmanus.logger import get_logger, log_tool_call
from excelmanus.memory import ConversationMemory
from excelmanus.skillpacks import SkillMatchResult, SkillRouter, Skillpack
from excelmanus.skillpacks.context_builder import build_contexts_with_budget
from excelmanus.task_list import TaskStore
from excelmanus.tools import task_tools
from excelmanus.tools.registry import ToolNotAllowedError

if TYPE_CHECKING:
    from excelmanus.persistent_memory import PersistentMemory
    from excelmanus.memory_extractor import MemoryExtractor

logger = get_logger("engine")
_FORK_SUMMARY_MAX_CHARS = 4000
_META_TOOL_NAMES = ("select_skill", "explore_data", "list_skills")
_READ_ONLY_TOOLS = (
    "read_excel",
    "analyze_data",
    "filter_data",
    "list_sheets",
    "get_file_info",
    "search_files",
    "list_directory",
    "read_text_file",
    "read_cell_styles",
)


def _to_plain(value: Any) -> Any:
    """将 SDK 对象/命名空间对象转换为纯 Python 结构。"""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {k: _to_plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_plain(v) for v in value]

    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return _to_plain(model_dump(exclude_none=False))
        except TypeError:
            return _to_plain(model_dump())

    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return _to_plain(to_dict())

    if hasattr(value, "__dict__"):
        return {k: _to_plain(v) for k, v in vars(value).items() if not k.startswith("_")}

    return str(value)


def _assistant_message_to_dict(message: Any) -> dict[str, Any]:
    """提取 assistant 消息字典，尽量保留供应商扩展字段。"""
    payload = _to_plain(message)
    if not isinstance(payload, dict):
        payload = {"content": str(getattr(message, "content", "") or "")}

    payload["role"] = "assistant"
    return payload


def _summarize_text(text: str, max_len: int = 120) -> str:
    """将文本压缩为单行摘要，避免日志过长。"""
    compact = " ".join(text.split())
    if not compact:
        return "(空)"
    if len(compact) <= max_len:
        return compact
    return f"{compact[: max_len - 3]}..."


@dataclass
class ToolCallResult:
    """单次工具调用的结果记录。"""

    tool_name: str
    arguments: dict
    result: str
    success: bool
    error: str | None = None
    pending_approval: bool = False
    approval_id: str | None = None
    audit_record: AppliedApprovalRecord | None = None


@dataclass
class ChatResult:
    """一次 chat 调用的完整结果。"""

    reply: str
    tool_calls: list[ToolCallResult] = field(default_factory=list)
    iterations: int = 0
    truncated: bool = False


class AgentEngine:
    """核心代理引擎，驱动 LLM 与工具之间的 Tool Calling 循环。"""

    def __init__(
        self,
        config: ExcelManusConfig,
        registry: Any,
        skill_router: SkillRouter | None = None,
        persistent_memory: PersistentMemory | None = None,
        memory_extractor: MemoryExtractor | None = None,
    ) -> None:
        self._client = openai.AsyncOpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
        )
        # 路由子代理：优先使用独立的小模型，未配置时回退到主模型
        if config.router_model:
            self._router_client = openai.AsyncOpenAI(
                api_key=config.router_api_key or config.api_key,
                base_url=config.router_base_url or config.base_url,
            )
            self._router_model = config.router_model
        else:
            self._router_client = self._client
            self._router_model = config.model
        self._config = config
        self._registry = registry
        self._skill_router = skill_router
        self._memory = ConversationMemory(config)
        self._last_route_result = SkillMatchResult(
            skills_used=[],
            tool_scope=self._all_tool_names(),
            route_mode="legacy_all_tools",
            system_contexts=[],
        )
        # 任务清单存储：单会话内存级
        self._task_store = TaskStore()
        task_tools.init_store(self._task_store)
        # 注入 SkillpackLoader 供 list_skills 工具使用
        if self._skill_router is not None:
            from excelmanus.tools import skill_tools
            skill_tools.init_loader(self._skill_router._loader)
        # 会话级权限控制：默认限制代码 Skillpack，显式 /fullAccess 后解锁
        self._full_access_enabled: bool = False
        # 会话级子代理开关：初始化继承配置，可通过 /subagent 动态切换
        self._subagent_enabled: bool = config.subagent_enabled
        self._restricted_code_skillpacks: set[str] = {"excel_code_runner"}
        # 会话级 skill 累积：记录本会话已加载过的所有 skill 名称
        self._loaded_skill_names: set[str] = set()
        # 当前激活技能：None 表示未激活状态
        self._active_skill: Skillpack | None = None
        # auto 模式系统消息回退缓存：None | "merge"
        self._system_mode_fallback: str | None = None
        # 执行统计（每次 chat 调用后更新）
        self._last_iteration_count: int = 0
        self._last_tool_call_count: int = 0
        self._last_success_count: int = 0
        self._last_failure_count: int = 0
        self._approval = ApprovalManager(config.workspace_root)

        # ── 持久记忆集成 ──────────────────────────────────
        self._persistent_memory = persistent_memory
        self._memory_extractor = memory_extractor
        # 初始化 memory_tools 模块的 PersistentMemory 引用
        from excelmanus.tools import memory_tools
        memory_tools.init_memory(persistent_memory)
        # 会话启动时加载核心记忆到 system prompt
        if persistent_memory is not None:
            core_memory = persistent_memory.load_core()
            if core_memory:
                original = self._memory.system_prompt
                self._memory.system_prompt = (
                    f"{original}\n\n## 持久记忆\n{core_memory}"
                )

    async def extract_and_save_memory(self) -> None:
        """会话结束时调用：从对话历史中提取记忆并持久化。

        若 MemoryExtractor 或 PersistentMemory 未配置则静默跳过。
        所有异常均被捕获并记录日志，不影响会话正常结束。
        """
        if self._memory_extractor is None or self._persistent_memory is None:
            return
        try:
            messages = self._memory.get_messages()
            entries = await self._memory_extractor.extract(messages)
            if entries:
                self._persistent_memory.save_entries(entries)
                logger.info("持久记忆提取完成，保存了 %d 条记忆条目", len(entries))
        except Exception:
            logger.exception("持久记忆提取或保存失败，已跳过")


    @property
    def memory(self) -> ConversationMemory:
        """暴露 memory 供外部访问（如测试）。"""
        return self._memory

    @property
    def last_route_result(self) -> SkillMatchResult:
        """最近一轮 skill 路由结果。"""
        return self._last_route_result

    @property
    def full_access_enabled(self) -> bool:
        """当前会话是否启用 fullAccess。"""
        return self._full_access_enabled

    @property
    def subagent_enabled(self) -> bool:
        """当前会话是否启用 fork 子代理。"""
        return self._subagent_enabled

    def list_loaded_skillpacks(self) -> list[str]:
        """返回当前已加载的 Skillpack 名称。"""
        if self._skill_router is None:
            return []
        skillpacks = self._skill_router._loader.get_skillpacks()
        if not skillpacks:
            skillpacks = self._skill_router._loader.load_all()
        return sorted(skillpacks.keys())

    def list_skillpack_commands(self) -> list[tuple[str, str]]:
        """返回可用于 CLI 展示的 Skillpack 斜杠命令与参数提示。"""
        if self._skill_router is None:
            return []
        skillpacks = self._skill_router._loader.get_skillpacks()
        if not skillpacks:
            skillpacks = self._skill_router._loader.load_all()
        commands = [
            (skill.name, skill.argument_hint)
            for skill in skillpacks.values()
        ]
        return sorted(commands, key=lambda item: item[0].lower())

    def get_skillpack_argument_hint(self, name: str) -> str:
        """按技能名返回 argument_hint。"""
        if self._skill_router is None:
            return ""
        skillpacks = self._skill_router._loader.get_skillpacks()
        if not skillpacks:
            skillpacks = self._skill_router._loader.load_all()
        skill = skillpacks.get(name)
        if skill is not None:
            return skill.argument_hint

        lower_name = name.lower()
        for candidate in skillpacks.values():
            if candidate.name.lower() == lower_name:
                return candidate.argument_hint
        return ""

    def _emit(self, on_event: EventCallback | None, event: ToolCallEvent) -> None:
        """安全地发出事件，捕获回调异常。"""
        if on_event is None:
            return
        try:
            on_event(event)
        except Exception as exc:
            logger.warning("事件回调异常: %s", exc)

    async def chat(
        self,
        user_message: str,
        on_event: EventCallback | None = None,
        skill_hints: list[str] | None = None,
        slash_command: str | None = None,
        raw_args: str | None = None,
    ) -> str:
        """编排层：路由 → 消息管理 → 调用循环 → 返回结果。"""
        control_reply = await self._handle_control_command(user_message)
        if control_reply is not None:
            logger.info("控制命令执行: %s", _summarize_text(user_message))
            return control_reply

        if self._approval.has_pending():
            self._last_route_result = SkillMatchResult(
                skills_used=[],
                tool_scope=[],
                route_mode="control_command",
                system_contexts=[],
            )
            block_msg = self._approval.pending_block_message()
            logger.info("存在待确认项，已阻塞普通请求")
            return block_msg

        chat_start = time.monotonic()

        # 发出路由开始事件
        self._emit(
            on_event,
            ToolCallEvent(event_type=EventType.ROUTE_START),
        )

        effective_slash_command = slash_command
        effective_raw_args = raw_args or ""

        # 兼容直接调用 engine.chat("/skill ...") 的旧路径：
        # 若调用方未显式传 slash_command，自动从用户输入中解析。
        if effective_slash_command is None:
            manual_skill_name = self.resolve_skill_command(user_message)
            if manual_skill_name is not None:
                effective_slash_command = manual_skill_name
                _, _, tail = user_message.strip()[1:].partition(" ")
                effective_raw_args = tail.strip()

        route_result = await self._route_skills(
            user_message,
            slash_command=effective_slash_command,
            raw_args=effective_raw_args if effective_slash_command else None,
        )
        route_result = self._merge_with_loaded_skills(route_result)
        self._last_route_result = route_result

        # 发出路由结束事件（含匹配结果）
        self._emit(
            on_event,
            ToolCallEvent(
                event_type=EventType.ROUTE_END,
                route_mode=route_result.route_mode,
                skills_used=list(route_result.skills_used),
                tool_scope=list(route_result.tool_scope) if route_result.tool_scope else [],
            ),
        )

        # 追加用户消息
        self._memory.add_user_message(user_message)
        logger.info(
            "用户指令摘要: %s | route_mode=%s | skills=%s",
            _summarize_text(user_message),
            route_result.route_mode,
            route_result.skills_used,
        )

        reply = await self._tool_calling_loop(route_result, on_event)

        # 发出执行摘要事件
        elapsed = time.monotonic() - chat_start
        self._emit(
            on_event,
            ToolCallEvent(
                event_type=EventType.CHAT_SUMMARY,
                total_iterations=self._last_iteration_count,
                total_tool_calls=self._last_tool_call_count,
                success_count=self._last_success_count,
                failure_count=self._last_failure_count,
                elapsed_seconds=round(elapsed, 2),
            ),
        )

        return reply

    @staticmethod
    def _normalize_skill_command_name(name: str) -> str:
        """命令名归一化：小写并移除连字符/下划线。"""
        return name.strip().lower().replace("-", "").replace("_", "")

    def _list_loaded_skill_names(self) -> list[str]:
        """获取当前可匹配的 Skill 名称；为空时尝试主动加载。"""
        if self._skill_router is None:
            return []
        skillpacks = self._skill_router._loader.get_skillpacks()
        if not skillpacks:
            skillpacks = self._skill_router._loader.load_all()
        return list(skillpacks.keys())

    def resolve_skill_command(self, user_message: str) -> str | None:
        """将 `/skill_name ...` 解析为 Skill 名称（用于手动调用）。"""
        text = user_message.strip()
        if not text.startswith("/"):
            return None

        first = text.split(maxsplit=1)[0]
        if len(first) <= 1:
            return None

        command = first[1:]
        # 排除路径形式，避免将 `/Users/...` 误识别为命令
        if "/" in command or "\\" in command:
            return None

        skill_names = self._list_loaded_skill_names()
        if not skill_names:
            return None

        lower_map = {name.lower(): name for name in skill_names}
        direct = lower_map.get(command.lower())
        if direct is not None:
            return direct

        normalized_cmd = self._normalize_skill_command_name(command)
        normalized_matches = [
            name
            for name in skill_names
            if self._normalize_skill_command_name(name) == normalized_cmd
        ]
        if len(normalized_matches) == 1:
            return normalized_matches[0]
        return None

    def _blocked_skillpacks(self) -> set[str] | None:
        """返回当前会话被限制的技能包集合。"""
        if self._full_access_enabled:
            return None
        return set(self._restricted_code_skillpacks)

    def _build_meta_tools(self) -> list[dict[str, Any]]:
        """构建 LLM-Native 元工具定义。"""
        skill_catalog = "当前无可用技能。"
        skill_names: list[str] = []
        if self._skill_router is not None:
            blocked = self._blocked_skillpacks()
            build_catalog = getattr(self._skill_router, "build_skill_catalog", None)
            built: Any = None
            if callable(build_catalog):
                built = build_catalog(blocked_skillpacks=blocked)

            if isinstance(built, tuple) and len(built) == 2:
                catalog_text, names = built
                if isinstance(catalog_text, str) and catalog_text.strip():
                    skill_catalog = catalog_text.strip()
                if isinstance(names, list):
                    skill_names = [str(name) for name in names]

            # 兼容单测 mock router：无 build_skill_catalog 或返回值异常时，从 loader 兜底。
            if not skill_names:
                loader = getattr(self._skill_router, "_loader", None)
                get_skillpacks = getattr(loader, "get_skillpacks", None)
                load_all = getattr(loader, "load_all", None)
                if callable(get_skillpacks):
                    skillpacks = get_skillpacks()
                else:
                    skillpacks = {}
                if not skillpacks and callable(load_all):
                    skillpacks = load_all()
                if isinstance(skillpacks, dict):
                    if blocked:
                        skillpacks = {
                            name: skill
                            for name, skill in skillpacks.items()
                            if name not in blocked
                        }
                    skill_names = sorted(skillpacks.keys())
                    if skill_names:
                        lines = ["可用技能：\n"]
                        for name in skill_names:
                            skill = skillpacks[name]
                            description = str(getattr(skill, "description", "")).strip()
                            if description:
                                lines.append(f"- {name}：{description}")
                            else:
                                lines.append(f"- {name}")
                        skill_catalog = "\n".join(lines)

        select_skill_description = (
            "激活一个技能包来完成当前任务。"
            "当用户提出明确的数据处理、图表制作、格式整理等执行任务时应优先调用本工具。\n"
            "如果用户只是闲聊、问候、询问能力或不需要执行工具，请不要调用本工具，直接回复。\n\n"
            "Skill_Catalog:\n"
            f"{skill_catalog}"
        )
        explore_data_description = (
            "启动只读数据探查子代理，适用于以下场景：\n"
            "- 大体量 Excel 文件（担心一次性读全量开销过高）\n"
            "- 数据结构未知，需要先摸清 sheet、列结构和质量问题\n"
            "- 存在复杂数据质量问题，需要先探查再执行写入操作\n\n"
            "不适用场景：\n"
            "- 用户已经明确说明数据结构\n"
            "- 仅做简单读取或一次性明确操作\n"
            "- 仅询问能力或闲聊"
        )
        return [
            {
                "type": "function",
                "function": {
                    "name": "select_skill",
                    "description": select_skill_description,
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "skill_name": {
                                "type": "string",
                                "description": "要激活的技能名称",
                                "enum": skill_names,
                            },
                            "reason": {
                                "type": "string",
                                "description": "选择该技能的原因（可选，一句话）",
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
                    "name": "explore_data",
                    "description": explore_data_description,
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "task": {
                                "type": "string",
                                "description": "需要探查的问题或目标",
                            },
                            "file_paths": {
                                "type": "array",
                                "description": "待探查文件路径列表（可选）",
                                "items": {"type": "string"},
                            },
                        },
                        "required": ["task"],
                        "additionalProperties": False,
                    },
                },
            },
        ]

    async def _handle_select_skill(self, skill_name: str, reason: str = "") -> str:
        """处理 select_skill 调用：激活技能并返回技能上下文。"""
        if self._skill_router is None:
            return f"未找到技能: {skill_name}"

        loader = self._skill_router._loader
        skillpacks = loader.get_skillpacks()
        if not skillpacks:
            skillpacks = loader.load_all()

        blocked = self._blocked_skillpacks()
        if blocked:
            skillpacks = {
                name: skill
                for name, skill in skillpacks.items()
                if name not in blocked
            }

        if not skillpacks:
            return f"未找到技能: {skill_name}"

        selected = self._skill_router._find_skill_by_name(
            skillpacks=skillpacks,
            name=skill_name,
        )
        if selected is None:
            return f"未找到技能: {skill_name}"

        self._active_skill = selected
        self._loaded_skill_names.add(selected.name)

        context_text = selected.render_context()
        reason_text = reason.strip()
        if reason_text:
            return (
                f"已激活技能: {selected.name}\n"
                f"选择原因: {reason_text}\n\n"
                f"{context_text}"
            )
        return context_text

    def _get_current_tool_scope(
        self,
        route_result: SkillMatchResult | None = None,
    ) -> list[str]:
        """根据当前状态返回主代理可用工具范围。"""
        if self._active_skill is not None:
            scope = list(self._active_skill.allowed_tools)
            if "select_skill" not in scope:
                scope.append("select_skill")
            return scope

        # 兼容斜杠直连：路由已指定技能范围时，将 select_skill 追加到限定范围。
        if (
            route_result is not None
            and route_result.route_mode == "slash_direct"
            and route_result.tool_scope
        ):
            scope = list(route_result.tool_scope)
            if "select_skill" not in scope:
                scope.append("select_skill")
            return scope

        scope = self._all_tool_names()
        for tool_name in _META_TOOL_NAMES:
            if tool_name not in scope:
                scope.append(tool_name)
        return scope

    def _build_tools_for_scope(self, tool_scope: Sequence[str]) -> list[dict[str, Any]]:
        """按当前 scope 组合常规工具和元工具定义。"""
        schemas = self._get_openai_tools(tool_scope=tool_scope)
        allowed = set(tool_scope)
        for tool in self._build_meta_tools():
            function = tool.get("function", {})
            name = function.get("name")
            if name in allowed:
                schemas.append(tool)
        return schemas

    @staticmethod
    def _normalize_explore_file_paths(file_paths: list[Any] | None) -> list[str]:
        """规范化 explore_data 的 file_paths 参数。"""
        if not file_paths:
            return []
        normalized: list[str] = []
        for item in file_paths:
            if not isinstance(item, str):
                continue
            path = item.strip()
            if path:
                normalized.append(path)
        return normalized

    def _build_explorer_system_prompt(self, task: str, file_paths: list[str]) -> str:
        """构建 explore_data 子代理系统提示。"""
        path_text = "、".join(file_paths) if file_paths else "未提供（请先用只读工具定位目标文件）"
        read_only_tools = "、".join(_READ_ONLY_TOOLS)
        return (
            "你是 ExcelManus 的只读数据探查子代理。\n"
            "目标：在有限轮次内完成结构探查并输出高密度摘要，供主代理后续执行。\n"
            "硬性约束：禁止任何写入、删除、重命名、覆盖操作，只允许读取。\n"
            f"只读工具范围：{read_only_tools}\n"
            f"探查任务：{task.strip()}\n"
            f"候选文件：{path_text}\n\n"
            "输出要求：\n"
            "1. 优先给出文件/工作表结构、关键字段、数据质量风险。\n"
            "2. 明确指出后续执行前需要确认的参数或边界条件。\n"
            "3. 结尾给出 3~8 条可执行建议，便于主代理直接落地。"
        )

    async def _execute_subagent_loop(
        self,
        *,
        system_prompt: str,
        tool_scope: list[str],
        max_iterations: int,
    ) -> str:
        """子代理执行循环：LLM 调用 → 只读工具执行 → 熔断/迭代上限保护。"""
        subagent_memory = ConversationMemory(self._config)
        subagent_memory.add_user_message("请按系统提示完成只读探查并返回摘要。")

        tools = self._get_openai_tools(tool_scope=tool_scope)
        model_name = self._config.subagent_model or self._config.model
        max_failures = self._config.subagent_max_consecutive_failures
        consecutive_failures = 0

        for iteration in range(1, max_iterations + 1):
            messages = subagent_memory.get_messages(system_prompts=[system_prompt])
            kwargs: dict[str, Any] = {
                "model": model_name,
                "messages": messages,
            }
            if tools:
                kwargs["tools"] = tools

            response = await self._client.chat.completions.create(**kwargs)
            message = response.choices[0].message

            if not message.tool_calls:
                summary = str(message.content or "").strip()
                if not summary:
                    summary = "子代理未返回文本摘要。"
                subagent_memory.add_assistant_message(summary)
                return summary

            assistant_msg = _assistant_message_to_dict(message)
            subagent_memory.add_assistant_tool_message(assistant_msg)

            breaker_triggered = False
            breaker_skip_error = (
                f"工具未执行：连续 {max_failures} 次工具调用失败，已触发子代理熔断。"
            )
            failed_results: list[ToolCallResult] = []

            for tc in message.tool_calls:
                tool_call_id = getattr(tc, "id", "")

                if breaker_triggered:
                    subagent_memory.add_tool_result(tool_call_id, breaker_skip_error)
                    continue

                tc_result = await self._execute_tool_call(
                    tc,
                    tool_scope,
                    None,
                    iteration,
                )
                subagent_memory.add_tool_result(tool_call_id, tc_result.result)

                if tc_result.success:
                    consecutive_failures = 0
                else:
                    consecutive_failures += 1
                    failed_results.append(tc_result)

                if consecutive_failures >= max_failures:
                    breaker_triggered = True

            if breaker_triggered:
                recent_errors = [
                    f"- {item.tool_name}: {item.error}"
                    for item in failed_results[-max_failures:]
                ]
                breaker_summary = "\n".join(recent_errors)
                return (
                    f"子代理连续 {max_failures} 次工具调用失败，已提前终止。"
                    f"错误摘要：\n{breaker_summary}"
                )

        return (
            f"子代理达到最大迭代次数（{max_iterations}），返回有限摘要："
            "已完成部分只读探索，请主代理在执行前再次核对关键参数。"
        )

    async def _handle_explore_data(
        self,
        task: str,
        file_paths: list[Any] | None = None,
        *,
        on_event: EventCallback | None = None,
    ) -> str:
        """处理 explore_data：启动只读子代理并返回摘要。"""
        task_text = task.strip()
        normalized_paths = self._normalize_explore_file_paths(file_paths)
        tool_scope = list(_READ_ONLY_TOOLS)

        self._emit(
            on_event,
            ToolCallEvent(
                event_type=EventType.SUBAGENT_START,
                subagent_reason=task_text,
                subagent_tools=tool_scope,
                subagent_success=True,
            ),
        )

        try:
            system_prompt = self._build_explorer_system_prompt(task_text, normalized_paths)
            summary = await self._execute_subagent_loop(
                system_prompt=system_prompt,
                tool_scope=tool_scope,
                max_iterations=self._config.subagent_max_iterations,
            )
            output = summary.strip()
            if len(output) > _FORK_SUMMARY_MAX_CHARS:
                output = (
                    output[:_FORK_SUMMARY_MAX_CHARS]
                    + f"\n[摘要已截断，原始长度: {len(summary)} 字符]"
                )
            self._emit(
                on_event,
                ToolCallEvent(
                    event_type=EventType.SUBAGENT_SUMMARY,
                    subagent_reason=task_text,
                    subagent_tools=tool_scope,
                    subagent_summary=output,
                    subagent_success=True,
                ),
            )
            self._emit(
                on_event,
                ToolCallEvent(
                    event_type=EventType.SUBAGENT_END,
                    subagent_reason=task_text,
                    subagent_tools=tool_scope,
                    subagent_success=True,
                ),
            )
            return output
        except Exception as exc:
            error_summary = f"子代理执行失败：{exc}"
            self._emit(
                on_event,
                ToolCallEvent(
                    event_type=EventType.SUBAGENT_SUMMARY,
                    subagent_reason=task_text,
                    subagent_tools=tool_scope,
                    subagent_summary=error_summary,
                    subagent_success=False,
                ),
            )
            self._emit(
                on_event,
                ToolCallEvent(
                    event_type=EventType.SUBAGENT_END,
                    subagent_reason=task_text,
                    subagent_tools=tool_scope,
                    subagent_success=False,
                ),
            )
            return error_summary

    async def _tool_calling_loop(
        self,
        route_result: SkillMatchResult,
        on_event: EventCallback | None,
    ) -> str:
        """迭代循环体：LLM 请求 → thinking 提取 → 工具调用遍历 → 熔断检测。"""
        max_iter = self._config.max_iterations
        max_failures = self._config.max_consecutive_failures
        consecutive_failures = 0
        all_tool_results: list[ToolCallResult] = []
        # 重置统计
        self._last_iteration_count = 0
        self._last_tool_call_count = 0
        self._last_success_count = 0
        self._last_failure_count = 0

        for iteration in range(1, max_iter + 1):
            self._emit(
                on_event,
                ToolCallEvent(
                    event_type=EventType.ITERATION_START,
                    iteration=iteration,
                ),
            )

            messages = self._memory.get_messages(
                system_prompts=self._build_system_prompts(route_result.system_contexts)
            )

            tool_scope = self._get_current_tool_scope(route_result=route_result)
            tools = self._build_tools_for_scope(tool_scope=tool_scope)

            kwargs: dict[str, Any] = {
                "model": self._config.model,
                "messages": messages,
            }
            if tools:
                kwargs["tools"] = tools

            response = await self._create_chat_completion_with_system_fallback(kwargs)
            choice = response.choices[0]
            message = choice.message

            # 提取 thinking / reasoning 内容
            thinking_content = ""
            for thinking_key in ("thinking", "reasoning", "reasoning_content"):
                candidate = getattr(message, thinking_key, None)
                if candidate:
                    thinking_content = str(candidate)
                    break

            if thinking_content:
                self._emit(
                    on_event,
                    ToolCallEvent(
                        event_type=EventType.THINKING,
                        thinking=thinking_content,
                        iteration=iteration,
                    ),
                )

            # 无工具调用 → 返回文本回复
            if not message.tool_calls:
                reply_text = message.content or ""
                self._memory.add_assistant_message(reply_text)
                self._last_iteration_count = iteration
                logger.info("最终结果摘要: %s", _summarize_text(reply_text))
                return reply_text

            assistant_msg = _assistant_message_to_dict(message)
            self._memory.add_assistant_tool_message(assistant_msg)

            # 遍历工具调用
            breaker_triggered = False
            breaker_summary = ""
            breaker_skip_error = (
                f"工具未执行：连续 {max_failures} 次工具调用失败，已触发熔断。"
            )

            for tc in message.tool_calls:
                function = getattr(tc, "function", None)
                tool_name = getattr(function, "name", "")
                tool_call_id = getattr(tc, "id", "")

                if breaker_triggered:
                    all_tool_results.append(
                        ToolCallResult(
                            tool_name=tool_name,
                            arguments={},
                            result=breaker_skip_error,
                            success=False,
                            error=breaker_skip_error,
                        )
                    )
                    self._memory.add_tool_result(tool_call_id, breaker_skip_error)
                    continue

                tc_result = await self._execute_tool_call(
                    tc, tool_scope, on_event, iteration
                )

                all_tool_results.append(tc_result)
                self._memory.add_tool_result(tool_call_id, tc_result.result)

                if tc_result.pending_approval:
                    reply = tc_result.result
                    self._memory.add_assistant_message(reply)
                    self._last_iteration_count = iteration
                    logger.info("工具调用进入待确认队列: %s", tc_result.approval_id)
                    logger.info("最终结果摘要: %s", _summarize_text(reply))
                    return reply

                # 更新统计
                self._last_tool_call_count += 1
                if tc_result.success:
                    self._last_success_count += 1
                    consecutive_failures = 0
                    if tc_result.tool_name == "select_skill":
                        tool_scope = self._get_current_tool_scope(route_result=route_result)
                else:
                    self._last_failure_count += 1
                    consecutive_failures += 1

                # 熔断检测
                if (not breaker_triggered) and consecutive_failures >= max_failures:
                    recent_errors = [
                        f"- {r.tool_name}: {r.error}"
                        for r in all_tool_results[-max_failures:]
                        if not r.success
                    ]
                    breaker_summary = "\n".join(recent_errors)
                    breaker_triggered = True

            if breaker_triggered:
                reply = (
                    f"连续 {max_failures} 次工具调用失败，已终止执行。"
                    f"错误摘要：\n{breaker_summary}"
                )
                self._memory.add_assistant_message(reply)
                self._last_iteration_count = iteration
                logger.warning("连续 %d 次工具失败，熔断终止", max_failures)
                logger.info("最终结果摘要: %s", _summarize_text(reply))
                return reply

        self._last_iteration_count = max_iter
        reply = f"已达到最大迭代次数（{max_iter}），返回当前结果。请尝试简化任务或分步执行。"
        self._memory.add_assistant_message(reply)
        logger.warning("达到迭代上限 %d，截断返回", max_iter)
        logger.info("最终结果摘要: %s", _summarize_text(reply))
        return reply

    async def _execute_tool_call(
        self,
        tc: Any,
        tool_scope: Sequence[str],
        on_event: EventCallback | None,
        iteration: int,
    ) -> ToolCallResult:
        """单个工具调用：参数解析 → 执行 → 事件发射 → 返回结果。"""
        function = getattr(tc, "function", None)
        tool_name = getattr(function, "name", "")
        raw_args = getattr(function, "arguments", None)

        # 参数解析
        parse_error: str | None = None
        try:
            if raw_args is None or raw_args == "":
                arguments: dict[str, Any] = {}
            elif isinstance(raw_args, dict):
                arguments = raw_args
            elif isinstance(raw_args, str):
                parsed = json.loads(raw_args)
                if not isinstance(parsed, dict):
                    parse_error = f"参数必须为 JSON 对象，当前类型: {type(parsed).__name__}"
                    arguments = {}
                else:
                    arguments = parsed
            else:
                parse_error = f"参数类型无效: {type(raw_args).__name__}"
                arguments = {}
        except (json.JSONDecodeError, TypeError) as exc:
            parse_error = f"JSON 解析失败: {exc}"
            arguments = {}

        # 发射 TOOL_CALL_START 事件
        self._emit(
            on_event,
            ToolCallEvent(
                event_type=EventType.TOOL_CALL_START,
                tool_name=tool_name,
                arguments=arguments,
                iteration=iteration,
            ),
        )

        pending_approval = False
        approval_id: str | None = None
        audit_record: AppliedApprovalRecord | None = None

        # 执行工具调用
        if parse_error is not None:
            result_str = f"工具参数解析错误: {parse_error}"
            success = False
            error = result_str
            log_tool_call(
                logger,
                tool_name,
                {"_raw_arguments": raw_args},
                error=error,
            )
        else:
            try:
                if tool_scope is not None and tool_name not in set(tool_scope):
                    raise ToolNotAllowedError(f"工具 '{tool_name}' 不在授权范围内。")

                if tool_name == "select_skill":
                    selected_name = arguments.get("skill_name")
                    if not isinstance(selected_name, str) or not selected_name.strip():
                        result_str = "工具参数错误: skill_name 必须为非空字符串。"
                        success = False
                        error = result_str
                    else:
                        reason_value = arguments.get("reason", "")
                        reason_text = (
                            reason_value
                            if isinstance(reason_value, str)
                            else str(reason_value)
                        )
                        result_str = await self._handle_select_skill(
                            selected_name.strip(),
                            reason=reason_text,
                        )
                        success = not result_str.startswith("未找到技能:")
                        error = None if success else result_str
                    log_tool_call(
                        logger,
                        tool_name,
                        arguments,
                        result=result_str if success else None,
                        error=error if not success else None,
                    )
                elif tool_name == "explore_data":
                    task_value = arguments.get("task")
                    if not isinstance(task_value, str) or not task_value.strip():
                        result_str = "工具参数错误: task 必须为非空字符串。"
                        success = False
                        error = result_str
                    else:
                        raw_file_paths = arguments.get("file_paths")
                        if raw_file_paths is not None and not isinstance(raw_file_paths, list):
                            result_str = "工具参数错误: file_paths 必须为字符串数组。"
                            success = False
                            error = result_str
                        else:
                            result_str = await self._handle_explore_data(
                                task=task_value.strip(),
                                file_paths=raw_file_paths,
                                on_event=on_event,
                            )
                            success = not result_str.startswith("子代理执行失败：")
                            error = None if success else result_str
                    log_tool_call(
                        logger,
                        tool_name,
                        arguments,
                        result=result_str if success else None,
                        error=error if not success else None,
                    )
                elif self._approval.is_high_risk_tool(tool_name):
                    if not self._full_access_enabled:
                        pending = self._approval.create_pending(
                            tool_name=tool_name,
                            arguments=arguments,
                            tool_scope=tool_scope,
                        )
                        pending_approval = True
                        approval_id = pending.approval_id
                        result_str = self._format_pending_prompt(pending)
                        success = True
                        error = None
                        log_tool_call(logger, tool_name, arguments, result=result_str)
                    else:
                        result_value, audit_record = await self._execute_tool_with_audit(
                            tool_name=tool_name,
                            arguments=arguments,
                            tool_scope=tool_scope,
                            approval_id=self._approval.new_approval_id(),
                            created_at_utc=self._approval.utc_now(),
                            undoable=tool_name != "run_python_script",
                        )
                        result_str = str(result_value)
                        tool_def = getattr(self._registry, "get_tool", lambda _: None)(tool_name)
                        if tool_def is not None:
                            result_str = tool_def.truncate_result(result_str)
                        success = True
                        error = None
                        log_tool_call(logger, tool_name, arguments, result=result_str)
                else:
                    result_value = await asyncio.to_thread(
                        self._registry.call_tool,
                        tool_name,
                        arguments,
                        tool_scope=tool_scope,
                    )
                    result_str = str(result_value)
                    # 工具结果截断：超过 max_result_chars 时自动截断
                    tool_def = getattr(self._registry, "get_tool", lambda _: None)(tool_name)
                    if tool_def is not None:
                        result_str = tool_def.truncate_result(result_str)
                    success = True
                    error = None
                    log_tool_call(logger, tool_name, arguments, result=result_str)
            except ValueError as exc:
                result_str = str(exc)
                success = False
                error = result_str
                log_tool_call(logger, tool_name, arguments, error=error)
            except ToolNotAllowedError:
                # 格式化为与原有逻辑一致的 JSON 错误结构
                permission_error = {
                    "error_code": "TOOL_NOT_ALLOWED",
                    "tool": tool_name,
                    "allowed_tools": list(tool_scope),
                    "message": f"工具 '{tool_name}' 不在当前 Skillpack 授权范围内。",
                }
                result_str = json.dumps(permission_error, ensure_ascii=False)
                success = False
                error = result_str
                log_tool_call(logger, tool_name, arguments, error=error)
            except Exception as exc:
                result_str = f"工具执行错误: {exc}"
                success = False
                error = str(exc)
                log_tool_call(logger, tool_name, arguments, error=error)

        # 发射 TOOL_CALL_END 事件
        self._emit(
            on_event,
            ToolCallEvent(
                event_type=EventType.TOOL_CALL_END,
                tool_name=tool_name,
                arguments=arguments,
                result=result_str,
                success=success,
                error=error,
                iteration=iteration,
            ),
        )

        # 任务清单事件：成功执行 task_create/task_update 后发射对应事件
        if success and tool_name == "task_create":
            task_list = self._task_store.current
            if task_list is not None:
                self._emit(
                    on_event,
                    ToolCallEvent(
                        event_type=EventType.TASK_LIST_CREATED,
                        task_list_data=task_list.to_dict(),
                    ),
                )
        elif success and tool_name == "task_update":
            task_list = self._task_store.current
            if task_list is not None:
                self._emit(
                    on_event,
                    ToolCallEvent(
                        event_type=EventType.TASK_ITEM_UPDATED,
                        task_index=arguments.get("task_index"),
                        task_status=arguments.get("status", ""),
                        task_result=arguments.get("result"),
                        task_list_data=task_list.to_dict(),
                    ),
                )

        return ToolCallResult(
            tool_name=tool_name,
            arguments=arguments,
            result=result_str,
            success=success,
            error=error,
            pending_approval=pending_approval,
            approval_id=approval_id,
            audit_record=audit_record,
        )

    def _format_pending_prompt(self, pending: PendingApproval) -> str:
        """构造待确认提示。"""
        return (
            "检测到高风险操作，已进入待确认队列。\n"
            f"- ID: `{pending.approval_id}`\n"
            f"- 工具: `{pending.tool_name}`\n"
            "请执行以下命令之一：\n"
            f"- `/accept {pending.approval_id}` 执行\n"
            f"- `/reject {pending.approval_id}` 拒绝"
        )

    @staticmethod
    def _prepare_approval_arguments(
        tool_name: str,
        arguments: dict[str, Any],
        *,
        force_delete_confirm: bool,
    ) -> dict[str, Any]:
        """按执行上下文调整参数。"""
        copied = dict(arguments)
        if force_delete_confirm and tool_name in {"delete_file", "delete_sheet"}:
            copied["confirm"] = True
        return copied

    async def _execute_tool_with_audit(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        tool_scope: Sequence[str],
        approval_id: str,
        created_at_utc: str,
        undoable: bool,
        force_delete_confirm: bool = False,
    ) -> tuple[str, AppliedApprovalRecord]:
        """执行高风险工具并保存审计记录。"""
        audited_arguments = self._prepare_approval_arguments(
            tool_name,
            arguments,
            force_delete_confirm=force_delete_confirm,
        )

        def _execute(
            name: str,
            args: dict[str, Any],
            scope: Sequence[str],
        ) -> Any:
            return self._registry.call_tool(name, args, tool_scope=scope)

        return await asyncio.to_thread(
            self._approval.execute_and_audit,
            approval_id=approval_id,
            tool_name=tool_name,
            arguments=audited_arguments,
            tool_scope=list(tool_scope),
            execute=_execute,
            undoable=undoable,
            created_at_utc=created_at_utc,
        )

    def clear_memory(self) -> None:
        """清除对话历史。"""
        self._memory.clear()
        self._loaded_skill_names.clear()
        self._active_skill = None

    def _merge_with_loaded_skills(self, route_result: SkillMatchResult) -> SkillMatchResult:
        """将本轮路由结果与会话内历史已加载的 skill 合并。"""
        if self._skill_router is None:
            return route_result

        # 更新累积记录
        new_names = set(route_result.skills_used)
        self._loaded_skill_names.update(new_names)

        # 找出历史已加载但本轮未匹配的 skill
        history_only = self._loaded_skill_names - new_names
        if not history_only:
            return route_result

        # 查找历史 skill 对象并合并
        loader = self._skill_router._loader
        history_skills = [
            loader.get_skillpack(name)
            for name in sorted(history_only)
            if loader.get_skillpack(name) is not None
        ]
        if not history_skills:
            return route_result

        # 合并 tool_scope（去重，保持顺序：本轮优先）
        merged_tools = list(route_result.tool_scope)
        seen_tools = set(merged_tools)
        for skill in history_skills:
            for tool in skill.allowed_tools:
                if tool not in seen_tools:
                    seen_tools.add(tool)
                    merged_tools.append(tool)

        # 合并 skills 对象并统一应用预算
        if route_result.parameterized:
            merged_contexts = list(route_result.system_contexts)
            budget = self._config.skills_context_char_budget
            if budget <= 0:
                remaining_budget = 0
            else:
                used_chars = sum(len(ctx) for ctx in merged_contexts)
                remaining_budget = budget - used_chars

            if budget <= 0 or remaining_budget > 0:
                history_contexts = build_contexts_with_budget(
                    history_skills,
                    remaining_budget,
                )
                merged_contexts.extend(history_contexts)
        else:
            route_skills = [
                loader.get_skillpack(name)
                for name in route_result.skills_used
                if loader.get_skillpack(name) is not None
            ]
            merged_skill_objects = route_skills + history_skills
            merged_contexts = build_contexts_with_budget(
                merged_skill_objects, self._config.skills_context_char_budget
            )

        # 合并 skills_used
        merged_skills = list(route_result.skills_used)
        for skill in history_skills:
            if skill.name not in new_names:
                merged_skills.append(skill.name)

        logger.info(
            "skill 累积合并：本轮=%s，历史追加=%s",
            list(new_names),
            [s.name for s in history_skills],
        )

        return SkillMatchResult(
            skills_used=merged_skills,
            tool_scope=merged_tools,
            route_mode=route_result.route_mode,
            system_contexts=merged_contexts,
            parameterized=route_result.parameterized,
        )

    async def _route_skills(
        self,
        user_message: str,
        *,
        slash_command: str | None = None,
        raw_args: str | None = None,
    ) -> SkillMatchResult:
        if self._skill_router is None:
            return SkillMatchResult(
                skills_used=[],
                tool_scope=self._all_tool_names(),
                route_mode="legacy_all_tools",
                system_contexts=[],
            )

        blocked_skillpacks = (
            set(self._restricted_code_skillpacks)
            if not self._full_access_enabled
            else None
        )
        return await self._skill_router.route(
            user_message,
            slash_command=slash_command,
            raw_args=raw_args,
            blocked_skillpacks=blocked_skillpacks,
        )

    async def _handle_control_command(self, user_message: str) -> str | None:
        """处理会话级控制命令。命中时返回回复文本，否则返回 None。"""
        text = user_message.strip()
        if not text or not text.startswith("/"):
            return None

        parts = text.split()
        command = parts[0].strip().lower().replace("_", "")
        if command not in {"/fullaccess", "/subagent", "/accept", "/reject", "/undo"}:
            return None

        self._last_route_result = SkillMatchResult(
            skills_used=[],
            tool_scope=[],
            route_mode="control_command",
            system_contexts=[],
        )

        action = parts[1].strip().lower() if len(parts) == 2 else ""
        too_many_args = len(parts) > 2

        if command == "/fullaccess":
            if (action in {"on", ""}) and not too_many_args:
                self._full_access_enabled = True
                return "已开启 fullAccess。当前代码技能权限：full_access。"
            if action == "off" and not too_many_args:
                self._full_access_enabled = False
                return "已关闭 fullAccess。当前代码技能权限：restricted。"
            if action == "status" and not too_many_args:
                status = "full_access" if self._full_access_enabled else "restricted"
                return f"当前代码技能权限：{status}。"
            return "无效参数。用法：/fullAccess [on|off|status]。"

        if command == "/subagent":
            # /subagent 默认行为为查询状态，避免误触启停
            if action in {"status", ""} and not too_many_args:
                status = "enabled" if self._subagent_enabled else "disabled"
                return f"当前 fork 子代理状态：{status}。"
            if action == "on" and not too_many_args:
                self._subagent_enabled = True
                return "已开启 fork 子代理。"
            if action == "off" and not too_many_args:
                self._subagent_enabled = False
                return "已关闭 fork 子代理。"
            return "无效参数。用法：/subagent [on|off|status]。"

        if command == "/accept":
            return await self._handle_accept_command(parts)
        if command == "/reject":
            return self._handle_reject_command(parts)
        return self._handle_undo_command(parts)

    async def _handle_accept_command(self, parts: list[str]) -> str:
        """执行待确认操作。"""
        if len(parts) != 2:
            return "无效参数。用法：/accept <id>。"

        approval_id = parts[1].strip()
        pending = self._approval.pending
        if pending is None:
            return "当前没有待确认操作。"
        if pending.approval_id != approval_id:
            return f"待确认 ID 不匹配。当前待确认 ID 为 `{pending.approval_id}`。"

        try:
            _, record = await self._execute_tool_with_audit(
                tool_name=pending.tool_name,
                arguments=pending.arguments,
                tool_scope=pending.tool_scope,
                approval_id=pending.approval_id,
                created_at_utc=pending.created_at_utc,
                undoable=pending.tool_name != "run_python_script",
                force_delete_confirm=True,
            )
        except ToolNotAllowedError:
            self._approval.clear_pending()
            return (
                f"accept 执行失败：工具 `{pending.tool_name}` 当前不在授权范围内。"
            )
        except Exception as exc:  # noqa: BLE001
            self._approval.clear_pending()
            return f"accept 执行失败：{exc}"

        self._approval.clear_pending()
        lines = [
            f"已执行待确认操作 `{approval_id}`。",
            f"- 工具: `{record.tool_name}`",
            f"- 审计目录: `{record.audit_dir}`",
            f"- 可回滚: {'是' if record.undoable else '否'}",
        ]
        if record.result_preview:
            lines.append(f"- 结果摘要: {record.result_preview}")
        if record.undoable:
            lines.append(f"- 回滚命令: `/undo {approval_id}`")
        return "\n".join(lines)

    def _handle_reject_command(self, parts: list[str]) -> str:
        """拒绝待确认操作。"""
        if len(parts) != 2:
            return "无效参数。用法：/reject <id>。"
        approval_id = parts[1].strip()
        return self._approval.reject_pending(approval_id)

    def _handle_undo_command(self, parts: list[str]) -> str:
        """回滚已确认操作。"""
        if len(parts) != 2:
            return "无效参数。用法：/undo <id>。"
        approval_id = parts[1].strip()
        return self._approval.undo(approval_id)

    def _all_tool_names(self) -> list[str]:
        get_tool_names = getattr(self._registry, "get_tool_names", None)
        if callable(get_tool_names):
            return list(get_tool_names())

        get_all_tools = getattr(self._registry, "get_all_tools", None)
        if callable(get_all_tools):
            return [tool.name for tool in get_all_tools()]

        return []

    def _get_openai_tools(self, tool_scope: Sequence[str] | None) -> list[dict[str, Any]]:
        get_openai_schemas = getattr(self._registry, "get_openai_schemas", None)
        if not callable(get_openai_schemas):
            return []
        try:
            return get_openai_schemas(mode="chat_completions", tool_scope=tool_scope)
        except TypeError:
            return get_openai_schemas(mode="chat_completions")


    def _build_system_prompts(self, skill_contexts: list[str]) -> list[str]:
        base_prompt = self._memory.system_prompt
        if not skill_contexts:
            return [base_prompt]

        mode = self._effective_system_mode()
        if mode == "merge":
            merged = "\n\n".join([base_prompt, *skill_contexts])
            return [merged]

        return [base_prompt, *skill_contexts]

    def _effective_system_mode(self) -> str:
        configured = self._config.system_message_mode
        if configured != "auto":
            return configured
        if self._system_mode_fallback == "merge":
            return "merge"
        return "multi"

    async def _create_chat_completion_with_system_fallback(
        self,
        kwargs: dict[str, Any],
    ) -> Any:
        try:
            return await self._client.chat.completions.create(**kwargs)
        except Exception as exc:
            if (
                self._config.system_message_mode == "auto"
                and self._effective_system_mode() == "multi"
                and self._is_system_compatibility_error(exc)
            ):
                logger.warning("检测到多 system 兼容性错误，自动回退到 merge 模式")
                self._system_mode_fallback = "merge"
                merged_messages = self._memory.get_messages(
                    system_prompts=self._build_system_prompts(
                        self._last_route_result.system_contexts
                    )
                )
                retry_kwargs = dict(kwargs)
                retry_kwargs["messages"] = merged_messages
                return await self._client.chat.completions.create(**retry_kwargs)
            raise

    @staticmethod
    def _is_system_compatibility_error(exc: Exception) -> bool:
        text = str(exc).lower()
        keywords = [
            "multiple system",
            "at most one system",
            "only one system",
            "system messages",
            "role 'system'",
        ]
        return any(keyword in text for keyword in keywords)
