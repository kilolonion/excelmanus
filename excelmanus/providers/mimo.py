"""Xiaomi MiMo OpenAI-compatible client.

MiMo follows the Chat Completions message shape, but it does not accept a
number of OpenAI gateway extensions that ExcelManus may add for other
providers (prompt cache keys, service tiers, stream usage options, and
``reasoning_effort``).  Keep this normalization at the transport boundary so
the normal request compiler and the other OpenAI-compatible providers remain
unchanged.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse


_MIMO_HOSTS = ("xiaomimimo.com", "mimo.mi.com")
_MIMO_UNSUPPORTED_KEYS = frozenset(
    {
        "prompt_cache_key",
        "service_tier",
        "stream_options",
        "parallel_tool_calls",
        "reasoning_effort",
    }
)


def is_mimo_base_url(base_url: str) -> bool:
    """Return whether *base_url* targets Xiaomi MiMo's API host."""
    try:
        host = (urlparse(base_url).hostname or "").lower().rstrip(".")
    except Exception:
        return False
    return any(host == suffix or host.endswith("." + suffix) for suffix in _MIMO_HOSTS)


def _history_lacks_tool_reasoning(messages: Any) -> bool:
    """Whether an assistant tool-call message cannot satisfy MiMo replay rules."""
    if not isinstance(messages, list):
        return False
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        if not message.get("tool_calls"):
            continue
        if not any(
            str(message.get(key) or "").strip()
            for key in ("reasoning_content", "thinking", "reasoning")
        ):
            return True
    return False


def sanitize_mimo_request(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Remove gateway-only fields and map the legacy token limit spelling.

    The returned mapping is a detached shallow copy; nested ``extra_body`` is
    copied before editing so callers never observe transport-side mutation.
    """
    cleaned = dict(kwargs)
    for key in _MIMO_UNSUPPORTED_KEYS:
        cleaned.pop(key, None)

    # MiMo documents max_completion_tokens.  Probe/legacy callers may still
    # use the OpenAI SDK's older max_tokens spelling.
    max_tokens = cleaned.pop("max_tokens", None)
    if max_tokens is not None and "max_completion_tokens" not in cleaned:
        cleaned["max_completion_tokens"] = max_tokens

    extra_body = cleaned.get("extra_body")
    if isinstance(extra_body, dict):
        nested = dict(extra_body)
        for key in _MIMO_UNSUPPORTED_KEYS:
            nested.pop(key, None)
        nested_max_tokens = nested.pop("max_tokens", None)
        if nested_max_tokens is not None and "max_completion_tokens" not in cleaned:
            cleaned["max_completion_tokens"] = nested_max_tokens
        thinking = nested.get("thinking")
        if (
            isinstance(thinking, dict)
            and thinking.get("type") == "enabled"
            and _history_lacks_tool_reasoning(cleaned.get("messages"))
        ):
            # Do not invent a chain of thought for old persisted histories.
            # MiMo explicitly rejects those histories while thinking is on.
            raise ValueError("MiMo 思考模式要求工具调用历史包含 reasoning_content；请新建会话或显式关闭思考。")
        if nested:
            cleaned["extra_body"] = nested
        else:
            cleaned.pop("extra_body", None)
    return cleaned


class _MimoChatCompletions:
    def __init__(self, client: "MimoClient") -> None:
        self._client = client

    async def create(self, **kwargs: Any) -> Any:
        return await self._client._inner.chat.completions.create(
            **sanitize_mimo_request(kwargs)
        )


class _MimoChat:
    def __init__(self, client: "MimoClient") -> None:
        self.completions = _MimoChatCompletions(client)


class MimoClient:
    """Duck-typed ``openai.AsyncOpenAI`` client with MiMo request cleanup."""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        default_headers: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> None:
        import openai

        self._inner = openai.AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            default_headers=default_headers,
            **kwargs,
        )
        self._base_url = base_url
        self.chat = _MimoChat(self)

    @property
    def base_url(self) -> Any:
        return self._inner.base_url

    def __getattr__(self, name: str) -> Any:
        # Keep models/close and future SDK helpers available to capability
        # probes and lifecycle code without exposing the raw chat endpoint.
        return getattr(self._inner, name)


__all__ = ["MimoClient", "is_mimo_base_url", "sanitize_mimo_request"]
