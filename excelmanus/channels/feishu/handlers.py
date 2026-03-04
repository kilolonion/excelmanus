"""飞书 Webhook handler：将飞书事件回调转为 ChannelMessage。

此模块是飞书 SDK 与通用 MessageHandler 之间的桥梁。
支持两种接入模式：
  1. FastAPI 路由（推荐）：作为 webhook endpoint 注册到飞书事件订阅
  2. lark-oapi EventDispatcher：使用 SDK 内置的事件分发器

飞书事件文档: https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/im-v1/message/events/receive
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from excelmanus.channels.api_client import ExcelManusAPIClient
from excelmanus.channels.base import (
    ChannelMessage,
    ChannelUser,
    FileAttachment,
    ImageAttachment,
)
from excelmanus.channels.feishu.adapter import FeishuAdapter
from excelmanus.channels.message_handler import MessageHandler
from excelmanus.channels.rate_limit import RateLimitConfig
from excelmanus.channels.session_store import SessionStore

logger = logging.getLogger("excelmanus.channels.feishu.handlers")

# lark-oapi 延迟导入保护
_lark_available = False
try:
    from lark_oapi.api.im.v1 import (
        GetMessageResourceRequest,
    )
    _lark_available = True
except ImportError:
    pass


def _parse_command(text: str) -> tuple[bool, str, list[str]]:
    """解析 /command arg1 arg2 格式。

    Returns:
        (is_command, command, args)
    """
    text = text.strip()
    if not text.startswith("/"):
        return False, "", []
    parts = text.split()
    cmd = parts[0].lstrip("/")
    args = parts[1:] if len(parts) > 1 else []
    return True, cmd, args


def _extract_user(event: dict[str, Any]) -> ChannelUser:
    """从飞书事件中提取用户信息。"""
    sender = event.get("sender", {})
    sender_id = sender.get("sender_id", {})
    user_id = sender_id.get("open_id", "") or sender_id.get("user_id", "")
    return ChannelUser(
        user_id=user_id,
        username="",
        display_name=sender.get("sender_type", ""),
    )


def _extract_chat_id(event: dict[str, Any]) -> str:
    """从飞书事件中提取 chat_id。"""
    message = event.get("message", {})
    return message.get("chat_id", "")


async def _download_image(
    client: Any, message_id: str, image_key: str,
) -> bytes | None:
    """从飞书 API 下载图片内容。"""
    if not _lark_available or client is None:
        return None
    try:
        request = GetMessageResourceRequest.builder() \
            .message_id(message_id) \
            .file_key(image_key) \
            .type("image") \
            .build()
        resp = client.im.v1.message_resource.get(request)
        if not resp.success():
            logger.warning("飞书图片下载失败: code=%s msg=%s", resp.code, resp.msg)
            return None
        return resp.file.read() if resp.file else None
    except Exception:
        logger.warning("飞书图片下载异常: %s", image_key, exc_info=True)
        return None


async def _download_file(
    client: Any, message_id: str, file_key: str,
) -> bytes | None:
    """从飞书 API 下载文件内容。"""
    if not _lark_available or client is None:
        return None
    try:
        request = GetMessageResourceRequest.builder() \
            .message_id(message_id) \
            .file_key(file_key) \
            .type("file") \
            .build()
        resp = client.im.v1.message_resource.get(request)
        if not resp.success():
            logger.warning("飞书文件下载失败: code=%s msg=%s", resp.code, resp.msg)
            return None
        return resp.file.read() if resp.file else None
    except Exception:
        logger.warning("飞书文件下载异常: %s", file_key, exc_info=True)
        return None


async def handle_feishu_event(
    adapter: FeishuAdapter,
    handler: MessageHandler,
    event: dict[str, Any],
) -> None:
    """处理飞书 im.message.receive_v1 事件。

    将飞书消息事件转为 ChannelMessage，交给 MessageHandler 处理。
    """
    message = event.get("message", {})
    msg_type = message.get("message_type", "")
    message_id = message.get("message_id", "")
    chat_id = _extract_chat_id(event)
    user = _extract_user(event)

    if not chat_id or not user.user_id:
        logger.debug("飞书事件缺少 chat_id 或 user_id，跳过")
        return

    text = ""
    files: list[FileAttachment] = []
    images: list[ImageAttachment] = []

    # 解析消息内容
    content_str = message.get("content", "{}")
    try:
        content = json.loads(content_str) if isinstance(content_str, str) else content_str
    except json.JSONDecodeError:
        content = {}

    if msg_type == "text":
        text = content.get("text", "")

    elif msg_type == "image":
        image_key = content.get("image_key", "")
        if image_key and adapter.lark_client:
            data = await _download_image(adapter.lark_client, message_id, image_key)
            if data:
                images.append(ImageAttachment(data=data, media_type="image/png"))

    elif msg_type == "file":
        file_key = content.get("file_key", "")
        file_name = content.get("file_name", "uploaded_file")
        if file_key and adapter.lark_client:
            data = await _download_file(adapter.lark_client, message_id, file_key)
            if data:
                mime = "application/octet-stream"
                files.append(FileAttachment(filename=file_name, data=data, mime_type=mime))

    elif msg_type == "post":
        # 富文本消息 → 提取纯文本
        title = content.get("title", "")
        body_content = content.get("content", [])
        parts = [title] if title else []
        for line_items in body_content:
            for item in line_items:
                tag = item.get("tag", "")
                if tag == "text":
                    parts.append(item.get("text", ""))
                elif tag == "a":
                    parts.append(item.get("text", "") or item.get("href", ""))
                elif tag == "img":
                    image_key = item.get("image_key", "")
                    if image_key and adapter.lark_client:
                        img_data = await _download_image(adapter.lark_client, message_id, image_key)
                        if img_data:
                            images.append(ImageAttachment(data=img_data, media_type="image/png"))
        text = "\n".join(parts).strip()

    else:
        logger.debug("飞书不支持的消息类型: %s", msg_type)
        return

    # 空消息跳过
    if not text.strip() and not files and not images:
        return

    is_cmd, cmd, args = _parse_command(text) if text.strip() else (False, "", [])
    _chat_type_raw = message.get("chat_type", "p2p")
    channel_msg = ChannelMessage(
        channel="feishu",
        user=user,
        chat_id=chat_id,
        text=text,
        files=files,
        images=images,
        is_command=is_cmd,
        command=cmd,
        command_args=args,
        raw=event,
        chat_type="private" if _chat_type_raw == "p2p" else "group",
    )
    try:
        await handler.handle_message(channel_msg)
    except Exception:
        logger.error("飞书消息处理异常", exc_info=True)
        try:
            await adapter.send_text(chat_id, "❌ 处理消息时发生内部错误")
        except Exception:
            pass


async def handle_feishu_card_action(
    adapter: FeishuAdapter,
    handler: MessageHandler,
    action: dict[str, Any],
) -> None:
    """处理飞书卡片按钮回调。

    飞书卡片 action 结构:
    {
        "open_id": "ou_xxx",
        "open_message_id": "om_xxx",
        "open_chat_id": "oc_xxx",
        "action": {"value": {"action": "approve:xxx"}, "tag": "button"}
    }
    """
    user_id = action.get("open_id", "")
    chat_id = action.get("open_chat_id", "")
    action_data = action.get("action", {})
    value = action_data.get("value", {})
    callback_data = value.get("action", "") if isinstance(value, dict) else str(value)

    if not callback_data or not chat_id or not user_id:
        return

    msg = ChannelMessage(
        channel="feishu",
        user=ChannelUser(user_id=user_id),
        chat_id=chat_id,
        callback_data=callback_data,
        raw=action,
        chat_type="private",
    )
    try:
        await handler.handle_message(msg)
    except Exception:
        logger.error("飞书卡片回调处理异常", exc_info=True)
        try:
            await adapter.send_text(chat_id, "❌ 处理卡片回调时发生内部错误")
        except Exception:
            pass


def build_feishu_handler(
    app_id: str | None = None,
    app_secret: str | None = None,
    api_url: str | None = None,
    session_store: SessionStore | None = None,
    rate_limit_config: RateLimitConfig | None = None,
    bind_manager: object | None = None,
    service_token: str | None = None,
    event_bridge: object | None = None,
    config_store: object | None = None,
) -> tuple[FeishuAdapter, MessageHandler]:
    """构建飞书适配器和消息处理器。

    Returns:
        (adapter, handler) 二元组。
    """
    _app_id = app_id or os.environ.get("EXCELMANUS_FEISHU_APP_ID", "")
    _app_secret = app_secret or os.environ.get("EXCELMANUS_FEISHU_APP_SECRET", "")

    adapter = FeishuAdapter(app_id=_app_id, app_secret=_app_secret)
    api_client = ExcelManusAPIClient(api_url=api_url, service_token=service_token)
    store = session_store or SessionStore()
    msg_handler = MessageHandler(
        adapter=adapter,
        api_client=api_client,
        session_store=store,
        rate_limit_config=rate_limit_config,
        bind_manager=bind_manager,
        event_bridge=event_bridge,
        config_store=config_store,
    )
    return adapter, msg_handler
