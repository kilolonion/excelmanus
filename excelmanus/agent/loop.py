"""Step 循环体：模型流 + 工具批。Driver.step 调用这里。"""

from __future__ import annotations

import asyncio
import time
from dataclasses import replace
from itertools import count
from typing import Any

import openai

from excelmanus.engine_core.llm_caller import (
    compute_retry_delay,
    is_content_filter_error,
    is_nonretryable_auth_error,
    is_retryable_llm_error,
)
from excelmanus.engine_types import (
    ApprovalResolver,
    ChatResult,
    QuestionResolver,
    ToolCallResult,
    TurnDiagnostic,
    _ToolCallBatch,
)
from excelmanus.engine_utils import (
    _extract_anthropic_cache_tokens,
    _extract_cached_tokens,
    _extract_completion_message,
    _extract_ttft_ms,
    _looks_like_html_document,
    _message_content_to_text,
    _normalize_tool_calls,
    _summarize_text,
    _usage_token,
    build_mention_context_block,
)
from excelmanus.error_guidance import classify_failure as _classify_failure
from excelmanus.events import EventCallback, EventType, ToolCallEvent
from excelmanus.interaction import DEFAULT_INTERACTION_TIMEOUT
from excelmanus.logger import get_logger
from excelmanus.message_serialization import (
    assistant_message_to_dict as _assistant_message_to_dict,
    to_plain as _to_plain,
)
from excelmanus.skillpacks import SkillMatchResult

logger = get_logger("agent.loop")


def _failure_guidance_event(guidance: Any) -> ToolCallEvent:
    return ToolCallEvent(
        event_type=EventType.FAILURE_GUIDANCE,
        fg_category=guidance.category,
        fg_code=guidance.code,
        fg_title=guidance.title,
        fg_message=guidance.message,
        fg_stage=guidance.stage,
        fg_retryable=guidance.retryable,
        fg_diagnostic_id=guidance.diagnostic_id,
        fg_actions=guidance.actions,
        fg_provider=guidance.provider,
        fg_model=guidance.model,
    )

def _handle_text_reply(
    engine: Any,
    *,
    message: Any,
    iteration: int,
    all_tool_results: list,
    total_prompt_tokens: int,
    total_completion_tokens: int,
    _finalize_result: Any,
) -> tuple[str, Any]:
    """处理 LLM 返回纯文本（无 tool_calls）的情况。

    纯文本一律结束本轮。仅保留 HTML 整页响应检测：那是 LLM 客户端
    配置错误（base_url 指到了网页），不是对回复内容的行为判断。
    """
    reply_text = _message_content_to_text(getattr(message, "content", None))

    if _looks_like_html_document(reply_text):
        error_reply = engine._format_html_endpoint_error(reply_text)
        engine._memory.add_assistant_message(error_reply)
        engine._last_iteration_count = iteration
        logger.error(
            "检测到疑似 HTML 页面响应，base_url=%s，已返回配置提示",
            engine._config.base_url,
        )
        logger.info("最终结果摘要: %s", _summarize_text(error_reply))
        return "return", _finalize_result(
            reply=error_reply,
            tool_calls=list(all_tool_results),
            iterations=iteration,
            truncated=False,
            prompt_tokens=total_prompt_tokens,
            completion_tokens=total_completion_tokens,
            total_tokens=total_prompt_tokens + total_completion_tokens,
        )

    engine._memory.add_assistant_message(reply_text)
    engine._last_iteration_count = iteration
    logger.info("最终结果摘要: %s", _summarize_text(reply_text))
    return "return", _finalize_result(
        reply=reply_text,
        tool_calls=list(all_tool_results),
        iterations=iteration,
        truncated=False,
        prompt_tokens=total_prompt_tokens,
        completion_tokens=total_completion_tokens,
        total_tokens=total_prompt_tokens + total_completion_tokens,
    )


