"""问答与审批交互处理 — 从 AgentEngine 提取的用户交互逻辑。

包括：
- ask_user 工具处理（非阻塞/阻塞模式）
- 问题队列管理与事件发射
- 待回答问题的用户输入解析与路由恢复
"""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from excelmanus.engine_core.idle_tracker import idle_segment
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
        self._recovery: dict[str, Any] | None = None

    def _persist(self) -> None:
        driver = getattr(self._engine, "_driver", None)
        if driver is not None:
            driver._persist_runtime_state()

    def snapshot(self) -> dict[str, Any] | None:
        return deepcopy(self._recovery)

    def clear_recovery(self) -> None:
        self._recovery = None

    def restore(self, record: dict[str, Any] | None) -> None:
        self._recovery = deepcopy(record) if isinstance(record, dict) else None
        if not self._recovery or self._recovery.get("consumed"):
            return
        if self._recovery.get("kind") == "question":
            if self._recovery.get("phase") != "paused":
                self._restore_questions()
        elif self._recovery.get("kind") == "approval":
            if self._recovery.get("phase") == "waiting" and not self._recovery.get("consumed"):
                self._engine._approval.restore_pending(self._recovery["approval"])
            else:
                self._engine._approval.clear_pending()

    def can_recover(self) -> bool:
        record = self._recovery or {}
        return bool(record.get("tool_call_id") and not record.get("consumed"))

    def pause_recovery(self) -> None:
        if self._recovery and self._recovery.get("phase") == "waiting":
            self._recovery["phase"] = "paused"

    def needs_resume(self) -> bool:
        return self.can_recover() and not self._engine._driver.running

    def approval_is_actionable(self, approval_id: str) -> bool:
        record = self._recovery or {}
        return (record.get("kind") == "approval" and record.get("phase") == "waiting"
                and record.get("approval", {}).get("approval_id") == approval_id
                and not record.get("consumed"))

    def _parent_call_id(self) -> str | None:
        from excelmanus.tools.context import current_call

        call = current_call()
        parent = call.parent_call_id if call is not None else None
        session = getattr(self._engine, "_active_code_mode_session", None)
        return parent or getattr(session, "root_call_id", None)

    def remember_approval(self, pending: "PendingApproval", tool_call_id: str) -> None:
        record = self._recovery or {}
        if record.get("approval", {}).get("approval_id") == pending.approval_id:
            return
        parent = pending.parent_call_id or self._parent_call_id()
        if parent == tool_call_id:
            parent = None
        self._recovery = {
            "kind": "approval", "phase": "waiting", "consumed": False,
            "tool_call_id": tool_call_id, "parent_call_id": parent,
            "approval": asdict(pending), "decision": None,
        }
        self._persist()

    def record_approval_decision(self, approval_id: str, decision: str) -> bool:
        record = self._recovery or {}
        if record.get("kind") != "approval" or record.get("approval", {}).get("approval_id") != approval_id:
            return False
        if record.get("decision") is not None:
            return True  # 已提交的决策不被重复请求改写。
        normalized = str(decision).strip().lower()
        decision = "accept" if normalized in {"accept", "approved", "allow", "yes"} else "fullaccess" if normalized == "fullaccess" else "reject"
        record.update(decision=decision, phase="decided")
        self._persist()
        return True

    def approval_execution_started(self, approval_id: str) -> None:
        record = self._recovery or {}
        if record.get("approval", {}).get("approval_id") == approval_id:
            record["phase"] = "executing"
            self._persist()

    def finish_approval(self, approval_id: str, result: str, success: bool) -> None:
        record = self._recovery or {}
        if record.get("approval", {}).get("approval_id") == approval_id:
            record.update(phase="completed", result=result, success=success)
            self._persist()

    def observe_tool_result(self, tool_call_id: str, result: str, success: bool) -> None:
        record = self._recovery or {}
        if record.get("tool_call_id") == tool_call_id and not record.get("consumed"):
            record.update(phase="completed", result=result, success=success)
            self._persist()

    def consume_tool_result(self, tool_call_id: str) -> None:
        record = self._recovery or {}
        target = record.get("parent_call_id") or record.get("tool_call_id")
        if target == tool_call_id and not record.get("consumed"):
            record["consumed"] = True
            self._persist()

    def record_question_answer(self, question_id: str, payload: dict[str, Any]) -> bool:
        record = self._recovery or {}
        if record.get("kind") != "question" or not any(
            row["question_id"] == question_id for row in record.get("questions", [])
        ):
            return False
        answers = record["answers"]
        if question_id not in answers:
            answers[question_id] = deepcopy(payload)
            if not self._engine._driver.running:
                self._restore_questions()
            self._persist()
        return True

    def _restore_questions(self) -> None:
        record = self._recovery or {}
        remaining = [] if record.get("phase") == "completed" else [
            row for row in record.get("questions", [])
            if row["question_id"] not in record.get("answers", {})
        ]
        self._engine._question_flow.restore(remaining)

    async def wait_approval_decision(self, approval_id: str) -> Any:
        record = self._recovery or {}
        if record.get("approval", {}).get("approval_id") == approval_id and record.get("decision") is not None:
            return {"decision": record["decision"], "approval_id": approval_id}
        fut = self._engine._interaction_registry.create(approval_id)
        with idle_segment(self._engine, "approval"):
            return await asyncio.wait_for(fut, timeout=DEFAULT_INTERACTION_TIMEOUT)

    async def resume_pending(self, on_event: "EventCallback | None", *,
                             approval_resolver: Any = None, question_resolver: Any = None) -> Any:
        """重建待决交互的消费者，并把最终结果写回原工具调用。"""
        e = self._engine
        record = self._recovery
        if record is None or not self.can_recover():
            return
        e._question_resolver = question_resolver
        if record["kind"] == "question" and record.get("phase") != "completed":
            record["phase"] = "waiting"
            self._restore_questions()
            await self._collect_question_answers(on_event=on_event, iteration=0)
        elif record["kind"] == "approval" and record.get("phase") != "completed":
            saved = record["approval"]
            approval_id = saved["approval_id"]
            if record.get("phase") == "executing":
                # 已开始执行时只能依据已有回执恢复，不能把已批准工具再执行一遍。
                applied = e._approval.get_applied(approval_id)
                if applied is not None:
                    text = applied.result_preview
                else:
                    # 没有回执：按工具效果给确切状态（写入/未知副作用=结果未确认的
                    # 失败，命令=可能仍在后台运行），不留"结果未完整记录"这种模糊说法。
                    from excelmanus.engine_core.aborted_calls import dangling_call_placeholder

                    text = dangling_call_placeholder(str(saved.get("tool_name") or ""))
                self.finish_approval(approval_id, text, bool(applied and applied.execution_status == "success"))
            else:
                e._approval.restore_pending(saved)
                pending = e._approval.pending
                assert pending is not None
                if record.get("decision") is None:
                    record["phase"] = "waiting"
                    self._persist()
                    self.emit_pending_approval_event(pending=pending, on_event=on_event, iteration=0,
                                                     tool_call_id=record["tool_call_id"])
                    with idle_segment(e, "approval"):
                        payload = (await approval_resolver(pending) if approval_resolver is not None
                                   else await self.wait_approval_decision(approval_id))
                    self.record_approval_decision(approval_id, payload["decision"] if isinstance(payload, dict) else str(payload))
                updates, _ = await e._apply_approval_decision(
                    record["decision"], pending, approval_id, record["tool_call_id"], on_event, 0, "恢复审批",
                )
                self.finish_approval(approval_id, updates["result"], bool(updates["success"]))
        target = record.get("parent_call_id") or record["tool_call_id"]
        result = str(record.get("result") or "")
        if record.get("parent_call_id"):
            result = json.dumps({"status": "interrupted", "message": "原脚本已中断，未重新执行脚本。待决子调用已处理，后续请从实际结果继续。",
                                 "call_id": record["tool_call_id"], "interaction_result": result}, ensure_ascii=False)
        replaced = e.memory.replace_tool_result(target, result)
        if not replaced:
            e.memory.add_tool_result(target, result)
        else:
            from excelmanus.prompt.envelope import invalidate_envelope
            invalidate_envelope(e)
        record["consumed"] = True
        e._interaction_registry.cleanup_done()
        self._persist()
        from excelmanus.engine_types import ToolCallResult

        name = record.get("approval", {}).get("tool_name", "ask_user")
        arguments = record.get("approval", {}).get("arguments", record.get("arguments", {}))
        if record.get("parent_call_id"):
            name = "run_code"
        from excelmanus.events import EventType, ToolCallEvent
        e._emit(on_event, ToolCallEvent(
            event_type=EventType.TOOL_CALL_END, tool_call_id=target,
            tool_name=name, arguments=deepcopy(arguments), result=result,
            success=bool(record.get("success")) and not bool(record.get("parent_call_id")),
        ))
        return ToolCallResult(tool_name=name, arguments=deepcopy(arguments), result=result,
                              success=bool(record.get("success")) and not bool(record.get("parent_call_id")))

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
                tool_call_id=question.tool_call_id,
                question_selection=deepcopy(question.selection),
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

        self.remember_approval(pending, tool_call_id)
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
        from excelmanus.workbook.interaction import (
            normalize_ask_user_arguments,
            prepare_questions,
        )

        pending_list = e._question_flow.enqueue_batch(
            questions_payload=prepare_questions(e, normalize_ask_user_arguments(arguments)),
            tool_call_id=tool_call_id,
        )
        persist_runtime = getattr(getattr(e, "_driver", None), "_persist_runtime_state", None)
        if callable(persist_runtime):
            persist_runtime()
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
        e = self._engine
        from excelmanus.workbook.interaction import (
            ASK_USER_ARGUMENT_EXAMPLE,
            normalize_ask_user_arguments,
            prepare_questions,
        )
        from excelmanus.workbook.snapshot import SnapshotError

        try:
            questions_value = normalize_ask_user_arguments(arguments)
            prepared = await asyncio.to_thread(prepare_questions, e, questions_value)
            pending_list = e._question_flow.enqueue_batch(
                questions_payload=prepared,
                tool_call_id=tool_call_id,
            )
        except ValueError as exc:
            from excelmanus.engine_core.tool_result import error_result

            return error_result(
                str(exc),
                code="TOOL_ARGUMENT_VALIDATION_ERROR",
                remediation=(
                    "按 example 的字段名修正后重试；仍不确定可用 "
                    "introspect_capability 查询 ask_user 的参数细节。"
                ),
                fields={
                    "tool": "ask_user",
                    "example": ASK_USER_ARGUMENT_EXAMPLE,
                    "required_fields": ["questions[].text", "questions[].options|selection"],
                },
            ).model_text
        except SnapshotError as exc:
            from excelmanus.engine_core.tool_result import error_result

            fields = dict(exc.fields or {})
            fields["tool"] = "ask_user"
            return error_result(str(exc), code=exc.code, fields=fields).model_text
        self._recovery = {
            "kind": "question", "phase": "waiting", "consumed": False,
            "tool_call_id": tool_call_id, "parent_call_id": self._parent_call_id(),
            "questions": [asdict(question) for question in pending_list], "answers": {},
            "arguments": deepcopy(arguments),
        }
        self._persist()
        return await self._collect_question_answers(on_event=on_event, iteration=iteration)

    async def _collect_question_answers(self, *, on_event: "EventCallback | None", iteration: int) -> str:
        from excelmanus.events import EventType, ToolCallEvent

        e = self._engine
        record = self._recovery
        assert record is not None
        resolver = getattr(e, "_question_resolver", None)
        self._restore_questions()
        while (pending_q := e._question_flow.current()) is not None:
            fut = None if resolver is not None else e._interaction_registry.create(pending_q.question_id)
            self.emit_user_question_event(
                question=pending_q, on_event=on_event, iteration=iteration,
            )
            try:
                if resolver is not None:
                    try:
                        with idle_segment(e, "question"):
                            raw_answer = await resolver(pending_q)
                    except Exception as exc:
                        logger.warning("question_resolver 异常: %s", exc)
                        raw_answer = ""
                    try:
                        payload = e._question_flow.parse_answer(raw_answer, question=pending_q).to_tool_result()
                    except Exception:
                        payload = {"raw_input": raw_answer}
                else:
                    assert fut is not None
                    with idle_segment(e, "question"):
                        payload = await asyncio.wait_for(fut, timeout=DEFAULT_INTERACTION_TIMEOUT)
            except asyncio.CancelledError:
                e._question_flow.clear()
                e._interaction_registry.cancel(pending_q.question_id)
                self._persist()
                raise
            except asyncio.TimeoutError:
                e._question_flow.clear()
                e._interaction_registry.cleanup_done()
                result = f"等待用户回答超时（{int(DEFAULT_INTERACTION_TIMEOUT)}s），已取消问题。"
                record.update(phase="completed", result=result, success=False)
                self._persist()
                return result
            answer = payload if isinstance(payload, dict) else {"raw_input": str(payload)}
            self.record_question_answer(pending_q.question_id, answer)
            current = e._question_flow.current()
            if current is not None and current.question_id == pending_q.question_id:
                e._question_flow.pop_current()
            self._persist()
            e._emit(
                on_event,
                ToolCallEvent(
                    event_type=EventType.APPROVAL_RESOLVED,
                    approval_id=pending_q.question_id,
                    approval_tool_name="ask_user",
                    result=str(answer.get("raw_input", answer)),
                    success=True,
                    iteration=iteration,
                ),
            )

        e._interaction_registry.cleanup_done()
        collected = [record["answers"][question["question_id"]] for question in record["questions"]]
        result = json.dumps(collected[0] if len(collected) == 1 else collected, ensure_ascii=False)
        record.update(phase="completed", result=result, success=True)
        self._persist()
        return result

    async def await_question_answer(
        self,
        pending_q: "PendingQuestion",
    ) -> Any:
        """统一等待用户回答：优先 question_resolver，回退到 InteractionRegistry Future。"""
        e = self._engine
        resolver = e._question_resolver
        if resolver is not None:
            with idle_segment(e, "question"):
                raw_answer = await resolver(pending_q)
            try:
                parsed = e._question_flow.parse_answer(raw_answer, question=pending_q)
                return parsed.to_tool_result()
            except Exception:
                return {"raw_input": raw_answer}
        else:
            fut = e._interaction_registry.create(pending_q.question_id)
            with idle_segment(e, "question"):
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
