"""ToolResult — 工具执行结果的结构化数据类。

替代原先通过 JSON 字符串中 _image_injection 等魔法字段传递的 side-channel 协议，
提供类型安全的工具返回值表示。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ImageInjection:
    """工具返回的图片注入数据。"""

    base64: str
    mime_type: str = "image/png"
    detail: str = "auto"


@dataclass
class ToolResult:
    """工具执行的结构化返回值。

    Attributes:
        text: 工具返回的文本内容（供 LLM 消费）。
        success: 工具是否执行成功。
        image: 可选的图片注入数据（替代 _image_injection side-channel）。
        cow_mapping: 可选的 CoW 路径映射。
        metadata: 可选的额外元数据。
    """

    text: str
    success: bool = True
    image: ImageInjection | None = None
    cow_mapping: dict[str, str] | None = None
    metadata: dict[str, Any] | None = field(default=None)
