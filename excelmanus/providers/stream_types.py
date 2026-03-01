from __future__ import annotations

import re
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


# ── 内联 <thinking> 标签提取（共享工具） ──────────────────────────

_THINKING_TAG_RE = re.compile(
    r"<thinking>(.*?)</thinking>",
    re.DOTALL,
)


def extract_inline_thinking(text: str) -> tuple[str, str]:
    """从文本中提取 <thinking>...</thinking> 标签内容。

    部分中转站将 extended thinking 内容以 XML 标签形式
    混入 text block，而非返回标准的 thinking content block。
    此函数将其分离为 (thinking_text, clean_content)。

    返回 ("", original_text) 如果没有 <thinking> 标签。
    """
    thinking_parts: list[str] = []
    for m in _THINKING_TAG_RE.finditer(text):
        thinking_parts.append(m.group(1).strip())
    if not thinking_parts:
        return "", text
    clean = _THINKING_TAG_RE.sub("", text).strip()
    return "\n".join(thinking_parts), clean


class InlineThinkingStateMachine:
    """流式 <thinking> 内联标签检测状态机。

    用于流式响应中逐 chunk 检测 <thinking>...</thinking> 标签，
    将 thinking 内容与正常内容分离后分别 yield 对应的 StreamDelta。

    用法::

        sm = InlineThinkingStateMachine()
        for chunk_text in stream:
            for delta in sm.feed(chunk_text):
                yield delta
        # 流结束后 flush 残余缓冲
        for delta in sm.flush():
            yield delta
    """

    _OPEN_TAG = "<thinking>"
    _CLOSE_TAG = "</thinking>"

    def __init__(self) -> None:
        self._in_thinking: bool = False
        self._buffer: str = ""

    def feed(self, text: str) -> list[StreamDelta]:
        """输入一个 text chunk，返回 0~N 个 StreamDelta。"""
        results: list[StreamDelta] = []
        buf = self._buffer + text
        self._buffer = ""

        while buf:
            if self._in_thinking:
                end_idx = buf.find(self._CLOSE_TAG)
                if end_idx != -1:
                    think_text = buf[:end_idx]
                    if think_text:
                        results.append(StreamDelta(thinking_delta=think_text))
                    buf = buf[end_idx + len(self._CLOSE_TAG):]
                    self._in_thinking = False
                else:
                    # 检查是否有跨 chunk 的闭标签前缀
                    tail_match = self._find_partial_tag_suffix(buf, self._CLOSE_TAG)
                    if tail_match > 0:
                        safe = buf[:-tail_match]
                        if safe:
                            results.append(StreamDelta(thinking_delta=safe))
                        self._buffer = buf[-tail_match:]
                    else:
                        results.append(StreamDelta(thinking_delta=buf))
                    buf = ""
            else:
                start_idx = buf.find(self._OPEN_TAG)
                if start_idx != -1:
                    content_text = buf[:start_idx]
                    if content_text:
                        results.append(StreamDelta(content_delta=content_text))
                    buf = buf[start_idx + len(self._OPEN_TAG):]
                    self._in_thinking = True
                else:
                    # 检查是否有跨 chunk 的开标签前缀
                    tail_match = self._find_partial_tag_suffix(buf, self._OPEN_TAG)
                    if tail_match > 0:
                        safe = buf[:-tail_match]
                        if safe:
                            results.append(StreamDelta(content_delta=safe))
                        self._buffer = buf[-tail_match:]
                    else:
                        results.append(StreamDelta(content_delta=buf))
                    buf = ""

        return results

    def flush(self) -> list[StreamDelta]:
        """流结束时 flush 残余缓冲。"""
        results: list[StreamDelta] = []
        if self._buffer:
            if self._in_thinking:
                results.append(StreamDelta(thinking_delta=self._buffer))
            else:
                results.append(StreamDelta(content_delta=self._buffer))
            self._buffer = ""
        return results

    @staticmethod
    def _find_partial_tag_suffix(buf: str, tag: str) -> int:
        """检查 buf 尾部是否是 tag 的前缀，返回匹配长度（0 表示无匹配）。"""
        max_check = min(len(tag) - 1, len(buf))
        for length in range(max_check, 0, -1):
            if tag.startswith(buf[-length:]):
                return length
        return 0
