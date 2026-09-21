"""控制面：followup / steer / inject / 斜杠路由。

斜杠与待答问题不进模型历史。Driver 只认领 inbox 并跑 step。
"""

from __future__ import annotations

import time
from typing import Any

from excelmanus.engine_types import (
    ApprovalResolver,
    ChatResult,
    QuestionResolver,
)
from excelmanus.engine_utils import _summarize_text
from excelmanus.events import EventCallback, EventType, ToolCallEvent
from excelmanus.hooks import HookDecision, HookEvent
from excelmanus.logger import get_logger
from excelmanus.mentions.parser import MentionParser, ResolvedMention
from excelmanus.engine_core.skill_resolver import SkillResolver
from excelmanus.skillpacks import SkillMatchResult

logger = get_logger("agent.session_api")


def _ui_tool_access_from_chat_mode(chat_mode: str) -> str:
    if chat_mode == "read":
        return "read_only"
    return "may_write"

def push_guide_message(engine, message: str) -> None:
    """guide / hook 注入：inbox next-step，不单独唤醒。"""
    text = str(message or "").strip()
    if text:
        engine.inject(text)

def drain_guide_messages(engine) -> list[str]:
    """取出并清空未认领的 next-step（兼容旧 API）。"""
    return engine._driver.inbox.drain_unclaimed("next-step")

def push_interrupt_message(engine, message: str) -> None:
    """飞行中的用户后续：inbox next-turn，当前 turn 不进入组装。"""
    text = str(message or "").strip()
    if text:
        engine._driver.enqueue_followup(text)

def drain_interrupt_messages(engine) -> list[str]:
    """取出并清空未认领的 next-turn（兼容旧 API）。"""
    return engine._driver.inbox.drain_unclaimed("next-turn")

def steer(engine, message: str):
    """步中插话 → next-step，下一步可见，不打断当前工具批。"""
    text = str(message or "").strip()
    if not text:
        return None
    return engine._driver.steer(text)

def inject(engine, message: str):
    """注入 → next-step，不单独唤醒。"""
    text = str(message or "").strip()
    if not text:
        return None
    return engine._driver.inject(text)

