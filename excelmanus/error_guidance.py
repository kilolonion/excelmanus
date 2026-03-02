"""结构化失败引导 — 将异常归因为用户可操作的 FailureGuidance。

从异常对象中提取 HTTP 状态码、异常类型、错误消息关键词，
映射为 category / code / title / message / actions 结构，
供 SSE failure_guidance 事件携带到前端渲染为可交互卡片。
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

from excelmanus.engine_core.llm_caller import iter_exception_chain


# ── 数据模型 ──────────────────────────────────────────────────


@dataclass
class FailureGuidance:
    """结构化失败引导，与 SSE failure_guidance 事件 1:1 对应。"""

    category: str = "unknown"       # model | transport | config | quota | unknown
    code: str = "internal_error"    # 机器可读错误码
    title: str = "内部错误"          # 一句话标题（≤15 字）
    message: str = ""               # 用户可见描述（≤80 字）
    stage: str = ""                 # 失败阶段（取自 pipeline_progress）
    retryable: bool = False
    diagnostic_id: str = ""         # UUID
    actions: list[dict[str, str]] = field(default_factory=list)
    provider: str = ""
    model: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ── Action 构造辅助 ───────────────────────────────────────────

_ACTION_RETRY = {"type": "retry", "label": "立即重试"}
_ACTION_OPEN_SETTINGS = {"type": "open_settings", "label": "检查模型设置"}
_ACTION_COPY_DIAGNOSTIC = {"type": "copy_diagnostic", "label": "复制诊断 ID"}


def _actions_for(retryable: bool) -> list[dict[str, str]]:
    """根据是否可重试生成默认 action 列表。"""
    if retryable:
        return [_ACTION_RETRY, _ACTION_OPEN_SETTINGS, _ACTION_COPY_DIAGNOSTIC]
    return [_ACTION_OPEN_SETTINGS, _ACTION_COPY_DIAGNOSTIC]


# ── 状态码提取 ────────────────────────────────────────────────


def _extract_status_code(exc: Exception) -> int | None:
    """从异常链中提取 HTTP 状态码。"""
    for candidate in iter_exception_chain(exc):
        code = getattr(candidate, "status_code", None)
        if isinstance(code, int):
            return code
        # httpx.Response 的 status_code
        resp = getattr(candidate, "response", None)
        if resp is not None:
            sc = getattr(resp, "status_code", None)
            if isinstance(sc, int):
                return sc
    return None


# ── 分类规则 ──────────────────────────────────────────────────

# 关键词 → (category, code, title, message, retryable)
_KEYWORD_RULES: list[tuple[tuple[str, ...], str, str, str, str, bool]] = [
    # quota 优先
    (
        ("insufficient quota", "quota exceeded", "billing", "balance", "payment_required"),
        "quota", "quota_exceeded",
        "额度不足", "模型 API 额度已用尽或账单异常，请检查服务商账户余额。",
        False,
    ),
    # auth
    (
        ("invalid api key", "invalid api_key", "api key", "authentication", "unauthorized"),
        "model", "model_auth_failed",
        "模型认证失败", "API Key 无效或已过期，请在模型设置中更新。",
        False,
    ),
    # model not found
    (
        ("model not found", "model_not_found", "does not exist", "no such model", "invalid model"),
        "model", "model_not_found",
        "模型不存在", "请求的模型标识无效，请在设置中确认 Model ID 是否正确。",
        False,
    ),
    # overloaded
    (
        ("overloaded", "capacity", "service unavailable", "temporarily unavailable"),
        "model", "model_overloaded",
        "模型服务过载", "模型服务暂时不可用，请稍后重试。",
        True,
    ),
    # network / transport
    (
        (
            "connection refused", "connect timeout", "name or service not known",
            "network is unreachable", "temporary failure in name resolution",
            "broken pipe", "connection reset", "connection aborted",
            "connection closed", "server disconnected", "econnreset",
        ),
        "transport", "network_error",
        "网络连接失败", "无法连接到模型服务，请检查网络或 Base URL 配置。",
        True,
    ),
    # timeout
    (
        ("timed out", "timeout", "deadline exceeded"),
        "transport", "connect_timeout",
        "连接超时", "模型服务响应超时，请稍后重试或检查网络。",
        True,
    ),
]


def classify_failure(
    exc: Exception,
    *,
    stage: str = "",
    provider: str = "",
    model: str = "",
    retries_exhausted: bool = False,
) -> FailureGuidance:
    """从异常对象构建结构化失败引导。

    分类优先级：
    1. HTTP 状态码精确匹配（401/402/403/404/429/5xx）
    2. 异常类名精确匹配
    3. 错误消息关键词匹配
    4. 兜底 → unknown/internal_error
    """
    diagnostic_id = str(uuid.uuid4())
    status_code = _extract_status_code(exc)
    exc_class_name = type(exc).__name__.lower()
    exc_text = str(exc).lower()

    # ── 1. HTTP 状态码精确匹配 ──

    if status_code == 401 or status_code == 403:
        return FailureGuidance(
            category="model",
            code="model_auth_failed",
            title="模型认证失败",
            message="API Key 无效、已过期或权限不足，请在模型设置中更新。",
            stage=stage,
            retryable=False,
            diagnostic_id=diagnostic_id,
            actions=_actions_for(False),
            provider=provider,
            model=model,
        )

    if status_code == 402:
        return FailureGuidance(
            category="quota",
            code="quota_exceeded",
            title="额度不足",
            message="模型 API 额度已用尽或存在账单问题，请检查服务商账户。",
            stage=stage,
            retryable=False,
            diagnostic_id=diagnostic_id,
            actions=_actions_for(False),
            provider=provider,
            model=model,
        )

    if status_code == 404:
        return FailureGuidance(
            category="model",
            code="model_not_found",
            title="模型不存在",
            message="请求的模型标识无效或已下线，请在设置中确认 Model ID。",
            stage=stage,
            retryable=False,
            diagnostic_id=diagnostic_id,
            actions=_actions_for(False),
            provider=provider,
            model=model,
        )

    if status_code == 429:
        return FailureGuidance(
            category="quota",
            code="rate_limited",
            title="请求频率受限",
            message="模型 API 调用频率超限，请稍后重试。",
            stage=stage,
            retryable=True,
            diagnostic_id=diagnostic_id,
            actions=_actions_for(True),
            provider=provider,
            model=model,
        )

    if status_code is not None and 500 <= status_code < 600:
        return FailureGuidance(
            category="model",
            code="provider_internal_error",
            title="模型服务异常",
            message=f"模型服务返回 {status_code} 错误，请稍后重试。",
            stage=stage,
            retryable=True,
            diagnostic_id=diagnostic_id,
            actions=_actions_for(True),
            provider=provider,
            model=model,
        )

    # ── 2. 异常类名精确匹配 ──

    if "authenticationerror" in exc_class_name:
        return FailureGuidance(
            category="model",
            code="model_auth_failed",
            title="模型认证失败",
            message="API Key 无效或已过期，请在模型设置中更新。",
            stage=stage,
            retryable=False,
            diagnostic_id=diagnostic_id,
            actions=_actions_for(False),
            provider=provider,
            model=model,
        )

    if "ratelimiterror" in exc_class_name:
        return FailureGuidance(
            category="quota",
            code="rate_limited",
            title="请求频率受限",
            message="模型 API 调用频率超限，请稍后重试。",
            stage=stage,
            retryable=True,
            diagnostic_id=diagnostic_id,
            actions=_actions_for(True),
            provider=provider,
            model=model,
        )

    if "notfounderror" in exc_class_name:
        return FailureGuidance(
            category="model",
            code="model_not_found",
            title="模型不存在",
            message="请求的模型标识无效或已下线，请在设置中确认 Model ID。",
            stage=stage,
            retryable=False,
            diagnostic_id=diagnostic_id,
            actions=_actions_for(False),
            provider=provider,
            model=model,
        )

    if any(kw in exc_class_name for kw in (
        "timeouterror", "apitimeouterror", "connecttimeout", "readtimeout",
    )):
        return FailureGuidance(
            category="transport",
            code="connect_timeout",
            title="连接超时",
            message="模型服务响应超时，请稍后重试或检查网络。",
            stage=stage,
            retryable=True,
            diagnostic_id=diagnostic_id,
            actions=_actions_for(True),
            provider=provider,
            model=model,
        )

    if any(kw in exc_class_name for kw in (
        "connectionerror", "apiconnectionerror", "connecterror",
        "connectionrefusederror", "connectionreseterror",
    )):
        return FailureGuidance(
            category="transport",
            code="network_error",
            title="网络连接失败",
            message="无法连接到模型服务，请检查网络或 Base URL 配置。",
            stage=stage,
            retryable=True,
            diagnostic_id=diagnostic_id,
            actions=_actions_for(True),
            provider=provider,
            model=model,
        )

    # ── 3. 关键词匹配 ──

    for keywords, category, code, title, message, retryable in _KEYWORD_RULES:
        if any(kw in exc_text for kw in keywords):
            return FailureGuidance(
                category=category,
                code=code,
                title=title,
                message=message,
                stage=stage,
                retryable=retryable,
                diagnostic_id=diagnostic_id,
                actions=_actions_for(retryable),
                provider=provider,
                model=model,
            )

    # ── 4. 兜底 ──

    return FailureGuidance(
        category="unknown",
        code="internal_error",
        title="内部错误",
        message="服务处理出现异常，请稍后重试。如问题持续，请联系管理员。",
        stage=stage,
        retryable=False,
        diagnostic_id=diagnostic_id,
        actions=[_ACTION_COPY_DIAGNOSTIC],
        provider=provider,
        model=model,
    )


def classify_workspace_full(
    *,
    stage: str = "",
    detail: str = "",
) -> FailureGuidance:
    """工作区配额超限的专用构造（不依赖异常对象）。"""
    return FailureGuidance(
        category="quota",
        code="workspace_full",
        title="工作区已满",
        message=f"工作区配额超限（{detail}），请先清理文件后再试。" if detail else "工作区配额超限，请先清理文件后再试。",
        stage=stage,
        retryable=False,
        diagnostic_id=str(uuid.uuid4()),
        actions=[_ACTION_OPEN_SETTINGS],
    )
