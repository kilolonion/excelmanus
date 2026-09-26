"""流中断连（incomplete chunked read）恢复语义测试。

上游模型服务在流式输出中途断开（peer closed connection ... incomplete
chunked read）时：

1. run_tool_loop 按可重试瞬时故障退避重试，预算耗尽后抛出
   ``LLMRetryExhaustedError``（并先发出 LLM_RETRY exhausted + FAILURE_GUIDANCE 事件）；
2. Agent Driver 将其降级成本轮失败收尾（明确提示 + truncated=True），
   回合与 SSE 流正常结束，不让传输异常击穿整轮。
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest

from excelmanus.agent.loop import run_tool_loop
from excelmanus.config import ExcelManusConfig
from excelmanus.engine import AgentEngine, ChatResult
from excelmanus.engine_core.llm_caller import LLMRetryExhaustedError
from excelmanus.events import EventType
from excelmanus.skillpacks import SkillMatchResult
from excelmanus.tools import ToolRegistry

_DISCONNECT_MESSAGE = (
    "peer closed connection without sending complete message body "
    "(incomplete chunked read)"
)


def _make_config(**overrides) -> ExcelManusConfig:
    defaults = {
        "api_key": "test-key",
        "base_url": "https://test.example.com/v1",
        "model": "test-model",
        "max_iterations": 20,
        "max_consecutive_failures": 3,
        "workspace_root": str(Path(__file__).resolve().parent),
        "llm_retry_max_attempts": 2,
    }
    defaults.update(overrides)
    return ExcelManusConfig(**defaults)


def _disconnect_error() -> Exception:
    """复刻 httpx/h11 断流异常链（映射层 raise ... from ...）。"""
    inner = httpx.RemoteProtocolError(_DISCONNECT_MESSAGE)
    outer = httpx.RemoteProtocolError(_DISCONNECT_MESSAGE)
    outer.__cause__ = inner
    return outer


@pytest.mark.asyncio
async def test_run_tool_loop_raises_typed_error_after_retry_exhausted(monkeypatch) -> None:
    """断流重试耗尽后抛 LLMRetryExhaustedError，并先发出重试耗尽/失败引导事件。"""
    monkeypatch.setattr("excelmanus.agent.loop.compute_retry_delay", lambda *_: 0)
    engine = AgentEngine(_make_config(), ToolRegistry())
    engine.memory.add_user_message("测试流中断连")
    mocked_call = AsyncMock(side_effect=_disconnect_error())
    engine._llm_caller.create_chat_completion_with_retry = mocked_call
    events = []
    route_result = SkillMatchResult(skills_used=[], route_mode="fallback", system_contexts=[])

    with pytest.raises(LLMRetryExhaustedError) as exc_info:
        await run_tool_loop(engine, route_result, on_event=events.append)

    assert mocked_call.await_count == 2
    assert exc_info.value.attempts == 2
    assert issubclass(type(exc_info.value), RuntimeError)
    kinds = [event.event_type for event in events]
    assert EventType.LLM_RETRY in kinds
    assert EventType.FAILURE_GUIDANCE in kinds
    exhausted = [
        event for event in events
        if event.event_type == EventType.LLM_RETRY and event.retry_status == "exhausted"
    ]
    assert exhausted
    # 原始断流异常保留在异常链中，分类器仍可判定为瞬时故障
    from excelmanus.engine_core.llm_caller import is_retryable_llm_error
    assert is_retryable_llm_error(exc_info.value)


@pytest.mark.asyncio
async def test_followup_degrades_gracefully_when_stream_disconnects(monkeypatch) -> None:
    """断流重试耗尽后回合不崩溃：返回明确的失败收尾结果，可继续对话。"""
    monkeypatch.setattr("excelmanus.agent.loop.compute_retry_delay", lambda *_: 0)
    engine = AgentEngine(_make_config(), ToolRegistry())
    mocked_call = AsyncMock(side_effect=_disconnect_error())
    engine._llm_caller.create_chat_completion_with_retry = mocked_call
    events = []

    result = await engine.followup("测试流中断连", on_event=events.append)

    assert isinstance(result, ChatResult)
    assert result.truncated is True
    assert "重试" in result.reply
    assert mocked_call.await_count == 2
    kinds = [event.event_type for event in events]
    assert EventType.TURN_FAILED in kinds
    assert EventType.TURN_END in kinds


@pytest.mark.asyncio
async def test_retry_then_reconnect_still_succeeds(monkeypatch) -> None:
    """断流后重连成功仍正常出结果，不触发降级收尾。"""
    from types import SimpleNamespace

    monkeypatch.setattr("excelmanus.agent.loop.compute_retry_delay", lambda *_: 0)
    engine = AgentEngine(_make_config(), ToolRegistry())
    engine.memory.add_user_message("测试断流后重连")
    ok_response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="已恢复", tool_calls=None))]
    )
    mocked_call = AsyncMock(side_effect=[_disconnect_error(), ok_response])
    engine._llm_caller.create_chat_completion_with_retry = mocked_call

    result = await engine.followup("测试断流后重连")

    assert isinstance(result, ChatResult)
    assert result.reply == "已恢复"
    assert result.truncated is False
    assert mocked_call.await_count == 2


@pytest.mark.asyncio
async def test_retry_recovery_notified_before_stream_completes(monkeypatch) -> None:
    """重试连通后立即发 LLM_RETRY succeeded，不等整段输出结束。

    回归点：``succeeded``（前端“模型服务已恢复”）必须在重试请求的首块
    内容到达时就发出——早于该次尝试的后续输出——且整轮只发一次。
    """
    from types import SimpleNamespace

    monkeypatch.setattr("excelmanus.agent.loop.compute_retry_delay", lambda *_: 0)
    engine = AgentEngine(_make_config(), ToolRegistry())
    engine.memory.add_user_message("测试恢复通知时机")
    events: list = []
    observed: list[bool] = []

    def _chunk(text: str) -> SimpleNamespace:
        return SimpleNamespace(
            content_delta=text,
            thinking_delta=None,
            tool_calls_delta=None,
            finish_reason=None,
            usage=None,
            replay_state=None,
        )

    def _has_recovered_notice() -> bool:
        return any(
            event.event_type == EventType.LLM_RETRY and event.retry_status == "succeeded"
            for event in events
        )

    async def _stream():
        yield _chunk("第一段")
        # 首块已被消费：此刻就应已收到“已恢复”通知，而不是等流结束
        observed.append(_has_recovered_notice())
        yield _chunk("第二段")
        yield SimpleNamespace(
            content_delta="",
            thinking_delta=None,
            tool_calls_delta=None,
            finish_reason="stop",
            usage=None,
            replay_state=None,
        )

    mocked_call = AsyncMock(side_effect=[_disconnect_error(), _stream()])
    engine._llm_caller.create_chat_completion_with_retry = mocked_call

    result = await engine.followup("测试恢复通知时机", on_event=events.append)

    assert isinstance(result, ChatResult)
    assert result.truncated is False
    assert "第一段" in result.reply and "第二段" in result.reply
    # 首块内容消费后即已通知“已恢复”
    assert observed == [True]
    succeeded = [
        event for event in events
        if event.event_type == EventType.LLM_RETRY and event.retry_status == "succeeded"
    ]
    assert len(succeeded) == 1, "提前通知后不应在整段输出结束后重复通知"
    assert succeeded[0].retry_attempt == 2
    first_delta_idx = next(
        i for i, event in enumerate(events) if event.event_type == EventType.TEXT_DELTA
    )
    succeeded_idx = next(
        i
        for i, event in enumerate(events)
        if event.event_type == EventType.LLM_RETRY and event.retry_status == "succeeded"
    )
    assert succeeded_idx < first_delta_idx
