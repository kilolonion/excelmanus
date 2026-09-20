from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from excelmanus.providers.claude import ClaudeClient
from excelmanus.providers.gemini import GeminiClient
from excelmanus.providers.openai_responses import OpenAIResponsesClient


class _DummyResponse:
    def __init__(self, *, status_code: int, payload: dict[str, Any], text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self) -> dict[str, Any]:
        return self._payload


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


class _FakeJsonResponse:
    def __init__(self, payload: dict[str, Any], status_code: int = 200) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict[str, Any]:
        return self._payload

    async def aread(self) -> bytes:
        return b"background error"


def _sample_chat_tools() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "ask_user",
                "description": "向用户提问",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "question": {
                            "type": "string",
                        }
                    },
                    "required": ["question"],
                },
            },
        }
    ]


@pytest.mark.asyncio
async def test_openai_responses_provider_maps_forced_tool_choice() -> None:
    client = OpenAIResponsesClient(api_key="k", base_url="https://example.com/v1")
    captured_body: dict[str, Any] = {}

    def _fake_stream(method: str, url: str, *, json: dict[str, Any], headers: dict[str, str]) -> _FakeStreamContext:
        del method, url, headers
        captured_body.clear()
        captured_body.update(json)
        response = _FakeStreamResponse(
            status_code=200,
            lines=[
                'data: {"type":"response.completed","response":{"output":[{"type":"message","content":[{"type":"output_text","text":"ok"}]}],"usage":{"input_tokens":1,"output_tokens":1}}}',
                "data: [DONE]",
            ],
        )
        return _FakeStreamContext(response)

    client._http.stream = _fake_stream
    try:
        await client.chat.completions.create(
            model="gpt-test",
            messages=[{"role": "user", "content": "hi"}],
            tools=_sample_chat_tools(),
            tool_choice={"type": "function", "function": {"name": "ask_user"}},
        )
    finally:
        await client.close()

    assert captured_body["tool_choice"] == {"type": "function", "name": "ask_user"}


@pytest.mark.asyncio
async def test_openai_responses_provider_maps_reasoning_effort() -> None:
    client = OpenAIResponsesClient(api_key="k", base_url="https://example.com/v1")
    captured_body: dict[str, Any] = {}

    def _fake_stream(method: str, url: str, *, json: dict[str, Any], headers: dict[str, str]) -> _FakeStreamContext:
        del method, url, headers
        captured_body.clear()
        captured_body.update(json)
        response = _FakeStreamResponse(
            status_code=200,
            lines=[
                'data: {"type":"response.completed","response":{"output":[{"type":"message","content":[{"type":"output_text","text":"ok"}]}],"usage":{"input_tokens":1,"output_tokens":1}}}',
                "data: [DONE]",
            ],
        )
        return _FakeStreamContext(response)

    client._http.stream = _fake_stream
    try:
        await client.chat.completions.create(
            model="gpt-test",
            messages=[{"role": "user", "content": "hi"}],
            reasoning_effort="low",
        )
    finally:
        await client.close()

    reasoning = captured_body.get("reasoning")
    assert isinstance(reasoning, dict)
    assert reasoning.get("effort") == "low"


