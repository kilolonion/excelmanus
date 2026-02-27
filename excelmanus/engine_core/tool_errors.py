"""ToolError — 结构化错误分类、自动重试判定与错误压缩。

职责：
- ToolErrorKind 枚举：RETRYABLE / PERMANENT / NEEDS_HUMAN / CONTEXT_OVERFLOW
- classify_tool_error(): 将异常或错误字符串映射到 ToolErrorKind
- compact_error(): 将冗长的原始错误压缩为结构化 JSON，供 LLM 消费
- RetryPolicy: 可重试错误的退避策略（最大重试次数、基础延迟）
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ToolErrorKind(Enum):
    """工具错误分类。"""

    RETRYABLE = "retryable"          # 网络超时、rate limit、临时故障
    PERMANENT = "permanent"          # 文件不存在、参数无效、权限拒绝
    NEEDS_HUMAN = "needs_human"      # 数据歧义、需要用户确认
    CONTEXT_OVERFLOW = "overflow"    # 工具结果过大、内存不足


@dataclass(frozen=True)
class ToolError:
    """结构化的工具错误描述。"""

    kind: ToolErrorKind
    summary: str
    suggestion: str = ""
    original_error: str = ""
    retryable: bool = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "retryable", self.kind == ToolErrorKind.RETRYABLE)

    def to_compact_str(self) -> str:
        """压缩为 LLM 友好的 JSON 字符串。"""
        d: dict[str, Any] = {
            "error_kind": self.kind.value,
            "summary": self.summary,
        }
        if self.suggestion:
            d["suggestion"] = self.suggestion
        return json.dumps(d, ensure_ascii=False)


# ── 分类规则 ──────────────────────────────────────────────────

# 可重试错误的关键词/模式（大小写不敏感）
_RETRYABLE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE) for p in [
        r"timeout",
        r"timed?\s*out",
        r"rate.?limit",
        r"too\s+many\s+requests",
        r"429",
        r"503",
        r"502",
        r"connection\s*(reset|refused|aborted|error)",
        r"temporary\s*(failure|error|unavailable)",
        r"network\s*(error|unreachable)",
        r"ECONNRESET",
        r"ECONNREFUSED",
        r"ETIMEDOUT",
        r"retry",
        r"server\s+error",
        r"internal\s+server\s+error",
        r"resource\s+temporarily\s+unavailable",
    ]
]

# 需要人工介入的关键词/模式
_NEEDS_HUMAN_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE) for p in [
        r"ambiguous",
        r"歧义",
        r"无法确定",
        r"请.*确认",
        r"请.*选择",
        r"multiple\s+matches",
        r"需要.*用户",
    ]
]

# 上下文溢出的关键词/模式
_OVERFLOW_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE) for p in [
        r"result\s+too\s+large",
        r"结果过大",
        r"truncat",
        r"截断",
        r"exceed.*limit",
        r"超出.*限制",
        r"out\s+of\s+memory",
        r"内存不足",
        r"MemoryError",
    ]
]

# 可重试的异常类型名
_RETRYABLE_EXCEPTION_TYPES: frozenset[str] = frozenset({
    "TimeoutError",
    "asyncio.TimeoutError",
    "ConnectionError",
    "ConnectionResetError",
    "ConnectionRefusedError",
    "ConnectionAbortedError",
    "OSError",
    "httpx.TimeoutException",
    "httpx.ConnectError",
    "httpx.ReadTimeout",
    "httpx.WriteTimeout",
    "httpx.PoolTimeout",
    "openai.APITimeoutError",
    "openai.RateLimitError",
    "openai.APIConnectionError",
    "openai.InternalServerError",
    "anthropic.APITimeoutError",
    "anthropic.RateLimitError",
    "anthropic.APIConnectionError",
    "anthropic.InternalServerError",
})


def classify_tool_error(
    error: str | Exception,
    *,
    tool_name: str = "",
) -> ToolError:
    """将错误分类为 ToolError。

    分类优先级：CONTEXT_OVERFLOW > RETRYABLE > NEEDS_HUMAN > PERMANENT
    """
    if isinstance(error, Exception):
        error_str = str(error)
        exc_type = type(error).__name__
        # 先按异常类型精确匹配
        full_type = f"{type(error).__module__}.{exc_type}"
        if exc_type in _RETRYABLE_EXCEPTION_TYPES or full_type in _RETRYABLE_EXCEPTION_TYPES:
            return ToolError(
                kind=ToolErrorKind.RETRYABLE,
                summary=f"{exc_type}: {_truncate(error_str, 200)}",
                suggestion="这是一个临时性错误，系统将自动重试。",
                original_error=_truncate(error_str, 500),
            )
    else:
        error_str = error

    # 按模式匹配分类
    # 1. 上下文溢出（最高优先级）
    for pat in _OVERFLOW_PATTERNS:
        if pat.search(error_str):
            return ToolError(
                kind=ToolErrorKind.CONTEXT_OVERFLOW,
                summary=_truncate(error_str, 200),
                suggestion="结果数据量过大，请缩小操作范围或分批处理。",
                original_error=_truncate(error_str, 500),
            )

    # 2. 可重试
    for pat in _RETRYABLE_PATTERNS:
        if pat.search(error_str):
            return ToolError(
                kind=ToolErrorKind.RETRYABLE,
                summary=_truncate(error_str, 200),
                suggestion="这是一个临时性错误，系统将自动重试。",
                original_error=_truncate(error_str, 500),
            )

    # 3. 需要人工
    for pat in _NEEDS_HUMAN_PATTERNS:
        if pat.search(error_str):
            return ToolError(
                kind=ToolErrorKind.NEEDS_HUMAN,
                summary=_truncate(error_str, 200),
                suggestion="请调用 ask_user 工具向用户确认。",
                original_error=_truncate(error_str, 500),
            )

    # 4. 兜底：永久错误
    return ToolError(
        kind=ToolErrorKind.PERMANENT,
        summary=_truncate(error_str, 200),
        suggestion="请检查参数是否正确，或尝试其他方法。",
        original_error=_truncate(error_str, 500),
    )


def compact_error(
    error: str | None,
    *,
    tool_name: str = "",
    tool_error: ToolError | None = None,
) -> str:
    """将错误信息压缩为 LLM 友好的格式。

    如果已有 ToolError 分类，直接使用其 compact 格式；
    否则对原始错误字符串做基本截断和清理。
    """
    if tool_error is not None:
        return tool_error.to_compact_str()

    if not error:
        return ""

    # 清理常见的冗余信息
    cleaned = _clean_traceback(error)
    return _truncate(cleaned, 300)


# ── 重试策略 ──────────────────────────────────────────────────

@dataclass(frozen=True)
class RetryPolicy:
    """可重试错误的退避策略。"""

    max_retries: int = 2
    base_delay_seconds: float = 0.5
    max_delay_seconds: float = 4.0

    def delay_for_attempt(self, attempt: int) -> float:
        """计算第 N 次重试的延迟（指数退避）。"""
        delay = self.base_delay_seconds * (2 ** attempt)
        return min(delay, self.max_delay_seconds)


# 默认重试策略
DEFAULT_RETRY_POLICY = RetryPolicy(max_retries=2, base_delay_seconds=0.5)


# ── 辅助函数 ──────────────────────────────────────────────────

def _truncate(text: str, max_len: int) -> str:
    """截断文本到指定长度。"""
    if len(text) <= max_len:
        return text
    return text[:max_len - 3] + "..."


_TRACEBACK_RE = re.compile(
    r"Traceback \(most recent call last\):.*?(?=\w+Error:|\w+Exception:)",
    re.DOTALL,
)


def _clean_traceback(error: str) -> str:
    """移除 Python traceback，只保留最终错误行。"""
    # 如果包含 traceback，提取最后的错误行
    lines = error.strip().splitlines()
    if len(lines) > 3 and "Traceback" in lines[0]:
        # 取最后一行（通常是 ErrorType: message）
        return lines[-1].strip()
    # 移除 traceback 块但保留前后内容
    cleaned = _TRACEBACK_RE.sub("", error).strip()
    return cleaned if cleaned else error
