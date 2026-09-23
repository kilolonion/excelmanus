"""Step 循环体：模型流 + 工具批。Driver.step 调用这里。"""

from __future__ import annotations

import asyncio
import time
from dataclasses import replace
from itertools import count
from typing import Any

from excelmanus.engine_core.idle_tracker import idle_segment, reset_idle_tracker
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
    _extract_completion_message,
    _extract_ttft_ms,
    _looks_like_html_document,
    _message_content_to_text,
    _normalize_tool_calls,
    _summarize_text,
    _usage_token,
)
from excelmanus.request.compiler import compile_request, header_from_sealed
from excelmanus.request.series import series_of
from excelmanus.request.usage import extract_cache_usage
from excelmanus.error_guidance import classify_failure as _classify_failure
from excelmanus.events import EventCallback, EventType, ToolCallEvent
from excelmanus.interaction import DEFAULT_INTERACTION_TIMEOUT
from excelmanus.logger import get_logger
from excelmanus.tools.policy import write_effect_for_call
from excelmanus.message_serialization import (
    assistant_message_to_dict as _assistant_message_to_dict,
    sanitize_tool_call_arguments as _sanitize_tool_call_arguments,
    to_plain as _to_plain,
)
from excelmanus.skillpacks import SkillMatchResult
from excelmanus.agent.budget import TurnBudgetExceeded
from excelmanus.workbook.user_edits import has_pending_user_edits

logger = get_logger("agent.loop")


async def _execute_and_resolve_tool(
    engine: Any, tc: Any, tool_scope: Any, on_event: Any, iteration: int,
    current_route_result: Any, approval_resolver: Any,
) -> ToolCallResult:
    """Keep the existing approval flow inside the lifetime of its tool call."""
    tool_call_id = getattr(tc, "id", "")
    tc_result = await engine._execute_tool_call(
        tc,
        tool_scope,
        on_event,
        iteration,
        route_result=current_route_result,
    )

    if tc_result.pending_approval and not tc_result.defer_tool_result and tool_call_id:
        engine._memory.add_tool_result(tool_call_id, tc_result.result)
        tc._pending_result_written = True

    if tc_result.pending_approval:
        driver_for_snapshot = getattr(engine, "_driver", None)
        persist_runtime = getattr(driver_for_snapshot, "_persist_runtime_state", None)
        if callable(persist_runtime):
            persist_runtime()
        pending = engine._approval.pending
        if approval_resolver is not None and pending is not None:
            # ── 内联审批：在同一轮对话内等待用户决策 ──
            approval_id = tc_result.approval_id or pending.approval_id
            logger.info("内联审批等待决策: %s", approval_id)
            try:
                with idle_segment(engine, "approval"):
                    decision = await approval_resolver(pending)
            except asyncio.CancelledError:
                # Cancellation is a control signal, never an
                # approval decision.  Close the pending gate,
                # then propagate cancellation to the driver so
                # the turn cannot continue after abort.
                engine._approval.reject_pending(approval_id)
                engine._interaction_registry.cleanup_done()
                raise
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
            try:
                with idle_segment(engine, "approval"):
                    decision_payload = await engine._interaction_handler.wait_approval_decision(approval_id)
            except asyncio.TimeoutError:
                reject_msg = engine._approval.reject_pending(
                    approval_id, timeout=True,
                )
                fields = _approval_reject_fields(reject_msg, timeout=True)
                if tool_call_id:
                    engine._memory.replace_tool_result(
                        tool_call_id, fields["result"],
                    )
                tc_result = replace(tc_result, **fields)
                logger.info("审批等待超时，自动拒绝: %s", approval_id)
                engine._interaction_handler.finish_approval(approval_id, fields["result"], False)
                engine._interaction_registry.cleanup_done()
            except asyncio.CancelledError:
                reject_msg = engine._approval.reject_pending(approval_id)
                fields = _approval_reject_fields(reject_msg, timeout=False)
                if tool_call_id:
                    engine._memory.replace_tool_result(
                        tool_call_id, fields["result"],
                    )
                tc_result = replace(tc_result, **fields)
                engine._interaction_registry.cleanup_done()
                # Do not turn task cancellation into a normal
                # rejection: callers must observe cancellation
                # and Driver must emit TURN_FAILED.
                raise
            else:
                decision = decision_payload.get("decision") if isinstance(decision_payload, dict) else str(decision_payload)
                engine._interaction_registry.cleanup_done()
                updates, _wrote = await engine._apply_approval_decision(
                    decision, pending, approval_id,
                    tool_call_id, on_event, iteration, "Web 审批",
                )
                tc_result = replace(tc_result, **updates)

    if tc_result.pending_question:
        driver_for_snapshot = getattr(engine, "_driver", None)
        persist_runtime = getattr(driver_for_snapshot, "_persist_runtime_state", None)
        if callable(persist_runtime):
            persist_runtime()

    return tc_result


async def _await_with_turn_budget(engine: Any, awaitable: Any) -> Any:
    """让一次 LLM 请求（含其 provider 重试）使用父回合剩余时间。"""
    budget = getattr(engine, "_turn_budget", None)
    if budget is not None:
        try:
            budget.ensure_time()
        except Exception:
            close = getattr(awaitable, "close", None)
            if callable(close):
                close()
            raise
    driver = getattr(engine, "_driver", None)
    remaining = getattr(driver, "remaining_turn_seconds", lambda: None)()
    if remaining is None:
        return await awaitable
    if remaining <= 0:
        raise TurnBudgetExceeded("wall_clock", "本轮 wall-clock 预算已耗尽")
    return await asyncio.wait_for(awaitable, timeout=remaining)