async def followup(
    engine,
    user_message: str,
    on_event: EventCallback | None = None,
    slash_command: str | None = None,
    raw_args: str | None = None,
    mention_contexts: list[ResolvedMention] | None = None,
    images: list[dict[str, Any]] | None = None,
    approval_resolver: ApprovalResolver | None = None,
    question_resolver: QuestionResolver | None = None,
    chat_mode: str = "write",
    context_input: dict[str, Any] | None = None,
) -> ChatResult:
    """用户后续：控制面处理完毕后入 inbox next-turn 并 wakeup。

    斜杠与待答问题不进模型历史。不在这里探查工作簿或分析任务意图。
    """
    normalized_images: list[dict[str, str]] = []
    for item in images or []:
        if not isinstance(item, dict):
            continue
        attachment_id = str(item.get("attachment_id") or "").strip()
        if not attachment_id:
            continue
        media_type = str(item.get("media_type", "image/png") or "image/png").strip() or "image/png"
        detail_raw = str(item.get("detail", "auto") or "auto").strip().lower()
        detail = detail_raw if detail_raw in {"auto", "low", "high"} else "auto"
        row: dict[str, str] = {
            "attachment_id": attachment_id,
            "media_type": media_type,
            "detail": detail,
        }
        normalized_images.append(row)

    if normalized_images:
        logger.info(
            "收到 %d 张图片附件 (media_types=%s, attachment_ids=%s)",
            len(normalized_images),
            [img["media_type"] for img in normalized_images],
            [img.get("attachment_id", "") for img in normalized_images],
        )
        for img in normalized_images:
            engine._tool_dispatcher._injected_image_hashes.add(img["attachment_id"])

    # ── 视觉能力前置检查：附件只交给激活模型阅读 ──
    if normalized_images and not engine._is_vision_capable:
        reject_msg = (
            "当前模型不支持图片识别，无法处理图片附件。\n\n"
            "请切换到支持视觉的模型，或设置 `EXCELMANUS_MAIN_MODEL_VISION=true`。"
        )
        logger.warning(
            "拒绝图片请求: main_vision=%s",
            engine._is_vision_capable,
        )
        return ChatResult(reply=reject_msg)

    command_parts = user_message.strip().split(maxsplit=1)
    if command_parts and command_parts[0].lower() == "/resume":
        return await engine._driver.resume(
            command_parts[1] if len(command_parts) > 1 else "", on_event=on_event,
            approval_resolver=approval_resolver, question_resolver=question_resolver,
        )
    if (command_parts and command_parts[0].lower() in {"/accept", "/reject"}
            and len(command_parts) == 2 and not engine._driver.running
            and engine._interaction_handler.approval_is_actionable(command_parts[1].strip())):
        from excelmanus.chat_turn import submit_approval
        submit_approval(engine, command_parts[1].strip(), "accept" if command_parts[0].lower() == "/accept" else "reject")
        return await engine._driver.resume(on_event=on_event, question_resolver=question_resolver)

    pending_question = engine._question_flow.current()
    if pending_question is not None and not engine._driver.running and engine._interaction_handler.can_recover():
        if not user_message.strip().startswith("/"):
            from excelmanus.chat_turn import submit_question_answer
            submit_question_answer(engine, pending_question.question_id, user_message)
            return await engine._driver.resume(on_event=on_event, question_resolver=question_resolver)

    if engine._question_flow.has_pending() and not engine._driver.running:
        engine._question_resolver = question_resolver
        engine._mention_contexts = mention_contexts or []
        engine._ingest_mention_versions(mention_contexts)
        pending_chat_start = time.monotonic()
        pending_result = await engine._interaction_handler.handle_pending_question_answer(
            user_message=user_message,
            on_event=on_event,
        )
        if pending_result is not None:
            if pending_result.iterations > 0:
                elapsed = time.monotonic() - pending_chat_start
                engine._emit(
                    on_event,
                    ToolCallEvent(
                        event_type=EventType.CHAT_SUMMARY,
                        total_iterations=engine._last_iteration_count,
                        total_tool_calls=engine._last_tool_call_count,
                        success_count=engine._last_success_count,
                        failure_count=engine._last_failure_count,
                        elapsed_seconds=round(elapsed, 2),
                        prompt_tokens=pending_result.prompt_tokens,
                        completion_tokens=pending_result.completion_tokens,
                        total_tokens=pending_result.total_tokens,
                    ),
                )
            return pending_result

    control_reply = await engine._command_handler.handle(user_message, on_event=on_event)
    if control_reply is not None:
        logger.info("控制命令执行: %s", _summarize_text(user_message))
        engine._driver._persist_runtime_state()
        return ChatResult(reply=control_reply)

    # 待审批只卡住同一 tool_call_id 的回执路径，不阻塞无关的新用户回合。
    item = engine._driver.enqueue_followup(
        user_message,
        extra={
            "on_event": on_event,
            "slash_command": slash_command,
            "raw_args": raw_args or "",
            "mention_contexts": mention_contexts,
            "images": normalized_images,
            "approval_resolver": approval_resolver,
            "question_resolver": question_resolver,
            "chat_mode": chat_mode,
            "context_input": context_input or {},
        },
    )
    result = await engine._driver.wait_for_item(item)
    return result if isinstance(result, ChatResult) else ChatResult(reply="")

