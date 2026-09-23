"""vision_capability.keyword_implies_vision 的 token 序列匹配测试。"""

from __future__ import annotations

import pytest

from excelmanus.vision_capability import keyword_implies_vision


@pytest.mark.parametrize("model", [
    "amazon.nova-lite-v1:0",
    "us.anthropic.claude-opus-5",
    "qwen3-vl-plus",
    "o1-pro",
    "gemini-3.8-flash",
    "llama-3.2-11b-vision-instruct",
    "chatgpt-4o-latest",
    "openrouter/anthropic/claude-sonnet-5",
    "gpt-5.2-codex",
    "kimi-k2.5",
    "glm-5.3",
    "MiniMax-M3",
    "mimo-v2.5",
    "mimo-v2.6-flash",
    "mimo-v2-omni",
])
def test_vision_models(model: str) -> None:
    assert keyword_implies_vision(model) is True


@pytest.mark.parametrize("model", [
    "amazon.nova-micro-v1:0",
    "qwen3-coder-plus",
    "gpt-4o-realtime-preview",
    "text-embedding-3-large",
    "moonshot-v1-8k",
    "kimi-k2",
    "o1-mini",
    "gemini-embedding-001",
    "llama-3.2-3b-instruct",
    "step-3.5-flash",
    "glm-4.7",
    "mimo-v2.5-pro",
    "mimo-v2.5-asr",
    "mimo-v2.5-tts",
    "mimo-v2-flash",
    "some-unknown-text-model",
])
def test_non_vision_models(model: str) -> None:
    assert keyword_implies_vision(model) is False
