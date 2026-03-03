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
    # context length exceeded
    (
        (
            "context_length_exceeded", "context length", "maximum context",
            "token limit", "too many tokens", "max_tokens",
            "reduce the length", "reduce your prompt",
            "request too large", "payload too large",
        ),
        "model", "context_length_exceeded",
        "上下文超长", "对话历史超出模型上下文窗口限制，请尝试 /compact 压缩上下文或开始新会话。",
        False,
    ),
    # invalid request body (400)
    (
        (
            "invalid request", "invalid_request_error", "bad request",
            "invalid message", "invalid value", "invalid type",
            "expected an object", "expected a string",
            "unrecognized request argument", "extra inputs are not permitted",
        ),
        "model", "invalid_request",
        "请求格式错误", "发送给模型的请求格式有误，请检查模型配置或尝试 /compact 压缩上下文。",
        False,
    ),
    # proxy (must be before network_error to avoid "connection refused" matching first)
    (
        (
            "proxy error", "proxy connection",
            "proxy authentication", "http_proxy", "https_proxy",
            "tunnel connection failed",
        ),
        "transport", "proxy_error",
        "代理连接失败", "通过代理连接模型服务失败，请检查代理配置。",
        True,
    ),
    # disk / filesystem (must be before quota to avoid "disk quota" matching "quota")
    (
        (
            "no space left", "disk full", "disk quota",
            "not enough space", "磁盘空间不足",
            "errno 28", "errno 122",
        ),
        "config", "disk_full",
        "磁盘空间不足", "服务器磁盘空间不足，请清理文件后重试。",
        False,
    ),
    # permission denied (filesystem, must be before auth to avoid "access denied" matching auth)
    (
        (
            "permission denied", "operation not permitted",
            "errno 13",
        ),
        "config", "permission_denied",
        "权限不足", "文件系统操作权限不足，请检查工作区目录权限设置。",
        False,
    ),
    # content filter / safety (must be before auth to avoid "flagged" false positives)
    (
        (
            "content_filter", "content filter", "content_policy",
            "content policy violation", "content_management_policy",
            "responsible_ai_policy", "flagged", "blocked by",
            "safety system", "harm_category",
        ),
        "model", "content_filtered",
        "内容审查拦截", "请求或回复触发了模型的内容安全策略，请调整输入内容后重试。",
        False,
    ),
    # encoding (must be before generic errors)
    (
        (
            "unicodedecodeerror", "unicodeencodeerror",
            "codec can't decode", "codec can't encode",
            "charmap", "invalid start byte", "invalid continuation byte",
        ),
        "config", "encoding_error",
        "编码错误", "数据编码异常，可能是文件编码不兼容，请检查输入文件。",
        False,
    ),
    # overloaded
    (
        ("overloaded", "capacity", "service unavailable", "temporarily unavailable"),
        "model", "model_overloaded",
        "模型服务过载", "模型服务暂时不可用，请稍后重试。",
        True,
    ),
    # SSL / TLS
    (
        (
            "ssl error", "ssl:", "sslerror", "[ssl]",
            "certificate verify failed", "certificate_verify_failed",
            "sslcertverificationerror", "ssl handshake", "tlsv1",
            "ssl_error", "ssl_cert", "ssl certificate",
        ),
        "transport", "ssl_error",
        "SSL/TLS 错误", "与模型服务的安全连接失败，请检查 Base URL 或证书配置。",
        False,
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
    # JSON decode / response parse
    (
        (
            "json decode", "jsondecodeerror", "expecting value",
            "unterminated string", "invalid json",
            "not valid json", "json parse error",
        ),
        "transport", "response_parse_error",
        "响应解析失败", "模型服务返回了无法解析的数据，请稍后重试。",
        True,
    ),
    # stream interruption
    (
        (
            "incomplete chunked", "incompleteread",
            "stream ended", "stream interrupted",
            "premature end", "response ended prematurely",
            "remotedisconnected", "remote end closed",
        ),
        "transport", "stream_interrupted",
        "流式传输中断", "模型服务的响应传输中断，请重试。",
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
        # 区分 "路由不存在"（base URL 路径错误）和 "模型不存在"（模型 ID 错误）
        _route_keywords = ("route", "completions not found", "endpoint not found", "path not found", "not found")
        _model_keywords = ("model", "deployment")
        _is_route_error = any(kw in exc_text for kw in _route_keywords) and not any(kw in exc_text for kw in _model_keywords)
        if _is_route_error:
            return FailureGuidance(
                category="config",
                code="base_url_misconfigured",
                title="API 路径错误",
                message=(
                    "模型服务返回 404，通常是 Base URL 路径不正确。"
                    "OpenAI 兼容 API 的 Base URL 应以 /v1 结尾，请在设置中检查。"
                ),
                stage=stage,
                retryable=False,
                diagnostic_id=diagnostic_id,
                actions=_actions_for(False),
                provider=provider,
                model=model,
            )
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

    if status_code == 408:
        return FailureGuidance(
            category="transport",
            code="request_timeout",
            title="请求超时",
            message="模型服务处理请求超时，请稍后重试或缩短输入内容。",
            stage=stage,
            retryable=True,
            diagnostic_id=diagnostic_id,
            actions=_actions_for(True),
            provider=provider,
            model=model,
        )

    if status_code == 413:
        return FailureGuidance(
            category="model",
            code="payload_too_large",
            title="请求体过大",
            message="请求数据超出模型服务限制，请减少输入内容或使用 /compact 压缩上下文。",
            stage=stage,
            retryable=False,
            diagnostic_id=diagnostic_id,
            actions=_actions_for(False),
            provider=provider,
            model=model,
        )

    if status_code == 422:
        return FailureGuidance(
            category="model",
            code="invalid_request",
            title="请求参数无效",
            message="模型服务无法处理当前请求参数，请检查模型配置。",
            stage=stage,
            retryable=False,
            diagnostic_id=diagnostic_id,
            actions=_actions_for(False),
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

    # 会话相关错误（SSE 流内部捕获时全局 exception_handler 不生效）
    if "sessionbusyerror" in exc_class_name:
        return FailureGuidance(
            category="transport",
            code="session_busy",
            title="会话忙碌",
            message="当前会话正在处理另一个请求，请等待完成后再试。",
            stage=stage,
            retryable=True,
            diagnostic_id=diagnostic_id,
            actions=_actions_for(True),
            provider=provider,
            model=model,
        )

    if "sessionlimitexceedederror" in exc_class_name:
        return FailureGuidance(
            category="quota",
            code="session_limit",
            title="会话数量超限",
            message="系统会话数量已达上限，请关闭不需要的会话或稍后再试。",
            stage=stage,
            retryable=True,
            diagnostic_id=diagnostic_id,
            actions=_actions_for(True),
            provider=provider,
            model=model,
        )

    if "sessionnotfounderror" in exc_class_name:
        return FailureGuidance(
            category="config",
            code="session_not_found",
            title="会话不存在",
            message="会话已过期或被清理，请刷新页面开始新对话。",
            stage=stage,
            retryable=False,
            diagnostic_id=diagnostic_id,
            actions=[_ACTION_COPY_DIAGNOSTIC],
            provider=provider,
            model=model,
        )

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

    if any(kw in exc_class_name for kw in (
        "sslerror", "sslcertverificationerror", "certificateerror",
    )):
        return FailureGuidance(
            category="transport",
            code="ssl_error",
            title="SSL/TLS 错误",
            message="与模型服务的安全连接失败，请检查 Base URL 或网络代理证书配置。",
            stage=stage,
            retryable=False,
            diagnostic_id=diagnostic_id,
            actions=_actions_for(False),
            provider=provider,
            model=model,
        )

    if any(kw in exc_class_name for kw in (
        "proxyerror", "proxytimeout",
    )):
        return FailureGuidance(
            category="transport",
            code="proxy_error",
            title="代理连接失败",
            message="通过代理连接模型服务失败，请检查代理配置。",
            stage=stage,
            retryable=True,
            diagnostic_id=diagnostic_id,
            actions=_actions_for(True),
            provider=provider,
            model=model,
        )

    if any(kw in exc_class_name for kw in (
        "jsondecode", "jsondecodeerror",
    )):
        return FailureGuidance(
            category="transport",
            code="response_parse_error",
            title="响应解析失败",
            message="模型服务返回了无法解析的数据，可能是服务暂时异常，请稍后重试。",
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
