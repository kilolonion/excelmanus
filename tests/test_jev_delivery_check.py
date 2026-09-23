"""Jev 交付检查保持实时流式输出，仅撤回未通过的草稿。禁止打网。"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from excelmanus.agent.loop import run_tool_loop
from excelmanus.config import ExcelManusConfig
from excelmanus.engine import AgentEngine
from excelmanus.engine_types import ToolCallResult
from excelmanus.engine_core.tool_result import ToolResult
from excelmanus.events import EventType
from excelmanus.providers.stream_types import StreamDelta
from excelmanus.skillpacks import SkillMatchResult
from excelmanus.system_one.types import Decision
from excelmanus.tools import ToolRegistry


def _config(**overrides: object) -> ExcelManusConfig:
    values: dict[str, object] = {
        "api_key": "test-key",
        "base_url": "https://test.example.com/v1",
        "model": "test-model",
        "max_iterations": 8,
        "max_consecutive_failures": 3,
        "workspace_root": str(Path(__file__).resolve().parent),
        "ai_gateway_api_key": "vck_test",
    }
    values.update(overrides)
    return ExcelManusConfig(**values)


def _route() -> SkillMatchResult:
    return SkillMatchResult(skills_used=[], route_mode="fallback", system_contexts=[])


async def _text_stream(text: str):
    yield StreamDelta(content_delta=text)
    yield StreamDelta(
        finish_reason="stop",
        usage={"prompt_tokens": 2, "completion_tokens": 1},
    )


def _queue_streams(engine: AgentEngine, texts: list[str]) -> AsyncMock:
    """每次 LLM 调用返回一条新的流式响应。"""
    streams = [_text_stream(text) for text in texts]
    mocked = AsyncMock(side_effect=lambda **_kwargs: streams.pop(0))
    engine._client.chat.completions.create = mocked
    return mocked


def _text_deltas(events: list) -> list[str]:
    return [
        ev.text_delta
        for ev in events
        if getattr(ev, "event_type", None) == EventType.TEXT_DELTA
    ]


def _verify_decision(next_step: str) -> Decision:
    return Decision(
        kind="noop",
        reason=f"next:{next_step}",
        extras={"satisfied": 0.8, "scope_ok": 0.9, "next": next_step},
        applied=True,
    )


def _written_engine(**overrides: object) -> AgentEngine:
    engine = AgentEngine(_config(**overrides), ToolRegistry())
    engine._state.affected_files = ["a.xlsx"]
    return engine


@pytest.mark.asyncio
@pytest.mark.parametrize("emit_events", [True, False])
async def test_delivery_check_retracts_draft_and_reasks(emit_events: bool) -> None:
    """inspect_more：已流式展示的草稿撤回，建议仍注入下一迭代。"""
    engine = _written_engine()
    mocked_create = _queue_streams(engine, ["草稿答复", "核对后的最终答复"])
    events: list = []
    initial = [ToolCallResult("edit_spreadsheet", {"file_path": "a.xlsx"}, "ok", True)]
    with patch(
        "excelmanus.system_one.evaluate",
        AsyncMock(return_value=_verify_decision("inspect_more")),
    ) as mocked_eval:
        result = await run_tool_loop(
            engine, _route(), on_event=events.append if emit_events else None,
            initial_tool_results=initial,
        )
    mocked_eval.assert_awaited_once()
    assert mocked_create.await_count == 2
    assert _text_deltas(events) == (["草稿答复", "核对后的最终答复"] if emit_events else [])
    retractions = [event for event in events if event.event_type == EventType.RETRACT_TEXT]
    assert len(retractions) == (1 if emit_events else 0)
    if emit_events:
        assert retractions[0].iteration == 1
    assert result.reply == "核对后的最终答复"
    # 草稿进了记忆但不展示；隐藏建议消息已注入
    hidden = [
        m for m in engine._memory.messages
        if m.get("_prompt_kind") == "jev_delivery_check"
    ]
    assert len(hidden) == 1
    assert hidden[0].get("_ui_hidden") is True
    assert "交付检查" in hidden[0]["content"]
    assert engine._mutation_verification["next"] == "inspect_more"
    draft = next(m for m in engine._memory.messages if m.get("_prompt_kind") == "jev_delivery_draft")
    from excelmanus.chat_history import ChatHistoryStore
    assert ChatHistoryStore._durable_payload(draft)["_ui_hidden"] is True


@pytest.mark.asyncio
async def test_delivery_check_does_not_buffer_live_text() -> None:
    """第一段须在第二段生成前到达 UI，且核对期间无需等待整段刷新。"""
    engine = _written_engine()
    events: list = []
    async def live_stream():
        yield StreamDelta(content_delta="第一部分")
        assert _text_deltas(events) == ["第一部分"]
        yield StreamDelta(content_delta="第二部分")
        assert _text_deltas(events) == ["第一部分", "第二部分"]
        yield StreamDelta(finish_reason="stop", usage={"prompt_tokens": 2, "completion_tokens": 2})
    engine._client.chat.completions.create = AsyncMock(side_effect=lambda **_kw: live_stream())
    initial = [ToolCallResult("edit_spreadsheet", {"file_path": "a.xlsx"}, "ok", True)]
    initial[0].structured = ToolResult(success=True, model_text="ok", value={
        "meta": {"write_verification": {"status": "success", "sheet": "Sheet1"}},
    })
    with patch(
        "excelmanus.system_one.evaluate",
        AsyncMock(return_value=_verify_decision("none")),
    ) as mocked_eval:
        result = await run_tool_loop(
            engine, _route(), on_event=events.append,
            initial_tool_results=initial,
        )
    mocked_eval.assert_awaited_once()
    assert _text_deltas(events) == ["第一部分", "第二部分"]
    assert result.reply == "第一部分第二部分"
    assert not any(event.event_type == EventType.RETRACT_TEXT for event in events)


async def _multi_chunk_stream():
    yield StreamDelta(content_delta="第一部分")
    yield StreamDelta(content_delta="第二部分")
    yield StreamDelta(
        finish_reason="stop",
        usage={"prompt_tokens": 2, "completion_tokens": 2},
    )


@pytest.mark.asyncio
async def test_delivery_check_lists_unevidenced_items() -> None:
    """A1.5：inspect_more 且 items 有缺证据项时，建议列出待核对事项与如实收尾要求。"""
    engine = _written_engine()
    mocked_create = _queue_streams(engine, ["草稿答复", "最终答复"])
    events: list = []
    initial = [ToolCallResult("edit_spreadsheet", {"file_path": "a.xlsx"}, "ok", True)]
    decision = Decision(
        kind="noop",
        reason="checklist_items_unevidenced",
        extras={
            "satisfied": 0.9,
            "scope_ok": 0.9,
            "next": "inspect_more",
            "missing_items": 1,
            "items": [
                {"id": "item_1", "text": "把 A 列标红", "verdict": "evidenced", "confidence": 0.9},
                {"id": "item_2", "text": "汇总 B 列", "verdict": "missing", "confidence": 0.9},
            ],
        },
        applied=True,
    )
    with patch("excelmanus.system_one.evaluate", AsyncMock(return_value=decision)):
        result = await run_tool_loop(
            engine, _route(), on_event=events.append,
            initial_tool_results=initial,
        )
    assert mocked_create.await_count == 2
    hidden = [
        m for m in engine._memory.messages
        if m.get("_prompt_kind") == "jev_delivery_check"
    ]
    assert len(hidden) == 1
    content = hidden[0]["content"]
    assert "待核对事项" in content
    assert "汇总 B 列" in content
    assert "如实列为未完成" in content
    assert _text_deltas(events) == ["草稿答复", "最终答复"]
    assert sum(event.event_type == EventType.RETRACT_TEXT for event in events) == 1
    assert result.reply == "最终答复"


@pytest.mark.asyncio
async def test_read_only_turn_streams_live_without_evaluate() -> None:
    """只读回合不暂存、不评估，TEXT_DELTA 实时发出。"""
    engine = AgentEngine(_config(), ToolRegistry())
    events: list = []
    engine._client.chat.completions.create = AsyncMock(
        side_effect=lambda **_kw: _text_stream("只读答复"),
    )
    with patch("excelmanus.system_one.evaluate", AsyncMock()) as mocked_eval:
        result = await run_tool_loop(engine, _route(), on_event=events.append)
    mocked_eval.assert_not_awaited()
    assert _text_deltas(events) == ["只读答复"]
    assert result.reply == "只读答复"
    assert not any(
        m.get("_prompt_kind") == "jev_delivery_check"
        for m in engine._memory.messages
        if isinstance(m, dict)
    )