# ── 并行批同因折叠（P3-a）──────────────────────────────────
#
# 同 iteration、同并行批内 N 个失败若同因（同 error_code + 同文件 +
# 同可用表清单），durable 全部保留全文，第 2 条起仅在出网投影时折叠。
# all_tool_results（内存）、审计与熔断计数保持全文不变。
# 指针内联 remediation + available_sheets，模型无需跳转即可纠正；
# 不用 spill: 前缀、不设 spill 键，与 spill 取回通道隔离。

_DEDUP_POINTER_MAX_CHARS = 400
_DEDUP_ARGS_HINT_MAX_CHARS = 120


def _dedup_parse_result(result: Any) -> dict[str, Any]:
    """从 ToolCallResult.result 文本解析规范错误 JSON，失败返回 {}。"""
    import json

    if isinstance(result, dict):
        return result
    if not isinstance(result, str):
        return {}
    text = result.strip()
    if not text.startswith("{"):
        return {}
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _dedup_error_info(tc_result: Any) -> dict[str, Any] | None:
    """提取 (error_code, failure_class, fields, remediation, sheets)。"""
    from excelmanus.engine_core.error_payload import (
        failure_class_for_error_code,
        remediation_for,
    )

    structured = getattr(tc_result, "structured", None)
    code: str | None = None
    fields: dict[str, Any] = {}
    err = getattr(structured, "error", None) if structured is not None else None
    if err is not None:
        code = getattr(err, "code", None)
        raw_fields = getattr(err, "fields", None)
        if isinstance(raw_fields, dict):
            fields = raw_fields
    parsed = _dedup_parse_result(getattr(tc_result, "result", ""))
    if not code:
        raw_code = parsed.get("error_code") or parsed.get("code")
        code = str(raw_code) if raw_code else None
    if not code:
        return None
    if not fields:
        raw_fields = parsed.get("fields")
        if isinstance(raw_fields, dict):
            fields = raw_fields
    sheets = fields.get("available_sheets") or parsed.get("available_sheets") or []
    sheets = [str(s) for s in sheets if str(s).strip()][:5]
    remediation = (
        parsed.get("remediation")
        or remediation_for(code, extra={**fields, "available_sheets": sheets} if sheets else fields)
    )
    return {
        "code": code,
        "failure_class": failure_class_for_error_code(code),
        "fields": fields,
        "remediation": str(remediation or ""),
        "sheets": sheets,
        "message": str(parsed.get("message") or getattr(tc_result, "error", "") or "")[:80],
    }


def _dedup_norm_file(arguments: Any) -> str:
    args = arguments if isinstance(arguments, dict) else {}
    raw = args.get("file_path") or args.get("path") or ""
    return str(raw).replace("\\", "/").strip().lower()


def _dedup_group_key(tc_result: Any) -> tuple[str, str, str, str] | None:
    """同因分组键 (error_code, file, sheets指纹, failure_class)，缺一不可。"""
    if getattr(tc_result, "success", True):
        return None
    if getattr(tc_result, "defer_tool_result", False):
        return None
    if getattr(tc_result, "pending_approval", False) or getattr(tc_result, "pending_question", False):
        return None
    info = _dedup_error_info(tc_result)
    if info is None:
        return None
    norm_file = _dedup_norm_file(getattr(tc_result, "arguments", {}))
    sheets = info["sheets"]
    if not norm_file and not sheets:
        return None
    if sheets:
        sheets_fp = "|".join(sorted(sheets))
    else:
        sheets_fp = f"msg:{info['message'][:80]}"
    return (info["code"], norm_file, sheets_fp, info["failure_class"])


def _dedup_args_hint(first_args: Any, args: Any) -> str:
    """两组参数的差异摘要（≤120字），完全相同写“同参”。"""
    fa = first_args if isinstance(first_args, dict) else {}
    ga = args if isinstance(args, dict) else {}
    if fa == ga:
        return "同参"
    parts: list[str] = []
    for key in list(fa.keys()) + [k for k in ga.keys() if k not in fa]:
        fv, gv = fa.get(key), ga.get(key)
        if fv == gv:
            continue
        parts.append(f"{key}: {str(fv)[:24]} vs {str(gv)[:24]}")
        if sum(len(p) for p in parts) > _DEDUP_ARGS_HINT_MAX_CHARS:
            break
    text = "; ".join(parts)[:_DEDUP_ARGS_HINT_MAX_CHARS] or "参数不同"
    return text


def _build_dedup_pointer(
    *,
    code: str,
    failure_class: str,
    group: str,
    ref_tool_call_id: str,
    first_tool: str,
    position: str,
    remediation: str,
    sheets: list[str],
    args_hint: str,
) -> str:
    """构造折叠指针 JSON（不用 spill: 前缀、不设 spill 键）。"""
    import json

    payload = {
        "status": "error",
        "error_code": code,
        "dedup": "same-batch",
        "ref_tool_call_id": ref_tool_call_id,
        "group": group,
        "message": f"与同批 {first_tool} 同因失败（{position}），详情见该条。",
        "failure_class": failure_class,
        "remediation": remediation,
        "available_sheets": sheets,
        "args_hint": args_hint,
    }
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    # 超预算时只压缩 remediation（表清单独立字段保留），仍超则放弃折叠由调用方决定。
    while len(text) > _DEDUP_POINTER_MAX_CHARS and len(payload["remediation"]) > 24:
        overflow = len(text) - _DEDUP_POINTER_MAX_CHARS + 3
        payload["remediation"] = payload["remediation"][: max(0, len(payload["remediation"]) - overflow)] + "…"
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return text


