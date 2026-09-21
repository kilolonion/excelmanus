"""LLM 通信层 — 流式消费、兜底重试。

从 AgentEngine 提取的 LLM API 交互逻辑，包括：
- 流式响应消费与事件发射
- 异常链遍历与 Retry-After 提取
"""

from __future__ import annotations

import random
import time
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from excelmanus.logger import get_logger
from excelmanus.providers.stream_types import InlineThinkingStateMachine

if TYPE_CHECKING:
    from excelmanus.events import EventCallback
    from excelmanus.engine import AgentEngine

logger = get_logger("llm_caller")

_DEGRADED_PARAMS: dict[tuple[str, str], set[str]] = {}
_DEGRADED_WARNED: set[tuple[str, str, str]] = set()
_DEGRADE_SUSPECT_KEYS = frozenset({
    "prompt_cache_key", "stream_options",
    "top_logprobs", "logprobs", "parallel_tool_calls",
    "service_tier",
})


def _degrade_bucket(protocol: str, model: str) -> tuple[str, str]:
    return (str(protocol or ""), str(model or ""))


def degraded_params(protocol: str, model: str) -> frozenset[str]:
    """本会话已对 (protocol, model) 降级、不再试探的出网参数。"""
    return frozenset(_DEGRADED_PARAMS.get(_degrade_bucket(protocol, model), ()))


def mark_degraded(protocol: str, model: str, param: str) -> None:
    """记录 (protocol, model) 已降级参数；warning 每个三元组只打一次。"""
    name = str(param or "").strip()
    if not name:
        return
    key = _degrade_bucket(protocol, model)
    _DEGRADED_PARAMS.setdefault(key, set()).add(name)
    warn_key = (key[0], key[1], name)
    if warn_key in _DEGRADED_WARNED:
        return
    _DEGRADED_WARNED.add(warn_key)
    logger.warning(
        "出网参数已降级，本会话不再试探: protocol=%s model=%s param=%s",
        protocol,
        model,
        name,
    )


def reset_degraded_params() -> None:
    """测试辅助：清空粘性降级记录。"""
    _DEGRADED_PARAMS.clear()
    _DEGRADED_WARNED.clear()


def _protocol_of_engine(engine: Any) -> str:
    from excelmanus.prompt.envelope import protocol_from_engine

    return protocol_from_engine(engine)


def _model_of_kwargs(engine: Any, kwargs: dict[str, Any]) -> str:
    model = kwargs.get("model")
    if isinstance(model, str) and model.strip():
        return model.strip()
    config = getattr(engine, "_config", None)
    return str(getattr(engine, "_active_model", None) or getattr(config, "model", "") or "")


def _strip_degraded(kwargs: dict[str, Any], protocol: str, model: str) -> dict[str, Any]:
    skip = degraded_params(protocol, model)
    if not skip:
        return kwargs
    return {key: value for key, value in kwargs.items() if key not in skip}


async def _recompile_after_degrade(
    engine: Any,
    current: dict[str, Any],
    stripped: set[str],
    protocol: str,
    model: str,
    *,
    original: dict[str, Any],
) -> dict[str, Any]:
    """Unsupported params go back through compile; strip only if compile cannot run."""
    from excelmanus.request.compiler import compile_request
    from excelmanus.request.series import series_of

    series_of(engine).note("request/degrade", params=sorted(stripped))
    prepared = None
    try:
        prepared, error = await compile_request(
            engine,
            extra=getattr(engine, "_compile_extra", None),
        )
        if error is not None:
            prepared = None
    except Exception:
        prepared = None
    if prepared is None:
        from excelmanus.request.types import PreparedRequest

        if isinstance(getattr(engine, "_prepared_request", None), PreparedRequest):
            raise ValueError("请求重编译失败，不能沿旧载荷重试")
        return {key: value for key, value in current.items() if key not in stripped}
    retry = _strip_degraded(prepared.create_kwargs(), protocol, model)
    if original.get("stream"):
        retry["stream"] = True
    skip = degraded_params(protocol, model)
    if "stream_options" in original and "stream_options" not in skip:
        retry["stream_options"] = original["stream_options"]
    return retry


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


