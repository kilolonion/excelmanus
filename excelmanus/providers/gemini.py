"""Gemini 原生 API 适配器：提供与 openai.AsyncOpenAI 鸭子类型兼容的接口。

将 OpenAI Chat Completions 格式的请求转换为 Gemini generateContent 格式，
并将 Gemini 响应转换回 OpenAI 格式，使 AgentEngine 无需感知底层差异。

支持的 base_url 格式示例：
  - https://generativelanguage.googleapis.com/v1beta
  - https://right.codes/gemini/v1beta
  - https://right.codes/gemini/v1beta/models/gemini-2.5-flash:streamGenerateContent?alt=sse
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx

from excelmanus.logger import get_logger
from excelmanus.providers.stream_types import StreamDelta

logger = get_logger("gemini_provider")


# ── 响应数据结构（模拟 OpenAI SDK 对象） ─────────────────────────


@dataclass
class _Function:
    """模拟 openai.types.chat.ChatCompletionMessageToolCall.Function。"""
    name: str
    arguments: str  # JSON 字符串


@dataclass
class _ToolCall:
    """模拟 openai.types.chat.ChatCompletionMessageToolCall。"""
    id: str
    type: str = "function"
    function: _Function = field(default_factory=lambda: _Function(name="", arguments="{}"))


@dataclass
class _Message:
    """模拟 openai.types.chat.ChatCompletionMessage。"""
    role: str = "assistant"
    content: str | None = None
    tool_calls: list[_ToolCall] | None = None


@dataclass
class _Choice:
    """模拟 openai.types.chat.chat_completion.Choice。"""
    index: int = 0
    message: _Message = field(default_factory=_Message)
    finish_reason: str = "stop"


@dataclass
class _Usage:
    """模拟 openai.types.CompletionUsage。"""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class _ChatCompletion:
    """模拟 openai.types.chat.ChatCompletion。"""
    id: str = ""
    object: str = "chat.completion"
    model: str = ""
    choices: list[_Choice] = field(default_factory=list)
    usage: _Usage = field(default_factory=_Usage)


    # 兼容旧引用：保留 _ChatCompletion._StreamDelta 名称。
    _StreamDelta = StreamDelta



# ── 格式转换工具函数 ─────────────────────────────────────────


def _parse_data_uri(url: str) -> tuple[str, str]:
    """解析 data:mime;base64,data 格式的 URI，返回 (mime_type, base64_data)。"""
    if url.startswith("data:"):
        header, _, data = url.partition(",")
        mime = header.split(":")[1].split(";")[0] if ":" in header else "image/png"
        return mime, data
    return "image/png", url


def _openai_messages_to_gemini(
    messages: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """将 OpenAI messages 数组转换为 Gemini 的 systemInstruction + contents。

    返回 (system_instruction, contents)。
    """
    system_parts: list[str] = []
    contents: list[dict[str, Any]] = []

    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content")

        if role == "system":
            # Gemini 使用 systemInstruction 传递系统消息
            if isinstance(content, str) and content.strip():
                system_parts.append(content)
            continue

        if role == "user":
            if isinstance(content, list):
                parts: list[dict[str, Any]] = []
                for item in content:
                    if isinstance(item, dict):
                        if item.get("type") == "text":
                            parts.append({"text": item.get("text", "")})
                        elif item.get("type") == "image_url":
                            img_info = item.get("image_url", {})
                            url = img_info.get("url", "")
                            mime, b64 = _parse_data_uri(url)
                            parts.append({"inlineData": {"mimeType": mime, "data": b64}})
                contents.append({"role": "user", "parts": parts})
            else:
                contents.append({
                    "role": "user",
                    "parts": [{"text": content or ""}],
                })
            continue

        if role == "assistant":
            parts: list[dict[str, Any]] = []
            # 文本内容
            if content:
                parts.append({"text": content})
            # 工具调用 → functionCall
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
                    parts.append({
                        "functionCall": {"name": name, "args": args},
                    })
            if parts:
                contents.append({"role": "model", "parts": parts})
            continue

        if role == "tool":
            # OpenAI 工具结果 → Gemini functionResponse
            tool_call_id = msg.get("tool_call_id", "")
            # 尝试从上下文中找到对应的函数名
            func_name = _find_function_name_by_call_id(messages, tool_call_id)
            response_content = content or ""
            # 尝试解析为 JSON，Gemini 期望 functionResponse.response 为对象
            try:
                parsed = json.loads(response_content)
            except (json.JSONDecodeError, TypeError):
                parsed = {"result": response_content}
            contents.append({
                "role": "user",
                "parts": [{
                    "functionResponse": {
                        "name": func_name,
                        "response": parsed if isinstance(parsed, dict) else {"result": parsed},
                    },
                }],
            })
            continue

        # 未知角色，作为 user 消息传递
        logger.warning("未知消息角色 %r，作为 user 消息传递", role)
        contents.append({
            "role": "user",
            "parts": [{"text": str(content or "")}],
        })

    # 合并连续同角色消息（Gemini 要求 user/model 严格交替）
    contents = _merge_consecutive_roles(contents)

    system_instruction = None
    if system_parts:
        merged_system = "\n\n".join(system_parts)
        system_instruction = {"parts": [{"text": merged_system}]}

    return system_instruction, contents


def _find_function_name_by_call_id(
    messages: list[dict[str, Any]], tool_call_id: str,
) -> str:
    """从消息历史中根据 tool_call_id 反查函数名。"""
    if not tool_call_id:
        return "unknown_function"
    for msg in reversed(messages):
        if msg.get("role") != "assistant":
            continue
        for tc in msg.get("tool_calls", []):
            tc_dict = tc if isinstance(tc, dict) else {}
            if tc_dict.get("id") == tool_call_id:
                func = tc_dict.get("function", {})
                return func.get("name", "unknown_function")
    return "unknown_function"


def _merge_consecutive_roles(contents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """合并连续同角色的消息（Gemini 要求 user/model 严格交替）。"""
    if not contents:
        return contents
    merged: list[dict[str, Any]] = [contents[0]]
    for item in contents[1:]:
        if item["role"] == merged[-1]["role"]:
            merged[-1]["parts"].extend(item["parts"])
        else:
            merged.append(item)
    return merged


def _openai_tools_to_gemini(
    tools: list[dict[str, Any]] | None,
) -> list[dict[str, Any]] | None:
    """将 OpenAI tools 格式转换为 Gemini functionDeclarations 格式。"""
    if not tools:
        return None
    declarations: list[dict[str, Any]] = []
    for tool in tools:
        if tool.get("type") != "function":
            continue
        func = tool.get("function", {})
        decl: dict[str, Any] = {
            "name": func.get("name", ""),
            "description": func.get("description", ""),
        }
        params = func.get("parameters")
        if params:
            # Gemini 不支持 additionalProperties，需要清理
            decl["parameters"] = _clean_schema_for_gemini(params)
        declarations.append(decl)
    if not declarations:
        return None
    return [{"functionDeclarations": declarations}]


def _map_openai_tool_choice_to_gemini(tool_choice: Any) -> dict[str, Any] | None:
    """将 OpenAI tool_choice 映射为 Gemini toolConfig.functionCallingConfig。"""
    if tool_choice is None:
        return None

    if isinstance(tool_choice, str):
        normalized = tool_choice.strip().lower()
        if normalized == "auto":
            return {"functionCallingConfig": {"mode": "AUTO"}}
        if normalized == "required":
            return {"functionCallingConfig": {"mode": "ANY"}}
        if normalized == "none":
            return {"functionCallingConfig": {"mode": "NONE"}}
        return None

    if not isinstance(tool_choice, dict):
        return None

    tc_type = str(tool_choice.get("type", "")).strip().lower()
    if tc_type in {"auto", "none", "required"}:
        return _map_openai_tool_choice_to_gemini(tc_type)

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
        return {
            "functionCallingConfig": {
                "mode": "ANY",
                "allowedFunctionNames": [name],
            }
        }
    return None


def _clean_schema_for_gemini(schema: dict[str, Any]) -> dict[str, Any]:
    """清理 JSON Schema 使其兼容 Gemini API。

    Gemini 不支持某些 OpenAI schema 扩展字段。
    """
    cleaned = dict(schema)
    # 移除 Gemini 不支持的字段
    for key in ("additionalProperties", "$schema", "title"):
        cleaned.pop(key, None)
    # 递归清理 properties
    if "properties" in cleaned and isinstance(cleaned["properties"], dict):
        cleaned["properties"] = {
            k: _clean_schema_for_gemini(v) if isinstance(v, dict) else v
            for k, v in cleaned["properties"].items()
        }
    # 递归清理 items
    if "items" in cleaned and isinstance(cleaned["items"], dict):
        cleaned["items"] = _clean_schema_for_gemini(cleaned["items"])
    return cleaned


def _gemini_response_to_openai(
    data: dict[str, Any], model: str,
) -> _ChatCompletion:
    """将 Gemini generateContent 响应转换为 OpenAI ChatCompletion 格式。"""
    candidates = data.get("candidates", [])
    if not candidates:
        # 可能被安全过滤
        block_reason = data.get("promptFeedback", {}).get("blockReason", "UNKNOWN")
        return _ChatCompletion(
            id=f"gemini-{uuid.uuid4().hex[:12]}",
            model=model,
            choices=[_Choice(
                message=_Message(
                    content=f"[Gemini 安全过滤] 请求被拒绝，原因：{block_reason}",
                ),
                finish_reason="stop",
            )],
        )

    candidate = candidates[0]
    content_obj = candidate.get("content", {})
    parts = content_obj.get("parts", [])

    text_parts: list[str] = []
    tool_calls: list[_ToolCall] = []

    for part in parts:
        if "text" in part:
            text_parts.append(part["text"])
        elif "functionCall" in part:
            fc = part["functionCall"]
            call_id = f"call_{uuid.uuid4().hex[:24]}"
            args = fc.get("args", {})
            tool_calls.append(_ToolCall(
                id=call_id,
                function=_Function(
                    name=fc.get("name", ""),
                    arguments=json.dumps(args, ensure_ascii=False),
                ),
            ))

    message = _Message(
        content="\n".join(text_parts) if text_parts else None,
        tool_calls=tool_calls if tool_calls else None,
    )

    # 确定 finish_reason
    finish_reason_map = {
        "STOP": "stop",
        "MAX_TOKENS": "length",
        "SAFETY": "content_filter",
        "RECITATION": "content_filter",
    }
    raw_reason = candidate.get("finishReason", "STOP")
    finish_reason = finish_reason_map.get(raw_reason, "stop")
    if tool_calls:
        finish_reason = "tool_calls"

    # 解析 usage
    usage_meta = data.get("usageMetadata", {})
    prompt_tokens = usage_meta.get("promptTokenCount", 0)
    completion_tokens = usage_meta.get("candidatesTokenCount", 0)
    cached_content_tokens = usage_meta.get("cachedContentTokenCount", 0)

    usage = _Usage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
    )
    # 附加 Gemini 缓存统计到 usage 对象（供上层 _extract_cached_tokens 提取）
    if cached_content_tokens:
        # 兼容 OpenAI 格式：模拟 prompt_tokens_details.cached_tokens
        usage.prompt_tokens_details = {"cached_tokens": cached_content_tokens}  # type: ignore[attr-defined]

    return _ChatCompletion(
        id=f"gemini-{uuid.uuid4().hex[:12]}",
        model=model,
        choices=[_Choice(
            message=message,
            finish_reason=finish_reason,
        )],
        usage=usage,
    )


# ── 解析 base_url，提取 Gemini API 基础路径 ─────────────────────


def _extract_model_from_url(base_url: str) -> str | None:
    """从 Gemini 完整 URL 中提取模型名（如果有的话）。

    例如：
      https://right.codes/gemini/v1beta/models/gemini-2.5-flash:streamGenerateContent?alt=sse
      → "gemini-2.5-flash"

      https://right.codes/gemini/v1beta
      → None
    """
    url = base_url.split("?")[0]
    url = re.sub(r":(?:stream)?[Gg]enerate[Cc]ontent$", "", url)
    match = re.search(r"/models/([^/:]+)$", url)
    return match.group(1) if match else None


def _normalize_gemini_base_url(base_url: str) -> str:
    """从用户提供的 base_url 中提取 Gemini API 基础路径。

    支持的输入格式：
      - https://right.codes/gemini/v1beta/models/gemini-2.5-flash:streamGenerateContent?alt=sse
      - https://right.codes/gemini/v1beta
      - https://generativelanguage.googleapis.com/v1beta

    输出格式：https://host/path/v1beta（不含 /models/... 部分）
    """
    # 去除查询参数
    url = base_url.split("?")[0]
    # 去除 :generateContent / :streamGenerateContent 后缀
    url = re.sub(r":(?:stream)?[Gg]enerate[Cc]ontent$", "", url)
    # 去除 /models/xxx 部分
    url = re.sub(r"/models/[^/]+$", "", url)
    # 确保以 /v1beta 或 /v1 结尾
    if not re.search(r"/v1(?:beta\d*)?$", url):
        # 尝试找到 v1beta 或 v1 的位置并截断
        match = re.search(r"(/v1(?:beta\d*)?)", url)
        if match:
            url = url[: match.end()]
        else:
            # 默认追加 /v1beta
            url = url.rstrip("/") + "/v1beta"
    return url.rstrip("/")


# ── Gemini 客户端（鸭子类型兼容 openai.AsyncOpenAI） ─────────────


class _GeminiChatCompletions:
    """模拟 openai.AsyncOpenAI().chat.completions 接口。"""

    def __init__(self, client: GeminiClient) -> None:
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
        """将 OpenAI chat.completions.create 调用转发到 Gemini API。"""
        thinking_budget = kwargs.pop("_thinking_budget", 0)
        thinking_level = kwargs.pop("_thinking_level", "")
        if stream:
            return await self._client._generate_stream(
                model=model, messages=messages, tools=tools,
                tool_choice=kwargs.get("tool_choice"),
                thinking_budget=thinking_budget,
                thinking_level=thinking_level,
            )
        return await self._client._generate(
            model=model,
            messages=messages,
            tools=tools,
            tool_choice=kwargs.get("tool_choice"),
        )


class _GeminiChat:
    """模拟 openai.AsyncOpenAI().chat 接口。"""

    def __init__(self, client: GeminiClient) -> None:
        self.completions = _GeminiChatCompletions(client)


class GeminiClient:
    """Gemini 原生 API 客户端，鸭子类型兼容 openai.AsyncOpenAI。

    用法与 openai.AsyncOpenAI 完全一致：
        client = GeminiClient(api_key="...", base_url="...")
        response = await client.chat.completions.create(
            model="gemini-2.5-flash",
            messages=[...],
            tools=[...],
        )
    """

    def __init__(self, api_key: str, base_url: str) -> None:
        self._api_key = api_key
        # 从原始 URL 中提取模型名（如果有），作为默认模型
        self._default_model = _extract_model_from_url(base_url)
        self._base_url = _normalize_gemini_base_url(base_url)
        self._http = httpx.AsyncClient(timeout=300.0)
        self.chat = _GeminiChat(self)

    async def _generate(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: Any = None,
        tool_choice: Any = None,
    ) -> _ChatCompletion:
        """执行 Gemini generateContent 请求。"""
        # 如果 URL 中包含模型名，优先使用（允许用户只填完整 URL 不配 MODEL）
        effective_model = self._default_model or model
        system_instruction, contents = _openai_messages_to_gemini(messages)

        # 构建请求体
        body: dict[str, Any] = {"contents": contents}
        if system_instruction:
            body["systemInstruction"] = system_instruction

        # 转换工具定义
        tools_list = tools if isinstance(tools, list) else None
        gemini_tools = _openai_tools_to_gemini(tools_list)
        if gemini_tools:
            body["tools"] = gemini_tools

        mapped_tool_config = _map_openai_tool_choice_to_gemini(tool_choice)
        if mapped_tool_config is not None:
            body["toolConfig"] = mapped_tool_config

        # 构建请求 URL
        url = f"{self._base_url}/models/{effective_model}:generateContent"

        # 认证：Gemini API 支持 key 查询参数或 Authorization header
        headers = {"Content-Type": "application/json"}
        # 如果 api_key 看起来像 Bearer token（以 "ya29." 开头），用 header
        # 否则用查询参数（标准 Gemini API key 方式）
        if self._api_key.startswith("ya29."):
            headers["Authorization"] = f"Bearer {self._api_key}"
            params: dict[str, str] = {}
        else:
            params = {"key": self._api_key}

        logger.debug(
            "Gemini 请求: model=%s, contents=%d条, tools=%d个",
            effective_model,
            len(contents),
            len(gemini_tools[0]["functionDeclarations"]) if gemini_tools else 0,
        )

        try:
            resp = await self._http.post(
                url, json=body, headers=headers, params=params,
            )
        except httpx.HTTPError as exc:
            error_text = str(exc).strip()
            error_detail = (
                f"{exc.__class__.__name__}: {error_text}"
                if error_text
                else repr(exc)
            )
            logger.error("Gemini HTTP 请求失败: %s", error_detail)
            raise RuntimeError(f"Gemini API 请求失败: {error_detail}") from exc

        if resp.status_code != 200:
            error_text = resp.text[:500]
            logger.error(
                "Gemini API 返回错误 %d: %s", resp.status_code, error_text,
            )
            raise RuntimeError(
                f"Gemini API 错误 (HTTP {resp.status_code}): {error_text}"
            )

        try:
            data = resp.json()
        except (json.JSONDecodeError, ValueError) as exc:
            logger.error("Gemini 响应 JSON 解析失败: %s", exc)
            raise RuntimeError(f"Gemini 响应解析失败: {exc}") from exc

        result = _gemini_response_to_openai(data, effective_model)
        logger.debug(
            "Gemini 响应: tool_calls=%d, content_len=%d, tokens=%d",
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
        thinking_budget: int = 0,
        thinking_level: str = "",
    ) -> Any:
        """流式执行 Gemini generateContent 请求，返回异步生成器 yield StreamDelta。"""
        effective_model = self._default_model or model
        system_instruction, contents = _openai_messages_to_gemini(messages)
        body: dict[str, Any] = {"contents": contents}
        if system_instruction:
            body["systemInstruction"] = system_instruction
        tools_list = tools if isinstance(tools, list) else None
        gemini_tools = _openai_tools_to_gemini(tools_list)
        if gemini_tools:
            body["tools"] = gemini_tools
        mapped_tool_config = _map_openai_tool_choice_to_gemini(tool_choice)
        if mapped_tool_config is not None:
            body["toolConfig"] = mapped_tool_config
        if thinking_level:
            gen_config = body.get("generationConfig", {})
            gen_config["thinkingConfig"] = {"thinkingLevel": thinking_level}
            body["generationConfig"] = gen_config
        elif thinking_budget > 0:
            gen_config = body.get("generationConfig", {})
            gen_config["thinkingConfig"] = {"thinkingBudget": thinking_budget}
            body["generationConfig"] = gen_config

        url = f"{self._base_url}/models/{effective_model}:streamGenerateContent?alt=sse"
        headers = {"Content-Type": "application/json"}
        if self._api_key.startswith("ya29."):
            headers["Authorization"] = f"Bearer {self._api_key}"
            params: dict[str, str] = {}
        else:
            params = {"key": self._api_key}

        async def _stream_generator():
            async with self._http.stream("POST", url, json=body, headers=headers, params=params) as resp:
                if resp.status_code != 200:
                    error_text = await resp.aread()
                    raise RuntimeError(
                        f"Gemini API 错误 (HTTP {resp.status_code}): {error_text[:500]}"
                    )

                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    raw = line[6:]
                    try:
                        chunk_data = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    candidates = chunk_data.get("candidates", [])
                    if not candidates:
                        usage_meta = chunk_data.get("usageMetadata")
                        if usage_meta:
                            u = _Usage(
                                prompt_tokens=usage_meta.get("promptTokenCount", 0),
                                completion_tokens=usage_meta.get("candidatesTokenCount", 0),
                                total_tokens=usage_meta.get("totalTokenCount", 0),
                            )
                            _cached = usage_meta.get("cachedContentTokenCount", 0)
                            if _cached:
                                u.prompt_tokens_details = {"cached_tokens": _cached}  # type: ignore[attr-defined]
                            yield StreamDelta(usage=u)
                        continue

                    candidate = candidates[0]
                    parts = candidate.get("content", {}).get("parts", [])
                    finish = candidate.get("finishReason")

                    for part in parts:
                        if "text" in part:
                            yield StreamDelta(content_delta=part["text"])
                        elif "thought" in part:
                            yield StreamDelta(thinking_delta=part["thought"])
                        elif "functionCall" in part:
                            fc = part["functionCall"]
                            yield StreamDelta(tool_calls_delta=[{
                                "index": 0,
                                "id": str(uuid.uuid4()),
                                "name": fc.get("name", ""),
                                "arguments": json.dumps(fc.get("args", {})),
                            }])

                    if finish:
                        mapped_finish = "stop"
                        if finish in ("FUNCTION_CALL", "TOOL_CALLS"):
                            mapped_finish = "tool_calls"
                        usage_meta = chunk_data.get("usageMetadata")
                        u = None
                        if usage_meta:
                            u = _Usage(
                                prompt_tokens=usage_meta.get("promptTokenCount", 0),
                                completion_tokens=usage_meta.get("candidatesTokenCount", 0),
                                total_tokens=usage_meta.get("totalTokenCount", 0),
                            )
                            _cached = usage_meta.get("cachedContentTokenCount", 0)
                            if _cached:
                                u.prompt_tokens_details = {"cached_tokens": _cached}  # type: ignore[attr-defined]
                        yield StreamDelta(finish_reason=mapped_finish, usage=u)

        return _stream_generator()

    async def close(self) -> None:
        """关闭 HTTP 客户端。"""
        await self._http.aclose()