def plan_parallel_dedup(
    items: list[tuple[Any, Any]],
) -> dict[int, str]:
    """为并行批结果计算折叠计划：{下标: 指针文本}。

    输入为保序的 [(tc, tc_result)]；首条保留全文，其余同组折叠。
    只读不写 memory，调用方决定入库文本。
    """
    groups: dict[tuple[str, str, str, str], list[int]] = {}
    for index, (_tc, tc_result) in enumerate(items):
        key = _dedup_group_key(tc_result)
        if key is None:
            continue
        groups.setdefault(key, []).append(index)
    plan: dict[int, str] = {}
    for key, indices in groups.items():
        if len(indices) < 2:
            continue
        code, norm_file, sheets_fp, fclass = key
        first_index = indices[0]
        first_tc, first_result = items[first_index]
        first_id = str(getattr(first_tc, "id", "") or "")
        first_tool = str(getattr(first_result, "tool_name", "") or "")
        first_args = getattr(first_result, "arguments", {})
        group_label = f"{code}|{norm_file or '-'}|{sheets_fp[:48]}"
        total = len(indices)
        for rank, index in enumerate(indices[1:], start=2):
            _tc, tc_result = items[index]
            info = _dedup_error_info(tc_result) or {}
            pointer = _build_dedup_pointer(
                code=code,
                failure_class=fclass,
                group=group_label,
                ref_tool_call_id=first_id,
                first_tool=first_tool or "同批首条",
                position=f"第{rank}/{total}条",
                remediation=str(info.get("remediation") or ""),
                sheets=[str(s) for s in (info.get("sheets") or [])][:5],
                args_hint=_dedup_args_hint(first_args, getattr(tc_result, "arguments", {})),
            )
            if len(pointer) > _DEDUP_POINTER_MAX_CHARS:
                continue
            plan[index] = pointer
    return plan


def _approval_reject_fields(reject_msg: str, *, timeout: bool) -> dict[str, Any]:
    """把审批拒绝/超时收成模型可读的 ToolCallResult 字段。"""
    import json

    from excelmanus.engine_core.error_payload import APPROVAL_DENIED, APPROVAL_TIMEOUT
    from excelmanus.engine_core.tool_result import error_result, from_payload

    code = APPROVAL_TIMEOUT if timeout else APPROVAL_DENIED
    structured = None
    text = reject_msg
    try:
        parsed = json.loads(reject_msg)
    except (json.JSONDecodeError, TypeError, ValueError):
        parsed = None
    if isinstance(parsed, dict):
        structured = from_payload(parsed)
        text = structured.model_text
    else:
        structured = error_result(reject_msg, code=code)
        text = structured.model_text
    return {
        "pending_approval": False,
        "success": False,
        "result": text,
        "error": code,
        "structured": structured,
    }


def _release_open_attempt(engine: Any) -> None:
    from excelmanus.attachments.files_api import release_file_ids

    request_id = getattr(engine, "_open_request_id", None)
    if request_id:
        release_file_ids(request_id)
        engine._open_request_id = None


def _bind_prepared_outbound(engine: Any, prepared: Any) -> tuple[Any, dict[str, Any], dict[str, Any]]:
    import openai
    from excelmanus.attachments.files_api import lease_file_ids
    from excelmanus.engine_core.llm_caller import degraded_params

    route = prepared.route
    lease_file_ids(prepared.request_id, prepared.file_leases)
    engine._open_request_id = prepared.request_id
    kwargs = prepared.create_kwargs()
    stream_kwargs = dict(kwargs)
    stream_kwargs["stream"] = True
    skip = set(degraded_params(route.protocol_label(), route.model))
    skip |= set(degraded_params(route.protocol, route.model))
    if isinstance(getattr(engine, "_client", None), openai.AsyncOpenAI) and "stream_options" not in skip:
        stream_kwargs["stream_options"] = {"include_usage": True}
    return route, kwargs, stream_kwargs


