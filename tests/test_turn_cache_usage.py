"""Cache usage reaches each completed turn, its transport payload and renderer."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from excelmanus.agent.session import AgentEngine
from excelmanus.api_routes_chat import _build_reply_sse
from excelmanus.api_sse import sse_event_to_sse
from excelmanus.config import ExcelManusConfig
from excelmanus.events import EventType
from excelmanus.renderer import StreamRenderer
from excelmanus.tools.registry import ToolDef, ToolRegistry


def _engine(tmp_path, **config):
    registry = ToolRegistry()
    registry.register_tool(ToolDef(
        name="add_numbers", description="Add numbers",
        input_schema={"type": "object", "properties": {}}, func=lambda: 2,
    ))
    return AgentEngine(ExcelManusConfig(
        api_key="test", base_url="https://test.example/v1", model="test-model",
        workspace_root=str(tmp_path), **config,
    ), registry)


def _response(usage, *, tool=False):
    calls = [SimpleNamespace(
        id="call_1", type="function",
        function=SimpleNamespace(name="add_numbers", arguments="{}"),
    )] if tool else None
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="ok", tool_calls=calls))],
        usage=usage,
    )


def _payload(sse):
    return json.loads(next(line[6:] for line in sse.splitlines() if line.startswith("data: ")))


@pytest.mark.asyncio
async def test_cache_usage_sums_calls_and_resets_next_turn(tmp_path):
    engine = _engine(tmp_path)
    engine._client.chat.completions.create = AsyncMock(side_effect=[
        _response({"prompt_tokens": 5000, "completion_tokens": 10,
                   "prompt_tokens_details": {"cached_tokens": 4000}}, tool=True),
        _response({"prompt_tokens": 10000, "completion_tokens": 20,
                   "prompt_cache_hit_tokens": 1000}),
        _response({"prompt_tokens": 500, "completion_tokens": 5,
                   "prompt_tokens_details": {"cached_tokens": 0}}),
    ])
    events = []
    result = await engine.followup("算一下", on_event=events.append)
    assert (result.prompt_tokens, result.cached_tokens, result.total_tokens) == (15000, 5000, 15030)
    assert result.iterations == 2
    assert engine._session_diagnostics[-1]["cached_tokens"] == 5000
    for kind in (EventType.CHAT_SUMMARY, EventType.TURN_REPLY):
        event = next(event for event in events if event.event_type == kind)
        assert _payload(sse_event_to_sse(event))["cached_tokens"] == 5000
        assert "缓存命中 5,000 tokens (33.3%)" in StreamRenderer._format_token_usage(event)
    assert _payload(_build_reply_sse(result, engine))["cached_tokens"] == 5000

    result = await engine.followup("下一轮")
    assert result.cached_tokens == 0
    assert result.prompt_tokens == 500


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_first", [False, True])
async def test_partial_cache_usage_stays_unknown(tmp_path, missing_first):
    engine = _engine(tmp_path)
    known = {"prompt_tokens": 500, "completion_tokens": 5,
             "prompt_tokens_details": {"cached_tokens": 100}}
    unknown = {"prompt_tokens": 700, "completion_tokens": 5}
    usages = [unknown, known] if missing_first else [known, unknown]
    engine._client.chat.completions.create = AsyncMock(side_effect=[
        _response(usages[0], tool=True), _response(usages[1]),
    ])
    events = []
    result = await engine.followup("算一下", on_event=events.append)
    assert result.prompt_tokens == 1200
    assert result.cached_tokens is None
    summary = next(event for event in events if event.event_type == EventType.CHAT_SUMMARY)
    assert _payload(sse_event_to_sse(summary))["cached_tokens"] is None
    assert "缓存命中 未提供" in StreamRenderer._format_token_usage(summary)


@pytest.mark.asyncio
async def test_budget_exit_keeps_last_calls_cache_usage(tmp_path):
    engine = _engine(tmp_path, turn_token_budget=10)
    engine._client.chat.completions.create = AsyncMock(return_value=_response({
        "prompt_tokens": 7, "completion_tokens": 4,
        "cache_read_input_tokens": 5, "cache_creation_input_tokens": 1,
    }))
    result = await engine.followup("预算测试")
    assert result.truncated
    assert result.cached_tokens == 5
    assert result.prompt_tokens == 7