def is_retryable_llm_error(exc: Exception) -> bool:
    """判断 LLM 调用异常是否可安全重试（5xx / 429 / 网络错误）。"""
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
        }:
            return True

        if name == "jsondecodeerror":
            if {"incompleteread", "remotedisconnected", "connectionerror", "connecterror", "timeouterror", "apitimeouterror"} & {
                c.__class__.__name__.lower() for c in iter_exception_chain(exc)
            }:
                return True
            continue

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
    strong_keywords = (
        "context_length_exceeded",
        "context length",
        "maximum context",
        "context window",
        "too many tokens",
        "reduce the length",
        "reduce your prompt",
    )
    loose_keywords = (
        "token limit",
        "request too large",
        "payload too large",
    )
    for candidate in iter_exception_chain(exc):
        text = f"{candidate} {candidate!r}".lower()
        if any(kw in text for kw in strong_keywords):
            return True
        # 只有同时提到 prompt/context/history，才把宽泛的 size 错误看作上下文超限。
        if any(kw in text for kw in loose_keywords) and any(
            marker in text for marker in ("prompt", "context", "history")
        ):
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


# ── LLMCaller 类 ──────────────────────────────────────────


class LLMCaller:
    """LLM 通信层：流式消费、兜底重试。

    通过 ``self._engine`` 引用访问 AgentEngine 的客户端和配置。
    """

    def __init__(self, engine: "AgentEngine") -> None:
        self._engine = engine

    async def _send_attempt(self, **kwargs: Any) -> Any:
        from excelmanus.request.types import PreparedRequest
        from excelmanus.attachments.files_api import lease_file_ids, release_file_ids

        e = self._engine
        prepared = getattr(e, "_prepared_request", None)
        if isinstance(prepared, PreparedRequest):
            expected = prepared.create_kwargs()
            # 粘性降级参数在发送口剥离（_strip_degraded），比较时同样排除，
            # 否则降级标记后的每次编译尝试都会被守卫误判。
            skip = set(degraded_params(prepared.route.protocol, prepared.route.model))
            skip |= set(degraded_params(prepared.route.protocol_label(), prepared.route.model))
            if any(
                kwargs.get(key) != value
                for key, value in expected.items()
                if key not in skip
            ):
                raise ValueError("outbound request differs from compiled attempt")
            old_id = getattr(e, "_open_request_id", None)
            if old_id and old_id != prepared.request_id:
                release_file_ids(old_id)
            lease_file_ids(prepared.request_id, prepared.file_leases)
            e._open_request_id = prepared.request_id
            e._sent_prepared_request = prepared
        from excelmanus.trace import traced_request

        previous = getattr(e, "_last_model_response_at", None)
        e._model_idle_seconds = max(0.0, time.monotonic() - previous) if isinstance(previous, (int, float)) else None
        return await traced_request(e, e._client.chat.completions.create, kwargs)

    # ── 流式消费 ──────────────────────────────────────────

    async def consume_stream(
        self, stream: Any, on_event: "EventCallback | None", iteration: int,
        *, _llm_start_ts: float | None = None,
    ) -> tuple[Any, Any]:
        try:
            return await self._consume_stream(stream, on_event, iteration, _llm_start_ts=_llm_start_ts)
        finally:
            import inspect

            close = getattr(stream, "aclose", None) or getattr(stream, "close", None)
            if callable(close):
                try:
                    closed = close()
                    if inspect.isawaitable(closed):
                        await closed
                except Exception:
                    logger.debug("stream close failed", exc_info=True)

    async def _consume_stream(
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
        replay_state = None
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
                    if getattr(chunk, "replay_state", None) is not None:
                        replay_state = chunk.replay_state
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
            replay_state=replay_state,
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

    async def create_chat_completion_with_retry(
        self,
        kwargs: dict[str, Any],
    ) -> Any:
        e = self._engine
        # 只剥内部 _thinking_*；prompt_cache_key / stream_options 必须首次出网，
        # 旧 SDK TypeError 或 provider 拒参再走下面的剥离重试。
        _strip_keys = {k for k in kwargs if k.startswith("_thinking")}
        if _strip_keys:
            kwargs = {k: v for k, v in kwargs.items() if k not in _strip_keys}
        protocol = _protocol_of_engine(e)
        model = _model_of_kwargs(e, kwargs)
        kwargs = _strip_degraded(kwargs, protocol, model)
        try:
            return await self._send_attempt(**kwargs)
        except Exception as exc:

            native = kwargs.get("_prepared_body") or {}
            if (isinstance(native, dict) and native.get("previous_response_id")
                    and "previous_response_id" in str(exc).lower()
                    and any(word in str(exc).lower() for word in ("not found", "expired", "invalid", "unsupported"))):
                from excelmanus.request.compiler import compile_request

                extra = dict(getattr(e, "_compile_extra", None) or {})
                extra.pop("_responses_previous_response_id", None)
                extra.pop("previous_response_id", None)
                if isinstance(extra.get("extra_body"), dict):
                    extra["extra_body"] = {k: v for k, v in extra["extra_body"].items() if k != "previous_response_id"}
                prepared, error = await compile_request(e, extra=extra)
                if error is not None or prepared is None:
                    raise
                retry = prepared.create_kwargs()
                if kwargs.get("stream"):
                    retry["stream"] = True
                return await self._send_attempt(**retry)

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

            if "reasoning_content" in str(exc).lower():
                from excelmanus.request.compiler import compile_request

                logger.warning("reasoning_content 被拒，回到编译口重试（不补空串）")
                prepared, err = await compile_request(
                    e,
                    extra=getattr(e, "_compile_extra", None),
                )
                if err is not None or prepared is None:
                    raise
                retry_kwargs = _strip_degraded(prepared.create_kwargs(), protocol, model)
                return await self._send_attempt(**retry_kwargs)

            # W5: 不支持参数错误 → 分轮剥离可疑参数后重试，并粘性记录，
            # 同一 (protocol, model) 后续请求不再试探。
            if is_unsupported_param_error(exc):
                retry_kwargs = dict(kwargs)
                last_exc: Exception = exc
                while True:
                    present = [k for k in _DEGRADE_SUSPECT_KEYS if k in retry_kwargs]
                    if not present:
                        raise last_exc
                    text = str(last_exc).lower()
                    mentioned = [k for k in present if k in text]
                    stripped = set(mentioned or present)
                    for name in sorted(stripped):
                        mark_degraded(protocol, model, name)
                    retry_kwargs = await _recompile_after_degrade(
                        e,
                        retry_kwargs,
                        stripped,
                        protocol,
                        model,
                        original=kwargs,
                    )
                    try:
                        return await self._send_attempt(**retry_kwargs)
                    except Exception as retry_exc:
                        if not is_unsupported_param_error(retry_exc):
                            raise
                        last_exc = retry_exc

            # 溢出走 request-error：只有 surface 代数推进才重试。
            if _is_context_length_error(exc):
                from excelmanus.compaction import recover_request_overflow

                _ctx_budget = getattr(e, "_context_budget", None)
                if _ctx_budget is not None and not _ctx_budget.is_user_overridden:
                    _old_budget = _ctx_budget.max_tokens
                    _new_budget = max(4096, int(_old_budget * 0.8))
                    _ctx_budget.set_override(_new_budget, adaptive=True)
                    logger.warning(
                        "上下文超限，自动缩减预算 %d → %d tokens（-20%%）",
                        _old_budget, _new_budget,
                    )
                    if hasattr(e, "_memory"):
                        e._memory.update_context_window(_new_budget)
                    _cm = getattr(e, "_compaction_manager", None)
                    if _cm is not None:
                        _cm.max_context_tokens = _new_budget

                recovered = await recover_request_overflow(
                    e,
                    kwargs.get("messages") if isinstance(kwargs.get("messages"), list) else None,
                )
                if recovered is None:
                    raise
                logger.warning("request-error：surface 已推进，使用重编译请求重试")
                retry_kwargs = _strip_degraded(recovered.create_kwargs(), protocol, model)
                return await self._send_attempt(**retry_kwargs)

            raise
