"""QQ Bot handler 注册：将 botpy 的事件回调转为 ChannelMessage。

此模块是 botpy SDK 与通用 MessageHandler 之间的桥梁。
支持三种消息场景：QQ群 @机器人、C2C 私聊、QQ频道 @机器人。
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import re

import httpx

from excelmanus.channels.api_client import ExcelManusAPIClient
from excelmanus.channels.base import (
    ChannelMessage,
    ChannelUser,
    FileAttachment,
    ImageAttachment,
)
from excelmanus.channels.message_handler import MessageHandler
from excelmanus.channels.qq.adapter import (
    PREFIX_C2C,
    PREFIX_GROUP,
    PREFIX_GUILD,
    QQBotAdapter,
)
from excelmanus.channels.rate_limit import RateLimitConfig
from excelmanus.channels.session_store import SessionStore

logger = logging.getLogger("excelmanus.channels.qq.handlers")

# 匹配 /command 格式（QQ 消息中可能带 @机器人 前缀，需要清理）
_CMD_PATTERN = re.compile(r"^/(\w+)(?:\s+(.*))?$", re.DOTALL)

# botpy 的 @ 清理正则（群消息中 @机器人 的内容）
_AT_CLEAN = re.compile(r"<@!\d+>\s*")

# 附件下载共享 HTTP 客户端（模块级复用，避免每次创建/销毁）
_download_client: httpx.AsyncClient | None = None


_DOWNLOAD_MAX_RETRIES = 3
_DOWNLOAD_BACKOFF_BASE = 1.5  # 秒
_DOWNLOAD_BACKOFF_MAX = 10.0  # 秒
_DOWNLOAD_RETRYABLE_STATUS = {429, 502, 503, 504}


def _get_download_client() -> httpx.AsyncClient:
    """获取或创建附件下载用 HTTP 客户端。"""
    global _download_client
    if _download_client is None or _download_client.is_closed:
        _download_client = httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, connect=15.0),
            follow_redirects=True,
        )
    return _download_client


async def _download_attachment(url: str) -> bytes | None:
    """从 QQ CDN 下载附件内容，带指数退避重试。失败时返回 None。"""
    if not url:
        return None
    # QQ 附件 URL 可能不含 scheme
    if url.startswith("/"):
        url = f"https://multimedia.nt.qq.com.cn{url}"
    elif not url.startswith("http"):
        url = f"https://{url}"

    client = _get_download_client()
    last_exc: Exception | None = None
    for attempt in range(_DOWNLOAD_MAX_RETRIES):
        try:
            resp = await client.get(url)
            if resp.status_code in _DOWNLOAD_RETRYABLE_STATUS:
                retry_after = float(resp.headers.get("Retry-After", 0))
                delay = max(retry_after, _DOWNLOAD_BACKOFF_BASE * (2 ** attempt))
                delay = min(delay, _DOWNLOAD_BACKOFF_MAX) + random.uniform(0, 0.5)
                logger.info("QQ 附件下载收到 %s，第 %d 次重试，%.1fs 后重试", resp.status_code, attempt + 1, delay)
                await asyncio.sleep(delay)
                continue
            resp.raise_for_status()
            return resp.content
        except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError, httpx.ReadError) as exc:
            last_exc = exc
            delay = min(_DOWNLOAD_BACKOFF_BASE * (2 ** attempt), _DOWNLOAD_BACKOFF_MAX) + random.uniform(0, 0.5)
            logger.info("QQ 附件下载网络异常 (%s)，第 %d 次重试，%.1fs 后重试", type(exc).__name__, attempt + 1, delay)
            await asyncio.sleep(delay)
        except Exception as exc:
            logger.warning("下载 QQ 附件失败（不可重试）: %s", url, exc_info=True)
            return None

    logger.warning("下载 QQ 附件失败（已重试 %d 次）: %s — %s", _DOWNLOAD_MAX_RETRIES, url, last_exc)
    return None


def _extract_attachments(message) -> list[dict]:
    """从 botpy 消息对象提取附件元数据。

    botpy 消息的 attachments 字段结构::

        [{"content_type": "image/png", "filename": "...", "url": "...", ...}]

    Returns:
        附件元数据列表，由 ``_resolve_attachments`` 异步下载后转为
        FileAttachment / ImageAttachment。
    """
    raw_attachments = getattr(message, "attachments", None) or []
    attachment_metas: list[dict] = []
    for att in raw_attachments:
        if isinstance(att, dict):
            attachment_metas.append(att)
        else:
            attachment_metas.append({
                "url": getattr(att, "url", ""),
                "content_type": getattr(att, "content_type", ""),
                "filename": getattr(att, "filename", ""),
                "size": getattr(att, "size", 0),
            })
    return attachment_metas


async def _resolve_attachments(
    attachment_metas: list[dict],
) -> tuple[list[FileAttachment], list[ImageAttachment]]:
    """异步下载附件数据并归一化为 FileAttachment / ImageAttachment。"""
    files: list[FileAttachment] = []
    images: list[ImageAttachment] = []
    for meta in attachment_metas:
        url = meta.get("url", "")
        content_type = meta.get("content_type", "application/octet-stream") or "application/octet-stream"
        filename = meta.get("filename", "")
        if not url:
            continue
        data = await _download_attachment(url)
        if data is None:
            continue
        if not filename:
            # 从 content_type 推断默认文件名
            ext = content_type.split("/")[-1].split(";")[0].strip()
            filename = f"attachment.{ext}"
        files.append(FileAttachment(filename=filename, data=data, mime_type=content_type))
        if content_type.startswith("image/"):
            images.append(ImageAttachment(data=data, media_type=content_type))
    return files, images


def _clean_at_prefix(text: str) -> str:
    """清除消息开头的 @机器人 标记。"""
    return _AT_CLEAN.sub("", text).strip()


def _parse_command(text: str) -> tuple[bool, str, list[str]]:
    """解析 /command arg1 arg2 格式。

    Returns:
        (is_command, command_name, args)
    """
    m = _CMD_PATTERN.match(text.strip())
    if not m:
        return False, "", []
    cmd = m.group(1)
    raw_args = m.group(2) or ""
    args = raw_args.split() if raw_args.strip() else []
    return True, cmd, args


def build_qq_app(
    app_id: str | None = None,
    secret: str | None = None,
    api_url: str | None = None,
    allowed_users: set[str] | None = None,
    session_store: SessionStore | None = None,
    rate_limit_config: RateLimitConfig | None = None,
    bind_manager: object | None = None,
    service_token: str | None = None,
    is_sandbox: bool = False,
    config_store: object | None = None,
    timeout: int = 20,
):
    """构建 QQ Bot Client，注册所有事件 handler。

    Returns:
        (client, adapter, handler) 三元组。
    """
    import botpy

    _app_id = app_id or os.environ.get("EXCELMANUS_QQ_APPID", "")
    _secret = secret or os.environ.get("EXCELMANUS_QQ_SECRET", "")
    if not _app_id or not _secret:
        raise ValueError(
            "QQ Bot 凭据未设置。请设置 EXCELMANUS_QQ_APPID 和 EXCELMANUS_QQ_SECRET 环境变量。"
        )

    # 初始化组件
    adapter = QQBotAdapter(app_id=_app_id, secret=_secret)
    api_client = ExcelManusAPIClient(api_url=api_url, service_token=service_token)
    store = session_store or SessionStore()
    handler = MessageHandler(
        adapter=adapter,
        api_client=api_client,
        session_store=store,
        allowed_users=allowed_users,
        rate_limit_config=rate_limit_config,
        bind_manager=bind_manager,
        config_store=config_store,
    )

    # ── 定义 botpy Client 子类 ──

    class ExcelManusQQClient(botpy.Client):
        """ExcelManus QQ Bot Client。"""

        async def on_ready(self):
            logger.info("QQ Bot 已就绪: %s", self.robot.name)
            logger.info("QQ Bot intents: %s", self.intents)
            # 注入 BotAPI 到 adapter
            adapter.set_api(self.api)

        # ── QQ群 @机器人消息 ──

        async def on_group_at_message_create(self, message):
            """群聊中 @机器人 的消息。"""
            logger.info("收到群消息: group=%s content=%s", getattr(message, "group_openid", "?"), getattr(message, "content", "")[:50])
            try:
                await _handle_group_message(adapter, handler, message)
            except Exception:
                logger.error("处理群消息异常", exc_info=True)

        # ── C2C 私聊消息 ──

        async def on_c2c_message_create(self, message):
            """C2C 私聊消息。"""
            logger.info("收到私聊消息: user=%s content=%s", getattr(getattr(message, "author", None), "user_openid", "?"), getattr(message, "content", "")[:50])
            try:
                await _handle_c2c_message(adapter, handler, message)
            except Exception:
                logger.error("处理私聊消息异常", exc_info=True)

        # ── QQ频道 @机器人消息 ──

        async def on_at_message_create(self, message):
            """频道中 @机器人 的消息。"""
            logger.info("收到频道消息: channel=%s content=%s", getattr(message, "channel_id", "?"), getattr(message, "content", "")[:50])
            try:
                await _handle_guild_message(adapter, handler, message)
            except Exception:
                logger.error("处理频道消息异常", exc_info=True)

        # ── 机器人生命周期事件 ──

        async def on_group_add_robot(self, event):
            logger.info("机器人加入群聊: %s", getattr(event, "group_openid", "?"))

        async def on_group_del_robot(self, event):
            logger.info("机器人退出群聊: %s", getattr(event, "group_openid", "?"))

        async def on_friend_add(self, event):
            logger.info("用户添加机器人: %s", getattr(event, "openid", "?"))

        async def on_friend_del(self, event):
            logger.info("用户删除机器人: %s", getattr(event, "openid", "?"))

    # 构建 Client
    intents = botpy.Intents(
        public_messages=True,        # 群/C2C 公域消息
        public_guild_messages=True,  # 频道公域消息
    )
    client = ExcelManusQQClient(
        intents=intents,
        timeout=timeout,
        is_sandbox=is_sandbox,
        bot_log=None,  # 禁用 botpy 内置日志，使用项目统一日志
    )

    return client, adapter, handler


# ── 消息处理函数 ──


async def _handle_group_message(
    adapter: QQBotAdapter,
    handler: MessageHandler,
    message,
) -> None:
    """处理 QQ 群 @机器人消息。"""
    group_openid = getattr(message, "group_openid", None)
    if not group_openid:
        return

    msg_id = getattr(message, "id", "")
    content = getattr(message, "content", "") or ""
    content = _clean_at_prefix(content)

    author = getattr(message, "author", None)
    user_openid = getattr(author, "member_openid", "") if author else ""
    if not user_openid:
        return

    # 提取附件（图片/文件）
    attachment_metas = _extract_attachments(message)
    files, images = await _resolve_attachments(attachment_metas) if attachment_metas else ([], [])

    # 纯文本为空且无附件时跳过
    if not content.strip() and not files and not images:
        return

    chat_id = f"{PREFIX_GROUP}{group_openid}"
    adapter.record_incoming_msg(chat_id, msg_id)

    is_cmd, cmd, args = _parse_command(content) if content.strip() else (False, "", [])
    msg = ChannelMessage(
        channel="qq",
        user=ChannelUser(
            user_id=user_openid,
            username="",
            display_name="",
        ),
        chat_id=chat_id,
        text=content,
        files=files,
        images=images,
        is_command=is_cmd,
        command=cmd,
        command_args=args,
        raw=message,
        chat_type="group",
    )
    await handler.handle_message(msg)


async def _handle_c2c_message(
    adapter: QQBotAdapter,
    handler: MessageHandler,
    message,
) -> None:
    """处理 C2C 私聊消息。"""
    author = getattr(message, "author", None)
    user_openid = getattr(author, "user_openid", "") if author else ""
    if not user_openid:
        return

    msg_id = getattr(message, "id", "")
    content = getattr(message, "content", "") or ""
    content = content.strip()

    # 提取附件（图片/文件）
    attachment_metas = _extract_attachments(message)
    files, images = await _resolve_attachments(attachment_metas) if attachment_metas else ([], [])

    # 纯文本为空且无附件时跳过
    if not content and not files and not images:
        return

    chat_id = f"{PREFIX_C2C}{user_openid}"
    adapter.record_incoming_msg(chat_id, msg_id)

    is_cmd, cmd, args = _parse_command(content) if content else (False, "", [])
    msg = ChannelMessage(
        channel="qq",
        user=ChannelUser(
            user_id=user_openid,
            username="",
            display_name="",
        ),
        chat_id=chat_id,
        text=content,
        files=files,
        images=images,
        is_command=is_cmd,
        command=cmd,
        command_args=args,
        raw=message,
        chat_type="private",
    )
    await handler.handle_message(msg)


async def _handle_guild_message(
    adapter: QQBotAdapter,
    handler: MessageHandler,
    message,
) -> None:
    """处理 QQ 频道 @机器人消息。"""
    channel_id = getattr(message, "channel_id", None)
    if not channel_id:
        return

    msg_id = getattr(message, "id", "")
    content = getattr(message, "content", "") or ""
    content = _clean_at_prefix(content)

    author = getattr(message, "author", None)
    user_id = getattr(author, "id", "") if author else ""
    username = getattr(author, "username", "") if author else ""
    if not user_id:
        return

    # 提取附件（图片/文件）
    attachment_metas = _extract_attachments(message)
    files, images = await _resolve_attachments(attachment_metas) if attachment_metas else ([], [])

    # 纯文本为空且无附件时跳过
    if not content.strip() and not files and not images:
        return

    chat_id = f"{PREFIX_GUILD}{channel_id}"
    adapter.record_incoming_msg(chat_id, msg_id)

    is_cmd, cmd, args = _parse_command(content) if content.strip() else (False, "", [])
    msg = ChannelMessage(
        channel="qq",
        user=ChannelUser(
            user_id=user_id,
            username=username,
            display_name=username,
        ),
        chat_id=chat_id,
        text=content,
        files=files,
        images=images,
        is_command=is_cmd,
        command=cmd,
        command_args=args,
        raw=message,
        chat_type="channel",
    )
    await handler.handle_message(msg)


def run_qq_bot(
    app_id: str | None = None,
    secret: str | None = None,
    api_url: str | None = None,
    allowed_users: set[str] | None = None,
    service_token: str | None = None,
) -> None:
    """一键启动 QQ Bot（阻塞运行）。"""
    _app_id = app_id or os.environ.get("EXCELMANUS_QQ_APPID", "")
    _secret = secret or os.environ.get("EXCELMANUS_QQ_SECRET", "")

    client, adapter, handler = build_qq_app(
        app_id=_app_id,
        secret=_secret,
        api_url=api_url,
        allowed_users=allowed_users,
        service_token=service_token,
    )

    logger.info("ExcelManus QQ Bot 启动中...")
    logger.info("API: %s", handler.api.api_url)
    if allowed_users:
        logger.info("允许的用户: %s", allowed_users)
    else:
        logger.info("⚠️ 未设置用户限制，所有人可用")

    client.run(appid=_app_id, secret=_secret)
