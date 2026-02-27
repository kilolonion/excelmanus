"""Engine 数据类型与类型别名 — 从 engine.py 提取的零状态依赖类型定义。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Awaitable, Callable

if TYPE_CHECKING:
    from excelmanus.approval import AppliedApprovalRecord, PendingApproval
    from excelmanus.question_flow import PendingQuestion
    from excelmanus.subagent import SubagentResult

# ── Thinking 配置 ──────────────────────────────────────────────
_EFFORT_RATIOS: dict[str, float] = {
    "none": 0.0, "minimal": 0.10, "low": 0.20,
    "medium": 0.50, "high": 0.80, "xhigh": 0.95,
}

# effort → Gemini thinkingLevel 映射
_EFFORT_TO_GEMINI_LEVEL: dict[str, str] = {
    "none": "minimal", "minimal": "minimal", "low": "low",
    "medium": "medium", "high": "high", "xhigh": "high",
}

# effort → OpenAI reasoning_effort 映射
_EFFORT_TO_OPENAI: dict[str, str] = {
    "none": "none", "minimal": "minimal", "low": "low",
    "medium": "medium", "high": "high", "xhigh": "high",
}


@dataclass
class ThinkingConfig:
    """Thinking（推理深度）统一配置，支持等级制和预算制。"""

    effort: str = "medium"  # none|minimal|low|medium|high|xhigh
    budget_tokens: int = 0  # >0 时覆盖 effort 换算值

    @property
    def is_disabled(self) -> bool:
        return self.effort == "none" and self.budget_tokens <= 0

    def effective_budget(self, max_tokens: int = 16384) -> int:
        """计算有效 token 预算。budget_tokens > 0 直接返回，否则按 effort 比例换算。"""
        if self.budget_tokens > 0:
            return self.budget_tokens
        ratio = _EFFORT_RATIOS.get(self.effort, 0.5)
        return max(1024, int(max_tokens * ratio)) if ratio > 0 else 0

    @property
    def openai_effort(self) -> str:
        return _EFFORT_TO_OPENAI.get(self.effort, "medium")

    @property
    def gemini_level(self) -> str:
        return _EFFORT_TO_GEMINI_LEVEL.get(self.effort, "high")


@dataclass
class ToolCallResult:
    """单次工具调用的结果记录。"""

    tool_name: str
    arguments: dict
    result: str
    success: bool
    error: str | None = None
    error_kind: str | None = None  # ToolErrorKind.value: retryable/permanent/needs_human/overflow
    pending_approval: bool = False
    approval_id: str | None = None
    audit_record: "AppliedApprovalRecord | None" = None
    pending_question: bool = False
    question_id: str | None = None
    # pending_plan 和 plan_id 已废弃（Chat Mode Tabs 重构）
    defer_tool_result: bool = False
    finish_accepted: bool = False


class _AuditedExecutionError(Exception):
    """携带审计记录的工具执行异常。"""

    def __init__(self, *, cause: Exception, record: "AppliedApprovalRecord") -> None:
        super().__init__(str(cause))
        self.cause = cause
        self.record = record


@dataclass
class _ToolCallBatch:
    """一组连续的工具调用，标记是否可并行执行。"""

    tool_calls: list[Any]
    parallel: bool


@dataclass
class TurnDiagnostic:
    """单次 LLM 迭代的诊断快照，用于事后分析。"""

    iteration: int
    # token 使用
    prompt_tokens: int = 0
    completion_tokens: int = 0
    # provider 缓存命中的 token 数（OpenAI prompt_tokens_details.cached_tokens）
    cached_tokens: int = 0
    # Anthropic 提示词缓存专用字段
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    # TTFT（Time To First Token）毫秒
    ttft_ms: float = 0.0
    # 模型 thinking/reasoning 内容
    thinking_content: str = ""
    # 该迭代暴露给模型的工具名列表
    tool_names: list[str] = field(default_factory=list)
    # 门禁事件
    guard_events: list[str] = field(default_factory=list)
    # Think-Act 推理检测
    has_reasoning: bool = True
    reasoning_chars: int = 0
    silent_tool_call_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "iteration": self.iteration,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
        }
        if self.cached_tokens:
            d["cached_tokens"] = self.cached_tokens
        if self.cache_creation_input_tokens:
            d["cache_creation_input_tokens"] = self.cache_creation_input_tokens
        if self.cache_read_input_tokens:
            d["cache_read_input_tokens"] = self.cache_read_input_tokens
        if self.ttft_ms:
            d["ttft_ms"] = self.ttft_ms
        if self.thinking_content:
            d["thinking_content"] = self.thinking_content
        if self.tool_names:
            d["tool_names"] = self.tool_names
        if self.guard_events:
            d["guard_events"] = self.guard_events
        if not self.has_reasoning:
            d["has_reasoning"] = False
        if self.reasoning_chars:
            d["reasoning_chars"] = self.reasoning_chars
        if self.silent_tool_call_count:
            d["silent_tool_call_count"] = self.silent_tool_call_count
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
    # Think-Act 推理质量指标
    reasoning_metrics: dict[str, Any] = field(default_factory=dict)

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
    subagent_result: "SubagentResult | None" = None


# ── 审批解析器回调类型 ──────────────────────────────────────
# 返回值为 "accept" / "reject" / "fullaccess" / None（None 等同 reject）。
# CLI 传入交互式选择器实现，Web API 不传则回退到现有行为（退出循环）。
ApprovalResolver = Callable[["PendingApproval"], Awaitable[str | None]]

# ── 问题解析器回调类型 ──────────────────────────────────────
# CLI/bench 传入交互式问答实现；Web API 不传则使用 InteractionRegistry Future。
# 回调接收 PendingQuestion，返回用户原始回答文本。
QuestionResolver = Callable[["PendingQuestion"], Awaitable[str]]
