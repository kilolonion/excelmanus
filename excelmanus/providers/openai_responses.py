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
from excelmanus.providers.stream_types import (
    InlineThinkingStateMachine,
    StreamDelta,
    extract_inline_thinking,
)

logger = get_logger("openai_responses_provider")


# ── 异常类型 ──────────────────────────────────────────────────


class ResponsesAPIError(Exception):
    """Responses API 请求失败。

    携带 status_code 属性，使 retry / classify_failure 管线
    能像处理 openai.APIStatusError 一样识别 429 / 5xx / 401 等状态码。
    """

    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        super().__init__(message)


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
    thinking: str | None = None
    reasoning: str | None = None
    reasoning_content: str | None = None


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



# ── ID 格式转换 ──────────────────────────────────────────────────


def _ensure_fc_id(call_id: str) -> str:
    """确保 function_call id 以 'fc_' 开头（Responses API 要求）。

    Chat Completions 返回 'call_xxx' 格式，Responses API 要求 'fc_xxx'。
    """
    if not call_id:
        return f"fc_{uuid.uuid4().hex[:24]}"
    if call_id.startswith("fc_"):
        return call_id
    if call_id.startswith("call_"):
        return "fc_" + call_id[5:]
    return "fc_" + call_id


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
            # 多模态内容：将 Chat Completions 格式转换为 Responses API 格式
            if isinstance(content, list):
                converted_parts: list[dict[str, Any]] = []
                for part in content:
                    if not isinstance(part, dict):
                        continue
                    part_type = part.get("type", "")
                    if part_type == "text":
                        converted_parts.append({"type": "input_text", "text": part.get("text", "")})
                    elif part_type == "image_url":
                        img_info = part.get("image_url", {})
                        url = img_info.get("url", "") if isinstance(img_info, dict) else str(img_info)
                        detail = img_info.get("detail", "auto") if isinstance(img_info, dict) else "auto"
                        converted_parts.append({"type": "input_image", "image_url": url, "detail": detail})
                    else:
                        converted_parts.append(part)
                input_items.append({
                    "type": "message",
                    "role": "user",
                    "content": converted_parts if converted_parts else "",
                })
            else:
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
                    fc_id = _ensure_fc_id(tc_id)
                    input_items.append({
                        "type": "function_call",
                        "id": fc_id,
                        "call_id": fc_id,
                        "name": name,
                        "arguments": args_raw if isinstance(args_raw, str) else json.dumps(args_raw, ensure_ascii=False),
                    })
            continue

        if role == "tool":
            # 工具结果 → function_call_output item
            tool_call_id = msg.get("tool_call_id", "")
            input_items.append({
                "type": "function_call_output",
                "call_id": _ensure_fc_id(tool_call_id),
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


def _apply_chat_kwargs_to_responses_body(body: dict[str, Any], kwargs: dict[str, Any]) -> None:
    """将 Chat Completions kwargs 透传/映射到 Responses API 请求体。"""
    extra_body = kwargs.get("extra_body")
    if isinstance(extra_body, dict):
        body.update(extra_body)

    reasoning_effort = kwargs.get("reasoning_effort")
    if isinstance(reasoning_effort, str) and reasoning_effort.strip():
        reasoning_payload = body.get("reasoning")
        if not isinstance(reasoning_payload, dict):
            reasoning_payload = {}
        # 若 extra_body 已显式设置 reasoning.effort，则优先保留用户覆盖值
        reasoning_payload.setdefault("effort", reasoning_effort.strip().lower())
        # OpenAI Responses 仅提供思考摘要而非原始 CoT。
        # 使用 "detailed" 以尽量获取更丰富的推理摘要内容，
        # "auto" 在部分模型（如 codex）上仅返回一行标题。
        reasoning_payload.setdefault("summary", "detailed")
        body["reasoning"] = reasoning_payload

    max_tokens = kwargs.get("max_tokens")
    if isinstance(max_tokens, int) and max_tokens > 0 and "max_output_tokens" not in body:
        body["max_output_tokens"] = max_tokens


def _collect_reasoning_texts_from_summary(summary: Any) -> list[str]:
    """从 reasoning.summary 字段提取文本。"""
    texts: list[str] = []
    if isinstance(summary, str):
        if summary.strip():
            texts.append(summary)
        return texts
    if not isinstance(summary, list):
        return texts
    for entry in summary:
        if isinstance(entry, str):
            if entry.strip():
                texts.append(entry)
            continue
        if not isinstance(entry, dict):
            continue
        text = entry.get("text")
        if isinstance(text, str) and text.strip():
            texts.append(text)
            continue
        summary_text = entry.get("summary_text")
        if isinstance(summary_text, str) and summary_text.strip():
            texts.append(summary_text)
    return texts


def _collect_reasoning_texts_from_output_item(item: Any) -> list[str]:
    """从 Responses output item（reasoning block）提取思考摘要文本。"""
    if not isinstance(item, dict):
        return []
    if item.get("type") != "reasoning":
        return []

    texts: list[str] = []
    texts.extend(_collect_reasoning_texts_from_summary(item.get("summary")))

    text = item.get("text")
    if isinstance(text, str) and text.strip():
        texts.append(text)

    content = item.get("content")
    if isinstance(content, str) and content.strip():
        texts.append(content)
    elif isinstance(content, list):
        for part in content:
            if isinstance(part, str):
                if part.strip():
                    texts.append(part)
                continue
            if not isinstance(part, dict):
                continue
            p_text = part.get("text")
            if isinstance(p_text, str) and p_text.strip():
                texts.append(p_text)
                continue
            p_summary = part.get("summary_text")
            if isinstance(p_summary, str) and p_summary.strip():
                texts.append(p_summary)

    return texts


def _extract_reasoning_delta_from_event(event_type: str, event_data: dict[str, Any]) -> str:
    """从 Responses 流事件中提取 reasoning delta（仅增量）。

    只处理 ``response.reasoning_summary_text.delta`` 事件——
    ``.done`` / ``.part.added`` / ``.part.done`` 事件包含的是已流式发送过的
    完整累积文本，若一并提取会导致 reasoning 内容重复 2~3 倍。
    """
    if event_type == "response.reasoning_summary_text.delta":
        text = event_data.get("delta") or ""
        return text if isinstance(text, str) else ""

    return ""


def _join_reasoning_parts(parts: list[str], inline_thinking: str) -> str | None:
    """合并 reasoning 摘要与内联 thinking，去重并保留顺序。"""
    ordered: list[str] = []
    seen: set[str] = set()

    for raw in [*parts, inline_thinking]:
        text = raw.strip() if isinstance(raw, str) else ""
        if not text or text in seen:
            continue
        seen.add(text)
        ordered.append(text)

    return "\n".join(ordered) if ordered else None


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
    reasoning_parts: list[str] = []
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
        elif block_type == "reasoning":
            reasoning_parts.extend(_collect_reasoning_texts_from_output_item(block))

    # 合并 text，然后检测非标准 <thinking> 内联标签
    raw_text = "\n".join(text_parts) if text_parts else ""
    inline_thinking, clean_text = extract_inline_thinking(raw_text)
    thinking_joined = _join_reasoning_parts(reasoning_parts, inline_thinking)

    message = _Message(
        content=clean_text if clean_text else None,
        tool_calls=tool_calls if tool_calls else None,
        thinking=thinking_joined,
        reasoning=thinking_joined,
        reasoning_content=thinking_joined,
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

    usage = _Usage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
    )
    # 提取 Responses API 的缓存统计（input_tokens_details.cached_tokens）
    input_details = usage_data.get("input_tokens_details", {})
    if isinstance(input_details, dict):
        cached = input_details.get("cached_tokens", 0)
        if cached:
            usage.prompt_tokens_details = {"cached_tokens": cached}  # type: ignore[attr-defined]

    return _ChatCompletion(
        id=resp_id,
        model=data.get("model", model),
        choices=[_Choice(
            message=message,
            finish_reason=finish_reason,
        )],
        usage=usage,
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
                extra_kwargs=kwargs,
            )
        return await self._client._generate(
            model=model,
            messages=messages,
            tools=tools,
            tool_choice=kwargs.get("tool_choice"),
            extra_kwargs=kwargs,
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
            model="gpt-5",
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
        extra_kwargs: dict[str, Any] | None = None,
    ) -> _ChatCompletion:
        """执行 Responses API 请求。

        注意：backend-api 强制要求 stream=true，因此即使调用方请求非流式，
        内部也走流式请求并收集完整结果后返回。
        """
        instructions, input_items = _chat_messages_to_responses_input(messages)

        body: dict[str, Any] = {
            "model": model,
            "input": input_items,
            "stream": True,
            "store": False,
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
        _apply_chat_kwargs_to_responses_body(body, extra_kwargs or {})

        url = f"{self._base_url}/responses"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }

        logger.debug(
            "Responses API 请求 (collected stream): model=%s, input=%d项, tools=%d个",
            model,
            len(input_items),
            len(responses_tools) if responses_tools else 0,
        )

        # 通过流式请求收集完整响应（backend-api 不支持非流式）
        # 消费所有流事件，在 response.completed 时提取完整响应对象，
        # 然后通过 _responses_output_to_openai 统一转换为 ChatCompletion 格式。
        completed_response: dict[str, Any] | None = None

        async with self._http.stream("POST", url, json=body, headers=headers) as resp:
            if resp.status_code != 200:
                error_text = await resp.aread()
                raise ResponsesAPIError(
                    resp.status_code,
                    f"Responses API 错误 (HTTP {resp.status_code}): {error_text[:500]}",
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

                if event_data.get("type") == "response.completed":
                    completed_response = event_data.get("response", {})

        if completed_response is None:
            raise ResponsesAPIError(0, "Responses API 流未返回 response.completed 事件")

        result = _responses_output_to_openai(completed_response, model)
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
        extra_kwargs: dict[str, Any] | None = None,
    ) -> Any:
        """流式执行 Responses API 请求，返回异步生成器 yield StreamDelta。"""
        instructions, input_items = _chat_messages_to_responses_input(messages)
        body: dict[str, Any] = {
            "model": model,
            "input": input_items,
            "stream": True,
            "store": False,
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
        _apply_chat_kwargs_to_responses_body(body, extra_kwargs or {})

        url = f"{self._base_url}/responses"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }

        async def _stream_generator():
            current_tools: dict[int, dict] = {}
            _inline_sm = InlineThinkingStateMachine()

            async with self._http.stream("POST", url, json=body, headers=headers) as resp:
                if resp.status_code != 200:
                    error_text = await resp.aread()
                    raise ResponsesAPIError(
                        resp.status_code,
                        f"Responses API 错误 (HTTP {resp.status_code}): {error_text[:500]}",
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
                    reasoning_delta = _extract_reasoning_delta_from_event(event_type, event_data)
                    if reasoning_delta:
                        yield StreamDelta(thinking_delta=reasoning_delta)

                    if event_type == "response.output_text.delta":
                        delta_text = event_data.get("delta", "")
                        if delta_text:
                            for _d in _inline_sm.feed(delta_text):
                                yield _d

                    elif event_type == "response.output_item.added":
                        item = event_data.get("item", {})
                        output_index = event_data.get("output_index", 0)
                        if item.get("type") == "function_call":
                            current_tools[output_index] = {
                                "id": item.get("call_id", item.get("id", "")),
                                "name": item.get("name", ""),
                                "arguments": "",
                            }
                        # reasoning item 的文本通过 reasoning_summary_text.delta
                        # 事件逐增量流式发送，此处不再重复提取以避免内容重复。

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
                            # 提取缓存统计
                            _input_details = usage_data.get("input_tokens_details", {})
                            if isinstance(_input_details, dict):
                                _cached = _input_details.get("cached_tokens", 0)
                                if _cached:
                                    u.prompt_tokens_details = {"cached_tokens": _cached}  # type: ignore[attr-defined]
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
