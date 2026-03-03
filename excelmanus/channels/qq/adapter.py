"""QQ Bot 渠道适配器：实现 ChannelAdapter 接口。

QQ 官方机器人 SDK: https://bot.q.qq.com/wiki/develop/api-v2/
库: botpy (qq-botpy)

支持三种消息场景：
  - QQ群（group）：群聊中 @机器人 触发
  - C2C 私聊：用户直接发消息给机器人
  - QQ频道（guild）：频道中 @机器人 触发

chat_id 编码规则：
  - "group:{group_openid}" — QQ群
  - "c2c:{user_openid}" — C2C 私聊
  - "guild:{channel_id}" — QQ频道子频道
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any

from excelmanus.channels.base import ChannelAdapter, ChannelCapabilities
from excelmanus.channels.chunking import smart_chunk

logger = logging.getLogger("excelmanus.channels.qq")

# QQ 消息长度上限（群/C2C 约 2000 字符，频道约 4000）
QQ_MAX_MESSAGE_LEN = 2000
# 被动回复窗口（秒）
QQ_PASSIVE_REPLY_WINDOW = 300

# 发送重试配置（botpy 超时后返回 None，需应用层重试）
QQ_SEND_MAX_RETRIES = 2
QQ_SEND_RETRY_BASE_DELAY = 1.0  # 秒，指数退避基数

# chat_id 前缀常量
PREFIX_GROUP = "group:"
PREFIX_C2C = "c2c:"
PREFIX_GUILD = "guild:"


def parse_chat_id(chat_id: str) -> tuple[str, str]:
    """解析 chat_id 为 (type, id)。

    Returns:
        ("group", group_openid) / ("c2c", user_openid) / ("guild", channel_id)
    """
    if chat_id.startswith(PREFIX_GROUP):
        return "group", chat_id[len(PREFIX_GROUP):]
    if chat_id.startswith(PREFIX_C2C):
        return "c2c", chat_id[len(PREFIX_C2C):]
    if chat_id.startswith(PREFIX_GUILD):
        return "guild", chat_id[len(PREFIX_GUILD):]
    # 兼容：无前缀默认当 guild channel_id
    return "guild", chat_id


class QQBotAdapter(ChannelAdapter):
    """QQ Bot 适配器。

    通过 botpy SDK 实现消息收发，支持 QQ群 / C2C 私聊 / QQ频道 三种场景。
    handlers.py 中的 ExcelManusQQClient 将 botpy 事件转换为通用 ChannelMessage，
    由 MessageHandler 统一调度后通过本适配器回送响应。
    """

    name = "qq"
    capabilities = ChannelCapabilities(
        supports_edit=False,
        supports_card=False,
        supports_reply_chain=False,
        supports_typing=False,
        max_message_length=QQ_MAX_MESSAGE_LEN,
        max_edits_per_minute=0,
        preferred_format="plain",
        passive_reply_window=QQ_PASSIVE_REPLY_WINDOW,
    )

    def __init__(self, app_id: str = "", secret: str = "", **kwargs) -> None:
        self.app_id = app_id
        self.secret = secret
        self._api: Any = None  # botpy.BotAPI 实例，由 handlers.py 注入
        # 缓存最近的入站 msg_id，用于被动回复（QQ 要求 5 分钟内回复需带 msg_id）
        # key: chat_id, value: (msg_id, timestamp)
        self._last_msg_ids: dict[str, tuple[str, float]] = {}
        # msg_seq 计数器（同一 msg_id 下递增，避免重复）
        self._msg_seq: dict[str, int] = {}
        self._record_count: int = 0  # 用于触发定期清理

    def set_api(self, api: Any) -> None:
        """注入 botpy.BotAPI 实例。"""
        self._api = api

    def record_incoming_msg(self, chat_id: str, msg_id: str) -> None:
        """记录入站消息 ID，用于后续被动回复。"""
        if not msg_id:
            return
        self._last_msg_ids[chat_id] = (msg_id, time.monotonic())
        self._msg_seq[chat_id] = 1
        # 每 100 次记录触发一次过期清理，避免无界增长
        self._record_count += 1
        if self._record_count % 100 == 0:
            self._cleanup_expired()

    def _get_reply_msg_id(self, chat_id: str) -> str | None:
        """获取可用于被动回复的 msg_id（5 分钟窗口内）。"""
        entry = self._last_msg_ids.get(chat_id)
        if entry is None:
            return None
        msg_id, ts = entry
        if time.monotonic() - ts > QQ_PASSIVE_REPLY_WINDOW:
            return None
        return msg_id or None  # 空字符串也返回 None

    def _cleanup_expired(self) -> None:
        """清理过期的 msg_id 缓存条目。"""
        now = time.monotonic()
        expired = [
            k for k, (_, ts) in self._last_msg_ids.items()
            if now - ts > QQ_PASSIVE_REPLY_WINDOW
        ]
        for k in expired:
            del self._last_msg_ids[k]
            self._msg_seq.pop(k, None)

    def _next_msg_seq(self, chat_id: str) -> int:
        """获取并递增 msg_seq（同一 msg_id 下的消息序号）。"""
        seq = self._msg_seq.get(chat_id, 1)
        self._msg_seq[chat_id] = seq + 1
        return seq

    # ── 生命周期 ──

    async def start(self) -> None:
        """启动 QQ Bot。实际由 handlers.py 管理生命周期。"""
        pass

    async def stop(self) -> None:
        """停止 QQ Bot。"""
        pass

    # ── 内部发送方法 ──

    async def _send_to_chat(self, chat_id: str, content: str) -> dict | None:
        """向指定 chat_id 发送文本消息。根据前缀路由到对应 API。

        botpy 在 HTTP 超时时静默返回 None（不抛异常），因此当结果为 None
        时进行应用层重试，使用指数退避避免雪崩。
        """
        if self._api is None:
            logger.warning("QQ Bot API 未初始化，无法发送消息")
            return None

        chat_type, target_id = parse_chat_id(chat_id)
        msg_id = self._get_reply_msg_id(chat_id)
        msg_seq = self._next_msg_seq(chat_id)

        last_exc: Exception | None = None
        for attempt in range(1 + QQ_SEND_MAX_RETRIES):
            try:
                if chat_type == "group":
                    result = await self._api.post_group_message(
                        group_openid=target_id,
                        msg_type=0,
                        content=content,
                        msg_id=msg_id,
                        msg_seq=msg_seq,
                    )
                elif chat_type == "c2c":
                    result = await self._api.post_c2c_message(
                        openid=target_id,
                        msg_type=0,
                        content=content,
                        msg_id=msg_id,
                        msg_seq=msg_seq,
                    )
                else:
                    result = await self._api.post_message(
                        channel_id=target_id,
                        content=content,
                        msg_id=msg_id,
                    )

                if result is not None:
                    return result

                # botpy 返回 None → 大概率超时，重试
                if attempt < QQ_SEND_MAX_RETRIES:
                    delay = QQ_SEND_RETRY_BASE_DELAY * (2 ** attempt)
                    logger.warning(
                        "QQ 发送消息返回空（可能超时），%0.1fs 后重试 (%d/%d) chat_id=%s",
                        delay, attempt + 1, QQ_SEND_MAX_RETRIES, chat_id,
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(
                        "QQ 发送消息失败（重试耗尽） chat_id=%s type=%s",
                        chat_id, chat_type,
                    )
            except Exception as exc:
                last_exc = exc
                if attempt < QQ_SEND_MAX_RETRIES:
                    delay = QQ_SEND_RETRY_BASE_DELAY * (2 ** attempt)
                    logger.warning(
                        "QQ 发送消息异常，%0.1fs 后重试 (%d/%d): %s",
                        delay, attempt + 1, QQ_SEND_MAX_RETRIES, exc,
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(
                        "QQ 发送消息失败 chat_id=%s type=%s",
                        chat_id, chat_type, exc_info=True,
                    )

        return None

    # ── 发送能力 ──

    async def send_text(self, chat_id: str, text: str) -> None:
        """发送纯文本消息，使用语义分块拆分长消息。"""
        for part in smart_chunk(text, QQ_MAX_MESSAGE_LEN, "plain"):
            await self._send_to_chat(chat_id, part)

    async def send_markdown(self, chat_id: str, text: str) -> None:
        """发送 Markdown 消息。QQ 对 Markdown 支持有限，降级为纯文本。"""
        # 剥离 HTML 标签（send_markdown 内容可能含 <b> 等 Telegram HTML 标记）
        plain = re.sub(r"<[^>]+>", "", text)
        await self.send_text(chat_id, plain)

    async def send_file(self, chat_id: str, data: bytes, filename: str) -> None:
        """发送文件。QQ 群/C2C 文件 API 需要 URL，暂降级为文本提示。"""
        size_kb = len(data) / 1024
        await self.send_text(
            chat_id,
            f"📎 文件已生成: {filename} ({size_kb:.1f} KB)\n"
            f"QQ 暂不支持直接发送文件，请通过 Web 界面下载。",
        )

    async def send_approval_card(
        self,
        chat_id: str,
        approval_id: str,
        tool_name: str,
        risk_level: str,
        args_summary: dict[str, str],
    ) -> None:
        """发送审批卡片，降级为纯文本 + 命令提示。"""
        risk_emoji = {"green": "🟢", "yellow": "🟡", "red": "🔴"}.get(
            risk_level.lower(), "⚠️"
        )

        args_text = ""
        if args_summary:
            lines = []
            for k, v in list(args_summary.items())[:5]:
                v_str = str(v)
                if len(v_str) > 80:
                    v_str = v_str[:77] + "..."
                lines.append(f"  {k}: {v_str}")
            args_text = "\n".join(lines)

        text = (
            f"🔒 操作审批\n\n"
            f"{risk_emoji} 风险等级: {risk_level.upper()}\n"
            f"📝 工具: {tool_name}"
        )
        if args_text:
            text += f"\n\n{args_text}"
        text += (
            f"\n\n回复 /approve {approval_id} 批准"
            f"\n回复 /reject {approval_id} 拒绝"
        )

        await self.send_text(chat_id, text)

    async def send_question_card(
        self,
        chat_id: str,
        question_id: str,
        header: str,
        text: str,
        options: list[dict[str, str]],
    ) -> None:
        """发送问答卡片，降级为纯文本编号选项。"""
        msg_text = "💬 ExcelManus 想确认：\n"
        if header:
            msg_text += f"\n{header}\n"
        if text:
            msg_text += f"\n{text}"

        if options:
            msg_text += "\n\n选项："
            for i, opt in enumerate(options, 1):
                label = opt.get("label", f"选项 {i}")
                msg_text += f"\n  {i}. {label}"
            msg_text += "\n\n回复编号或直接输入文字"
        else:
            msg_text += "\n\n直接回复文字即可"

        await self.send_text(chat_id, msg_text)

    async def show_typing(self, chat_id: str) -> None:
        """QQ 不支持 typing 指示器，空操作。"""
        pass

    async def send_text_return_id(
        self, chat_id: str, text: str, reply_to: str | None = None,
    ) -> str:
        """发送文本并返回消息 ID。长文本先分块，仅返回首条消息 ID。"""
        parts = smart_chunk(text, QQ_MAX_MESSAGE_LEN, "plain")
        first_id = ""
        for i, part in enumerate(parts):
            result = await self._send_to_chat(chat_id, part)
            if i == 0 and result and isinstance(result, dict):
                first_id = result.get("id", "")
        return first_id

    async def send_markdown_return_id(
        self, chat_id: str, text: str, reply_to: str | None = None,
    ) -> str:
        """发送 Markdown 并返回消息 ID（降级为纯文本）。"""
        plain = re.sub(r"<[^>]+>", "", text)
        return await self.send_text_return_id(chat_id, plain, reply_to)

    async def send_progress(self, chat_id: str, stage: str, message: str) -> None:
        """发送进度提示。"""
        await self.send_text(chat_id, f"⏳ [{stage}] {message}")
