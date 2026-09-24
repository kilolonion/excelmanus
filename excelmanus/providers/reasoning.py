"""Read visible reasoning text across OpenAI-compatible response dialects.

Only known text fields are rendered. Signatures, encrypted blocks and token
counts are metadata, not evidence that a response contains visible reasoning.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def _field(value: Any, name: str) -> Any:
    return value.get(name) if isinstance(value, Mapping) else getattr(value, name, None)


def _text(value: Any, depth: int = 0) -> str:
    if isinstance(value, str):
        return value
    if depth >= 8:
        return ""
    if isinstance(value, (list, tuple)):
        return "\n".join(part for item in value if (part := _text(item, depth + 1)))
    if _field(value, "type") in ("reasoning.encrypted", "redacted_thinking"):
        return ""
    for key in ("text", "thinking", "reasoning_content", "summary_text", "summary", "content"):
        text = _text(_field(value, key), depth + 1) if _field(value, key) is not None else ""
        if text:
            return text
    return ""


def split_content_parts(content: Any) -> tuple[str, str]:
    """Split typed content blocks into (reasoning, ordinary text)."""
    if isinstance(content, str):
        return "", content
    if not isinstance(content, (list, tuple)):
        return "", ""
    reasoning: list[str] = []
    text: list[str] = []
    for part in content:
        if isinstance(part, str):
            text.append(part)
            continue
        kind = _field(part, "type")
        thought = _field(part, "thought")
        if kind in ("reasoning.encrypted", "redacted_thinking"):
            continue
        if kind in (
            "thinking", "reasoning", "reasoning_content", "reasoning_text",
            "reasoning.text", "reasoning.summary", "summary_text",
        ) or thought is True or isinstance(thought, str):
            reasoning.append(_text(part) or (thought if isinstance(thought, str) else ""))
        elif kind in (None, "text", "output_text"):
            text.append(_text(_field(part, "text")))
    return "\n".join(part for part in reasoning if part), "".join(text)


def extract_reasoning_text(message: Any) -> str:
    """Read a message or delta, preferring one alias to avoid duplicate text."""
    for key in ("reasoning_content", "thinking", "reasoning", "thinking_text", "reasoning_details"):
        text = _text(_field(message, key))
        if text:
            return text
    return split_content_parts(_field(message, "content"))[0]
