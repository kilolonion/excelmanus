"""消息序列化工具：将 provider SDK 对象转换为可持久化的纯 Python 结构。"""

from __future__ import annotations

import json
from typing import Any

_TO_PLAIN_MAX_DEPTH = 32
_MALFORMED_ARGS_PREVIEW = 500


def to_plain(value: Any, _depth: int = 0) -> Any:
    """将 SDK 对象/命名空间对象转换为纯 Python 结构。"""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if _depth >= _TO_PLAIN_MAX_DEPTH:
        return str(value)
    if isinstance(value, dict):
        return {k: to_plain(v, _depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_plain(v, _depth + 1) for v in value]

    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return to_plain(model_dump(exclude_none=False), _depth + 1)
        except TypeError:
            return to_plain(model_dump(), _depth + 1)

    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return to_plain(to_dict(), _depth + 1)

    if hasattr(value, "__dict__"):
        return {
            k: to_plain(v, _depth + 1)
            for k, v in vars(value).items()
            if not k.startswith("_")
        }

    return str(value)


def assistant_message_to_dict(message: Any) -> dict[str, Any]:
    """提取 assistant 消息字典，尽量保留供应商扩展字段。"""
    payload = to_plain(message)
    if not isinstance(payload, dict):
        payload = {"content": str(getattr(message, "content", "") or "")}
    if "content" not in payload:
        payload["content"] = str(getattr(message, "content", "") or "")
    payload["role"] = "assistant"

    # DeepSeek thinking 模式要求 assistant 消息中包含 reasoning_content；
    # 当 reasoning_content 丢失时，从 thinking/reasoning 字段中恢复。
    _rc = payload.get("reasoning_content")
    if not _rc:
        _fallback = payload.get("thinking") or payload.get("reasoning")
        if _fallback:
            payload["reasoning_content"] = _fallback

    return payload


def sanitize_tool_call_arguments(tool_calls: list[Any]) -> list[Any]:
    """确保每个 tool_call 的 function.arguments 是合法 JSON 字符串。

    模型可能输出截断/非法的 arguments；若原样留在历史里，后续请求会被
    网关以 400 拒绝（部分网关在渲染模板时重解析 arguments），整个会话
    随之卡死。非法值替换为保留预览的占位 JSON，tool_result 中的
    INVALID_ARGS 已告知模型具体错误。
    """
    for call in tool_calls or []:
        if not isinstance(call, dict):
            continue
        function = call.get("function")
        if not isinstance(function, dict):
            continue
        arguments = function.get("arguments")
        if isinstance(arguments, str):
            try:
                json.loads(arguments)
                continue
            except (json.JSONDecodeError, TypeError):
                preview = arguments[:_MALFORMED_ARGS_PREVIEW]
                function["arguments"] = json.dumps(
                    {"_malformed_arguments": preview, "_truncated_len": len(arguments)},
                    ensure_ascii=False,
                )
        elif arguments is None:
            function["arguments"] = "{}"
        else:
            try:
                function["arguments"] = json.dumps(arguments, ensure_ascii=False, default=str)
            except (TypeError, ValueError):
                function["arguments"] = "{}"
    return tool_calls


__all__ = ["to_plain", "assistant_message_to_dict", "sanitize_tool_call_arguments"]
