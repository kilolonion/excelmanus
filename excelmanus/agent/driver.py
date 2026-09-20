"""Driver：kick / turn / pre_step / step。

会话循环的所有权在这里。``AgentEngine`` 是服务包（memory、registry、
dispatcher）。一步之内的模型流与工具分派走 ``agent.loop``。
"""

from __future__ import annotations

import time
import asyncio
from collections.abc import Awaitable
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from excelmanus.agent.inbox import Inbox, InboxItem, InboxTarget
from excelmanus.events import EventType, ToolCallEvent
from excelmanus.logger import get_logger
from excelmanus.agent.budget import TurnBudget
from excelmanus.agent.budget import TurnBudgetExceeded

logger = get_logger("agent.driver")

PreStepDecision = Literal["enter", "reject"]
PreStepHook = Callable[[list[InboxItem], InboxTarget], PreStepDecision]
PreStepAttachment = Callable[[Any], Awaitable[PreStepDecision]]


@dataclass
class PreparedStep:
    kind: PreStepDecision
    messages: list[Any]
    claimed: list[InboxItem] = field(default_factory=list)
    followup_item: InboxItem | None = None
    early_result: Any = None
    assembly: Any = None


class Driver:
    """极瘦循环：打开 turn → 认领 → 若干 step → 关闭；还有 next-turn 则再来。"""

    def __init__(self, engine: Any) -> None:
        self.engine = engine
        self.inbox = Inbox()
        self.status: Literal["idle", "running"] = "idle"
        self.turn_index = 0
        self.step_index = 0
        self.turn_id = ""
        self.step_id = ""
        self.assembly_log: list[dict[str, Any]] = []
        self._hooks: list[PreStepHook] = []
        self._attachments: list[PreStepAttachment] = []
        self._on_event: Any = None
        self._idle_event = asyncio.Event()
        self._idle_event.set()
        self._runner_task: asyncio.Task[Any] | None = None
        self._cancel_reason: str | None = None
        self._active_item: InboxItem | None = None
        self._turn_record: dict[str, Any] | None = None

    @property
    def running(self) -> bool:
        return self._runner_task is not None and not self._runner_task.done()

    def remaining_turn_seconds(self) -> float | None:
        """返回当前主回合的绝对剩余 wall-clock；空闲时返回 None。"""
        budget = getattr(self.engine, "_turn_budget", None)
        if budget is not None:
            return budget.remaining_seconds()
        deadline = getattr(self.engine, "_turn_deadline_mono", None)
        if deadline is None:
            return None
        return max(0.0, float(deadline) - time.monotonic())

    def current_turn(self) -> dict[str, Any]:
        record: dict[str, Any] = deepcopy(self._turn_record or {"status": "idle"})
        record.pop("input", None)
        record.pop("staged_inputs", None)
        record["queued_count"] = len(self.inbox.next_turn)
        record["can_resume"] = not self.running and (
            record["status"] in {"interrupted", "cancelled", "timeout", "error", "truncated"}
            or bool(self.inbox.next_turn)
        )
        approval = getattr(self.engine, "_approval", None)
        questions = getattr(self.engine, "_question_flow", None)
        blocked = []
        if record["can_resume"]:
            if approval is not None and approval.has_pending():
                blocked.append("approval")
            if questions is not None and questions.has_pending():
                blocked.append("question")
        record["resume_blocked_by"] = blocked
        interaction = getattr(self.engine, "_interaction_handler", None)
        if interaction is not None and interaction.can_recover():
            record["resume_blocked_by"] = blocked = []
        if blocked:
            record["can_resume"] = False
        return record

    def add_pre_step_hook(self, hook: PreStepHook) -> None:
        self._hooks.append(hook)

    def add_pre_step_attachment(self, attachment: PreStepAttachment) -> None:
        self._attachments.append(attachment)

    async def _run_attachments(self) -> PreStepDecision:
        for attachment in self._attachments:
            decision = await attachment(self.engine)
            if decision == "reject":
                return "reject"
        return "enter"

    def enqueue_followup(
        self,
        content: str,
        *,
        extra: dict[str, Any] | None = None,
    ) -> InboxItem:
        item = self.inbox.push_followup(content, extra=extra)
        self._persist_runtime_state()
        return item

    def steer(self, content: str, *, extra: dict[str, Any] | None = None) -> InboxItem:
        item = self.inbox.push_steer(content, extra=extra)
        self._persist_runtime_state()
        return item

    def inject(self, content: str, *, extra: dict[str, Any] | None = None) -> InboxItem:
        item = self.inbox.push_inject(content, extra=extra)
        self._persist_runtime_state()
        return item

    def inject_workbook_change(self, event: dict[str, Any]) -> None:
        """Coalesce edit bursts without waking an idle agent or losing its task."""
        from excelmanus.workbook.user_edits import MAX_EVENTS, PROMPT_KIND, render_changes

        item = next((item for item in self.inbox.next_step
                     if item.extra.get("prompt_kind") == PROMPT_KIND), None)
        events = list(item.extra.get("workbook_events", [])) if item else []
        if any(e["event_id"] == event["event_id"] for e in events):
            return
        events.append({"event_id": event["event_id"], "path": event["path"],
                       "content_version": event.get("after_version"),
                       "changes": event["context"]["summary"]})
        # Limit both event count and characters so continuous typing cannot flood context.
        events = events[-MAX_EVENTS:]
        while len(events) > 1 and sum(len(str(e)) for e in events) > 10000:
            events.pop(0)
        count = (item.extra.get("workbook_event_count", 0) if item else 0) + 1
        paths = list(item.extra.get("workbook_paths", [])) if item else []
        paths = [path for path in paths if path != event["path"]] + [event["path"]]
        paths = paths[-32:]
        extra = {"prompt_kind": PROMPT_KIND, "workbook_events": events,
                 "workbook_event_count": count, "workbook_paths": paths}
        content = render_changes(events, count, paths)
        if item is None:
            self.inject(content, extra=extra)
        else:
            item.content = content
            item.extra.update(extra)
            self._persist_runtime_state()

    def runtime_state(self) -> dict[str, Any]:
        from excelmanus.trace import trace_of

        engine = self.engine
        trace = trace_of(engine)
        approval = getattr(engine, "_approval", None)
        questions = getattr(engine, "_question_flow", None)
        return {
            "trace": trace.snapshot() if trace else None,
            "driver": {
                "turn_index": self.turn_index,
                "step_index": self.step_index,
                "active": self.status == "running",
                "inbox": self.inbox.reconstruct(),
                "turn": deepcopy(self._turn_record),
            },
            "approval": (
                approval.snapshot_pending()
                if approval is not None and callable(getattr(approval, "snapshot_pending", None))
                else None
            ),
            "questions": (
                questions.snapshot()
                if questions is not None and callable(getattr(questions, "snapshot", None))
                else []
            ),
            "interaction": (
                engine._interaction_handler.snapshot()
                if getattr(engine, "_interaction_handler", None) is not None else None
            ),
        }

    def restore_runtime_state(self, raw: dict[str, Any] | None) -> None:
        if not isinstance(raw, dict):
            return
        from excelmanus.trace import TraceRecorder

        self.engine._trace = TraceRecorder.from_snapshot(raw.get("trace"))
        self.engine._trace_active_request_key = None
        self.engine._trace_last_request_key = None
        driver_state: dict[str, Any] = raw["driver"] if isinstance(raw.get("driver"), dict) else {}
        self.turn_index = int(driver_state.get("turn_index", 0) or 0)
        self.step_index = int(driver_state.get("step_index", 0) or 0)
        self.status = "idle"
        self._active_item = None
        self._turn_record = deepcopy(driver_state.get("turn"))
        if self._turn_record and self._turn_record.get("status") in {"preparing", "running"}:
            self._turn_record.update(status="interrupted", finished_at=time.time())
        self.inbox.load_reconstructed(driver_state.get("inbox"))
        approval = getattr(self.engine, "_approval", None)
        if approval is not None and callable(getattr(approval, "restore_pending", None)):
            approval.restore_pending(raw.get("approval"))
        questions = getattr(self.engine, "_question_flow", None)
        if questions is not None and callable(getattr(questions, "restore", None)):
            questions.restore(raw.get("questions"))
        interaction = getattr(self.engine, "_interaction_handler", None)
        if interaction is not None:
            interaction.restore(raw.get("interaction"))

    def _persist_runtime_state(self) -> None:
        state = getattr(self.engine, "_state", None)
        if state is None:
            return
        state.runtime_state = self.runtime_state()
        saver = getattr(self.engine, "save_session_snapshot", None)
        if callable(saver):
            saver()

    def _ensure_runner(self) -> asyncio.Task[Any]:
        task = self._runner_task
        if task is None or task.done():
            task = asyncio.create_task(self._run_actor())
            self._runner_task = task
        return task

    async def _run_actor(self) -> None:
        self.status = "running"
        self._idle_event.clear()
        try:
            while True:
                timeout = float(
                    getattr(getattr(self.engine, "config", None), "turn_timeout_seconds", 0)
                    or 0
                )
                self._cancel_reason = "timeout" if timeout > 0 else None
                try:
                    has_next = (
                        await asyncio.wait_for(self.turn(), timeout=timeout)
                        if timeout > 0
                        else await self.turn()
                    )
                except asyncio.TimeoutError:
                    self._cancel_active_work()
                    has_next = bool(self.inbox.next_turn)
                except Exception:
                    # turn 已把异常结算到所属输入；独立的后续输入仍可执行。
                    has_next = bool(self.inbox.next_turn)
                finally:
                    self._cancel_reason = None
                if not has_next:
                    return
        finally:
            self.status = "idle"
            self._idle_event.set()
            current = asyncio.current_task()
            if self._runner_task is current:
                self._runner_task = None
            self._persist_runtime_state()

    async def kick(self) -> None:
        """Wake the session actor and wait without owning or cancelling it."""
        task = self._ensure_runner()
        await asyncio.shield(task)

    async def wait_for_item(self, item: InboxItem) -> Any:
        """Wait until this particular followup has a terminal result.

        ``kick()`` is a wakeup operation, rather than an ownership contract.  A
        followup can arrive in the small window between the actor's final queue
        check and it becoming idle.  Waiting on the global idle event in that
        case returns an empty result and silently loses the caller's turn.  Tie
        the wait to the item and kick the actor again whenever it is idle.
        """
        while item.result is None:
            task = self._ensure_runner()
            notification = asyncio.create_task(item.completed.wait())
            try:
                # 等当前输入完成即可；取消等待者不会取消 actor 或其他输入。
                await asyncio.wait((notification, task), return_when=asyncio.FIRST_COMPLETED)
                if item.result is None and task.cancelled():
                    raise asyncio.CancelledError
            finally:
                notification.cancel()
                await asyncio.gather(notification, return_exceptions=True)
        if isinstance(item.result, BaseException):
            raise item.result
        return item.result

    async def resume(self, message: str = "", *, on_event: Any = None,
                     approval_resolver: Any = None, question_resolver: Any = None) -> Any:
        """显式继续最近中断的主任务，或唤醒尚未认领的输入。"""
        from excelmanus.engine_types import ChatResult

        if self.running:
            return ChatResult(reply="当前任务仍在执行。补充要求可作为插话发送。")
        previous = self._turn_record or {}
        state = self.current_turn()
        if state["resume_blocked_by"]:
            return ChatResult(reply="上次任务的旧快照缺少交互调用记录，尚不支持恢复该审批或问答；请重新发起任务。")
        if not state["can_resume"]:
            return ChatResult(reply="当前没有可继续的中断任务或排队消息。")
        queued_resume = next((item for item in self.inbox.next_turn
                              if item.extra.get("resumed_from") == previous.get("turn_id")
                              and previous.get("turn_id")), None)
        if queued_resume is not None:
            item = queued_resume
            item.extra["on_event"] = on_event
            if message.strip():
                self.steer(message)
        elif previous.get("status") in {"interrupted", "cancelled", "timeout", "error", "truncated"}:
            saved_input = previous.get("input") or {}
            original_task = str(previous.get("task") or saved_input.get("content") or "")
            continuation = "继续上次未完成的任务。"
            if message.strip():
                continuation += f"\n\n补充要求：{message.strip()}"
            extra = dict(saved_input.get("extra") or {})
            extra.pop("slash_command", None)
            extra.pop("raw_args", None)
            extra.update(on_event=on_event, resumed_from=previous.get("turn_id"), resume_task=original_task)
            for staged in reversed(previous.get("staged_inputs") or []):
                self.inbox.push(staged["kind"], staged["content"], extra=staged.get("extra"), first=True)
            previous["staged_inputs"] = []
            self.inbox.push(
                "inject", f"中断任务的原始要求：{original_task}\n"
                "结合已保存的对话继续；先核对中断步骤的实际结果，保留已经完成的工作。",
                extra={"prompt_kind": "task_resume"}, first=True,
            )
            item = self.inbox.push("followup", continuation, extra=extra, first=True)
        else:
            item = self.inbox.peek_turn()
            assert item is not None
            item.extra["on_event"] = on_event
            if message.strip():
                self.steer(message)
        item.extra.update(
            approval_resolver=approval_resolver, question_resolver=question_resolver,
            resume_interaction=self.engine._interaction_handler.can_recover(),
        )
        self._persist_runtime_state()
        return await self.wait_for_item(item)

    def request_cancel(self, reason: str = "cancelled") -> None:
        self._cancel_reason = reason
        dispatcher = getattr(self.engine, "_tool_dispatcher", None)
        cancel = getattr(dispatcher, "request_cancel", None)
        if callable(cancel):
            cancel()
        task = self._runner_task
        if task is not None and not task.done() and task is not asyncio.current_task():
            task.cancel()

    async def stop(self, reason: str = "cancelled") -> None:
        """停止并等待 actor 收尾；取消等待者与取消任务是两种操作。"""
        task = self._runner_task
        self.request_cancel(reason)
        if task is not None and task is not asyncio.current_task():
            await asyncio.shield(asyncio.gather(task, return_exceptions=True))

    def _cancel_active_work(self) -> None:
        dispatcher = getattr(self.engine, "_tool_dispatcher", None)
        cancel = getattr(dispatcher, "request_cancel", None)
        if callable(cancel):
            cancel()

    async def turn(self) -> bool:
        engine = self.engine
        queued = self.inbox.peek_turn()
        if queued is None:
            return False
        self._on_event = queued.extra.get("on_event", self._on_event)
        self.turn_index += 1
        self.step_index = 0
        self.turn_id = f"t{self.turn_index}"
        self.step_id = ""
        engine._trace_last_request_key = None
        engine._trace_active_request_key = None
        turn_started = time.monotonic()
        timeout_seconds = float(
            getattr(getattr(engine, "config", None), "turn_timeout_seconds", 0) or 0
        )
        engine._turn_deadline_mono = (
            time.monotonic() + timeout_seconds if timeout_seconds > 0 else None
        )
        inherited = getattr(engine, "_inherited_turn_budget", None)
        if inherited is not None:
            engine._turn_budget = inherited
            budget_owned = False
        else:
            config = getattr(engine, "config", None)
            engine._turn_budget = TurnBudget(
                deadline_mono=engine._turn_deadline_mono,
                max_tokens=int(getattr(config, "turn_token_budget", 0) or 0),
                max_cost_usd=float(getattr(config, "turn_cost_budget_usd", 0.0) or 0.0),
                input_cost_per_1k_usd=float(getattr(config, "input_cost_per_1k_usd", 0.0) or 0.0),
                output_cost_per_1k_usd=float(getattr(config, "output_cost_per_1k_usd", 0.0) or 0.0),
            )
            budget_owned = True
        if self._turn_record is not None:
            self._turn_record["budget"] = engine._turn_budget.snapshot()
        self._active_item = None
        self._persist_runtime_state()
        self._emit(
            ToolCallEvent(
                event_type=EventType.TURN_START,
                turn_id=self.turn_id,
                iteration=self.turn_index,
            ),
        )

        if hasattr(engine, "_state"):
            engine._state.increment_turn()
        engine._tools_cache = None

        last_result: Any = None
        followup_item: InboxItem | None = None
        ran_loop = False
        try:
            prepared = await self.pre_step("next-turn")
            followup_item = prepared.followup_item
            if prepared.early_result is not None:
                last_result = prepared.early_result
            elif prepared.kind == "reject" or not prepared.messages:
                # A rejected pre-step still has a claimed followup.  Complete
                # that item explicitly so its caller never waits forever.
                last_result = prepared.early_result
                if last_result is None and followup_item is not None:
                    from excelmanus.engine_types import ChatResult

                    last_result = ChatResult(reply="")
            else:
                ran_loop = True
                last_result = await self.step(prepared)
            await self.turn_stopping()
            if ran_loop and last_result is not None:
                engine._finalize_driver_turn(
                    last_result,
                    on_event=self._on_event,
                    chat_start=turn_started,
                )
                try:
                    from excelmanus.system_one.host import maybe_emit_ui_hint

                    await maybe_emit_ui_hint(
                        engine,
                        last_result,
                        on_event=self._on_event,
                    )
                except Exception:
                    logger.debug("ui_hint hook failed; continuing turn", exc_info=True)
            if self._turn_record is not None:
                self._turn_record["status"] = "truncated" if getattr(last_result, "truncated", False) else "completed"
        except TurnBudgetExceeded as exc:
            from excelmanus.engine_types import ChatResult

            reason = getattr(exc, "kind", "budget")
            label = "超时" if reason == "wall_clock" else "预算耗尽"
            last_result = ChatResult(reply=f"本轮{label}（{reason}），已停止继续执行。", truncated=True)
            if ran_loop:
                self.engine._finalize_driver_turn(
                    last_result,
                    on_event=self._on_event,
                    chat_start=turn_started,
                )
            if self._turn_record is not None:
                self._turn_record.update(status="timeout" if reason == "wall_clock" else "truncated", stop_reason=reason)
            self._emit(
                ToolCallEvent(
                    event_type=EventType.TURN_FAILED,
                    turn_id=self.turn_id,
                    iteration=self.turn_index,
                    stop_reason=reason,
                    turn_error=str(exc),
                ),
            )
        except asyncio.CancelledError:
            from excelmanus.engine_types import ChatResult

            reason = self._cancel_reason or "cancelled"
            if reason != "shutdown":
                self.engine._interaction_handler.pause_recovery()
            message = "本轮执行超时。" if reason == "timeout" else "本轮执行已取消。"
            last_result = ChatResult(reply=message, truncated=True)
            if self._turn_record is not None:
                self._turn_record.update(status="timeout" if reason == "timeout" else "cancelled", stop_reason=reason)
            self._emit(
                ToolCallEvent(
                    event_type=EventType.TURN_FAILED,
                    turn_id=self.turn_id,
                    iteration=self.turn_index,
                    stop_reason=reason,
                    turn_error=("turn timeout" if reason == "timeout" else "turn cancelled"),
                ),
            )
            raise
        except Exception as exc:
            logger.exception("Agent turn failed: %s", exc)
            self._emit(
                ToolCallEvent(
                    event_type=EventType.TURN_FAILED,
                    turn_id=self.turn_id,
                    iteration=self.turn_index,
                    stop_reason="error",
                    turn_error=str(exc)[:500],
                ),
            )
            last_result = exc
            if self._turn_record is not None:
                self._turn_record.update(status="error", error=str(exc))
            # 失败也要把 durable 状态保存给恢复路径；不吞异常。
            raise
        finally:
            if self._turn_record is not None:
                self._turn_record["finished_at"] = time.time()
            engine._turn_deadline_mono = None
            if self._turn_record is not None and getattr(engine, "_turn_budget", None) is not None:
                self._turn_record["budget"] = engine._turn_budget.snapshot()
            self._persist_runtime_state()
            if budget_owned:
                engine._turn_budget = None
            self._emit(
                ToolCallEvent(
                    event_type=EventType.TURN_END,
                    turn_id=self.turn_id,
                    iteration=self.turn_index,
                ),
            )
            # 包括 pre_step 已认领、但还没来得及返回 PreparedStep 的失败路径。
            item = self._active_item
            self._active_item = None
            if item is not None:
                item.result = last_result
                item.completed.set()
        return bool(self.inbox.next_turn)

    async def pre_step(self, target: InboxTarget) -> PreparedStep:
        self.step_index = max(self.step_index, 0) + 1
        self.step_id = f"s{self.step_index}"
        claimed = self.inbox.claim(target, turn=self.turn_index, step=self.step_index)
        followup_item = next((item for item in claimed if item.kind == "followup"), None)

        if target == "next-turn" and followup_item is not None:
            self._active_item = followup_item
            self._turn_record = {
                "turn_id": self.turn_id, "item_id": followup_item.id,
                "task": followup_item.extra.get("resume_task") or followup_item.content,
                "status": "preparing", "step_index": self.step_index,
                "started_at": time.time(), "finished_at": None,
                "resumed_from": followup_item.extra.get("resumed_from"),
                "input": followup_item.snapshot(),
                "staged_inputs": [item.snapshot() for item in claimed if item is not followup_item],
                "budget": (
                    self.engine._turn_budget.snapshot()
                    if getattr(self.engine, "_turn_budget", None) is not None else None
                ),
            }
            self._persist_runtime_state()

        if target == "next-turn" and followup_item is None:
            return PreparedStep(kind="reject", messages=[], claimed=claimed)

        for hook in self._hooks:
            if hook(claimed, target) == "reject":
                logger.info(
                    "pre_step 拒绝 turn=%s step=%s target=%s claimed=%d",
                    self.turn_id,
                    self.step_id,
                    target,
                    len(claimed),
                )
                return PreparedStep(
                    kind="reject",
                    messages=[],
                    claimed=claimed,
                    followup_item=followup_item,
                )

        if followup_item is not None:
            self.engine._pending_user_text = str(followup_item.content or "")
        try:
            if followup_item is not None and followup_item.extra.get("resume_interaction"):
                from excelmanus.plan_mode import apply_chat_mode
                self.engine._state.reset_loop_stats()
                self.engine._tool_dispatcher.reset_cancel()
                apply_chat_mode(self.engine, followup_item.extra.get("chat_mode") or "write",
                                source="request", on_event=self._on_event)
                followup_item.extra["recovered_tool_result"] = await self.engine._interaction_handler.resume_pending(
                    self._on_event,
                    approval_resolver=followup_item.extra.get("approval_resolver"),
                    question_resolver=followup_item.extra.get("question_resolver"),
                )
            rejected = await self._run_attachments() == "reject"
        finally:
            if followup_item is not None:
                self.engine._pending_user_text = None
        if rejected:
            return PreparedStep(
                kind="reject",
                messages=[],
                claimed=claimed,
                followup_item=followup_item,
            )

        if followup_item is not None:
            # 只修复已经结束的上一回合；在插入恢复提示/steer 之前补齐工具结果。
            repaired = self.engine._memory.repair_dangling_tool_calls()
            if repaired:
                logger.info("修复了 %d 个中断遗留的悬空 tool_call", repaired)
        self._record_claim(claimed, target)
        # pending next-step 先于本 turn 的 followup 进入 memory。
        self._append_step_items(claimed)
        if self._turn_record is not None:
            self._turn_record["staged_inputs"] = []

        early_result = None
        if followup_item is not None:
            extra = followup_item.extra
            on_event = extra.get("on_event")
            if on_event is not None:
                self._on_event = on_event
            early_result = await self.engine._apply_claimed_followup(followup_item)
            if early_result is not None:
                return PreparedStep(
                    kind="reject",
                    messages=[],
                    claimed=claimed,
                    followup_item=followup_item,
                    early_result=early_result,
                )

        messages: list[Any]
        if target == "next-step":
            messages = claimed if claimed else ["__continue__"]
        else:
            messages = claimed

        return PreparedStep(
            kind="enter",
            messages=messages,
            claimed=claimed,
            followup_item=followup_item,
        )

    async def step(self, prepared: PreparedStep) -> Any:
        extra = (prepared.followup_item.extra if prepared.followup_item else {}) or {}
        on_event = extra.get("on_event", self._on_event)
        approval_resolver = extra.get("approval_resolver")
        question_resolver = extra.get("question_resolver")
        route_result = getattr(self.engine, "_last_route_result", None)
        from excelmanus.agent.loop import run_tool_loop

        # 挂到引擎：Runtime 的 hook ASK 与 Code Mode 子调用审批等待
        # 与顶层共用同一决策通道（resolver 或 InteractionRegistry）。
        engine = self.engine
        prev_resolver = getattr(engine, "_approval_resolver", None)
        engine._approval_resolver = approval_resolver
        try:
            return await run_tool_loop(
                engine,
                route_result,
                on_event,
                approval_resolver=approval_resolver,
                question_resolver=question_resolver,
                skip_initial_inbox_claim=True,
                initial_tool_results=[extra["recovered_tool_result"]] if extra.get("recovered_tool_result") else None,
            )
        finally:
            engine._approval_resolver = prev_resolver

    async def consume_next_step(self, *, iteration: int) -> list[InboxItem]:
        """步边界：先跑附件（压缩），再认领 steer / inject。"""
        self.step_index = iteration
        self.step_id = f"s{iteration}"
        await self._run_attachments()
        claimed = self.inbox.claim("next-step", turn=self.turn_index, step=iteration)
        if not claimed:
            return []
        self._record_claim(claimed, "next-step")
        self._append_step_items(claimed)
        return claimed

    async def turn_stopping(self) -> None:
        """自然停止钩子。压缩已挂在 pre_step / consume_next_step。"""
        return None

    def mark_step(self, iteration: int) -> None:
        self.step_index = iteration
        self.step_id = f"s{iteration}"
        if self._turn_record is not None:
            self._turn_record.update(status="running", step_index=iteration)
        self._persist_runtime_state()

    def _append_step_items(self, claimed: list[InboxItem]) -> None:
        for item in claimed:
            if item.kind in ("steer", "inject"):
                text = str(item.content or "").strip()
                if text:
                    kind = item.extra.get("prompt_kind") if item.kind == "inject" else None
                    if kind:
                        self.engine.memory.add_user_message(text, hidden=True, prompt_kind=kind)
                    else:
                        self.engine.memory.add_user_message(text)

    def _record_claim(self, claimed: list[InboxItem], target: InboxTarget) -> None:
        self.assembly_log.append(
            {
                "turn": self.turn_index,
                "step": self.step_index,
                "target": target,
                "contents": [item.content for item in claimed],
            },
        )
        if claimed:
            self._emit(
                ToolCallEvent(
                    event_type=EventType.INBOX_CLAIMED,
                    turn_id=self.turn_id,
                    step_id=self.step_id,
                    iteration=self.step_index,
                    inbox_claimed=[item.to_public_dict() for item in claimed],
                ),
            )

    def _emit(self, event: ToolCallEvent) -> None:
        emit = getattr(self.engine, "_emit", None)
        if emit is None:
            return
        emit(self._on_event, event)
