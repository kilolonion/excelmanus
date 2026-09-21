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
from excelmanus.providers.openai_responses import OpenAIResponsesClient, ResponsesAPIError
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


@pytest.mark.asyncio
async def test_openai_responses_stream_emits_reasoning_delta() -> None:
    client = OpenAIResponsesClient(api_key="k", base_url="https://api.openai.com/v1")
    response = _FakeStreamResponse(
        status_code=200,
        lines=[
            'data: {"type":"response.reasoning_summary_text.delta","delta":"思考片段"}',
            'data: {"type":"response.output_text.delta","delta":"最终答案"}',
            "data: [DONE]",
        ],
    )
    client._http.stream = lambda *args, **kwargs: _FakeStreamContext(response)

    try:
        stream = await client._generate_stream(
            model="gpt-5",
            messages=[{"role": "user", "content": "hi"}],
        )
        first_chunk = await anext(stream)
        assert isinstance(first_chunk, StreamDelta)
        assert first_chunk.thinking_delta == "思考片段"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_openai_responses_stream_separates_reasoning_summary_parts() -> None:
    """相邻 reasoning summary part 之间应补换行，避免 **标题** 段落粘连。"""
    client = OpenAIResponsesClient(api_key="k", base_url="https://api.openai.com/v1")
    response = _FakeStreamResponse(
        status_code=200,
        lines=[
            'data: {"type":"response.reasoning_summary_part.added","item_id":"rs_1","output_index":0,"summary_index":0,"part":{"type":"summary_text","text":""}}',
            'data: {"type":"response.reasoning_summary_text.delta","delta":"**Planning product name and price insertion**"}',
            'data: {"type":"response.reasoning_summary_part.added","item_id":"rs_1","output_index":0,"summary_index":1,"part":{"type":"summary_text","text":""}}',
            'data: {"type":"response.reasoning_summary_text.delta","delta":"**Implementing formulas and formatting after insertion**"}',
            "data: [DONE]",
        ],
    )
    client._http.stream = lambda *args, **kwargs: _FakeStreamContext(response)

    try:
        stream = await client._generate_stream(
            model="gpt-5",
            messages=[{"role": "user", "content": "hi"}],
        )
        thinking = ""
        async for chunk in stream:
            thinking += chunk.thinking_delta or ""
        assert thinking == (
            "**Planning product name and price insertion**\n"
            "**Implementing formulas and formatting after insertion**"
        )
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_openai_responses_stream_no_separator_before_first_summary_part() -> None:
    """首个 summary part 前不应产生前置换行。"""
    client = OpenAIResponsesClient(api_key="k", base_url="https://api.openai.com/v1")
    response = _FakeStreamResponse(
        status_code=200,
        lines=[
            'data: {"type":"response.reasoning_summary_part.added","item_id":"rs_1","output_index":0,"summary_index":0,"part":{"type":"summary_text","text":""}}',
            'data: {"type":"response.reasoning_summary_text.delta","delta":"**Only heading**"}',
            "data: [DONE]",
        ],
    )
    client._http.stream = lambda *args, **kwargs: _FakeStreamContext(response)

    try:
        stream = await client._generate_stream(
            model="gpt-5",
            messages=[{"role": "user", "content": "hi"}],
        )
        thinking = ""
        async for chunk in stream:
            thinking += chunk.thinking_delta or ""
        assert thinking == "**Only heading**"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_openai_responses_stream_forwards_reasoning_effort() -> None:
    client = OpenAIResponsesClient(api_key="k", base_url="https://api.openai.com/v1")
    captured_body: dict[str, Any] = {}
    response = _FakeStreamResponse(
        status_code=200,
        lines=[
            'data: {"type":"response.output_text.delta","delta":"ok"}',
            "data: [DONE]",
        ],
    )

    def _fake_stream(*args, **kwargs):
        captured_body.clear()
        captured_body.update(kwargs.get("json", {}))
        return _FakeStreamContext(response)

    client._http.stream = _fake_stream

    try:
        stream = await client.chat.completions.create(
            model="gpt-5.3-codex",
            messages=[{"role": "user", "content": "hi"}],
            stream=True,
            reasoning_effort="high",
        )
        await anext(stream)
    finally:
        await client.close()

    assert captured_body.get("reasoning", {}).get("effort") == "high"
    assert captured_body.get("reasoning", {}).get("summary") == "detailed"


# ── Responses 终态/失败事件与异常断流 ──────────────────────────


