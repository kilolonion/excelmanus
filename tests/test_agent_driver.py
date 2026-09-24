"""Wave B：Inbox + Driver（followup / steer / inject）。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from excelmanus.agent.inbox import Inbox
from excelmanus.config import ExcelManusConfig
from excelmanus.engine import AgentEngine, ChatResult
from excelmanus.events import EventType
from excelmanus.tools.registry import ToolDef, ToolRegistry


def _make_config(**overrides) -> ExcelManusConfig:
    defaults = {
        "api_key": "test-key",
        "base_url": "https://test.example.com/v1",
        "model": "test-model",
        "max_iterations": 8,
        "max_consecutive_failures": 3,
        "workspace_root": str(Path(__file__).resolve().parent),
    }
    defaults.update(overrides)
    return ExcelManusConfig(**defaults)


def _make_engine() -> AgentEngine:
    return AgentEngine(_make_config(), ToolRegistry())


def _text_response(content: str):
    from types import SimpleNamespace
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=None))],
    )


def _tool_response(call_id: str, name: str, arguments: str = "{}"):
    from types import SimpleNamespace
    tc = SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=arguments))
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="", tool_calls=[tc]))],
    )


def test_inbox_claim_one_followup_leaves_the_rest() -> None:
    inbox = Inbox()
    inbox.push_followup("a")
    inbox.push_followup("b")
    claimed = inbox.claim("next-turn", turn=1, step=1)
    assert [item.content for item in claimed] == ["a"]
    assert [item.content for item in inbox.next_turn] == ["b"]
    assert claimed[0].claimed is True
    assert claimed[0].claimed_turn == 1


def test_claim_next_turn_includes_pending_next_step() -> None:
    inbox = Inbox()
    inbox.push_inject("guide")
    inbox.push_steer("steer")
    inbox.push_followup("hello")
    claimed = inbox.claim("next-turn", turn=1, step=1)
    kinds = [item.kind for item in claimed]
    assert kinds == ["inject", "steer", "followup"]
    assert inbox.next_step == ()
    assert inbox.next_turn == ()


def test_claim_next_turn_without_followup_does_not_take_next_step() -> None:
    inbox = Inbox()
    inbox.push_steer("keep me")
    claimed = inbox.claim("next-turn", turn=1, step=1)
    assert claimed == []
    assert [item.content for item in inbox.next_step] == ["keep me"]


def test_rejected_claim_does_not_reenter() -> None:
    inbox = Inbox()
    item = inbox.push_followup("x")
    claimed = inbox.claim("next-turn", turn=1, step=1)
    assert claimed == [item]
    assert inbox.next_turn == ()
    reconstructed = inbox.reconstruct()
    assert reconstructed["next-turn"] == []


def test_inbox_reconstruct_roundtrip() -> None:
    inbox = Inbox()
    inbox.push_followup("later")
    inbox.push_inject("hint")
    data = inbox.reconstruct()
    other = Inbox()
    other.load_reconstructed(data)
    assert [item.content for item in other.next_turn] == ["later"]
    assert [item.content for item in other.next_step] == ["hint"]


def test_inbox_promotes_unconsumed_steer_to_next_turn() -> None:
    inbox = Inbox()
    item = inbox.push_steer(
        "改看 B 表",
        extra={"dispatch_id": "dsp-1", "client_message_id": "client-1", "dispatch_mode": "steer"},
    )
    promoted = inbox.promote_orphaned_steer()
    assert promoted == [item]
    assert inbox.next_step == ()
    assert inbox.next_turn[0].kind == "followup"
    assert inbox.next_turn[0].to_public_dict()["dispatch_id"] == "dsp-1"


def test_chat_entrypoint_removed() -> None:
    assert "chat" not in AgentEngine.__dict__


@pytest.mark.asyncio
async def test_plain_text_opens_turn_1_step_1_and_ends_turn() -> None:
    engine = _make_engine()
    events: list = []
    engine._client.chat.completions.create = AsyncMock(return_value=_text_response("ok"))
    result = await engine.followup("一条用户消息", on_event=events.append)
    assert result.reply == "ok"
    kinds = [e.event_type for e in events]
    assert EventType.TURN_START in kinds
    assert EventType.STEP_START in kinds
    assert EventType.STEP_END in kinds
    assert EventType.TURN_END in kinds
    assert EventType.INBOX_CLAIMED in kinds
    starts = [e for e in events if e.event_type == EventType.TURN_START]
    assert len(starts) == 1
    assert starts[0].turn_id == "t1"
    steps = [e for e in events if e.event_type == EventType.STEP_START]
    assert steps[0].step_id == "s1"
    assert engine._driver.status == "idle"
    log = engine._driver.assembly_log
    assert log[0]["turn"] == 1
    assert log[0]["step"] == 1
    assert log[0]["target"] == "next-turn"
    assert log[0]["contents"] == ["一条用户消息"]


@pytest.mark.asyncio
async def test_rejected_pre_step_does_not_write_memory_or_requeue() -> None:
    engine = _make_engine()
    engine._driver.add_pre_step_hook(lambda _claimed, _target: "reject")
    engine._client.chat.completions.create = AsyncMock(return_value=_text_response("不应调用"))
    result = await engine.followup("不要进模型")
    assert isinstance(result, ChatResult)
    assert result.reply == ""
    engine._client.chat.completions.create.assert_not_called()
    assert engine._driver.inbox.next_turn == ()
    user_contents = [
        m.get("content") for m in engine.memory.messages if m.get("role") == "user"
    ]
    assert "不要进模型" not in user_contents


@pytest.mark.asyncio
async def test_in_step_followup_enters_turn_2_not_current_tool_batch() -> None:
    engine = _make_engine()

    def ping() -> str:
        engine.push_interrupt_message("第二条")
        return "pong"

    engine._registry.register_tool(
        ToolDef(
            name="ping",
            description="ping",
            input_schema={"type": "object", "properties": {}},
            func=ping,
        )
    )
    events: list = []
    engine._client.chat.completions.create = AsyncMock(
        side_effect=[
            _tool_response("c1", "ping"),
            _text_response("turn1 done"),
            _text_response("turn2 done"),
        ]
    )
    result = await engine.followup("第一条", on_event=events.append)
    assert result.reply == "turn1 done"

    followup_claims = [
        entry for entry in engine._driver.assembly_log if entry["target"] == "next-turn"
    ]
    assert followup_claims[0]["turn"] == 1
    assert followup_claims[0]["contents"] == ["第一条"]
    assert followup_claims[1]["turn"] == 2
    assert followup_claims[1]["contents"] == ["第二条"]

    turn_ids = [e.turn_id for e in events if e.event_type == EventType.TURN_START]
    assert turn_ids == ["t1", "t2"]

    first_call_messages = engine._client.chat.completions.create.call_args_list[0].kwargs["messages"]
    user_blobs = [
        str(m.get("content"))
        for m in first_call_messages
        if m.get("role") == "user"
    ]
    assert any("第一条" in blob for blob in user_blobs)
    assert all("第二条" not in blob for blob in user_blobs)


@pytest.mark.asyncio
async def test_steer_is_visible_next_step_same_turn() -> None:
    engine = _make_engine()

    def ping() -> str:
        engine.steer("改用 B 表")
        return "pong"

    engine._registry.register_tool(
        ToolDef(
            name="ping",
            description="ping",
            input_schema={"type": "object", "properties": {}},
            func=ping,
        )
    )
    events: list = []
    engine._client.chat.completions.create = AsyncMock(
        side_effect=[
            _tool_response("c1", "ping"),
            _text_response("已改用 B 表"),
        ]
    )
    result = await engine.followup("先看 A 表", on_event=events.append)
    assert result.reply == "已改用 B 表"
    turn_starts = [e for e in events if e.event_type == EventType.TURN_START]
    assert len(turn_starts) == 1
    step_claims = [
        entry for entry in engine._driver.assembly_log if entry["target"] == "next-step"
    ]
    assert any("改用 B 表" in content for entry in step_claims for content in entry["contents"])
    second_messages = engine._client.chat.completions.create.call_args_list[-1].kwargs["messages"]
    assert any(
        m.get("role") == "user" and "改用 B 表" in str(m.get("content"))
        for m in second_messages
    )


@pytest.mark.asyncio
async def test_inject_does_not_wakeup_and_joins_next_followup() -> None:
    engine = _make_engine()
    engine.inject("系统提示：用中文")
    assert engine._driver.status == "idle"
    assert engine._driver.inbox.next_step
    engine._client.chat.completions.create = AsyncMock(return_value=_text_response("好的"))
    result = await engine.followup("开始")
    assert result.reply == "好的"
    first_claim = engine._driver.assembly_log[0]
    assert first_claim["target"] == "next-turn"
    assert "系统提示：用中文" in first_claim["contents"]
    assert "开始" in first_claim["contents"]
    user_contents = [
        m.get("content") for m in engine.memory.messages if m.get("role") == "user"
    ]
    assert user_contents[:2] == ["系统提示：用中文", "开始"]


@pytest.mark.asyncio
async def test_wait_for_item_recovers_followup_enqueued_at_idle_race() -> None:
    """A followup arriving after the actor's final queue check is retried.

    The actor may have already decided that no next turn exists while another
    request is enqueueing.  ``wait_for_item`` must wake a fresh actor instead
    of returning an empty ChatResult for the second caller.
    """
    from excelmanus.engine_types import ChatResult
    from excelmanus.agent.driver import Driver

    engine = SimpleNamespace(_tools_cache=None, _state=SimpleNamespace(increment_turn=lambda: None))
    driver = Driver(engine)
    engine._driver = driver
    first = driver.enqueue_followup("first")
    turn_checked = asyncio.Event()
    release = asyncio.Event()

    async def fake_turn() -> bool:
        current = first if first.result is None else second
        current.result = ChatResult(reply=current.content)
        turn_checked.set()
        await release.wait()
        return False

    driver.turn = fake_turn  # type: ignore[method-assign]
    first_kick = asyncio.create_task(driver.kick())
    await turn_checked.wait()
    second = driver.enqueue_followup("second")
    second_wait = asyncio.create_task(driver.wait_for_item(second))
    release.set()
    await first_kick
    result = await second_wait
    assert result.reply == "second"


@pytest.mark.asyncio
async def test_cancelling_one_kick_does_not_cancel_shared_actor() -> None:
    """A caller cancellation must leave the session actor available to others."""
    from excelmanus.engine_types import ChatResult
    from excelmanus.agent.driver import Driver

    engine = SimpleNamespace(_tools_cache=None, _state=SimpleNamespace(increment_turn=lambda: None))
    driver = Driver(engine)
    first = driver.enqueue_followup("first")
    second = driver.enqueue_followup("second")
    started = asyncio.Event()
    release = asyncio.Event()

    async def fake_turn() -> bool:
        current = first if first.result is None else second
        current.result = ChatResult(reply=current.content)
        started.set()
        await release.wait()
        return current is first

    driver.turn = fake_turn  # type: ignore[method-assign]
    owner = asyncio.create_task(driver.kick())
    await started.wait()
    owner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await owner
    release.set()
    result = await driver.wait_for_item(second)
    assert result.reply == "second"


@pytest.mark.asyncio
async def test_turn_timeout_cancels_llm_and_returns_functional_result() -> None:
    engine = AgentEngine(_make_config(turn_timeout_seconds=0.01), ToolRegistry())

    async def slow_completion(**_kwargs):
        await asyncio.sleep(1)

    engine._client.chat.completions.create = slow_completion
    result = await engine.followup("超时测试")
    assert "超时" in result.reply
    assert engine._driver.status == "idle"


def test_driver_runtime_snapshot_restores_inbox_and_interactions() -> None:
    from excelmanus.agent.driver import Driver
    from excelmanus.engine_core.session_state import SessionState
    from excelmanus.question_flow import QuestionFlowManager

    class _Approval:
        def __init__(self) -> None:
            self.pending = {"approval_id": "a1", "tool_name": "write_text_file"}

        def snapshot_pending(self):
            return self.pending

        def restore_pending(self, value):
            self.pending = value

    source_state = SessionState()
    source_engine = SimpleNamespace(
        _state=source_state,
        _approval=_Approval(),
        _question_flow=QuestionFlowManager(),
        save_session_snapshot=lambda: None,
    )
    source_driver = Driver(source_engine)
    source_driver.turn_index = 4
    source_driver.step_index = 2
    source_driver.enqueue_followup("resume me")
    source_state.runtime_state = source_driver.runtime_state()
    persisted = source_state.to_dict()

    restored_state = SessionState.from_dict(persisted)
    restored_engine = SimpleNamespace(
        _state=restored_state,
        _approval=_Approval(),
        _question_flow=QuestionFlowManager(),
        save_session_snapshot=lambda: None,
    )
    restored_driver = Driver(restored_engine)
    restored_driver.restore_runtime_state(restored_state.runtime_state)
    assert restored_driver.turn_index == 4
    assert restored_driver.step_index == 2
    assert [item.content for item in restored_driver.inbox.next_turn] == ["resume me"]
    assert restored_engine._approval.pending["approval_id"] == "a1"


def test_sse_live_turn_events_include_ids_and_keep_preparing_copy() -> None:
    from excelmanus.api_sse import sse_event_to_sse
    from excelmanus.events import ToolCallEvent

    preparing = ToolCallEvent(
        event_type=EventType.PIPELINE_PROGRESS,
        pipeline_stage="preparing",
        pipeline_message="正在准备本轮",
        turn_id="t1",
        step_id="s1",
        iteration=1,
    )
    payload = sse_event_to_sse(preparing)
    assert payload is not None
    assert "正在准备本轮" in payload
    assert "t1" in payload
    assert "s1" in payload
    assert "正在分析意图" not in payload

    turn_start = ToolCallEvent(
        event_type=EventType.TURN_START,
        turn_id="t1",
        iteration=1,
    )
    turn_sse = sse_event_to_sse(turn_start)
    assert turn_sse is not None
    assert "event: turn_start" in turn_sse
    assert "t1" in turn_sse

    claimed = ToolCallEvent(
        event_type=EventType.INBOX_CLAIMED,
        turn_id="t1",
        step_id="s1",
        inbox_claimed=[{"id": "inb_1", "kind": "followup", "content": "hi"}],
    )
    claimed_sse = sse_event_to_sse(claimed)
    assert claimed_sse is not None
    assert "event: inbox_claimed" in claimed_sse
