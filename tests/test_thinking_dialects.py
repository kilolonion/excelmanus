"""Probe-selected gateway dialects must also enable reasoning in chat requests."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from openai import AsyncOpenAI

from excelmanus.engine_types import ThinkingConfig
from excelmanus.model_probe import ModelCapabilities, probe_thinking
from excelmanus.providers import ClaudeClient, GeminiClient
from excelmanus.providers.stream_types import StreamDelta
from excelmanus.request.compiler import create_extra_from_engine
from tests.test_reasoning_formats import Stream


def matches(body, required):
    return isinstance(body, dict) and all(
        matches(body.get(key), value) if isinstance(value, dict) else body.get(key) == value
        for key, value in required.items()
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("model,required,expected_type", [
    ("proxy/deepseek-v4.1-flash", {"thinking": {"type": "enabled"}}, "glm_thinking"),
    ("proxy/qwen3", {"chat_template_kwargs": {"enable_thinking": True}}, "chat_template"),
    ("alias-enable", {"enable_thinking": True}, "enable_thinking"),
    ("alias-thinking", {"thinking": {"type": "enabled"}}, "glm_thinking"),
    ("alias-effort", {"reasoning_effort": "high"}, "openai_reasoning"),
    ("alias-template", {"chat_template_kwargs": {"enable_thinking": True}}, "chat_template"),
    ("alias-reasoning", {"reasoning": {}}, "openrouter"),
    ("anthropic/claude-sonnet-4-5", {"thinking": {"type": "enabled"}}, "claude_compat"),
])
async def test_gateway_dialect_survives_probe_to_chat(model, required, expected_type):
    requests = []

    def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        budget = body.get("thinking", {}).get("budget_tokens", 0)
        if budget and body.get("max_tokens", budget + 1) <= budget:
            return httpx.Response(400, json={"error": {"message": "max_tokens must exceed thinking budget_tokens"}})
        enabled = matches(body, required)
        if body.get("stream"):
            delta = {"reasoning_content": "Reasoning."} if enabled else {"content": "Answer"}
            payload = {
                "id": "probe", "model": model, "created": 1, "object": "chat.completion.chunk",
                "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
            }
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                 text=f"data: {json.dumps(payload)}\n\ndata: [DONE]\n\n")
        return httpx.Response(200, json={
            "id": "chat", "model": model, "created": 1, "object": "chat.completion",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {
                "role": "assistant", "content": "Answer", "reasoning_content": "Reasoning." if enabled else "",
            }}],
        })

    async with AsyncOpenAI(
        api_key="test", base_url="https://gateway.example/v1", max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
    ) as client:
        supported, error, thinking_type = await probe_thinking(client, model, "https://gateway.example/v1")
        assert (supported, error, thinking_type) == (True, "", expected_type)
        engine = SimpleNamespace(
            _active_model=model, _active_protocol="openai",
            _model_capabilities=ModelCapabilities(supports_thinking=supported, thinking_type=thinking_type),
            _thinking_config=ThinkingConfig(effort="high"),
        )
        result = await client.chat.completions.create(
            model=model, messages=[{"role": "user", "content": "Test"}],
            **create_extra_from_engine(engine),
        )
        assert result.choices[0].message.reasoning_content == "Reasoning."
        assert matches(requests[-1], required)


@pytest.mark.asyncio
@pytest.mark.parametrize("client_type,expected_type", [(ClaudeClient, "claude"), (GeminiClient, "gemini")])
async def test_native_probe_checks_thinking_after_content_and_closes(client_type, expected_type):
    client = client_type.__new__(client_type)
    stream = Stream([StreamDelta(content_delta="Preamble"), StreamDelta(thinking_delta="Reasoning.")])
    create = AsyncMock(return_value=stream)
    client.chat = SimpleNamespace(completions=SimpleNamespace(create=create))

    assert await probe_thinking(client, "test", "https://example.com") == (True, "", expected_type)
    assert stream.closed
    assert "max_tokens" not in create.await_args.kwargs


@pytest.mark.asyncio
@pytest.mark.parametrize("client_type", [ClaudeClient, GeminiClient])
async def test_native_auth_failure_does_not_mark_thinking_unsupported(client_type):
    client = client_type.__new__(client_type)
    client.chat = SimpleNamespace(completions=SimpleNamespace(
        create=AsyncMock(side_effect=RuntimeError("401 unsupported authentication")),
    ))
    supported, error, _ = await probe_thinking(client, "test", "https://example.com")
    assert supported is None
    assert "401" in error
