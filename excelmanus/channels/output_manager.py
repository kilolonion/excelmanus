"""流式分块输出管理器：根据平台能力自动选择输出策略。

三种策略：
  - EditStreamStrategy（Telegram）：发消息 → 定时 edit → 超长新消息 + reply_to
  - CardStreamStrategy（飞书）：发卡片 → 高频 update_card 流式更新
  - BatchSendStrategy（QQ / 其他）：累积全部 → 智能分块 → 逐条发送
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from excelmanus.channels.base import ChannelAdapter, ChannelCapabilities
from excelmanus.channels.chunking import (
    degrade_tables,
    find_sentence_boundary,
    has_sentence_boundary,
    smart_chunk,
)

logger = logging.getLogger("excelmanus.channels.output_manager")

# ── 空闲心跳消息池 ──

_HEARTBEAT_TOOL_RUNNING: list[str] = [
    "🔧 正在执行 {tool_name}，这可能需要一些时间…",
    "⚙️ {tool_name} 仍在运行中，请耐心等待",
    "🔄 工具 {tool_name} 正在处理数据，马上就好",
    "🛠️ {tool_name} 执行中… 复杂操作需要多一点时间",
]

_HEARTBEAT_THINKING: list[str] = [
    "🧠 AI 正在深度分析您的请求…",
    "💭 正在思考最佳方案，请稍候…",
    "🤔 正在理解您的需求并规划步骤…",
    "📊 正在分析数据结构和内容…",
    "🔍 正在梳理逻辑，为您寻找最优解…",
]

_HEARTBEAT_WRITING: list[str] = [
    "📝 正在组织更多内容…",
    "✍️ 还在撰写回复中，请稍等",
    "💬 回复内容较多，仍在整理中…",
]

_HEARTBEAT_LONG_RUNNING: list[str] = [
    "⏱️ 已持续处理 {elapsed}，任务较复杂，仍在努力中…",
    "💪 复杂任务处理中（已用时 {elapsed}），感谢您的耐心等待",
    "🏃 仍在全力处理中（{elapsed}），请再等等",
]

# 心跳间隔阶梯（秒）：首次 15s → 30s → 之后每 60s
_HEARTBEAT_INTERVALS: list[float] = [15.0, 30.0, 60.0]


def _format_elapsed(seconds: float) -> str:
    """将秒数格式化为易读的耗时文本。"""
    if seconds < 60:
        return f"{int(seconds)}秒"
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    if secs:
        return f"{minutes}分{secs}秒"
    return f"{minutes}分钟"


def _pick_heartbeat_message(
    running_tools: list[str],
    has_text: bool,
    elapsed: float,
) -> str:
    """根据当前状态选择一条心跳消息。"""
    # 长时间运行 (>60s) → 优先显示耗时
    if elapsed > 60:
        msg = random.choice(_HEARTBEAT_LONG_RUNNING)
        return msg.format(elapsed=_format_elapsed(elapsed))
    # 有活跃工具
    if running_tools:
        msg = random.choice(_HEARTBEAT_TOOL_RUNNING)
        return msg.format(tool_name=running_tools[-1])
    # 已有部分文本输出但暂停了
    if has_text:
        return random.choice(_HEARTBEAT_WRITING)
    # 纯思考
    return random.choice(_HEARTBEAT_THINKING)


@dataclass
class StreamEvent:
    """SSE 事件的标准化表示。"""

    type: str           # event type: session_init, text_delta, tool_call_start, etc.
    data: dict[str, Any] = field(default_factory=dict)


class OutputStrategy(ABC):
    """输出策略抽象基类。"""

    def __init__(self, adapter: ChannelAdapter, chat_id: str) -> None:
        self._adapter = adapter
        self._chat_id = chat_id

    def _postprocess_text(self, text: str) -> str:
        """渠道感知后处理：对不支持 Markdown 表格的平台做表格降级。"""
        if not self._adapter.capabilities.supports_markdown_tables and text:
            return degrade_tables(text)
        return text

    @abstractmethod
    async def on_text_delta(self, content: str) -> None:
        """接收一段文本增量。"""

    @abstractmethod
    async def on_tool_start(self, tool_name: str) -> None:
        """工具开始执行。"""

    @abstractmethod
    async def on_tool_end(self, tool_name: str, success: bool) -> None:
        """工具执行结束。"""

    @abstractmethod
    async def on_progress(self, stage: str, message: str) -> None:
        """流水线进度事件。"""

    @abstractmethod
    async def finalize(self) -> None:
        """流结束，刷新所有残余缓冲。"""

    @abstractmethod
    def get_full_text(self) -> str:
        """返回累积的完整回复文本（供后续处理使用）。"""

    async def on_tool_notice(self, summary: str) -> None:
        """工具调用简要通知（/tools 开启时触发）。默认发送独立文本。"""
        await self._adapter.send_text(self._chat_id, summary)

    async def on_reasoning_notice(self, content: str) -> None:
        """推理过程通知（/reasoning 开启时触发）。默认截断后发送。"""
        display = content[:500] + ("..." if len(content) > 500 else "")
        await self._adapter.send_text(self._chat_id, f"💭 {display}")

    async def on_idle_heartbeat(self, message: str) -> None:
        """空闲心跳：长时间无事件时发送状态提示。默认发送文本。"""
        await self._adapter.send_text(self._chat_id, message)


class EditStreamStrategy(OutputStrategy):
    """Telegram 式策略：发送消息 → 定时编辑 → 超长时新消息续接。

    利用 editMessageText 实现渐进式输出，编辑间隔自适应调整以平衡响应速度和速率限制。
    """

    # 打字光标符号，编辑期间附加在末尾，finalize 时移除
    _TYPING_CURSOR = " \u25cd"
    # 自适应编辑间隔参数
    _EDIT_INTERVAL_MIN = 1.5   # 前几次编辑的最小间隔
    _EDIT_INTERVAL_MAX = 3.0   # 稳定后的最大间隔
    _EDIT_INTERVAL_STEP = 0.3  # 每次编辑后间隔增量
    # 首次刷新智能阈值
    _FIRST_FLUSH_CHARS = 120   # 字符数阈值
    _FIRST_FLUSH_TIMEOUT = 2.0 # 超时强制刷新（秒）
    # 持续 typing action 间隔
    _TYPING_INTERVAL = 4.0

    def __init__(
        self,
        adapter: ChannelAdapter,
        chat_id: str,
        *,
        first_flush_chars: int = 120,
        edit_interval: float = 3.0,
    ) -> None:
        super().__init__(adapter, chat_id)
        self._first_flush_chars = first_flush_chars
        self._edit_interval = edit_interval
        self._buffer: list[str] = []
        self._buffer_len = 0
        self._current_msg_id: str = ""
        self._current_msg_text: str = ""
        self._last_edit_time: float = 0
        self._flushed = False
        self._all_text_parts: list[str] = []
        self._tool_states: list[dict[str, str]] = []
        self._max_len = adapter.capabilities.max_message_length or 4000
        # P1a: 自适应编辑间隔
        self._edit_count: int = 0
        # P1c: 持续 typing action
        self._last_typing_time: float = 0
        # P1e: 首次刷新超时
        self._first_delta_time: float = 0

    def _current_edit_interval(self) -> float:
        """P1a: 自适应编辑间隔：前几次快速编辑，逐渐放慢。"""
        return min(
            self._EDIT_INTERVAL_MIN + self._edit_count * self._EDIT_INTERVAL_STEP,
            self._EDIT_INTERVAL_MAX,
        )

    async def _maybe_show_typing(self) -> None:
        """P1c: 持续发送 typing action，保持\u201c正在输入\u201d指示。"""
        if not self._adapter.capabilities.supports_typing:
            return
        now = time.monotonic()
        if now - self._last_typing_time >= self._TYPING_INTERVAL:
            try:
                await self._adapter.show_typing(self._chat_id)
            except Exception:
                pass
            self._last_typing_time = now

    async def on_text_delta(self, content: str) -> None:
        self._buffer.append(content)
        self._buffer_len += len(content)
        self._all_text_parts.append(content)

        # P1c: 持续 typing
        await self._maybe_show_typing()

        if not self._flushed:
            # P1e: 智能首次刷新 — 字符阈值 / 段落边界 / 超时
            if self._first_delta_time == 0:
                self._first_delta_time = time.monotonic()
            buf_joined = "".join(self._buffer)
            should_flush = (
                self._buffer_len >= self._first_flush_chars
                or (self._buffer_len >= 40 and has_sentence_boundary(buf_joined))
                or (time.monotonic() - self._first_delta_time >= self._FIRST_FLUSH_TIMEOUT)
            )
            if should_flush:
                await self._flush_initial()
        else:
            await self._try_edit()

    async def on_tool_start(self, tool_name: str) -> None:
        self._tool_states.append({"name": tool_name, "status": "running"})
        # P1d: 工具进度内联到主消息
        await self._try_edit_with_tools()

    async def on_tool_end(self, tool_name: str, success: bool) -> None:
        for tc in reversed(self._tool_states):
            if tc["name"] == tool_name and tc["status"] == "running":
                tc["status"] = "done" if success else "error"
                break
        # P1d: 工具状态变化时强制更新
        await self._try_edit_with_tools(force=True)

    async def on_progress(self, stage: str, message: str) -> None:
        """进度事件 → 尝试内联更新到主消息。"""
        if not message:
            return
        await self._maybe_show_typing()
        # 将进度信息作为工具状态的一部分展示
        await self._try_edit_with_tools()

    async def finalize(self) -> None:
        if not self._flushed:
            # 短回复 — 从未触发首次刷新，直接发送完整内容
            text = "".join(self._buffer).strip()
            if text:
                full = self._postprocess_text(self._prepend_tool_summary(text))
                await self._adapter.send_markdown(self._chat_id, full)
            return

        # 流结束 — 最终编辑一次，确保显示完整内容（无光标）
        remaining = "".join(self._buffer).strip()
        final_text = self._current_msg_text + (remaining or "")
        # 移除工具摘要前缀后重新添加完成版本
        final_text = self._postprocess_text(final_text.rstrip())
        if final_text:
            if len(final_text) <= self._max_len:
                ok = await self._adapter.edit_markdown(
                    self._chat_id, self._current_msg_id, final_text,
                )
                if not ok:
                    if remaining:
                        await self._adapter.send_markdown(self._chat_id, remaining)
            else:
                # 超长 — 编辑当前消息到满，剩余发新消息
                if remaining:
                    await self._overflow_send(remaining)

    def get_full_text(self) -> str:
        return "".join(self._all_text_parts).strip()

    # ── 内部方法 ──

    async def _flush_initial(self) -> None:
        """首次刷新：发送初始消息（带光标）。"""
        text = "".join(self._buffer)
        # P1d: 如果已有工具状态，内联到消息顶部
        text_with_tools = self._prepend_tool_summary(text)
        # P1b: 附加打字光标
        display = text_with_tools + self._TYPING_CURSOR
        self._current_msg_text = text_with_tools
        self._buffer = []
        self._buffer_len = 0
        self._flushed = True

        self._current_msg_id = await self._adapter.send_markdown_return_id(
            self._chat_id, display,
        )
        self._last_edit_time = time.monotonic()

    async def _try_edit(self) -> None:
        """尝试编辑当前消息（P1a 自适应间隔）。"""
        now = time.monotonic()
        # P1a: 使用自适应间隔
        if now - self._last_edit_time < self._current_edit_interval():
            return
        if not self._buffer:
            return

        # P1d: 内联工具摘要
        base_text = self._prepend_tool_summary("".join(self._all_text_parts))
        # P1b: 附加打字光标
        display_text = base_text + self._TYPING_CURSOR

        if len(base_text) > self._max_len:
            await self._overflow_send("".join(self._buffer))
            return

        ok = await self._adapter.edit_markdown(
            self._chat_id, self._current_msg_id, display_text,
        )
        if ok:
            self._current_msg_text = base_text
            self._buffer = []
            self._buffer_len = 0
            self._last_edit_time = now
            self._edit_count += 1
        # 编辑失败不丢数据 — 保留在 buffer 中等下次

    async def _try_edit_with_tools(self, force: bool = False) -> None:
        """P1d: 工具状态变化时尝试编辑主消息（工具摘要内联）。"""
        if not self._flushed:
            # 还未发送首条消息，先发工具状态消息
            text = self._build_tool_status_text()
            if text:
                await self._adapter.send_text(self._chat_id, text)
            return

        now = time.monotonic()
        if not force and now - self._last_edit_time < self._current_edit_interval():
            return

        # 重建完整显示文本：工具摘要 + 已有文本 + 光标
        all_text = "".join(self._all_text_parts)
        base_text = self._prepend_tool_summary(all_text) if all_text else self._build_tool_status_text()
        if not base_text:
            return
        display_text = base_text + self._TYPING_CURSOR

        if len(display_text) > self._max_len:
            return

        ok = await self._adapter.edit_markdown(
            self._chat_id, self._current_msg_id, display_text,
        )
        if ok:
            self._current_msg_text = base_text
            self._last_edit_time = now
            self._edit_count += 1

    async def _overflow_send(self, remaining_text: str) -> None:
        """当前消息已满，发送新消息续接。"""
        chunks = smart_chunk(
            remaining_text,
            self._max_len,
            self._adapter.capabilities.preferred_format,
        )
        reply_to = self._current_msg_id if self._adapter.capabilities.supports_reply_chain else None
        for chunk in chunks:
            msg_id = await self._adapter.send_markdown_return_id(
                self._chat_id, chunk, reply_to=reply_to,
            )
            if msg_id:
                self._current_msg_id = msg_id
                self._current_msg_text = chunk
            reply_to = msg_id or None
        self._buffer = []
        self._buffer_len = 0

    def _build_tool_status_text(self) -> str:
        """P1d: 构建工具状态单行文本。"""
        if not self._tool_states:
            return ""
        icons = {"done": "✅", "error": "❌", "running": "🔄"}
        parts = []
        for tc in self._tool_states:
            icon = icons.get(tc["status"], "🔧")
            parts.append(f"{icon} {tc['name']}")
        return "⚙️ " + " → ".join(parts)

    async def on_tool_notice(self, summary: str) -> None:
        """Telegram: 工具通知独立发送，避免与推理通知互相覆盖。"""
        await self._adapter.send_text(self._chat_id, f"🔧 {summary}")

    async def on_reasoning_notice(self, content: str) -> None:
        """Telegram: 推理通知独立发送（避免撑爆编辑消息）。"""
        display = content[:800] + ("..." if len(content) > 800 else "")
        await self._adapter.send_text(self._chat_id, f"💭 {display}")

    async def on_idle_heartbeat(self, message: str) -> None:
        """Telegram: 心跳编辑到主消息末尾，未刷新时发新文本。"""
        if self._flushed and self._current_msg_id:
            # 将心跳追加到当前消息末尾
            display = self._current_msg_text + f"\n\n_{message}_" + self._TYPING_CURSOR
            if len(display) <= self._max_len:
                ok = await self._adapter.edit_markdown(
                    self._chat_id, self._current_msg_id, display,
                )
                if ok:
                    return
        await self._adapter.send_text(self._chat_id, message)

    def _prepend_tool_summary(self, text: str) -> str:
        """在文本前添加工具链摘要（短回复场景）。"""
        if not self._tool_states:
            return text
        icons = {"done": "✅", "error": "❌", "running": "🔧"}
        chain = " → ".join(
            f"{icons.get(tc['status'], '🔧')} {tc['name']}" for tc in self._tool_states
        )
        return f"⚙️ {chain}\n\n{text}"


class CardStreamStrategy(OutputStrategy):
    """飞书式策略：通过消息卡片 + 卡片更新实现流式输出。

    飞书支持卡片更新（~5次/秒），所有内容在同一张卡片内展示和更新。

    P3 增强：
    - 进度事件集成到卡片底部
    - 超长内容自动发新卡片续接
    - 最终卡片状态着色（绿=成功 / 红=出错）
    """

    def __init__(
        self,
        adapter: ChannelAdapter,
        chat_id: str,
        *,
        update_interval: float = 0.5,
    ) -> None:
        super().__init__(adapter, chat_id)
        self._update_interval = update_interval
        self._buffer: list[str] = []
        self._all_text_parts: list[str] = []
        self._card_msg_id: str = ""
        self._last_update_time: float = 0
        self._tool_states: list[dict[str, str]] = []
        self._max_len = adapter.capabilities.max_message_length or 5000
        self._sent = False
        # P3a: 进度信息
        self._progress_text: str = ""
        # P3b: 已溢出的文本长度（跟踪续卡需求）
        self._overflow_sent_len: int = 0
        # P3c: 是否有错误
        self._has_error: bool = False
        # 工具/推理通知独立字段（避免串台）
        self._tool_notice_text: str = ""
        self._reasoning_notice_text: str = ""

    async def on_text_delta(self, content: str) -> None:
        self._buffer.append(content)
        self._all_text_parts.append(content)
        await self._try_update()

    async def on_tool_start(self, tool_name: str) -> None:
        self._tool_states.append({"name": tool_name, "status": "running"})
        await self._try_update(force=True)

    async def on_tool_end(self, tool_name: str, success: bool) -> None:
        for tc in reversed(self._tool_states):
            if tc["name"] == tool_name and tc["status"] == "running":
                tc["status"] = "done" if success else "error"
                break
        await self._try_update(force=True)

    async def on_progress(self, stage: str, message: str) -> None:
        """P3a: 进度事件集成到卡片底部。"""
        if message:
            self._progress_text = f"⏳ [{stage}] {message}"
            await self._try_update(force=True)

    def set_error(self, error: str) -> None:
        """P3c: 标记出错状态，影响最终卡片颜色。"""
        self._has_error = True

    async def finalize(self) -> None:
        self._progress_text = ""  # 清除进度信息
        card = self._build_card(final=True)
        if self._card_msg_id:
            ok = await self._adapter.update_card(
                self._chat_id, self._card_msg_id, card,
            )
            if not ok:
                # 更新失败 — 发送最终文本
                text = self.get_full_text()
                if text:
                    await self._adapter.send_markdown(self._chat_id, text)
        elif self.get_full_text():
            # 从未发过卡片 — 直接发送最终卡片
            await self._adapter.send_card(self._chat_id, card)

    def get_full_text(self) -> str:
        return "".join(self._all_text_parts).strip()

    async def on_tool_notice(self, summary: str) -> None:
        """飞书: 工具通知独立字段，不覆盖推理通知。"""
        self._tool_notice_text = f"🔧 {summary}"
        await self._try_update(force=True)

    async def on_reasoning_notice(self, content: str) -> None:
        """飞书: 推理通知独立字段，不覆盖工具通知。"""
        display = content[:600] + ("..." if len(content) > 600 else "")
        self._reasoning_notice_text = f"💭 {display}"
        await self._try_update(force=True)

    async def on_idle_heartbeat(self, message: str) -> None:
        """飞书: 更新卡片 header 为心跳消息。"""
        if self._card_msg_id:
            card = self._build_card(final=False, header_override=message)
            await self._adapter.update_card(
                self._chat_id, self._card_msg_id, card,
            )
        else:
            # 尚未发过卡片 — 发送带心跳标题的卡片
            card = self._build_card(final=False, header_override=message)
            self._card_msg_id = await self._adapter.send_card(
                self._chat_id, card,
            )

    async def _try_update(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_update_time < self._update_interval:
            return

        # P3b: 检查是否需要溢出到新卡片
        full_text = "".join(self._all_text_parts).strip()
        visible_text = full_text[self._overflow_sent_len:]
        if len(visible_text) > self._max_len and self._card_msg_id:
            # 当前卡片已满 — finalize 当前卡片并发新卡片
            overflow_card = self._build_card(final=True, text_override=full_text[:self._overflow_sent_len + self._max_len])
            await self._adapter.update_card(
                self._chat_id, self._card_msg_id, overflow_card,
            )
            self._overflow_sent_len += self._max_len
            self._card_msg_id = ""  # 重置，下面会发新卡片

        card = self._build_card(final=False)
        if self._card_msg_id:
            ok = await self._adapter.update_card(
                self._chat_id, self._card_msg_id, card,
            )
            if ok:
                self._last_update_time = now
        else:
            self._card_msg_id = await self._adapter.send_card(
                self._chat_id, card,
            )
            self._last_update_time = now

    def _build_card(
        self,
        final: bool = False,
        header_override: str = "",
        text_override: str | None = None,
    ) -> dict[str, Any]:
        """构建飞书消息卡片 JSON。"""
        elements: list[dict[str, Any]] = []

        # 工具状态
        if self._tool_states:
            icons = {"done": "✅", "error": "❌", "running": "🔄"}
            tool_lines = []
            for tc in self._tool_states:
                icon = icons.get(tc["status"], "🔧")
                tool_lines.append(f"{icon} {tc['name']}")
            elements.append({
                "tag": "div",
                "text": {"tag": "plain_text", "content": " → ".join(tool_lines)},
            })
            elements.append({"tag": "hr"})

        # 文本内容
        if text_override is not None:
            text = text_override.strip()
        else:
            full_text = "".join(self._all_text_parts).strip()
            text = full_text[self._overflow_sent_len:]  # P3b: 只显示当前卡片的部分
        if text:
            display = text[:self._max_len] if len(text) > self._max_len else text
            if not final and len(text) > 0:
                display += " ▍"  # 打字光标
            elements.append({
                "tag": "div",
                "text": {"tag": "lark_md", "content": display},
            })

        # P3a: 进度信息（非 final 时展示）
        if not final and self._progress_text:
            elements.append({"tag": "hr"})
            elements.append({
                "tag": "note",
                "elements": [
                    {"tag": "plain_text", "content": self._progress_text},
                ],
            })

        # 工具/推理通知（独立字段，互不覆盖）
        _notice_parts = []
        if not final and self._tool_notice_text:
            _notice_parts.append(self._tool_notice_text)
        if not final and self._reasoning_notice_text:
            _notice_parts.append(self._reasoning_notice_text)
        if _notice_parts:
            elements.append({"tag": "hr"})
            for np in _notice_parts:
                elements.append({
                    "tag": "note",
                    "elements": [
                        {"tag": "plain_text", "content": np},
                    ],
                })

        # P3c: 错误信息（final 时展示）
        if final and self._has_error:
            elements.append({"tag": "hr"})
            elements.append({
                "tag": "note",
                "elements": [
                    {"tag": "plain_text", "content": "⚠️ 处理过程中出现错误"},
                ],
            })

        # P3b: 续卡标注
        is_continuation = self._overflow_sent_len > 0 and text_override is None
        if header_override:
            header_title = header_override
        elif final:
            header_title = "ExcelManus"
        elif is_continuation:
            header_title = "ExcelManus（续）"
        else:
            header_title = "ExcelManus 思考中..."

        # P3c: 最终卡片状态着色
        if final:
            template = "red" if self._has_error else "green"
        else:
            template = "wathet"

        return {
            "header": {
                "title": {"tag": "plain_text", "content": header_title},
                "template": template,
            },
            "elements": elements,
            "text": text,  # 降级用
        }


class BatchSendStrategy(OutputStrategy):
    """QQ / 无编辑能力平台策略：渐进式分段发送。

    P2 增强：
    - 段落级渐进发送：遇到段落边界且缓冲 ≥ 阈值时即时发送
    - 工具完成即时反馈：每个工具结束后发送状态通知
    - 保活消息携带进度信息
    """

    # P2a: 渐进发送阈值（字符数）
    _PROGRESSIVE_MIN_CHARS = 200
    # P2a: 渐进发送间隔（避免刷屏）
    _PROGRESSIVE_INTERVAL = 3.0

    def __init__(
        self,
        adapter: ChannelAdapter,
        chat_id: str,
        *,
        send_interval: float = 1.0,
        keepalive_interval: float = 240.0,
    ) -> None:
        super().__init__(adapter, chat_id)
        self._send_interval = send_interval
        self._keepalive_interval = keepalive_interval
        self._all_text_parts: list[str] = []
        self._tool_states: list[dict[str, str]] = []
        self._sent_keepalive = False
        self._start_time = time.monotonic()
        self._last_keepalive_time = self._start_time
        self._max_len = adapter.capabilities.max_message_length or 2000
        # P2a: 渐进式发送状态
        self._progressive_buffer: list[str] = []
        self._progressive_buffer_len: int = 0
        self._last_progressive_send: float = 0
        self._progressive_sent_parts: list[str] = []
        # P2b: 工具完成计数
        self._tools_done: int = 0
        self._tools_total: int = 0

    async def on_text_delta(self, content: str) -> None:
        self._all_text_parts.append(content)
        self._progressive_buffer.append(content)
        self._progressive_buffer_len += len(content)
        # QQ 被动回复窗口保活
        await self._check_keepalive()
        # P2a: 检查是否可以渐进发送
        await self._try_progressive_send()

    async def on_tool_start(self, tool_name: str) -> None:
        self._tool_states.append({"name": tool_name, "status": "running"})
        self._tools_total += 1
        # 首个工具开始时发送"处理中"提示
        if len(self._tool_states) == 1 and not self._sent_keepalive:
            await self._adapter.send_text(self._chat_id, "⏳ 正在处理，请稍候...")
            self._sent_keepalive = True
            self._last_keepalive_time = time.monotonic()

    async def on_tool_end(self, tool_name: str, success: bool) -> None:
        for tc in reversed(self._tool_states):
            if tc["name"] == tool_name and tc["status"] == "running":
                tc["status"] = "done" if success else "error"
                break
        self._tools_done += 1
        # P2b: 工具完成即时反馈
        icon = "✅" if success else "❌"
        status_word = "完成" if success else "失败"
        await self._adapter.send_text(
            self._chat_id, f"{icon} {tool_name} {status_word}",
        )
        self._last_keepalive_time = time.monotonic()
        self._sent_keepalive = True

    async def on_progress(self, stage: str, message: str) -> None:
        await self._check_keepalive()

    async def finalize(self) -> None:
        # P2a: 发送残余缓冲
        remaining = "".join(self._progressive_buffer).strip()
        if remaining:
            full = self._postprocess_text(remaining)
            chunks = smart_chunk(
                full, self._max_len,
                self._adapter.capabilities.preferred_format,
            )
            for i, chunk in enumerate(chunks):
                if i > 0:
                    await asyncio.sleep(self._send_interval)
                await self._adapter.send_markdown(self._chat_id, chunk)
            self._progressive_sent_parts.append(remaining)
            self._progressive_buffer = []
            self._progressive_buffer_len = 0
        elif not self._progressive_sent_parts:
            # 从未渐进发送过，走旧逻辑
            text = self.get_full_text()
            if not text:
                return
            full = self._postprocess_text(self._prepend_tool_summary(text))
            chunks = smart_chunk(
                full, self._max_len,
                self._adapter.capabilities.preferred_format,
            )
            for i, chunk in enumerate(chunks):
                if i > 0:
                    await asyncio.sleep(self._send_interval)
                await self._adapter.send_markdown(self._chat_id, chunk)

    def get_full_text(self) -> str:
        return "".join(self._all_text_parts).strip()

    async def on_idle_heartbeat(self, message: str) -> None:
        """QQ/其他: 发送心跳文本，同时刷新 keepalive 计时器。"""
        await self._adapter.send_text(self._chat_id, message)
        self._last_keepalive_time = time.monotonic()
        self._sent_keepalive = True

    # P2a: 渐进式发送（自然语言断句增强）
    async def _try_progressive_send(self) -> None:
        """在自然语言句子边界处尝试即时发送缓冲内容。

        使用 find_sentence_boundary() 进行多级断句：
        段落 > 句末换行 > 句末标点（。！？；.!?;） > 逗号级（，、,） > 裸换行。
        代码块内部的标点不作为断点。
        """
        if self._progressive_buffer_len < self._PROGRESSIVE_MIN_CHARS:
            return
        now = time.monotonic()
        if now - self._last_progressive_send < self._PROGRESSIVE_INTERVAL:
            return

        buf_text = "".join(self._progressive_buffer)
        # 使用自然语言断句：在 min_pos 之后寻找最佳句子边界
        cut = find_sentence_boundary(
            buf_text,
            min_pos=self._PROGRESSIVE_MIN_CHARS // 2,
        )
        if cut <= 0:
            return  # 无合适的断句点

        to_send = buf_text[:cut].strip()
        leftover = buf_text[cut:]
        if not to_send:
            return

        processed = self._postprocess_text(to_send)
        chunks = smart_chunk(
            processed, self._max_len,
            self._adapter.capabilities.preferred_format,
        )
        for i, chunk in enumerate(chunks):
            if i > 0:
                await asyncio.sleep(self._send_interval)
            await self._adapter.send_markdown(self._chat_id, chunk)

        self._progressive_sent_parts.append(to_send)
        self._progressive_buffer = [leftover] if leftover else []
        self._progressive_buffer_len = len(leftover)
        self._last_progressive_send = now
        self._last_keepalive_time = now
        self._sent_keepalive = True

    async def _check_keepalive(self) -> None:
        """P2c: 保活消息携带进度信息。"""
        window = self._adapter.capabilities.passive_reply_window
        if window <= 0:
            return
        now = time.monotonic()
        if now - self._last_keepalive_time >= self._keepalive_interval:
            if self._tools_total > 0:
                msg = f"⏳ 仍在处理中... (已完成 {self._tools_done}/{self._tools_total} 个步骤)"
            else:
                elapsed = _format_elapsed(now - self._start_time)
                msg = f"⏳ 仍在处理中...（已用时 {elapsed}）"
            await self._adapter.send_text(self._chat_id, msg)
            self._last_keepalive_time = now
            self._sent_keepalive = True

    def _prepend_tool_summary(self, text: str) -> str:
        if not self._tool_states:
            return text
        icons = {"done": "✅", "error": "❌", "running": "🔧"}
        chain = " → ".join(
            f"{icons.get(tc['status'], '🔧')} {tc['name']}" for tc in self._tool_states
        )
        return f"⚙️ {chain}\n\n{text}"


class ChunkedOutputManager:
    """流式分块输出管理器。

    根据 adapter.capabilities 自动选择最佳输出策略，
    接收 SSE 事件流并实时推送到渠道。

    内置空闲心跳：当 SSE 事件长时间未到达时，自动发送上下文相关的
    "仍在处理"提示，间隔递增（15s → 30s → 60s…）以避免刷屏。

    用法::

        manager = ChunkedOutputManager(adapter, chat_id)
        async for event_type, data in api.stream_chat_events(...):
            await manager.feed(event_type, data)
        result = await manager.finalize()
    """

    def __init__(self, adapter: ChannelAdapter, chat_id: str) -> None:
        self._adapter = adapter
        self._chat_id = chat_id
        self._strategy = self._pick_strategy(adapter, chat_id)

        # ChatResult 收集
        self._session_id: str = ""
        self._tool_calls: list[dict[str, Any]] = []
        self._approval: dict[str, Any] | None = None
        self._question: dict[str, Any] | None = None
        self._file_downloads: list[dict[str, Any]] = []
        self._progress_events: list[dict[str, Any]] = []
        self._staging_event: dict[str, Any] | None = None
        self._error: str | None = None

        # 空闲心跳状态
        self._start_time: float = time.monotonic()
        self._last_event_time: float = self._start_time
        self._heartbeat_task: asyncio.Task | None = None  # type: ignore[type-arg]
        self._heartbeat_count: int = 0

    @staticmethod
    def _pick_strategy(
        adapter: ChannelAdapter, chat_id: str,
    ) -> OutputStrategy:
        caps = adapter.capabilities
        if caps.supports_card_update:
            return CardStreamStrategy(adapter, chat_id)
        elif caps.supports_edit and caps.max_edits_per_minute > 0:
            return EditStreamStrategy(adapter, chat_id)
        else:
            return BatchSendStrategy(adapter, chat_id)

    # ── 心跳管理 ──

    def start_heartbeat(self) -> None:
        """启动后台空闲心跳任务。应在开始消费 SSE 事件前调用。"""
        if self._heartbeat_task is None:
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    def _stop_heartbeat(self) -> None:
        """停止心跳任务。"""
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            self._heartbeat_task = None

    def _next_heartbeat_interval(self) -> float:
        """返回下次心跳前应等待的秒数（递增阶梯）。"""
        idx = min(self._heartbeat_count, len(_HEARTBEAT_INTERVALS) - 1)
        return _HEARTBEAT_INTERVALS[idx]

    def _get_running_tools(self) -> list[str]:
        """返回当前仍在运行的工具名列表。"""
        return [
            tc["name"] for tc in self._tool_calls
            if tc.get("status") == "running"
        ]

    async def _heartbeat_loop(self) -> None:
        """后台循环：检测空闲并发送心跳。"""
        try:
            while True:
                interval = self._next_heartbeat_interval()
                await asyncio.sleep(interval)

                idle = time.monotonic() - self._last_event_time
                if idle < interval * 0.8:
                    # 刚收到过事件，重置等待
                    continue

                elapsed = time.monotonic() - self._start_time
                running_tools = self._get_running_tools()
                has_text = bool(self._strategy.get_full_text())

                message = _pick_heartbeat_message(running_tools, has_text, elapsed)
                try:
                    await self._strategy.on_idle_heartbeat(message)
                except Exception:
                    logger.debug("心跳发送失败", exc_info=True)

                self._heartbeat_count += 1
        except asyncio.CancelledError:
            pass

    def _touch_event_time(self) -> None:
        """更新最后事件时间戳。"""
        self._last_event_time = time.monotonic()

    async def feed(self, event_type: str, data: dict[str, Any]) -> None:
        """接收一个 SSE 事件并分发到策略。"""
        self._touch_event_time()

        if event_type == "session_init":
            self._session_id = data.get("session_id", self._session_id)

        elif event_type in ("text", "text_delta"):
            chunk = data.get("content", "")
            if chunk:
                await self._strategy.on_text_delta(chunk)

        elif event_type == "tool_call_start":
            tool_name = data.get("tool_name", "unknown")
            self._tool_calls.append({"name": tool_name, "status": "running"})
            await self._strategy.on_tool_start(tool_name)

        elif event_type == "tool_call_end":
            tool_name = data.get("tool_name", "")
            success = data.get("success", True)
            for tc in reversed(self._tool_calls):
                if tc["name"] == tool_name and tc["status"] == "running":
                    tc["status"] = "done" if success else "error"
                    break
            await self._strategy.on_tool_end(tool_name, success)

        elif event_type == "pending_approval":
            self._approval = data

        elif event_type == "user_question":
            self._question = data

        elif event_type == "file_download":
            self._file_downloads.append(data)

        elif event_type == "pipeline_progress":
            self._progress_events.append(data)
            stage = data.get("stage", "")
            message = data.get("message", "")
            await self._strategy.on_progress(stage, message)

        elif event_type == "reply":
            # reply 事件包含完整回复文本（与已流式推送的 text_delta 重复），
            # 仅在未收到任何 text_delta 时才作为兜底输出，避免双重发送。
            content = data.get("content", "")
            if content and not self._strategy.get_full_text():
                await self._strategy.on_text_delta(content)

        elif event_type == "error":
            self._error = data.get("error", "未知错误")

        elif event_type == "failure_guidance":
            title = data.get("title", "")
            message = data.get("message", "")
            self._error = f"{title}: {message}" if title else message

        elif event_type == "tool_call_notice":
            summary = data.get("args_summary", "")
            if summary:
                await self._strategy.on_tool_notice(summary)

        elif event_type == "reasoning_notice":
            content = data.get("content", "")
            if content:
                await self._strategy.on_reasoning_notice(content)

        elif event_type == "staging_updated":
            self._staging_event = data

    async def finalize(self) -> dict[str, Any]:
        """流结束，刷新策略缓冲区，返回结构化结果。

        返回 dict 包含与 ChatResult 相同的字段，
        供 MessageHandler 做后续处理（审批/问答/文件下载等）。
        """
        self._stop_heartbeat()

        # P3c: 通知策略错误状态（影响飞书卡片颜色）
        if self._error and hasattr(self._strategy, "set_error"):
            self._strategy.set_error(self._error)

        # 如果有错误且没有文本，通过策略输出错误消息
        if self._error and not self._strategy.get_full_text():
            await self._strategy.on_text_delta(f"❌ {self._error}")

        await self._strategy.finalize()

        # 组装回复文本（供兼容逻辑使用）
        reply = self._strategy.get_full_text()

        return {
            "reply": reply,
            "session_id": self._session_id,
            "tool_calls": self._tool_calls,
            "approval": self._approval,
            "question": self._question,
            "file_downloads": self._file_downloads,
            "progress_events": self._progress_events,
            "staging_event": self._staging_event,
            "error": self._error,
        }
