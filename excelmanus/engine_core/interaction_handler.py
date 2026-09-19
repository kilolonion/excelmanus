"""问答与审批交互处理 — 从 AgentEngine 提取的用户交互逻辑。

包括：
- ask_user 工具处理（非阻塞/阻塞模式）
- 问题队列管理与事件发射
- 待回答问题的用户输入解析与路由恢复
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

from excelmanus.engine_utils import (
    _SYSTEM_Q_MODE_SWITCH,
    _SYSTEM_Q_PLAN_EXIT,
    _SYSTEM_Q_SUBAGENT_APPROVAL,
)
from excelmanus.interaction import DEFAULT_INTERACTION_TIMEOUT
from excelmanus.logger import get_logger

if TYPE_CHECKING:
    from excelmanus.approval import PendingApproval
    from excelmanus.engine import AgentEngine
    from excelmanus.engine_types import ChatResult
    from excelmanus.events import EventCallback
    from excelmanus.question_flow import PendingQuestion

logger = get_logger("interaction_handler")


class InteractionHandler:
    """问答与审批交互处理器。

    通过 ``self._engine`` 引用访问 AgentEngine 的问题队列、审批管理器、
    交互注册表等状态。
    """

    def __init__(self, engine: "AgentEngine") -> None:
        self._engine = engine

    # ── 事件发射辅助 ──────────────────────────────────────

    @staticmethod
    def question_options_payload(question: "PendingQuestion") -> list[dict[str, str]]:
        return [
            {
                "label": option.label,
                "description": option.description,
            }
            for option in question.options
        ]

    def emit_user_question_event(
        self,
        *,
        question: "PendingQuestion",
        on_event: "EventCallback | None",
        iteration: int,
    ) -> None:
        from excelmanus.events import EventType, ToolCallEvent

        e = self._engine
        e._emit(
            on_event,
            ToolCallEvent(
                event_type=EventType.USER_QUESTION,
                question_id=question.question_id,
                question_header=question.header,
                question_text=question.text,
                question_options=self.question_options_payload(question),
                question_multi_select=question.multi_select,
                question_queue_size=e._question_flow.queue_size(),
                iteration=iteration,
            ),
        )

    def emit_pending_approval_event(
        self,
        *,
        pending: "PendingApproval",
        on_event: "EventCallback | None",
        iteration: int,
        tool_call_id: str = "",
    ) -> None:
        """发射待确认审批事件，供前端渲染审批卡片。"""
        from excelmanus.events import EventType, ToolCallEvent
        from excelmanus.tools.policy import get_tool_risk_level, sanitize_approval_args_summary

        self._engine._emit(
            on_event,
            ToolCallEvent(
                event_type=EventType.PENDING_APPROVAL,
                tool_call_id=tool_call_id,
                approval_id=pending.approval_id,
                approval_tool_name=pending.tool_name,
                approval_arguments=dict(pending.arguments),
                approval_risk_level=get_tool_risk_level(pending.tool_name),
                approval_args_summary=sanitize_approval_args_summary(pending.arguments),
                iteration=iteration,
            ),
        )

    # ── ask_user 处理 ──────────────────────────────────────

    def handle_ask_user(
        self,
        *,
        arguments: dict[str, Any],
        tool_call_id: str,
        on_event: "EventCallback | None",
        iteration: int,
    ) -> tuple[str, str]:
        e = self._engine
        # ── 统一 questions 数组模式 ──
        questions_value = arguments.get("questions")

        # 向后兼容：旧的 question（单数对象）自动转为 questions 数组
        if not isinstance(questions_value, list) or len(questions_value) == 0:
            question_value = arguments.get("question")
            if isinstance(question_value, dict):
                questions_value = [question_value]
            else:
                raise ValueError("工具参数错误: questions 必须为非空数组。")

        pending_list = e._question_flow.enqueue_batch(
            questions_payload=questions_value,
            tool_call_id=tool_call_id,
        )
        # 只 emit 第一个问题，后续问题在回答后逐个 emit
        first = pending_list[0]
        self.emit_user_question_event(
            question=first,
            on_event=on_event,
            iteration=iteration,
        )
        ids = [p.question_id for p in pending_list]
        if len(pending_list) == 1:
            return f"已创建待回答问题 `{first.question_id}`。", first.question_id
        return (
            f"已创建 {len(pending_list)} 个待回答问题：{', '.join(ids)}。",
            first.question_id,
        )

    async def handle_ask_user_blocking(
        self,
        *,
        arguments: dict[str, Any],
        tool_call_id: str,
        on_event: "EventCallback | None",
        iteration: int,
    ) -> str:
        """阻塞式 ask_user：创建问题、发射事件、await 用户回答。

        逐个等待每个问题的回答，收集后返回合并结果字符串给 LLM。

        - bench/同步前端模式：使用 _question_resolver 回调（同步交互）。
        - Web 模式：使用 InteractionRegistry Future（等待 /answer API）。
        超时 DEFAULT_INTERACTION_TIMEOUT 秒后返回超时消息。
        """
        from excelmanus.events import EventType, ToolCallEvent

        e = self._engine
        # ── 统一 questions 数组模式 ──
        questions_value = arguments.get("questions")
        if not isinstance(questions_value, list) or len(questions_value) == 0:
            question_value = arguments.get("question")
            if isinstance(question_value, dict):
                questions_value = [question_value]
            else:
                raise ValueError("工具参数错误: questions 必须为非空数组。")

        pending_list = e._question_flow.enqueue_batch(
            questions_payload=questions_value,
            tool_call_id=tool_call_id,
        )

        resolver = getattr(e, "_question_resolver", None)
        collected_answers: list[dict[str, Any]] = []

        for i, pending_q in enumerate(pending_list):
            # 发射当前问题事件
            self.emit_user_question_event(
                question=pending_q,
                on_event=on_event,
                iteration=iteration,
            )

            if resolver is not None:
                # ── bench/同步前端模式：通过回调获取回答 ──
                try:
                    raw_answer = await resolver(pending_q)
                except Exception as _qr_exc:
                    logger.warning("question_resolver 异常: %s", _qr_exc)
                    raw_answer = ""
                e._question_flow.pop_current()
                try:
                    parsed = e._question_flow.parse_answer(raw_answer, question=pending_q)
                    payload = parsed.to_tool_result()
                except Exception:
                    payload = {"raw_input": raw_answer}
            else:
                # ── Web 模式：创建 Future 并等待 /answer API ──
                fut = e._interaction_registry.create(pending_q.question_id)
                try:
                    payload = await asyncio.wait_for(fut, timeout=DEFAULT_INTERACTION_TIMEOUT)
                except asyncio.TimeoutError:
                    for remaining in pending_list[i:]:
                        e._question_flow.pop_current()
                        e._interaction_registry.cancel(remaining.question_id)
                    e._interaction_registry.cleanup_done()
                    return f"等待用户回答超时（{int(DEFAULT_INTERACTION_TIMEOUT)}s），已取消问题。"
                except asyncio.CancelledError:
                    for remaining in pending_list[i:]:
                        e._question_flow.pop_current()
                    e._interaction_registry.cleanup_done()
                    return "用户取消了问题。"
                e._question_flow.pop_current()

            if isinstance(payload, dict):
                collected_answers.append(payload)
            else:
                collected_answers.append({"raw_input": str(payload)})

            # 发射已回答事件
            e._emit(
                on_event,
                ToolCallEvent(
                    event_type=EventType.APPROVAL_RESOLVED,
                    approval_id=pending_q.question_id,
                    approval_tool_name="ask_user",
                    result=str(payload.get("raw_input", payload) if isinstance(payload, dict) else payload),
                    success=True,
                    iteration=iteration,
                ),
            )

        e._interaction_registry.cleanup_done()

        # 格式化合并结果
        if len(collected_answers) == 1:
            answer = collected_answers[0]
            return json.dumps(answer, ensure_ascii=False)
        return json.dumps(collected_answers, ensure_ascii=False)

    async def await_question_answer(
        self,
        pending_q: "PendingQuestion",
    ) -> Any:
        """统一等待用户回答：优先 question_resolver，回退到 InteractionRegistry Future。"""
        e = self._engine
        resolver = e._question_resolver
        if resolver is not None:
            raw_answer = await resolver(pending_q)
            try:
                parsed = e._question_flow.parse_answer(raw_answer, question=pending_q)
                return parsed.to_tool_result()
            except Exception:
                return {"raw_input": raw_answer}
        else:
            fut = e._interaction_registry.create(pending_q.question_id)
            return await asyncio.wait_for(fut, timeout=DEFAULT_INTERACTION_TIMEOUT)

    def handle_plan_exit_answer(
        self,
        *,
        parsed: Any,
        on_event: "EventCallback | None" = None,
    ) -> "ChatResult":
        from excelmanus.engine_types import ChatResult
        from excelmanus.plan_mode import apply_chat_mode

        e = self._engine
        selected = ""
        options = getattr(parsed, "selected_options", None) or []
        if options:
            selected = str(options[0].get("label", "") or "")
        emit = on_event or getattr(getattr(e, "_driver", None), "_on_event", None)
        if "批准" in selected:
            apply_chat_mode(e, "write", source="plan_exit", on_event=emit)
            return ChatResult(reply="已批准计划并退出计划模式。")
        e._pending_plan_exit = None
        return ChatResult(reply="已拒绝退出，仍留在计划模式。")

    def handle_mode_switch_answer(
        self,
        *,
        parsed: Any,
        target: str,
        on_event: "EventCallback | None" = None,
    ) -> "ChatResult":
        from excelmanus.engine_types import ChatResult
        from excelmanus.plan_mode import apply_chat_mode

        e = self._engine
        selected = ""
        options = getattr(parsed, "selected_options", None) or []
        if options:
            selected = str(options[0].get("label", "") or "")
        emit = on_event or getattr(getattr(e, "_driver", None), "_on_event", None)
        if "切换" in selected or "进入" in selected:
            nxt = apply_chat_mode(e, target, source="request", on_event=emit)
            return ChatResult(reply=f"已切换到{nxt}模式。")
        return ChatResult(reply="已保持当前模式。")

    # ── 待回答问题处理 ──────────────────────────────────────

    async def handle_pending_question_answer(
        self,
        *,
        user_message: str,
        on_event: "EventCallback | None",
    ) -> "ChatResult | None":
        from excelmanus.engine_types import ChatResult

        e = self._engine
        text = user_message.strip()
        current = e._question_flow.current()
        if current is None:
            e._pending_question_route_result = None
            return ChatResult(reply="当前没有待回答问题。")

        if text.startswith("/"):
            # 允许审批/权限相关命令在问题待回答时穿透执行
            _lower = text.lower().replace("_", "")
            _passthrough = ("/fullaccess", "/accept", "/reject", "/plan")
            if any(_lower.startswith(p) for p in _passthrough):
                # 返回 None 表示本方法不处理，由 chat() 继续走控制命令路径
                return None
            return ChatResult(
                reply=(
                    "当前有待回答问题，请先回答后再使用命令。\n\n"
                    f"{e._question_flow.format_prompt(current)}"
                )
            )

        parsed = e._question_flow.try_parse_explicit_answer(user_message, question=current)
        if parsed is None:
            seen_calls: set[str] = set()
            while e._question_flow.has_pending():
                popped_pending = e._question_flow.pop_current()
                if popped_pending is None:
                    break
                e._system_question_actions.pop(popped_pending.question_id, None)
                if popped_pending.tool_call_id in seen_calls:
                    continue
                seen_calls.add(popped_pending.tool_call_id)
                e._memory.add_tool_result(
                    popped_pending.tool_call_id,
                    json.dumps(
                        {"cancelled": True, "reason": "user_continued"},
                        ensure_ascii=False,
                    ),
                )
            e._pending_question_route_result = None
            e._batch_answers.clear()
            return None

        popped = e._question_flow.pop_current()
        if popped is None:
            e._pending_question_route_result = None
            return ChatResult(reply="当前没有待回答问题。")

        system_action = e._system_question_actions.pop(parsed.question_id, None)
        action_type = str(system_action.get("type", "")).strip() if system_action else ""

        # ── 多问题批量模式：同一 tool_call_id 的问题需要累积回答 ──
        next_q = e._question_flow.current()
        same_batch = (
            next_q is not None
            and next_q.tool_call_id == popped.tool_call_id
        )
        if same_batch:
            # 累积到 _batch_answers，暂不写入 tool_result
            batch_key = popped.tool_call_id
            if batch_key not in e._batch_answers:
                e._batch_answers[batch_key] = []
            e._batch_answers[batch_key].append(parsed.to_tool_result())
        else:
            # 最后一个问题（或单问题模式）：合并所有累积回答 + 当前回答，一次性写入
            batch_key = popped.tool_call_id
            accumulated = []
            if batch_key in e._batch_answers:
                accumulated = e._batch_answers.pop(batch_key)
            accumulated.append(parsed.to_tool_result())
            if len(accumulated) == 1:
                tool_result = json.dumps(accumulated[0], ensure_ascii=False)
            else:
                tool_result = json.dumps(
                    {"answers": accumulated, "total": len(accumulated)},
                    ensure_ascii=False,
                )
            e._memory.add_tool_result(popped.tool_call_id, tool_result)

        logger.info("已接收问题回答: %s", parsed.question_id)
        if system_action is not None:
            e._pending_question_route_result = None
            if action_type == _SYSTEM_Q_SUBAGENT_APPROVAL:
                action_result = ChatResult(
                    reply="子代理高风险审批冒泡已移除。子会话审批钉死 never，被拒由主模型处理。",
                )
            elif action_type == _SYSTEM_Q_PLAN_EXIT:
                action_result = self.handle_plan_exit_answer(
                    parsed=parsed,
                    on_event=on_event,
                )
            elif action_type == _SYSTEM_Q_MODE_SWITCH:
                action_result = self.handle_mode_switch_answer(
                    parsed=parsed,
                    target=str(system_action.get("target") or "write"),
                    on_event=on_event,
                )
            else:
                action_result = ChatResult(reply="已记录你的回答。")

            if e._question_flow.has_pending():
                next_question = e._question_flow.current()
                assert next_question is not None
                self.emit_user_question_event(
                    question=next_question,
                    on_event=on_event,
                    iteration=0,
                )
                merged = (
                    f"{action_result.reply}\n\n"
                    f"{e._question_flow.format_prompt(next_question)}"
                )
                return ChatResult(reply=merged)
            return action_result

        # 队列仍有待答问题，继续前台追问
        if e._question_flow.has_pending():
            next_question = e._question_flow.current()
            assert next_question is not None
            self.emit_user_question_event(
                question=next_question,
                on_event=on_event,
                iteration=0,
            )
            return ChatResult(reply=e._question_flow.format_prompt(next_question))

        route_to_resume = e._pending_question_route_result
        e._pending_question_route_result = None
        if route_to_resume is None:
            return ChatResult(reply="已记录你的回答。")
        # 从上次中断的轮次之后继续执行
        resume_iteration = e._last_iteration_count + 1
        from excelmanus.agent.loop import run_tool_loop

        return await run_tool_loop(
            e,
            route_to_resume,
            on_event,
            start_iteration=resume_iteration,
            question_resolver=e._question_resolver,
        )