def apply_outbound_epoch(
    engine: Any,
    envelope: Any,
    *,
    model: str,
    protocol: str,
    call_config: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], Any, str | None]:
    """兼容入口：只做系列前缀检查，不写 last_accepted。"""
    _ = (model, protocol, call_config)
    from excelmanus.prompt.envelope import compute_epoch_identity, call_config_from_engine
    from excelmanus.request.route import resolve_route

    wire = list(getattr(envelope, "wire_messages", None) or [])
    route = resolve_route(engine)
    engine._resolved_route = route
    header = header_from_sealed(engine, envelope)
    prefix_error = series_of(engine).check_prefix(header)
    if prefix_error:
        logger.error("request series prefix invariant broken: %s", prefix_error)
    identity = getattr(envelope, "identity", None)
    catalog_digest = ""
    if identity is not None:
        catalog_digest = str(getattr(identity, "catalog_digest", "") or "")
    system = getattr(envelope, "system_head", None) or getattr(envelope, "system", None) or ""
    resolved_config = call_config
    if resolved_config is None:
        resolved_config = call_config_from_engine(engine)
    epoch = compute_epoch_identity(
        session_id=str(getattr(engine, "_session_id", "") or ""),
        model=str(model or route.model or ""),
        protocol=str(protocol or route.protocol_label() or ""),
        call_config=resolved_config,
        tools=list(getattr(envelope, "tools", None) or []),
        system=system if isinstance(system, str) else "",
        catalog_digest=catalog_digest,
        wire_payload=wire,
    )
    return wire, epoch, prefix_error


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

    payload = _assistant_message_to_dict(message)
    payload["content"] = reply_text
    engine._memory.add_assistant_tool_message(payload)
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
    initial_tool_results: list[ToolCallResult] | None = None,
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
    def _finalize_result(**kwargs: Any) -> ChatResult:
        """统一出口：刷新 registry + checkpoint + 自动发射 MUTATION 事件。"""
        engine._try_refresh_registry()
        # 每轮结束保存会话快照（SessionState + TaskStore，不是文件检查点）
        engine.save_session_snapshot()
        # 自动发射 MUTATION 事件（写入路径由 Host 追踪，不靠结束工具申报）
        if engine._state.affected_files and on_event is not None:
            from excelmanus.events import EventType, ToolCallEvent, changed_mutations
            from excelmanus.workspace.identity import (
                collect_public_identities,
                workspace_root_of,
            )
            root = workspace_root_of(engine)
            changed = collect_public_identities(
                engine._state.affected_files,
                root,
            )
            engine.emit(
                on_event,
                ToolCallEvent(
                    event_type=EventType.MUTATION,
                    changed_files=changed,
                    mutations=changed_mutations(changed, workspace_root=root),
                ),
            )
        from excelmanus.workspace.scratch import leftover_reminder

        reminder = leftover_reminder(getattr(engine.config, "workspace_root", None))
        if reminder:
            reply = str(kwargs.get("reply") or "")
            kwargs["reply"] = f"{reply}\n\n{reminder}" if reply else reminder
        return ChatResult(**kwargs)

    max_failures = engine._config.max_consecutive_failures
    max_iterations = engine._config.max_iterations
    consecutive_failures = 0
    all_tool_results: list[ToolCallResult] = list(initial_tool_results or [])
    current_route_result = route_result
    # 恢复执行时保留之前的统计，仅首次调用时重置
    if start_iteration <= 1:
        if not initial_tool_results:
            engine._state.reset_loop_stats()
        else:
            engine._state.last_tool_call_count = len(initial_tool_results)
            engine._state.last_success_count = sum(result.success for result in initial_tool_results)
            engine._state.last_failure_count = len(initial_tool_results) - engine._state.last_success_count
        if engine._tool_dispatcher is not None:
            engine._tool_dispatcher.reset_cancel()
            engine._tool_dispatcher.begin_call_budget(
                max_iterations,
                reason=f"已达到本轮工具调用上限 ({max_iterations})",
            )
    tool_access = "may_write"
    # token 使用累计
    total_prompt_tokens = 0
    total_completion_tokens = 0
    # 诊断收集
    engine._turn_diagnostics = []

    for iteration in count(start_iteration):
        dispatcher = engine._tool_dispatcher
        budget_dead = (
            dispatcher is not None and not dispatcher.has_call_budget_remaining()
        )
        if iteration > max_iterations or budget_dead:
            if iteration > max_iterations:
                reply = f"已达到最大迭代次数 ({max_iterations})，已停止。"
                done_iter = max_iterations
            else:
                reply = f"已达到本轮工具调用上限 ({max_iterations})，已停止。"
                done_iter = max(start_iteration, iteration - 1)
            engine._memory.add_assistant_message(reply)
            engine._last_iteration_count = done_iter
            logger.warning("%s", reply)
            return _finalize_result(
                reply=reply,
                tool_calls=list(all_tool_results),
                iterations=done_iter,
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

        # 步前压缩挂在 Driver 附件上，不在循环体里分支。
        _claim_step = iteration > start_iteration or not skip_initial_inbox_claim or has_pending_user_edits(driver)
        if driver is not None and _claim_step:
            _step_claimed = await driver.consume_next_step(iteration=iteration)
            if _step_claimed:
                logger.info("下一步认领 %d 条 steer/inject", len(_step_claimed))

        _ctx_start = time.monotonic()
        prepared, context_error = await compile_request(
            engine,
            tool_access=tool_access,
            vision_capable=engine._is_vision_capable,
        )
        envelope = getattr(engine, "_last_envelope", None)
        if iteration == start_iteration:
            logger.debug("perf.loop: context_build %.0fms", (time.monotonic() - _ctx_start) * 1000)

        def _fail_closed_context(error_text: str) -> ChatResult:
            engine._last_iteration_count = iteration
            engine._last_failure_count += 1
            engine._memory.add_assistant_message(error_text)
            logger.warning("系统上下文预算检查失败，终止执行: %s", error_text)
            _emit_step_end()
            return _finalize_result(
                reply=error_text,
                tool_calls=list(all_tool_results),
                iterations=iteration,
                truncated=False,
                prompt_tokens=total_prompt_tokens,
                completion_tokens=total_completion_tokens,
                total_tokens=total_prompt_tokens + total_completion_tokens,
            )

        if context_error is not None or prepared is None or envelope is None:
            return _fail_closed_context(context_error or "系统上下文组装失败")

        from excelmanus.attachments.files_api import (
            accept_file_leases,
            collect_wire_file_ids,
            extract_file_ids_from_error,
            invalidate_file_ids,
            invalidate_scope,
            is_stale_file_error,
        )

        tools = envelope.tools
        tool_scope = None
        route, kwargs, stream_kwargs = _bind_prepared_outbound(engine, prepared)
        _files_base_url = route.endpoint
        _files_api_key = route.api_key
        _files_attempted = bool(prepared.file_leases)

        try:
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

            # Stream drafts immediately; retract only this iteration if delivery
            # verification requests another pass or the provider retries.
            streamed_text = False
            check_delivery = False
            try:
                from excelmanus.system_one.host import should_check_delivery

                check_delivery = bool(should_check_delivery(engine))
            except Exception:
                logger.debug("Jev 交付检查前置判断失败", exc_info=True)

            def _forward(event: Any) -> None:
                # consume_stream 已经通过 engine._emit 盖章/trace/审计过一次，
                # 这里只负责把事件交给外部回调，避免重复记录。
                try:
                    on_event(event)
                except Exception as exc:
                    logger.warning("事件回调异常: %s", exc)

            def _stream_on_event(event: Any) -> None:
                nonlocal streamed_text
                if getattr(event, "event_type", None) == EventType.TEXT_DELTA:
                    streamed_text = True
                _forward(event)

            def _retract_streamed_text() -> None:
                nonlocal streamed_text
                if streamed_text:
                    engine._emit(on_event, ToolCallEvent(
                        event_type=EventType.RETRACT_TEXT, iteration=iteration,
                    ))
                    streamed_text = False

            # ── LLM 调用 + 5xx/429 自动重试 ──
            _retry_max = engine._config.llm_retry_max_attempts
            _retry_base = engine._config.llm_retry_base_delay_seconds
            _retry_cap = engine._config.llm_retry_max_delay_seconds
            _auth_refresh_attempted = False  # 401 时仅尝试一次凭证刷新重试
            _stale_file_retried = False
            # while 而非 for：stale file_id / 401 刷新是一次性恢复（各有标志位），
            # 不应消耗重试预算，也不允许在预算耗尽时带着未绑定的 message 掉出循环。
            message: Any = None
            usage: Any = None
            _retry_attempt = 0
            while True:
                _retry_attempt += 1
                _retract_streamed_text()
                try:
                    try:
                        stream_or_response = await _await_with_turn_budget(
                            engine,
                            engine._llm_caller.create_chat_completion_with_retry(stream_kwargs),
                        )
                        # 检查返回值是否为异步迭代器（支持流式）
                        if hasattr(stream_or_response, "__aiter__"):
                            message, usage = await _await_with_turn_budget(
                                engine,
                                engine._llm_caller.consume_stream(
                                    stream_or_response,
                                    _stream_on_event if on_event is not None else None,
                                    iteration,
                                    _llm_start_ts=_llm_start_ts,
                                ),
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
                        _retract_streamed_text()
                        response = await _await_with_turn_budget(
                            engine,
                            engine._llm_caller.create_chat_completion_with_retry(kwargs),
                        )
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
                    if (
                        _files_attempted
                        and not _stale_file_retried
                        and is_stale_file_error(_retry_exc)
                    ):
                        _stale_file_retried = True
                        named = extract_file_ids_from_error(_retry_exc)
                        used = list(prepared.file_leases) or collect_wire_file_ids(
                            list(kwargs.get("messages") or [])
                        )
                        target_ids = [fid for fid in used if fid in named] if named else used
                        if target_ids:
                            invalidate_file_ids(
                                target_ids,
                                base_url=_files_base_url,
                                api_key=_files_api_key,
                                route=route,
                            )
                        else:
                            invalidate_scope(
                                base_url=_files_base_url,
                                api_key=_files_api_key,
                                route=route,
                            )
                        logger.warning("Files file_id 失效，失效索引后整请求重编译一次")
                        _release_open_attempt(engine)
                        series_of(engine).note("transport/renew")
                        try:
                            prepared, rebound_error = await compile_request(
                                engine,
                                tool_access=tool_access,
                                vision_capable=engine._is_vision_capable,
                                extra=getattr(engine, "_compile_extra", None),
                            )
                            if rebound_error is not None or prepared is None:
                                return _fail_closed_context(
                                    rebound_error or "系统上下文组装失败"
                                )
                            envelope = getattr(engine, "_last_envelope", None)
                            route, kwargs, stream_kwargs = _bind_prepared_outbound(engine, prepared)
                            _files_base_url = route.endpoint
                            _files_api_key = route.api_key
                            _files_attempted = bool(prepared.file_leases)
                        except Exception:
                            logger.warning("Files 重传失败，沿用最近一次编译体", exc_info=True)
                        continue

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
                            _release_open_attempt(engine)
                            series_of(engine).note("route/change")
                            prepared, rebound_error = await compile_request(
                                engine,
                                tool_access=tool_access,
                                vision_capable=engine._is_vision_capable,
                                extra=getattr(engine, "_compile_extra", None),
                                event="route/change",
                            )
                            if rebound_error is not None or prepared is None:
                                return _fail_closed_context(
                                    rebound_error or "凭证刷新后请求重编译失败"
                                )
                            envelope = getattr(engine, "_last_envelope", None)
                            route, kwargs, stream_kwargs = _bind_prepared_outbound(engine, prepared)
                            _files_base_url = route.endpoint
                            _files_api_key = route.api_key
                            _files_attempted = bool(prepared.file_leases)
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

            from excelmanus.request.types import PreparedRequest

            sent = getattr(engine, "_sent_prepared_request", None)
            if isinstance(sent, PreparedRequest):
                prepared = sent
                route = sent.route
            if not getattr(message, "_stream_truncated", False):
                series_of(engine).accept(prepared.header)
                accept_file_leases(prepared.file_leases)
            if getattr(message, "replay_state", None) is not None:
                message.replay_source = {"protocol": route.protocol, "model": route.model}
                if route.protocol == "openai_responses":
                    message.replay_source["compaction_generation"] = int(getattr(engine, "_compaction_generation", 0) or 0)
                    from excelmanus.request.compiler import content_payload
                    from excelmanus.prompt.envelope import digest_text

                    message.replay_source["credential_scope"] = route.credential_scope
                    message.replay_source["request_content_identity"] = prepared.header.content_identity
                    message.replay_source["output_identity"] = digest_text(
                        content_payload([_assistant_message_to_dict(message)])
                    )
                replay = getattr(message, "replay_state", None)
                response_id = replay.get("response_id") if isinstance(replay, dict) else None
                if isinstance(response_id, str) and response_id.strip():
                    if route.protocol == "openai_responses":
                        engine._responses_last_response = {
                            "id": response_id.strip(),
                            "protocol": route.protocol,
                            "model": route.model,
                            "compaction_generation": int(getattr(engine, "_compaction_generation", 0) or 0),
                        }
        except asyncio.CancelledError:
            series_of(engine).cancel()
            raise
        finally:
            _release_open_attempt(engine)

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
        engine._last_model_response_at = time.monotonic()
        reset_idle_tracker(engine)  # 每个响应-请求间隔只统计本间隔内的空闲段

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

        # 图片：历史保持 append-only ref，投影只发生在本次请求。

        # 累计 token 使用量
        if usage is not None:
            total_prompt_tokens += _usage_token(usage, "prompt_tokens")
            total_completion_tokens += _usage_token(usage, "completion_tokens")
            budget = getattr(engine, "_turn_budget", None)
            if budget is not None:
                budget.record_usage(usage)
                if budget.exhausted_reason:
                    reason_text = {
                        "tokens": "已达到本轮 token 预算",
                        "cost": "已达到本轮成本预算",
                        "wall_clock": "已达到本轮 wall-clock 预算",
                    }.get(budget.exhausted_reason, "已达到本轮预算")
                    reply = f"{reason_text}，已停止继续调用模型和工具。"
                    engine._memory.add_assistant_message(reply)
                    engine._last_iteration_count = iteration
                    _emit_step_end()
                    return _finalize_result(
                        reply=reply,
                        tool_calls=list(all_tool_results),
                        iterations=iteration,
                        truncated=True,
                        prompt_tokens=total_prompt_tokens,
                        completion_tokens=total_completion_tokens,
                        total_tokens=total_prompt_tokens + total_completion_tokens,
                    )

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
        # provider 真实计量锚定：下一次压力测量直接以 prompt_tokens 为基准
        if iter_prompt > 0:
            try:
                engine._memory.note_provider_prompt_tokens(iter_prompt)
            except Exception:
                logger.debug("usage 锚点记录失败", exc_info=True)
        cache_usage = extract_cache_usage(usage)
        iter_cache_creation, iter_cache_read = _extract_anthropic_cache_tokens(usage)
        iter_ttft = _extract_ttft_ms(usage)
        diag = TurnDiagnostic(
            iteration=iteration,
            prompt_tokens=iter_prompt,
            completion_tokens=iter_completion,
            cached_tokens=cache_usage.hit,
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

        # ── 缓存命中观测：每步可见 + 持续 miss 告警 ──
        # 前缀稳定是设计目标，命中退化必须可观测（DSH 以 e2e 断言保证；
        # 这里用运行时比率告警兜底：长历史 + 连续低命中 = 前缀在某处漂移）。
        if cache_usage.hit is None:
            logger.info(
                "cache: hit=unknown / prompt=%d iter=%d reason=%s",
                iter_prompt, iteration, cache_usage.miss_reason,
            )
            engine._cache_miss_streak = 0
        elif iter_prompt >= 2000:
            iter_cache_read_total = max(cache_usage.hit, iter_cache_read)
            ratio = iter_cache_read_total / iter_prompt
            logger.info(
                "cache: read=%d / prompt=%d (%.0f%%) iter=%d",
                iter_cache_read_total, iter_prompt, ratio * 100, iteration,
            )
            if ratio < 0.1:
                engine._cache_miss_streak = int(getattr(engine, "_cache_miss_streak", 0)) + 1
            else:
                engine._cache_miss_streak = 0
            if int(getattr(engine, "_cache_miss_streak", 0)) >= 3 and not getattr(
                engine, "_cache_miss_warned", False
            ):
                engine._cache_miss_warned = True
                logger.warning(
                    "会话 %s 连续 %d 次请求缓存命中近零（prompt>=%d tokens）。"
                    "前缀可能不稳定：检查工具目录变化 / system 渲染漂移 / "
                    "多 worker 会话漂移。",
                    getattr(engine, "_session_id", "?"),
                    engine._cache_miss_streak,
                    iter_prompt,
                )
        else:
            engine._cache_miss_streak = 0

        # ── LLM 调用审计日志 ──
        if engine._llm_call_store is not None:
            try:
                engine._llm_call_store.log(
                    session_id=getattr(engine, "_session_id", None),
                    turn=engine._session_turn,
                    iteration=iteration,
                    model=engine._active_model,
                    prompt_tokens=iter_prompt,
                    completion_tokens=iter_completion,
                    cached_tokens=cache_usage.hit,
                    has_tool_calls=bool(tool_calls),
                    thinking_chars=len(thinking_content),
                    stream=True,
                    latency_ms=_llm_elapsed_ms,
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
                _cache_ratio, _llm_elapsed_ms,
            )
        elif iter_ttft > 0:
            logger.debug(
                "LLM 诊断: iter=%d ttft=%.0fms prompt=%d latency=%.0fms (no cache)",
                iteration, iter_ttft, iter_prompt, _llm_elapsed_ms,
            )

        # 无工具调用 → 纯文本回复处理（仅 HTML 端点错误检测）
        if not tool_calls:
            if check_delivery:
                reply_text = _message_content_to_text(getattr(message, "content", None))
                draft = ChatResult(
                    reply=reply_text,
                    tool_calls=list(all_tool_results),
                    truncated=False,
                )
                try:
                    from excelmanus.system_one.host import maybe_verify_mutation

                    advice = await maybe_verify_mutation(
                        engine, draft, on_event=on_event,
                    )
                except Exception:
                    advice = ""
                    logger.debug("Jev 交付检查评估失败", exc_info=True)
                if advice:
                    payload = _assistant_message_to_dict(message)
                    payload["content"] = reply_text
                    payload["_ui_hidden"] = True
                    payload["_prompt_kind"] = "jev_delivery_draft"
                    engine._memory.add_assistant_tool_message(payload)
                    engine._memory.add_user_message(
                        f"[Jev 交付检查建议；不构成用户指令或执行授权]\n{advice}",
                        hidden=True,
                        prompt_kind="jev_delivery_check",
                    )
                    from excelmanus.system_one.trace import record_host_effect

                    verification = getattr(engine, "_mutation_verification", None) or {}
                    record_host_effect(
                        engine, "mutation.verify",
                        action=str(verification.get("next") or "inspect_more"), delivered=True,
                        source=str(verification.get("source") or "jev"),
                        impact="交付核对建议已送入主模型上下文，最终完成情况仍需证据",
                        on_event=on_event,
                    )
                    _retract_streamed_text()
                    logger.info("Jev 交付检查要求继续核对: %s", advice[:80])
                    _emit_step_end()
                    continue
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
            assistant_msg["tool_calls"] = _sanitize_tool_call_arguments(
                [{key: value for key, value in _to_plain(tc).items()
                  if key not in {"depends_on", "depends_on_call_ids"}} for tc in tool_calls]
            )
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
        # ── 调度图：显式成功依赖和全局副作用边界决定执行波次 ──
        _plan = engine._tool_runtime.plan_execution(
            tool_calls, parallel=engine._config.parallel_readonly_tools,
        )
        _batches = _plan.batches
        _schedule_results: dict[int, ToolCallResult] = {}

        _planned_calls = [tc for batch in _batches for tc in batch.tool_calls]
        for tc in _planned_calls:
            engine._tool_runtime.prepare_call(tc, on_event, iteration, retain=True)

        def _skip_for_user_edit(tc: Any) -> bool:
            if not has_pending_user_edits(driver):
                return False
            text = "USER_EDIT_PENDING: 用户已修改工作簿；本工具尚未执行。请先阅读下一步改动简报，重新读取后再决定操作。"
            result = ToolCallResult(tool_name=tc.function.name, arguments={}, result=text,
                                    success=False, error="USER_EDIT_PENDING")
            result = engine._tool_runtime.finish_queued(tc, result)
            all_tool_results.append(result)
            _schedule_results[id(tc)] = result
            if tc.id:
                engine._memory.add_tool_result(tc.id, result.result)
            return True

        try:
            for _batch in _batches:
                ready_calls = []
                for tc in _batch.tool_calls:
                    if _skip_for_user_edit(tc):
                        continue
                    blocked = _plan.blocked_result(tc, _schedule_results)
                    if blocked is None:
                        ready_calls.append(tc)
                        continue
                    # A dependency skip is a paired, durable result, but it is not
                    # another executed-tool failure for the consecutive breaker.
                    blocked = engine._tool_runtime.finish_queued(tc, blocked)
                    all_tool_results.append(blocked)
                    _schedule_results[id(tc)] = blocked
                    if tc.id:
                        engine._memory.add_tool_result(tc.id, blocked.result)
                    engine._last_tool_call_count += 1
                    engine._last_failure_count += 1
                _batch = _ToolCallBatch(ready_calls, _batch.parallel and len(ready_calls) > 1)
                if not ready_calls:
                    continue
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
                        engine._tool_runtime.finish_queued(tc, all_tool_results[-1])
                        if tool_call_id:
                            engine._memory.add_tool_result(tool_call_id, breaker_skip_error)
                    continue

                if _batch.parallel:
                    _reclassified = engine._tool_runtime.reclassify_batch(_batch.tool_calls)
                    if len(_reclassified) != 1 or not _reclassified[0].parallel:
                        _batch = _ToolCallBatch(list(_batch.tool_calls), False)
                if _batch.parallel:
                    # ── 并行路径：只读工具并发执行 ──
                    with idle_segment(engine, "tool"):
                        _parallel_results = await engine._execute_tool_calls_parallel(
                            _batch.tool_calls, tool_scope, on_event, iteration,
                            route_result=current_route_result,
                        )
                    # P3-a 同批同因折叠：durable 存全文，wire 投影存指针。
                    _dedup_plan = plan_parallel_dedup(list(_parallel_results))
                    for _p_index, (_p_tc, _p_tc_result) in enumerate(_parallel_results):
                        tc, tc_result = _p_tc, _p_tc_result
                        function = getattr(tc, "function", None)
                        tool_name = getattr(function, "name", "")
                        tool_call_id = getattr(tc, "id", "")

                        all_tool_results.append(tc_result)
                        _schedule_results[id(tc)] = tc_result

                        # 按序写入 memory
                        if not tc_result.defer_tool_result and tool_call_id:
                            _pointer = _dedup_plan.get(_p_index)
                            if getattr(tc, "_pending_result_written", False):
                                engine._memory.replace_tool_result(tool_call_id, tc_result.result)
                            elif _pointer is not None and not breaker_triggered:
                                engine._memory.add_tool_result(
                                    tool_call_id,
                                    tc_result.result,
                                    projection_content=_pointer,
                                )
                            else:
                                engine._memory.add_tool_result(tool_call_id, tc_result.result)

                        engine._interaction_handler.consume_tool_result(tool_call_id)

                        # 统计更新（只读工具不触发 write_effect 分支）
                        engine._last_tool_call_count += 1
                        if tc_result.success:
                            engine._last_success_count += 1
                            consecutive_failures = 0
                        else:
                            engine._last_failure_count += 1
                            if tc_result.error != "CANCELLED":
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

                        if _skip_for_user_edit(tc):
                            continue
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
                            engine._tool_runtime.finish_queued(tc, all_tool_results[-1])
                            if tool_call_id:
                                engine._memory.add_tool_result(tool_call_id, breaker_skip_error)
                            continue

                        with idle_segment(engine, "tool"):
                            tc_result = await engine._tool_runtime.run_managed(
                                tc, lambda: _execute_and_resolve_tool(
                                    engine, tc, tool_scope, on_event, iteration, current_route_result, approval_resolver,
                                ), on_event, iteration,
                            )
                        all_tool_results.append(tc_result)
                        if not tc_result.defer_tool_result and tool_call_id:
                            if getattr(tc, "_pending_result_written", False):
                                engine._memory.replace_tool_result(tool_call_id, tc_result.result)
                            else:
                                engine._memory.add_tool_result(tool_call_id, tc_result.result)
                        engine._interaction_handler.consume_tool_result(tool_call_id)

                        # 更新统计
                        _schedule_results[id(tc)] = tc_result
                        engine._last_tool_call_count += 1
                        if tc_result.success:
                            engine._last_success_count += 1
                            consecutive_failures = 0
                            # 计划/只读 PERMISSION_DENIED 为失败，不会走到这里。
                            _write_effect = write_effect_for_call(
                                tc_result.tool_name,
                                tc_result.arguments,
                                declared=engine._get_tool_write_effect(tc_result.tool_name),
                            )
                            if _write_effect == "workspace_write":
                                engine._record_workspace_write_action()
                            elif _write_effect == "external_write":
                                engine._record_external_write_action()
                        else:
                            engine._last_failure_count += 1
                            # 已在 ToolDispatcher 中自动重试过的 retryable 错误
                            # 不再计入熔断计数（重试已耗尽说明是持续性故障）
                            if tc_result.error != "CANCELLED":
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

        finally:
            engine._tool_runtime.end_batch(_planned_calls)

        # 说明：旧的 ask_user 退出路径已移除。
        # 阻塞式 ask_user 在 AskUserHandler 内 await Future，
        # 返回用户回答作为 tool result，循环不中断。

        # ── 延迟图片注入：所有 tool_result 写入 memory 后再注入 user 图片消息 ──
        # 如果在 tool_result 之前注入，会破坏 assistant(tool_calls) → tool(responses)
        # 的消息序列，导致 OpenAI 兼容 API 返回 400 错误。
        engine._tool_dispatcher.flush_deferred_images()

        # Consume the enabled Jev post-batch decision before the next model
        # request.  The advice is hidden context; the user-facing reply still
        # comes from the main model and the breaker remains authoritative.
        jev_recovery_advice = ""
        try:
            from excelmanus.system_one.host import maybe_advise_after_tools

            jev_recovery_advice = await maybe_advise_after_tools(
                engine,
                list(all_tool_results),
                breaker_triggered=breaker_triggered,
                iteration=iteration,
                on_event=on_event,
            )
        except Exception:
            logger.debug("post-batch Jev advice failed; continuing turn", exc_info=True)

        if breaker_triggered:
            reply = (
                f"连续 {max_failures} 次工具调用失败，已终止执行。"
                f"错误摘要：\n{breaker_summary}"
            )
            if jev_recovery_advice:
                reply = f"{reply}\n\n{jev_recovery_advice}"
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
