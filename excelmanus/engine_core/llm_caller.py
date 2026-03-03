"""LLM 通信层 — 流式消费、兜底重试、窗口顾问调用。

从 AgentEngine 提取的 LLM API 交互逻辑，包括：
- 流式响应消费与事件发射
- 系统消息兼容性兜底（replace → merge 自动回退）
- 窗口感知小模型顾问调用与瞬时错误重试
- 异常链遍历与 Retry-After 提取
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Sequence
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from excelmanus.engine_utils import (
    _AUX_NO_THINKING_EXTRA_BODY,
    _WINDOW_ADVISOR_RETRY_AFTER_CAP_SECONDS,
    _WINDOW_ADVISOR_RETRY_DELAY_MAX_SECONDS,
    _WINDOW_ADVISOR_RETRY_DELAY_MIN_SECONDS,
    _WINDOW_ADVISOR_RETRY_TIMEOUT_CAP_SECONDS,
    _extract_completion_message,
    _message_content_to_text,
)
from excelmanus.logger import get_logger
from excelmanus.providers.stream_types import InlineThinkingStateMachine, extract_inline_thinking
from excelmanus.window_perception.small_model import build_advisor_messages, parse_small_model_plan

if TYPE_CHECKING:
    from excelmanus.events import EventCallback
    from excelmanus.engine import AgentEngine
    from excelmanus.window_perception import (
        AdvisorContext,
        LifecyclePlan,
        PerceptionBudget,
    )
    from excelmanus.window_perception.domain import Window

logger = get_logger("llm_caller")


def _patch_reasoning_content(messages: list[dict]) -> list[dict]:
    """为所有 assistant 消息补充 reasoning_content 字段（DeepSeek thinking mode 兼容）。"""
    patched = []
    for msg in messages:
        if isinstance(msg, dict) and msg.get("role") == "assistant":
            if "reasoning_content" not in msg:
                msg = dict(msg)
                msg["reasoning_content"] = ""
        patched.append(msg)
    return patched


# ── 纯函数 / 静态工具 ──────────────────────────────────────


def iter_exception_chain(exc: Exception) -> list[Exception]:
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


def is_transient_window_advisor_exception(exc: Exception) -> bool:
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
        "incomplete chunked",
        "incompleteread",
        "remotedisconnected",
        "remote end closed",
        "stream ended",
        "stream interrupted",
        "premature end",
        "response ended prematurely",
        "json decode",
        "jsondecodeerror",
        "expecting value",
    )
    for candidate in iter_exception_chain(exc):
        status_code = getattr(candidate, "status_code", None)
        if isinstance(status_code, int) and (
            status_code == 429 or status_code == 408 or 500 <= status_code < 600
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
            "incompleteread",
            "remotedisconnected",
            "jsondecodeerror",
        }:
            return True

        text = f"{candidate} {candidate!r}".lower()
        if any(keyword in text for keyword in transient_keywords):
            return True

    return False


def extract_retry_after_seconds(exc: Exception) -> float | None:
    """尽量从异常响应头提取 Retry-After（秒）。"""
    for candidate in iter_exception_chain(exc):
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


def window_advisor_retry_delay_seconds(exc: Exception) -> float:
    """计算轻量重试等待时间。"""
    retry_after = extract_retry_after_seconds(exc)
    if retry_after is not None:
        return max(
            _WINDOW_ADVISOR_RETRY_DELAY_MIN_SECONDS,
            min(_WINDOW_ADVISOR_RETRY_AFTER_CAP_SECONDS, retry_after),
        )
    return random.uniform(
        _WINDOW_ADVISOR_RETRY_DELAY_MIN_SECONDS,
        _WINDOW_ADVISOR_RETRY_DELAY_MAX_SECONDS,
    )


def window_advisor_retry_timeout_seconds(primary_timeout_seconds: float) -> float:
    """计算二次快速重试超时，确保短于首轮。"""
    retry_timeout = min(
        _WINDOW_ADVISOR_RETRY_TIMEOUT_CAP_SECONDS,
        max(0.1, float(primary_timeout_seconds) * 0.4),
    )
    if retry_timeout >= primary_timeout_seconds:
        retry_timeout = max(0.1, primary_timeout_seconds - 0.1)
    return retry_timeout


def is_retryable_llm_error(exc: Exception) -> bool:
    """判断 LLM 调用异常是否可安全重试（5xx / 429 / 网络错误）。

    复用 ``is_transient_window_advisor_exception`` 的判定逻辑，
    作为主 LLM 调用重试的公共入口。
    """
    return is_transient_window_advisor_exception(exc)


def is_nonretryable_auth_error(exc: Exception) -> bool:
    """判断是否为鉴权/权限错误（401/403），用于跳过无意义的回退重试。"""
    auth_keywords = (
        "missing scopes",
        "insufficient permissions",
        "permission denied",
        "authentication",
        "unauthorized",
        "forbidden",
        "invalid api key",
    )
    for candidate in iter_exception_chain(exc):
        status_code = getattr(candidate, "status_code", None)
        if isinstance(status_code, int) and status_code in {401, 403}:
            return True

        response = getattr(candidate, "response", None)
        if response is not None:
            resp_status = getattr(response, "status_code", None)
            if isinstance(resp_status, int) and resp_status in {401, 403}:
                return True

        name = candidate.__class__.__name__.lower()
        if any(token in name for token in ("authentication", "unauthorized", "forbidden", "permission")):
            return True

        text = f"{candidate} {candidate!r}".lower()
        if any(keyword in text for keyword in auth_keywords):
            return True

    return False


def compute_retry_delay(
    attempt: int,
    base_delay: float,
    max_delay: float,
    exc: Exception,
) -> float:
    """计算指数退避重试延迟（秒），优先使用 Retry-After 头。

    - attempt: 第几次失败（从 1 开始）
    - base_delay: 基准延迟（秒）
    - max_delay: 单次最大延迟上限（秒）
    - exc: 触发重试的异常
    """
    retry_after = extract_retry_after_seconds(exc)
    if retry_after is not None:
        return min(max_delay, max(1.0, retry_after))
    # 指数退避 + jitter: base * 2^(attempt-1) + 随机 0~1s
    delay = base_delay * (2 ** (attempt - 1)) + random.uniform(0, 1)
    return min(max_delay, delay)


def merge_leading_system_messages(messages: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
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


def is_unsupported_param_error(exc: Exception) -> bool:
    """检测是否为 provider 不支持某参数的错误（如 prompt_cache_key、stream_options）。"""
    text = str(exc).lower()
    keywords = [
        "unexpected keyword",
        "unrecognized request argument",
        "unknown parameter",
        "invalid parameter",
        "prompt_cache_key",
        "stream_options",
        "extra inputs are not permitted",
    ]
    return any(keyword in text for keyword in keywords)


def _is_context_length_error(exc: Exception) -> bool:
    """检测是否为上下文长度超限错误（400 context_length_exceeded 等）。"""
    keywords = (
        "context_length_exceeded",
        "context length",
        "maximum context",
        "token limit",
        "too many tokens",
        "max_tokens",
        "reduce the length",
        "reduce your prompt",
        "request too large",
        "payload too large",
    )
    for candidate in iter_exception_chain(exc):
        text = f"{candidate} {candidate!r}".lower()
        if any(kw in text for kw in keywords):
            return True
    return False


def is_content_filter_error(exc: Exception) -> bool:
    """检测是否为内容安全策略拦截错误。"""
    filter_keywords = (
        "content_filter",
        "content filter",
        "content_policy",
        "content policy violation",
        "content_management_policy",
        "responsible_ai_policy",
        "flagged",
        "blocked by",
        "safety system",
        "harm_category",
    )
    for candidate in iter_exception_chain(exc):
        text = f"{candidate} {candidate!r}".lower()
        if any(kw in text for kw in filter_keywords):
            return True
    return False


def is_system_compatibility_error(exc: Exception) -> bool:
    text = str(exc).lower()
    keywords = [
        "multiple system",
        "at most one system",
        "only one system",
        "system messages",
        "role 'system'",
    ]
    return any(keyword in text for keyword in keywords)


# ── LLMCaller 类 ──────────────────────────────────────────


class LLMCaller:
    """LLM 通信层：流式消费、兜底重试、窗口顾问。

    通过 ``self._engine`` 引用访问 AgentEngine 的客户端和配置。
    """

    def __init__(self, engine: "AgentEngine") -> None:
        self._engine = engine

    # ── 窗口感知顾问 ──────────────────────────────────────

    async def run_window_perception_advisor_async(
        self,
        windows: list["Window"],
        active_window_id: str | None,
        budget: "PerceptionBudget",
        context: "AdvisorContext",
    ) -> "LifecyclePlan | None":
        """异步调用小模型生成窗口生命周期建议。"""
        e = self._engine
        messages = build_advisor_messages(
            windows=windows,
            active_window_id=active_window_id,
            budget=budget,
            context=context,
        )
        timeout_seconds = max(
            0.1,
            int(e._config.window_perception_advisor_timeout_ms) / 1000,
        )

        async def _invoke(timeout: float) -> Any:
            return await asyncio.wait_for(
                e._advisor_client.chat.completions.create(
                    model=e._advisor_model,
                    messages=messages,
                    extra_body=_AUX_NO_THINKING_EXTRA_BODY,
                ),
                timeout=timeout,
            )

        try:
            response = await _invoke(timeout_seconds)
        except asyncio.TimeoutError:
            logger.info("窗口感知小模型调用超时（%.2fs）", timeout_seconds)
            return None
        except Exception as exc:
            if not is_transient_window_advisor_exception(exc):
                logger.warning("窗口感知小模型调用失败，已回退规则顾问", exc_info=True)
                return None

            retry_delay = window_advisor_retry_delay_seconds(exc)
            retry_timeout = window_advisor_retry_timeout_seconds(timeout_seconds)
            logger.info(
                "窗口感知小模型触发瞬时错误，%.2fs 后执行一次快速重试（%.2fs）：%s",
                retry_delay,
                retry_timeout,
                exc.__class__.__name__,
            )
            await asyncio.sleep(retry_delay)
            try:
                response = await _invoke(retry_timeout)
            except asyncio.TimeoutError:
                logger.info("窗口感知小模型快速重试超时（%.2fs）", retry_timeout)
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

    # ── 流式消费 ──────────────────────────────────────────

    async def consume_stream(
        self,
        stream: Any,
        on_event: "EventCallback | None",
        iteration: int,
        *,
        _llm_start_ts: float | None = None,
    ) -> tuple[Any, Any]:
        """消费流式响应，逐 chunk 发射 delta 事件，返回累积的 (message, usage)。

        兼容两种 chunk 格式：
        - openai.AsyncOpenAI: ChatCompletionChunk (choices[0].delta)
        - 自定义 provider: _StreamDelta (content_delta / thinking_delta)
        """
        from excelmanus.events import EventType, ToolCallEvent

        e = self._engine
        content_parts: list[str] = []
        thinking_parts: list[str] = []
        _thinking_streamed = False  # 标记是否已通过 THINKING_DELTA 流式发射过
        tool_calls_accumulated: dict[int, dict] = {}
        finish_reason: str | None = None
        usage = None
        _tool_call_notified = False
        _inline_sm = InlineThinkingStateMachine()  # 内联 <thinking> 标签检测
        _first_token_received = False
        _ttft_ms: float = 0.0

        _consecutive_chunk_errors = 0
        _max_chunk_errors = 3
        async for chunk in stream:
            try:
                # ── TTFT 计时：记录首个有效内容 token 的到达时间 ──
                if not _first_token_received and _llm_start_ts is not None:
                    _has_content = False
                    if hasattr(chunk, "content_delta"):
                        _has_content = bool(chunk.content_delta or chunk.thinking_delta)
                    else:
                        _choices = getattr(chunk, "choices", None)
                        if _choices:
                            _d = getattr(_choices[0], "delta", None)
                            if _d and (getattr(_d, "content", None) or getattr(_d, "thinking", None)):
                                _has_content = True
                    if _has_content:
                        _first_token_received = True
                        _ttft_ms = (time.monotonic() - _llm_start_ts) * 1000

                # ── 自定义 provider 的 _StreamDelta ──
                if hasattr(chunk, "content_delta"):
                    if chunk.content_delta:
                        content_parts.append(chunk.content_delta)
                        e._emit(on_event, ToolCallEvent(
                            event_type=EventType.TEXT_DELTA,
                            text_delta=chunk.content_delta,
                            iteration=iteration,
                        ))
                    if chunk.thinking_delta:
                        thinking_parts.append(chunk.thinking_delta)
                        _thinking_streamed = True
                        e._emit(on_event, ToolCallEvent(
                            event_type=EventType.THINKING_DELTA,
                            thinking_delta=chunk.thinking_delta,
                            iteration=iteration,
                        ))
                    if chunk.tool_calls_delta:
                        if not _tool_call_notified:
                            _tool_call_notified = True
                            e._emit(on_event, ToolCallEvent(
                                event_type=EventType.PIPELINE_PROGRESS,
                                pipeline_stage="generating_tool_call",
                                pipeline_message="正在生成工具调用...",
                            ))
                        for tc in chunk.tool_calls_delta:
                            idx = tc.get("index", 0)
                            tool_calls_accumulated[idx] = tc
                    if chunk.finish_reason:
                        finish_reason = chunk.finish_reason
                    if chunk.usage:
                        usage = chunk.usage
                    _consecutive_chunk_errors = 0
                    continue

                # ── openai.AsyncOpenAI 的 ChatCompletionChunk ──
                choices = getattr(chunk, "choices", None)
                if not choices:
                    chunk_usage = getattr(chunk, "usage", None)
                    if chunk_usage:
                        usage = chunk_usage
                    _consecutive_chunk_errors = 0
                    continue

                delta = getattr(choices[0], "delta", None)
                if delta is None:
                    _consecutive_chunk_errors = 0
                    continue

                delta_content = getattr(delta, "content", None)
                if delta_content:
                    # 通过状态机检测内联 <thinking> 标签
                    for _sd in _inline_sm.feed(delta_content):
                        if _sd.thinking_delta:
                            thinking_parts.append(_sd.thinking_delta)
                            _thinking_streamed = True
                            e._emit(on_event, ToolCallEvent(
                                event_type=EventType.THINKING_DELTA,
                                thinking_delta=_sd.thinking_delta,
                                iteration=iteration,
                            ))
                        if _sd.content_delta:
                            content_parts.append(_sd.content_delta)
                            e._emit(on_event, ToolCallEvent(
                                event_type=EventType.TEXT_DELTA,
                                text_delta=_sd.content_delta,
                                iteration=iteration,
                            ))

                for thinking_key in ("thinking", "reasoning", "reasoning_content"):
                    thinking_val = getattr(delta, thinking_key, None)
                    if thinking_val:
                        thinking_parts.append(str(thinking_val))
                        _thinking_streamed = True
                        e._emit(on_event, ToolCallEvent(
                            event_type=EventType.THINKING_DELTA,
                            thinking_delta=str(thinking_val),
                            iteration=iteration,
                        ))
                        break

                delta_tool_calls = getattr(delta, "tool_calls", None)
                if delta_tool_calls:
                    if not _tool_call_notified:
                        _tool_call_notified = True
                        e._emit(on_event, ToolCallEvent(
                            event_type=EventType.PIPELINE_PROGRESS,
                            pipeline_stage="generating_tool_call",
                            pipeline_message="正在生成工具调用...",
                        ))
                    _TEXT_STREAMING_TOOLS = {"write_text_file", "edit_text_file", "write_plan"}
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
                                # 为文本写入工具发射流式参数 delta 事件
                                _tc_name = tool_calls_accumulated[idx]["name"]
                                if _tc_name in _TEXT_STREAMING_TOOLS:
                                    e._emit(on_event, ToolCallEvent(
                                        event_type=EventType.TOOL_CALL_ARGS_DELTA,
                                        tool_call_id=tool_calls_accumulated[idx]["id"],
                                        tool_name=_tc_name,
                                        args_delta=args,
                                        iteration=iteration,
                                    ))
                        tc_id = getattr(tc_delta, "id", None)
                        if tc_id:
                            tool_calls_accumulated[idx]["id"] = tc_id

                chunk_finish = getattr(choices[0], "finish_reason", None)
                if chunk_finish:
                    finish_reason = chunk_finish

                chunk_usage = getattr(chunk, "usage", None)
                if chunk_usage:
                    usage = chunk_usage

                _consecutive_chunk_errors = 0
            except Exception as _chunk_exc:
                _consecutive_chunk_errors += 1
                if _consecutive_chunk_errors >= _max_chunk_errors:
                    logger.warning(
                        "流式消费连续 %d 个 chunk 解析失败，中止: %s",
                        _consecutive_chunk_errors, _chunk_exc,
                    )
                    break
                logger.debug("流式 chunk 解析异常（已跳过）: %s", _chunk_exc)
                continue

        # 组装为与非流式路径兼容的 message 对象
        content = "".join(content_parts)
        thinking = "".join(thinking_parts)

        tool_calls_list = []
        if tool_calls_accumulated:
            for idx in sorted(tool_calls_accumulated.keys()):
                tc = tool_calls_accumulated[idx]
                tool_calls_list.append(SimpleNamespace(
                    id=tc["id"],
                    type="function",
                    function=SimpleNamespace(
                        name=tc["name"],
                        arguments=tc["arguments"],
                    ),
                ))

        message = SimpleNamespace(
            content=content,
            tool_calls=tool_calls_list or None,
            thinking=thinking if thinking else None,
            reasoning=thinking if thinking else None,
            reasoning_content=thinking if thinking else None,
            _thinking_streamed=_thinking_streamed,
            _stream_truncated=_consecutive_chunk_errors >= _max_chunk_errors,
        )

        # 附加 TTFT 和 cache 统计到 usage（供 TurnDiagnostic 提取）
        if usage is not None:
            if _ttft_ms > 0:
                # 动态附加 ttft_ms 属性
                if isinstance(usage, dict):
                    usage["_ttft_ms"] = round(_ttft_ms, 1)
                else:
                    usage._ttft_ms = round(_ttft_ms, 1)  # type: ignore[attr-defined]

        return message, usage

    # ── LLM 调用兜底 ──────────────────────────────────────

    async def create_chat_completion_with_system_fallback(
        self,
        kwargs: dict[str, Any],
    ) -> Any:
        e = self._engine
        # 过滤 SDK 不兼容参数：_thinking_* / prompt_cache_key / stream_options
        # 这些参数在旧版 openai SDK 中会直接触发 TypeError，
        # 必须在首次调用前移除，而非依赖 retry 路径。
        _strip_keys = {k for k in kwargs if k.startswith("_thinking")}
        _strip_keys |= {"prompt_cache_key", "stream_options"} & set(kwargs)
        if _strip_keys:
            kwargs = {k: v for k, v in kwargs.items() if k not in _strip_keys}
        try:
            return await e._client.chat.completions.create(**kwargs)
        except Exception as exc:

            # 404 路由错误诊断：最常见原因是 base_url 路径不正确
            _exc_text_lower = str(exc).lower()
            _status = getattr(exc, "status_code", None)
            if _status == 404 or (
                "notfounderror" in type(exc).__name__.lower()
                and any(kw in _exc_text_lower for kw in ("route", "completions not found", "endpoint"))
            ):
                _client_base = getattr(getattr(e, "_client", None), "base_url", None)
                logger.error(
                    "404 诊断: 模型服务返回路由不存在。"
                    "当前 base_url=%s, model=%s。"
                    "OpenAI 兼容 API 的 Base URL 通常应以 /v1 结尾，"
                    "请检查 EXCELMANUS_BASE_URL 配置。",
                    _client_base, e._config.model,
                )

            # DeepSeek thinking mode: assistant 消息必须包含 reasoning_content 字段
            if "reasoning_content" in str(exc).lower():
                source_messages = kwargs.get("messages")
                if isinstance(source_messages, list):
                    logger.warning("检测到 reasoning_content 缺失，自动补全后重试")
                    patched = _patch_reasoning_content(source_messages)
                    retry_kwargs = dict(kwargs)
                    retry_kwargs["messages"] = patched
                    retry_kwargs.pop("prompt_cache_key", None)
                    return await e._client.chat.completions.create(**retry_kwargs)

            # 上下文超长自动恢复：自适应缩减预算 + 紧急截断对话历史后重试一次
            if _is_context_length_error(exc):
                # 自适应缩减：当前预算可能偏大，缩减 20% 防止后续轮次再次超限
                _ctx_budget = getattr(e, "_context_budget", None)
                if _ctx_budget is not None and not _ctx_budget.is_user_overridden:
                    _old_budget = _ctx_budget.max_tokens
                    _new_budget = max(4096, int(_old_budget * 0.8))
                    _ctx_budget.set_override(_new_budget, adaptive=True)
                    logger.warning(
                        "上下文超限，自动缩减预算 %d → %d tokens（-20%%）",
                        _old_budget, _new_budget,
                    )
                    # 同步更新 memory 和 compaction 的阈值
                    if hasattr(e, "_memory"):
                        e._memory.update_context_window(_new_budget)
                    _cm = getattr(e, "_compaction_manager", None)
                    if _cm is not None:
                        _cm.max_context_tokens = _new_budget

                source_messages = kwargs.get("messages")
                if isinstance(source_messages, list) and len(source_messages) > 3:
                    logger.warning(
                        "检测到上下文超长错误（%d 条消息），紧急截断后重试",
                        len(source_messages),
                    )
                    # 保留 system 消息 + 最后 ~1/3 的非 system 消息
                    sys_msgs = [m for m in source_messages if m.get("role") == "system"]
                    non_sys = [m for m in source_messages if m.get("role") != "system"]
                    keep = max(2, len(non_sys) // 3)
                    trimmed = sys_msgs + non_sys[-keep:]
                    retry_kwargs = dict(kwargs)
                    retry_kwargs["messages"] = trimmed
                    retry_kwargs.pop("prompt_cache_key", None)
                    return await e._client.chat.completions.create(**retry_kwargs)

            if (
                e._config.system_message_mode == "auto"
                and e._effective_system_mode() == "replace"
                and is_system_compatibility_error(exc)
            ):
                logger.warning("检测到 replace(system 分段) 兼容性错误，自动回退到 merge 模式")
                type(e)._system_mode_fallback_cache[e._system_mode_cache_key] = "merge"
                e._system_mode_fallback = "merge"
                source_messages = kwargs.get("messages")
                if not isinstance(source_messages, list):
                    raise
                merged_messages = merge_leading_system_messages(source_messages)
                retry_kwargs = dict(kwargs)
                retry_kwargs["messages"] = merged_messages
                # 同样移除可能不支持的 prompt_cache_key
                retry_kwargs.pop("prompt_cache_key", None)
                return await e._client.chat.completions.create(**retry_kwargs)
            raise
