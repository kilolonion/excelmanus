"""消息序列化工具：将 provider SDK 对象转换为可持久化的纯 Python 结构。"""

from __future__ import annotations

from typing import Any

_TO_PLAIN_MAX_DEPTH = 32


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
    return payload


__all__ = ["to_plain", "assistant_message_to_dict"]