async def apply_claimed_followup(engine, item: Any) -> ChatResult | None:
    """认领后才路由并写入 memory。返回 ChatResult 表示短路径结束本 turn。"""
    extra = item.extra if isinstance(getattr(item, "extra", None), dict) else {}
    engine._question_resolver = extra.get("question_resolver")
    user_message = str(item.content or "")
    on_event: EventCallback | None = extra.get("on_event")
    slash_command = extra.get("slash_command")
    raw_args = extra.get("raw_args") or ""
    mention_contexts = extra.get("mention_contexts")
    normalized_images: list[dict[str, str]] = list(extra.get("images") or [])
    chat_mode = extra.get("chat_mode") or "write"
    chat_start = time.monotonic()

    # 认领后先替换引用快照；技能短路分支也不能留下上一轮的待注入引用。
    engine._mention_contexts = mention_contexts or []
    engine._ingest_mention_versions(mention_contexts)

    from excelmanus.plan_mode import apply_chat_mode
    # 请求体 chat_mode 是权威：点「编辑」必须能离开 plan，禁止被旧 _plan_active 粘住。
    # tab / 请求切到 read 也是用户显式退出（清 pending），与 /plan off 等价。
    apply_chat_mode(
        engine,
        str(chat_mode or "write"),
        source="request",
        on_event=on_event,
    )

    def _add_user_turn_to_memory(text: str) -> None:
        if not normalized_images:
            engine._memory.add_user_message(text)
            return
        from excelmanus.attachments.store import get_attachment_store
        from excelmanus.attachments.types import AttachmentError

        parts: list[dict[str, Any]] = []
        if text:
            parts.append({"type": "text", "text": text})
        store = get_attachment_store()
        for image in normalized_images:
            attachment_id = image.get("attachment_id") or ""
            ref = store.get_ref(attachment_id)
            if ref is None:
                raise AttachmentError(
                    f"附件不存在或已过期: {attachment_id}",
                    "ATTACHMENT_MISSING",
                )
            parts.append({"type": "image", "attachment": ref.to_dict()})
            engine._tool_dispatcher._injected_image_hashes.add(ref.attachment_id)
        engine._memory.add_user_message(parts if parts else text)

    effective_slash_command = slash_command
    effective_raw_args = raw_args or ""

    if effective_slash_command is None:
        manual_skill_with_args = engine._skill_resolver.resolve_skill_command_with_args(user_message)
        if manual_skill_with_args is not None:
            effective_slash_command, effective_raw_args = manual_skill_with_args

    if effective_slash_command is None and mention_contexts:
        for rm in mention_contexts:
            if rm.mention.kind == "skill" and not rm.error:
                effective_slash_command = rm.mention.value
                parse_result = MentionParser.parse(user_message)
                effective_raw_args = parse_result.clean_text
                break

    route_result = await engine._route_skills(
        user_message,
        slash_command=effective_slash_command,
        raw_args=effective_raw_args if effective_slash_command else None,
        chat_mode=engine._current_chat_mode,
        on_event=on_event,
        images=normalized_images if normalized_images else None,
    )

    route_result, user_message = await engine._adapt_guidance_only_slash_route(
        route_result=route_result,
        user_message=user_message,
        slash_command=effective_slash_command,
        raw_args=effective_raw_args,
    )
    engine._last_route_result = route_result

    if effective_slash_command and route_result.route_mode == "slash_not_user_invocable":
        reply = f"技能 `{effective_slash_command}` 不允许手动调用。"
        _add_user_turn_to_memory(user_message)
        engine._memory.add_assistant_message(reply)
        engine._last_iteration_count = 1
        engine._last_tool_call_count = 0
        engine._last_success_count = 0
        engine._last_failure_count = 1
        engine._emit_short_circuit_summary(on_event, chat_start)
        return ChatResult(reply=reply, tool_calls=[], iterations=1, truncated=False)

    if effective_slash_command and route_result.route_mode == "slash_not_found":
        normalized_cmd = SkillResolver.normalize_skill_command_name(effective_slash_command)
        blocked = engine._skill_resolver.blocked_skillpacks()
        if blocked and normalized_cmd in blocked:
            reply = (
                f"技能 `{effective_slash_command}` 当前受访问限制，"
                f"请先执行 `/fullaccess on` 解除限制后再试。"
            )
        else:
            reply = f"未找到技能 `{effective_slash_command}`，请通过 `/skills` 查看可用技能列表。"
        _add_user_turn_to_memory(user_message)
        engine._memory.add_assistant_message(reply)
        engine._last_iteration_count = 1
        engine._last_tool_call_count = 0
        engine._last_success_count = 0
        engine._last_failure_count = 1
        engine._emit_short_circuit_summary(on_event, chat_start)
        return ChatResult(reply=reply, tool_calls=[], iterations=1, truncated=False)

    selected_skill = engine._skill_resolver.pick_route_skill(route_result)
    if selected_skill is not None:
        user_prompt_hook_raw = engine._skill_resolver.run_skill_hook(
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
        user_prompt_hook = await engine._skill_resolver.resolve_hook_result(
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
            engine._memory.add_assistant_message(reply)
            engine._last_iteration_count = 1
            engine._last_tool_call_count = 0
            engine._last_success_count = 0
            engine._last_failure_count = 1
            engine._emit_short_circuit_summary(on_event, chat_start)
            return ChatResult(reply=reply, tool_calls=[], iterations=1, truncated=False)

    engine._turn_image_count = len(normalized_images)
    from excelmanus.system_one.host import maybe_record_turn_exposure

    await maybe_record_turn_exposure(engine, user_message, on_event=on_event)
    from excelmanus.system_one.intent_context import suggest_context

    context_advice = await suggest_context(engine, user_message, extra.get("context_input"), on_event=on_event)

    from excelmanus.prompt.skill_catalog import prepare_skill_followup
    route_result, skill_invocation = prepare_skill_followup(
        engine,
        user_message=user_message,
        slash_command=effective_slash_command,
        route_result=route_result,
    )
    final_skills_used = list(route_result.skills_used)
    if engine._active_skills:
        for skill in engine._active_skills:
            if skill.name not in final_skills_used:
                final_skills_used.append(skill.name)
    route_result = SkillMatchResult(
        skills_used=final_skills_used,
        tool_scope=[],
        route_mode=getattr(route_result, "route_mode", "all_tools"),
        system_contexts=[],
        parameterized=route_result.parameterized,
    )
    engine._last_route_result = route_result
    _add_user_turn_to_memory(user_message)
    if context_advice:
        engine._memory.add_user_message(context_advice, hidden=True, prompt_kind="jev_context_advice")
    if skill_invocation:
        engine._memory.add_user_message(
            skill_invocation, hidden=True, prompt_kind="skill_invocation",
        )
    logger.info(
        "用户指令摘要: %s | skills=%s",
        _summarize_text(user_message),
        route_result.skills_used,
    )
    item.content = user_message
    return None

def emit_short_circuit_summary(
    engine,
    on_event: EventCallback | None,
    chat_start: float,
) -> None:
    elapsed = time.monotonic() - chat_start
    engine._emit(
        on_event,
        ToolCallEvent(
            event_type=EventType.CHAT_SUMMARY,
            total_iterations=engine._last_iteration_count,
            total_tool_calls=engine._last_tool_call_count,
            success_count=engine._last_success_count,
            failure_count=engine._last_failure_count,
            elapsed_seconds=round(elapsed, 2),
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
        ),
    )

def finalize_driver_turn(
    engine,
    chat_result: ChatResult,
    *,
    on_event: EventCallback | None,
    chat_start: float,
) -> None:
    route_result = engine._last_route_result
    chat_result.tool_access = _ui_tool_access_from_chat_mode(
        getattr(engine, "_current_chat_mode", "write"),
    )
    chat_result.route_mode = getattr(route_result, "route_mode", "") or ""
    chat_result.skills_used = list(getattr(route_result, "skills_used", []) or [])
    chat_result.turn_diagnostics = list(engine._turn_diagnostics)

    _injection_summary_for_diag: list[dict[str, Any]] = []
    if engine._state.prompt_injection_snapshots:
        _latest = engine._state.prompt_injection_snapshots[-1]
        if _latest.get("session_turn") == engine._session_turn:
            _injection_summary_for_diag = _latest.get("summary", [])
    engine._session_diagnostics.append({
        "session_turn": engine._session_turn,
        "tool_access": chat_result.tool_access,
        "route_mode": chat_result.route_mode,
        "skills_used": list(chat_result.skills_used),
        "iterations": chat_result.iterations,
        "prompt_tokens": chat_result.prompt_tokens,
        "completion_tokens": chat_result.completion_tokens,
        "total_tokens": chat_result.total_tokens,
        "write_guard_triggered": chat_result.write_guard_triggered,
        "turn_diagnostics": [d.to_dict() for d in engine._turn_diagnostics],
        "prompt_injection_summary": _injection_summary_for_diag,
    })

    elapsed = time.monotonic() - chat_start
    engine._emit(
        on_event,
        ToolCallEvent(
            event_type=EventType.CHAT_SUMMARY,
            total_iterations=engine._last_iteration_count,
            total_tool_calls=engine._last_tool_call_count,
            success_count=engine._last_success_count,
            failure_count=engine._last_failure_count,
            elapsed_seconds=round(elapsed, 2),
            prompt_tokens=chat_result.prompt_tokens,
            completion_tokens=chat_result.completion_tokens,
            total_tokens=chat_result.total_tokens,
        ),
    )
    from excelmanus.system_one.host import (
        clear_turn_exposure,
        emit_recovery_outcome,
        remember_turn_tools,
    )

    remember_turn_tools(engine, chat_result)
    try:
        emit_recovery_outcome(engine, on_event=on_event)
    except Exception:
        logger.debug("Jev 恢复结果记录失败；继续回合收尾", exc_info=True)
    clear_turn_exposure(engine)


# ── Skill 解析与 Hook 管理（委托到 SkillResolver）──────────

async def adapt_guidance_only_slash_route(
    engine,
    *,
    route_result: SkillMatchResult,
    user_message: str,
    slash_command: str | None,
    raw_args: str,
) -> tuple[SkillMatchResult, str]:
    """斜杠带任务文本时，把用户可见句子换成任务文本；技能正文仍走 followup。"""
    if not slash_command or route_result.route_mode != "slash_direct":
        return route_result, user_message

    task_text = raw_args.strip()
    if not task_text:
        return route_result, user_message

    logger.info(
        "斜杠技能 %s 带任务文本，进入循环: %s",
        slash_command,
        _summarize_text(task_text),
    )
    return route_result, task_text

async def route_skills(
    engine,
    user_message: str,
    *,
    slash_command: str | None = None,
    raw_args: str | None = None,
    chat_mode: str = "write",
    on_event: EventCallback | None = None,
    images: list[dict[str, Any]] | None = None,
) -> SkillMatchResult:
    if engine._skill_router is None:
        return SkillMatchResult(
            skills_used=[],
            route_mode="all_tools",
            system_contexts=[],
        )

    blocked_skillpacks = (
        set(engine._restricted_code_skillpacks)
        if not engine._full_access_enabled
        else None
    )
    del user_message, chat_mode, on_event, images
    return await engine._skill_router.parse_slash_skill(
        slash_command,
        raw_args=raw_args,
        blocked_skillpacks=blocked_skillpacks,
    )