@pytest.mark.asyncio
async def test_claude_provider_maps_required_and_forced_tool_choice() -> None:
    client = ClaudeClient(api_key="k", base_url="https://api.anthropic.com")
    captured_bodies: list[dict[str, Any]] = []

    async def _fake_post(url: str, *, json: dict[str, Any], headers: dict[str, str]) -> _DummyResponse:
        del url, headers
        captured_bodies.append(dict(json))
        return _DummyResponse(
            status_code=200,
            payload={
                "id": "msg_1",
                "model": "claude-test",
                "content": [{"type": "text", "text": "ok"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )

    client._http.post = AsyncMock(side_effect=_fake_post)
    try:
        await client.chat.completions.create(
            model="claude-test",
            messages=[{"role": "user", "content": "hi"}],
            tools=_sample_chat_tools(),
            tool_choice="required",
        )
        await client.chat.completions.create(
            model="claude-test",
            messages=[{"role": "user", "content": "hi"}],
            tools=_sample_chat_tools(),
            tool_choice={"type": "function", "function": {"name": "ask_user"}},
        )
    finally:
        await client.close()

    assert captured_bodies[0]["tool_choice"] == {"type": "any"}
    assert captured_bodies[1]["tool_choice"] == {"type": "tool", "name": "ask_user"}


@pytest.mark.asyncio
async def test_gemini_provider_maps_required_none_and_forced_tool_choice() -> None:
    client = GeminiClient(
        api_key="k",
        base_url="https://generativelanguage.googleapis.com/v1beta",
    )
    captured_bodies: list[dict[str, Any]] = []

    async def _fake_post(
        url: str,
        *,
        json: dict[str, Any],
        headers: dict[str, str],
        params: dict[str, str],
    ) -> _DummyResponse:
        del url, headers, params
        captured_bodies.append(dict(json))
        return _DummyResponse(
            status_code=200,
            payload={
                "candidates": [{"content": {"parts": [{"text": "ok"}]}, "finishReason": "STOP"}],
                "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1},
            },
        )

    client._http.post = AsyncMock(side_effect=_fake_post)
    try:
        await client.chat.completions.create(
            model="gemini-2.5-flash",
            messages=[{"role": "user", "content": "hi"}],
            tools=_sample_chat_tools(),
            tool_choice="required",
        )
        await client.chat.completions.create(
            model="gemini-2.5-flash",
            messages=[{"role": "user", "content": "hi"}],
            tools=_sample_chat_tools(),
            tool_choice="none",
        )
        await client.chat.completions.create(
            model="gemini-2.5-flash",
            messages=[{"role": "user", "content": "hi"}],
            tools=_sample_chat_tools(),
            tool_choice={"type": "function", "function": {"name": "ask_user"}},
        )
    finally:
        await client.close()

    assert captured_bodies[0]["toolConfig"] == {"functionCallingConfig": {"mode": "ANY"}}
    assert captured_bodies[1]["toolConfig"] == {"functionCallingConfig": {"mode": "NONE"}}
    assert captured_bodies[2]["toolConfig"] == {
        "functionCallingConfig": {
            "mode": "ANY",
            "allowedFunctionNames": ["ask_user"],
        }
    }


@pytest.mark.asyncio
async def test_openai_responses_provider_forwards_prompt_cache_key() -> None:
    """主循环设置的 prompt_cache_key 必须进入 /responses 请求体。"""
    client = OpenAIResponsesClient(api_key="k", base_url="https://example.com/v1")
    captured_body: dict[str, Any] = {}

    def _fake_stream(method: str, url: str, *, json: dict[str, Any], headers: dict[str, str]) -> _FakeStreamContext:
        del method, url, headers
        captured_body.clear()
        captured_body.update(json)
        response = _FakeStreamResponse(
            status_code=200,
            lines=[
                'data: {"type":"response.completed","response":{"output":[{"type":"message","content":[{"type":"output_text","text":"ok"}]}],"usage":{"input_tokens":1,"output_tokens":1}}}',
                "data: [DONE]",
            ],
        )
        return _FakeStreamContext(response)

    client._http.stream = _fake_stream
    try:
        await client.chat.completions.create(
            model="gpt-test",
            messages=[{"role": "user", "content": "hi"}],
            prompt_cache_key="em_session",
        )
    finally:
        await client.close()

    assert captured_body["prompt_cache_key"] == "em_session"


@pytest.mark.asyncio
async def test_openai_responses_stream_keeps_prompt_cache_key() -> None:
    """流式 Responses 路径同样透传 prompt_cache_key（首次出网不剥离）。"""
    client = OpenAIResponsesClient(api_key="k", base_url="https://example.com/v1")
    captured_bodies: list[dict[str, Any]] = []

    def _fake_stream(method: str, url: str, *, json: dict[str, Any], headers: dict[str, str]) -> _FakeStreamContext:
        del method, url, headers
        captured_bodies.append(dict(json))
        response = _FakeStreamResponse(
            status_code=200,
            lines=[
                'data: {"type":"response.output_text.delta","delta":"ok"}',
                'data: {"type":"response.completed","response":{"output":[{"type":"message","content":[{"type":"output_text","text":"ok"}]}],"usage":{"input_tokens":1,"output_tokens":1}}}',
                "data: [DONE]",
            ],
        )
        return _FakeStreamContext(response)

    client._http.stream = _fake_stream
    try:
        result = await client.chat.completions.create(
            model="gpt-test",
            messages=[{"role": "user", "content": "hi"}],
            prompt_cache_key="em_stream_session",
            stream=True,
        )
        if hasattr(result, "__aiter__"):
            async for _delta in result:
                pass
    finally:
        await client.close()

    assert captured_bodies
    assert all(body["prompt_cache_key"] == "em_stream_session" for body in captured_bodies)


@pytest.mark.asyncio
async def test_openai_responses_exposes_response_id_for_native_continuation() -> None:
    client = OpenAIResponsesClient(api_key="k", base_url="https://example.com/v1")
    captured_body: dict[str, Any] = {}

    def _fake_stream(method: str, url: str, *, json: dict[str, Any], headers: dict[str, str]) -> _FakeStreamContext:
        del method, url, headers
        captured_body.update(json)
        response = _FakeStreamResponse(
            status_code=200,
            lines=[
                'data: {"type":"response.completed","response":{"id":"resp_1","output":[{"type":"message","content":[{"type":"output_text","text":"ok"}]}],"usage":{"input_tokens":1,"output_tokens":1}}}',
                "data: [DONE]",
            ],
        )
        return _FakeStreamContext(response)

    client._http.stream = _fake_stream
    try:
        response = await client.chat.completions.create(
            model="gpt-test",
            messages=[{"role": "user", "content": "hi"}],
            stream=False,
        )
    finally:
        await client.close()

    message = response.choices[0].message
    assert response.response_id == "resp_1"
    assert message.replay_state == {"response_id": "resp_1"}


def test_responses_continuation_sends_only_new_items() -> None:
    from excelmanus.providers.request_body import responses_body

    body = responses_body(
        "gpt-test",
        [
            {"role": "user", "content": "first"},
            {
                "role": "assistant",
                "content": "ok",
                "replay_state": {"response_id": "resp_1"},
            },
            {"role": "user", "content": "follow up"},
        ],
        extra_kwargs={
            "_responses_previous_response_id": "resp_1",
            "_responses_store": True,
        },
    )

    assert body["previous_response_id"] == "resp_1"
    assert body["store"] is True
    assert [item["content"] for item in body["input"]] == ["follow up"]


def test_request_compiler_projects_responses_continuation_state() -> None:
    from types import SimpleNamespace

    from excelmanus.request.compiler import create_extra_from_engine

    engine = SimpleNamespace(
        _config=SimpleNamespace(responses_continuation_enabled=True),
        _active_protocol="openai_responses",
        _active_model="gpt-test",
        _active_profile=None,
        _thinking_config=None,
        _responses_last_response={
            "id": "resp_1",
            "protocol": "openai_responses",
            "model": "gpt-test",
        },
    )

    extra = create_extra_from_engine(engine)

    assert extra["_responses_previous_response_id"] == "resp_1"
    assert extra["_responses_store"] is True


@pytest.mark.asyncio
async def test_openai_responses_background_response_is_polled_to_terminal_state() -> None:
    client = OpenAIResponsesClient(api_key="k", base_url="https://example.com/v1")
    client._http.post = AsyncMock(return_value=_FakeJsonResponse({"id": "resp_bg", "status": "queued"}))
    client._http.get = AsyncMock(return_value=_FakeJsonResponse({
        "id": "resp_bg",
        "status": "completed",
        "output": [{"type": "message", "content": [{"type": "output_text", "text": "done"}]}],
        "usage": {"input_tokens": 2, "output_tokens": 1},
    }))
    try:
        response = await client.chat.completions.create(
            model="gpt-test",
            messages=[{"role": "user", "content": "long task"}],
            _responses_background=True,
            _responses_store=True,
        )
    finally:
        await client.close()

    assert response.choices[0].message.content == "done"
    client._http.get.assert_awaited_once()


@pytest.mark.asyncio
async def test_openai_responses_async_tool_done_event_is_normalized_once() -> None:
    client = OpenAIResponsesClient(api_key="k", base_url="https://example.com/v1")

    def _fake_stream(method: str, url: str, *, json: dict[str, Any], headers: dict[str, str]) -> _FakeStreamContext:
        del method, url, json, headers
        return _FakeStreamContext(_FakeStreamResponse(
            status_code=200,
            lines=[
                'data: {"type":"response.output_item.added","output_index":0,"item":{"type":"function_call","call_id":"fc_1","name":"lookup"}}',
                'data: {"type":"response.output_item.done","output_index":0,"item":{"type":"function_call","call_id":"fc_1","name":"lookup","arguments":"{\\"q\\":\\"x\\"}"}}',
                'data: {"type":"response.completed","response":{"id":"resp_async","output":[{"type":"function_call","call_id":"fc_1","name":"lookup","arguments":"{\\"q\\":\\"x\\"}"}]}}',
                "data: [DONE]",
            ],
        ))

    client._http.stream = _fake_stream
    try:
        stream = await client.chat.completions.create(
            model="gpt-test",
            messages=[{"role": "user", "content": "lookup"}],
            stream=True,
        )
        deltas = [delta async for delta in stream]
    finally:
        await client.close()

    tool_deltas = [delta.tool_calls_delta for delta in deltas if delta.tool_calls_delta]
    assert len(tool_deltas) == 1
    assert tool_deltas[0][0]["name"] == "lookup"
    assert tool_deltas[0][0]["arguments"] == '{"q":"x"}'


@pytest.mark.asyncio
async def test_openai_responses_background_handles_are_queryable_and_cancelable() -> None:
    client = OpenAIResponsesClient(api_key="k", base_url="https://example.com/v1")
    client._http.get = AsyncMock(return_value=_FakeJsonResponse({"id": "resp_1", "status": "in_progress"}))
    client._http.post = AsyncMock(return_value=_FakeJsonResponse({"id": "resp_1", "status": "cancelled"}))
    try:
        status = await client.get_background_response("resp_1")
        cancelled = await client.cancel_background_response("resp_1")
    finally:
        await client.close()

    assert status["status"] == "in_progress"
    assert cancelled["status"] == "cancelled"
    assert client._http.post.call_args.args[0].endswith("/responses/resp_1/cancel")


@pytest.mark.asyncio
async def test_openai_responses_steer_response_uses_previous_response_id() -> None:
    client = OpenAIResponsesClient(api_key="k", base_url="https://example.com/v1")
    captured_body: dict[str, Any] = {}

    def _fake_stream(method: str, url: str, *, json: dict[str, Any], headers: dict[str, str]) -> _FakeStreamContext:
        del method, url, headers
        captured_body.update(json)
        return _FakeStreamContext(_FakeStreamResponse(
            status_code=200,
            lines=[
                'data: {"type":"response.completed","response":{"id":"resp_2","output":[{"type":"message","content":[{"type":"output_text","text":"steered"}]}]}}',
                "data: [DONE]",
            ],
        ))

    client._http.stream = _fake_stream
    try:
        result = await client.steer_response("resp_1", "请改用第二种口径", model="gpt-test")
    finally:
        await client.close()

    assert result.choices[0].message.content == "steered"
    assert captured_body["previous_response_id"] == "resp_1"
    assert captured_body["store"] is True
