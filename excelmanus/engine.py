"""Agent 核心引擎：Skillpack 路由 + Tool Calling 循环。"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
import json
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Literal

import openai

from excelmanus.approval import AppliedApprovalRecord, ApprovalManager, PendingApproval
from excelmanus.config import ExcelManusConfig, ModelProfile
from excelmanus.events import EventCallback, EventType, ToolCallEvent
from excelmanus.hooks import (
    HookCallContext,
    HookDecision,
    HookEvent,
    SkillHookRunner,
)
from excelmanus.logger import get_logger, log_tool_call
from excelmanus.memory import ConversationMemory, TokenCounter
from excelmanus.plan_mode import (
    PendingPlanState,
    PlanDraft,
    new_plan_id,
    parse_plan_markdown,
    plan_filename,
    save_plan_markdown,
    utc_now_iso,
)
from excelmanus.question_flow import PendingQuestion, QuestionFlowManager
from excelmanus.skillpacks import (
    SkillMatchResult,
    SkillRouter,
    Skillpack,
    SkillpackManager,
)
from excelmanus.skillpacks.context_builder import build_contexts_with_budget
from excelmanus.subagent import SubagentExecutor, SubagentRegistry, SubagentResult
from excelmanus.task_list import TaskStore
from excelmanus.tools import task_tools
from excelmanus.mcp.manager import MCPManager, parse_tool_prefix
from excelmanus.tools.registry import ToolNotAllowedError

if TYPE_CHECKING:
    from excelmanus.persistent_memory import PersistentMemory
    from excelmanus.memory_extractor import MemoryExtractor

logger = get_logger("engine")
_META_TOOL_NAMES = ("select_skill", "delegate_to_subagent", "list_subagents", "ask_user")
_ALWAYS_AVAILABLE_TOOLS = ("task_create", "task_update", "ask_user", "delegate_to_subagent")
_PLAN_CONTEXT_MAX_CHARS = 6000
_RECENT_EXCEL_FILE_MAX = 5
_RECENT_SUBAGENT_TASK_MAX_CHARS = 160
_MIN_SYSTEM_CONTEXT_CHARS = 256
_SYSTEM_CONTEXT_SHRINK_MARKER = "[上下文已压缩以适配上下文窗口]"
_SYSTEM_Q_SUBAGENT_APPROVAL = "subagent_high_risk_approval"
_SUBAGENT_APPROVAL_OPTION_ACCEPT = "立即接受并执行"
_SUBAGENT_APPROVAL_OPTION_FULLACCESS_RETRY = "开启 fullAccess 后重试（推荐）"
_SUBAGENT_APPROVAL_OPTION_REJECT = "拒绝本次操作"
_SKILL_AGENT_ALIASES = {
    "explore": "explorer",
    "plan": "planner",
    "general-purpose": "analyst",
    "generalpurpose": "analyst",
}


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


def _message_content_to_text(content: Any) -> str:
    """将供应商差异化 content 统一为文本。"""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
            else:
                text = getattr(item, "text", None)
                if isinstance(text, str):
                    parts.append(text)
        if parts:
            return "".join(parts)
    return str(content)


def _normalize_tool_calls(raw_tool_calls: Any) -> list[Any]:
    """兼容 dict/object 两种 tool_call 结构。"""
    if raw_tool_calls is None:
        return []
    if isinstance(raw_tool_calls, tuple):
        raw_tool_calls = list(raw_tool_calls)
    if not isinstance(raw_tool_calls, list):
        return []

    normalized: list[Any] = []
    for item in raw_tool_calls:
        if isinstance(item, dict):
            raw_function = item.get("function")
            if isinstance(raw_function, dict):
                function_obj = SimpleNamespace(
                    name=str(raw_function.get("name", "") or ""),
                    arguments=raw_function.get("arguments"),
                )
            else:
                function_obj = SimpleNamespace(
                    name=str(getattr(raw_function, "name", "") or ""),
                    arguments=getattr(raw_function, "arguments", None),
                )
            normalized.append(
                SimpleNamespace(
                    id=str(item.get("id", "") or ""),
                    function=function_obj,
                )
            )
        else:
            normalized.append(item)
    return normalized


def _coerce_completion_message(message: Any) -> Any:
    """将消息对象标准化为包含 content/tool_calls 的结构。"""
    if message is None:
        return SimpleNamespace(content="", tool_calls=[])
    if isinstance(message, str):
        return SimpleNamespace(content=message, tool_calls=[])
    if isinstance(message, dict):
        return SimpleNamespace(
            content=message.get("content"),
            tool_calls=_normalize_tool_calls(message.get("tool_calls")),
            thinking=message.get("thinking"),
            reasoning=message.get("reasoning"),
            reasoning_content=message.get("reasoning_content"),
        )
    return message


def _extract_completion_message(response: Any) -> tuple[Any, Any]:
    """从 provider 响应中提取首个 message，并兼容字符串响应。"""
    usage = getattr(response, "usage", None)

    if isinstance(response, str):
        return SimpleNamespace(content=response, tool_calls=[]), usage

    choices = getattr(response, "choices", None)
    if isinstance(choices, list) and choices:
        message = getattr(choices[0], "message", None)
        if message is not None:
            return _coerce_completion_message(message), usage

    payload = _to_plain(response)
    if isinstance(payload, dict):
        if usage is None:
            usage = payload.get("usage")
        choices_payload = payload.get("choices")
        if isinstance(choices_payload, list) and choices_payload:
            first = choices_payload[0]
            if isinstance(first, dict):
                message_payload = first.get("message")
            else:
                message_payload = getattr(first, "message", None)
            if message_payload is not None:
                return _coerce_completion_message(message_payload), usage
        for key in ("output_text", "content", "text"):
            candidate = payload.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return SimpleNamespace(content=candidate, tool_calls=[]), usage

    return SimpleNamespace(content=str(response), tool_calls=[]), usage


def _usage_token(usage: Any, key: str) -> int:
    """读取 usage 中 token 计数，兼容 dict/object。"""
    if usage is None:
        return 0
    value = usage.get(key) if isinstance(usage, dict) else getattr(usage, key, 0)
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _looks_like_html_document(text: str) -> bool:
    """判断文本是否像整页 HTML 文档（常见于 base_url 配置错误）。"""
    stripped = text.lstrip()
    if not stripped:
        return False
    lowered = stripped.lower()
    if lowered.startswith("<!doctype html") or lowered.startswith("<html"):
        return True
    return "<html" in lowered and "</html>" in lowered and "<head" in lowered


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
    pending_question: bool = False
    question_id: str | None = None
    pending_plan: bool = False
    plan_id: str | None = None
    defer_tool_result: bool = False


@dataclass
class ChatResult:
    """一次 chat 调用的完整结果。"""

    reply: str
    tool_calls: list[ToolCallResult] = field(default_factory=list)
    iterations: int = 0
    truncated: bool = False
    # token 使用统计（来自 LLM API 的 usage 字段）
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    def __str__(self) -> str:
        """兼容旧调用方将 chat 结果当作字符串直接使用。"""
        return self.reply

    def __eq__(self, other: object) -> bool:
        """兼容与 str 比较，同时保留 ChatResult 间的结构化比较。"""
        if isinstance(other, str):
            return self.reply == other
        if isinstance(other, ChatResult):
            return (
                self.reply == other.reply
                and self.tool_calls == other.tool_calls
                and self.iterations == other.iterations
                and self.truncated == other.truncated
            )
        return NotImplemented

    def __contains__(self, item: str) -> bool:
        """兼容 `'xx' in result` 形式。"""
        return item in self.reply

    def __getattr__(self, name: str) -> Any:
        """兼容 result.strip()/startswith() 等字符串方法。"""
        return getattr(self.reply, name)


@dataclass
class DelegateSubagentOutcome:
    """委派子代理的结构化返回。"""

    reply: str
    success: bool
    picked_agent: str | None = None
    task_text: str = ""
    normalized_paths: list[str] = field(default_factory=list)
    subagent_result: SubagentResult | None = None


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
            self._router_follow_active_model = False
        else:
            self._router_client = self._client
            self._router_model = config.model
            self._router_follow_active_model = True
        self._config = config
        self._registry = registry
        self._skill_router = skill_router
        self._skillpack_manager = (
            SkillpackManager(config, skill_router._loader)
            if skill_router is not None
            else None
        )
        self._memory = ConversationMemory(config)
        self._last_route_result = SkillMatchResult(
            skills_used=[],
            tool_scope=self._all_tool_names(),
            route_mode="all_tools",
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
        self._subagent_registry = SubagentRegistry(config)
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
        self._subagent_executor = SubagentExecutor(
            parent_config=config,
            parent_registry=registry,
            approval_manager=self._approval,
        )
        self._hook_runner = SkillHookRunner(config)
        self._transient_hook_contexts: list[str] = []
        self._hook_started_skills: set[str] = set()
        self._question_flow = QuestionFlowManager(max_queue_size=8)
        self._system_question_actions: dict[str, dict[str, Any]] = {}
        self._pending_question_route_result: SkillMatchResult | None = None
        self._plan_mode_enabled: bool = False
        self._pending_plan: PendingPlanState | None = None
        self._approved_plan_context: str | None = None
        self._suspend_task_create_plan_once: bool = False
        self._recent_excel_files: list[str] = []
        self._last_subagent_name: str | None = None
        self._last_subagent_task: str | None = None

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

        # ── MCP Client 集成 ──────────────────────────────────
        self._mcp_manager = MCPManager(config.workspace_root)

        # ── 多模型切换 ──────────────────────────────────
        self._active_model: str = config.model
        self._active_api_key: str = config.api_key
        self._active_base_url: str = config.base_url
        self._active_model_name: str | None = None  # 当前激活的 profile name

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

    async def initialize_mcp(self) -> None:
        """异步初始化 MCP 连接（需在 event loop 中调用）。

        由 CLI 或 API 入口在启动时显式调用。

        注意：
        破坏性重构后，不再将 MCP Server 自动注入为 Skillpack。
        MCP 仅负责工具注册；Skill 仅负责策略与授权。
        """
        await self._mcp_manager.initialize(self._registry)

        # 将 MCP 白名单注册到审批管理器
        auto_approved = self._mcp_manager.auto_approved_tools
        if auto_approved:
            self._approval.register_mcp_auto_approve(auto_approved)

    async def shutdown_mcp(self) -> None:
        """关闭所有 MCP Server 连接，释放资源。"""
        if self._active_skill is not None:
            self._run_skill_hook(
                skill=self._active_skill,
                event=HookEvent.STOP,
                payload={"reason": "shutdown_mcp"},
            )
            self._run_skill_hook(
                skill=self._active_skill,
                event=HookEvent.SESSION_END,
                payload={"reason": "shutdown_mcp"},
            )
        else:
            for skill_name in list(self._hook_started_skills):
                skill = self._get_loaded_skill(skill_name)
                if skill is None:
                    continue
                self._run_skill_hook(
                    skill=skill,
                    event=HookEvent.STOP,
                    payload={"reason": "shutdown_mcp"},
                )
                self._run_skill_hook(
                    skill=skill,
                    event=HookEvent.SESSION_END,
                    payload={"reason": "shutdown_mcp"},
                )
        self._hook_started_skills.clear()
        await self._mcp_manager.shutdown()
    def mcp_server_info(self) -> list[dict[str, Any]]:
        """返回 MCP Server 连接状态摘要，供 CLI 展示。"""
        return self._mcp_manager.get_server_info()

    @property
    def mcp_connected_count(self) -> int:
        """已连接的 MCP Server 数量。"""
        return len(self._mcp_manager.connected_servers)

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
        """当前会话是否启用 subagent。"""
        return self._subagent_enabled

    @property
    def plan_mode_enabled(self) -> bool:
        """当前会话是否启用 plan mode。"""
        return self._plan_mode_enabled

    def has_pending_question(self) -> bool:
        """当前会话是否存在待回答问题。"""
        return self._question_flow.has_pending()

    def current_pending_question(self) -> PendingQuestion | None:
        """返回当前待回答问题（队首）。"""
        return self._question_flow.current()

    def is_waiting_multiselect_answer(self) -> bool:
        """是否正在等待多选题回答。"""
        current = self._question_flow.current()
        return bool(current and current.multi_select)

    def list_loaded_skillpacks(self) -> list[str]:
        """返回当前已加载的 Skillpack 名称。"""
        if self._skillpack_manager is not None:
            rows = self._skillpack_manager.list_skillpacks()
            return sorted(str(item["name"]) for item in rows)
        if self._skill_router is None:
            return []
        skillpacks = self._skill_router._loader.get_skillpacks()
        if not skillpacks:
            skillpacks = self._skill_router._loader.load_all()
        return sorted(skillpacks.keys())

    def list_skillpack_commands(self) -> list[tuple[str, str]]:
        """返回可用于 CLI 展示的 Skillpack 斜杠命令与参数提示。"""
        if self._skillpack_manager is not None:
            rows = self._skillpack_manager.list_skillpacks()
            commands = [
                (str(item["name"]), str(item.get("argument_hint", "") or ""))
                for item in rows
                if bool(item.get("user_invocable", True))
            ]
            return sorted(commands, key=lambda item: item[0].lower())
        if self._skill_router is None:
            return []
        skillpacks = self._skill_router._loader.get_skillpacks()
        if not skillpacks:
            skillpacks = self._skill_router._loader.load_all()
        commands = [
            (skill.name, skill.argument_hint)
            for skill in skillpacks.values()
            if skill.user_invocable
        ]
        return sorted(commands, key=lambda item: item[0].lower())

    def get_skillpack_argument_hint(self, name: str) -> str:
        """按技能名返回 argument_hint。"""
        if self._skillpack_manager is not None:
            try:
                detail = self._skillpack_manager.get_skillpack(name)
            except Exception:
                return ""
            hint = detail.get("argument_hint")
            return hint if isinstance(hint, str) else ""
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

    def list_skillpacks_detail(self) -> list[dict[str, Any]]:
        """返回全部技能详情（按名称排序）。"""
        manager = self._require_skillpack_manager()
        return manager.list_skillpacks()

    def get_skillpack_detail(self, name: str) -> dict[str, Any]:
        """返回指定技能详情。"""
        manager = self._require_skillpack_manager()
        return manager.get_skillpack(name)

    def create_skillpack(
        self,
        name: str,
        payload: dict[str, Any],
        *,
        actor: str,
    ) -> dict[str, Any]:
        """创建 project 层技能。"""
        manager = self._require_skillpack_manager()
        return manager.create_skillpack(name=name, payload=payload, actor=actor)

    def patch_skillpack(
        self,
        name: str,
        payload: dict[str, Any],
        *,
        actor: str,
    ) -> dict[str, Any]:
        """更新 project 层技能。"""
        manager = self._require_skillpack_manager()
        return manager.patch_skillpack(name=name, payload=payload, actor=actor)

    def delete_skillpack(
        self,
        name: str,
        *,
        actor: str,
        reason: str = "",
    ) -> dict[str, Any]:
        """软删除 project 层技能。"""
        manager = self._require_skillpack_manager()
        return manager.delete_skillpack(
            name=name,
            actor=actor,
            reason=reason,
        )

    def _require_skillpack_manager(self) -> SkillpackManager:
        if self._skillpack_manager is None:
            raise RuntimeError("skillpack 管理器不可用。")
        return self._skillpack_manager

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
        slash_command: str | None = None,
        raw_args: str | None = None,
    ) -> ChatResult:
        """编排层：路由 → 消息管理 → 调用循环 → 返回结果。"""
        if self._question_flow.has_pending():
            pending_chat_start = time.monotonic()
            pending_result = await self._handle_pending_question_answer(
                user_message=user_message,
                on_event=on_event,
            )
            if pending_result.iterations > 0:
                elapsed = time.monotonic() - pending_chat_start
                self._emit(
                    on_event,
                    ToolCallEvent(
                        event_type=EventType.CHAT_SUMMARY,
                        total_iterations=self._last_iteration_count,
                        total_tool_calls=self._last_tool_call_count,
                        success_count=self._last_success_count,
                        failure_count=self._last_failure_count,
                        elapsed_seconds=round(elapsed, 2),
                        prompt_tokens=pending_result.prompt_tokens,
                        completion_tokens=pending_result.completion_tokens,
                        total_tokens=pending_result.total_tokens,
                    ),
                )
            return pending_result

        control_reply = await self._handle_control_command(user_message, on_event=on_event)
        if control_reply is not None:
            logger.info("控制命令执行: %s", _summarize_text(user_message))
            return ChatResult(reply=control_reply)

        if self._approval.has_pending():
            self._last_route_result = SkillMatchResult(
                skills_used=[],
                tool_scope=[],
                route_mode="control_command",
                system_contexts=[],
            )
            block_msg = self._approval.pending_block_message()
            logger.info("存在待确认项，已阻塞普通请求")
            return ChatResult(reply=block_msg)

        if self._pending_plan is not None:
            block_msg = self._format_pending_plan_prompt()
            logger.info("存在待审批计划，已阻塞普通请求")
            return ChatResult(reply=block_msg)

        if self._plan_mode_enabled and not user_message.strip().startswith("/"):
            logger.info("plan mode 命中，进入仅规划路径")
            return await self._run_plan_mode_only(
                user_message=user_message,
                on_event=on_event,
            )

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
        # 将路由结果中的 tool_scope 与实际可调用范围对齐（含元工具）。
        effective_tool_scope = self._get_current_tool_scope(route_result=route_result)
        route_result = SkillMatchResult(
            skills_used=list(route_result.skills_used),
            tool_scope=effective_tool_scope,
            route_mode=route_result.route_mode,
            system_contexts=list(route_result.system_contexts),
            parameterized=route_result.parameterized,
        )
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

        if effective_slash_command and route_result.route_mode == "slash_not_user_invocable":
            reply = f"技能 `{effective_slash_command}` 不允许手动调用。"
            self._memory.add_user_message(user_message)
            self._memory.add_assistant_message(reply)
            self._last_iteration_count = 1
            self._last_tool_call_count = 0
            self._last_success_count = 0
            self._last_failure_count = 1
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
                    prompt_tokens=0,
                    completion_tokens=0,
                    total_tokens=0,
                ),
            )
            return ChatResult(
                reply=reply,
                tool_calls=[],
                iterations=1,
                truncated=False,
            )

        selected_skill = self._pick_route_skill(route_result)
        if selected_skill is not None:
            user_prompt_hook = self._run_skill_hook(
                skill=selected_skill,
                event=HookEvent.USER_PROMPT_SUBMIT,
                payload={
                    "user_message": user_message,
                    "slash_command": effective_slash_command or "",
                    "raw_args": effective_raw_args,
                    "route_mode": route_result.route_mode,
                    "skills_used": list(route_result.skills_used),
                },
            )
            if (
                user_prompt_hook is not None
                and isinstance(user_prompt_hook.updated_input, dict)
            ):
                updated_message = user_prompt_hook.updated_input.get("user_message")
                if isinstance(updated_message, str) and updated_message.strip():
                    user_message = updated_message.strip()
            if user_prompt_hook is not None and user_prompt_hook.decision == HookDecision.DENY:
                reason = user_prompt_hook.reason or "Hook 拒绝了当前请求。"
                reply = f"请求已被 Hook 拦截：{reason}"
                self._memory.add_user_message(user_message)
                self._memory.add_assistant_message(reply)
                self._last_iteration_count = 1
                self._last_tool_call_count = 0
                self._last_success_count = 0
                self._last_failure_count = 1
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
                        prompt_tokens=0,
                        completion_tokens=0,
                        total_tokens=0,
                    ),
                )
                return ChatResult(
                    reply=reply,
                    tool_calls=[],
                    iterations=1,
                    truncated=False,
                )

        if (
            effective_slash_command
            and route_result.route_mode == "slash_direct"
            and selected_skill is not None
            and selected_skill.command_dispatch == "tool"
            and selected_skill.command_tool
        ):
            self._memory.add_user_message(user_message)
            chat_result = await self._run_command_dispatch_skill(
                skill=selected_skill,
                raw_args=effective_raw_args,
                route_result=route_result,
                on_event=on_event,
            )
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
                    prompt_tokens=chat_result.prompt_tokens,
                    completion_tokens=chat_result.completion_tokens,
                    total_tokens=chat_result.total_tokens,
                ),
            )
            return chat_result

        # 追加用户消息
        self._memory.add_user_message(user_message)
        logger.info(
            "用户指令摘要: %s | route_mode=%s | skills=%s",
            _summarize_text(user_message),
            route_result.route_mode,
            route_result.skills_used,
        )

        chat_result = await self._tool_calling_loop(route_result, on_event)

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
                prompt_tokens=chat_result.prompt_tokens,
                completion_tokens=chat_result.completion_tokens,
                total_tokens=chat_result.total_tokens,
            ),
        )

        return chat_result

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

    def _list_manual_invocable_skill_names(self) -> list[str]:
        """获取可手动调用的技能名（user_invocable=true）。"""
        if self._skillpack_manager is not None:
            rows = self._skillpack_manager.list_skillpacks()
            return [
                str(item["name"])
                for item in rows
                if bool(item.get("user_invocable", True))
            ]
        if self._skill_router is None:
            return []
        skillpacks = self._skill_router._loader.get_skillpacks()
        if not skillpacks:
            skillpacks = self._skill_router._loader.load_all()
        names: list[str] = []
        for name, skill in skillpacks.items():
            if not isinstance(name, str) or not name.strip():
                continue
            if bool(getattr(skill, "user_invocable", True)):
                names.append(name)
        return names

    def resolve_skill_command(self, user_message: str) -> str | None:
        """将 `/skill_name ...` 解析为 Skill 名称（用于手动调用）。"""
        text = user_message.strip()
        if not text.startswith("/"):
            return None

        command_line = text[1:]
        if not command_line:
            return None

        # 手动命令解析仅允许 user_invocable=True 的技能。
        skill_names = self._list_manual_invocable_skill_names()
        if not skill_names:
            return None

        lower_to_name = {name.lower(): name for name in skill_names}
        command_line_lower = command_line.lower()

        # 1) 精确匹配（含命名空间）
        exact = lower_to_name.get(command_line_lower)
        if exact is not None:
            return exact

        # 2) 前缀匹配（/skill_name 后跟参数）
        for candidate in sorted(skill_names, key=len, reverse=True):
            lower_candidate = candidate.lower()
            if command_line_lower == lower_candidate:
                return candidate
            if command_line_lower.startswith(lower_candidate + " "):
                return candidate

        # 先尝试已注册技能匹配，之后再按路径输入兜底排除，避免误伤命名空间技能。
        command_token = command_line.split(maxsplit=1)[0]
        if "/" in command_token and "." in command_token:
            return None

        # 3) 无分隔符归一兜底匹配（兼容旧命令）
        command = command_token
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

    def _get_loaded_skill(self, name: str) -> Skillpack | None:
        if self._skill_router is None:
            return None
        loader = self._skill_router._loader
        skill = loader.get_skillpack(name)
        if skill is not None:
            return skill
        skillpacks = loader.get_skillpacks()
        if not skillpacks:
            skillpacks = loader.load_all()
        return skillpacks.get(name)

    def _pick_route_skill(self, route_result: SkillMatchResult | None) -> Skillpack | None:
        if self._active_skill is not None:
            return self._active_skill
        if route_result is None or not route_result.skills_used:
            return None
        return self._get_loaded_skill(route_result.skills_used[0])

    @staticmethod
    def _normalize_skill_agent_name(agent_name: str | None) -> str | None:
        if not agent_name:
            return None
        normalized = agent_name.strip()
        if not normalized:
            return None
        lowered = normalized.lower()
        return _SKILL_AGENT_ALIASES.get(lowered, normalized)

    def _push_hook_context(self, text: str) -> None:
        normalized = text.strip()
        if not normalized:
            return
        self._transient_hook_contexts.append(normalized)

    def _run_skill_hook(
        self,
        *,
        skill: Skillpack | None,
        event: HookEvent,
        payload: dict[str, Any],
        tool_name: str = "",
    ):
        if skill is None:
            return None

        def _invoke(target_event: HookEvent, target_payload: dict[str, Any]):
            context = HookCallContext(
                event=target_event,
                skill_name=skill.name,
                payload=target_payload,
                tool_name=tool_name,
                full_access_enabled=self._full_access_enabled,
            )
            hook_result = self._hook_runner.run(skill=skill, context=context)
            if hook_result.additional_context:
                self._push_hook_context(hook_result.additional_context)
            return hook_result

        if event == HookEvent.SESSION_START:
            self._hook_started_skills.add(skill.name)
            return _invoke(event, payload)

        if skill.name not in self._hook_started_skills:
            start_result = _invoke(
                HookEvent.SESSION_START,
                {"trigger_event": event.value, **payload},
            )
            self._hook_started_skills.add(skill.name)
            if start_result is not None and start_result.decision == HookDecision.DENY:
                return start_result

        result = _invoke(event, payload)
        if event in {HookEvent.STOP, HookEvent.SESSION_END}:
            self._hook_started_skills.discard(skill.name)
        return result

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
                                suffix = " [⚠️ 需要 fullAccess 权限，使用 /fullAccess on 开启]"
                            else:
                                suffix = ""
                            if description:
                                lines.append(f"- {name}：{description}{suffix}")
                            else:
                                lines.append(f"- {name}{suffix}")
                        skill_catalog = "\n".join(lines)

        select_skill_description = (
            "激活一个技能包来获取执行任务所需的工具。"
            "仅在当前工具列表不足以完成用户请求时调用。\n"
            "如果用户只是闲聊、问候、询问能力或不需要执行工具，请不要调用本工具，直接回复。\n"
            "⚠️ 信息隔离：不要向用户提及技能名称、工具名称、技能包等内部概念，"
            "只需自然地执行任务并呈现结果。\n\n"
            "Skill_Catalog:\n"
            f"{skill_catalog}"
        )
        subagent_catalog, subagent_names = self._subagent_registry.build_catalog()
        delegate_description = (
            "把任务委派给 subagent 执行。适用场景："
            "(1) 需要批量探查多个文件/sheet 结构时委派 explorer；"
            "(2) 需要执行复杂数据分析时委派 analyst；"
            "(3) 需要批量写入或格式化时委派 writer；"
            "(4) 需要编写和调试 Python 脚本时委派 coder。"
            "当搜索结果不确定、需要逐个检查多个目标时，优先委派 explorer 而非自己逐个尝试。\n\n"
            "Subagent_Catalog:\n"
            f"{subagent_catalog or '当前无可用子代理。'}"
        )
        list_subagents_description = "列出当前可用的全部 subagent 及职责。"
        ask_user_description = (
            "向用户发起结构化选择题以消除歧义。适用场景："
            "(1) 搜索到多个候选文件/sheet，需要用户确认目标；"
            "(2) 用户指令存在多种合理解读，需要确认意图；"
            "(3) 操作涉及不可逆选择（如覆盖文件），需要用户决策。"
            "规则：已知答案时不要问；选项应具体（如列出实际文件名），不要泛泛而问。"
            "触发后暂停执行，等待用户回答后继续。"
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
                    "name": "delegate_to_subagent",
                    "description": delegate_description,
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "task": {
                                "type": "string",
                                "description": "需要子代理完成的任务描述",
                            },
                            "agent_name": {
                                "type": "string",
                                "description": "可选，指定子代理名称；不传则自动选择",
                                "enum": subagent_names,
                            },
                            "file_paths": {
                                "type": "array",
                                "description": "可选，相关文件路径列表",
                                "items": {"type": "string"},
                            },
                        },
                        "required": ["task"],
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
                            "question": {
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
                                        "description": "候选项（2-4个），系统会自动追加 Other。",
                                        "minItems": 2,
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
                                            "required": ["label", "description"],
                                            "additionalProperties": False,
                                        },
                                    },
                                    "multiSelect": {
                                        "type": "boolean",
                                        "description": "是否允许多选",
                                    },
                                },
                                "required": ["text", "header", "options"],
                                "additionalProperties": False,
                            },
                        },
                        "required": ["question"],
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

        # 检查是否尝试激活被限制的技能
        blocked = self._blocked_skillpacks()
        if blocked and skill_name in blocked:
            # 从全量技能包中获取描述
            desc = ""
            skill_obj = skillpacks.get(skill_name)
            if skill_obj is not None:
                desc = f"\n该技能用于：{skill_obj.description}"
            return (
                f"⚠️ 技能 '{skill_name}' 需要 fullAccess 权限才能使用。{desc}\n"
                f"请告知用户使用 /fullAccess on 命令开启完全访问权限后重试。"
            )

        if not skillpacks:
            return f"未找到技能: {skill_name}"

        selected = self._skill_router._find_skill_by_name(
            skillpacks=skillpacks,
            name=skill_name,
        )
        if selected is None:
            return f"未找到技能: {skill_name}"
        mcp_requirements_error = self._validate_skill_mcp_requirements(selected)
        if mcp_requirements_error:
            return mcp_requirements_error

        self._active_skill = selected
        self._loaded_skill_names.add(selected.name)

        context_text = selected.render_context()
        return f"OK\n{context_text}"

    @staticmethod
    def _normalize_mcp_identifier(name: str) -> str:
        return name.strip().replace("-", "_").lower()

    def _available_mcp_server_set(self) -> set[str]:
        return {
            self._normalize_mcp_identifier(name)
            for name in self._mcp_manager.connected_servers
            if isinstance(name, str) and name.strip()
        }

    def _available_mcp_tool_pairs(self) -> set[tuple[str, str]]:
        pairs: set[tuple[str, str]] = set()
        for tool_name in self._all_tool_names():
            if not tool_name.startswith("mcp_"):
                continue
            try:
                server_name, original_tool = parse_tool_prefix(tool_name)
            except ValueError:
                continue
            pairs.add(
                (
                    self._normalize_mcp_identifier(server_name),
                    original_tool.strip().lower(),
                )
            )
        return pairs

    def _validate_skill_mcp_requirements(self, skill: Skillpack) -> str | None:
        required_servers = [
            item.strip()
            for item in skill.required_mcp_servers
            if isinstance(item, str) and item.strip()
        ]
        required_tools = [
            item.strip()
            for item in skill.required_mcp_tools
            if isinstance(item, str) and item.strip()
        ]
        if not required_servers and not required_tools:
            return None

        connected_servers = self._available_mcp_server_set()
        available_tool_pairs = self._available_mcp_tool_pairs()

        missing_servers: list[str] = []
        for server in required_servers:
            normalized = self._normalize_mcp_identifier(server)
            if normalized not in connected_servers:
                missing_servers.append(server)

        missing_tools: list[str] = []
        for token in required_tools:
            server_name, sep, tool_name = token.partition(":")
            if not sep:
                missing_tools.append(token)
                continue
            normalized_server = self._normalize_mcp_identifier(server_name)
            target_tool = tool_name.strip().lower()
            if target_tool == "*":
                matched = any(srv == normalized_server for srv, _ in available_tool_pairs)
            else:
                matched = (normalized_server, target_tool) in available_tool_pairs
            if not matched:
                missing_tools.append(token)

        if not missing_servers and not missing_tools:
            return None

        lines = [f"⚠️ 技能 '{skill.name}' 的 MCP 依赖未满足。"]
        if missing_servers:
            lines.append(f"- 缺少 MCP Server：{', '.join(missing_servers)}")
        if missing_tools:
            lines.append(f"- 缺少 MCP 工具：{', '.join(missing_tools)}")
        lines.append("请先配置并连接对应 MCP（mcp.json）后重试该技能。")
        return "\n".join(lines)

    def _get_current_tool_scope(
        self,
        route_result: SkillMatchResult | None = None,
    ) -> list[str]:
        """根据当前状态返回主代理可用工具范围。"""
        if self._active_skill is not None:
            scope = self._expand_tool_scope_patterns(self._active_skill.allowed_tools)
            if "select_skill" not in scope:
                scope.append("select_skill")
            return self._append_global_mcp_tools(self._ensure_always_available(scope))

        # 兼容斜杠直连：路由已指定技能范围时，将 select_skill 追加到限定范围。
        if (
            route_result is not None
            and route_result.route_mode == "slash_direct"
            and route_result.tool_scope
        ):
            scope = self._expand_tool_scope_patterns(route_result.tool_scope)
            if "select_skill" not in scope:
                scope.append("select_skill")
            return self._append_global_mcp_tools(self._ensure_always_available(scope))

        # 严格收敛：fallback / slash_not_found / no_skillpack 等非直连路由
        # 仅使用路由授权工具，并追加必要元工具。
        if route_result is not None and route_result.tool_scope:
            scope = self._expand_tool_scope_patterns(route_result.tool_scope)
            for tool_name in _META_TOOL_NAMES:
                if tool_name not in scope:
                    scope.append(tool_name)
            return self._append_global_mcp_tools(self._ensure_always_available(scope))

        scope = self._all_tool_names()
        for tool_name in _META_TOOL_NAMES:
            if tool_name not in scope:
                scope.append(tool_name)
        return self._append_global_mcp_tools(self._ensure_always_available(scope))

    @staticmethod
    def _ensure_always_available(scope: list[str]) -> list[str]:
        """确保任务管理工具始终在 scope 中可用。"""
        for tool_name in _ALWAYS_AVAILABLE_TOOLS:
            if tool_name not in scope:
                scope.append(tool_name)
        return scope

    def _append_global_mcp_tools(self, scope: list[str]) -> list[str]:
        """将全局 MCP 工具追加到当前 scope（去重）。"""
        for tool_name in self._all_tool_names():
            if tool_name.startswith("mcp_") and tool_name not in scope:
                scope.append(tool_name)
        return scope

    def _expand_tool_scope_patterns(self, scope: Sequence[str]) -> list[str]:
        """展开工具授权中的 MCP 选择器。

        支持三种写法：
        - `mcp:*`：允许所有已注册 MCP 工具
        - `mcp:{server}:*`：允许指定 server 的全部 MCP 工具
        - `mcp:{server}:{tool}`：允许指定 server 的指定工具
        """
        all_tools = self._all_tool_names()
        if not all_tools:
            return list(scope)

        mcp_tools = [name for name in all_tools if name.startswith("mcp_")]
        expanded: list[str] = []
        seen: set[str] = set()

        def _append(name: str) -> None:
            if name not in seen:
                seen.add(name)
                expanded.append(name)

        for token in scope:
            if not isinstance(token, str):
                continue
            selector = token.strip()
            if not selector:
                continue
            if selector == "mcp:*":
                for tool_name in mcp_tools:
                    _append(tool_name)
                continue
            if selector.startswith("mcp:"):
                parts = selector.split(":", 2)
                if len(parts) != 3:
                    continue
                server_name = parts[1].strip().replace("-", "_")
                tool_name = parts[2].strip()
                if not server_name or not tool_name:
                    continue

                for mcp_name in mcp_tools:
                    try:
                        normalized_server, original_tool = parse_tool_prefix(mcp_name)
                    except ValueError:
                        continue
                    if normalized_server != server_name:
                        continue
                    if tool_name == "*" or original_tool == tool_name:
                        _append(mcp_name)
                continue

            _append(selector)

        return expanded

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
    def _normalize_subagent_file_paths(file_paths: list[Any] | None) -> list[str]:
        """规范化 subagent 输入文件路径。"""
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

    def _build_parent_context_summary(self) -> str:
        """构建主会话上下文摘要。"""
        messages = self._memory.get_messages()
        lines: list[str] = []
        for msg in messages[-6:]:
            role = str(msg.get("role", "")).strip()
            content = str(msg.get("content", "")).strip()
            if not content:
                continue
            if role == "user":
                lines.append(f"用户: {content[:200]}")
            elif role == "assistant":
                lines.append(f"助手: {content[:200]}")
        return "\n".join(lines)

    async def _auto_select_subagent(
        self,
        *,
        task: str,
        file_paths: list[str],
    ) -> str:
        """自动选择最合适的子代理，失败时回退 explorer。"""
        _, candidates = self._subagent_registry.build_catalog()
        if not candidates:
            return "explorer"

        joined_candidates = ", ".join(candidates)
        file_hint = "、".join(file_paths) if file_paths else "无"
        messages = [
            {
                "role": "system",
                "content": (
                    "你是子代理分派器。只输出 JSON："
                    '{"agent_name":"候选中的一个名称"}。'
                    "不要输出解释。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"候选子代理：{joined_candidates}\n"
                    f"任务：{task}\n"
                    f"相关文件：{file_hint}"
                ),
            },
        ]
        try:
            response = await self._router_client.chat.completions.create(
                model=self._router_model,
                messages=messages,
            )
            message, _ = _extract_completion_message(response)
            content = _message_content_to_text(getattr(message, "content", None)).strip()
            parsed = json.loads(content) if content else {}
            picked = str(parsed.get("agent_name", "")).strip()
            if picked in set(candidates):
                return picked
        except Exception:
            logger.warning("自动选择子代理失败，回退 explorer", exc_info=True)
        return "explorer"

    async def run_subagent(
        self,
        *,
        agent_name: str,
        prompt: str,
        on_event: EventCallback | None = None,
    ) -> SubagentResult:
        """执行指定子代理。"""
        config = self._subagent_registry.get(agent_name)
        if config is None:
            return SubagentResult(
                success=False,
                summary=f"未找到子代理: {agent_name}",
                error=f"SubagentNotFound: {agent_name}",
                subagent_name=agent_name,
                permission_mode="default",
                conversation_id="",
            )
        return await self._subagent_executor.run(
            config=config,
            prompt=prompt,
            parent_context=self._build_parent_context_summary(),
            on_event=on_event,
            full_access_enabled=self._full_access_enabled,
        )

    async def _delegate_to_subagent(
        self,
        *,
        task: str,
        agent_name: str | None = None,
        file_paths: list[Any] | None = None,
        on_event: EventCallback | None = None,
    ) -> DelegateSubagentOutcome:
        """执行 delegate_to_subagent 并返回结构化结果。"""
        if not self._subagent_enabled:
            return DelegateSubagentOutcome(
                reply="subagent 当前处于关闭状态，请先执行 `/subagent on`。",
                success=False,
            )

        task_text = task.strip()
        if not task_text:
            return DelegateSubagentOutcome(
                reply="工具参数错误: task 必须为非空字符串。",
                success=False,
            )
        normalized_paths = self._normalize_subagent_file_paths(file_paths)

        picked_agent = (agent_name or "").strip()
        if not picked_agent:
            picked_agent = await self._auto_select_subagent(
                task=task_text,
                file_paths=normalized_paths,
            )
        picked_agent = self._normalize_skill_agent_name(picked_agent) or "explorer"

        hook_skill = self._active_skill
        pre_subagent_hook = self._run_skill_hook(
            skill=hook_skill,
            event=HookEvent.SUBAGENT_START,
            payload={
                "task": task_text,
                "agent_name": picked_agent,
                "file_paths": normalized_paths,
            },
        )
        if pre_subagent_hook is not None and pre_subagent_hook.decision == HookDecision.DENY:
            reason = pre_subagent_hook.reason or "Hook 拒绝了子代理执行。"
            return DelegateSubagentOutcome(
                reply=f"子代理执行已被 Hook 拦截：{reason}",
                success=False,
                picked_agent=picked_agent,
                task_text=task_text,
                normalized_paths=normalized_paths,
            )

        prompt = task_text
        if normalized_paths:
            prompt += f"\n\n相关文件：{', '.join(normalized_paths)}"

        result = await self.run_subagent(
            agent_name=picked_agent,
            prompt=prompt,
            on_event=on_event,
        )
        self._run_skill_hook(
            skill=hook_skill,
            event=HookEvent.SUBAGENT_STOP,
            payload={
                "task": task_text,
                "agent_name": picked_agent,
                "success": result.success,
                "summary": result.summary,
            },
        )
        if result.success:
            self._update_recent_excel_context(
                candidate_paths=[*normalized_paths, *result.observed_files],
                subagent_name=picked_agent,
                task=task_text,
            )
            return DelegateSubagentOutcome(
                reply=result.summary,
                success=True,
                picked_agent=picked_agent,
                task_text=task_text,
                normalized_paths=normalized_paths,
                subagent_result=result,
            )
        return DelegateSubagentOutcome(
            reply=f"子代理执行失败（{picked_agent}）：{result.summary}",
            success=False,
            picked_agent=picked_agent,
            task_text=task_text,
            normalized_paths=normalized_paths,
            subagent_result=result,
        )

    async def _handle_delegate_to_subagent(
        self,
        *,
        task: str,
        agent_name: str | None = None,
        file_paths: list[Any] | None = None,
        on_event: EventCallback | None = None,
    ) -> str:
        """处理 delegate_to_subagent 元工具。"""
        outcome = await self._delegate_to_subagent(
            task=task,
            agent_name=agent_name,
            file_paths=file_paths,
            on_event=on_event,
        )
        return outcome.reply

    def _handle_list_subagents(self) -> str:
        """列出可用子代理。"""
        agents = self._subagent_registry.list_all()
        if not agents:
            return "当前没有可用子代理。"
        lines: list[str] = [f"共 {len(agents)} 个可用子代理：\n"]
        for agent in agents:
            lines.append(f"- {agent.name} ({agent.permission_mode})：{agent.description}")
        return "\n".join(lines)

    @staticmethod
    def _task_create_objective(arguments: dict[str, Any]) -> str:
        """将 task_create 参数转为规划目标文本。"""
        title = str(arguments.get("title", "") or "").strip()
        raw_subtasks = arguments.get("subtasks")
        subtasks: list[str] = []
        if isinstance(raw_subtasks, list):
            for item in raw_subtasks:
                text = str(item).strip()
                if text:
                    subtasks.append(text)

        lines = ["请基于以下任务草稿生成可审批执行计划："]
        if title:
            lines.append(f"任务标题：{title}")
        if subtasks:
            lines.append("候选子任务：")
            lines.extend(f"- {item}" for item in subtasks)
        else:
            lines.append("候选子任务：暂无，需你根据目标拆解。")
        return "\n".join(lines)

    @staticmethod
    def _build_planner_prompt(*, objective: str, source: str) -> str:
        """构造 planner 子代理提示。"""
        source_label = "plan mode" if source == "plan_mode" else "task_create_hook"
        return (
            f"任务来源：{source_label}\n"
            "你需要产出一份可审批的执行计划文档。\n\n"
            "用户目标如下：\n"
            f"{objective.strip()}\n\n"
            "请严格按系统约束输出 Markdown（含 `## 任务清单` 与 `tasklist-json` 代码块）。"
        )

    async def _create_pending_plan_draft(
        self,
        *,
        objective: str,
        source: Literal["plan_mode", "task_create_hook"],
        route_to_resume: SkillMatchResult | None,
        tool_call_id: str | None,
        on_event: EventCallback | None,
    ) -> tuple[PlanDraft | None, str | None]:
        """调用 planner 生成待审批计划草案。"""
        if self._pending_plan is not None:
            return None, "当前已有待审批计划，请先批准或拒绝。"

        plan_result = await self.run_subagent(
            agent_name="planner",
            prompt=self._build_planner_prompt(objective=objective, source=source),
            on_event=on_event,
        )
        if not plan_result.success:
            detail = (plan_result.error or plan_result.summary or "未知错误").strip()
            return None, f"planner 执行失败：{detail}"

        markdown = str(plan_result.summary or "").strip()
        if not markdown:
            return None, "planner 未返回计划文档。"

        try:
            title, subtasks = parse_plan_markdown(markdown)
        except ValueError as exc:
            return None, str(exc)

        plan_id = new_plan_id()
        file_name = plan_filename(plan_id)
        try:
            file_path = save_plan_markdown(
                markdown=markdown,
                workspace_root=self._config.workspace_root,
                filename=file_name,
            )
        except Exception as exc:  # noqa: BLE001
            return None, f"计划文档落盘失败：{exc}"

        draft = PlanDraft(
            plan_id=plan_id,
            markdown=markdown,
            title=title,
            subtasks=subtasks,
            file_path=file_path,
            source=source,
            objective=objective.strip(),
            created_at_utc=utc_now_iso(),
        )
        self._pending_plan = PendingPlanState(
            draft=draft,
            tool_call_id=tool_call_id,
            route_to_resume=route_to_resume,
        )
        return draft, None

    def _format_pending_plan_prompt(self) -> str:
        """构造待审批计划提示。"""
        pending = self._pending_plan
        if pending is None:
            return "当前没有待审批计划。"
        draft = pending.draft
        return (
            "已生成计划草案，待你审批后继续执行。\n"
            f"- ID: `{draft.plan_id}`\n"
            f"- 文件: `{draft.file_path}`\n"
            f"- 标题: {draft.title}\n"
            f"- 子任务数: {len(draft.subtasks)}\n"
            "请执行以下命令之一：\n"
            f"- `/plan approve {draft.plan_id}` 批准并继续执行\n"
            f"- `/plan reject {draft.plan_id}` 拒绝该计划"
        )

    async def _intercept_task_create_with_plan(
        self,
        *,
        arguments: dict[str, Any],
        route_result: SkillMatchResult | None,
        tool_call_id: str,
        on_event: EventCallback | None,
    ) -> tuple[str, str | None, str | None]:
        """拦截 task_create，改为 planner 生成待审批计划。"""
        objective = self._task_create_objective(arguments)
        draft, error = await self._create_pending_plan_draft(
            objective=objective,
            source="task_create_hook",
            route_to_resume=route_result,
            tool_call_id=tool_call_id,
            on_event=on_event,
        )
        if error is not None:
            return f"计划生成失败：{error}", None, f"计划生成失败：{error}"
        assert draft is not None
        return self._format_pending_plan_prompt(), draft.plan_id, None

    async def _run_plan_mode_only(
        self,
        *,
        user_message: str,
        on_event: EventCallback | None,
    ) -> ChatResult:
        """plan mode 下仅生成计划，不进入常规执行循环。"""
        objective = user_message.strip()
        if not objective:
            return ChatResult(reply="plan mode 需要非空目标描述。")

        draft, error = await self._create_pending_plan_draft(
            objective=objective,
            source="plan_mode",
            route_to_resume=None,
            tool_call_id=None,
            on_event=on_event,
        )
        if error is not None:
            return ChatResult(reply=f"计划生成失败：{error}")
        assert draft is not None
        return ChatResult(reply=self._format_pending_plan_prompt())

    @staticmethod
    def _question_options_payload(question: PendingQuestion) -> list[dict[str, str]]:
        return [
            {
                "label": option.label,
                "description": option.description,
            }
            for option in question.options
        ]

    def _emit_user_question_event(
        self,
        *,
        question: PendingQuestion,
        on_event: EventCallback | None,
        iteration: int,
    ) -> None:
        self._emit(
            on_event,
            ToolCallEvent(
                event_type=EventType.USER_QUESTION,
                question_id=question.question_id,
                question_header=question.header,
                question_text=question.text,
                question_options=self._question_options_payload(question),
                question_multi_select=question.multi_select,
                question_queue_size=self._question_flow.queue_size(),
                iteration=iteration,
            ),
        )

    def _handle_ask_user(
        self,
        *,
        arguments: dict[str, Any],
        tool_call_id: str,
        on_event: EventCallback | None,
        iteration: int,
    ) -> tuple[str, str]:
        question_value = arguments.get("question")
        if not isinstance(question_value, dict):
            raise ValueError("工具参数错误: question 必须为对象。")

        pending = self._question_flow.enqueue(
            question_payload=question_value,
            tool_call_id=tool_call_id,
        )
        self._emit_user_question_event(
            question=pending,
            on_event=on_event,
            iteration=iteration,
        )
        return f"已创建待回答问题 `{pending.question_id}`。", pending.question_id

    def _enqueue_subagent_approval_question(
        self,
        *,
        approval_id: str,
        tool_name: str,
        picked_agent: str,
        task_text: str,
        normalized_paths: list[str],
        tool_call_id: str,
        on_event: EventCallback | None,
        iteration: int,
    ) -> PendingQuestion:
        """创建“子代理高风险审批”系统问题并入队。"""
        question_payload = {
            "header": "高风险确认",
            "text": (
                f"子代理 `{picked_agent}` 请求执行高风险工具 `{tool_name}`"
                f"（审批 ID: {approval_id}）。请选择后续动作。"
            ),
            "options": [
                {
                    "label": _SUBAGENT_APPROVAL_OPTION_ACCEPT,
                    "description": f"立即执行 `/accept {approval_id}`。",
                },
                {
                    "label": _SUBAGENT_APPROVAL_OPTION_FULLACCESS_RETRY,
                    "description": "先开启 fullAccess，再重试子代理任务。",
                },
                {
                    "label": _SUBAGENT_APPROVAL_OPTION_REJECT,
                    "description": f"执行 `/reject {approval_id}` 并停止本次高风险步骤。",
                },
            ],
            "multiSelect": False,
        }
        pending = self._question_flow.enqueue(
            question_payload=question_payload,
            tool_call_id=tool_call_id,
        )
        self._system_question_actions[pending.question_id] = {
            "type": _SYSTEM_Q_SUBAGENT_APPROVAL,
            "approval_id": approval_id,
            "picked_agent": picked_agent,
            "task_text": task_text,
            "normalized_paths": list(normalized_paths),
        }
        self._emit_user_question_event(
            question=pending,
            on_event=on_event,
            iteration=iteration,
        )
        return pending

    async def _handle_subagent_approval_answer(
        self,
        *,
        action: dict[str, Any],
        parsed: Any,
        on_event: EventCallback | None,
    ) -> ChatResult:
        """处理“子代理高风险审批”系统问题的回答。"""
        selected_options = parsed.selected_options if hasattr(parsed, "selected_options") else []
        selected_label = (
            str(selected_options[0].get("label", "")).strip()
            if selected_options
            else ""
        )
        approval_id = str(action.get("approval_id", "")).strip()
        picked_agent = str(action.get("picked_agent", "")).strip()
        task_text = str(action.get("task_text", "")).strip()
        normalized_paths = action.get("normalized_paths")
        file_paths = normalized_paths if isinstance(normalized_paths, list) else []

        if not approval_id:
            return ChatResult(reply="系统问题上下文缺失：approval_id 为空。")

        if selected_label == _SUBAGENT_APPROVAL_OPTION_ACCEPT:
            accept_reply = await self._handle_accept_command(["/accept", approval_id])
            reply = (
                f"{accept_reply}\n"
                "若需要子代理自动继续执行，建议选择“开启 fullAccess 后重试（推荐）”。"
            )
            return ChatResult(reply=reply)

        if selected_label == _SUBAGENT_APPROVAL_OPTION_FULLACCESS_RETRY:
            lines: list[str] = []
            if not self._full_access_enabled:
                self._full_access_enabled = True
                lines.append("已开启 fullAccess。当前代码技能权限：full_access。")
            else:
                lines.append("fullAccess 已开启。")

            reject_reply = self._handle_reject_command(["/reject", approval_id])
            lines.append(reject_reply)

            rerun_reply = await self._handle_delegate_to_subagent(
                task=task_text,
                agent_name=picked_agent or None,
                file_paths=file_paths,
                on_event=on_event,
            )
            lines.append("已按当前权限重新执行子代理任务：")
            lines.append(rerun_reply)
            return ChatResult(reply="\n".join(lines))

        if selected_label == _SUBAGENT_APPROVAL_OPTION_REJECT:
            reject_reply = self._handle_reject_command(["/reject", approval_id])
            reply = (
                f"{reject_reply}\n"
                "如需自动执行高风险步骤，可先使用 `/fullAccess on` 后重新发起任务。"
            )
            return ChatResult(reply=reply)

        manual = (
            "已记录你的回答。\n"
            f"当前审批 ID: `{approval_id}`\n"
            "你可以手动执行以下命令：\n"
            f"- `/accept {approval_id}`\n"
            "- `/fullAccess on`（可选）\n"
            f"- `/reject {approval_id}`"
        )
        return ChatResult(reply=manual)

    async def _handle_pending_question_answer(
        self,
        *,
        user_message: str,
        on_event: EventCallback | None,
    ) -> ChatResult:
        text = user_message.strip()
        current = self._question_flow.current()
        if current is None:
            self._pending_question_route_result = None
            return ChatResult(reply="当前没有待回答问题。")

        if text.startswith("/"):
            return ChatResult(
                reply=(
                    "当前有待回答问题，请先回答后再使用命令。\n\n"
                    f"{self._question_flow.format_prompt(current)}"
                )
            )

        try:
            parsed = self._question_flow.parse_answer(user_message, question=current)
        except ValueError as exc:
            return ChatResult(
                reply=f"回答格式错误：{exc}\n\n{self._question_flow.format_prompt(current)}"
            )

        popped = self._question_flow.pop_current()
        if popped is None:
            self._pending_question_route_result = None
            return ChatResult(reply="当前没有待回答问题。")

        tool_result = json.dumps(parsed.to_tool_result(), ensure_ascii=False)
        self._memory.add_tool_result(popped.tool_call_id, tool_result)
        logger.info("已接收问题回答: %s", parsed.question_id)

        system_action = self._system_question_actions.pop(parsed.question_id, None)
        if system_action is not None:
            self._pending_question_route_result = None
            action_type = str(system_action.get("type", "")).strip()
            if action_type == _SYSTEM_Q_SUBAGENT_APPROVAL:
                action_result = await self._handle_subagent_approval_answer(
                    action=system_action,
                    parsed=parsed,
                    on_event=on_event,
                )
            else:
                action_result = ChatResult(reply="已记录你的回答。")

            if self._question_flow.has_pending():
                next_question = self._question_flow.current()
                assert next_question is not None
                self._emit_user_question_event(
                    question=next_question,
                    on_event=on_event,
                    iteration=0,
                )
                merged = (
                    f"{action_result.reply}\n\n"
                    f"{self._question_flow.format_prompt(next_question)}"
                )
                return ChatResult(reply=merged)
            return action_result

        # 队列仍有待答问题，继续前台追问（不触发路由，不恢复执行）。
        if self._question_flow.has_pending():
            next_question = self._question_flow.current()
            assert next_question is not None
            self._emit_user_question_event(
                question=next_question,
                on_event=on_event,
                iteration=0,
            )
            return ChatResult(reply=self._question_flow.format_prompt(next_question))

        route_to_resume = self._pending_question_route_result
        self._pending_question_route_result = None
        if route_to_resume is None:
            return ChatResult(reply="已记录你的回答。")
        # 从上次中断的轮次之后继续执行
        resume_iteration = self._last_iteration_count + 1
        return await self._tool_calling_loop(
            route_to_resume, on_event, start_iteration=resume_iteration
        )

    async def _tool_calling_loop(
        self,
        route_result: SkillMatchResult,
        on_event: EventCallback | None,
        *,
        start_iteration: int = 1,
    ) -> ChatResult:
        """迭代循环体：LLM 请求 → thinking 提取 → 工具调用遍历 → 熔断检测。"""
        max_iter = self._config.max_iterations
        max_failures = self._config.max_consecutive_failures
        consecutive_failures = 0
        all_tool_results: list[ToolCallResult] = []
        # 恢复执行时保留之前的统计，仅首次调用时重置
        if start_iteration <= 1:
            self._last_iteration_count = 0
            self._last_tool_call_count = 0
            self._last_success_count = 0
            self._last_failure_count = 0
        # token 使用累计
        total_prompt_tokens = 0
        total_completion_tokens = 0

        for iteration in range(start_iteration, max_iter + 1):
            self._emit(
                on_event,
                ToolCallEvent(
                    event_type=EventType.ITERATION_START,
                    iteration=iteration,
                ),
            )

            system_prompts, context_error = self._prepare_system_prompts_for_request(
                route_result.system_contexts
            )
            if context_error is not None:
                self._last_iteration_count = iteration
                self._last_failure_count += 1
                self._memory.add_assistant_message(context_error)
                logger.warning("系统上下文预算检查失败，终止执行: %s", context_error)
                return ChatResult(
                    reply=context_error,
                    tool_calls=list(all_tool_results),
                    iterations=iteration,
                    truncated=False,
                    prompt_tokens=total_prompt_tokens,
                    completion_tokens=total_completion_tokens,
                    total_tokens=total_prompt_tokens + total_completion_tokens,
                )

            messages = self._memory.trim_for_request(
                system_prompts=system_prompts,
                max_context_tokens=self._config.max_context_tokens,
            )

            tool_scope = self._get_current_tool_scope(route_result=route_result)
            tools = self._build_tools_for_scope(tool_scope=tool_scope)

            kwargs: dict[str, Any] = {
                "model": self._active_model,
                "messages": messages,
            }
            if tools:
                kwargs["tools"] = tools

            response = await self._create_chat_completion_with_system_fallback(kwargs)
            message, usage = _extract_completion_message(response)
            tool_calls = _normalize_tool_calls(getattr(message, "tool_calls", None))

            # 累计 token 使用量
            if usage is not None:
                total_prompt_tokens += _usage_token(usage, "prompt_tokens")
                total_completion_tokens += _usage_token(usage, "completion_tokens")

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
            if not tool_calls:
                reply_text = _message_content_to_text(getattr(message, "content", None))
                if _looks_like_html_document(reply_text):
                    error_reply = self._format_html_endpoint_error(reply_text)
                    self._memory.add_assistant_message(error_reply)
                    self._last_iteration_count = iteration
                    logger.error(
                        "检测到疑似 HTML 页面响应，base_url=%s，已返回配置提示",
                        self._config.base_url,
                    )
                    logger.info("最终结果摘要: %s", _summarize_text(error_reply))
                    return ChatResult(
                        reply=error_reply,
                        tool_calls=list(all_tool_results),
                        iterations=iteration,
                        truncated=False,
                        prompt_tokens=total_prompt_tokens,
                        completion_tokens=total_completion_tokens,
                        total_tokens=total_prompt_tokens + total_completion_tokens,
                    )
                self._memory.add_assistant_message(reply_text)
                self._last_iteration_count = iteration
                logger.info("最终结果摘要: %s", _summarize_text(reply_text))
                return ChatResult(
                    reply=reply_text,
                    tool_calls=list(all_tool_results),
                    iterations=iteration,
                    truncated=False,
                    prompt_tokens=total_prompt_tokens,
                    completion_tokens=total_completion_tokens,
                    total_tokens=total_prompt_tokens + total_completion_tokens,
                )

            assistant_msg = _assistant_message_to_dict(message)
            if tool_calls:
                assistant_msg["tool_calls"] = [_to_plain(tc) for tc in tool_calls]
            self._memory.add_assistant_tool_message(assistant_msg)

            # 遍历工具调用
            breaker_triggered = False
            breaker_summary = ""
            breaker_skip_error = (
                f"工具未执行：连续 {max_failures} 次工具调用失败，已触发熔断。"
            )
            question_started = False

            for tc in tool_calls:
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
                    if tool_call_id:
                        self._memory.add_tool_result(tool_call_id, breaker_skip_error)
                    continue

                if question_started and tool_name != "ask_user":
                    skipped_msg = "工具未执行：存在待回答问题，当前轮次已跳过。"
                    skipped_result = ToolCallResult(
                        tool_name=tool_name,
                        arguments={},
                        result=skipped_msg,
                        success=True,
                        error=None,
                    )
                    all_tool_results.append(skipped_result)
                    if tool_call_id:
                        self._memory.add_tool_result(tool_call_id, skipped_msg)
                    self._last_tool_call_count += 1
                    self._last_success_count += 1
                    continue

                tc_result = await self._execute_tool_call(
                    tc,
                    tool_scope,
                    on_event,
                    iteration,
                    route_result=route_result,
                )

                all_tool_results.append(tc_result)
                if not tc_result.defer_tool_result and tool_call_id:
                    self._memory.add_tool_result(tool_call_id, tc_result.result)

                if tc_result.pending_approval:
                    reply = tc_result.result
                    self._memory.add_assistant_message(reply)
                    self._last_iteration_count = iteration
                    logger.info("工具调用进入待确认队列: %s", tc_result.approval_id)
                    logger.info("最终结果摘要: %s", _summarize_text(reply))
                    return ChatResult(
                        reply=reply,
                        tool_calls=list(all_tool_results),
                        iterations=iteration,
                        truncated=False,
                        prompt_tokens=total_prompt_tokens,
                        completion_tokens=total_completion_tokens,
                        total_tokens=total_prompt_tokens + total_completion_tokens,
                    )

                if tc_result.pending_plan:
                    reply = tc_result.result
                    self._memory.add_assistant_message(reply)
                    self._last_iteration_count = iteration
                    logger.info("工具调用进入待审批计划队列: %s", tc_result.plan_id)
                    logger.info("最终结果摘要: %s", _summarize_text(reply))
                    return ChatResult(
                        reply=reply,
                        tool_calls=list(all_tool_results),
                        iterations=iteration,
                        truncated=False,
                        prompt_tokens=total_prompt_tokens,
                        completion_tokens=total_completion_tokens,
                        total_tokens=total_prompt_tokens + total_completion_tokens,
                    )

                if tc_result.pending_question:
                    question_started = True
                    if self._pending_question_route_result is None:
                        self._pending_question_route_result = route_result

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

            if self._question_flow.has_pending():
                reply = self._question_flow.format_prompt()
                self._last_iteration_count = iteration
                logger.info("命中 ask_user，进入待回答状态")
                logger.info("最终结果摘要: %s", _summarize_text(reply))
                return ChatResult(
                    reply=reply,
                    tool_calls=list(all_tool_results),
                    iterations=iteration,
                    truncated=False,
                    prompt_tokens=total_prompt_tokens,
                    completion_tokens=total_completion_tokens,
                    total_tokens=total_prompt_tokens + total_completion_tokens,
                )

            if breaker_triggered:
                reply = (
                    f"连续 {max_failures} 次工具调用失败，已终止执行。"
                    f"错误摘要：\n{breaker_summary}"
                )
                self._memory.add_assistant_message(reply)
                self._last_iteration_count = iteration
                logger.warning("连续 %d 次工具失败，熔断终止", max_failures)
                logger.info("最终结果摘要: %s", _summarize_text(reply))
                return ChatResult(
                    reply=reply,
                    tool_calls=list(all_tool_results),
                    iterations=iteration,
                    truncated=False,
                    prompt_tokens=total_prompt_tokens,
                    completion_tokens=total_completion_tokens,
                    total_tokens=total_prompt_tokens + total_completion_tokens,
                )

        self._last_iteration_count = max_iter
        reply = f"已达到最大迭代次数（{max_iter}），返回当前结果。请尝试简化任务或分步执行。"
        self._memory.add_assistant_message(reply)
        logger.warning("达到迭代上限 %d，截断返回", max_iter)
        logger.info("最终结果摘要: %s", _summarize_text(reply))
        return ChatResult(
            reply=reply,
            tool_calls=list(all_tool_results),
            iterations=max_iter,
            truncated=True,
            prompt_tokens=total_prompt_tokens,
            completion_tokens=total_completion_tokens,
            total_tokens=total_prompt_tokens + total_completion_tokens,
        )

    async def _execute_tool_call(
        self,
        tc: Any,
        tool_scope: Sequence[str],
        on_event: EventCallback | None,
        iteration: int,
        route_result: SkillMatchResult | None = None,
    ) -> ToolCallResult:
        """单个工具调用：参数解析 → 执行 → 事件发射 → 返回结果。"""
        function = getattr(tc, "function", None)
        tool_name = getattr(function, "name", "")
        raw_args = getattr(function, "arguments", None)
        tool_call_id = getattr(tc, "id", "") or f"call_{int(time.time() * 1000)}"

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
        pending_question = False
        question_id: str | None = None
        pending_plan = False
        plan_id: str | None = None
        defer_tool_result = False

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
            hook_skill = self._pick_route_skill(route_result)
            pre_hook = self._run_skill_hook(
                skill=hook_skill,
                event=HookEvent.PRE_TOOL_USE,
                payload={
                    "tool_name": tool_name,
                    "arguments": dict(arguments),
                    "iteration": iteration,
                },
                tool_name=tool_name,
            )
            if pre_hook is not None and isinstance(pre_hook.updated_input, dict):
                arguments = dict(pre_hook.updated_input)

            if pre_hook is not None and pre_hook.decision == HookDecision.DENY:
                reason = pre_hook.reason or "Hook 拒绝执行该工具。"
                result_str = f"工具调用被 Hook 拒绝：{reason}"
                success = False
                error = result_str
                log_tool_call(logger, tool_name, arguments, error=error)
            elif pre_hook is not None and pre_hook.decision == HookDecision.ASK:
                try:
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
                except ValueError:
                    result_str = self._approval.pending_block_message()
                    success = False
                    error = result_str
                    log_tool_call(logger, tool_name, arguments, error=error)
            else:
                try:
                    if tool_scope is not None and tool_name not in set(tool_scope):
                        raise ToolNotAllowedError(f"工具 '{tool_name}' 不在授权范围内。")

                    skip_plan_once_for_task_create = False
                    if tool_name == "task_create" and self._suspend_task_create_plan_once:
                        skip_plan_once_for_task_create = True
                        self._suspend_task_create_plan_once = False

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
                    elif tool_name == "delegate_to_subagent":
                        task_value = arguments.get("task")
                        if not isinstance(task_value, str) or not task_value.strip():
                            result_str = "工具参数错误: task 必须为非空字符串。"
                            success = False
                            error = result_str
                        else:
                            agent_name_value = arguments.get("agent_name")
                            if agent_name_value is not None and not isinstance(agent_name_value, str):
                                result_str = "工具参数错误: agent_name 必须为字符串。"
                                success = False
                                error = result_str
                            else:
                                raw_file_paths = arguments.get("file_paths")
                                if raw_file_paths is not None and not isinstance(raw_file_paths, list):
                                    result_str = "工具参数错误: file_paths 必须为字符串数组。"
                                    success = False
                                    error = result_str
                                else:
                                    delegate_outcome = await self._delegate_to_subagent(
                                        task=task_value.strip(),
                                        agent_name=agent_name_value.strip() if isinstance(agent_name_value, str) else None,
                                        file_paths=raw_file_paths,
                                        on_event=on_event,
                                    )
                                    result_str = delegate_outcome.reply
                                    success = delegate_outcome.success
                                    error = None if success else result_str

                                    sub_result = delegate_outcome.subagent_result
                                    if (
                                        not success
                                        and sub_result is not None
                                        and sub_result.pending_approval_id is not None
                                    ):
                                        pending = self._approval.pending
                                        approval_id_value = sub_result.pending_approval_id
                                        high_risk_tool = (
                                            pending.tool_name
                                            if pending is not None and pending.approval_id == approval_id_value
                                            else "高风险工具"
                                        )
                                        question = self._enqueue_subagent_approval_question(
                                            approval_id=approval_id_value,
                                            tool_name=high_risk_tool,
                                            picked_agent=delegate_outcome.picked_agent or "subagent",
                                            task_text=delegate_outcome.task_text,
                                            normalized_paths=delegate_outcome.normalized_paths,
                                            tool_call_id=tool_call_id,
                                            on_event=on_event,
                                            iteration=iteration,
                                        )
                                        result_str = f"已创建待回答问题 `{question.question_id}`。"
                                        question_id = question.question_id
                                        pending_question = True
                                        defer_tool_result = True
                                        success = True
                                        error = None
                        log_tool_call(
                            logger,
                            tool_name,
                            arguments,
                            result=result_str if success else None,
                            error=error if not success else None,
                        )
                    elif tool_name == "list_subagents":
                        result_str = self._handle_list_subagents()
                        success = True
                        error = None
                        log_tool_call(
                            logger,
                            tool_name,
                            arguments,
                            result=result_str,
                        )
                    elif tool_name == "ask_user":
                        result_str, question_id = self._handle_ask_user(
                            arguments=arguments,
                            tool_call_id=tool_call_id,
                            on_event=on_event,
                            iteration=iteration,
                        )
                        success = True
                        error = None
                        pending_question = True
                        defer_tool_result = True
                        log_tool_call(
                            logger,
                            tool_name,
                            arguments,
                            result=result_str,
                        )
                    elif tool_name == "task_create" and not skip_plan_once_for_task_create:
                        result_str, plan_id, plan_error = await self._intercept_task_create_with_plan(
                            arguments=arguments,
                            route_result=route_result,
                            tool_call_id=tool_call_id,
                            on_event=on_event,
                        )
                        success = plan_error is None
                        error = plan_error
                        pending_plan = success
                        defer_tool_result = success
                        log_tool_call(
                            logger,
                            tool_name,
                            arguments,
                            result=result_str if success else None,
                            error=error if not success else None,
                        )
                    elif self._approval.is_audit_only_tool(tool_name):
                        result_value, audit_record = await self._execute_tool_with_audit(
                            tool_name=tool_name,
                            arguments=arguments,
                            tool_scope=tool_scope,
                            approval_id=self._approval.new_approval_id(),
                            created_at_utc=self._approval.utc_now(),
                            undoable=tool_name not in {"run_code", "run_shell"},
                        )
                        result_str = str(result_value)
                        tool_def = getattr(self._registry, "get_tool", lambda _: None)(tool_name)
                        if tool_def is not None:
                            result_str = tool_def.truncate_result(result_str)
                        success = True
                        error = None
                        log_tool_call(logger, tool_name, arguments, result=result_str)
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
                        elif self._approval.is_mcp_tool(tool_name):
                            # 非白名单 MCP 工具在 fullAccess 下可直接执行（不做文件审计）。
                            result_value = await asyncio.to_thread(
                                self._registry.call_tool,
                                tool_name,
                                arguments,
                                tool_scope=tool_scope,
                            )
                            result_str = str(result_value)
                            tool_def = getattr(self._registry, "get_tool", lambda _: None)(tool_name)
                            if tool_def is not None:
                                result_str = tool_def.truncate_result(result_str)
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
                                undoable=tool_name not in {"run_code", "run_shell"},
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

            post_hook_event = HookEvent.POST_TOOL_USE if success else HookEvent.POST_TOOL_USE_FAILURE
            post_hook = self._run_skill_hook(
                skill=hook_skill,
                event=post_hook_event,
                payload={
                    "tool_name": tool_name,
                    "arguments": dict(arguments),
                    "success": success,
                    "result": result_str,
                    "error": error,
                    "iteration": iteration,
                },
                tool_name=tool_name,
            )
            if post_hook is not None:
                if post_hook.additional_context:
                    result_str = f"{result_str}\n[Hook] {post_hook.additional_context}"
                if post_hook.decision == HookDecision.DENY:
                    reason = post_hook.reason or "post hook 拒绝"
                    success = False
                    error = reason
                    result_str = f"{result_str}\n[Hook 拒绝] {reason}"

        result_str = self._apply_tool_result_hard_cap(result_str)
        if error:
            error = self._apply_tool_result_hard_cap(str(error))

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
        if success and tool_name == "task_create" and not pending_plan:
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
            pending_question=pending_question,
            question_id=question_id,
            pending_plan=pending_plan,
            plan_id=plan_id,
            defer_tool_result=defer_tool_result,
        )

    def _apply_tool_result_hard_cap(self, text: str) -> str:
        """对工具结果应用全局硬截断，避免超长输出撑爆上下文。"""
        normalized = str(text or "")
        cap = int(self._config.tool_result_hard_cap_chars)
        if cap <= 0 or len(normalized) <= cap:
            return normalized
        return (
            f"{normalized[:cap]}\n"
            f"[结果已全局截断，原始长度: {len(normalized)} 字符，"
            f"上限: {cap} 字符]"
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
        if self._active_skill is not None:
            self._run_skill_hook(
                skill=self._active_skill,
                event=HookEvent.STOP,
                payload={"reason": "clear_memory"},
            )
            self._run_skill_hook(
                skill=self._active_skill,
                event=HookEvent.SESSION_END,
                payload={"reason": "clear_memory"},
            )
        self._memory.clear()
        self._loaded_skill_names.clear()
        self._hook_started_skills.clear()
        self._active_skill = None
        self._question_flow.clear()
        self._system_question_actions.clear()
        self._pending_question_route_result = None
        self._pending_plan = None
        self._approved_plan_context = None
        self._recent_excel_files.clear()
        self._last_subagent_name = None
        self._last_subagent_task = None

    # ── 多模型切换 ──────────────────────────────────

    @property
    def current_model(self) -> str:
        """当前使用的模型标识符。"""
        return self._active_model

    @property
    def current_model_name(self) -> str | None:
        """当前激活的模型 profile 短名称，None 表示使用默认配置。"""
        return self._active_model_name

    def list_models(self) -> list[dict[str, str]]:
        """列出所有可用模型档案，含当前激活标记。"""
        result: list[dict[str, str]] = []
        # 默认模型（来自主配置）
        is_default_active = self._active_model_name is None
        result.append({
            "name": "default",
            "model": self._config.model,
            "base_url": self._config.base_url,
            "description": "默认模型（主配置）",
            "active": "yes" if is_default_active else "",
        })
        for profile in self._config.models:
            result.append({
                "name": profile.name,
                "model": profile.model,
                "base_url": profile.base_url,
                "description": profile.description,
                "active": "yes" if self._active_model_name == profile.name else "",
            })
        return result

    def model_names(self) -> list[str]:
        """返回所有可用模型短名称列表（含 default）。"""
        names = ["default"]
        names.extend(p.name for p in self._config.models)
        return names

    def switch_model(self, name: str) -> str:
        """切换到指定模型档案。返回切换结果描述。

        支持智能匹配：精确匹配 > 前缀匹配 > 包含匹配。
        """
        name = name.strip()
        if not name:
            return "请指定模型名称。用法：/model <名称>，/model list 查看可用模型。"

        # 切换回默认
        if name.lower() == "default":
            self._active_model = self._config.model
            self._active_api_key = self._config.api_key
            self._active_base_url = self._config.base_url
            self._active_model_name = None
            self._client = openai.AsyncOpenAI(
                api_key=self._active_api_key,
                base_url=self._active_base_url,
            )
            self._sync_router_model_runtime()
            return f"已切换到默认模型：{self._config.model}"

        # 在 profiles 中查找：精确匹配 > 前缀匹配 > 包含匹配
        profiles = self._config.models
        lowered = name.lower()

        # 精确匹配
        matched = next((p for p in profiles if p.name.lower() == lowered), None)
        # 前缀匹配
        if matched is None:
            prefix_matches = [p for p in profiles if p.name.lower().startswith(lowered)]
            if len(prefix_matches) == 1:
                matched = prefix_matches[0]
        # 包含匹配（模型标识符中包含输入）
        if matched is None:
            contain_matches = [
                p for p in profiles
                if lowered in p.name.lower() or lowered in p.model.lower()
            ]
            if len(contain_matches) == 1:
                matched = contain_matches[0]

        if matched is None:
            available = ", ".join(p.name for p in profiles) if profiles else "无"
            return f"未找到模型 {name!r}。可用模型：default, {available}"

        self._active_model = matched.model
        self._active_api_key = matched.api_key
        self._active_base_url = matched.base_url
        self._active_model_name = matched.name
        self._client = openai.AsyncOpenAI(
            api_key=self._active_api_key,
            base_url=self._active_base_url,
        )
        self._sync_router_model_runtime()
        desc = f"（{matched.description}）" if matched.description else ""
        return f"已切换到模型：{matched.name} → {matched.model}{desc}"

    def _sync_router_model_runtime(self) -> None:
        """在主模型切换后同步路由模型运行时（仅跟随模式）。"""
        if not self._router_follow_active_model:
            return
        self._router_client = self._client
        self._router_model = self._active_model

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
                route_mode="all_tools",
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

    @staticmethod
    def _schema_accepts_string(schema: Any) -> bool:
        if not isinstance(schema, dict):
            return False
        type_value = schema.get("type")
        if type_value == "string":
            return True
        if isinstance(type_value, list) and "string" in type_value:
            return True
        return False

    def _map_command_dispatch_arguments(
        self,
        *,
        tool_name: str,
        raw_args: str,
    ) -> tuple[dict[str, Any] | None, str | None]:
        normalized_raw = raw_args.strip()
        if not normalized_raw:
            return {}, None

        try:
            parsed = json.loads(normalized_raw)
        except Exception:  # noqa: BLE001
            parsed = None

        if isinstance(parsed, dict):
            return parsed, None

        tool_def = getattr(self._registry, "get_tool", lambda _: None)(tool_name)
        if tool_def is None:
            return None, f"未找到命令分发目标工具：{tool_name}"

        schema = getattr(tool_def, "input_schema", {}) or {}
        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            return None, "命令分发失败：目标工具参数 schema 非法。"

        if len(properties) == 1:
            key, val = next(iter(properties.items()))
            if self._schema_accepts_string(val):
                return {key: normalized_raw}, None

        for candidate in ("input", "query", "text", "path"):
            if candidate in properties and self._schema_accepts_string(properties[candidate]):
                return {candidate: normalized_raw}, None

        return (
            None,
            "命令分发失败：无法将参数自动映射到工具入参。请使用 JSON 对象参数。",
        )

    async def _run_command_dispatch_skill(
        self,
        *,
        skill: Skillpack,
        raw_args: str,
        route_result: SkillMatchResult,
        on_event: EventCallback | None,
    ) -> ChatResult:
        tool_name = skill.command_tool or ""
        arguments, error_message = self._map_command_dispatch_arguments(
            tool_name=tool_name,
            raw_args=raw_args,
        )
        if arguments is None:
            reply = error_message or "命令分发失败。"
            self._memory.add_assistant_message(reply)
            self._last_iteration_count = 1
            self._last_tool_call_count = 0
            self._last_success_count = 0
            self._last_failure_count = 1
            return ChatResult(
                reply=reply,
                tool_calls=[],
                iterations=1,
                truncated=False,
            )

        tool_call_id = f"dispatch_{int(time.time() * 1000)}"
        tc = SimpleNamespace(
            id=tool_call_id,
            function=SimpleNamespace(
                name=tool_name,
                arguments=json.dumps(arguments, ensure_ascii=False),
            ),
        )
        tool_scope = self._get_current_tool_scope(route_result=route_result)
        tc_result = await self._execute_tool_call(
            tc,
            tool_scope,
            on_event,
            iteration=1,
            route_result=route_result,
        )

        if not tc_result.defer_tool_result:
            self._memory.add_tool_result(tool_call_id, tc_result.result)

        if tc_result.pending_question and self._pending_question_route_result is None:
            self._pending_question_route_result = route_result

        if self._question_flow.has_pending():
            reply = self._question_flow.format_prompt()
        else:
            reply = tc_result.result

        self._memory.add_assistant_message(reply)
        self._last_iteration_count = 1
        self._last_tool_call_count = 1
        self._last_success_count = 1 if tc_result.success else 0
        self._last_failure_count = 0 if tc_result.success else 1
        return ChatResult(
            reply=reply,
            tool_calls=[tc_result],
            iterations=1,
            truncated=False,
        )

    async def _handle_control_command(
        self,
        user_message: str,
        *,
        on_event: EventCallback | None = None,
    ) -> str | None:
        """处理会话级控制命令。命中时返回回复文本，否则返回 None。"""
        text = user_message.strip()
        if not text or not text.startswith("/"):
            return None

        parts = text.split()
        command = parts[0].strip().lower().replace("_", "")
        if command not in {"/fullaccess", "/subagent", "/accept", "/reject", "/undo", "/plan", "/planmode", "/model"}:
            return None

        self._last_route_result = SkillMatchResult(
            skills_used=[],
            tool_scope=[],
            route_mode="control_command",
            system_contexts=[],
        )

        action = parts[1].strip().lower() if len(parts) >= 2 else ""
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
            if action in {"status", ""} and len(parts) <= 2:
                status = "enabled" if self._subagent_enabled else "disabled"
                return f"当前 subagent 状态：{status}。"
            if action == "on" and len(parts) == 2:
                self._subagent_enabled = True
                return "已开启 subagent。"
            if action == "off" and len(parts) == 2:
                self._subagent_enabled = False
                return "已关闭 subagent。"
            if action == "list" and len(parts) == 2:
                return self._handle_list_subagents()
            if action == "run":
                agent_name, task, parse_error = self._parse_subagent_run_command(text)
                if parse_error is not None:
                    return parse_error
                assert task is not None
                outcome = await self._delegate_to_subagent(
                    task=task,
                    agent_name=agent_name,
                    on_event=on_event,
                )
                if (
                    not outcome.success
                    and outcome.subagent_result is not None
                    and outcome.subagent_result.pending_approval_id is not None
                ):
                    pending = self._approval.pending
                    approval_id_value = outcome.subagent_result.pending_approval_id
                    high_risk_tool = (
                        pending.tool_name
                        if pending is not None and pending.approval_id == approval_id_value
                        else "高风险工具"
                    )
                    question = self._enqueue_subagent_approval_question(
                        approval_id=approval_id_value,
                        tool_name=high_risk_tool,
                        picked_agent=outcome.picked_agent or "subagent",
                        task_text=outcome.task_text or task,
                        normalized_paths=outcome.normalized_paths,
                        tool_call_id=f"subagent_run_{int(time.time() * 1000)}",
                        on_event=on_event,
                        iteration=0,
                    )
                    return self._question_flow.format_prompt(question)
                return outcome.reply
            return (
                "无效参数。用法：/subagent [on|off|status|list]，"
                "或 /subagent run -- <task>，"
                "或 /subagent run <agent_name> -- <task>。"
            )

        if command in {"/plan", "/planmode"}:
            return await self._handle_plan_command(parts, on_event=on_event)

        if command == "/model":
            # /model → 显示当前模型
            # /model list → 列出所有可用模型
            # /model <name> → 切换模型
            if not action:
                name_display = self._active_model_name or "default"
                return f"当前模型：{name_display}（{self._active_model}）"
            if action == "list":
                rows = self.list_models()
                lines = ["可用模型："]
                for row in rows:
                    marker = " ✦" if row["active"] else ""
                    desc = f"  {row['description']}" if row["description"] else ""
                    lines.append(f"  {row['name']} → {row['model']}{desc}{marker}")
                return "\n".join(lines)
            # 其余视为模型名称，尝试切换
            model_arg = " ".join(parts[1:])
            return self.switch_model(model_arg)

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
                undoable=pending.tool_name not in {"run_code", "run_shell"},
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

    async def _handle_plan_command(
        self,
        parts: list[str],
        *,
        on_event: EventCallback | None,
    ) -> str:
        """处理 /plan 命令。"""
        action = parts[1].strip().lower() if len(parts) >= 2 else "status"

        if action in {"status", ""} and len(parts) <= 2:
            mode = "enabled" if self._plan_mode_enabled else "disabled"
            lines = [f"当前 plan mode 状态：{mode}。"]
            if self._pending_plan is not None:
                draft = self._pending_plan.draft
                lines.append(f"- 待审批计划 ID: `{draft.plan_id}`")
                lines.append(f"- 计划文件: `{draft.file_path}`")
                lines.append(f"- 子任务数: {len(draft.subtasks)}")
            return "\n".join(lines)

        if action == "on" and len(parts) == 2:
            self._plan_mode_enabled = True
            return "已开启 plan mode。后续普通对话将仅生成计划草案。"

        if action == "off" and len(parts) == 2:
            self._plan_mode_enabled = False
            return "已关闭 plan mode。"

        if action == "approve":
            return await self._handle_plan_approve(parts=parts, on_event=on_event)

        if action == "reject":
            return self._handle_plan_reject(parts=parts)

        return (
            "无效参数。用法：/plan [on|off|status]，"
            "或 /plan approve [plan_id]，"
            "或 /plan reject [plan_id]。"
        )

    async def _handle_plan_approve(
        self,
        *,
        parts: list[str],
        on_event: EventCallback | None,
    ) -> str:
        """批准待审批计划并自动继续执行。"""
        if len(parts) > 3:
            return "无效参数。用法：/plan approve [plan_id]。"

        if self._approval.has_pending():
            return (
                "当前存在高风险待确认操作，请先执行 `/accept <id>` 或 `/reject <id>`，"
                "再处理计划审批。"
            )

        pending = self._pending_plan
        if pending is None:
            return "当前没有待审批计划。"

        expected_id = pending.draft.plan_id
        provided_id = parts[2].strip() if len(parts) == 3 else ""
        if provided_id and provided_id != expected_id:
            return f"计划 ID 不匹配。当前待审批计划 ID 为 `{expected_id}`。"

        draft = pending.draft
        task_list = self._task_store.create(draft.title, draft.subtasks)
        self._approved_plan_context = (
            f"来源: {draft.file_path}\n"
            f"{draft.markdown.strip()}"
        )
        self._pending_plan = None
        self._plan_mode_enabled = False

        self._emit(
            on_event,
            ToolCallEvent(
                event_type=EventType.TASK_LIST_CREATED,
                task_list_data=task_list.to_dict(),
            ),
        )

        resume_prefix = (
            f"已批准计划 `{draft.plan_id}` 并创建任务清单「{draft.title}」，已切回执行模式。"
        )

        if draft.source == "task_create_hook":
            if pending.tool_call_id:
                self._memory.add_tool_result(
                    pending.tool_call_id,
                    (
                        f"计划 `{draft.plan_id}` 已批准并创建任务清单「{draft.title}」，"
                        f"共 {len(draft.subtasks)} 个子任务。"
                    ),
                )
            route_to_resume = pending.route_to_resume
            if route_to_resume is None:
                return resume_prefix

            self._suspend_task_create_plan_once = True
            try:
                resumed = await self._tool_calling_loop(route_to_resume, on_event)
            finally:
                self._suspend_task_create_plan_once = False
            return f"{resume_prefix}\n\n{resumed.reply}"

        self._suspend_task_create_plan_once = True
        try:
            resumed = await self.chat(draft.objective, on_event=on_event)
        finally:
            self._suspend_task_create_plan_once = False
        return f"{resume_prefix}\n\n{resumed.reply}"

    def _handle_plan_reject(self, *, parts: list[str]) -> str:
        """拒绝待审批计划。"""
        if len(parts) > 3:
            return "无效参数。用法：/plan reject [plan_id]。"

        if self._approval.has_pending():
            return (
                "当前存在高风险待确认操作，请先执行 `/accept <id>` 或 `/reject <id>`，"
                "再处理计划审批。"
            )

        pending = self._pending_plan
        if pending is None:
            return "当前没有待审批计划。"

        expected_id = pending.draft.plan_id
        provided_id = parts[2].strip() if len(parts) == 3 else ""
        if provided_id and provided_id != expected_id:
            return f"计划 ID 不匹配。当前待审批计划 ID 为 `{expected_id}`。"

        self._pending_plan = None
        return f"已拒绝计划 `{expected_id}`。"

    @staticmethod
    def _parse_subagent_run_command(
        text: str,
    ) -> tuple[str | None, str | None, str | None]:
        """解析 `/subagent run` 命令。"""
        raw = text.strip()
        lowered = raw.lower()
        prefix = ""
        for candidate in ("/subagent run", "/sub_agent run"):
            if lowered.startswith(candidate):
                prefix = candidate
                break
        if not prefix:
            return None, None, "无效参数。用法：/subagent run [agent_name] -- <task>。"

        rest = raw[len(prefix):].strip()
        if not rest:
            return None, None, "无效参数。用法：/subagent run [agent_name] -- <task>。"

        if rest.startswith("--"):
            task = rest[2:].strip()
            if not task:
                return None, None, "无效参数。`--` 后必须提供任务描述。"
            return None, task, None

        sep = " -- "
        if sep not in rest:
            return None, None, "无效参数。用法：/subagent run [agent_name] -- <task>。"
        agent_name, task = rest.split(sep, 1)
        agent_name = agent_name.strip()
        task = task.strip()
        if not agent_name or not task:
            return None, None, "无效参数。agent_name 与 task 都不能为空。"
        return agent_name, task, None

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

    @staticmethod
    def _system_prompts_token_count(system_prompts: Sequence[str]) -> int:
        total = 0
        for prompt in system_prompts:
            total += TokenCounter.count_message({"role": "system", "content": prompt})
        return total

    @staticmethod
    def _shrink_context_text(text: str) -> str:
        normalized = (text or "").strip()
        if not normalized:
            return ""
        if len(normalized) <= _MIN_SYSTEM_CONTEXT_CHARS:
            return ""
        keep_chars = max(_MIN_SYSTEM_CONTEXT_CHARS, len(normalized) // 2)
        shrinked = normalized[:keep_chars].rstrip()
        if _SYSTEM_CONTEXT_SHRINK_MARKER in shrinked:
            return shrinked
        return f"{shrinked}\n{_SYSTEM_CONTEXT_SHRINK_MARKER}"

    @staticmethod
    def _minimize_skill_context(text: str) -> str:
        lines = [line for line in str(text or "").splitlines() if line.strip()]
        if not lines:
            return ""
        head = lines[0]
        second = lines[1] if len(lines) > 1 else ""
        minimal_parts = [head]
        if second:
            minimal_parts.append(second)
        minimal_parts.append("[Skillpack 正文已省略以适配上下文窗口]")
        return "\n".join(minimal_parts)

    def _prepare_system_prompts_for_request(
        self,
        skill_contexts: list[str],
    ) -> tuple[list[str], str | None]:
        """构建用于本轮请求的 system prompts，并在必要时压缩上下文。"""
        base_prompt = self._memory.system_prompt

        access_notice = self._build_access_notice()
        if access_notice:
            base_prompt = base_prompt + "\n\n" + access_notice

        mcp_context = self._build_mcp_context_notice()
        if mcp_context:
            base_prompt = base_prompt + "\n\n" + mcp_context

        if self._transient_hook_contexts:
            hook_context = "\n".join(self._transient_hook_contexts).strip()
            self._transient_hook_contexts.clear()
            if hook_context:
                base_prompt = base_prompt + "\n\n## Hook 上下文\n" + hook_context

        approved_plan_context = self._build_approved_plan_context_notice()
        recent_excel_context = self._build_recent_excel_context_notice()
        current_skill_contexts = [
            ctx for ctx in skill_contexts if isinstance(ctx, str) and ctx.strip()
        ]

        def _compose_prompts() -> list[str]:
            mode = self._effective_system_mode()
            if mode == "merge":
                merged_parts = [base_prompt]
                if approved_plan_context:
                    merged_parts.append(approved_plan_context)
                if recent_excel_context:
                    merged_parts.append(recent_excel_context)
                merged_parts.extend(current_skill_contexts)
                return ["\n\n".join(merged_parts)]

            prompts = [base_prompt]
            if approved_plan_context:
                prompts.append(approved_plan_context)
            if recent_excel_context:
                prompts.append(recent_excel_context)
            prompts.extend(current_skill_contexts)
            return prompts

        threshold = max(1, int(self._config.max_context_tokens * 0.9))
        prompts = _compose_prompts()
        total_tokens = self._system_prompts_token_count(prompts)
        if total_tokens <= threshold:
            return prompts, None

        if approved_plan_context:
            approved_plan_context = self._shrink_context_text(approved_plan_context)
            prompts = _compose_prompts()
            total_tokens = self._system_prompts_token_count(prompts)
            if total_tokens <= threshold:
                return prompts, None
            approved_plan_context = ""

        if recent_excel_context:
            recent_excel_context = self._shrink_context_text(recent_excel_context)
            prompts = _compose_prompts()
            total_tokens = self._system_prompts_token_count(prompts)
            if total_tokens <= threshold:
                return prompts, None
            recent_excel_context = ""

        for idx in range(len(current_skill_contexts) - 1, -1, -1):
            minimized = self._minimize_skill_context(current_skill_contexts[idx])
            if minimized and minimized != current_skill_contexts[idx]:
                current_skill_contexts[idx] = minimized
                prompts = _compose_prompts()
                total_tokens = self._system_prompts_token_count(prompts)
                if total_tokens <= threshold:
                    return prompts, None

        while current_skill_contexts:
            current_skill_contexts.pop()
            prompts = _compose_prompts()
            total_tokens = self._system_prompts_token_count(prompts)
            if total_tokens <= threshold:
                return prompts, None

        if self._system_prompts_token_count(prompts) > threshold:
            return [], (
                "系统上下文过长，已无法在当前上下文窗口内继续执行。"
                "请减少附加上下文或拆分任务后重试。"
            )
        return prompts, None


    def _build_system_prompts(self, skill_contexts: list[str]) -> list[str]:
        base_prompt = self._memory.system_prompt

        # 注入权限状态说明，让 LLM 明确知道代码执行能力受限
        access_notice = self._build_access_notice()
        if access_notice:
            base_prompt = base_prompt + "\n\n" + access_notice

        approved_plan_context = self._build_approved_plan_context_notice()
        if approved_plan_context:
            base_prompt = base_prompt + "\n\n" + approved_plan_context

        recent_excel_context = self._build_recent_excel_context_notice()
        if recent_excel_context:
            base_prompt = base_prompt + "\n\n" + recent_excel_context

        # 注入 MCP 服务器概要，让 LLM 感知已连接的外部能力
        mcp_context = self._build_mcp_context_notice()
        if mcp_context:
            base_prompt = base_prompt + "\n\n" + mcp_context

        if self._transient_hook_contexts:
            hook_context = "\n".join(self._transient_hook_contexts).strip()
            self._transient_hook_contexts.clear()
            if hook_context:
                base_prompt = base_prompt + "\n\n## Hook 上下文\n" + hook_context

        if not skill_contexts:
            return [base_prompt]

        mode = self._effective_system_mode()
        if mode == "merge":
            merged = "\n\n".join([base_prompt, *skill_contexts])
            return [merged]

        return [base_prompt, *skill_contexts]

    def _build_approved_plan_context_notice(self) -> str:
        """注入已批准计划上下文。"""
        context = (self._approved_plan_context or "").strip()
        if not context:
            return ""
        if len(context) > _PLAN_CONTEXT_MAX_CHARS:
            truncated = context[:_PLAN_CONTEXT_MAX_CHARS]
            context = (
                f"{truncated}\n"
                f"[计划上下文已截断，原始长度: {len(self._approved_plan_context or '')} 字符]"
            )
        return f"## 已批准计划上下文\n{context}"

    def _build_access_notice(self) -> str:
        """当 fullAccess 关闭时，生成权限限制说明注入 system prompt。"""
        if self._full_access_enabled:
            return ""
        restricted = self._restricted_code_skillpacks
        if not restricted:
            return ""
        skill_list = "、".join(sorted(restricted))
        return (
            f"【权限提示】当前 fullAccess 权限处于关闭状态。"
            f"以下技能需要 fullAccess 权限才能激活：{skill_list}。"
            f"涉及代码执行的工具（如 write_text_file、run_code、run_shell）"
            f"在未激活对应技能时不应主动使用。"
            f"当用户询问是否能执行代码/脚本时，你应当告知用户：该能力存在但当前受限，"
            f"需要先使用 /fullAccess on 命令开启权限。"
        )

    def _build_mcp_context_notice(self) -> str:
        """生成已连接 MCP Server 的概要信息，注入 system prompt。"""
        servers = self._mcp_manager.get_server_info()
        if not servers:
            return ""
        lines = ["## MCP 扩展能力"]
        for srv in servers:
            name = srv["name"]
            tool_count = srv.get("tool_count", 0)
            tool_names = srv.get("tools", [])
            tools_str = "、".join(tool_names) if tool_names else "无"
            lines.append(f"- **{name}**（{tool_count} 个工具）：{tools_str}")
        lines.append(
            "以上 MCP 工具已注册，工具名带 `mcp_{server}_` 前缀，可直接调用。"
            "当用户询问你有哪些 MCP 或外部能力时，据此如实回答。"
        )
        return "\n".join(lines)

    def _build_recent_excel_context_notice(self) -> str:
        """注入近期 Excel 上下文，减少“这个文件”类追问的二次探查。"""
        if not self._recent_excel_files and not self._last_subagent_name:
            return ""

        lines = ["## 会话文件上下文（最近）"]
        if self._recent_excel_files:
            lines.append(
                "最近确认的 Excel 文件（按新到旧）："
                + "，".join(self._recent_excel_files)
            )
        if self._last_subagent_name:
            task = (self._last_subagent_task or "").strip()
            if task:
                lines.append(f"最近子代理任务：{self._last_subagent_name} / {task}")
            else:
                lines.append(f"最近子代理任务：{self._last_subagent_name}")
        lines.append(
            "当用户提到“这个文件/该文件”且无冲突时，优先指代上述最近文件；"
            "存在歧义时先澄清。"
        )
        return "\n".join(lines)

    def _update_recent_excel_context(
        self,
        *,
        candidate_paths: Sequence[str],
        subagent_name: str,
        task: str,
    ) -> None:
        """更新会话内最近 Excel 文件上下文。"""
        merged = list(self._recent_excel_files)
        seen = set(merged)
        for raw in candidate_paths:
            normalized = self._normalize_excel_path(raw)
            if not normalized or not self._is_excel_path(normalized):
                continue
            if normalized in seen:
                merged.remove(normalized)
            merged.insert(0, normalized)
            seen.add(normalized)
        self._recent_excel_files = merged[:_RECENT_EXCEL_FILE_MAX]
        self._last_subagent_name = subagent_name
        compact_task = " ".join(task.strip().split())
        if len(compact_task) > _RECENT_SUBAGENT_TASK_MAX_CHARS:
            compact_task = compact_task[:_RECENT_SUBAGENT_TASK_MAX_CHARS] + "..."
        self._last_subagent_task = compact_task or None

    @staticmethod
    def _normalize_excel_path(path: str) -> str:
        normalized = str(path).strip().replace("\\", "/")
        while normalized.startswith("./"):
            normalized = normalized[2:]
        return normalized

    @staticmethod
    def _is_excel_path(path: str) -> bool:
        lower = path.lower()
        return lower.endswith(".xlsx") or lower.endswith(".xlsm") or lower.endswith(".xls")

    def _effective_system_mode(self) -> str:
        configured = self._config.system_message_mode
        if configured != "auto":
            return configured
        if self._system_mode_fallback == "merge":
            return "merge"
        return "replace"

    def _format_html_endpoint_error(self, raw_text: str) -> str:
        """将 HTML 错配响应转换为可操作的配置提示。"""
        first_line = raw_text.strip().splitlines()[0] if raw_text.strip() else "(空)"
        preview = first_line[:120].replace("<", "[").replace(">", "]")
        return (
            "LLM 接口返回了 HTML 页面而不是模型 JSON 响应。\n"
            "这通常是 EXCELMANUS_BASE_URL 指向了网站首页，而不是 OpenAI 兼容 API 地址。\n"
            f"当前 EXCELMANUS_BASE_URL: {self._config.base_url}\n"
            "请改为可用的 API 端点（通常以 `/v1` 结尾），然后重试。\n"
            f"响应片段: {preview}"
        )

    async def _create_chat_completion_with_system_fallback(
        self,
        kwargs: dict[str, Any],
    ) -> Any:
        try:
            return await self._client.chat.completions.create(**kwargs)
        except Exception as exc:
            if (
                self._config.system_message_mode == "auto"
                and self._effective_system_mode() == "replace"
                and self._is_system_compatibility_error(exc)
            ):
                logger.warning("检测到 replace(system 分段) 兼容性错误，自动回退到 merge 模式")
                self._system_mode_fallback = "merge"
                source_messages = kwargs.get("messages")
                if not isinstance(source_messages, list):
                    raise
                merged_messages = self._merge_leading_system_messages(source_messages)
                retry_kwargs = dict(kwargs)
                retry_kwargs["messages"] = merged_messages
                return await self._client.chat.completions.create(**retry_kwargs)
            raise

    @staticmethod
    def _merge_leading_system_messages(messages: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        """将开头连续的多条 system 消息合并为一条，保持其余消息不变。"""
        normalized: list[dict[str, Any]] = []
        for msg in messages:
            if isinstance(msg, dict):
                normalized.append(dict(msg))
            else:
                normalized.append({"role": "user", "content": str(msg)})

        if not normalized:
            return normalized

        idx = 0
        parts: list[str] = []
        while idx < len(normalized):
            msg = normalized[idx]
            if msg.get("role") != "system":
                break
            content = msg.get("content")
            if isinstance(content, str) and content.strip():
                parts.append(content.strip())
            elif content is not None:
                parts.append(str(content))
            idx += 1

        if idx <= 1:
            return normalized

        merged_content = "\n\n".join(parts).strip()
        merged_message = {"role": "system", "content": merged_content}
        return [merged_message, *normalized[idx:]]

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
