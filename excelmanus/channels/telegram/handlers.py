"""Telegram handler 注册：将 python-telegram-bot 的 Update/Context 转为 ChannelMessage。

此模块是 Telegram SDK 与通用 MessageHandler 之间的桥梁。
"""

from __future__ import annotations

import logging
import os

from excelmanus.channels.api_client import ExcelManusAPIClient
from excelmanus.channels.base import ChannelMessage, ChannelUser, FileAttachment
from excelmanus.channels.message_handler import MessageHandler
from excelmanus.channels.session_store import SessionStore
from excelmanus.channels.telegram.adapter import TelegramAdapter

logger = logging.getLogger("channels.telegram.handlers")


def _make_user(update) -> ChannelUser:
    """从 Telegram Update 提取用户信息。"""
    user = update.effective_user
    if user is None:
        return ChannelUser(user_id="0")
    return ChannelUser(
        user_id=str(user.id),
        username=user.username or "",
        display_name=user.full_name or "",
    )


def _parse_command(text: str) -> tuple[str, list[str]]:
    """解析 /command arg1 arg2 格式。"""
    parts = text.strip().split()
    cmd = parts[0].lstrip("/").split("@")[0]  # 去除 @botname 后缀
    args = parts[1:] if len(parts) > 1 else []
    return cmd, args


def build_telegram_app(
    token: str | None = None,
    api_url: str | None = None,
    allowed_users: set[str] | None = None,
    session_store: SessionStore | None = None,
):
    """构建 Telegram Application，注册所有 handler。

    Returns:
        (app, adapter, handler) 三元组。
    """
    from telegram import BotCommand, Update
    from telegram.ext import (
        Application,
        CallbackQueryHandler,
        CommandHandler,
        ContextTypes,
        MessageHandler as TGMessageHandler,
        filters,
    )

    _token = token or os.environ.get("EXCELMANUS_TG_TOKEN", "")
    if not _token:
        raise ValueError("Telegram Bot Token 未设置。请设置 EXCELMANUS_TG_TOKEN 环境变量。")

    # 初始化组件
    adapter = TelegramAdapter(token=_token)
    api_client = ExcelManusAPIClient(api_url=api_url)
    store = session_store or SessionStore()
    handler = MessageHandler(
        adapter=adapter,
        api_client=api_client,
        session_store=store,
        allowed_users=allowed_users,
    )

    # ── handler 回调 ──

    async def _on_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.message:
            return
        text = update.message.text or ""
        cmd, args = _parse_command(text)
        msg = ChannelMessage(
            channel="telegram",
            user=_make_user(update),
            chat_id=str(update.effective_chat.id),
            text=text,
            is_command=True,
            command=cmd,
            command_args=args,
            raw=update,
        )
        await handler.handle_message(msg)

    async def _on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.message:
            return
        text = update.message.text or update.message.caption or ""
        if not text.strip():
            return

        # 设置 reaction 表情
        try:
            await update.message.set_reaction("👀")
        except Exception:
            pass

        msg = ChannelMessage(
            channel="telegram",
            user=_make_user(update),
            chat_id=str(update.effective_chat.id),
            text=text,
            raw=update,
        )
        await handler.handle_message(msg)

        try:
            await update.message.set_reaction("⚡")
        except Exception:
            pass

    async def _on_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.message or not update.message.document:
            return
        doc = update.message.document
        filename = doc.file_name or "uploaded_file"

        # 下载文件
        tg_file = await doc.get_file()
        file_bytes = await tg_file.download_as_bytearray()

        try:
            await update.message.set_reaction("📎")
        except Exception:
            pass

        caption = update.message.caption or ""
        msg = ChannelMessage(
            channel="telegram",
            user=_make_user(update),
            chat_id=str(update.effective_chat.id),
            text=caption,
            files=[FileAttachment(
                filename=filename,
                data=bytes(file_bytes),
                mime_type=doc.mime_type or "application/octet-stream",
            )],
            raw=update,
        )
        await handler.handle_message(msg)

    async def _on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if not query or not query.data:
            return
        await query.answer()

        msg = ChannelMessage(
            channel="telegram",
            user=_make_user(update),
            chat_id=str(query.message.chat_id),
            callback_data=query.data,
            raw=update,
        )
        await handler.handle_message(msg)

    # ── 构建 Application ──

    async def _post_init(app: Application) -> None:
        adapter.set_app(app)
        await app.bot.set_my_commands([
            BotCommand("start", "开始使用"),
            BotCommand("help", "查看所有命令"),
            BotCommand("new", "新建对话"),
            BotCommand("model", "查看/切换模型"),
            BotCommand("addmodel", "添加模型"),
            BotCommand("delmodel", "删除模型"),
            BotCommand("abort", "终止当前任务"),
        ])

    from telegram.request import HTTPXRequest

    request = HTTPXRequest(
        connect_timeout=20.0,
        read_timeout=30.0,
        write_timeout=30.0,
        pool_timeout=10.0,
    )
    app = (
        Application.builder()
        .token(_token)
        .request(request)
        .post_init(_post_init)
        .build()
    )

    # 注册 handler（顺序重要）
    commands = ["start", "help", "new", "model", "addmodel", "delmodel", "abort"]
    for cmd_name in commands:
        app.add_handler(CommandHandler(cmd_name, _on_command))
    app.add_handler(CallbackQueryHandler(_on_callback))
    app.add_handler(TGMessageHandler(filters.Document.ALL, _on_document))
    app.add_handler(TGMessageHandler(filters.TEXT & ~filters.COMMAND, _on_text))

    return app, adapter, handler


def run_telegram_bot(
    token: str | None = None,
    api_url: str | None = None,
    allowed_users: set[str] | None = None,
) -> None:
    """一键启动 Telegram Bot（阻塞运行）。"""
    app, adapter, handler = build_telegram_app(
        token=token,
        api_url=api_url,
        allowed_users=allowed_users,
    )

    logger.info("ExcelManus Telegram Bot 启动中...")
    logger.info("API: %s", handler.api.api_url)
    if allowed_users:
        logger.info("允许的用户: %s", allowed_users)
    else:
        logger.info("⚠️ 未设置用户限制，所有人可用")

    app.run_polling(drop_pending_updates=True)
