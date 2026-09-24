"""Reasoning formats must agree between probing, streamed chat and fallback chat."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from excelmanus.engine_core.llm_caller import LLMCaller
from excelmanus.engine_utils import _extract_completion_message
from excelmanus.events import EventType
from excelmanus.model_probe import _try_thinking_stream
from excelmanus.providers.stream_types import StreamDelta


FORMATS = [
    {"reasoning_content": "Reasoning."},
    {"reasoning": "Reasoning."},
    {"thinking": "Reasoning."},
    {"thinking_text": "Reasoning."},
    {"reasoning": {"text": "Reasoning.", "signature": "metadata"}},
    {"reasoning": {"summary": [{"type": "summary_text", "text": "Reasoning."}]}},
    {"thinking": [{"type": "thinking", "thinking": "Reasoning."}]},
    {"reasoning_details": [{"type": "reasoning.text", "text": "Reasoning."}]},
    {"reasoning_details": [{"type": "reasoning.summary", "summary": "Reasoning."}]},
    {"reasoning_details": [
        {"type": "reasoning.encrypted", "data": "opaque"},
        {"type": "reasoning.text", "text": "Reasoning."},
    ]},
    {"content": [{"type": "thinking", "thinking": "Reasoning."}]},
    {"content": [{"type": "reasoning", "summary": [{"text": "Reasoning."}]}]},
    {"content": [{"thought": True, "text": "Reasoning.", "thoughtSignature": "metadata"}]},
    {"reasoning_content": "Reasoning.", "reasoning": "Reasoning.",
     "reasoning_details": [{"type": "reasoning.text", "text": "Reasoning."}]},
]


class Stream:
    def __init__(self, chunks):
        self.chunks = chunks
        self.closed = False

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for chunk in self.chunks:
            yield chunk

    async def close(self):
        self.closed = True


def chunk(fields):
    return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(**fields))])


async def probe(chunks):
    stream = Stream(chunks)
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=AsyncMock(return_value=stream),
    )))
    result = await _try_thinking_stream(client, "test", [], 1.0, {})
    assert stream.closed
    return result


async def chat(chunks):
    events = []
    engine = SimpleNamespace(_emit=lambda callback, event: events.append(event))
    stream = Stream(chunks)
    message, _ = await LLMCaller(engine).consume_stream(stream, None, 1)
    assert stream.closed
    assert not message._stream_truncated
    assert "".join(e.thinking_delta for e in events if e.event_type == EventType.THINKING_DELTA) == (message.thinking or "")
    assert "".join(e.text_delta for e in events if e.event_type == EventType.TEXT_DELTA) == message.content
    return message


@pytest.mark.asyncio
@pytest.mark.parametrize("fields", FORMATS)
async def test_probe_and_stream_agree_on_structured_reasoning(fields):
    # Gateways may emit a preamble before the first reasoning delta.
    chunks = [chunk({"content": "Lead "}), chunk(fields), chunk({"content": "Answer"})]
    assert await probe(chunks) == (True, "")
    message = await chat(chunks)
    assert message.thinking == "Reasoning."
    assert message.content == "Lead Answer"


@pytest.mark.parametrize("fields", FORMATS)
def test_nonstream_chat_normalizes_same_formats(fields):
    content = fields.get("content")
    content = [*content, {"type": "text", "text": "Answer"}] if content else "Answer"
    message, _ = _extract_completion_message({"choices": [{"message": {**fields, "content": content}}]})
    assert message.thinking == "Reasoning."
    assert message.reasoning_content == "Reasoning."
    assert message.content == "Answer"


@pytest.mark.asyncio
@pytest.mark.parametrize("fields", [
    {"thinking": True},
    {"reasoning": {"effort": "high", "tokens": 100}},
    {"reasoning_details": [{"type": "reasoning.encrypted", "data": "opaque", "text": "opaque"}]},
    {"thinking": {"type": "redacted_thinking", "data": "opaque", "text": "opaque"}},
    {"content": [{"type": "redacted_thinking", "data": "opaque"}]},
    {"thinking": {"signature": "signature-only"}},
])
async def test_metadata_does_not_count_as_visible_reasoning(fields):
    chunks = [chunk(fields), chunk({"content": "Answer"})]
    assert await probe(chunks) == (False, "")
    message = await chat(chunks)
    assert message.thinking is None
    assert message.content == "Answer"


@pytest.mark.asyncio
@pytest.mark.parametrize("tag", ["think", "thinking", "THINK", "Thinking"])
@pytest.mark.parametrize("native", [False, True])
async def test_fragmented_inline_tags_and_trailing_angle_bracket(tag, native):
    text = f"Lead <{tag}>Reasoning.</{tag}>Answer<"
    chunks = [StreamDelta(content_delta=c) if native else chunk({"content": c}) for c in text]
    assert await probe(chunks) == (True, "")
    message = await chat(chunks)
    assert message.thinking == "Reasoning."
    assert message.content == "Lead Answer<"


def test_nonstream_inline_tags_and_tool_calls_survive_normalization():
    message, _ = _extract_completion_message({"choices": [{"message": {
        "content": "<THINK>Reasoning.</THINK>Answer",
        "tool_calls": [{"id": "call1", "type": "function", "function": {"name": "test", "arguments": "{}"}}],
        "replay_state": {"signature": "preserved"},
    }}]})
    assert message.thinking == "Reasoning."
    assert message.content == "Answer"
    assert message.tool_calls[0].function.name == "test"
    assert message.replay_state == {"signature": "preserved"}


@pytest.mark.asyncio
async def test_normal_text_is_not_inferred_as_reasoning():
    chunks = [chunk({"content": "Analysis: 17 * 23 = 391. Thoughtful answer."})]
    assert await probe(chunks) == (False, "")
    message = await chat(chunks)
    assert message.thinking is None
    assert message.content == "Analysis: 17 * 23 = 391. Thoughtful answer."
