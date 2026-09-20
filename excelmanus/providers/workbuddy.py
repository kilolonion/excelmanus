"""WorkBuddy / CodeBuddy Chat Completions 适配器。

上游（copilot.tencent.com / workbuddy.ai 的 /v2/chat/completions）是
OpenAI Chat Completions 兼容接口，但存在方言差异，依据上游文档
workbuddy 插件做如下归一化：

- 上游按流式工作：始终用 stream=true 请求；调用方要非流式时在本地聚合。
- ``tool_choice`` 只接受字符串/单 function 对象；``"none"`` 需连同
  tools 一起剔除（上游不认识该值）。
- Global realm（workbuddy.ai）要求消息列表含 system 消息，缺失时补一条。
- ``reasoning_content`` 流式字段原样透传；聚合时合并进 message。
"""

from __future__ import annotations

import logging
from typing import Any, AsyncIterator
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_WORKBUDDY_HOSTS = ("copilot.tencent.com", "workbuddy.ai", "www.workbuddy.ai")

_GLOBAL_SYSTEM_MESSAGE = {"role": "system", "content": "You are a helpful assistant."}


def is_workbuddy_base_url(base_url: str) -> bool:
    """判断 base_url 是否指向 WorkBuddy/CodeBuddy 上游。"""
    try:
        host = (urlparse(base_url).hostname or "").lower()
    except Exception:
        return False
    return any(host == h or host.endswith("." + h) for h in _WORKBUDDY_HOSTS)


def _normalize_tool_choice(kwargs: dict[str, Any]) -> None:
    """将 tool_choice 归一化为上游支持的形态。"""
    if "tool_choice" not in kwargs:
        return
    tc = kwargs.get("tool_choice")
    if isinstance(tc, str):
        if tc == "none":
            # 上游不支持 none：剔除 tool_choice 并移除 tools/functions
            kwargs.pop("tool_choice", None)
            kwargs.pop("tools", None)
            kwargs.pop("functions", None)
        elif tc in ("auto", "required"):
            pass
        else:
            kwargs["tool_choice"] = "auto"
        return
    if isinstance(tc, dict):
        tc_type = str(tc.get("type") or "")
        if tc_type == "function":
            fn = tc.get("function") or {}
            name = str(fn.get("name") or "")
            kwargs["tool_choice"] = (
                {"type": "function", "function": {"name": name}} if name else "auto"
            )
        elif tc_type in ("auto", "required"):
            kwargs["tool_choice"] = tc_type
        else:
            kwargs["tool_choice"] = "auto"
        return
    kwargs.pop("tool_choice", None)


def _ensure_system_message(kwargs: dict[str, Any], is_global: bool) -> None:
    """Global realm 要求至少一条 system 消息。"""
    if not is_global:
        return
    messages = kwargs.get("messages")
    if not isinstance(messages, list):
        return
    has_system = any(
        isinstance(m, dict) and m.get("role") == "system" for m in messages
    )
    if not has_system:
        kwargs["messages"] = [dict(_GLOBAL_SYSTEM_MESSAGE), *messages]


