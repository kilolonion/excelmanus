from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class StreamDelta:
    """跨 provider 共享的流式增量数据结构。"""

    thinking_delta: str = ""
    content_delta: str = ""
    tool_calls_delta: list[Any] = field(default_factory=list)
    finish_reason: str | None = None
    usage: Any | None = None
