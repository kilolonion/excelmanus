from types import SimpleNamespace

import pytest

from excelmanus.providers import create_client
from excelmanus.providers.mimo import (
    MimoClient,
    _MimoChat,
    is_mimo_base_url,
    sanitize_mimo_request,
)


def test_mimo_host_detection() -> None:
    assert is_mimo_base_url("https://api.xiaomimimo.com/v1")
    assert is_mimo_base_url("https://mimo.mi.com/v1")
    assert not is_mimo_base_url("https://api.openai.com/v1")


def test_create_client_selects_mimo_transport() -> None:
    client = create_client(
        api_key="test-key",
        base_url="https://api.xiaomimimo.com/v1",
        protocol="openai",
        model="mimo-v2.6-pro-ultraspeed",
    )
    assert isinstance(client, MimoClient)


def test_sanitize_mimo_request_removes_gateway_extensions() -> None:
    body = sanitize_mimo_request(
        {
            "model": "mimo-v2.6-pro-ultraspeed",
            "prompt_cache_key": "em_session",
            "service_tier": "priority",
            "stream_options": {"include_usage": True},
            "max_tokens": 300,
            "extra_body": {
                "thinking": {"type": "enabled"},
                "reasoning_effort": "medium",
                "prompt_cache_key": "nested",
            },
        }
    )
    assert body["max_completion_tokens"] == 300
    assert body["extra_body"] == {"thinking": {"type": "enabled"}}
    assert all(key not in body for key in ("prompt_cache_key", "service_tier", "stream_options", "max_tokens"))


def test_sanitize_mimo_request_rejects_silent_reasoning_downgrade() -> None:
    with pytest.raises(ValueError, match="reasoning_content"):
        sanitize_mimo_request(
            {
                "messages": [
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [{"id": "call_1", "type": "function"}],
                    },
                    {"role": "tool", "tool_call_id": "call_1", "content": "ok"},
                ],
                "extra_body": {"thinking": {"type": "enabled"}},
            }
        )

@pytest.mark.asyncio
async def test_mimo_client_sanitizes_before_openai_transport() -> None:
    captured = {}

    async def create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace()

    client = MimoClient.__new__(MimoClient)
    client._inner = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
    )
    client.chat = _MimoChat(client)

    await client.chat.completions.create(
        model="mimo-v2.6-pro-ultraspeed",
        messages=[{"role": "user", "content": "hi"}],
        stream=True,
        prompt_cache_key="em_session",
        stream_options={"include_usage": True},
        extra_body={"thinking": {"type": "enabled"}, "reasoning_effort": "high"},
    )
    assert "prompt_cache_key" not in captured
    assert "stream_options" not in captured
    assert captured["extra_body"] == {"thinking": {"type": "enabled"}}
