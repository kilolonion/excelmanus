"""Telegram 渠道适配器：实现 ChannelAdapter 接口。"""

from __future__ import annotations

import logging
import os
from pathlib import Path, PurePosixPath

from excelmanus.channels.base import ChannelAdapter, ChannelCapabilities
from excelmanus.channels.chunking import smart_chunk

logger = logging.getLogger("excelmanus.channels.telegram")

# Telegram 消息长度上限
TG_MAX_MESSAGE_LEN = 4096


class TelegramAdapter(ChannelAdapter):
    """Telegram Bot 适配器。

    通过 python-telegram-bot 库实现消息收发，
    将 Telegram 特有的 Update/Context 转换为通用 ChannelMessage。
    """

    name = "telegram"
    capabilities = ChannelCapabilities(
        supports_edit=True,
        supports_reply_chain=True,
        supports_typing=True,
        max_message_length=TG_MAX_MESSAGE_LEN,
        max_edits_per_minute=20,
        preferred_format="html",
    )

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
        """发送纯文本消息，使用语义分块拆分长消息。"""
        if self._app is None:
            logger.warning("send_text 失败: _app 未初始化 (chat_id=%s)", chat_id)
            return
        bot = self._app.bot
        for part in smart_chunk(text, TG_MAX_MESSAGE_LEN, "plain"):
            try:
                await bot.send_message(chat_id=int(chat_id), text=part)
            except Exception:
                logger.error("send_message 失败 (chat_id=%s, text_len=%d)", chat_id, len(part), exc_info=True)
                raise

    async def send_markdown(self, chat_id: str, text: str) -> None:
        """发送 Markdown 消息，优先尝试 HTML，失败时降级纯文本。"""
        if self._app is None:
            return
        from telegram.constants import ParseMode

        bot = self._app.bot
        for part in smart_chunk(text, TG_MAX_MESSAGE_LEN, "html"):
            try:
                await bot.send_message(
                    chat_id=int(chat_id),
                    text=part,
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                # HTML 解析失败 → 尝试 Markdown
                try:
                    plain_part = smart_chunk(part, TG_MAX_MESSAGE_LEN, "plain")[0]
                    await bot.send_message(chat_id=int(chat_id), text=plain_part)
                except Exception:
                    await bot.send_message(chat_id=int(chat_id), text=part)

    async def send_file(self, chat_id: str, data: bytes, filename: str) -> None:
        """发送文件给用户。data 为文件内容字节。"""
        if self._app is None:
            raise RuntimeError("Telegram app 未初始化，无法发送文件")
        import io
        bot = self._app.bot
        buf = io.BytesIO(data)
        buf.name = filename
        await bot.send_document(
            chat_id=int(chat_id),
            document=buf,
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

    async def send_text_return_id(
        self, chat_id: str, text: str, reply_to: str | None = None,
    ) -> str:
        """发送文本并返回消息 ID。"""
        if self._app is None:
            return ""
        bot = self._app.bot
        kwargs: dict = {"chat_id": int(chat_id), "text": text}
        if reply_to:
            kwargs["reply_to_message_id"] = int(reply_to)
        msg = await bot.send_message(**kwargs)
        return str(msg.message_id)

    async def send_markdown_return_id(
        self, chat_id: str, text: str, reply_to: str | None = None,
    ) -> str:
        """发送 HTML 格式消息并返回消息 ID，失败降级纯文本。"""
        if self._app is None:
            return ""
        from telegram.constants import ParseMode

        bot = self._app.bot
        kwargs: dict = {"chat_id": int(chat_id), "text": text}
        if reply_to:
            kwargs["reply_to_message_id"] = int(reply_to)
        try:
            msg = await bot.send_message(parse_mode=ParseMode.HTML, **kwargs)
        except Exception:
            msg = await bot.send_message(**kwargs)
        return str(msg.message_id)

    async def edit_text(
        self, chat_id: str, message_id: str, text: str,
    ) -> bool:
        """编辑已发送的纯文本消息。"""
        if self._app is None:
            return False
        try:
            await self._app.bot.edit_message_text(
                chat_id=int(chat_id),
                message_id=int(message_id),
                text=text,
            )
            return True
        except Exception:
            logger.debug("edit_text failed chat=%s msg=%s", chat_id, message_id, exc_info=True)
            return False

    async def edit_markdown(
        self, chat_id: str, message_id: str, text: str,
    ) -> bool:
        """编辑已发送的 HTML 格式消息，失败降级为纯文本编辑。"""
        if self._app is None:
            return False
        from telegram.constants import ParseMode

        try:
            await self._app.bot.edit_message_text(
                chat_id=int(chat_id),
                message_id=int(message_id),
                text=text,
                parse_mode=ParseMode.HTML,
            )
            return True
        except Exception:
            # HTML 解析失败 → 纯文本降级
            try:
                await self._app.bot.edit_message_text(
                    chat_id=int(chat_id),
                    message_id=int(message_id),
                    text=text,
                )
                return True
            except Exception:
                logger.debug("edit_markdown failed chat=%s msg=%s", chat_id, message_id, exc_info=True)
                return False

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

    async def send_staged_card(
        self,
        chat_id: str,
        files: list[dict],
        pending_count: int,
        session_id: str,
    ) -> None:
        """发送 staged 文件摘要卡片，带 InlineKeyboard 按钮。"""
        if self._app is None:
            return
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        lines = [f"📦 {pending_count} 个文件待确认:\n"]
        for i, f in enumerate(files[:10], 1):
            name = PurePosixPath(f.get("original_path", "?")).name
            summary = f.get("summary")
            detail = ""
            if summary:
                parts = []
                if summary.get("cells_changed"):
                    parts.append(f"~{summary['cells_changed']}")
                if summary.get("cells_added"):
                    parts.append(f"+{summary['cells_added']}")
                if summary.get("cells_removed"):
                    parts.append(f"-{summary['cells_removed']}")
                if summary.get("sheets_added"):
                    parts.append(f"+{len(summary['sheets_added'])} sheet")
                delta = summary.get("size_delta_bytes", 0)
                if delta and not parts:
                    parts.append(f"Δ{delta:+d}B")
                if parts:
                    detail = f"  ({', '.join(parts)})"
            lines.append(f"  {i}. {name}{detail}")
        text = "\n".join(lines)

        # 构建按钮
        keyboard_rows = []
        if pending_count <= 3:
            for i in range(min(pending_count, len(files))):
                name = PurePosixPath(files[i].get("original_path", "?")).name
                short_name = name[:20] if len(name) > 20 else name
                keyboard_rows.append([
                    InlineKeyboardButton(
                        f"✅ {short_name}",
                        callback_data=f"apply_staged:{session_id}:{i}",
                    ),
                    InlineKeyboardButton(
                        f"❌ {short_name}",
                        callback_data=f"discard_staged:{session_id}:{i}",
                    ),
                ])
        # 全量按钮（始终显示）
        keyboard_rows.append([
            InlineKeyboardButton(
                "✅ 全部应用",
                callback_data=f"apply_staged:{session_id}:all",
            ),
            InlineKeyboardButton(
                "❌ 全部丢弃",
                callback_data=f"discard_staged:{session_id}:all",
            ),
        ])

        await self._app.bot.send_message(
            chat_id=int(chat_id),
            text=text,
            reply_markup=InlineKeyboardMarkup(keyboard_rows),
        )
