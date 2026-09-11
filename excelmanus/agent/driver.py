"""Driver：kick / turn / pre_step / step。

会话循环的所有权在这里。``AgentEngine`` 是服务包（memory、registry、
dispatcher）。一步之内的模型流与工具分派走 ``agent.loop``。
"""

from __future__ import annotations

import time
from collections.abc import Awaitable
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from excelmanus.agent.inbox import Inbox, InboxItem, InboxTarget
from excelmanus.events import EventType, ToolCallEvent
from excelmanus.logger import get_logger

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
        return self.inbox.push_followup(content, extra=extra)

    def steer(self, content: str, *, extra: dict[str, Any] | None = None) -> InboxItem:
        return self.inbox.push_steer(content, extra=extra)

    def inject(self, content: str, *, extra: dict[str, Any] | None = None) -> InboxItem:
        return self.inbox.push_inject(content, extra=extra)

    async def kick(self) -> None:
        if self.status == "running":
            return
        self.status = "running"
        try:
            while await self.turn():
                pass
        finally:
            self.status = "idle"

    async def turn(self) -> bool:
        engine = self.engine
        self.turn_index += 1
        self.step_index = 0
        self.turn_id = f"t{self.turn_index}"
        self.step_id = ""
        turn_started = time.monotonic()
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
                last_result = prepared.early_result
            else:
                ran_loop = True
                last_result = await self.step(prepared)
            if followup_item is not None:
                followup_item.result = last_result
            await self.turn_stopping()
            if ran_loop and last_result is not None:
                engine._finalize_driver_turn(
                    last_result,
                    on_event=self._on_event,
                    chat_start=turn_started,
                )
        finally:
            self._emit(
                ToolCallEvent(
                    event_type=EventType.TURN_END,
                    turn_id=self.turn_id,
                    iteration=self.turn_index,
                ),
            )
        return bool(self.inbox.next_turn)

    async def pre_step(self, target: InboxTarget) -> PreparedStep:
        self.step_index = max(self.step_index, 0) + 1
        self.step_id = f"s{self.step_index}"
        claimed = self.inbox.claim(target, turn=self.turn_index, step=self.step_index)
        followup_item = next((item for item in claimed if item.kind == "followup"), None)

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

        if await self._run_attachments() == "reject":
            return PreparedStep(
                kind="reject",
                messages=[],
                claimed=claimed,
                followup_item=followup_item,
            )

        self._record_claim(claimed, target)
        # pending next-step 先于本 turn 的 followup 进入 memory。
        self._append_step_items(claimed)

        early_result = None
        if followup_item is not None:
            extra = followup_item.extra
            on_event = extra.get("on_event")
            if on_event is not None:
                self._on_event = on_event
            early_result = await self.engine._apply_claimed_followup(followup_item)
            if early_result is not None:
                followup_item.result = early_result
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

        return await run_tool_loop(
            self.engine,
            route_result,
            on_event,
            approval_resolver=approval_resolver,
            question_resolver=question_resolver,
            skip_initial_inbox_claim=True,
        )

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

    def _append_step_items(self, claimed: list[InboxItem]) -> None:
        for item in claimed:
            if item.kind in ("steer", "inject"):
                text = str(item.content or "").strip()
                if text:
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
