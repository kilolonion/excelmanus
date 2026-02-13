"""Agent 核心引擎：Skillpack 路由 + Tool Calling 循环。"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
import json
from dataclasses import dataclass, field
from typing import Any

import openai

from excelmanus.config import ExcelManusConfig
from excelmanus.events import EventCallback, EventType, ToolCallEvent
from excelmanus.logger import get_logger, log_tool_call
from excelmanus.memory import ConversationMemory
from excelmanus.skillpacks import SkillMatchResult, SkillRouter
from excelmanus.skillpacks.context_builder import build_contexts_with_budget
from excelmanus.task_list import TaskStore
from excelmanus.tools import task_tools
from excelmanus.tools.registry import ToolNotAllowedError

logger = get_logger("engine")


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
        self._restricted_code_skillpacks: set[str] = {"excel_code_runner"}
        # 会话级 skill 累积：记录本会话已加载过的所有 skill 名称
        self._loaded_skill_names: set[str] = set()
        # auto 模式系统消息回退缓存：None | "merge"
        self._system_mode_fallback: str | None = None
        # 执行统计（每次 chat 调用后更新）
        self._last_iteration_count: int = 0
        self._last_tool_call_count: int = 0
        self._last_success_count: int = 0
        self._last_failure_count: int = 0

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

    def list_loaded_skillpacks(self) -> list[str]:
        """返回当前已加载的 Skillpack 名称。"""
        if self._skill_router is None:
            return []
        return sorted(self._skill_router._loader.get_skillpacks().keys())

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
    ) -> str:
        """编排层：路由 → 消息管理 → 调用循环 → 返回结果。"""
        control_reply = self._handle_control_command(user_message)
        if control_reply is not None:
            logger.info("控制命令执行: %s", _summarize_text(user_message))
            return control_reply

        chat_start = time.monotonic()

        # 发出路由开始事件
        self._emit(
            on_event,
            ToolCallEvent(event_type=EventType.ROUTE_START),
        )

        manual_skill_name = self.resolve_skill_command(user_message)
        effective_skill_hints = (
            [manual_skill_name] if manual_skill_name else skill_hints
        )
        route_result = await self._route_skills(user_message, effective_skill_hints)
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

    async def _tool_calling_loop(
        self,
        route_result: SkillMatchResult,
        on_event: EventCallback | None,
    ) -> str:
        """迭代循环体：LLM 请求 → thinking 提取 → 工具调用遍历 → 熔断检测。"""
        tool_scope = route_result.tool_scope
        tools = self._get_openai_tools(tool_scope=tool_scope)

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

                # 更新统计
                self._last_tool_call_count += 1
                if tc_result.success:
                    self._last_success_count += 1
                    consecutive_failures = 0
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
        )

    def clear_memory(self) -> None:
        """清除对话历史。"""
        self._memory.clear()
        self._loaded_skill_names.clear()

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
        )

    async def _route_skills(
        self,
        user_message: str,
        skill_hints: list[str] | None,
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
            skill_hints=skill_hints,
            confirm_with_llm=self._confirm_with_llm,
            blocked_skillpacks=blocked_skillpacks,
        )

    def _handle_control_command(self, user_message: str) -> str | None:
        """处理会话级控制命令。命中时返回回复文本，否则返回 None。"""
        text = user_message.strip()
        if not text or not text.startswith("/"):
            return None

        parts = text.split()
        command = parts[0].strip().lower().replace("_", "")
        if command != "/fullaccess":
            return None

        if len(parts) == 1:
            action = "on"
        elif len(parts) == 2:
            action = parts[1].strip().lower()
        else:
            action = ""

        self._last_route_result = SkillMatchResult(
            skills_used=[],
            tool_scope=[],
            route_mode="control_command",
            system_contexts=[],
        )

        if action in {"on", ""} and len(parts) <= 2:
            self._full_access_enabled = True
            return "已开启 fullAccess。当前代码技能权限：full_access。"
        if action == "off":
            self._full_access_enabled = False
            return "已关闭 fullAccess。当前代码技能权限：restricted。"
        if action == "status":
            status = "full_access" if self._full_access_enabled else "restricted"
            return f"当前代码技能权限：{status}。"
        return "无效参数。用法：/fullAccess [on|off|status]。"

    async def _confirm_with_llm(self, user_message: str, candidates: list[Any]) -> list[str]:
        """用同一个主模型做候选 Skillpack 二次确认。"""
        if not candidates:
            return []

        skill_lines = [f"- {skill.name}: {skill.description}" for skill in candidates]
        prompt = (
            "请从候选 Skillpack 中选择最合适的最多 "
            f"{self._config.skills_max_selected} 个名称，仅输出 JSON 数组，例如 "
            "[\"data_basic\", \"chart_basic\"]。\n"
            "候选列表：\n"
            + "\n".join(skill_lines)
            + "\n\n用户请求："
            + user_message
        )

        response = await self._router_client.chat.completions.create(
            model=self._router_model,
            messages=[
                {
                    "role": "system",
                    "content": "你是路由器，仅输出 JSON 数组，不要输出解释。",
                },
                {"role": "user", "content": prompt},
            ],
        )
        content = str(response.choices[0].message.content or "").strip()
        if not content:
            return []

        selected: list[str]
        try:
            parsed = json.loads(content)
            if isinstance(parsed, list):
                selected = [str(item).strip() for item in parsed if str(item).strip()]
            else:
                selected = []
        except json.JSONDecodeError:
            selected = [seg.strip() for seg in content.replace("\n", ",").split(",") if seg.strip()]

        valid_names = {skill.name for skill in candidates}
        filtered: list[str] = []
        for name in selected:
            if name not in valid_names:
                continue
            if name in filtered:
                continue
            filtered.append(name)
            if len(filtered) >= self._config.skills_max_selected:
                break
        return filtered

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
