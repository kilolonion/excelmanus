"""首次 LLM 调用必须带上 prompt_cache_key / stream_options。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from excelmanus.engine_core.llm_caller import (
    LLMCaller,
    _is_context_length_error,
    is_retryable_llm_error,
    reset_degraded_params,
)


@pytest.fixture(autouse=True)
def _reset_sticky_degrade() -> None:
    reset_degraded_params()
    yield
    reset_degraded_params()


def _caller(create) -> LLMCaller:
    engine = SimpleNamespace(
        _client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))),
        _config=SimpleNamespace(model="test"),
    )
    return LLMCaller(engine)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_first_create_keeps_prompt_cache_key_and_stream_options() -> None:
    captured: dict[str, object] = {}

    async def create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(ok=True)

    caller = _caller(create)
    await caller.create_chat_completion_with_retry(
        {
            "model": "test",
            "messages": [{"role": "user", "content": "hi"}],
            "prompt_cache_key": "em_abc",
            "stream_options": {"include_usage": True},
            "_thinking_budget": 32,
        }
    )
    assert captured["prompt_cache_key"] == "em_abc"
    assert captured["stream_options"] == {"include_usage": True}
    assert "_thinking_budget" not in captured


@pytest.mark.asyncio
async def test_unsupported_params_are_stripped_one_round_at_a_time() -> None:
    calls: list[dict[str, object]] = []

    async def create(**kwargs):
        calls.append(dict(kwargs))
        if "prompt_cache_key" in kwargs:
            raise TypeError("create() got an unexpected keyword argument 'prompt_cache_key'")
        if "stream_options" in kwargs:
            raise TypeError("create() got an unexpected keyword argument 'stream_options'")
        return SimpleNamespace(ok=True)

    caller = _caller(create)
    await caller.create_chat_completion_with_retry(
        {
            "model": "test",
            "messages": [{"role": "user", "content": "hi"}],
            "prompt_cache_key": "em_abc",
            "stream_options": {"include_usage": True},
        }
    )
    assert calls[0]["prompt_cache_key"] == "em_abc"
    assert "prompt_cache_key" not in calls[1]
    assert "stream_options" in calls[1]
    assert "prompt_cache_key" not in calls[2]
    assert "stream_options" not in calls[2]


def test_max_tokens_parameter_error_is_not_context_overflow() -> None:
    assert _is_context_length_error(RuntimeError("max_tokens must be <= 4096")) is False
    assert _is_context_length_error(RuntimeError("context_length_exceeded")) is True

def test_bare_json_decode_error_is_not_a_network_retry() -> None:
    import json

    try:
        json.loads("{not-json")
    except json.JSONDecodeError as exc:
        assert is_retryable_llm_error(exc) is False


@pytest.mark.asyncio
async def test_system_compatibility_error_is_not_retried_by_merging() -> None:
    """旧 replace→merge 兼容回退已删除：兼容错误直接抛给上层，不再偷偷改写历史。"""
    calls: list[dict[str, object]] = []

    async def create(**kwargs):
        calls.append(dict(kwargs))
        raise RuntimeError("at most one system message is supported")

    caller = _caller(create)
    with pytest.raises(RuntimeError, match="at most one system"):
        await caller.create_chat_completion_with_retry(
            {
                "model": "test",
                "messages": [
                    {"role": "system", "content": "S1"},
                    {"role": "user", "content": "hi"},
                    {"role": "system", "content": "S2"},
                ],
            }
        )
    assert len(calls) == 1
