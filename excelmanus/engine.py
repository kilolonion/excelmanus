"""Agent 核心引擎：Skillpack 路由 + Tool Calling 循环。"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from pathlib import Path
import json
import random
import re as _re
from dataclasses import dataclass, field, replace
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Literal

import openai

from excelmanus.approval import AppliedApprovalRecord, ApprovalManager, PendingApproval
from excelmanus.compaction import CompactionManager
from excelmanus.backup import BackupManager
from excelmanus.providers import create_client
from excelmanus.config import ExcelManusConfig, ModelProfile
from excelmanus.events import EventCallback, EventType, ToolCallEvent
from excelmanus.hooks import (
    HookAgentAction,
    HookCallContext,
    HookDecision,
    HookEvent,
    HookResult,
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
from excelmanus.task_list import TaskStatus, TaskStore
from excelmanus.tools import focus_tools, task_tools
from excelmanus.engine_core.command_handler import CommandHandler
from excelmanus.engine_core.context_builder import ContextBuilder
from excelmanus.engine_core.session_state import SessionState
from excelmanus.engine_core.subagent_orchestrator import SubagentOrchestrator
from excelmanus.engine_core.tool_dispatcher import ToolDispatcher
from excelmanus.mentions.parser import MentionParser, ResolvedMention
from excelmanus.mcp.manager import MCPManager, parse_tool_prefix
from excelmanus.tools.policy import MUTATING_ALL_TOOLS
from excelmanus.tools.registry import ToolNotAllowedError
from excelmanus.window_perception import (
    AdvisorContext,
    LifecyclePlan,
    PerceptionBudget,
    WindowPerceptionManager,
)
from excelmanus.window_perception.domain import Window
from excelmanus.window_perception.small_model import build_advisor_messages, parse_small_model_plan

if TYPE_CHECKING:
    from excelmanus.persistent_memory import PersistentMemory
    from excelmanus.memory_extractor import MemoryExtractor

logger = get_logger("engine")
_META_TOOL_NAMES = ("activate_skill", "delegate_to_subagent", "list_subagents", "ask_user")
_ALWAYS_AVAILABLE_TOOLS = (
    "task_create", "task_update", "ask_user", "delegate_to_subagent",
    "memory_save", "memory_read_topic",
)
_ALWAYS_AVAILABLE_TOOLS_SET = frozenset(_ALWAYS_AVAILABLE_TOOLS)
_SYSTEM_Q_SUBAGENT_APPROVAL = "subagent_high_risk_approval"
_SUBAGENT_APPROVAL_OPTION_ACCEPT = "立即接受并执行"
_SUBAGENT_APPROVAL_OPTION_FULLACCESS_RETRY = "开启 fullaccess 后重试（推荐）"
_SUBAGENT_APPROVAL_OPTION_REJECT = "拒绝本次操作"
_SYSTEM_Q_PLAN_APPROVAL = "plan_approval"
_PLAN_APPROVAL_OPTION_APPROVE = "批准执行"
_PLAN_APPROVAL_OPTION_REJECT = "拒绝计划"
_WINDOW_ADVISOR_RETRY_DELAY_MIN_SECONDS = 0.3
_WINDOW_ADVISOR_RETRY_DELAY_MAX_SECONDS = 0.8
_WINDOW_ADVISOR_RETRY_AFTER_CAP_SECONDS = 1.5
_WINDOW_ADVISOR_RETRY_TIMEOUT_CAP_SECONDS = 8.0
_VALID_WRITE_HINTS = {"may_write", "read_only", "unknown"}
_SKILL_AGENT_ALIASES = {
    "explore": "explorer",
    "plan": "planner",
    "general-purpose": "analyst",
    "generalpurpose": "analyst",
}


def _normalize_write_hint(value: Any) -> str:
    """规范化 write_hint，仅返回 may_write/read_only/unknown。"""
    if not isinstance(value, str):
        return "unknown"
    normalized = value.strip().lower()
    if normalized in _VALID_WRITE_HINTS:
        return normalized
    return "unknown"


def _merge_write_hint(route_hint: Any, fallback_hint: Any) -> str:
    """优先使用路由 write_hint；无效时回退到当前状态。"""
    normalized_route = _normalize_write_hint(route_hint)
    if normalized_route != "unknown":
        return normalized_route
    return _normalize_write_hint(fallback_hint)


def _merge_write_hint_with_override(route_hint: Any, override_hint: Any) -> str:
    """合并 write_hint，但 override_hint == 'may_write' 时强制覆盖 route_hint。

    用于写入工具成功后的场景：self._current_write_hint 已被
    升级为 'may_write'，不应被原始 route_hint（如 'read_only'）压制。
    """
    normalized_override = _normalize_write_hint(override_hint)
    if normalized_override == "may_write":
        return "may_write"
    return _merge_write_hint(route_hint, override_hint)


# ── Mention 上下文 XML 组装 ──────────────────────────────

# 各 mention 类型对应的 XML 标签名和属性名
_MENTION_XML_TAG_MAP: dict[str, tuple[str, str]] = {
    "file": ("file", "path"),
    "folder": ("folder", "path"),
    "skill": ("skill", "name"),
    "mcp": ("mcp", "server"),
}


def build_mention_context_block(
    mention_contexts: list[ResolvedMention],
) -> str:
    """将 ResolvedMention 列表组装为 <mention_context> XML 块。

    规则：
    - 成功解析的 mention 用类型对应的 XML 标签包裹 context_block
    - 解析失败的 mention 用 <error> 标签包裹错误信息
    - img 类型跳过（不生成 context block）
    - 列表为空时返回空字符串
    """
    if not mention_contexts:
        return ""

    parts: list[str] = []
    for rm in mention_contexts:
        # img 类型不生成 context block
        if rm.mention.kind == "img":
            continue

        if rm.error:
            parts.append(
                f'<error ref="{rm.mention.raw}">\n  {rm.error}\n</error>'
            )
        elif rm.context_block:
            tag_info = _MENTION_XML_TAG_MAP.get(rm.mention.kind)
            if tag_info:
                tag, attr = tag_info
                parts.append(
                    f'<{tag} {attr}="{rm.mention.value}">\n'
                    f"{rm.context_block}\n"
                    f"</{tag}>"
                )

    if not parts:
        return ""

    inner = "\n".join(parts)
    return f"<mention_context>\n{inner}\n</mention_context>"


_TO_PLAIN_MAX_DEPTH = 32


def _to_plain(value: Any, _depth: int = 0) -> Any:
    """将 SDK 对象/命名空间对象转换为纯 Python 结构。"""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if _depth >= _TO_PLAIN_MAX_DEPTH:
        return str(value)
    if isinstance(value, dict):
        return {k: _to_plain(v, _depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_plain(v, _depth + 1) for v in value]

    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return _to_plain(model_dump(exclude_none=False), _depth + 1)
        except TypeError:
            return _to_plain(model_dump(), _depth + 1)

    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return _to_plain(to_dict(), _depth + 1)

    if hasattr(value, "__dict__"):
        return {k: _to_plain(v, _depth + 1) for k, v in vars(value).items() if not k.startswith("_")}

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


def _extract_cached_tokens(usage: Any) -> int:
    """从 usage.prompt_tokens_details.cached_tokens 提取缓存命中 token 数。

    兼容 OpenAI SDK 对象和 dict 两种格式。非 OpenAI provider 无此字段时返回 0。
    """
    if usage is None:
        return 0
    details = (
        usage.get("prompt_tokens_details")
        if isinstance(usage, dict)
        else getattr(usage, "prompt_tokens_details", None)
    )
    if details is None:
        return 0
    raw = (
        details.get("cached_tokens")
        if isinstance(details, dict)
        else getattr(details, "cached_tokens", 0)
    )
    try:
        return int(raw or 0)
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


# ── 执行守卫：检测"仅建议不执行"的回复 ──────────────────────

_FORMULA_ADVICE_PATTERN = _re.compile(
    r"=(?:IF|DATE|VLOOKUP|HLOOKUP|INDEX|MATCH|SUMIF|COUNTIF|CONCATENATE|LEFT|RIGHT|MID|"
    r"AVERAGE|MAX|MIN|SUM|TRIM|LEN|FIND|SEARCH|IFERROR|AND|OR|NOT|TEXT|VALUE|ROUND|"
    r"SUMPRODUCT|OFFSET|INDIRECT|SUBSTITUTE|UPPER|LOWER|PROPER|DATEDIF|YEARFRAC|"
    r"NETWORKDAYS|WORKDAY|EOMONTH|EDATE|DAYS|DATEVALUE|TIMEVALUE|NOW|TODAY|"
    r"LARGE|TEXTJOIN|LET|TEXTSPLIT|XMATCH|VSTACK|SEQUENCE|FILTER|SORT|UNIQUE|"
    r"LAMBDA|CHOOSECOLS|CHOOSEROWS|HSTACK)\s*\(",
    _re.IGNORECASE,
)

_FORMULA_ADVICE_FALLBACK_PATTERN = _re.compile(
    r"(?<![<>=!])=(?![<>=])\s*[A-Z][A-Z0-9_]{2,}\s*\(",
)

_VBA_MACRO_ADVICE_PATTERN = _re.compile(
    r"(```\s*vb|Sub\s+\w+\s*\(|End\s+Sub\b|\.Range\s*\(|\.Cells\s*\("
    r"|Application\.\w+|Dim\s+\w+\s+As\s)",
    _re.IGNORECASE,
)

# 用户主动请求 VBA 相关帮助的检测模式
_USER_VBA_REQUEST_PATTERN = _re.compile(
    r"(VBA|宏|macro|vbaProject"
    r"|查看.*(?:宏|VBA|macro)|(?:宏|VBA|macro).*(?:代码|源码|内容|逻辑|模块)"
    r"|解[释读析].*(?:宏|VBA|macro)|(?:宏|VBA|macro).*(?:什么|哪些|有没有|是否)"
    r"|inspect.*vba|include.*vba"
    r"|提取.*(?:宏|VBA)|(?:宏|VBA).*提取)",
    _re.IGNORECASE,
)


def _user_requests_vba(text: str) -> bool:
    """检测用户消息是否主动请求 VBA/宏相关帮助（查看、解释、提取等）。"""
    if not text:
        return False
    return bool(_USER_VBA_REQUEST_PATTERN.search(text))


def _contains_formula_advice(text: str, *, vba_exempt: bool = False) -> bool:
    """检测回复文本中是否包含 Excel 公式或 VBA/宏代码建议（而非实际执行）。

    Args:
        text: 回复文本。
        vba_exempt: 若为 True，跳过 VBA 宏模式检测（用户主动请求 VBA 时）。
    """
    if not text:
        return False
    if _FORMULA_ADVICE_PATTERN.search(text) or _FORMULA_ADVICE_FALLBACK_PATTERN.search(text):
        return True
    if not vba_exempt and _VBA_MACRO_ADVICE_PATTERN.search(text):
        return True
    return False


_WRITE_ACTION_VERBS = _re.compile(
    r"(删除|替换|写入|创建|修改|格式化|转置|排序|过滤|合并|计算|填充|插入|移动|复制到|粘贴|更新|设置|调整|添加|生成"
    r"|delete|remove|replace|write|create|modify|format|transpose|merge"
    r"|fill|insert|move|paste|update|generate"
    r"|find\s+and\s+(?:replace|delete)|put\s+in|place\s+in|enter\s+in|apply)",
    _re.IGNORECASE,
)

_FILE_REFERENCE_PATTERN = _re.compile(
    r"(\.\s*xlsx\b|\.\s*xls\b|\.\s*csv\b|[A-Za-z0-9_\-/\\]+\.(?:xlsx|xls|csv))",
    _re.IGNORECASE,
)


def _detect_write_intent(text: str) -> bool:
    """检测用户消息是否同时包含文件引用和写入动作动词。"""
    if not text:
        return False
    has_file = bool(_FILE_REFERENCE_PATTERN.search(text))
    has_action = bool(_WRITE_ACTION_VERBS.search(text))
    return has_file and has_action


# 写入类工具集合统一复用策略层 SSOT，避免与 policy 漂移
_WRITE_TOOL_NAMES = MUTATING_ALL_TOOLS


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
    finish_accepted: bool = False


class _AuditedExecutionError(Exception):
    """携带审计记录的工具执行异常。"""

    def __init__(self, *, cause: Exception, record: AppliedApprovalRecord) -> None:
        super().__init__(str(cause))
        self.cause = cause
        self.record = record


@dataclass
class TurnDiagnostic:
    """单次 LLM 迭代的诊断快照，用于事后分析。"""

    iteration: int
    # token 使用
    prompt_tokens: int = 0
    completion_tokens: int = 0
    # provider 缓存命中的 token 数（OpenAI prompt_tokens_details.cached_tokens）
    cached_tokens: int = 0
    # 模型 thinking/reasoning 内容
    thinking_content: str = ""
    # 该迭代暴露给模型的工具名列表
    tool_names: list[str] = field(default_factory=list)
    # 门禁事件
    guard_events: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "iteration": self.iteration,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
        }
        if self.cached_tokens:
            d["cached_tokens"] = self.cached_tokens
        if self.thinking_content:
            d["thinking_content"] = self.thinking_content
        if self.tool_names:
            d["tool_names"] = self.tool_names
        if self.guard_events:
            d["guard_events"] = self.guard_events
        return d


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
    write_guard_triggered: bool = False
    # 诊断数据：每轮迭代的快照
    turn_diagnostics: list[TurnDiagnostic] = field(default_factory=list)
    # 路由诊断
    write_hint: str = ""
    route_mode: str = ""
    skills_used: list[str] = field(default_factory=list)
    task_tags: tuple[str, ...] = ()

    def __str__(self) -> str:
        """兼容旧调用方将 chat 结果当作字符串直接使用。"""
        return self.reply

    def __hash__(self) -> int:
        """自定义 __eq__ 后必须显式定义 __hash__，否则实例不可哈希。"""
        return hash(self.reply)

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
        """兼容 result.strip()/startswith() 等字符串方法。

        使用 object.__getattribute__ 避免 self.reply 未初始化时无限递归。
        """
        try:
            reply = object.__getattribute__(self, "reply")
        except AttributeError:
            raise AttributeError(name) from None
        return getattr(reply, name)


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

    # auto 模式系统消息兼容性探测结果（类级缓存，跨会话复用）
    # None = 尚未探测；"merge" = 已确认需要合并
    _system_mode_fallback_cache: str | None = None

    def __init__(
        self,
        config: ExcelManusConfig,
        registry: Any,
        skill_router: SkillRouter | None = None,
        persistent_memory: PersistentMemory | None = None,
        memory_extractor: MemoryExtractor | None = None,
        mcp_manager: MCPManager | None = None,
        own_mcp_manager: bool = True,
    ) -> None:
        # ── 解耦组件（Phase 3）：必须在所有 property 代理字段赋值之前初始化 ──
        self._state = SessionState()
        self._client = create_client(
            api_key=config.api_key,
            base_url=config.base_url,
        )
        # 路由子代理：优先使用独立的小模型，未配置时回退到主模型
        if config.router_model:
            self._router_client = create_client(
                api_key=config.router_api_key or config.api_key,
                base_url=config.router_base_url or config.base_url,
            )
            self._router_model = config.router_model
            self._router_follow_active_model = False
        else:
            self._router_client = self._client
            self._router_model = config.model
            self._router_follow_active_model = True
        # 窗口感知顾问小模型：window_advisor_* + aux_model → 主模型
        _adv_api_key = config.window_advisor_api_key or config.api_key
        _adv_base_url = config.window_advisor_base_url or config.base_url
        _adv_model = config.aux_model or config.model
        # 始终创建独立 client，避免与 _client 共享对象导致测试 mock 互相干扰
        self._advisor_client = create_client(
            api_key=_adv_api_key,
            base_url=_adv_base_url,
        )
        self._advisor_model = _adv_model
        # adviser 是否跟随主模型切换：仅当未配置辅助模型时
        self._advisor_follow_active_model = not config.aux_model
        # VLM 独立客户端：vlm_* → 主模型
        _vlm_api_key = config.vlm_api_key or config.api_key
        _vlm_base_url = config.vlm_base_url or config.base_url
        _vlm_model = config.vlm_model or config.model
        if config.vlm_base_url:
            self._vlm_client = create_client(
                api_key=_vlm_api_key,
                base_url=_vlm_base_url,
            )
        else:
            self._vlm_client = self._client
        self._vlm_model = _vlm_model
        self._config = config
        # ── 视觉能力推断 ──
        self._is_vision_capable = self._infer_vision_capable(config)
        # B 通道可用：vlm_enhance 开启 且 有独立 VLM 配置（或主模型可作为 VLM）
        self._vlm_enhance_available = (
            config.vlm_enhance
            and bool(config.vlm_api_key or config.vlm_base_url or config.vlm_model)
        )
        if config.vlm_enhance and not self._vlm_enhance_available:
            logger.info("VLM 增强已开启但未配置独立 VLM，B 通道不可用")
        logger.info(
            "视觉模式: main_vision=%s, vlm_enhance=%s",
            self._is_vision_capable, self._vlm_enhance_available,
        )
        # fork 出 per-session registry，避免多会话共享同一实例时
        # 会话级工具（task_tools / skill_tools）重复注册抛出 ToolRegistryError
        self._registry = registry.fork() if hasattr(registry, "fork") else registry
        self._skill_router = skill_router
        self._skillpack_manager = (
            SkillpackManager(config, skill_router._loader)
            if skill_router is not None
            else None
        )
        self._memory = ConversationMemory(config)
        # 运行时变量注入系统提示词
        resolved_root = str(Path(config.workspace_root).resolve())
        _runtime_vars = {
            "workspace_root": resolved_root,
        }
        for _var_key, _var_val in _runtime_vars.items():
            self._memory.system_prompt = self._memory.system_prompt.replace(
                f"{{{_var_key}}}", _var_val
            )
        # ── 动态能力图谱注入 ──────────────────────────────────
        try:
            from excelmanus.introspection.capability_map import CapabilityMapGenerator
            _cap_gen = CapabilityMapGenerator(registry=self._registry)
            self._capability_map_text = _cap_gen.generate()
            self._memory.system_prompt = self._memory.system_prompt.replace(
                "{auto_generated_capability_map}", self._capability_map_text
            )
        except Exception:
            logger.debug("能力图谱生成失败，使用占位符", exc_info=True)
            self._capability_map_text = ""
            # 移除未替换的占位符，避免 LLM 看到原始模板标记
            self._memory.system_prompt = self._memory.system_prompt.replace(
                "{auto_generated_capability_map}", ""
            )
        self._last_route_result = SkillMatchResult(
            skills_used=[],
            route_mode="all_tools",
            system_contexts=[],
        )
        # 任务清单存储：单会话内存级，闭包注入避免全局状态污染
        self._task_store = TaskStore()
        self._registry.register_tools(task_tools.get_tools(self._task_store))
        # 会话级权限控制：默认限制代码 Skillpack，显式 /fullaccess 后解锁
        self._full_access_enabled: bool = False
        # 会话级子代理开关：初始化继承配置，可通过 /subagent 动态切换
        self._subagent_enabled: bool = config.subagent_enabled
        self._subagent_registry = SubagentRegistry(config)
        self._restricted_code_skillpacks: set[str] = {"excel_code_runner"}
        # 会话级 skill 累积：记录本会话已加载过的 skill 名称及其最后激活轮次
        self._loaded_skill_names: dict[str, int] = {}
        # 当前激活技能列表：末尾为主 skill，空列表表示未激活
        self._active_skills: list[Skillpack] = []
        # auto 模式系统消息回退缓存（已迁移至类变量 _system_mode_fallback_cache）
        # 保留实例属性作为向后兼容别名
        self._system_mode_fallback: str | None = type(self)._system_mode_fallback_cache
        # ── 解耦组件（Phase 3）：状态变量已由 self._state 管理 ──
        # self._state 在 __init__ 顶部初始化，以下属性通过 @property 代理访问：
        # _session_turn, _last_iteration_count, _last_tool_call_count,
        # _last_success_count, _last_failure_count, _current_write_hint,
        # _has_write_tool_call, _turn_diagnostics, _session_diagnostics,
        # _execution_guard_fired, _vba_exempt, _finish_task_warned
        self._subagent_orchestrator: SubagentOrchestrator | None = None  # 延迟初始化（需要 self）
        self._tool_dispatcher: ToolDispatcher | None = None  # 延迟初始化（需要 registry fork）
        self._approval = ApprovalManager(config.workspace_root)
        # ── 备份沙盒模式 ──────────────────────────────────
        self._backup_enabled: bool = config.backup_enabled
        self._backup_manager: BackupManager | None = (
            BackupManager(workspace_root=config.workspace_root)
            if config.backup_enabled
            else None
        )
        self._subagent_executor = SubagentExecutor(
            parent_config=config,
            parent_registry=registry,
            approval_manager=self._approval,
        )
        self._hook_runner = SkillHookRunner(config)
        self._transient_hook_contexts: list[str] = []
        self._hook_started_skills: set[str] = set()
        self._hook_agent_action_depth: int = 0
        self._question_flow = QuestionFlowManager(max_queue_size=8)
        self._system_question_actions: dict[str, dict[str, Any]] = {}
        self._pending_question_route_result: SkillMatchResult | None = None
        self._pending_approval_route_result: SkillMatchResult | None = None
        self._pending_approval_tool_call_id: str | None = None
        self._plan_mode_enabled: bool = False
        self._plan_intercept_task_create: bool = False
        self._bench_mode: bool = False
        self._mention_contexts: list[ResolvedMention] | None = None
        self._pending_plan: PendingPlanState | None = None
        self._approved_plan_context: str | None = None
        self._suspend_task_create_plan_once: bool = False
        self._window_perception = WindowPerceptionManager(
            enabled=config.window_perception_enabled,
            budget=PerceptionBudget(
                system_budget_tokens=config.window_perception_system_budget_tokens,
                tool_append_tokens=config.window_perception_tool_append_tokens,
                max_windows=config.window_perception_max_windows,
                default_rows=config.window_perception_default_rows,
                default_cols=config.window_perception_default_cols,
                minimized_tokens=config.window_perception_minimized_tokens,
                background_after_idle=config.window_perception_background_after_idle,
                suspend_after_idle=config.window_perception_suspend_after_idle,
                terminate_after_idle=config.window_perception_terminate_after_idle,
                window_full_max_rows=config.window_full_max_rows,
                window_full_total_budget_tokens=config.window_full_total_budget_tokens,
                window_data_buffer_max_rows=config.window_data_buffer_max_rows,
            ),
            adaptive_model_mode_overrides=dict(config.adaptive_model_mode_overrides or {}),
            advisor_mode=(
                "rules"
                if config.window_perception_advisor_mode == "rules"
                else "hybrid"
            ),
            advisor_trigger_window_count=config.window_perception_advisor_trigger_window_count,
            advisor_trigger_turn=config.window_perception_advisor_trigger_turn,
            advisor_plan_ttl_turns=config.window_perception_advisor_plan_ttl_turns,
            intent_enabled=config.window_intent_enabled,
            intent_sticky_turns=config.window_intent_sticky_turns,
            intent_repeat_warn_threshold=config.window_intent_repeat_warn_threshold,
            intent_repeat_trip_threshold=config.window_intent_repeat_trip_threshold,
        )
        self._window_perception.bind_async_advisor_runner(
            self._run_window_perception_advisor_async
        )
        focus_tools.init_focus_manager(
            manager=self._window_perception,
            refill_reader=self._focus_window_refill_reader,
        )

        # ── 上下文自动压缩（Compaction）──────────────────────
        self._compaction_manager = CompactionManager(config)

        # ── PromptComposer 集成 ─────────────────────────────
        self._prompt_composer: Any = None
        try:
            from excelmanus.prompt_composer import PromptComposer as _PC
            _prompts_dir = Path(__file__).resolve().parent / "prompts"
            if _prompts_dir.is_dir():
                self._prompt_composer = _PC(_prompts_dir)
                self._prompt_composer.load_all()
        except Exception:
            logger.debug("PromptComposer 初始化失败，策略注入不可用", exc_info=True)

        # ── Workspace Manifest（工作区文件清单） ─────────────
        self._workspace_manifest: Any = None  # WorkspaceManifest | None
        self._workspace_manifest_built: bool = False
        self._manifest_refresh_needed: bool = False

        # ── 持久记忆集成 ────────────────────────
        self._persistent_memory = persistent_memory
        self._memory_extractor = memory_extractor
        # 语义记忆增强层（延迟初始化，待首轮 chat 时异步同步索引）
        self._semantic_memory: Any = None  # SemanticMemory | None
        self._embedding_client: Any = None  # EmbeddingClient | None
        if persistent_memory is not None and config.embedding_enabled:
            try:
                from excelmanus.embedding.client import EmbeddingClient
                from excelmanus.embedding.semantic_memory import SemanticMemory
                _emb_openai_client = openai.AsyncOpenAI(
                    api_key=config.embedding_api_key or config.api_key,
                    base_url=config.embedding_base_url or config.base_url,
                )
                self._embedding_client = EmbeddingClient(
                    client=_emb_openai_client,
                    model=config.embedding_model,
                    dimensions=config.embedding_dimensions,
                    timeout_seconds=config.embedding_timeout_seconds,
                )
                self._semantic_memory = SemanticMemory(
                    persistent_memory=persistent_memory,
                    embedding_client=self._embedding_client,
                    top_k=config.memory_semantic_top_k,
                    threshold=config.memory_semantic_threshold,
                    fallback_recent=config.memory_semantic_fallback_recent,
                )
            except Exception:
                logger.debug("语义记忆初始化失败，回退到传统加载", exc_info=True)
                self._semantic_memory = None
                self._embedding_client = None
        # 会话启动时加载核心记忆到 system prompt（同步回退，语义检索在首轮 chat 时异步执行）
        if persistent_memory is not None:
            core_memory = persistent_memory.load_core()
            if core_memory:
                original = self._memory.system_prompt
                self._memory.system_prompt = (
                    f"{original}\n\n## 持久记忆\n{core_memory}"
                )

        # ── MCP Client 集成 ──────────────────────────────────
        self._mcp_manager = mcp_manager or MCPManager(config.workspace_root)
        self._own_mcp_manager = own_mcp_manager

        # ── 多模型切换 ──────────────────────────────────
        self._active_model: str = config.model
        self._active_api_key: str = config.api_key
        self._active_base_url: str = config.base_url
        self._active_model_name: str | None = None  # 当前激活的 profile name

        # ── 解耦组件延迟初始化 ──────────────────────────────
        self._tool_dispatcher = ToolDispatcher(self)
        self._subagent_orchestrator = SubagentOrchestrator(self)
        self._command_handler = CommandHandler(self)
        self._context_builder = ContextBuilder(self)

    @staticmethod
    def _infer_vision_capable(config: "ExcelManusConfig") -> bool:
        """推断主模型是否支持视觉输入。"""
        mv = config.main_model_vision
        if mv == "true":
            return True
        if mv == "false":
            return False
        # auto: 根据模型名关键词推断
        model_lower = config.model.lower()
        _NON_VISION_KEYWORDS = (
            # OpenAI o-mini 文本推理模型（无视觉）
            "o1-mini", "o3-mini",
            # Amazon Nova 文本/语音模型（无视觉）
            "amazon.nova-micro", "amazon.nova-sonic",
            # Gemini embedding（文本向量）
            "gemini-embedding",
            # Llama 3.2 文本模型（非 Vision 版本）
            "llama-3.2-1b", "llama-3.2-3b",
            # Stepfun 当前无图片理解标记的 flash 变体
            "step-3.5-flash",
            # Mistral Small 3.0/3.1 文本模型（3.2 起支持视觉）
            "mistral-small-3.0", "mistral-small-3.1",
        )
        if any(kw in model_lower for kw in _NON_VISION_KEYWORDS):
            return False

        _VISION_KEYWORDS = (
            # ── OpenAI GPT 系列 ──────────────────────────────────────
            # GPT-4 视觉系列
            "gpt-4o", "gpt-4-turbo", "gpt-4-vision", "gpt-4.1",
            # GPT-5 全系（gpt-5 / gpt-5.1 / gpt-5.2 均支持视觉）
            "gpt-5",
            # OpenAI 图像模型
            "gpt-image-1",
            # ── OpenAI o 推理系列（o1 起均支持图像输入）────────────
            # o1 / o1-pro / o3 / o3-pro / o4-mini / o4
            "o1", "o3", "o4",
            # ── xAI Grok 视觉系列 ────────────────────────────────────
            # grok-2-vision-1212（已弃用但仍在用）/ grok-4（原生多模态）
            "grok-2-vision", "grok-4",
            # ── Anthropic Claude ────────────────────────────────────
            # 3.x 格式：claude-opus-3-... / claude-sonnet-3-... / claude-haiku-3-...
            "claude-opus-", "claude-sonnet-", "claude-haiku-",
            # 4.x+ 格式：claude-opus-4-6 / claude-sonnet-4-6 / claude-haiku-4-5
            "claude-opus-4", "claude-sonnet-4", "claude-haiku-4",
            # ── Google Gemini ────────────────────────────────────────
            # 覆盖 1.5 / 2.0 / 2.5 / 3.x / 3.1 全系（全部支持视觉）
            "gemini",
            # ── Amazon Nova ─────────────────────────────────────────
            # Nova Lite / Pro / Premier 支持图片+视频输入
            # Bedrock 格式：amazon.nova-lite-v1:0 / amazon.nova-pro-v1:0
            # Nova 2 系列：us.amazon.nova-2-lite-v1:0
            "amazon.nova", "nova-lite", "nova-pro", "nova-premier",
            # ── 通用视觉后缀 ─────────────────────────────────────────
            "-vl", "-vision", "-multimodal",
            # ── Qwen VL 系列（阿里云）────────────────────────────────
            "qwen-vl", "qwen2-vl", "qwen2.5-vl", "qwen3-vl", "qwen3.5-vl",
            # Qwen-Omni / Qwen3-Omni：全模态（文本+图像+音频+视频）
            "qwen-omni", "qwen2.5-omni", "qwen3-omni",
            # ── DeepSeek VL 系列 ─────────────────────────────────────
            "deepseek-vl",
            # Janus-Pro：DeepSeek 开源多模态（第三方 API 部署）
            "janus-pro",
            # ── Meta Llama Vision 系列 ───────────────────────────────
            # Llama 3.2：Llama-3.2-11B-Vision / Llama-3.2-90B-Vision
            "llama-3.2-", "llama3.2-vision",
            # Llama 4：Llama-4-Scout / Llama-4-Maverick（原生多模态）
            "llama-4-", "llama4-",
            # ── Mistral 视觉系列 ─────────────────────────────────────
            # Pixtral 12B / Pixtral Large
            "pixtral",
            # Ministral 3B / 8B / 14B（支持视觉）
            "ministral-3b", "ministral-8b", "ministral-14b",
            # Mistral Small 3.1+ / Mistral Medium 3+ / Mistral Large 3+（含视觉编码器）
            "mistral-small-3", "mistral-medium-3", "mistral-large-3",
            # ── Microsoft Phi 多模态系列 ─────────────────────────────
            # phi-3-vision / phi-3.5-vision / phi-4-multimodal
            "phi-3-vision", "phi-3.5-vision", "phi-4-multimodal",
            # ── 智谱 GLM 视觉系列（Z.ai）────────────────────────────
            # GLM-4V / GLM-4.1V / GLM-4.5V / GLM-4.6V
            "glm-4v", "glm-4.1v", "glm-4.5v", "glm-4.6v",
            # ── 开源视觉模型 ─────────────────────────────────────────
            "internvl",          # InternVL / InternVL2 / InternVL2.5 / InternVL3
            "minicpm-v",         # MiniCPM-V 系列
            "minicpm-o",         # MiniCPM-o 系列（全模态）
            # ── 百度 ERNIE VL 系列 ───────────────────────────────────
            "ernie-4.5-vl", "ernie-vl",
            # ── Cohere Command A Vision / Aya Vision ────────────────
            "command-a-vision",
            "aya-vision",        # Cohere Aya Vision 8B / 32B（多语言视觉模型）
            # ── Moonshot Kimi VL ─────────────────────────────────────
            # moonshot-v1-vision-preview / kimi-vl
            "moonshot-v1-vision", "kimi-vl",
            # ── 零一万物 Yi-VL ───────────────────────────────────────
            "yi-vl",
            # ── 字节跳动 Doubao / Seed VL ────────────────────────────
            # doubao-1.5-vision-pro / doubao-1.5-vision-pro-32k / doubao-1.6-vision
            "doubao-1.5-vision", "doubao-1.6-vision", "doubao-vision", "seed1.5-vl", "seed-vl",
            # ── 腾讯混元 Hunyuan Vision ──────────────────────────────
            # hunyuan-vision / hunyuan-vision-1.5
            "hunyuan-vision",
            # ── MiniMax VL 系列 ──────────────────────────────────────
            # MiniMax-VL-01（视觉语言模型）
            "minimax-vl",
            # ── Stepfun Step 视觉系列 ────────────────────────────────
            # step-1v / step-1.5v / step-3（多模态推理）
            "step-1v", "step-1.5v", "step-3",
            # step-r1-v-mini / step-1o-vision-* / step-1o-turbo-vision
            "step-r1-v-mini", "step-1o-vision", "step-1o-turbo-vision",
            # ── LLaVA 系列（开源经典）────────────────────────────────
            # llava / llava-1.5 / llava-1.6 / llava-onevision / llava-next
            "llava",
        )
        return any(kw in model_lower for kw in _VISION_KEYWORDS)

    # ── Property 代理：所有循环/会话级状态委托给 self._state ──────

    @property
    def _session_turn(self) -> int:
        return self._state.session_turn

    @_session_turn.setter
    def _session_turn(self, value: int) -> None:
        self._state.session_turn = value

    @property
    def _last_iteration_count(self) -> int:
        return self._state.last_iteration_count

    @_last_iteration_count.setter
    def _last_iteration_count(self, value: int) -> None:
        self._state.last_iteration_count = value

    @property
    def _last_tool_call_count(self) -> int:
        return self._state.last_tool_call_count

    @_last_tool_call_count.setter
    def _last_tool_call_count(self, value: int) -> None:
        self._state.last_tool_call_count = value

    @property
    def _last_success_count(self) -> int:
        return self._state.last_success_count

    @_last_success_count.setter
    def _last_success_count(self, value: int) -> None:
        self._state.last_success_count = value

    @property
    def _last_failure_count(self) -> int:
        return self._state.last_failure_count

    @_last_failure_count.setter
    def _last_failure_count(self, value: int) -> None:
        self._state.last_failure_count = value

    @property
    def _current_write_hint(self) -> str:
        return self._state.current_write_hint

    @_current_write_hint.setter
    def _current_write_hint(self, value: str) -> None:
        self._state.current_write_hint = value

    @property
    def _has_write_tool_call(self) -> bool:
        return self._state.has_write_tool_call

    @_has_write_tool_call.setter
    def _has_write_tool_call(self, value: bool) -> None:
        self._state.has_write_tool_call = value

    @property
    def _turn_diagnostics(self) -> list:
        return self._state.turn_diagnostics

    @_turn_diagnostics.setter
    def _turn_diagnostics(self, value: list) -> None:
        self._state.turn_diagnostics = value

    @property
    def _session_diagnostics(self) -> list:
        return self._state.session_diagnostics

    @_session_diagnostics.setter
    def _session_diagnostics(self, value: list) -> None:
        self._state.session_diagnostics = value

    @property
    def _execution_guard_fired(self) -> bool:
        return self._state.execution_guard_fired

    @_execution_guard_fired.setter
    def _execution_guard_fired(self, value: bool) -> None:
        self._state.execution_guard_fired = value

    @property
    def _vba_exempt(self) -> bool:
        return self._state.vba_exempt

    @_vba_exempt.setter
    def _vba_exempt(self, value: bool) -> None:
        self._state.vba_exempt = value

    @property
    def _finish_task_warned(self) -> bool:
        return self._state.finish_task_warned

    @_finish_task_warned.setter
    def _finish_task_warned(self, value: bool) -> None:
        self._state.finish_task_warned = value

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
                # 增量建立向量索引
                if self._semantic_memory is not None:
                    try:
                        await self._semantic_memory.index_entries(entries)
                    except Exception:
                        logger.debug("增量向量索引失败", exc_info=True)
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
        self.sync_mcp_auto_approve()

    def sync_mcp_auto_approve(self) -> None:
        """将当前 MCP 白名单同步到审批管理器。"""
        auto_approved = self._mcp_manager.auto_approved_tools
        if auto_approved:
            self._approval.register_mcp_auto_approve(auto_approved)

    async def shutdown_mcp(self) -> None:
        """关闭所有 MCP Server 连接，释放资源。"""
        if self._active_skills:
            _primary = self._active_skills[-1]
            self._run_skill_hook(
                skill=_primary,
                event=HookEvent.STOP,
                payload={"reason": "shutdown_mcp"},
            )
            self._run_skill_hook(
                skill=_primary,
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
        if self._own_mcp_manager:
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
    def session_diagnostics(self) -> list[dict[str, Any]]:
        """会话级诊断累积数据，供 /save 导出。"""
        return self._session_diagnostics

    @property
    def prompt_injection_snapshots(self) -> list[dict[str, Any]]:
        """提示词注入完整快照，供 /save 导出。"""
        return self._state.prompt_injection_snapshots

    @property
    def full_access_enabled(self) -> bool:
        """当前会话是否启用 fullaccess。"""
        return self._full_access_enabled

    @property
    def subagent_enabled(self) -> bool:
        """当前会话是否启用 subagent。"""
        return self._subagent_enabled

    @property
    def plan_mode_enabled(self) -> bool:
        """当前会话是否启用 plan mode。"""
        return self._plan_mode_enabled

    @property
    def backup_enabled(self) -> bool:
        """当前会话是否启用备份沙盒模式。"""
        return self._backup_enabled

    @property
    def backup_manager(self) -> BackupManager | None:
        return self._backup_manager

    def enable_bench_sandbox(self) -> None:
        """启用 benchmark 沙盒模式：解除所有交互式阻塞。

        - fullaccess = True：高风险工具直接执行，不弹确认
        - plan 拦截关闭：task_create 直接执行，不生成待审批计划
        - plan mode 关闭：普通对话不进入仅规划路径
        - subagent 启用：允许委派子代理
        - bench 模式标志：用于 activate_skill 短路非 Excel 类 skill
        """
        self._full_access_enabled = True
        self._plan_intercept_task_create = False
        self._plan_mode_enabled = False
        self._subagent_enabled = True
        self._bench_mode = True


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

    def has_pending_approval(self) -> bool:
        """当前会话是否存在待确认的高风险操作。"""
        return self._approval.has_pending()

    def current_pending_approval(self) -> "PendingApproval | None":
        """返回当前待确认操作。"""
        return self._approval.pending

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
        mention_contexts: list[ResolvedMention] | None = None,
        images: list[dict[str, Any]] | None = None,
    ) -> ChatResult:
        """编排层：路由 → 消息管理 → 调用循环 → 返回结果。"""
        normalized_images: list[dict[str, str]] = []
        for item in images or []:
            if not isinstance(item, dict):
                continue
            data = str(item.get("data", "") or "").strip()
            if not data:
                continue
            media_type = str(item.get("media_type", "image/png") or "image/png").strip() or "image/png"
            detail_raw = str(item.get("detail", "auto") or "auto").strip().lower()
            detail = detail_raw if detail_raw in {"auto", "low", "high"} else "auto"
            normalized_images.append({
                "data": data,
                "media_type": media_type,
                "detail": detail,
            })

        def _add_user_turn_to_memory(text: str) -> None:
            if not normalized_images:
                self._memory.add_user_message(text)
                return

            parts: list[dict[str, Any]] = []
            if text:
                parts.append({"type": "text", "text": text})
            for image in normalized_images:
                parts.append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{image['media_type']};base64,{image['data']}",
                        "detail": image["detail"],
                    },
                })

            self._memory.add_user_message(parts if parts else text)

        if self._question_flow.has_pending():
            pending_chat_start = time.monotonic()
            pending_result = await self._handle_pending_question_answer(
                user_message=user_message,
                on_event=on_event,
            )
            if pending_result is not None:
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
        # 每次真正的 chat 调用递增轮次计数器
        self._state.increment_turn()
        # 新任务默认重置 write_hint；续跑路径会在 _tool_calling_loop 中恢复。
        self._current_write_hint = "unknown"

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
            manual_skill_with_args = self._resolve_skill_command_with_args(user_message)
            if manual_skill_with_args is not None:
                effective_slash_command, effective_raw_args = manual_skill_with_args

        # ── @skill:name 路由：当无斜杠命令时，检测 mention 中的 skill 引用 ──
        if effective_slash_command is None and mention_contexts:
            for rm in mention_contexts:
                if rm.mention.kind == "skill" and not rm.error:
                    effective_slash_command = rm.mention.value
                    # raw_args: 从 clean_text 中提取（即移除所有 @ 标记后的文本）
                    parse_result = MentionParser.parse(user_message)
                    effective_raw_args = parse_result.clean_text
                    break

        # ── 路由（斜杠命令 + write_hint 分类） ──
        route_result = await self._route_skills(
            user_message,
            slash_command=effective_slash_command,
            raw_args=effective_raw_args if effective_slash_command else None,
        )

        route_result, user_message = await self._adapt_guidance_only_slash_route(
            route_result=route_result,
            user_message=user_message,
            slash_command=effective_slash_command,
            raw_args=effective_raw_args,
        )

        # 合并已激活 skill 的 system_contexts
        final_skills_used = list(route_result.skills_used)
        final_system_contexts = list(route_result.system_contexts)
        if self._active_skills:
            for skill in self._active_skills:
                if skill.name not in final_skills_used:
                    final_skills_used.append(skill.name)
                skill_context = skill.render_context()
                if skill_context.strip() and skill_context not in final_system_contexts:
                    final_system_contexts.append(skill_context)

        route_result = SkillMatchResult(
            skills_used=final_skills_used,
            tool_scope=getattr(route_result, "tool_scope", []),
            route_mode=getattr(route_result, "route_mode", "all_tools"),
            system_contexts=final_system_contexts,
            parameterized=route_result.parameterized,
            write_hint=_merge_write_hint(
                getattr(route_result, "write_hint", None),
                self._current_write_hint,
            ),
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
            _add_user_turn_to_memory(user_message)
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

        if effective_slash_command and route_result.route_mode == "slash_not_found":
            # 区分"技能被权限限制"与"技能真的不存在"，给出精确反馈
            normalized_cmd = self._normalize_skill_command_name(effective_slash_command)
            blocked = self._blocked_skillpacks()
            if blocked and normalized_cmd in blocked:
                reply = (
                    f"技能 `{effective_slash_command}` 当前受访问限制，"
                    f"请先执行 `/fullaccess on` 解除限制后再试。"
                )
            else:
                reply = f"未找到技能 `{effective_slash_command}`，请通过 `/skills` 查看可用技能列表。"
            _add_user_turn_to_memory(user_message)
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
            user_prompt_hook_raw = self._run_skill_hook(
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
            user_prompt_hook = await self._resolve_hook_result(
                event=HookEvent.USER_PROMPT_SUBMIT,
                hook_result=user_prompt_hook_raw,
                on_event=on_event,
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
                _add_user_turn_to_memory(user_message)
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
            _add_user_turn_to_memory(user_message)
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
        _add_user_turn_to_memory(user_message)
        logger.info(
            "用户指令摘要: %s | route_mode=%s | skills=%s",
            _summarize_text(user_message),
            route_result.route_mode,
            route_result.skills_used,
        )

        self._set_window_perception_turn_hints(
            user_message=user_message,
            is_new_task=True,
        )
        # 仅新任务重置执行守卫；同任务续跑需保留状态，避免重复注入提示。
        self._execution_guard_fired = False
        self._vba_exempt = _user_requests_vba(user_message)
        # 存储 mention 上下文供 _tool_calling_loop 注入系统提示词
        self._mention_contexts = mention_contexts
        chat_result = await self._tool_calling_loop(route_result, on_event)

        # 注入路由诊断信息到 ChatResult
        chat_result.write_hint = self._current_write_hint
        chat_result.route_mode = route_result.route_mode
        chat_result.skills_used = list(route_result.skills_used)
        chat_result.task_tags = route_result.task_tags
        chat_result.turn_diagnostics = list(self._turn_diagnostics)

        # 累积到会话级诊断
        # 获取本轮提示词注入摘要
        _injection_summary_for_diag: list[dict[str, Any]] = []
        if self._state.prompt_injection_snapshots:
            _latest = self._state.prompt_injection_snapshots[-1]
            if _latest.get("session_turn") == self._session_turn:
                _injection_summary_for_diag = _latest.get("summary", [])
        self._session_diagnostics.append({
            "session_turn": self._session_turn,
            "write_hint": self._current_write_hint,
            "route_mode": route_result.route_mode,
            "skills_used": list(route_result.skills_used),
            "task_tags": list(route_result.task_tags),
            "iterations": chat_result.iterations,
            "prompt_tokens": chat_result.prompt_tokens,
            "completion_tokens": chat_result.completion_tokens,
            "total_tokens": chat_result.total_tokens,
            "write_guard_triggered": chat_result.write_guard_triggered,
            "turn_diagnostics": [d.to_dict() for d in self._turn_diagnostics],
            "prompt_injection_summary": _injection_summary_for_diag,
        })

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

    @staticmethod
    def _iter_slash_command_lines(user_message: str) -> list[str]:
        """提取消息中所有可能的斜杠命令片段（支持命令出现在句中）。"""
        text = user_message.strip()
        if not text:
            return []
        command_lines: list[str] = []
        for idx, char in enumerate(text):
            if char != "/":
                continue
            if idx > 0 and not text[idx - 1].isspace():
                continue
            command_line = text[idx + 1 :].strip()
            if command_line:
                command_lines.append(command_line)
        return command_lines

    def _resolve_skill_from_command_line(
        self,
        command_line: str,
        *,
        skill_names: Sequence[str],
    ) -> tuple[str, str] | None:
        """解析单个命令片段，返回 (skill_name, raw_args)。"""
        lower_to_name = {name.lower(): name for name in skill_names}
        command_line_lower = command_line.lower()

        # 1) 精确匹配（含命名空间）
        exact = lower_to_name.get(command_line_lower)
        if exact is not None:
            return exact, ""

        # 2) 前缀匹配（/skill_name 后跟参数）
        for candidate in sorted(skill_names, key=len, reverse=True):
            lower_candidate = candidate.lower()
            if command_line_lower == lower_candidate:
                return candidate, ""
            if command_line_lower.startswith(lower_candidate + " "):
                raw_args = command_line[len(candidate) :].strip()
                return candidate, raw_args

        command_token, _, raw_tail = command_line.partition(" ")

        # 先尝试已注册技能匹配，之后再按路径输入兜底排除，避免误伤命名空间技能。
        if "/" in command_token and "." in command_token:
            return None

        # 3) 无分隔符归一兜底匹配（兼容旧命令）
        normalized_cmd = self._normalize_skill_command_name(command_token)
        normalized_matches = [
            name
            for name in skill_names
            if self._normalize_skill_command_name(name) == normalized_cmd
        ]
        if len(normalized_matches) == 1:
            return normalized_matches[0], raw_tail.strip()
        return None

    def _resolve_skill_command_with_args(self, user_message: str) -> tuple[str, str] | None:
        """解析消息中的手动 Skill 命令并返回 (skill_name, raw_args)。"""
        skill_names = self._list_manual_invocable_skill_names()
        if not skill_names:
            return None
        for command_line in self._iter_slash_command_lines(user_message):
            resolved = self._resolve_skill_from_command_line(
                command_line,
                skill_names=skill_names,
            )
            if resolved is not None:
                return resolved
        return None

    def _list_loaded_skill_names(self) -> list[str]:
        """获取当前可匹配的 Skill 名称；为空时尝试主动加载。"""
        if self._skill_router is None:
            return []
        skillpacks = self._skill_router._loader.get_skillpacks()
        if not skillpacks:
            skillpacks = self._skill_router._loader.load_all()
        return list(skillpacks.keys())

    def _get_loaded_skillpacks(self) -> dict | None:
        """获取运行时已加载的 Skillpack 对象字典，供预路由 catalog 构建使用。"""
        if self._skill_router is None:
            return None
        skillpacks = self._skill_router._loader.get_skillpacks()
        if not skillpacks:
            skillpacks = self._skill_router._loader.load_all()
        return skillpacks or None

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
        """将消息中的 `/skill_name ...` 解析为 Skill 名称（用于手动调用）。"""
        resolved = self._resolve_skill_command_with_args(user_message)
        if resolved is None:
            return None
        return resolved[0]

    @staticmethod
    def _normalize_skill_name(name: str) -> str:
        """归一化技能名：小写、去除连字符和下划线，与 router 保持一致。"""
        return name.strip().lower().replace("-", "").replace("_", "")

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
        if self._active_skills:
            return self._active_skills[-1]
        if route_result is None or not route_result.skills_used:
            return None
        return self._get_loaded_skill(route_result.skills_used[0])

    @property
    def _primary_skill(self) -> Skillpack | None:
        """当前主 skill（列表末尾），无激活时返回 None。"""
        return self._active_skills[-1] if self._active_skills else None

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

    @staticmethod
    def _merge_hook_reasons(current: str, extra: str) -> str:
        parts = [part.strip() for part in (current, extra) if str(part).strip()]
        return " | ".join(parts)

    def _normalize_hook_decision_scope(
        self,
        *,
        event: HookEvent,
        hook_result: HookResult,
    ) -> HookResult:
        if hook_result.decision != HookDecision.ASK or event == HookEvent.PRE_TOOL_USE:
            return hook_result
        reason = self._merge_hook_reasons(
            hook_result.reason,
            f"事件 {event.value} 不支持 ASK，已降级为 CONTINUE",
        )
        logger.warning("Hook ASK 降级：event=%s reason=%s", event.value, reason)
        return HookResult(
            decision=HookDecision.CONTINUE,
            reason=reason,
            updated_input=hook_result.updated_input,
            additional_context=hook_result.additional_context,
            agent_action=hook_result.agent_action,
            raw_output=dict(hook_result.raw_output),
        )

    def _apply_hook_agent_failure(
        self,
        *,
        hook_result: HookResult,
        action: HookAgentAction,
        message: str,
    ) -> HookResult:
        decision = hook_result.decision
        if action.on_failure == "deny":
            decision = HookDecision.DENY
        reason = self._merge_hook_reasons(hook_result.reason, message)
        return HookResult(
            decision=decision,
            reason=reason,
            updated_input=hook_result.updated_input,
            additional_context=hook_result.additional_context,
            agent_action=hook_result.agent_action,
            raw_output=dict(hook_result.raw_output),
        )

    async def _apply_hook_agent_action(
        self,
        *,
        event: HookEvent,
        hook_result: HookResult,
        on_event: EventCallback | None,
    ) -> HookResult:
        action = hook_result.agent_action
        if action is None:
            return hook_result

        task_text = action.task.strip()
        if not task_text:
            return hook_result

        if self._hook_agent_action_depth > 0:
            message = "agent hook 递归触发已被跳过"
            logger.warning("Hook agent action 递归保护触发：event=%s", event.value)
            return self._apply_hook_agent_failure(
                hook_result=hook_result,
                action=action,
                message=message,
            )

        picked_agent = self._normalize_skill_agent_name(action.agent_name)
        if not picked_agent:
            picked_agent = await self._auto_select_subagent(
                task=task_text,
                file_paths=[],
            )
        picked_agent = self._normalize_skill_agent_name(picked_agent) or "subagent"

        logger.info(
            "执行 hook agent action：event=%s agent=%s",
            event.value,
            picked_agent,
        )
        self._hook_agent_action_depth += 1
        try:
            sub_result = await self.run_subagent(
                agent_name=picked_agent,
                prompt=task_text,
                on_event=on_event,
            )
        except Exception as exc:  # noqa: BLE001
            message = f"agent hook 执行异常（{picked_agent}）：{exc}"
            logger.warning(message)
            return self._apply_hook_agent_failure(
                hook_result=hook_result,
                action=action,
                message=message,
            )
        finally:
            self._hook_agent_action_depth -= 1

        if not sub_result.success:
            message = f"agent hook 执行失败（{picked_agent}）：{sub_result.summary}"
            logger.warning(message)
            return self._apply_hook_agent_failure(
                hook_result=hook_result,
                action=action,
                message=message,
            )

        summary = (sub_result.summary or "").strip()
        additional_context = hook_result.additional_context
        if action.inject_summary_as_context and summary:
            injected = f"[Hook Agent:{picked_agent}] {summary}"
            additional_context = (
                f"{additional_context}\n{injected}"
                if additional_context
                else injected
            )
        return HookResult(
            decision=hook_result.decision,
            reason=hook_result.reason,
            updated_input=hook_result.updated_input,
            additional_context=additional_context,
            agent_action=hook_result.agent_action,
            raw_output=dict(hook_result.raw_output),
        )

    async def _resolve_hook_result(
        self,
        *,
        event: HookEvent,
        hook_result: HookResult | None,
        on_event: EventCallback | None,
    ) -> HookResult | None:
        if hook_result is None:
            return None
        normalized = self._normalize_hook_decision_scope(
            event=event,
            hook_result=hook_result,
        )
        resolved = await self._apply_hook_agent_action(
            event=event,
            hook_result=normalized,
            on_event=on_event,
        )

        before = (normalized.additional_context or "").strip()
        after = (resolved.additional_context or "").strip()
        if after:
            if before and after.startswith(before):
                delta = after[len(before) :].strip()
                if delta:
                    self._push_hook_context(delta)
            elif after != before:
                self._push_hook_context(after)
        return resolved

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
            return self._normalize_hook_decision_scope(
                event=target_event,
                hook_result=hook_result,
            )

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
        """构建 LLM-Native 元工具定义。

        构建 activate_skill + finish_task + delegate_to_subagent + list_subagents + ask_user。
        """
        # ── 构建 skill catalog ──
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
            "当你需要执行复杂任务、不确定最佳方案时，激活对应技能获取指引。\n"
            "⚠️ 不要向用户提及技能名称或工具名称等内部概念。\n"
            "调用后立即执行任务，不要仅输出计划。\n\n"
            f"{skill_catalog}"
        )
        subagent_catalog, subagent_names = self._subagent_registry.build_catalog()
        delegate_description = (
            "把任务委派给独立上下文的 subagent 执行。\n"
            "⚠️ 仅适用于需要 20+ 次工具调用的大规模后台任务（如：多文件批量变换、"
            "复杂多步骤修改、长链条数据管线）。\n"
            "⚠️ 禁止将以下任务委派给 subagent：\n"
            "- 单文件读取、探查（直接用 inspect_excel_files 或 read_excel）\n"
            "- 简单写入/格式化（直接用 run_code 或对应写入工具）\n"
            "- 单步分析（直接用 filter_data 或 run_code）\n"
            "复杂任务建议使用 task_brief 结构化分派（含背景、目标、约束、交付物），"
            "简单任务直接用 task 字符串。\n"
            "注意：委派即执行，不要先描述你将要委派什么，直接调用。\n\n"
            "Subagent_Catalog:\n"
            f"{subagent_catalog or '当前无可用子代理。'}"
        )
        list_subagents_description = "列出当前可用的全部 subagent 及职责。"
        ask_user_description = (
            "向用户提问并获取回答。这是与用户进行结构化交互的唯一方式。"
            "当你需要用户做选择、确认意图或做决定时，必须调用本工具，"
            "不要在文本回复中列出编号选项让用户回复。"
            "典型场景：多个候选目标需确认、指令有多种解读、"
            "任务有多条可行路径（如大文件的输出方式）、不可逆操作需确认。"
            "不需要问的情况：只有一条合理路径时直接执行；用户意图已明确时默认行动。"
            "选项应具体（列出实际文件名/方案名），不要泛泛而问。"
            "调用后暂停执行，等待用户回答后继续。"
        )
        # 写入门禁：仅当 write_hint == "may_write" 时注入 finish_task
        finish_task_tool = None
        if _normalize_write_hint(getattr(self, "_current_write_hint", "unknown")) == "may_write":
            # bench 模式：精简汇报，减少最后一轮 token 消耗
            if getattr(self, "_bench_mode", False):
                finish_task_tool = {
                    "type": "function",
                    "function": {
                        "name": "finish_task",
                        "description": (
                            "任务完成声明。写入操作执行完毕后调用。"
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
                            "任务完成声明。写入/修改操作执行完毕后调用，或确认当前任务为纯分析/查询后调用。"
                            "优先使用 report 参数进行结构化汇报（详细讲解模式），"
                            "像向同事汇报工作一样清晰易懂地说明操作、发现和建议。"
                            "如任务仅涉及筛选/统计/分析/查找且无需写回文件，可直接调用并在 report 中说明。"
                        ),
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "report": {
                                    "type": "object",
                                    "description": "结构化任务汇报（推荐）。用详细讲解语言填写各字段。",
                                    "properties": {
                                        "operations": {
                                            "type": "string",
                                            "description": "执行了哪些操作（按步骤简述，每步说清做了什么）",
                                        },
                                        "key_findings": {
                                            "type": "string",
                                            "description": "关键发现和数据结果（具体数字、行数、匹配率等）",
                                        },
                                        "explanation": {
                                            "type": "string",
                                            "description": "为什么这样做、结果的含义解读",
                                        },
                                        "suggestions": {
                                            "type": "string",
                                            "description": "后续使用建议或注意事项",
                                        },
                                        "affected_files": {
                                            "type": "array",
                                            "items": {"type": "string"},
                                            "description": "涉及修改的文件路径列表",
                                        },
                                    },
                                    "required": ["operations", "key_findings"],
                                    "additionalProperties": False,
                                },
                                "summary": {
                                    "type": "string",
                                    "description": "（兼容旧格式）简要完成摘要。优先使用 report。",
                                },
                            },
                            "required": [],
                            "additionalProperties": False,
                        },
                    },
                }
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
                    "name": "delegate_to_subagent",
                    "description": delegate_description,
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "task": {
                                "type": "string",
                                "description": "简单任务描述（与 task_brief 二选一）",
                            },
                            "task_brief": {
                                "type": "object",
                                "description": "结构化任务描述，适用于复杂研究或多步骤修改任务（与 task 二选一）",
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
                                        "description": "研究或执行目标列表",
                                        "items": {"type": "string"},
                                    },
                                    "constraints": {
                                        "type": "array",
                                        "description": "约束条件（如：只修改哪些文件、不要改动哪些模块）",
                                        "items": {"type": "string"},
                                    },
                                    "deliverables": {
                                        "type": "array",
                                        "description": "期望的交付物列表",
                                        "items": {"type": "string"},
                                    },
                                },
                                "required": ["title"],
                                "additionalProperties": False,
                            },
                            "agent_name": {
                                "type": "string",
                                "description": "可选，指定子代理名称；不传则自动选择",
                                **({"enum": subagent_names} if subagent_names else {}),
                            },
                            "file_paths": {
                                "type": "array",
                                "description": "可选，相关文件路径列表",
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
                        "required": ["question"],
                        "additionalProperties": False,
                    },
                },
            },
        ]
        if finish_task_tool is not None:
            tools.append(finish_task_tool)
        return tools

    def _build_v5_tools(self, *, write_hint: str = "unknown") -> list[dict[str, Any]]:
        """构建工具 schema + 元工具。

        当 write_hint == "read_only" 时，仅暴露只读工具子集 + run_code + 元工具，
        减少约 40-60% 的工具 schema token 开销。
        """
        from excelmanus.tools.policy import READ_ONLY_SAFE_TOOLS, CODE_POLICY_DYNAMIC_TOOLS

        domain_schemas = self._registry.get_tiered_schemas(
            mode="chat_completions",
        )
        meta_schemas = self._build_meta_tools()
        # 去除与 domain 重复的元工具（元工具优先）
        meta_names = {s.get("function", {}).get("name") for s in meta_schemas}
        filtered_domain = [s for s in domain_schemas if s.get("function", {}).get("name") not in meta_names]

        # 窄路由：read_only 任务只暴露读工具 + run_code（用于复杂分析）
        if write_hint == "read_only":
            _allowed = READ_ONLY_SAFE_TOOLS | CODE_POLICY_DYNAMIC_TOOLS | _ALWAYS_AVAILABLE_TOOLS_SET
            filtered_domain = [
                s for s in filtered_domain
                if s.get("function", {}).get("name", "") in _allowed
            ]

        return meta_schemas + filtered_domain

    @staticmethod
    def _is_activate_skill_ok(result: str) -> bool:
        """判断 _handle_activate_skill 返回值是否表示成功。

        成功时返回值以 "OK" 开头；失败情形包括：
        - "未找到技能: ..." — 技能不存在
        - "⚠️ ..." — 权限拒绝或 MCP 依赖未满足
        任何非 "OK" 开头的返回均视为失败。
        """
        return result.startswith("OK")

    async def _handle_activate_skill(self, skill_name: str, reason: str = "") -> str:
        """处理 activate_skill 调用：激活技能并返回技能上下文。"""
        if self._skill_router is None:
            return f"未找到技能: {skill_name}"

        loader = self._skill_router._loader
        skillpacks = loader.get_skillpacks()
        if not skillpacks:
            skillpacks = loader.load_all()

        # 检查是否尝试激活被限制的技能
        # 注意：必须对输入名称做归一化后再比较，防止通过大小写/连字符变体绕过限制
        # 例如 "Excel-Code-Runner" 归一化后与 "excel_code_runner" 相同
        blocked = self._blocked_skillpacks()
        if blocked:
            normalized_input = self._normalize_skill_name(skill_name)
            normalized_blocked = {self._normalize_skill_name(b) for b in blocked}
            if normalized_input in normalized_blocked:
                # 从全量技能包中获取描述（尝试精确名和归一化名）
                desc = ""
                skill_obj = skillpacks.get(skill_name)
                if skill_obj is None:
                    # 尝试通过归一化名找到实际技能对象
                    skill_obj = next(
                        (s for k, s in skillpacks.items() if self._normalize_skill_name(k) == normalized_input),
                        None,
                    )
                if skill_obj is not None:
                    desc = f"\n该技能用于：{skill_obj.description}"
                return (
                    f"⚠️ 技能 '{skill_name}' 需要 fullaccess 权限才能使用。{desc}\n"
                    f"请告知用户使用 /fullaccess on 命令开启完全访问权限后重试。"
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

        self._active_skills = [
            s for s in self._active_skills if s.name != selected.name
        ] + [selected]
        self._loaded_skill_names[selected.name] = self._session_turn

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
        """选择子代理。v5.2: 不再调用 LLM，直接返回默认 subagent。"""
        _, candidates = self._subagent_registry.build_catalog()
        if not candidates:
            return "subagent"
        # 如果只有一个候选，直接返回
        if len(candidates) == 1:
            return candidates[0]
        # 多个候选时（用户自定义场景），返回第一个
        return candidates[0]

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
        # 运行时模型选择：子代理自身 > 全局 aux_model > 当前激活主模型
        # 这样在未配置“子路由模型”时，可随着 /model 切换正确回退到主模型。
        resolved_model = config.model or self._config.aux_model or self._active_model
        runtime_config = config if config.model == resolved_model else replace(config, model=resolved_model)
        parent_context_parts: list[str] = []
        parent_summary = self._build_parent_context_summary()
        if parent_summary:
            parent_context_parts.append(parent_summary)
        window_context = self._build_window_perception_notice()
        if window_context:
            parent_context_parts.append(window_context)
        # full 模式：构建主代理级别的丰富上下文
        enriched_contexts: list[str] | None = None
        if runtime_config.capability_mode == "full":
            enriched_contexts = self._build_full_mode_contexts()

        return await self._subagent_executor.run(
            config=runtime_config,
            prompt=prompt,
            parent_context="\n\n".join(parent_context_parts),
            on_event=on_event,
            full_access_enabled=self._full_access_enabled,
            tool_result_enricher=self._enrich_subagent_tool_result_with_window_perception,
            enriched_contexts=enriched_contexts,
        )

    def _build_full_mode_contexts(self) -> list[str]:
        """为 full 模式子代理构建主代理级别的丰富上下文。"""
        contexts: list[str] = []

        # 1. MCP 扩展能力概要
        mcp_notice = self._build_mcp_context_notice()
        if mcp_notice:
            contexts.append(mcp_notice)

        # 2. 工具分类索引
        tool_index = self._build_tool_index_notice()
        if tool_index:
            contexts.append(tool_index)

        # 3. 权限状态说明
        access_notice = self._build_access_notice()
        if access_notice:
            contexts.append(access_notice)

        # 4. 备份模式说明
        backup_notice = self._build_backup_notice()
        if backup_notice:
            contexts.append(backup_notice)

        return contexts

    @staticmethod
    def _render_task_brief(brief: dict[str, Any]) -> str:
        """将结构化 task_brief 渲染为 Markdown 格式的任务指令。"""
        title = str(brief.get("title", "")).strip()
        parts: list[str] = [f"## 任务：{title}"]

        background = str(brief.get("background", "")).strip()
        if background:
            parts.append(f"### 背景\n{background}")

        objectives = brief.get("objectives")
        if isinstance(objectives, list) and objectives:
            items = "\n".join(
                f"{i + 1}. {str(obj).strip()}"
                for i, obj in enumerate(objectives)
                if str(obj).strip()
            )
            if items:
                parts.append(f"### 目标\n{items}")

        constraints = brief.get("constraints")
        if isinstance(constraints, list) and constraints:
            items = "\n".join(
                f"- {str(c).strip()}"
                for c in constraints
                if str(c).strip()
            )
            if items:
                parts.append(f"### 约束\n{items}")

        deliverables = brief.get("deliverables")
        if isinstance(deliverables, list) and deliverables:
            items = "\n".join(
                f"- {str(d).strip()}"
                for d in deliverables
                if str(d).strip()
            )
            if items:
                parts.append(f"### 交付物\n{items}")

        return "\n\n".join(parts)

    async def _delegate_to_subagent(
        self,
        *,
        task: str,
        agent_name: str | None = None,
        file_paths: list[Any] | None = None,
        on_event: EventCallback | None = None,
    ) -> DelegateSubagentOutcome:
        """执行 delegate_to_subagent 并返回结构化结果。

        委托给 SubagentOrchestrator 组件。
        """
        return await self._subagent_orchestrator.delegate(
            task=task,
            agent_name=agent_name,
            file_paths=file_paths,
            on_event=on_event,
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
            agent_name="subagent",
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
            "请选择「批准执行」或「拒绝计划」。"
        )

    def _enqueue_plan_approval_question(
        self,
        *,
        draft: PlanDraft,
        on_event: EventCallback | None,
        iteration: int = 0,
    ) -> PendingQuestion:
        """创建 plan 审批系统问题并入队。"""
        # 使用虚拟 tool_call_id（plan mode 无真实工具调用）
        virtual_tool_call_id = f"plan_approval_{draft.plan_id}"
        question_payload = {
            "header": "计划审批",
            "text": (
                f"已生成计划草案「{draft.title}」"
                f"（ID: {draft.plan_id}，子任务: {len(draft.subtasks)}）。"
                "请选择是否批准执行。"
            ),
            "options": [
                {
                    "label": _PLAN_APPROVAL_OPTION_APPROVE,
                    "description": "批准计划并开始执行任务。",
                },
                {
                    "label": _PLAN_APPROVAL_OPTION_REJECT,
                    "description": "拒绝该计划，不执行。",
                },
            ],
            "multiSelect": False,
        }
        pending = self._question_flow.enqueue(
            question_payload=question_payload,
            tool_call_id=virtual_tool_call_id,
        )
        self._system_question_actions[pending.question_id] = {
            "type": _SYSTEM_Q_PLAN_APPROVAL,
            "plan_id": draft.plan_id,
        }
        self._emit_user_question_event(
            question=pending,
            on_event=on_event,
            iteration=iteration,
        )
        return pending

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
        self._enqueue_plan_approval_question(
            draft=draft,
            on_event=on_event,
        )
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
        self._enqueue_plan_approval_question(
            draft=draft,
            on_event=on_event,
        )
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

    def _emit_pending_approval_event(
        self,
        *,
        pending: "PendingApproval",
        on_event: EventCallback | None,
        iteration: int,
    ) -> None:
        """发射待确认审批事件，供 CLI 渲染审批卡片。"""
        self._emit(
            on_event,
            ToolCallEvent(
                event_type=EventType.PENDING_APPROVAL,
                approval_id=pending.approval_id,
                approval_tool_name=pending.tool_name,
                approval_arguments=dict(pending.arguments),
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
                    "description": "先开启 fullaccess，再重试子代理任务。",
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
            accept_reply = await self._handle_accept_command(
                ["/accept", approval_id],
                on_event=on_event,
            )
            reply = (
                f"{accept_reply}\n"
                "若需要子代理自动继续执行，建议选择「开启 fullaccess 后重试（推荐）」。"
            )
            return ChatResult(reply=reply)

        if selected_label == _SUBAGENT_APPROVAL_OPTION_FULLACCESS_RETRY:
            lines: list[str] = []
            if not self._full_access_enabled:
                self._full_access_enabled = True
                lines.append("已开启 fullaccess。当前代码技能权限：full_access。")
            else:
                lines.append("fullaccess 已开启。")

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
                "如需自动执行高风险步骤，可先使用 `/fullaccess on` 后重新发起任务。"
            )
            return ChatResult(reply=reply)

        manual = (
            "已记录你的回答。\n"
            f"当前审批 ID: `{approval_id}`\n"
            "你可以手动执行以下命令：\n"
            f"- `/accept {approval_id}`\n"
            "- `/fullaccess on`（可选）\n"
            f"- `/reject {approval_id}`"
        )
        return ChatResult(reply=manual)

    async def _handle_plan_approval_answer(
        self,
        *,
        action: dict[str, Any],
        parsed: Any,
        on_event: EventCallback | None,
    ) -> ChatResult:
        """处理 plan 审批系统问题的回答。"""
        selected_options = (
            parsed.selected_options if hasattr(parsed, "selected_options") else []
        )
        selected_label = (
            str(selected_options[0].get("label", "")).strip()
            if selected_options
            else ""
        )
        plan_id = str(action.get("plan_id", "")).strip()

        pending = self._pending_plan
        if pending is None:
            return ChatResult(reply="当前没有待审批计划。")

        expected_id = pending.draft.plan_id
        if plan_id and plan_id != expected_id:
            return ChatResult(
                reply=f"计划 ID 不匹配。当前待审批计划 ID 为 `{expected_id}`。"
            )

        if selected_label == _PLAN_APPROVAL_OPTION_APPROVE:
            reply = await self._handle_plan_approve(
                parts=["/plan", "approve"],
                on_event=on_event,
            )
            return ChatResult(reply=reply)

        if selected_label == _PLAN_APPROVAL_OPTION_REJECT:
            reply = self._handle_plan_reject(parts=["/plan", "reject"])
            return ChatResult(reply=reply)

        # 用户选了"其他"或无法识别的选项
        return ChatResult(
            reply=(
                "已记录你的回答。你可以手动执行以下命令：\n"
                f"- `/plan approve {expected_id}` 批准并继续执行\n"
                f"- `/plan reject {expected_id}` 拒绝该计划"
            )
        )

    async def _handle_pending_question_answer(
        self,
        *,
        user_message: str,
        on_event: EventCallback | None,
    ) -> ChatResult | None:
        text = user_message.strip()
        current = self._question_flow.current()
        if current is None:
            self._pending_question_route_result = None
            return ChatResult(reply="当前没有待回答问题。")

        if text.startswith("/"):
            # 允许审批/权限相关命令在问题待回答时穿透执行
            _lower = text.lower().replace("_", "")
            _passthrough = ("/fullaccess", "/accept", "/reject")
            if any(_lower.startswith(p) for p in _passthrough):
                # 返回 None 表示本方法不处理，由 chat() 继续走控制命令路径
                return None
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

        system_action = self._system_question_actions.pop(parsed.question_id, None)
        action_type = str(system_action.get("type", "")).strip() if system_action else ""

        # plan 审批是系统级问题，不对应真实 tool_call，避免写入孤立 tool result。
        if action_type != _SYSTEM_Q_PLAN_APPROVAL:
            tool_result = json.dumps(parsed.to_tool_result(), ensure_ascii=False)
            self._memory.add_tool_result(popped.tool_call_id, tool_result)

        logger.info("已接收问题回答: %s", parsed.question_id)
        if system_action is not None:
            self._pending_question_route_result = None
            if action_type == _SYSTEM_Q_SUBAGENT_APPROVAL:
                action_result = await self._handle_subagent_approval_answer(
                    action=system_action,
                    parsed=parsed,
                    on_event=on_event,
                )
            elif action_type == _SYSTEM_Q_PLAN_APPROVAL:
                action_result = await self._handle_plan_approval_answer(
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
        self._set_window_perception_turn_hints(
            user_message=user_message,
            is_new_task=False,
        )
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
        current_route_result = route_result
        # 恢复执行时保留之前的统计，仅首次调用时重置
        if start_iteration <= 1:
            self._state.reset_loop_stats()
        consecutive_text_only: int = 0
        write_hint = _merge_write_hint(
            getattr(current_route_result, "write_hint", None),
            self._current_write_hint,
        )
        # 设置实例属性供 _build_meta_tools 读取
        self._current_write_hint = write_hint
        # token 使用累计
        total_prompt_tokens = 0
        total_completion_tokens = 0
        # 诊断收集
        self._turn_diagnostics = []

        for iteration in range(start_iteration, max_iter + 1):
            self._emit(
                on_event,
                ToolCallEvent(
                    event_type=EventType.ITERATION_START,
                    iteration=iteration,
                ),
            )

            system_prompts, context_error = self._prepare_system_prompts_for_request(
                current_route_result.system_contexts,
                route_result=current_route_result,
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

            # 注入 mention 上下文 XML 块到系统提示词
            mention_block = build_mention_context_block(
                getattr(self, "_mention_contexts", None) or [],
            )
            if mention_block:
                system_prompts.append(mention_block)

            # 上下文自动压缩（Compaction）：超阈值时后台静默压缩早期对话，
            # 使用增强的 ExcelManus 场景化摘要提示词，避免硬截断导致重要上下文丢失。
            if iteration > 1:
                _sys_msgs = self._memory._build_system_messages(system_prompts)
                if self._compaction_manager.should_compact(self._memory, _sys_msgs):
                    _summary_model = self._config.aux_model or self._active_model
                    try:
                        await self._compaction_manager.auto_compact(
                            memory=self._memory,
                            system_msgs=_sys_msgs,
                            client=self._client,
                            summary_model=_summary_model,
                        )
                    except Exception as _compact_exc:
                        logger.debug("自动 Compaction 异常，跳过: %s", _compact_exc)
                # 向后兼容：旧版 summarization 作为次级兜底
                elif (
                    self._config.summarization_enabled
                    and self._config.aux_model
                ):
                    _cur_tokens = self._memory._total_tokens_with_system_messages(_sys_msgs)
                    _threshold_ratio = self._config.summarization_threshold_ratio
                    if _cur_tokens > self._config.max_context_tokens * _threshold_ratio:
                        try:
                            await self._memory.summarize_and_trim(
                                threshold=int(self._config.max_context_tokens * (_threshold_ratio - 0.1)),
                                system_msgs=_sys_msgs,
                                client=self._client,
                                summary_model=self._config.aux_model,
                                keep_recent_turns=self._config.summarization_keep_recent_turns,
                            )
                        except Exception as _sum_exc:
                            logger.debug("对话摘要异常，跳过: %s", _sum_exc)

            messages = self._memory.trim_for_request(
                system_prompts=system_prompts,
                max_context_tokens=self._config.max_context_tokens,
            )

            # 分层 schema（core=完整, extended=摘要/已展开=完整）
            tools = self._build_v5_tools(write_hint=write_hint)
            tool_scope = None

            kwargs: dict[str, Any] = {
                "model": self._active_model,
                "messages": messages,
            }
            if tools:
                kwargs["tools"] = tools

            # Prompt Cache 优化：同一 session_turn 内共享 cache key，
            # 确保 OpenAI 路由到同一缓存机器，最大化系统提示前缀 cache hit。
            if self._config.prompt_cache_key_enabled:
                kwargs["prompt_cache_key"] = f"em_s{self._session_turn}"

            # 尝试流式调用
            stream_kwargs = dict(kwargs)
            stream_kwargs["stream"] = True
            if isinstance(self._client, openai.AsyncOpenAI):
                stream_kwargs["stream_options"] = {"include_usage": True}

            try:
                stream_or_response = await self._create_chat_completion_with_system_fallback(stream_kwargs)
                # 检查返回值是否为异步迭代器（支持流式）
                if hasattr(stream_or_response, "__aiter__"):
                    message, usage = await self._consume_stream(
                        stream_or_response, on_event, iteration,
                    )
                else:
                    # provider 不支持 stream，返回了普通 response 对象
                    message, usage = _extract_completion_message(stream_or_response)
            except Exception as stream_exc:
                # 流式调用失败时回退到非流式
                logger.warning("流式调用失败，回退到非流式: %s", stream_exc)
                response = await self._create_chat_completion_with_system_fallback(kwargs)
                message, usage = _extract_completion_message(response)

            tool_calls = _normalize_tool_calls(getattr(message, "tool_calls", None))

            # 图片降级：本轮 LLM 已收到完整 base64，后续轮次降级为文本引用
            self._memory.mark_images_sent()

            # 累计 token 使用量
            if usage is not None:
                total_prompt_tokens += _usage_token(usage, "prompt_tokens")
                total_completion_tokens += _usage_token(usage, "completion_tokens")

            # 提取 thinking 内容（流式模式下已累积到 message.thinking）
            thinking_content = getattr(message, "thinking", None) or ""

            if thinking_content:
                self._emit(
                    on_event,
                    ToolCallEvent(
                        event_type=EventType.THINKING,
                        thinking=thinking_content,
                        iteration=iteration,
                    ),
                )

            # ── 收集本轮迭代诊断快照 ──
            iter_prompt = _usage_token(usage, "prompt_tokens") if usage else 0
            iter_completion = _usage_token(usage, "completion_tokens") if usage else 0
            iter_cached = _extract_cached_tokens(usage)
            diag = TurnDiagnostic(
                iteration=iteration,
                prompt_tokens=iter_prompt,
                completion_tokens=iter_completion,
                cached_tokens=iter_cached,
                thinking_content=thinking_content,
                tool_names=[
                    s.get("function", {}).get("name", "")
                    for s in tools
                    if s.get("function", {}).get("name")
                ] if tools else [],
            )
            self._turn_diagnostics.append(diag)

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

            if not tool_calls:
                self._memory.add_assistant_message(reply_text)

                # ── 执行守卫：检测"仅建议不执行"并强制继续 ──
                # 当 write_hint=="may_write" 时，由下方 write_guard 统一处理，
                # 避免两个 guard 连续触发注入语义重叠的提示（P2 修复）。
                # 当用户主动请求 VBA 帮助时，豁免 VBA 代码模式检测。
                if (
                    write_hint != "may_write"
                    and not self._active_skills
                    and iteration < max_iter - 1
                    and _contains_formula_advice(reply_text, vba_exempt=self._vba_exempt)
                    and not self._execution_guard_fired
                ):
                    self._execution_guard_fired = True
                    guard_msg = (
                        "⚠️ 你刚才在文本中给出了公式或代码建议，但没有实际写入文件。"
                        "你拥有完整的 Excel 工具集可直接操作数据。"
                        "请立即调用对应工具执行操作。"
                        "严禁给出 VBA 宏代码、AppleScript 或外部脚本替代执行。"
                        "你的所有能力均可通过内置工具实现，无需用户离开本系统。"
                    )
                    self._memory.add_user_message(guard_msg)
                    if diag:
                        diag.guard_events.append("execution_guard")
                    logger.info("执行守卫触发：检测到公式建议未写入，注入继续执行提示")
                    continue

                # ── 写入门禁：write_hint == "may_write" 时检查是否有实际写入 ──
                if write_hint == "may_write" and not self._has_write_tool_call:
                    consecutive_text_only += 1
                    if consecutive_text_only < 2 and iteration < max_iter:
                        guard_msg = (
                            "你尚未调用任何写入工具完成实际操作。"
                            "如果当前任务确实仅涉及筛选/统计/分析/查找且无需写回文件，"
                            "请直接调用 finish_task 并在 report 中说明分析结果。"
                            "如果任务需要写入，请立即调用对应写入/格式化/图表工具执行。"
                            "注意：你拥有完整的 Excel 工具集可直接操作数据，"
                            "严禁建议用户运行 VBA 宏、AppleScript 或任何外部脚本。"
                            "严禁在文本中输出 VBA 代码块作为操作方案。"
                            "禁止以文本建议替代工具执行。"
                        )
                        self._memory.add_user_message(guard_msg)
                        if diag:
                            diag.guard_events.append("write_guard")
                        logger.info("写入门禁触发：无写入工具调用，注入继续执行提示 (consecutive=%d)", consecutive_text_only)
                        continue
                    else:
                        # 连续两次纯文本或已接近迭代上限，强制退出
                        self._last_iteration_count = iteration
                        if diag:
                            diag.guard_events.append("write_guard_exit")
                        logger.warning("写入门禁：连续 %d 次纯文本退出，强制结束", consecutive_text_only)
                        return ChatResult(
                            reply=reply_text,
                            tool_calls=list(all_tool_results),
                            iterations=iteration,
                            truncated=False,
                            prompt_tokens=total_prompt_tokens,
                            completion_tokens=total_completion_tokens,
                            total_tokens=total_prompt_tokens + total_completion_tokens,
                            write_guard_triggered=True,
                        )

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
                    route_result=current_route_result,
                )

                consecutive_text_only = 0
                all_tool_results.append(tc_result)

                # Stuck Detection：记录工具调用到滑动窗口
                try:
                    _tc_args, _ = self._tool_dispatcher.parse_arguments(
                        getattr(function, "arguments", None)
                    )
                except Exception:
                    _tc_args = {}
                self._state.record_tool_call_for_stuck_detection(tool_name, _tc_args)

                # finish_task 成功接受时退出循环
                if (
                    tc_result.tool_name == "finish_task"
                    and tc_result.success
                    and tc_result.finish_accepted
                ):
                    if tool_call_id:
                        self._memory.add_tool_result(tool_call_id, tc_result.result)
                    self._last_iteration_count = iteration
                    self._last_tool_call_count += 1
                    self._last_success_count += 1
                    reply = tc_result.result
                    self._memory.add_assistant_message(reply)
                    logger.info("finish_task 接受，退出循环: %s", _summarize_text(reply))
                    return ChatResult(
                        reply=reply,
                        tool_calls=list(all_tool_results),
                        iterations=iteration,
                        truncated=False,
                        prompt_tokens=total_prompt_tokens,
                        completion_tokens=total_completion_tokens,
                        total_tokens=total_prompt_tokens + total_completion_tokens,
                    )

                if not tc_result.defer_tool_result and tool_call_id:
                    self._memory.add_tool_result(tool_call_id, tc_result.result)

                if tc_result.pending_approval:
                    self._pending_approval_route_result = current_route_result
                    self._pending_approval_tool_call_id = tool_call_id
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
                        self._pending_question_route_result = current_route_result

                # 更新统计
                self._last_tool_call_count += 1
                if tc_result.success:
                    self._last_success_count += 1
                    consecutive_failures = 0
                    if tc_result.tool_name in _WRITE_TOOL_NAMES:
                        # Batch 1 精简: write_cells advice detection 已移除
                        self._state.record_write_action()
                        if write_hint != "may_write":
                            write_hint = "may_write"
                        # Manifest 增量刷新：写入操作后标记需要刷新
                        self._manifest_refresh_needed = True
                    # ── 同步 _execute_tool_call 内部的写入传播 ──
                    # delegate_to_subagent 等工具在 _execute_tool_call 中直接
                    # 设置 self._has_write_tool_call，此处同步 write_hint 局部变量。
                    if self._has_write_tool_call and write_hint != "may_write":
                        write_hint = "may_write"
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

            # ── Stuck Detection：检测重复/冗余工具调用模式 ──
            stuck_warning = self._state.detect_stuck_pattern()
            if stuck_warning:
                self._memory.add_user_message(stuck_warning)
                if diag:
                    diag.guard_events.append("stuck_detection")
                logger.warning("Stuck Detection 触发: %s", stuck_warning[:100])

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
        # Manifest 增量刷新（debounce：整个 loop 结束后刷新一次）
        self._try_refresh_manifest()
        return ChatResult(
            reply=reply,
            tool_calls=list(all_tool_results),
            iterations=max_iter,
            truncated=True,
            prompt_tokens=total_prompt_tokens,
            completion_tokens=total_completion_tokens,
            total_tokens=total_prompt_tokens + total_completion_tokens,
        )

    def _try_refresh_manifest(self) -> None:
        """写入操作后增量刷新 Workspace Manifest（debounce：每轮最多一次）。"""
        if not self._manifest_refresh_needed or self._workspace_manifest is None:
            return
        self._manifest_refresh_needed = False
        try:
            from excelmanus.workspace_manifest import refresh_manifest
            self._workspace_manifest = refresh_manifest(self._workspace_manifest)
            logger.info("Workspace manifest 增量刷新完成")
        except Exception:
            logger.debug("Workspace manifest 增量刷新失败", exc_info=True)

    async def _execute_tool_call(
        self,
        tc: Any,
        tool_scope: Sequence[str] | None,
        on_event: EventCallback | None,
        iteration: int,
        route_result: SkillMatchResult | None = None,
    ) -> ToolCallResult:
        """单个工具调用：委托给 ToolDispatcher.execute()。"""
        return await self._tool_dispatcher.execute(
            tc, tool_scope, on_event, iteration, route_result=route_result,
        )

    def _enrich_tool_result_with_window_perception(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        result_text: str,
        success: bool,
    ) -> str:
        """在工具返回中附加窗口感知信息。"""
        requested_mode = self._requested_window_return_mode()
        try:
            return self._window_perception.enrich_tool_result(
                tool_name=tool_name,
                arguments=arguments,
                result_text=result_text,
                success=success,
                mode=requested_mode,
                model_id=self._active_model,
            )
        except Exception:
            logger.warning(
                "窗口感知增强失败，已回退 enriched 模式: tool=%s",
                tool_name,
                exc_info=True,
            )
            try:
                return self._window_perception.enrich_tool_result(
                    tool_name=tool_name,
                    arguments=arguments,
                    result_text=result_text,
                    success=success,
                    mode="enriched",
                    model_id=self._active_model,
                )
            except Exception:
                return result_text

    def _enrich_subagent_tool_result_with_window_perception(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        result_text: str,
        success: bool,
    ) -> str:
        """子代理工具结果复用主会话窗口感知增强逻辑。"""
        return self._enrich_tool_result_with_window_perception(
            tool_name=tool_name,
            arguments=arguments,
            result_text=result_text,
            success=success,
        )

    def _requested_window_return_mode(self) -> str:
        """读取配置中的请求模式（含 adaptive）。"""
        raw_mode = str(
            getattr(self._config, "window_return_mode", "adaptive") or "adaptive"
        ).strip().lower()
        if raw_mode in {"unified", "anchored", "enriched", "adaptive"}:
            return raw_mode
        return "enriched"

    def _effective_window_return_mode(self) -> str:
        """返回当前会话有效模式（只会是 unified/anchored/enriched）。"""
        if not self._window_perception.enabled:
            return "enriched"
        requested_mode = self._requested_window_return_mode()
        return self._window_perception.resolve_effective_mode(
            requested_mode=requested_mode,
            model_id=self._active_model,
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
        if force_delete_confirm and tool_name in {"delete_file"}:
            copied["confirm"] = True
        return copied

    async def _call_registry_tool(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        tool_scope: Sequence[str] | None = None,
    ) -> Any:
        """在线程池中调用工具，并绑定当前会话的记忆上下文。

        委托给 ToolDispatcher 组件。
        """
        return await self._tool_dispatcher.call_registry_tool(
            tool_name=tool_name,
            arguments=arguments,
            tool_scope=tool_scope,
        )

    async def _execute_tool_with_audit(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        tool_scope: Sequence[str] | None = None,
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
            from excelmanus.tools import memory_tools

            with memory_tools.bind_memory_context(self._persistent_memory):
                return self._registry.call_tool(name, args, tool_scope=scope)

        try:
            return await asyncio.to_thread(
                self._approval.execute_and_audit,
                approval_id=approval_id,
                tool_name=tool_name,
                arguments=audited_arguments,
                tool_scope=list(tool_scope) if tool_scope else None,
                execute=_execute,
                undoable=undoable,
                created_at_utc=created_at_utc,
            )
        except Exception as exc:  # noqa: BLE001
            # execute_and_audit 在失败时会先写入 manifest 与 _applied，再抛异常。
            # 这里将失败记录带回调用方，避免上层丢失审计上下文。
            record = self._approval.get_applied(approval_id)
            if record is None:
                raise
            raise _AuditedExecutionError(cause=exc, record=record) from exc

    def clear_memory(self) -> None:
        """清除对话历史。"""
        if self._active_skills:
            _primary = self._active_skills[-1]
            self._run_skill_hook(
                skill=_primary,
                event=HookEvent.STOP,
                payload={"reason": "clear_memory"},
            )
            self._run_skill_hook(
                skill=_primary,
                event=HookEvent.SESSION_END,
                payload={"reason": "clear_memory"},
            )
        self._memory.clear()
        self._loaded_skill_names.clear()
        self._hook_started_skills.clear()
        self._active_skills.clear()
        self._question_flow.clear()
        self._system_question_actions.clear()
        self._pending_question_route_result = None
        self._pending_approval_route_result = None
        self._pending_approval_tool_call_id = None
        self._pending_plan = None
        self._approved_plan_context = None
        self._task_store.clear()
        self._approval.clear_pending()
        self._window_perception.reset()
        # 重置轮级状态变量，防止跨对话污染
        self._state.reset_session()
        # _system_mode_fallback 同步类缓存（不重置，跨会话复用探测结果）
        self._system_mode_fallback = type(self)._system_mode_fallback_cache
        self._last_route_result = SkillMatchResult(
            skills_used=[],
            route_mode="fallback",
        )

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
            self._client = create_client(
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
        self._client = create_client(
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
        # adviser 也跟随主模型（仅当 adviser 未配置独立模型时）
        if self._advisor_follow_active_model:
            self._advisor_client = self._client
            self._advisor_model = self._active_model

    async def _adapt_guidance_only_slash_route(
        self,
        *,
        route_result: SkillMatchResult,
        user_message: str,
        slash_command: str | None,
        raw_args: str,
    ) -> tuple[SkillMatchResult, str]:
        """将仅指导型 slash 技能回落为任务执行路由，避免“只讲不做”。

        触发条件：
        - 手动 slash 命令命中（route_mode=slash_direct）
        - 命中的 skill 不是 command_dispatch=tool
        - slash 参数中包含可执行任务文本
        """
        if not slash_command or route_result.route_mode != "slash_direct":
            return route_result, user_message

        task_text = raw_args.strip()
        if not task_text:
            return route_result, user_message

        skill = self._pick_route_skill(route_result)
        if skill is None:
            return route_result, user_message
        if skill.command_dispatch == "tool":
            return route_result, user_message

        # 先尝试词法分类，避免重复触发 write_hint LLM 调用
        pre_hint: str | None = None
        if self._skill_router is not None:
            pre_hint = self._skill_router._classify_write_hint_lexical(task_text) or None
        fallback = await self._route_skills(task_text, write_hint=pre_hint)
        guidance_context = (
            f"[Slash Guidance] 已启用技能 `{skill.name}` 的方法论约束。\n"
            "该技能仅用于补充执行规范，不改变用户任务目标。\n"
            "请优先调用工具完成任务，不要只输出「我先…」「我将…」等计划性文字。"
        )
        fallback_contexts = list(fallback.system_contexts)
        fallback_contexts.append(guidance_context)
        adapted = SkillMatchResult(
            skills_used=list(fallback.skills_used),
            route_mode=fallback.route_mode,
            system_contexts=fallback_contexts,
            parameterized=fallback.parameterized,
            write_hint=fallback.write_hint,
        )
        logger.info(
            "斜杠技能 %s 为 guidance-only，已回落到任务路由: %s",
            skill.name,
            _summarize_text(task_text),
        )
        return adapted, task_text

    async def _route_skills(
        self,
        user_message: str,
        *,
        slash_command: str | None = None,
        raw_args: str | None = None,
        write_hint: str | None = None,
    ) -> SkillMatchResult:
        if self._skill_router is None:
            return SkillMatchResult(
                skills_used=[],
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
            write_hint=write_hint,
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
        tc_result = await self._execute_tool_call(
            tc,
            None,
            on_event,
            iteration=1,
            route_result=route_result,
        )

        if not tc_result.defer_tool_result:
            self._memory.add_tool_result(tool_call_id, tc_result.result)

        if tc_result.pending_question and self._pending_question_route_result is None:
            self._pending_question_route_result = route_result
        if tc_result.pending_approval:
            self._pending_approval_route_result = route_result
            self._pending_approval_tool_call_id = tool_call_id

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
        """处理会话级控制命令：委托给 CommandHandler。"""
        return await self._command_handler.handle(
            user_message, on_event=on_event,
        )

    async def _handle_accept_command(
        self, parts: list[str], *, on_event: EventCallback | None = None,
    ) -> str:
        return await self._command_handler._handle_accept_command(parts, on_event=on_event)

    def _handle_reject_command(self, parts: list[str]) -> str:
        return self._command_handler._handle_reject_command(parts)

    async def _handle_plan_approve(
        self, *, parts: list[str], on_event: EventCallback | None = None,
    ) -> str:
        return await self._command_handler._handle_plan_approve(parts=parts, on_event=on_event)

    def _handle_plan_reject(self, *, parts: list[str]) -> str:
        return self._command_handler._handle_plan_reject(parts=parts)

    # ── Context Builder 委托方法 ──────────────────────────────

    def _all_tool_names(self) -> list[str]:
        return self._context_builder._all_tool_names()

    def _focus_window_refill_reader(
        self, *, file_path: str, sheet_name: str, range_ref: str,
    ) -> dict[str, Any]:
        return self._context_builder._focus_window_refill_reader(
            file_path=file_path, sheet_name=sheet_name, range_ref=range_ref,
        )

    def _prepare_system_prompts_for_request(
        self,
        skill_contexts: list[str],
        *,
        route_result: SkillMatchResult | None = None,
    ) -> tuple[list[str], str | None]:
        return self._context_builder._prepare_system_prompts_for_request(
            skill_contexts, route_result=route_result,
        )

    def _build_access_notice(self) -> str:
        return self._context_builder._build_access_notice()

    def _build_backup_notice(self) -> str:
        return self._context_builder._build_backup_notice()

    def _build_mcp_context_notice(self) -> str:
        return self._context_builder._build_mcp_context_notice()

    def _build_window_perception_notice(self) -> str:
        return self._context_builder._build_window_perception_notice()

    def _build_tool_index_notice(
        self, *, compact: bool = False, max_tools_per_category: int = 8,
    ) -> str:
        return self._context_builder._build_tool_index_notice(
            compact=compact, max_tools_per_category=max_tools_per_category,
        )

    def _set_window_perception_turn_hints(
        self, *, user_message: str, is_new_task: bool,
    ) -> None:
        self._context_builder._set_window_perception_turn_hints(
            user_message=user_message, is_new_task=is_new_task,
        )

    def _redirect_backup_paths(
        self, tool_name: str, arguments: dict[str, Any],
    ) -> dict[str, Any]:
        return self._context_builder._redirect_backup_paths(tool_name, arguments)

    def _has_incomplete_tasks(self) -> bool:
        return self._context_builder._has_incomplete_tasks()

    async def _auto_continue_task_loop(
        self,
        route_result: SkillMatchResult,
        on_event: EventCallback | None,
        initial_result: "ChatResult",
    ) -> "ChatResult":
        return await self._context_builder._auto_continue_task_loop(
            route_result, on_event, initial_result,
        )


    @staticmethod
    def _iter_exception_chain(exc: Exception) -> list[Exception]:
        """遍历异常链（__cause__ / __context__），用于提取底层错误信息。"""
        chain: list[Exception] = []
        seen: set[int] = set()
        current: Exception | None = exc
        while current is not None and id(current) not in seen:
            chain.append(current)
            seen.add(id(current))
            next_exc = getattr(current, "__cause__", None)
            if not isinstance(next_exc, Exception):
                next_exc = getattr(current, "__context__", None)
            current = next_exc if isinstance(next_exc, Exception) else None
        return chain

    @staticmethod
    def _is_transient_window_advisor_exception(exc: Exception) -> bool:
        """判断顾问调用异常是否可进行一次轻量重试。"""
        transient_keywords = (
            "429",
            "too many requests",
            "rate limit",
            "service unavailable",
            "temporarily unavailable",
            "connection reset",
            "connection aborted",
            "connection closed",
            "server disconnected",
            "broken pipe",
            "econnreset",
            "network is unreachable",
            "timed out",
            "timeout",
            "connecterror",
            "temporary failure in name resolution",
            "name or service not known",
        )
        for candidate in AgentEngine._iter_exception_chain(exc):
            status_code = getattr(candidate, "status_code", None)
            if isinstance(status_code, int) and (
                status_code == 429 or 500 <= status_code < 600
            ):
                return True

            name = candidate.__class__.__name__.lower()
            if name in {
                "ratelimiterror",
                "apiconnectionerror",
                "apitimeouterror",
                "connecterror",
                "proxyerror",
                "networkerror",
                "transporterror",
            }:
                return True

            text = f"{candidate} {candidate!r}".lower()
            if any(keyword in text for keyword in transient_keywords):
                return True

        return False

    @staticmethod
    def _extract_retry_after_seconds(exc: Exception) -> float | None:
        """尽量从异常响应头提取 Retry-After（秒）。"""
        for candidate in AgentEngine._iter_exception_chain(exc):
            response = getattr(candidate, "response", None)
            if response is None:
                continue
            headers = getattr(response, "headers", None)
            if headers is None:
                continue

            raw_retry_after: Any = None
            get_header = getattr(headers, "get", None)
            if callable(get_header):
                raw_retry_after = get_header("retry-after") or get_header("Retry-After")
            elif isinstance(headers, dict):
                raw_retry_after = headers.get("retry-after") or headers.get("Retry-After")

            if raw_retry_after is None:
                continue
            try:
                retry_after_seconds = float(str(raw_retry_after).strip())
            except (TypeError, ValueError):
                continue
            if retry_after_seconds < 0:
                continue
            return retry_after_seconds
        return None

    @staticmethod
    def _window_advisor_retry_delay_seconds(exc: Exception) -> float:
        """计算轻量重试等待时间。"""
        retry_after = AgentEngine._extract_retry_after_seconds(exc)
        if retry_after is not None:
            return max(
                _WINDOW_ADVISOR_RETRY_DELAY_MIN_SECONDS,
                min(_WINDOW_ADVISOR_RETRY_AFTER_CAP_SECONDS, retry_after),
            )
        return random.uniform(
            _WINDOW_ADVISOR_RETRY_DELAY_MIN_SECONDS,
            _WINDOW_ADVISOR_RETRY_DELAY_MAX_SECONDS,
        )

    @staticmethod
    def _window_advisor_retry_timeout_seconds(primary_timeout_seconds: float) -> float:
        """计算二次快速重试超时，确保短于首轮。"""
        retry_timeout = min(
            _WINDOW_ADVISOR_RETRY_TIMEOUT_CAP_SECONDS,
            max(0.1, float(primary_timeout_seconds) * 0.4),
        )
        if retry_timeout >= primary_timeout_seconds:
            retry_timeout = max(0.1, primary_timeout_seconds - 0.1)
        return retry_timeout

    async def _run_window_perception_advisor_async(
        self,
        windows: list[Window],
        active_window_id: str | None,
        budget: PerceptionBudget,
        context: AdvisorContext,
    ) -> LifecyclePlan | None:
        """异步调用小模型生成窗口生命周期建议。"""
        messages = build_advisor_messages(
            windows=windows,
            active_window_id=active_window_id,
            budget=budget,
            context=context,
        )
        timeout_seconds = max(
            0.1,
            int(self._config.window_perception_advisor_timeout_ms) / 1000,
        )

        async def _invoke(timeout: float) -> Any:
            return await asyncio.wait_for(
                self._advisor_client.chat.completions.create(
                    model=self._advisor_model,
                    messages=messages,
                ),
                timeout=timeout,
            )

        try:
            response = await _invoke(timeout_seconds)
        except asyncio.TimeoutError:
            logger.info("窗口感知小模型调用超时（%.2fs）", timeout_seconds)
            return None
        except Exception as exc:
            if not self._is_transient_window_advisor_exception(exc):
                logger.warning("窗口感知小模型调用失败，已回退规则顾问", exc_info=True)
                return None

            retry_delay_seconds = self._window_advisor_retry_delay_seconds(exc)
            retry_timeout_seconds = self._window_advisor_retry_timeout_seconds(timeout_seconds)
            logger.info(
                "窗口感知小模型触发瞬时错误，%.2fs 后执行一次快速重试（%.2fs）：%s",
                retry_delay_seconds,
                retry_timeout_seconds,
                exc.__class__.__name__,
            )
            await asyncio.sleep(retry_delay_seconds)
            try:
                response = await _invoke(retry_timeout_seconds)
            except asyncio.TimeoutError:
                logger.info("窗口感知小模型快速重试超时（%.2fs）", retry_timeout_seconds)
                return None
            except Exception:
                logger.warning("窗口感知小模型快速重试失败，已回退规则顾问", exc_info=True)
                return None

        message, _ = _extract_completion_message(response)
        content = _message_content_to_text(getattr(message, "content", None)).strip()
        if not content:
            return None
        plan = parse_small_model_plan(content)
        if plan is None:
            logger.info("窗口感知小模型输出解析失败，已回退规则顾问")
            return None
        return plan

    def _effective_system_mode(self) -> str:
        configured = self._config.system_message_mode
        if configured != "auto":
            return configured
        if type(self)._system_mode_fallback_cache == "merge":
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

    async def _consume_stream(
        self,
        stream: Any,
        on_event: EventCallback | None,
        iteration: int,
    ) -> tuple[Any, Any]:
        """消费流式响应，逐 chunk 发射 delta 事件，返回累积的 (message, usage)。

        兼容两种 chunk 格式：
        - openai.AsyncOpenAI: ChatCompletionChunk (choices[0].delta)
        - 自定义 provider: _StreamDelta (content_delta / thinking_delta)
        """
        content_parts: list[str] = []
        thinking_parts: list[str] = []
        tool_calls_accumulated: dict[int, dict] = {}
        finish_reason: str | None = None
        usage = None

        async for chunk in stream:
            # ── 自定义 provider 的 _StreamDelta ──
            if hasattr(chunk, "content_delta"):
                if chunk.content_delta:
                    content_parts.append(chunk.content_delta)
                    self._emit(on_event, ToolCallEvent(
                        event_type=EventType.TEXT_DELTA,
                        text_delta=chunk.content_delta,
                        iteration=iteration,
                    ))
                if chunk.thinking_delta:
                    thinking_parts.append(chunk.thinking_delta)
                    self._emit(on_event, ToolCallEvent(
                        event_type=EventType.THINKING_DELTA,
                        thinking_delta=chunk.thinking_delta,
                        iteration=iteration,
                    ))
                if chunk.tool_calls_delta:
                    for tc in chunk.tool_calls_delta:
                        idx = tc.get("index", 0)
                        tool_calls_accumulated[idx] = tc
                if chunk.finish_reason:
                    finish_reason = chunk.finish_reason
                if chunk.usage:
                    usage = chunk.usage
                continue

            # ── openai.AsyncOpenAI 的 ChatCompletionChunk ──
            choices = getattr(chunk, "choices", None)
            if not choices:
                chunk_usage = getattr(chunk, "usage", None)
                if chunk_usage:
                    usage = chunk_usage
                continue

            delta = getattr(choices[0], "delta", None)
            if delta is None:
                continue

            delta_content = getattr(delta, "content", None)
            if delta_content:
                content_parts.append(delta_content)
                self._emit(on_event, ToolCallEvent(
                    event_type=EventType.TEXT_DELTA,
                    text_delta=delta_content,
                    iteration=iteration,
                ))

            for thinking_key in ("thinking", "reasoning", "reasoning_content"):
                thinking_val = getattr(delta, thinking_key, None)
                if thinking_val:
                    thinking_parts.append(str(thinking_val))
                    self._emit(on_event, ToolCallEvent(
                        event_type=EventType.THINKING_DELTA,
                        thinking_delta=str(thinking_val),
                        iteration=iteration,
                    ))
                    break

            delta_tool_calls = getattr(delta, "tool_calls", None)
            if delta_tool_calls:
                for tc_delta in delta_tool_calls:
                    idx = getattr(tc_delta, "index", 0)
                    if idx not in tool_calls_accumulated:
                        tool_calls_accumulated[idx] = {
                            "id": getattr(tc_delta, "id", None) or "",
                            "name": "",
                            "arguments": "",
                        }
                    fn = getattr(tc_delta, "function", None)
                    if fn:
                        name = getattr(fn, "name", None)
                        if name:
                            tool_calls_accumulated[idx]["name"] = name
                        args = getattr(fn, "arguments", None)
                        if args:
                            tool_calls_accumulated[idx]["arguments"] += args
                    tc_id = getattr(tc_delta, "id", None)
                    if tc_id:
                        tool_calls_accumulated[idx]["id"] = tc_id

            chunk_finish = getattr(choices[0], "finish_reason", None)
            if chunk_finish:
                finish_reason = chunk_finish

            chunk_usage = getattr(chunk, "usage", None)
            if chunk_usage:
                usage = chunk_usage

        # 组装为与非流式路径兼容的 message 对象
        content = "".join(content_parts)
        thinking = "".join(thinking_parts)

        tool_calls_list = []
        if tool_calls_accumulated:
            for idx in sorted(tool_calls_accumulated.keys()):
                tc = tool_calls_accumulated[idx]
                tool_calls_list.append(SimpleNamespace(
                    id=tc["id"],
                    function=SimpleNamespace(
                        name=tc["name"],
                        arguments=tc["arguments"],
                    ),
                ))

        message = SimpleNamespace(
            content=content,
            tool_calls=tool_calls_list or None,
            thinking=thinking if thinking else None,
            reasoning=None,
            reasoning_content=None,
        )

        return message, usage

    async def _create_chat_completion_with_system_fallback(
        self,
        kwargs: dict[str, Any],
    ) -> Any:
        try:
            return await self._client.chat.completions.create(**kwargs)
        except Exception as exc:
            # prompt_cache_key 兼容性：非 OpenAI provider 可能不支持该参数
            if "prompt_cache_key" in kwargs and self._is_unsupported_param_error(exc):
                logger.debug("Provider 不支持 prompt_cache_key，移除后重试")
                retry_kwargs = {k: v for k, v in kwargs.items() if k != "prompt_cache_key"}
                try:
                    return await self._client.chat.completions.create(**retry_kwargs)
                except Exception:
                    pass  # 回退失败，走下方原有逻辑

            if (
                self._config.system_message_mode == "auto"
                and self._effective_system_mode() == "replace"
                and self._is_system_compatibility_error(exc)
            ):
                logger.warning("检测到 replace(system 分段) 兼容性错误，自动回退到 merge 模式")
                type(self)._system_mode_fallback_cache = "merge"
                self._system_mode_fallback = "merge"
                source_messages = kwargs.get("messages")
                if not isinstance(source_messages, list):
                    raise
                merged_messages = self._merge_leading_system_messages(source_messages)
                retry_kwargs = dict(kwargs)
                retry_kwargs["messages"] = merged_messages
                # 同样移除可能不支持的 prompt_cache_key
                retry_kwargs.pop("prompt_cache_key", None)
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
    def _is_unsupported_param_error(exc: Exception) -> bool:
        """检测是否为 provider 不支持某参数的错误（如 prompt_cache_key）。"""
        text = str(exc).lower()
        keywords = [
            "unexpected keyword",
            "unrecognized request argument",
            "unknown parameter",
            "invalid parameter",
            "prompt_cache_key",
            "extra inputs are not permitted",
        ]
        return any(keyword in text for keyword in keywords)

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
