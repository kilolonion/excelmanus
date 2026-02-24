"""Claude (Anthropic) 原生 API 适配器：提供与 openai.AsyncOpenAI 鸭子类型兼容的接口。

将 OpenAI Chat Completions 格式的请求转换为 Anthropic Messages API 格式，
并将 Claude 响应转换回 OpenAI 格式，使 AgentEngine 无需感知底层差异。

Claude API 关键差异：
  - 认证用 x-api-key header（非 Bearer token）
  - system 消息通过顶层 system 参数传递（非 messages 数组）
  - 响应用 content blocks（text/tool_use）而非 choices[].message
  - 工具定义用 input_schema（非 parameters）
  - 工具调用结果用 tool_result content block（非 tool role message）
  - 必须指定 max_tokens
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx

from excelmanus.logger import get_logger
from excelmanus.providers.stream_types import StreamDelta

logger = get_logger("claude_provider")

# 默认 max_tokens（Claude 要求必传）
_DEFAULT_MAX_TOKENS = 8192


# ── 响应数据结构（复用与 Gemini 适配器相同的模式） ─────────────────


@dataclass
class _Function:
    name: str
    arguments: str


@dataclass
class _ToolCall:
    id: str
    type: str = "function"
    function: _Function = field(default_factory=lambda: _Function(name="", arguments="{}"))


@dataclass
class _Message:
    role: str = "assistant"
    content: str | None = None
    tool_calls: list[_ToolCall] | None = None


@dataclass
class _Choice:
    index: int = 0
    message: _Message = field(default_factory=_Message)
    finish_reason: str = "stop"


@dataclass
class _Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class _ChatCompletion:
    id: str = ""
    object: str = "chat.completion"
    model: str = ""
    choices: list[_Choice] = field(default_factory=list)
    usage: _Usage = field(default_factory=_Usage)


    # 兼容旧引用：保留 _ChatCompletion._StreamDelta 名称。
    _StreamDelta = StreamDelta



# ── 格式转换：OpenAI → Claude ─────────────────────────────────


def _parse_data_uri(url: str) -> tuple[str, str]:
    """解析 data:mime;base64,data 格式的 URI，返回 (mime_type, base64_data)。"""
    if url.startswith("data:"):
        header, _, data = url.partition(",")
        mime = header.split(":")[1].split(";")[0] if ":" in header else "image/png"
        return mime, data
    return "image/png", url


def _openai_messages_to_claude(
    messages: list[dict[str, Any]],
) -> tuple[str | list[dict[str, Any]], list[dict[str, Any]]]:
    """将 OpenAI messages 转换为 Claude 的 system + messages。

    返回 (system, claude_messages)。
    system 可能是 str（无 cache_control）或 list[dict]（带 cache_control breakpoint）。
    """
    system_parts: list[str] = []
    claude_messages: list[dict[str, Any]] = []

    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content")

        if role == "system":
            if isinstance(content, str) and content.strip():
                system_parts.append(content)
            continue

        if role == "user":
            if isinstance(content, list):
                blocks: list[dict[str, Any]] = []
                for item in content:
                    if isinstance(item, dict):
                        if item.get("type") == "text":
                            blocks.append({"type": "text", "text": item.get("text", "")})
                        elif item.get("type") == "image_url":
                            img_info = item.get("image_url", {})
                            url = img_info.get("url", "")
                            mime, b64 = _parse_data_uri(url)
                            blocks.append({
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": mime,
                                    "data": b64,
                                },
                            })
                claude_messages.append({"role": "user", "content": blocks})
            else:
                claude_messages.append({
                    "role": "user",
                    "content": content or "",
                })
            continue

        if role == "assistant":
            blocks: list[dict[str, Any]] = []
            # 文本内容
            if content:
                blocks.append({"type": "text", "text": content})
            # 工具调用 → tool_use blocks
            tool_calls = msg.get("tool_calls")
            if tool_calls:
                for tc in tool_calls:
                    func = tc.get("function", {}) if isinstance(tc, dict) else {}
                    name = func.get("name", "")
                    args_raw = func.get("arguments", "{}")
                    try:
                        args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
                    except (json.JSONDecodeError, TypeError):
                        args = {}
                    tc_id = tc.get("id", "") if isinstance(tc, dict) else ""
                    blocks.append({
                        "type": "tool_use",
                        "id": tc_id or f"toolu_{uuid.uuid4().hex[:24]}",
                        "name": name,
                        "input": args,
                    })
            if blocks:
                claude_messages.append({"role": "assistant", "content": blocks})
            continue

        if role == "tool":
            # OpenAI 工具结果 → Claude tool_result 内容块
            tool_call_id = msg.get("tool_call_id", "")
            result_content = content or ""
            claude_messages.append({
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": tool_call_id,
                    "content": result_content,
                }],
            })
            continue

        # 未知角色作为 user
        logger.warning("未知消息角色 %r，作为 user 消息传递", role)
        claude_messages.append({
            "role": "user",
            "content": str(content or ""),
        })

    # Claude 要求 user/assistant 严格交替，合并连续同角色消息
    claude_messages = _merge_consecutive_claude_messages(claude_messages)

    # Prompt Caching：将 system 构建为带 cache_control breakpoint 的结构化格式。
    # 在最后一个 system block 上设置 cache_control，使整个 system 前缀可被缓存。
    if len(system_parts) == 1:
        system: str | list[dict[str, Any]] = [
            {
                "type": "text",
                "text": system_parts[0],
                "cache_control": {"type": "ephemeral"},
            }
        ]
    elif system_parts:
        # 多个 system block：仅在最后一个上设置 cache_control breakpoint
        system_blocks: list[dict[str, Any]] = []
        for i, part in enumerate(system_parts):
            block: dict[str, Any] = {"type": "text", "text": part}
            if i == len(system_parts) - 1:
                block["cache_control"] = {"type": "ephemeral"}
            system_blocks.append(block)
        system = system_blocks
    else:
        system = ""

    return system, claude_messages


def _merge_consecutive_claude_messages(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """合并连续同角色消息（Claude 要求严格交替）。"""
    if not messages:
        return messages
    merged: list[dict[str, Any]] = [messages[0]]
    for msg in messages[1:]:
        if msg["role"] == merged[-1]["role"]:
            # 合并 content
            prev_content = merged[-1]["content"]
            curr_content = msg["content"]
            # 统一为 list 形式
            prev_list = prev_content if isinstance(prev_content, list) else [{"type": "text", "text": str(prev_content)}]
            curr_list = curr_content if isinstance(curr_content, list) else [{"type": "text", "text": str(curr_content)}]
            merged[-1]["content"] = prev_list + curr_list
        else:
            merged.append(msg)
    return merged


def _openai_tools_to_claude(
    tools: list[dict[str, Any]] | None,
) -> list[dict[str, Any]] | None:
    """将 OpenAI tools 格式转换为 Claude tools 格式。"""
    if not tools:
        return None
    claude_tools: list[dict[str, Any]] = []
    for tool in tools:
        if tool.get("type") != "function":
            continue
        func = tool.get("function", {})
        ct: dict[str, Any] = {
            "name": func.get("name", ""),
            "description": func.get("description", ""),
        }
        params = func.get("parameters")
        if params:
            ct["input_schema"] = params
        else:
            ct["input_schema"] = {"type": "object", "properties": {}}
        claude_tools.append(ct)
    return claude_tools or None


def _map_openai_tool_choice_to_claude(tool_choice: Any) -> dict[str, Any] | None:
    """将 OpenAI tool_choice 映射为 Claude Messages API tool_choice。"""
    if tool_choice is None:
        return None

    if isinstance(tool_choice, str):
        normalized = tool_choice.strip().lower()
        if normalized == "auto":
            return {"type": "auto"}
        if normalized == "required":
            return {"type": "any"}
        if normalized == "none":
            # Claude 不提供 none 选项，降级为 auto，由模型自行决定是否调用工具。
            return {"type": "auto"}
        return None

    if not isinstance(tool_choice, dict):
        return None

    tc_type = str(tool_choice.get("type", "")).strip().lower()
    if tc_type in {"auto", "none", "required"}:
        return _map_openai_tool_choice_to_claude(tc_type)

    name = ""
    if tc_type == "function":
        function_value = tool_choice.get("function")
        if isinstance(function_value, dict):
            name = str(function_value.get("name", "")).strip()
        if not name:
            name = str(tool_choice.get("name", "")).strip()
    elif tc_type == "tool":
        name = str(tool_choice.get("name", "")).strip()

    if name:
        return {"type": "tool", "name": name}
    return None


# ── 格式转换：Claude → OpenAI ─────────────────────────────────


def _claude_response_to_openai(
    data: dict[str, Any], model: str,
) -> _ChatCompletion:
    """将 Claude Messages API 响应转换为 OpenAI ChatCompletion 格式。"""
    content_blocks = data.get("content", [])
    msg_id = data.get("id", f"msg_{uuid.uuid4().hex[:12]}")

    text_parts: list[str] = []
    tool_calls: list[_ToolCall] = []

    for block in content_blocks:
        block_type = block.get("type", "")
        if block_type == "text":
            text_parts.append(block.get("text", ""))
        elif block_type == "tool_use":
            tool_calls.append(_ToolCall(
                id=block.get("id", f"toolu_{uuid.uuid4().hex[:24]}"),
                function=_Function(
                    name=block.get("name", ""),
                    arguments=json.dumps(block.get("input", {}), ensure_ascii=False),
                ),
            ))
        elif block_type == "thinking":
            # Claude 扩展思考 — 暂存但不影响主流程
            pass

    message = _Message(
        content="\n".join(text_parts) if text_parts else None,
        tool_calls=tool_calls if tool_calls else None,
    )

    # stop_reason 映射为 OpenAI finish_reason
    stop_reason = data.get("stop_reason", "end_turn")
    finish_reason_map = {
        "end_turn": "stop",
        "tool_use": "tool_calls",
        "max_tokens": "length",
        "stop_sequence": "stop",
    }
    finish_reason = finish_reason_map.get(stop_reason, "stop")

    # usage 统计（含 prompt caching 字段）
    usage_data = data.get("usage", {})
    prompt_tokens = usage_data.get("input_tokens", 0)
    completion_tokens = usage_data.get("output_tokens", 0)
    cache_creation_tokens = usage_data.get("cache_creation_input_tokens", 0)
    cache_read_tokens = usage_data.get("cache_read_input_tokens", 0)

    usage = _Usage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
    )
    # 附加 cache 统计到 usage 对象（供上层提取）
    usage.cache_creation_input_tokens = cache_creation_tokens  # type: ignore[attr-defined]
    usage.cache_read_input_tokens = cache_read_tokens  # type: ignore[attr-defined]

    return _ChatCompletion(
        id=msg_id,
        model=data.get("model", model),
        choices=[_Choice(
            message=message,
            finish_reason=finish_reason,
        )],
        usage=usage,
    )


# ── Claude 客户端 ─────────────────────────────────────────────


class _ClaudeChatCompletions:
    """模拟 openai.AsyncOpenAI().chat.completions 接口。"""

    def __init__(self, client: ClaudeClient) -> None:
        self._client = client

    async def create(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: Any = None,
        stream: bool = False,
        **kwargs: Any,
    ) -> _ChatCompletion | Any:
        thinking_enabled = kwargs.pop("_thinking_enabled", False)
        thinking_budget = kwargs.pop("_thinking_budget", 0)
        if stream:
            return await self._client._generate_stream(
                model=model, messages=messages, tools=tools,
                tool_choice=kwargs.get("tool_choice"),
                thinking_enabled=thinking_enabled,
                thinking_budget=thinking_budget,
            )
        return await self._client._generate(
            model=model,
            messages=messages,
            tools=tools,
            tool_choice=kwargs.get("tool_choice"),
            thinking_enabled=thinking_enabled,
            thinking_budget=thinking_budget,
        )


class _ClaudeChat:
    """模拟 openai.AsyncOpenAI().chat 接口。"""

    def __init__(self, client: ClaudeClient) -> None:
        self.completions = _ClaudeChatCompletions(client)


class ClaudeClient:
    """Anthropic Claude 原生 API 客户端，鸭子类型兼容 openai.AsyncOpenAI。

    用法：
        client = ClaudeClient(api_key="...", base_url="https://api.anthropic.com")
        response = await client.chat.completions.create(
            model="claude-sonnet-4-5-20250929",
            messages=[...],
            tools=[...],
        )
    """

    def __init__(self, api_key: str, base_url: str) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._http = httpx.AsyncClient(timeout=300.0)
        self.chat = _ClaudeChat(self)

    async def _generate(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: Any = None,
        tool_choice: Any = None,
        thinking_enabled: bool = False,
        thinking_budget: int = 0,
    ) -> _ChatCompletion:
        """执行 Claude Messages API 请求。"""
        system, claude_messages = _openai_messages_to_claude(messages)

        body: dict[str, Any] = {
            "model": model,
            "messages": claude_messages,
            "max_tokens": _DEFAULT_MAX_TOKENS,
        }
        if thinking_enabled and thinking_budget > 0:
            body["thinking"] = {"type": "enabled", "budget_tokens": thinking_budget}
            body["max_tokens"] = max(_DEFAULT_MAX_TOKENS, thinking_budget + 4096)
        if system:
            body["system"] = system

        tools_list = tools if isinstance(tools, list) else None
        claude_tools = _openai_tools_to_claude(tools_list)
        if claude_tools:
            body["tools"] = claude_tools

        mapped_tool_choice = _map_openai_tool_choice_to_claude(tool_choice)
        if mapped_tool_choice is not None:
            body["tool_choice"] = mapped_tool_choice

        url = f"{self._base_url}/v1/messages"
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self._api_key,
            "anthropic-version": "2023-06-01",
            "anthropic-beta": "prompt-caching-2024-07-31",
        }

        logger.debug(
            "Claude 请求: model=%s, messages=%d条, tools=%d个",
            model,
            len(claude_messages),
            len(claude_tools) if claude_tools else 0,
        )

        try:
            resp = await self._http.post(url, json=body, headers=headers)
        except httpx.HTTPError as exc:
            logger.error("Claude HTTP 请求失败: %s", exc)
            raise RuntimeError(f"Claude API 请求失败: {exc}") from exc

        if resp.status_code != 200:
            error_text = resp.text[:500]
            logger.error("Claude API 返回错误 %d: %s", resp.status_code, error_text)
            raise RuntimeError(
                f"Claude API 错误 (HTTP {resp.status_code}): {error_text}"
            )

        try:
            data = resp.json()
        except (json.JSONDecodeError, ValueError) as exc:
            logger.error("Claude 响应 JSON 解析失败: %s", exc)
            raise RuntimeError(f"Claude 响应解析失败: {exc}") from exc

        result = _claude_response_to_openai(data, model)
        logger.debug(
            "Claude 响应: tool_calls=%d, content_len=%d, tokens=%d",
            len(result.choices[0].message.tool_calls or []),
            len(result.choices[0].message.content or ""),
            result.usage.total_tokens,
        )
        return result

    async def _generate_stream(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: Any = None,
        tool_choice: Any = None,
        thinking_enabled: bool = False,
        thinking_budget: int = 0,
    ) -> Any:
        """流式执行 Claude Messages API 请求，返回异步生成器 yield StreamDelta。"""
        system, claude_messages = _openai_messages_to_claude(messages)
        body: dict[str, Any] = {
            "model": model,
            "messages": claude_messages,
            "max_tokens": _DEFAULT_MAX_TOKENS,
            "stream": True,
        }
        if thinking_enabled and thinking_budget > 0:
            body["thinking"] = {"type": "enabled", "budget_tokens": thinking_budget}
            body["max_tokens"] = max(_DEFAULT_MAX_TOKENS, thinking_budget + 4096)
        if system:
            body["system"] = system
        tools_list = tools if isinstance(tools, list) else None
        claude_tools = _openai_tools_to_claude(tools_list)
        if claude_tools:
            body["tools"] = claude_tools
        mapped_tool_choice = _map_openai_tool_choice_to_claude(tool_choice)
        if mapped_tool_choice is not None:
            body["tool_choice"] = mapped_tool_choice

        url = f"{self._base_url}/v1/messages"
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self._api_key,
            "anthropic-version": "2023-06-01",
            "anthropic-beta": "prompt-caching-2024-07-31",
        }

        async def _stream_generator():
            async with self._http.stream("POST", url, json=body, headers=headers) as resp:
                if resp.status_code != 200:
                    error_text = await resp.aread()
                    raise RuntimeError(
                        f"Claude API 错误 (HTTP {resp.status_code}): {error_text[:500]}"
                    )

                current_tool_id: str | None = None
                current_tool_name: str | None = None
                current_tool_json: str = ""
                tool_call_index: int = -1
                # Prompt caching 统计（从 message_start 事件提取）
                _cache_creation_tokens: int = 0
                _cache_read_tokens: int = 0

                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    raw = line[6:]
                    if raw.strip() == "[DONE]":
                        break
                    try:
                        event_data = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    event_type = event_data.get("type", "")

                    # message_start 携带初始 usage（含 prompt caching 统计）
                    if event_type == "message_start":
                        msg_obj = event_data.get("message", {})
                        start_usage = msg_obj.get("usage", {})
                        _cache_creation_tokens = start_usage.get(
                            "cache_creation_input_tokens", 0
                        ) or 0
                        _cache_read_tokens = start_usage.get(
                            "cache_read_input_tokens", 0
                        ) or 0
                        if _cache_read_tokens > 0:
                            logger.info(
                                "Anthropic prompt cache HIT: "
                                "cache_read=%d, cache_creation=%d",
                                _cache_read_tokens,
                                _cache_creation_tokens,
                            )
                        elif _cache_creation_tokens > 0:
                            logger.info(
                                "Anthropic prompt cache MISS (creating): "
                                "cache_creation=%d",
                                _cache_creation_tokens,
                            )
                        continue

                    if event_type == "content_block_start":
                        block = event_data.get("content_block", {})
                        if block.get("type") == "tool_use":
                            tool_call_index += 1
                            current_tool_id = block.get("id", str(uuid.uuid4()))
                            current_tool_name = block.get("name", "")
                            current_tool_json = ""

                    elif event_type == "content_block_delta":
                        delta = event_data.get("delta", {})
                        delta_type = delta.get("type", "")
                        if delta_type == "text_delta":
                            yield StreamDelta(content_delta=delta.get("text", ""))
                        elif delta_type == "thinking_delta":
                            yield StreamDelta(thinking_delta=delta.get("thinking", ""))
                        elif delta_type == "input_json_delta":
                            current_tool_json += delta.get("partial_json", "")

                    elif event_type == "content_block_stop":
                        if current_tool_id and current_tool_name:
                            yield StreamDelta(tool_calls_delta=[{
                                "index": tool_call_index,
                                "id": current_tool_id,
                                "name": current_tool_name,
                                "arguments": current_tool_json,
                            }])
                            current_tool_id = None
                            current_tool_name = None
                            current_tool_json = ""

                    elif event_type == "message_delta":
                        delta = event_data.get("delta", {})
                        stop_reason = delta.get("stop_reason")
                        usage_data = event_data.get("usage", {})
                        finish = None
                        if stop_reason == "end_turn":
                            finish = "stop"
                        elif stop_reason == "tool_use":
                            finish = "tool_calls"
                        u = None
                        if usage_data:
                            u = _Usage(
                                prompt_tokens=usage_data.get("input_tokens", 0),
                                completion_tokens=usage_data.get("output_tokens", 0),
                                total_tokens=usage_data.get("input_tokens", 0)
                                + usage_data.get("output_tokens", 0),
                            )
                            # 附加 prompt caching 统计
                            u.cache_creation_input_tokens = _cache_creation_tokens  # type: ignore[attr-defined]
                            u.cache_read_input_tokens = _cache_read_tokens  # type: ignore[attr-defined]
                        yield StreamDelta(finish_reason=finish, usage=u)

        return _stream_generator()

    async def close(self) -> None:
        """关闭 HTTP 客户端。"""
        await self._http.aclose()

