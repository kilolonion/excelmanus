from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Callable

import pytest

from excelmanus.providers import claude as claude_provider
from excelmanus.providers import gemini as gemini_provider
from excelmanus.providers import openai_responses as responses_provider
from excelmanus.providers.claude import ClaudeClient
from excelmanus.providers.gemini import GeminiClient
from excelmanus.providers.openai_responses import OpenAIResponsesClient
from excelmanus.providers.stream_types import StreamDelta


class _FakeStreamResponse:
    def __init__(self, *, status_code: int, lines: list[str]) -> None:
        self.status_code = status_code
        self._lines = lines

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def aread(self) -> bytes:
        return b"stream error"


class _FakeStreamContext:
    def __init__(self, response: _FakeStreamResponse) -> None:
        self._response = response

    async def __aenter__(self) -> _FakeStreamResponse:
        return self._response

    async def __aexit__(self, exc_type, exc, tb) -> None:
        del exc_type, exc, tb


@dataclass(frozen=True)
class _ProviderCase:
    name: str
    client_factory: Callable[[], Any]
    stream_lines: list[str]
    expected_content_delta: str


def _cases() -> list[_ProviderCase]:
    return [
        _ProviderCase(
            name="claude",
            client_factory=lambda: ClaudeClient(api_key="k", base_url="https://api.anthropic.com"),
            stream_lines=[
                'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"hello-claude"}}',
                "data: [DONE]",
            ],
            expected_content_delta="hello-claude",
        ),
        _ProviderCase(
            name="responses",
            client_factory=lambda: OpenAIResponsesClient(api_key="k", base_url="https://api.openai.com/v1"),
            stream_lines=[
                'data: {"type":"response.output_text.delta","delta":"hello-responses"}',
                "data: [DONE]",
            ],
            expected_content_delta="hello-responses",
        ),
        _ProviderCase(
            name="gemini",
            client_factory=lambda: GeminiClient(api_key="k", base_url="https://generativelanguage.googleapis.com/v1beta"),
            stream_lines=[
                'data: {"candidates":[{"content":{"parts":[{"text":"hello-gemini"}]}}]}',
            ],
            expected_content_delta="hello-gemini",
        ),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("case", _cases(), ids=lambda c: c.name)
async def test_chat_completions_create_stream_returns_async_iterator(case: _ProviderCase) -> None:
    client = case.client_factory()
    response = _FakeStreamResponse(status_code=200, lines=case.stream_lines)
    client._http.stream = lambda *args, **kwargs: _FakeStreamContext(response)

    try:
        stream = await client.chat.completions.create(
            model="test-model",
            messages=[{"role": "user", "content": "hi"}],
            stream=True,
        )

        was_coroutine = asyncio.iscoroutine(stream)
        if was_coroutine:
            stream = await stream

        assert not was_coroutine
        assert hasattr(stream, "__aiter__")

        if hasattr(stream, "aclose"):
            await stream.aclose()
    finally:
        await client.close()


def test_provider_stream_delta_aliases_point_to_shared_type() -> None:
    assert claude_provider._ChatCompletion._StreamDelta is StreamDelta
    assert responses_provider._ChatCompletion._StreamDelta is StreamDelta
    assert gemini_provider._ChatCompletion._StreamDelta is StreamDelta


@pytest.mark.asyncio
@pytest.mark.parametrize("case", _cases(), ids=lambda c: c.name)
async def test_generate_stream_emits_content_delta(case: _ProviderCase) -> None:
    client = case.client_factory()
    response = _FakeStreamResponse(status_code=200, lines=case.stream_lines)
    client._http.stream = lambda *args, **kwargs: _FakeStreamContext(response)

    try:
        stream = await client._generate_stream(
            model="test-model",
            messages=[{"role": "user", "content": "hi"}],
        )

        first_chunk = await anext(stream)
        assert isinstance(first_chunk, StreamDelta)
        assert first_chunk.content_delta == case.expected_content_delta

        if hasattr(stream, "aclose"):
            await stream.aclose()
    finally:
        await client.close()