async def run_tool_loop(
    engine: Any,
    route_result: SkillMatchResult | None,
    on_event: EventCallback | None,
    *,
    start_iteration: int = 1,
    approval_resolver: ApprovalResolver | None = None,
    question_resolver: QuestionResolver | None = None,
    skip_initial_inbox_claim: bool = False,
) -> ChatResult:
    """迭代循环体：LLM 请求 → thinking 提取 → 工具调用遍历 → 熔断检测。

    next-turn 只在 Driver.pre_step 认领。此处仅在步边界认领 next-step。
    """
    if route_result is None:
        route_result = SkillMatchResult(
            skills_used=[],
            route_mode="all_tools",
            system_contexts=[],
        )
    from excelmanus.auth.providers.openai_codex import OpenAICodexProvider as _OpenAICodexProvider

    def _finalize_result(**kwargs: Any) -> ChatResult:
        """统一出口：刷新 registry + checkpoint + 自动发射 FILES_CHANGED 事件。"""
        engine._try_refresh_registry()
        # 每轮结束保存会话快照（SessionState + TaskStore，不是文件检查点）
        engine.save_session_snapshot()
        # 自动发射 FILES_CHANGED 事件（写入路径由 Host 追踪，不靠结束工具申报）
        if engine._state.affected_files and on_event is not None:
            from excelmanus.events import EventType, ToolCallEvent, mutations_from_identities
            from excelmanus.workspace.identity import (
                collect_public_identities,
                workspace_root_of,
            )
            changed = collect_public_identities(
                engine._state.affected_files,
                workspace_root_of(engine),
            )
            engine.emit(
                on_event,
                ToolCallEvent(
                    event_type=EventType.FILES_CHANGED,
                    changed_files=changed,
                    mutations=mutations_from_identities(changed),
                ),
            )
        return ChatResult(**kwargs)

    max_failures = engine._config.max_consecutive_failures
    consecutive_failures = 0
    all_tool_results: list[ToolCallResult] = []
    current_route_result = route_result
    max_iterations = max(1, int(getattr(engine._config, "max_iterations", 50) or 50))
    # 恢复执行时保留之前的统计，仅首次调用时重置
    if start_iteration <= 1:
        engine._state.reset_loop_stats()
        if engine._tool_dispatcher is not None:
            engine._tool_dispatcher.reset_cancel()
            engine._tool_dispatcher.begin_call_budget(max_iterations)
    tool_access = "may_write"
    # token 使用累计
    total_prompt_tokens = 0
    total_completion_tokens = 0
    # 诊断收集
    engine._turn_diagnostics = []

    for iteration in count(start_iteration):
        if iteration > max_iterations:
            reply = f"已达到最大迭代次数（{max_iterations}），已终止执行。"
            engine._memory.add_assistant_message(reply)
            engine._last_iteration_count = max_iterations
            logger.warning("已达到最大迭代次数 %d，终止执行", max_iterations)
            logger.info("最终结果摘要: %s", _summarize_text(reply))
            return _finalize_result(
                reply=reply,
                tool_calls=list(all_tool_results),
                iterations=max_iterations,
                truncated=True,
                prompt_tokens=total_prompt_tokens,
                completion_tokens=total_completion_tokens,
                total_tokens=total_prompt_tokens + total_completion_tokens,
            )
        driver = getattr(engine, "_driver", None)
        if driver is not None:
            driver.mark_step(iteration)
        engine._emit(
            on_event,
            ToolCallEvent(
                event_type=EventType.ITERATION_START,
                iteration=iteration,
            ),
        )
        engine._emit(
            on_event,
            ToolCallEvent(
                event_type=EventType.STEP_START,
                iteration=iteration,
            ),
        )

        def _emit_step_end() -> None:
            engine._emit(
                on_event,
                ToolCallEvent(
                    event_type=EventType.STEP_END,
                    iteration=iteration,
                ),
            )

        await engine._refresh_credential_if_needed(on_event=on_event)

        if iteration == start_iteration:
            engine._emit(
                on_event,
                ToolCallEvent(
                    event_type=EventType.PIPELINE_PROGRESS,
                    pipeline_stage="preparing",
                    pipeline_message="正在准备本轮",
                ),
            )
        else:
            engine._emit(
                on_event,
                ToolCallEvent(
                    event_type=EventType.PIPELINE_PROGRESS,
                    pipeline_stage="calling_model",
                    pipeline_message="正在调用模型",
                ),
            )

        _ctx_start = time.monotonic()
        prepared_prompts, context_error = engine._prepare_system_prompts_for_request()
        if iteration == start_iteration:
            logger.debug("perf.loop: context_build %.0fms", (time.monotonic() - _ctx_start) * 1000)
        if context_error is not None:
            engine._last_iteration_count = iteration
            engine._last_failure_count += 1
            engine._memory.add_assistant_message(context_error)
            logger.warning("系统上下文预算检查失败，终止执行: %s", context_error)
            _emit_step_end()
            return _finalize_result(
                reply=context_error,
                tool_calls=list(all_tool_results),
                iterations=iteration,
                truncated=False,
                prompt_tokens=total_prompt_tokens,
                completion_tokens=total_completion_tokens,
                total_tokens=total_prompt_tokens + total_completion_tokens,
            )

        system_prompts = [prepared_prompts[0]] if prepared_prompts else []
        user_contexts = list(getattr(engine, "_prompt_user_contexts", None) or [])
        mention_block = build_mention_context_block(
            getattr(engine, "_mention_contexts", None) or [],
        )
        if mention_block:
            user_contexts.append(mention_block)

        # 步前压缩挂在 Driver 附件上，不在循环体里分支。
        engine._last_system_msgs = (
            engine._memory.build_system_messages(system_prompts)
            + [{"role": "user", "content": text} for text in user_contexts if text.strip()]
        )

        # 步边界认领 next-step（steer / inject）。next-turn 不在这里排干。
        _claim_step = iteration > start_iteration or not skip_initial_inbox_claim
        if driver is not None and _claim_step:
            _step_claimed = await driver.consume_next_step(iteration=iteration)
            if _step_claimed:
                logger.info("下一步认领 %d 条 steer/inject", len(_step_claimed))

        messages = engine._memory.trim_for_request(
            system_prompts=system_prompts,
            max_context_tokens=engine.max_context_tokens,
            context_prompts=user_contexts,
        )

        # PromptRegistry.tools() 快照优先；仍由 ToolRegistry/MetaToolBuilder 生成。
        tools = getattr(engine, "_prompt_tool_snapshot", None)
        if not tools:
            tools = engine._meta_tool_builder.build_v5_tools(
                tool_access=tool_access,
            )
        tool_scope = None

        # 安全网：确保发送到 API 的 model 是实际模型 ID，不含 provider 前缀
        _api_model = engine._active_model
        if _OpenAICodexProvider.is_codex_profile_name(_api_model):
            _api_model = _OpenAICodexProvider.model_from_profile_name(_api_model) or _api_model

        kwargs: dict[str, Any] = {
            "model": _api_model,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools

        # 注入 thinking 参数
        # 优先级：profile.thinking_mode > caps.thinking_type > 默认
        caps = engine._model_capabilities
        tc = engine._thinking_config
        _profile = engine._active_profile
        _profile_thinking_mode = getattr(_profile, "thinking_mode", "auto") if _profile else "auto"

        if _profile_thinking_mode not in ("auto", ""):
            # 用户显式指定了 thinking_mode
            _effective_ttype = _profile_thinking_mode if _profile_thinking_mode != "disabled" else ""
        elif caps and caps.supports_thinking:
            _effective_ttype = caps.thinking_type
        else:
            _effective_ttype = ""

        budget = tc.effective_budget()
        if _effective_ttype == "claude":
            kwargs["_thinking_enabled"] = not tc.is_disabled
            kwargs["_thinking_budget"] = budget if not tc.is_disabled else 0
            kwargs["_thinking_effort"] = tc.claude_effort
        elif not tc.is_disabled:
            if _effective_ttype == "claude_compat":
                extra = kwargs.get("extra_body", {})
                from excelmanus.providers.claude import uses_adaptive_thinking
                if uses_adaptive_thinking(str(_api_model)):
                    extra["thinking"] = {"type": "adaptive"}
                    extra["output_config"] = {"effort": tc.claude_effort}
                else:
                    extra["thinking"] = {"type": "enabled", "budget_tokens": budget}
                kwargs["extra_body"] = extra
            elif _effective_ttype == "gemini":
                kwargs["_thinking_budget"] = budget
            elif _effective_ttype == "gemini_level":
                kwargs["_thinking_level"] = tc.gemini_level
            elif _effective_ttype == "openai_reasoning":
                kwargs["reasoning_effort"] = tc.openai_effort
            elif _effective_ttype == "enable_thinking":
                extra = kwargs.get("extra_body", {})
                extra["enable_thinking"] = True
                extra["thinking_budget"] = budget
                kwargs["extra_body"] = extra
            elif _effective_ttype == "glm_thinking":
                extra = kwargs.get("extra_body", {})
                extra["thinking"] = {"type": "enabled"}
                extra["reasoning_effort"] = tc.openai_effort
                kwargs["extra_body"] = extra
            elif _effective_ttype == "openrouter":
                extra = kwargs.get("extra_body", {})
                extra["reasoning"] = {
                    "effort": tc.openai_effort,
                    "max_tokens": budget,
                }
                kwargs["extra_body"] = extra
            # "deepseek" / "reasoning_content_auto" → 模型自动输出推理内容，无需额外参数

        # 注入 profile 自定义 extra_body / extra_headers
        if _profile:
            import json as _json
            if _profile.custom_extra_body:
                try:
                    _ceb = _json.loads(_profile.custom_extra_body)
                    if isinstance(_ceb, dict):
                        merged = kwargs.get("extra_body", {})
                        merged.update(_ceb)
                        kwargs["extra_body"] = merged
                except (ValueError, TypeError):
                    pass
            if _profile.custom_extra_headers:
                try:
                    _ceh = _json.loads(_profile.custom_extra_headers)
                    if isinstance(_ceh, dict):
                        merged = kwargs.get("extra_headers", {})
                        merged.update(_ceh)
                        kwargs["extra_headers"] = merged
                except (ValueError, TypeError):
                    pass

        # 提示词缓存优化：同一 session_turn 内共享 cache key，
        # 确保 OpenAI 路由到同一缓存机器，最大化系统提示前缀 cache hit。
        if engine._config.prompt_cache_key_enabled:
            kwargs["prompt_cache_key"] = f"em_s{engine._session_turn}"

        # 尝试流式调用
        if iteration == start_iteration:
            engine._emit(
                on_event,
                ToolCallEvent(
                    event_type=EventType.PIPELINE_PROGRESS,
                    pipeline_stage="calling_llm",
                    pipeline_message="正在与模型通信...",
                ),
            )
        else:
            engine._emit(
                on_event,
                ToolCallEvent(
                    event_type=EventType.PIPELINE_PROGRESS,
                    pipeline_stage="calling_model",
                    pipeline_message="正在调用模型",
                ),
            )
        _llm_start_ts = time.monotonic()
        stream_kwargs = dict(kwargs)
        stream_kwargs["stream"] = True
        if isinstance(engine._client, openai.AsyncOpenAI):
            stream_kwargs["stream_options"] = {"include_usage": True}

        # ── LLM 调用 + 5xx/429 自动重试 ──
        _retry_max = engine._config.llm_retry_max_attempts
        _retry_base = engine._config.llm_retry_base_delay_seconds
        _retry_cap = engine._config.llm_retry_max_delay_seconds
        _auth_refresh_attempted = False  # 401 时仅尝试一次凭证刷新重试
        for _retry_attempt in range(1, _retry_max + 1):
            try:
                try:
                    stream_or_response = await engine._llm_caller.create_chat_completion_with_system_fallback(stream_kwargs)
                    # 检查返回值是否为异步迭代器（支持流式）
                    if hasattr(stream_or_response, "__aiter__"):
                        message, usage = await engine._llm_caller.consume_stream(
                            stream_or_response, on_event, iteration,
                            _llm_start_ts=_llm_start_ts,
                        )
                    else:
                        # provider 不支持 stream，返回了普通 response 对象
                        message, usage = _extract_completion_message(stream_or_response)
                except Exception as stream_exc:
                    # 可重试的瞬时错误 → 跳过非流式回退，直接进入重试
                    if is_retryable_llm_error(stream_exc):
                        raise
                    # 认证/权限错误回退无意义：同样会在非流式再次失败
                    if is_nonretryable_auth_error(stream_exc):
                        raise
                    # 内容安全策略拦截回退无意义：非流式同样会被拦截
                    if is_content_filter_error(stream_exc):
                        raise
                    # 流式调用失败时回退到非流式
                    logger.warning("流式调用失败，回退到非流式: %s", stream_exc)
                    response = await engine._llm_caller.create_chat_completion_with_system_fallback(kwargs)
                    message, usage = _extract_completion_message(response)

                # 成功 — 若经历过重试则通知前端
                if _retry_attempt > 1:
                    engine._emit(
                        on_event,
                        ToolCallEvent(
                            event_type=EventType.LLM_RETRY,
                            retry_status="succeeded",
                            retry_attempt=_retry_attempt,
                            retry_max_attempts=_retry_max,
                        ),
                    )
                break  # 成功，退出重试循环

            except Exception as _retry_exc:
                # ── 内容安全策略拦截：不可重试，立即通知前端 ──
                if is_content_filter_error(_retry_exc):
                    engine._emit(
                        on_event,
                        _failure_guidance_event(_classify_failure(
                            _retry_exc,
                            stage="calling_llm",
                            provider=engine._extract_provider_label(),
                            model=engine._active_model or "",
                        )),
                    )
                    raise

                # ── 401/403 认证错误：尝试刷新凭证后重试一次 ──
                if is_nonretryable_auth_error(_retry_exc) and not _auth_refresh_attempted:
                    _auth_refresh_attempted = True
                    # 诊断日志：记录当前使用的凭证信息
                    _key_preview = (engine._active_api_key or "")[:20]
                    logger.warning(
                        "401 诊断: model=%s, base_url=%s, api_key_prefix=%s..., "
                        "has_resolver=%s",
                        engine._active_model, engine._active_base_url,
                        _key_preview, engine._credential_resolver is not None,
                    )
                    _old_key = engine._active_api_key
                    try:
                        await engine._refresh_credential_if_needed(on_event=on_event)
                    except Exception:
                        logger.debug("401 后凭证刷新失败", exc_info=True)
                    if engine._active_api_key != _old_key:
                        logger.info(
                            "401 后凭证已刷新，重试 LLM 调用 (attempt=%d)",
                            _retry_attempt,
                        )
                        engine._emit(
                            on_event,
                            ToolCallEvent(
                                event_type=EventType.PIPELINE_PROGRESS,
                                pipeline_stage="credential_refreshed_retrying",
                                pipeline_message="认证失败，已自动刷新凭证，正在重试...",
                            ),
                        )
                        # 用新凭证重建请求参数中的客户端引用
                        continue
                    # 凭证未变化，无法恢复
                    logger.warning(
                        "401 后凭证刷新未产生新 token，无法恢复: %s",
                        str(_retry_exc)[:200],
                    )
                    raise

                if _retry_attempt < _retry_max and is_retryable_llm_error(_retry_exc):
                    _delay = compute_retry_delay(
                        _retry_attempt, _retry_base, _retry_cap, _retry_exc,
                    )
                    _err_brief = str(_retry_exc)[:200]
                    logger.warning(
                        "LLM 调用失败（可重试），%0.1f 秒后第 %d/%d 次重试: %s",
                        _delay, _retry_attempt, _retry_max - 1, _err_brief,
                    )
                    # 通知前端：正在重试
                    engine._emit(
                        on_event,
                        ToolCallEvent(
                            event_type=EventType.LLM_RETRY,
                            retry_status="retrying",
                            retry_attempt=_retry_attempt,
                            retry_max_attempts=_retry_max,
                            retry_delay_seconds=_delay,
                            retry_error_message=_err_brief,
                        ),
                    )
                    engine._emit(
                        on_event,
                        ToolCallEvent(
                            event_type=EventType.PIPELINE_PROGRESS,
                            pipeline_stage="llm_retrying",
                            pipeline_message=(
                                f"模型服务暂时不可用，{_delay:.0f}秒后"
                                f"第 {_retry_attempt}/{_retry_max - 1} 次重试..."
                            ),
                        ),
                    )
                    await asyncio.sleep(_delay)
                    # 重试前重新发射 calling_llm 进度
                    engine._emit(
                        on_event,
                        ToolCallEvent(
                            event_type=EventType.PIPELINE_PROGRESS,
                            pipeline_stage="calling_llm",
                            pipeline_message=f"正在重试与模型通信（第 {_retry_attempt + 1}/{_retry_max} 次尝试）...",
                        ),
                    )
                    continue

                # 不可重试或重试次数耗尽
                if _retry_attempt >= _retry_max and is_retryable_llm_error(_retry_exc):
                    engine._emit(
                        on_event,
                        ToolCallEvent(
                            event_type=EventType.LLM_RETRY,
                            retry_status="exhausted",
                            retry_attempt=_retry_attempt,
                            retry_max_attempts=_retry_max,
                            retry_error_message=str(_retry_exc)[:200],
                        ),
                    )
                raise

        # 流式截断检测：consume_stream 因连续 chunk 解析错误而中止
        if getattr(message, "_stream_truncated", False):
            logger.warning(
                "流式响应因连续 chunk 解析错误而被截断，输出可能不完整 (content_len=%d)",
                len(getattr(message, "content", "") or ""),
            )
            engine._emit(
                on_event,
                _failure_guidance_event(_classify_failure(
                    RuntimeError("流式响应解析中断：连续多个数据块解析失败"),
                    stage="streaming",
                    provider=engine._extract_provider_label(),
                    model=engine._active_model or "",
                )),
            )

        tool_calls = _normalize_tool_calls(getattr(message, "tool_calls", None))

        _llm_elapsed_ms = (time.monotonic() - _llm_start_ts) * 1000
        _tc_names = [getattr(getattr(tc, "function", None), "name", "?") for tc in (tool_calls or [])]
        if iteration == start_iteration:
            logger.info(
                "perf.loop: first_llm_call %.0fms → tools=%s",
                _llm_elapsed_ms, _tc_names or "text_reply",
            )
        else:
            logger.debug(
                "perf.loop: llm_call iter=%d %.0fms → tools=%s",
                iteration, _llm_elapsed_ms, _tc_names or "text_reply",
            )

        # 图片生命周期：视觉模型保留图片利用 Provider 缓存，非视觉模型立即降级
        if engine._is_vision_capable:
            engine._memory.manage_image_lifecycle()
        else:
            engine._memory.mark_images_sent()

        # 累计 token 使用量
        if usage is not None:
            total_prompt_tokens += _usage_token(usage, "prompt_tokens")
            total_completion_tokens += _usage_token(usage, "completion_tokens")

        # 提取 thinking 内容（流式模式下已累积到 message.thinking）
        thinking_content = getattr(message, "thinking", None) or ""

        # 仅在流式过程中未发射过 THINKING_DELTA 时，才发射完整 THINKING 事件，
        # 避免前端收到重复的 thinking 块。
        _already_streamed = getattr(message, "_thinking_streamed", False)
        if thinking_content and not _already_streamed:
            engine._emit(
                on_event,
                ToolCallEvent(
                    event_type=EventType.THINKING,
                    thinking=thinking_content,
                    iteration=iteration,
                ),
            )

        # /reasoning 开启时额外发射推理内容通知
        if thinking_content and engine._show_reasoning:
            engine._emit(
                on_event,
                ToolCallEvent(
                    event_type=EventType.REASONING_NOTICE,
                    thinking=thinking_content,
                    iteration=iteration,
                ),
            )

        # ── 收集本轮迭代诊断快照 ──
        iter_prompt = _usage_token(usage, "prompt_tokens") if usage else 0
        iter_completion = _usage_token(usage, "completion_tokens") if usage else 0
        iter_cached = _extract_cached_tokens(usage)
        iter_cache_creation, iter_cache_read = _extract_anthropic_cache_tokens(usage)
        iter_ttft = _extract_ttft_ms(usage)
        diag = TurnDiagnostic(
            iteration=iteration,
            prompt_tokens=iter_prompt,
            completion_tokens=iter_completion,
            cached_tokens=iter_cached,
            cache_creation_input_tokens=iter_cache_creation,
            cache_read_input_tokens=iter_cache_read,
            ttft_ms=iter_ttft,
            thinking_content=thinking_content,
            tool_names=[
                s.get("function", {}).get("name", "")
                for s in tools
                if s.get("function", {}).get("name")
            ] if tools else [],
        )
        engine._turn_diagnostics.append(diag)

        # ── LLM 调用审计日志 ──
        if engine._llm_call_store is not None:
            try:
                _llm_latency = (time.monotonic() - _llm_start_ts) * 1000 if _llm_start_ts else 0.0
                engine._llm_call_store.log(
                    session_id=getattr(engine, "_session_id", None),
                    turn=engine._session_turn,
                    iteration=iteration,
                    model=engine._active_model,
                    prompt_tokens=iter_prompt,
                    completion_tokens=iter_completion,
                    cached_tokens=iter_cached,
                    has_tool_calls=bool(tool_calls),
                    thinking_chars=len(thinking_content),
                    stream=True,
                    latency_ms=_llm_latency,
                    ttft_ms=iter_ttft,
                    cache_creation_tokens=iter_cache_creation,
                    cache_read_tokens=iter_cache_read,
                )
            except Exception:
                pass

        # ── Prompt Cache 效果日志 ──
        if iter_cache_read > 0 or iter_cache_creation > 0:
            _cache_ratio = (
                iter_cache_read / max(1, iter_prompt) * 100
                if iter_prompt > 0 else 0
            )
            logger.info(
                "Prompt Cache 诊断: iter=%d ttft=%.0fms "
                "cache_read=%d cache_creation=%d prompt=%d "
                "cache_hit_ratio=%.1f%% latency=%.0fms",
                iteration, iter_ttft,
                iter_cache_read, iter_cache_creation, iter_prompt,
                _cache_ratio, _llm_latency,
            )
        elif iter_ttft > 0:
            logger.debug(
                "LLM 诊断: iter=%d ttft=%.0fms prompt=%d latency=%.0fms (no cache)",
                iteration, iter_ttft, iter_prompt, _llm_latency,
            )

        # 无工具调用 → 纯文本回复处理（仅 HTML 端点错误检测）
        if not tool_calls:
            text_action, text_result = _handle_text_reply(
                engine,
                message=message,
                iteration=iteration,
                all_tool_results=all_tool_results,
                total_prompt_tokens=total_prompt_tokens,
                total_completion_tokens=total_completion_tokens,
                _finalize_result=_finalize_result,
            )
            if text_action == "return":
                _emit_step_end()
                if driver is not None and driver.inbox.next_step:
                    continue
                return text_result

        assistant_msg = _assistant_message_to_dict(message)
        if tool_calls:
            assistant_msg["tool_calls"] = [_to_plain(tc) for tc in tool_calls]
        engine._memory.add_assistant_tool_message(assistant_msg)

        # 遍历工具调用
        _tool_names_in_batch = [
            getattr(getattr(tc, "function", None), "name", "")
            for tc in tool_calls
        ]
        _tool_count = len(tool_calls)
        _tool_label = (
            _tool_names_in_batch[0] if _tool_count == 1
            else f"{_tool_count} 个工具"
        )
        engine._emit(
            on_event,
            ToolCallEvent(
                event_type=EventType.PIPELINE_PROGRESS,
                pipeline_stage="executing_tools",
                pipeline_message=f"正在执行 {_tool_label}...",
            ),
        )
        breaker_triggered = False
        breaker_summary = ""
        breaker_skip_error = (
            f"工具未执行：连续 {max_failures} 次工具调用失败，已触发熔断。"
        )
        # ── 批次拆分：is_concurrency_safe 为 True 的相邻调用才进滚动池 ──
        if engine._config.parallel_readonly_tools:
            _batches = engine._tool_runtime.split_batches(tool_calls)
        else:
            _batches = [_ToolCallBatch([tc], False) for tc in tool_calls]

        for _batch in _batches:
            # ── breaker / question 跳过逻辑（适用于整个批次） ──
            if breaker_triggered:
                for tc in _batch.tool_calls:
                    function = getattr(tc, "function", None)
                    tool_name = getattr(function, "name", "")
                    tool_call_id = getattr(tc, "id", "")
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
                        engine._memory.add_tool_result(tool_call_id, breaker_skip_error)
                continue

            if _batch.parallel:
                _reclassified = engine._tool_runtime.reclassify_batch(_batch.tool_calls)
                if len(_reclassified) != 1 or not _reclassified[0].parallel:
                    _batch = _ToolCallBatch(list(_batch.tool_calls), False)
            if _batch.parallel:
                # ── 并行路径：只读工具并发执行 ──
                _parallel_results = await engine._execute_tool_calls_parallel(
                    _batch.tool_calls, tool_scope, on_event, iteration,
                    route_result=current_route_result,
                )
                for tc, tc_result in _parallel_results:
                    function = getattr(tc, "function", None)
                    tool_name = getattr(function, "name", "")
                    tool_call_id = getattr(tc, "id", "")

                    all_tool_results.append(tc_result)

                    # 按序写入 memory
                    if not tc_result.defer_tool_result and tool_call_id:
                        engine._memory.add_tool_result(tool_call_id, tc_result.result)

                    # 统计更新（只读工具不触发 write_effect 分支）
                    engine._last_tool_call_count += 1
                    if tc_result.success:
                        engine._last_success_count += 1
                        consecutive_failures = 0
                    else:
                        engine._last_failure_count += 1
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
            else:
                # ── 串行路径（保留完整原有逻辑） ──
                for tc in _batch.tool_calls:
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
                            engine._memory.add_tool_result(tool_call_id, breaker_skip_error)
                        continue

                    tc_result = await engine._execute_tool_call(
                        tc,
                        tool_scope,
                        on_event,
                        iteration,
                        route_result=current_route_result,
                    )

                    all_tool_results.append(tc_result)

                    if not tc_result.defer_tool_result and tool_call_id:
                        engine._memory.add_tool_result(tool_call_id, tc_result.result)

                    if tc_result.pending_approval:
                        pending = engine._approval.pending
                        if approval_resolver is not None and pending is not None:
                            # ── 内联审批：在同一轮对话内等待用户决策 ──
                            approval_id = tc_result.approval_id or pending.approval_id
                            logger.info("内联审批等待决策: %s", approval_id)
                            try:
                                decision = await approval_resolver(pending)
                            except Exception as _resolver_exc:  # noqa: BLE001
                                logger.warning("approval_resolver 异常，视为 reject: %s", _resolver_exc)
                                decision = None

                            updates, _wrote = await engine._apply_approval_decision(
                                decision, pending, approval_id,
                                tool_call_id, on_event, iteration, "内联审批",
                            )
                            tc_result = replace(tc_result, **updates)
                            # 内联审批完成，不退出循环，继续处理后续工具调用
                        else:
                            # ── 无 resolver（Web API 等）：阻塞等待用户决策 ──
                            approval_id = tc_result.approval_id or (pending.approval_id if pending else "")
                            logger.info("阻塞等待审批决策: %s", approval_id)
                            fut = engine._interaction_registry.create(approval_id)
                            try:
                                decision_payload = await asyncio.wait_for(
                                    fut, timeout=DEFAULT_INTERACTION_TIMEOUT,
                                )
                            except asyncio.TimeoutError:
                                reject_msg = engine._approval.reject_pending(approval_id)
                                if tool_call_id:
                                    engine._memory.replace_tool_result(tool_call_id, reject_msg)
                                tc_result = replace(
                                    tc_result,
                                    pending_approval=False, success=False,
                                    result=reject_msg, error=reject_msg,
                                )
                                logger.info("审批等待超时，自动拒绝: %s", approval_id)
                                engine._interaction_registry.cleanup_done()
                            except asyncio.CancelledError:
                                reject_msg = engine._approval.reject_pending(approval_id)
                                if tool_call_id:
                                    engine._memory.replace_tool_result(tool_call_id, reject_msg)
                                tc_result = replace(
                                    tc_result,
                                    pending_approval=False, success=False,
                                    result=reject_msg, error=reject_msg,
                                )
                                engine._interaction_registry.cleanup_done()
                            else:
                                decision = decision_payload.get("decision") if isinstance(decision_payload, dict) else str(decision_payload)
                                engine._interaction_registry.cleanup_done()
                                updates, _wrote = await engine._apply_approval_decision(
                                    decision, pending, approval_id,
                                    tool_call_id, on_event, iteration, "Web 审批",
                                )
                                tc_result = replace(tc_result, **updates)

                    # 更新统计
                    engine._last_tool_call_count += 1
                    if tc_result.success:
                        engine._last_success_count += 1
                        consecutive_failures = 0
                        _write_effect = engine._get_tool_write_effect(tc_result.tool_name)
                        if _write_effect == "workspace_write":
                            engine._record_workspace_write_action()
                        elif _write_effect == "external_write":
                            engine._record_external_write_action()
                    else:
                        engine._last_failure_count += 1
                        # 已在 ToolDispatcher 中自动重试过的 retryable 错误
                        # 不再计入熔断计数（重试已耗尽说明是持续性故障）
                        consecutive_failures += 1

                    # 熔断检测
                    if (not breaker_triggered) and consecutive_failures >= max_failures:
                        recent_errors = [
                            f"- {r.tool_name}({r.error_kind or 'unknown'}): {r.error}"
                            for r in all_tool_results[-max_failures:]
                            if not r.success
                        ]
                        breaker_summary = "\n".join(recent_errors)
                        breaker_triggered = True

        # 说明：旧的 ask_user 退出路径已移除。
        # 阻塞式 ask_user 在 AskUserHandler 内 await Future，
        # 返回用户回答作为 tool result，循环不中断。

        # ── 延迟图片注入：所有 tool_result 写入 memory 后再注入 user 图片消息 ──
        # 如果在 tool_result 之前注入，会破坏 assistant(tool_calls) → tool(responses)
        # 的消息序列，导致 OpenAI 兼容 API 返回 400 错误。
        engine._tool_dispatcher.flush_deferred_images()

        if breaker_triggered:
            reply = (
                f"连续 {max_failures} 次工具调用失败，已终止执行。"
                f"错误摘要：\n{breaker_summary}"
            )
            engine._memory.add_assistant_message(reply)
            engine._last_iteration_count = iteration
            logger.warning("连续 %d 次工具失败，熔断终止", max_failures)
            logger.info("最终结果摘要: %s", _summarize_text(reply))
            _emit_step_end()
            return _finalize_result(
                reply=reply,
                tool_calls=list(all_tool_results),
                iterations=iteration,
                truncated=False,
                prompt_tokens=total_prompt_tokens,
                completion_tokens=total_completion_tokens,
                total_tokens=total_prompt_tokens + total_completion_tokens,
            )

        _emit_step_end()