async def _fold_stream(stream: AsyncIterator[Any]) -> Any:
    """把 ChatCompletionChunk 流聚合为 ChatCompletion。"""
    from openai.types.chat import ChatCompletion

    meta: dict[str, Any] = {}
    usage: Any = None
    choices: dict[int, dict[str, Any]] = {}

    async for chunk in stream:
        if getattr(chunk, "id", None):
            meta.setdefault("id", chunk.id)
        if getattr(chunk, "created", None):
            meta.setdefault("created", chunk.created)
        if getattr(chunk, "model", None):
            meta.setdefault("model", chunk.model)
        if getattr(chunk, "usage", None):
            usage = chunk.usage
        for ch in getattr(chunk, "choices", None) or []:
            idx = getattr(ch, "index", 0) or 0
            slot = choices.setdefault(idx, {
                "role": "assistant",
                "content_parts": [],
                "reasoning_parts": [],
                "tool_calls": {},
                "finish_reason": None,
            })
            delta = getattr(ch, "delta", None)
            if delta is None:
                continue
            if getattr(delta, "role", None):
                slot["role"] = delta.role
            if getattr(delta, "content", None):
                slot["content_parts"].append(delta.content)
            reasoning = getattr(delta, "reasoning_content", None)
            if reasoning:
                slot["reasoning_parts"].append(reasoning)
            for tc in getattr(delta, "tool_calls", None) or []:
                tc_idx = getattr(tc, "index", 0) or 0
                acc = slot["tool_calls"].setdefault(tc_idx, {
                    "id": "",
                    "type": "function",
                    "name_parts": [],
                    "arg_parts": [],
                })
                if getattr(tc, "id", None):
                    acc["id"] += tc.id
                if getattr(tc, "type", None):
                    acc["type"] = tc.type
                fn = getattr(tc, "function", None)
                if fn is not None:
                    if getattr(fn, "name", None):
                        acc["name_parts"].append(fn.name)
                    if getattr(fn, "arguments", None):
                        acc["arg_parts"].append(fn.arguments)
            if getattr(ch, "finish_reason", None):
                slot["finish_reason"] = ch.finish_reason

    out_choices = []
    for idx in sorted(choices):
        slot = choices[idx]
        tool_calls = None
        if slot["tool_calls"]:
            tool_calls = [
                {
                    "id": acc["id"] or f"call_{idx}_{tc_idx}",
                    "type": acc["type"],
                    "function": {
                        "name": "".join(acc["name_parts"]),
                        "arguments": "".join(acc["arg_parts"]),
                    },
                }
                for tc_idx, acc in sorted(slot["tool_calls"].items())
            ]
        message: dict[str, Any] = {"role": slot["role"]}
        content = "".join(slot["content_parts"])
        message["content"] = content if content else None
        if tool_calls:
            message["tool_calls"] = tool_calls
        reasoning = "".join(slot["reasoning_parts"])
        if reasoning:
            message["reasoning_content"] = reasoning
        out_choices.append({
            "index": idx,
            "finish_reason": slot["finish_reason"] or "stop",
            "message": message,
        })

    payload: dict[str, Any] = {
        "id": meta.get("id") or "",
        "object": "chat.completion",
        "created": meta.get("created") or 0,
        "model": meta.get("model") or "",
        "choices": out_choices,
    }
    if usage is not None:
        payload["usage"] = (
            usage.model_dump() if hasattr(usage, "model_dump") else usage
        )
    return ChatCompletion.model_validate(payload)


class _WorkBuddyChatCompletions:
    def __init__(self, client: "WorkBuddyClient") -> None:
        self._client = client

    async def create(self, **kwargs: Any) -> Any:
        caller_stream = bool(kwargs.get("stream", False))
        _normalize_tool_choice(kwargs)
        _ensure_system_message(kwargs, self._client._is_global_realm)
        # 上游不认识的字段直接剔除，避免 400
        kwargs.pop("stream_options", None)
        kwargs["stream"] = True
        inner = await self._client._inner.chat.completions.create(**kwargs)
        if caller_stream:
            return inner
        return await _fold_stream(inner)


class _WorkBuddyChat:
    def __init__(self, client: "WorkBuddyClient") -> None:
        self.completions = _WorkBuddyChatCompletions(client)


class WorkBuddyClient:
    """WorkBuddy/CodeBuddy 上游客户端，鸭子类型兼容 openai.AsyncOpenAI。"""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        default_headers: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> None:
        import openai

        self._base_url = base_url
        try:
            host = (urlparse(base_url).hostname or "").lower()
        except Exception:
            host = ""
        self._is_global_realm = host.endswith("workbuddy.ai")
        self._inner = openai.AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            default_headers=default_headers,
            **kwargs,
        )
        self.chat = _WorkBuddyChat(self)


__all__ = ["WorkBuddyClient", "is_workbuddy_base_url"]