@pytest.mark.asyncio
async def test_openai_responses_stream_raises_on_response_failed() -> None:
    """response.failed 是已确认失败：抛出携带映射状态码的错误，而非静默结束。"""
    client = OpenAIResponsesClient(api_key="k", base_url="https://api.openai.com/v1")
    response = _FakeStreamResponse(
        status_code=200,
        lines=[
            'data: {"type":"response.output_text.delta","delta":"半截"}',
            'data: {"type":"response.failed","response":{"id":"resp_f","status":"failed","error":{"code":"server_error","message":"upstream boom"}}}',
            "data: [DONE]",
        ],
    )
    client._http.stream = lambda *args, **kwargs: _FakeStreamContext(response)

    try:
        stream = await client._generate_stream(
            model="gpt-5", messages=[{"role": "user", "content": "hi"}],
        )
        with pytest.raises(ResponsesAPIError) as exc_info:
            async for _ in stream:
                pass
        assert exc_info.value.status_code == 500
        assert "upstream boom" in str(exc_info.value)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_openai_responses_stream_raises_on_error_event() -> None:
    """error 事件同样属于已确认失败，错误码映射到 HTTP 语义状态码。"""
    client = OpenAIResponsesClient(api_key="k", base_url="https://api.openai.com/v1")
    response = _FakeStreamResponse(
        status_code=200,
        lines=[
            'data: {"type":"error","code":"rate_limit_exceeded","message":"slow down"}',
            "data: [DONE]",
        ],
    )
    client._http.stream = lambda *args, **kwargs: _FakeStreamContext(response)

    try:
        stream = await client._generate_stream(
            model="gpt-5", messages=[{"role": "user", "content": "hi"}],
        )
        with pytest.raises(ResponsesAPIError) as exc_info:
            async for _ in stream:
                pass
        assert exc_info.value.status_code == 429
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_openai_responses_stream_incomplete_is_terminal_length() -> None:
    """response.incomplete 是合法终态：finish_reason=length，不回退报错。"""
    client = OpenAIResponsesClient(api_key="k", base_url="https://api.openai.com/v1")
    response = _FakeStreamResponse(
        status_code=200,
        lines=[
            'data: {"type":"response.output_text.delta","delta":"partial"}',
            'data: {"type":"response.incomplete","response":{"id":"resp_i","status":"incomplete","output":[{"type":"message","content":[{"type":"output_text","text":"partial"}]}],"usage":{"input_tokens":3,"output_tokens":2}}}',
            "data: [DONE]",
        ],
    )
    client._http.stream = lambda *args, **kwargs: _FakeStreamContext(response)

    try:
        stream = await client._generate_stream(
            model="gpt-5", messages=[{"role": "user", "content": "hi"}],
        )
        deltas = [d async for d in stream]
        finish = next(d for d in deltas if d.finish_reason)
        assert finish.finish_reason == "length"
        assert finish.usage is not None
        assert finish.usage.prompt_tokens == 3
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_openai_responses_stream_premature_end_raises() -> None:
    """无终态事件且无 [DONE] 的断流必须报错（可重试），不得静默成功。"""
    from excelmanus.engine_core.llm_caller import is_retryable_llm_error

    client = OpenAIResponsesClient(api_key="k", base_url="https://api.openai.com/v1")
    response = _FakeStreamResponse(
        status_code=200,
        lines=[
            'data: {"type":"response.output_text.delta","delta":"半截输出"}',
        ],
    )
    client._http.stream = lambda *args, **kwargs: _FakeStreamContext(response)

    try:
        stream = await client._generate_stream(
            model="gpt-5", messages=[{"role": "user", "content": "hi"}],
        )
        with pytest.raises(ResponsesAPIError) as exc_info:
            async for _ in stream:
                pass
        assert is_retryable_llm_error(exc_info.value)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_openai_responses_stream_done_without_terminal_falls_back() -> None:
    """兼容不发终态事件的代理：[DONE] 视为正常收尾，补发 finish。"""
    client = OpenAIResponsesClient(api_key="k", base_url="https://api.openai.com/v1")
    response = _FakeStreamResponse(
        status_code=200,
        lines=[
            'data: {"type":"response.output_text.delta","delta":"ok"}',
            "data: [DONE]",
        ],
    )
    client._http.stream = lambda *args, **kwargs: _FakeStreamContext(response)

    try:
        stream = await client._generate_stream(
            model="gpt-5", messages=[{"role": "user", "content": "hi"}],
        )
        deltas = [d async for d in stream]
        finish = next(d for d in deltas if d.finish_reason)
        assert finish.finish_reason == "stop"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_openai_responses_generate_raises_on_failed_event() -> None:
    """非流式（收集式）路径同样把 response.failed 转成错误。"""
    client = OpenAIResponsesClient(api_key="k", base_url="https://api.openai.com/v1")
    response = _FakeStreamResponse(
        status_code=200,
        lines=[
            'data: {"type":"response.failed","response":{"status":"failed","error":{"code":"invalid_prompt","message":"bad input"}}}',
            "data: [DONE]",
        ],
    )
    client._http.stream = lambda *args, **kwargs: _FakeStreamContext(response)

    try:
        with pytest.raises(ResponsesAPIError) as exc_info:
            await client._generate(
                model="gpt-5", messages=[{"role": "user", "content": "hi"}],
            )
        assert exc_info.value.status_code == 422
        assert "bad input" in str(exc_info.value)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_openai_responses_generate_raises_without_terminal_event() -> None:
    """非流式路径在流结束却无终态事件时报错（可重试）。"""
    from excelmanus.engine_core.llm_caller import is_retryable_llm_error

    client = OpenAIResponsesClient(api_key="k", base_url="https://api.openai.com/v1")
    response = _FakeStreamResponse(
        status_code=200,
        lines=[
            'data: {"type":"response.output_text.delta","delta":"x"}',
            "data: [DONE]",
        ],
    )
    client._http.stream = lambda *args, **kwargs: _FakeStreamContext(response)

    try:
        with pytest.raises(ResponsesAPIError) as exc_info:
            await client._generate(
                model="gpt-5", messages=[{"role": "user", "content": "hi"}],
            )
        assert is_retryable_llm_error(exc_info.value)
    finally:
        await client.close()
