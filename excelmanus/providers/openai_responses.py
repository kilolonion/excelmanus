"""OpenAI Responses API 适配器：将 Responses API 格式转换为 Chat Completions 格式。

OpenAI Responses API（/responses）与 Chat Completions API（/chat/completions）的关键差异：
  - 端点不同：POST /responses vs POST /chat/completions
  - 输入用 input（消息数组）+ instructions（系统指令）
  - 响应用 output（content block 数组）而非 choices[].message
  - 工具调用在 output 中以 function_call 类型的 block 出现
  - 工具结果通过 function_call_output 类型的 input item 传递
  - usage 字段名不同：input_tokens / output_tokens

本适配器使 AgentEngine 可以透明地使用 Responses API 端点。
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx

from excelmanus.logger import get_logger
from excelmanus.providers.stream_types import StreamDelta

logger = get_logger("openai_responses_provider")


# ── 响应数据结构 ─────────────────────────────────────────────


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



# ── 格式转换：Chat Completions → Responses API ──────────────────


def _chat_messages_to_responses_input(
    messages: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]]]:
    """将 Chat Completions messages 转换为 Responses API 的 instructions + input。

    返回 (instructions, input_items)。
    """
    instructions_parts: list[str] = []
    input_items: list[dict[str, Any]] = []

    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content")

        if role == "system":
            # system 消息 → instructions 参数传递
            if isinstance(content, str) and content.strip():
                instructions_parts.append(content)
            continue

        if role == "user":
            input_items.append({
                "type": "message",
                "role": "user",
                "content": content or "",
            })
            continue

        if role == "assistant":
            # assistant 消息中的文本内容和工具调用
            # 文本部分
            if content:
                input_items.append({
                    "type": "message",
                    "role": "assistant",
                    "content": content,
                })
            # 工具调用 → function_call items
            tool_calls = msg.get("tool_calls")
            if tool_calls:
                for tc in tool_calls:
                    func = tc.get("function", {}) if isinstance(tc, dict) else {}
                    name = func.get("name", "")
                    args_raw = func.get("arguments", "{}")
                    tc_id = tc.get("id", "") if isinstance(tc, dict) else ""
                    input_items.append({
                        "type": "function_call",
                        "id": tc_id or f"call_{uuid.uuid4().hex[:24]}",
                        "call_id": tc_id or f"call_{uuid.uuid4().hex[:24]}",
                        "name": name,
                        "arguments": args_raw if isinstance(args_raw, str) else json.dumps(args_raw, ensure_ascii=False),
                    })
            continue

        if role == "tool":
            # 工具结果 → function_call_output item
            tool_call_id = msg.get("tool_call_id", "")
            input_items.append({
                "type": "function_call_output",
                "call_id": tool_call_id,
                "output": content or "",
            })
            continue

        # 未知角色作为 user
        logger.warning("未知消息角色 %r，作为 user 消息传递", role)
        input_items.append({
            "type": "message",
            "role": "user",
            "content": str(content or ""),
        })

    instructions = "\n\n".join(instructions_parts)
    return instructions, input_items


def _chat_tools_to_responses_tools(
    tools: list[dict[str, Any]] | None,
) -> list[dict[str, Any]] | None:
    """将 Chat Completions tools 格式转换为 Responses API tools 格式。

    Responses API 的 function tool 格式：
    {"type": "function", "name": "...", "description": "...", "parameters": {...}}
    与 Chat Completions 的嵌套 {"type": "function", "function": {...}} 不同。
    """
    if not tools:
        return None
    responses_tools: list[dict[str, Any]] = []
    for tool in tools:
        if tool.get("type") != "function":
            continue
        func = tool.get("function", {})
        rt: dict[str, Any] = {
            "type": "function",
            "name": func.get("name", ""),
            "description": func.get("description", ""),
        }
        params = func.get("parameters")
        if params:
            rt["parameters"] = params
        responses_tools.append(rt)
    return responses_tools or None


def _map_chat_tool_choice_to_responses(tool_choice: Any) -> Any:
    """将 Chat Completions 风格 tool_choice 映射为 Responses API 格式。"""
    if tool_choice is None:
        return None
    if isinstance(tool_choice, str):
        normalized = tool_choice.strip().lower()
        if normalized in {"auto", "none", "required"}:
            return normalized
        return None
    if not isinstance(tool_choice, dict):
        return None

    # OpenAI Chat 强制指定工具格式：
    # {"type":"function","function":{"name":"ask_user"}}
    tc_type = str(tool_choice.get("type", "")).strip().lower()
    if tc_type == "function":
        function_value = tool_choice.get("function")
        if isinstance(function_value, dict):
            name = str(function_value.get("name", "")).strip()
            if name:
                return {"type": "function", "name": name}
        name = str(tool_choice.get("name", "")).strip()
        if name:
            return {"type": "function", "name": name}
        return None

    if tc_type in {"auto", "none", "required"}:
        return tc_type
    return None


# ── 格式转换：Responses API → Chat Completions ──────────────────


def _responses_output_to_openai(
    data: dict[str, Any], model: str,
) -> _ChatCompletion:
    """将 Responses API 响应转换为 OpenAI ChatCompletion 格式。

    Responses API 响应结构：
    {
        "id": "resp_...",
        "model": "...",
        "output": [
            {"type": "message", "content": [{"type": "output_text", "text": "..."}]},
            {"type": "function_call", "id": "...", "call_id": "...", "name": "...", "arguments": "..."},
        ],
        "usage": {"input_tokens": N, "output_tokens": N, "total_tokens": N}
    }
    """
    resp_id = data.get("id", f"resp_{uuid.uuid4().hex[:12]}")
    output_blocks = data.get("output", [])

    text_parts: list[str] = []
    tool_calls: list[_ToolCall] = []

    for block in output_blocks:
        block_type = block.get("type", "")

        if block_type == "message":
            # message 块包含 content 数组
            for content_item in block.get("content", []):
                item_type = content_item.get("type", "")
                if item_type in ("output_text", "text"):
                    text_parts.append(content_item.get("text", ""))

        elif block_type == "function_call":
            call_id = block.get("call_id") or block.get("id", f"call_{uuid.uuid4().hex[:24]}")
            tool_calls.append(_ToolCall(
                id=call_id,
                function=_Function(
                    name=block.get("name", ""),
                    arguments=block.get("arguments", "{}"),
                ),
            ))

    message = _Message(
        content="\n".join(text_parts) if text_parts else None,
        tool_calls=tool_calls if tool_calls else None,
    )

    # 确定 finish_reason
    status = data.get("status", "completed")
    if tool_calls:
        finish_reason = "tool_calls"
    elif status == "incomplete":
        finish_reason = "length"
    else:
        finish_reason = "stop"

    # usage 统计映射
    usage_data = data.get("usage", {})
    prompt_tokens = usage_data.get("input_tokens", 0)
    completion_tokens = usage_data.get("output_tokens", 0)
    total_tokens = usage_data.get("total_tokens", prompt_tokens + completion_tokens)

    return _ChatCompletion(
        id=resp_id,
        model=data.get("model", model),
        choices=[_Choice(
            message=message,
            finish_reason=finish_reason,
        )],
        usage=_Usage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
        ),
    )


# ── OpenAI Responses API 客户端 ──────────────────────────────


class _ResponsesChatCompletions:
    """模拟 openai.AsyncOpenAI().chat.completions 接口。"""

    def __init__(self, client: OpenAIResponsesClient) -> None:
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
        if stream:
            return await self._client._generate_stream(
                model=model, messages=messages, tools=tools,
                tool_choice=kwargs.get("tool_choice"),
            )
        return await self._client._generate(
            model=model,
            messages=messages,
            tools=tools,
            tool_choice=kwargs.get("tool_choice"),
        )


class _ResponsesChat:
    """模拟 openai.AsyncOpenAI().chat 接口。"""

    def __init__(self, client: OpenAIResponsesClient) -> None:
        self.completions = _ResponsesChatCompletions(client)


class OpenAIResponsesClient:
    """OpenAI Responses API 客户端，鸭子类型兼容 openai.AsyncOpenAI。

    将 Chat Completions 格式的调用转发到 /responses 端点，
    并将响应转换回 Chat Completions 格式。

    用法：
        client = OpenAIResponsesClient(api_key="...", base_url="https://api.openai.com/v1")
        response = await client.chat.completions.create(
            model="gpt-4o",
            messages=[...],
            tools=[...],
        )
    """

    def __init__(self, api_key: str, base_url: str) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._http = httpx.AsyncClient(timeout=300.0)
        self.chat = _ResponsesChat(self)

    async def _generate(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: Any = None,
        tool_choice: Any = None,
    ) -> _ChatCompletion:
        """执行 Responses API 请求。"""
        instructions, input_items = _chat_messages_to_responses_input(messages)

        body: dict[str, Any] = {
            "model": model,
            "input": input_items,
        }
        if instructions:
            body["instructions"] = instructions

        tools_list = tools if isinstance(tools, list) else None
        responses_tools = _chat_tools_to_responses_tools(tools_list)
        if responses_tools:
            body["tools"] = responses_tools

        mapped_tool_choice = _map_chat_tool_choice_to_responses(tool_choice)
        if mapped_tool_choice is not None:
            body["tool_choice"] = mapped_tool_choice

        url = f"{self._base_url}/responses"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }

        logger.debug(
            "Responses API 请求: model=%s, input=%d项, tools=%d个",
            model,
            len(input_items),
            len(responses_tools) if responses_tools else 0,
        )

        try:
            resp = await self._http.post(url, json=body, headers=headers)
        except httpx.HTTPError as exc:
            logger.error("Responses API HTTP 请求失败: %s", exc)
            raise RuntimeError(f"Responses API 请求失败: {exc}") from exc

        if resp.status_code != 200:
            error_text = resp.text[:500]
            logger.error("Responses API 返回错误 %d: %s", resp.status_code, error_text)
            raise RuntimeError(
                f"Responses API 错误 (HTTP {resp.status_code}): {error_text}"
            )

        try:
            data = resp.json()
        except (json.JSONDecodeError, ValueError) as exc:
            logger.error("Responses API 响应 JSON 解析失败: %s", exc)
            raise RuntimeError(f"Responses API 响应解析失败: {exc}") from exc

        result = _responses_output_to_openai(data, model)
        logger.debug(
            "Responses API 响应: tool_calls=%d, content_len=%d, tokens=%d",
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
    ) -> Any:
        """流式执行 Responses API 请求，返回异步生成器 yield StreamDelta。"""
        instructions, input_items = _chat_messages_to_responses_input(messages)
        body: dict[str, Any] = {
            "model": model,
            "input": input_items,
            "stream": True,
        }
        if instructions:
            body["instructions"] = instructions
        tools_list = tools if isinstance(tools, list) else None
        responses_tools = _chat_tools_to_responses_tools(tools_list)
        if responses_tools:
            body["tools"] = responses_tools
        mapped_tool_choice = _map_chat_tool_choice_to_responses(tool_choice)
        if mapped_tool_choice is not None:
            body["tool_choice"] = mapped_tool_choice

        url = f"{self._base_url}/responses"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }

        async def _stream_generator():
            current_tools: dict[int, dict] = {}

            async with self._http.stream("POST", url, json=body, headers=headers) as resp:
                if resp.status_code != 200:
                    error_text = await resp.aread()
                    raise RuntimeError(
                        f"Responses API 错误 (HTTP {resp.status_code}): {error_text[:500]}"
                    )

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

                    if event_type == "response.output_text.delta":
                        delta_text = event_data.get("delta", "")
                        if delta_text:
                            yield StreamDelta(content_delta=delta_text)

                    elif event_type == "response.output_item.added":
                        item = event_data.get("item", {})
                        output_index = event_data.get("output_index", 0)
                        if item.get("type") == "function_call":
                            current_tools[output_index] = {
                                "id": item.get("call_id", item.get("id", "")),
                                "name": item.get("name", ""),
                                "arguments": "",
                            }

                    elif event_type == "response.function_call_arguments.delta":
                        output_index = event_data.get("output_index", 0)
                        delta_args = event_data.get("delta", "")
                        if output_index not in current_tools:
                            current_tools[output_index] = {
                                "id": event_data.get("item_id", ""),
                                "name": "",
                                "arguments": "",
                            }
                        current_tools[output_index]["arguments"] += delta_args

                    elif event_type == "response.function_call_arguments.done":
                        output_index = event_data.get("output_index", 0)
                        if output_index in current_tools:
                            tool = current_tools[output_index]
                            yield StreamDelta(tool_calls_delta=[{
                                "index": output_index,
                                "id": tool["id"],
                                "name": tool["name"],
                                "arguments": tool["arguments"],
                            }])

                    elif event_type == "response.completed":
                        response_obj = event_data.get("response", {})
                        usage_data = response_obj.get("usage", {})
                        u = None
                        if usage_data:
                            u = _Usage(
                                prompt_tokens=usage_data.get("input_tokens", 0),
                                completion_tokens=usage_data.get("output_tokens", 0),
                                total_tokens=usage_data.get("input_tokens", 0)
                                + usage_data.get("output_tokens", 0),
                            )
                        output = response_obj.get("output", [])
                        has_tool = any(
                            item.get("type") == "function_call"
                            for item in output
                            if isinstance(item, dict)
                        )
                        yield StreamDelta(
                            finish_reason="tool_calls" if has_tool else "stop",
                            usage=u,
                        )

        return _stream_generator()

    async def close(self) -> None:
        """关闭 HTTP 客户端。"""
        await self._http.aclose()
