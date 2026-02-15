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

    async def _fake_post(url: str, *, json: dict[str, Any], headers: dict[str, str]) -> _DummyResponse:
        del url, headers
        captured_body.clear()
        captured_body.update(json)
        return _DummyResponse(
            status_code=200,
            payload={
                "id": "resp_1",
                "model": "gpt-test",
                "status": "completed",
                "output": [{"type": "message", "content": [{"type": "output_text", "text": "ok"}]}],
                "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            },
        )

    client._http.post = AsyncMock(side_effect=_fake_post)
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
