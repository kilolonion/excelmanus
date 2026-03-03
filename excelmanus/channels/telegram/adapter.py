"""Telegram 渠道适配器：实现 ChannelAdapter 接口。"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from excelmanus.channels.base import ChannelAdapter

logger = logging.getLogger("channels.telegram")

# Telegram 消息长度上限
TG_MAX_MESSAGE_LEN = 4096


class TelegramAdapter(ChannelAdapter):
    """Telegram Bot 适配器。

    通过 python-telegram-bot 库实现消息收发，
    将 Telegram 特有的 Update/Context 转换为通用 ChannelMessage。
    """

    name = "telegram"

    def __init__(self, token: str = "", **kwargs) -> None:
        self.token = token or os.environ.get("EXCELMANUS_TG_TOKEN", "")
        self._app = None  # telegram.ext.Application

    async def start(self) -> None:
        """启动 Telegram Bot polling。由外部 handlers.py 调用。"""
        # 实际的 Application 构建和启动由 handlers.py 统一管理
        pass

    async def stop(self) -> None:
        """停止 Telegram Bot。"""
        if self._app is not None:
            await self._app.stop()
            await self._app.shutdown()

    def set_app(self, app) -> None:
        """注入 telegram.ext.Application 实例。"""
        self._app = app

    # ── 发送能力 ──

    async def send_text(self, chat_id: str, text: str) -> None:
        """发送纯文本消息，自动拆分长消息。"""
        if self._app is None:
            return
        bot = self._app.bot
        for part in self.split_message(text, TG_MAX_MESSAGE_LEN):
            await bot.send_message(chat_id=int(chat_id), text=part)

    async def send_markdown(self, chat_id: str, text: str) -> None:
        """发送 Markdown 消息，失败时降级纯文本。"""
        if self._app is None:
            return
        from telegram.constants import ParseMode

        bot = self._app.bot
        for part in self.split_message(text, TG_MAX_MESSAGE_LEN):
            try:
                await bot.send_message(
                    chat_id=int(chat_id),
                    text=part,
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception:
                await bot.send_message(chat_id=int(chat_id), text=part)

    async def send_file(self, chat_id: str, file_path: str, filename: str) -> None:
        """发送文件给用户。"""
        if self._app is None:
            return
        bot = self._app.bot
        path = Path(file_path)
        if not path.exists():
            await self.send_text(chat_id, f"⚠️ 文件不存在: {filename}")
            return
        with open(path, "rb") as f:
            await bot.send_document(
                chat_id=int(chat_id),
                document=f,
                filename=filename,
            )

    async def send_approval_card(
        self,
        chat_id: str,
        approval_id: str,
        tool_name: str,
        risk_level: str,
        args_summary: dict[str, str],
    ) -> None:
        """发送审批卡片，带 inline keyboard。"""
        if self._app is None:
            return
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

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

        text = f"🔒 操作审批\n\n{risk_emoji} 风险等级: {risk_level.upper()}\n📝 工具: {tool_name}"
        if args_text:
            text += f"\n\n{args_text}"

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ 批准", callback_data=f"approve:{approval_id}"),
                InlineKeyboardButton("❌ 拒绝", callback_data=f"reject:{approval_id}"),
            ]
        ])

        await self._app.bot.send_message(
            chat_id=int(chat_id),
            text=text,
            reply_markup=keyboard,
        )

    async def send_question_card(
        self,
        chat_id: str,
        question_id: str,
        header: str,
        text: str,
        options: list[dict[str, str]],
    ) -> None:
        """发送问答卡片，有选项时用 inline keyboard。"""
        if self._app is None:
            return
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        msg_text = "💬 ExcelManus 想确认：\n"
        if header:
            msg_text += f"\n{header}\n"
        if text:
            msg_text += f"\n{text}"

        bot = self._app.bot
        if options:
            keyboard_buttons = []
            for i, opt in enumerate(options):
                label = opt.get("label", f"选项 {i+1}")
                keyboard_buttons.append(
                    [InlineKeyboardButton(
                        label,
                        callback_data=f"answer:{question_id}:{label}",
                    )]
                )
            keyboard_buttons.append(
                [InlineKeyboardButton("💬 自由回复（直接发文字）", callback_data="noop")]
            )
            keyboard = InlineKeyboardMarkup(keyboard_buttons)
            await bot.send_message(
                chat_id=int(chat_id),
                text=msg_text,
                reply_markup=keyboard,
            )
        else:
            msg_text += "\n\n直接回复文字即可"
            await bot.send_message(chat_id=int(chat_id), text=msg_text)

    async def show_typing(self, chat_id: str) -> None:
        """发送 typing 指示器。"""
        if self._app is None:
            return
        from telegram.constants import ChatAction

        await self._app.bot.send_chat_action(
            chat_id=int(chat_id),
            action=ChatAction.TYPING,
        )

    async def send_progress(self, chat_id: str, stage: str, message: str) -> None:
        """发送进度提示。"""
        await self.send_text(chat_id, f"⏳ [{stage}] {message}")

    async def update_approval_result(
        self, chat_id: str, message_id: str, result_text: str,
    ) -> None:
        """更新审批消息，移除按钮。"""
        if self._app is None:
            return
        try:
            await self._app.bot.edit_message_reply_markup(
                chat_id=int(chat_id),
                message_id=int(message_id),
                reply_markup=None,
            )
        except Exception:
            pass

    async def update_question_result(
        self, chat_id: str, message_id: str, answer_text: str,
    ) -> None:
        """更新问答消息，移除按钮。"""
        if self._app is None:
            return
        try:
            await self._app.bot.edit_message_reply_markup(
                chat_id=int(chat_id),
                message_id=int(message_id),
                reply_markup=None,
            )
        except Exception:
            pass
