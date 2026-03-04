"""飞书渠道适配器：实现 ChannelAdapter 接口。

飞书开放平台: https://open.feishu.cn/
库: lark-oapi (飞书官方 SDK)

飞书特性：
- 支持富文本卡片消息（Interactive Card）
- 支持按钮交互（action block）
- 文件通过上传 API + file_key 发送
- 支持 Webhook 和长连接两种模式
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
from typing import Any

from excelmanus.channels.base import ChannelAdapter, ChannelCapabilities
from excelmanus.channels.chunking import smart_chunk

logger = logging.getLogger("excelmanus.channels.feishu")

# 飞书消息长度上限（卡片内容约 30000 字符，普通文本约 4000）
FEISHU_MAX_MESSAGE_LEN = 4000
# 卡片更新频率限制约 5 次/秒 → 每 0.5 秒一次
FEISHU_CARD_UPDATE_INTERVAL = 0.5

# lark-oapi 延迟导入保护
_lark_available = False
try:
    import lark_oapi as lark
    from lark_oapi.api.im.v1 import (
        CreateMessageRequest,
        CreateMessageRequestBody,
        CreateFileRequest,
        CreateFileRequestBody,
    )
    _lark_available = True
except ImportError:
    lark = None  # type: ignore[assignment]


class FeishuAdapter(ChannelAdapter):
    """飞书适配器。

    使用 lark-oapi SDK 与飞书开放平台交互。
    当 lark-oapi 未安装时，所有发送方法降级为日志记录（不抛异常）。
    """

    name = "feishu"
    capabilities = ChannelCapabilities(
        supports_edit=False,
        supports_card=True,
        supports_card_update=True,
        supports_reply_chain=True,
        supports_typing=False,
        supports_markdown_tables=True,
        max_message_length=FEISHU_MAX_MESSAGE_LEN,
        max_edits_per_minute=300,
        preferred_format="markdown",
    )

    def __init__(self, app_id: str = "", app_secret: str = "", **kwargs) -> None:
        self.app_id = app_id
        self.app_secret = app_secret
        self._client: Any | None = None

    def _ensure_client(self) -> bool:
        """确保 lark Client 已初始化。返回 False 表示 SDK 不可用。"""
        if not _lark_available:
            logger.warning("lark-oapi 未安装，飞书消息发送将被跳过。pip install lark-oapi")
            return False
        if self._client is None:
            if not self.app_id or not self.app_secret:
                logger.error("飞书凭据未设置 (app_id / app_secret)")
                return False
            self._client = lark.Client.builder() \
                .app_id(self.app_id) \
                .app_secret(self.app_secret) \
                .log_level(lark.LogLevel.WARNING) \
                .build()
        return True

    @property
    def lark_client(self) -> Any | None:
        """暴露内部 lark Client 实例（供 handlers 下载附件使用）。"""
        return self._client

    # ── 生命周期 ──

    async def start(self) -> None:
        """初始化飞书客户端。Webhook 模式下无需额外启动步骤。"""
        if not self._ensure_client():
            logger.warning("飞书适配器启动：lark-oapi 不可用或凭据缺失，将以降级模式运行")

    async def stop(self) -> None:
        """释放飞书客户端资源。"""
        self._client = None

    # ── 内部发送 ──

    async def _send_message(self, chat_id: str, msg_type: str, content: str) -> str:
        """发送飞书消息，lark-oapi 同步调用包装在 to_thread 中避免阻塞事件循环。"""
        if not self._ensure_client():
            return ""
        request = CreateMessageRequest.builder() \
            .receive_id_type("chat_id") \
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(chat_id)
                .msg_type(msg_type)
                .content(content)
                .build()
            ).build()
        try:
            response = await asyncio.to_thread(
                self._client.im.v1.message.create, request,
            )
            if not response.success():
                logger.error(
                    "飞书发送消息失败: code=%s msg=%s",
                    response.code, response.msg,
                )
                return ""
            return response.data.message_id if response.data else ""
        except Exception:
            logger.error("飞书发送消息异常", exc_info=True)
            return ""

    # ── 发送能力 ──

    async def send_text(self, chat_id: str, text: str) -> None:
        """发送纯文本消息，自动拆分长消息。"""
        for part in smart_chunk(text, FEISHU_MAX_MESSAGE_LEN, "plain"):
            content = json.dumps({"text": part}, ensure_ascii=False)
            await self._send_message(chat_id, "text", content)

    async def send_markdown(self, chat_id: str, text: str) -> None:
        """发送 Markdown 消息。飞书通过卡片渲染 Markdown (lark_md)。"""
        if not text.strip():
            return
        card = {
            "header": {
                "title": {"tag": "plain_text", "content": "ExcelManus"},
                "template": "blue",
            },
            "elements": [
                {
                    "tag": "div",
                    "text": {"tag": "lark_md", "content": text[:FEISHU_MAX_MESSAGE_LEN]},
                },
            ],
        }
        content = json.dumps(card, ensure_ascii=False)
        await self._send_message(chat_id, "interactive", content)

    async def send_file(self, chat_id: str, data: bytes, filename: str) -> None:
        """上传文件到飞书并发送文件消息。"""
        if not self._ensure_client():
            raise RuntimeError("飞书客户端未初始化，无法发送文件")
        try:
            # 1) 上传文件获取 file_key
            file_obj = io.BytesIO(data)
            request = CreateFileRequest.builder() \
                .request_body(
                    CreateFileRequestBody.builder()
                    .file_type("stream")
                    .file_name(filename)
                    .file(file_obj)
                    .build()
                ).build()
            resp = await asyncio.to_thread(
                self._client.im.v1.file.create, request,
            )
            if not resp.success():
                logger.error("飞书上传文件失败: code=%s msg=%s", resp.code, resp.msg)
                raise RuntimeError(f"飞书上传文件失败: code={resp.code}")
            file_key = resp.data.file_key

            # 2) 发送文件消息
            content = json.dumps({"file_key": file_key}, ensure_ascii=False)
            await self._send_message(chat_id, "file", content)
        except Exception:
            logger.error("飞书发送文件异常", exc_info=True)
            raise

    async def send_approval_card(
        self,
        chat_id: str,
        approval_id: str,
        tool_name: str,
        risk_level: str,
        args_summary: dict[str, str],
    ) -> None:
        """发送审批卡片（带批准/拒绝按钮）。"""
        risk_emoji = {"green": "🟢", "yellow": "🟡", "red": "🔴"}.get(
            risk_level.lower(), "⚠️"
        )
        args_lines = []
        for k, v in list(args_summary.items())[:5]:
            v_str = str(v)[:80]
            args_lines.append(f"**{k}**: {v_str}")
        args_md = "\n".join(args_lines) if args_lines else ""

        card = {
            "header": {
                "title": {"tag": "plain_text", "content": "🔒 操作审批"},
                "template": "orange" if risk_level == "yellow" else "red",
            },
            "elements": [
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": (
                            f"{risk_emoji} **风险等级**: {risk_level.upper()}\n"
                            f"📝 **工具**: {tool_name}\n\n{args_md}"
                        ),
                    },
                },
                {"tag": "hr"},
                {
                    "tag": "action",
                    "actions": [
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "✅ 批准"},
                            "type": "primary",
                            "value": {"action": f"approve:{approval_id}"},
                        },
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "❌ 拒绝"},
                            "type": "danger",
                            "value": {"action": f"reject:{approval_id}"},
                        },
                    ],
                },
            ],
        }
        content = json.dumps(card, ensure_ascii=False)
        await self._send_message(chat_id, "interactive", content)

    async def send_question_card(
        self,
        chat_id: str,
        question_id: str,
        header: str,
        text: str,
        options: list[dict[str, str]],
    ) -> None:
        """发送问答卡片（带选项按钮 + 自由回复提示）。"""
        elements: list[dict[str, Any]] = []
        md_content = "💬 ExcelManus 想确认：\n\n"
        if header:
            md_content += f"**{header}**\n\n"
        if text:
            md_content += f"{text}\n"
        elements.append({
            "tag": "div",
            "text": {"tag": "lark_md", "content": md_content},
        })

        if options:
            elements.append({"tag": "hr"})
            actions: list[dict[str, Any]] = []
            for i, opt in enumerate(options):
                label = opt.get("label", f"选项 {i + 1}")
                value = opt.get("value", label)
                actions.append({
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": label},
                    "type": "default",
                    "value": {"action": f"answer:{question_id}:{value}"},
                })
            elements.append({"tag": "action", "actions": actions})

        elements.append({
            "tag": "note",
            "elements": [
                {"tag": "plain_text", "content": "也可以直接输入文字回复"},
            ],
        })

        card = {
            "header": {
                "title": {"tag": "plain_text", "content": "❓ 请确认"},
                "template": "blue",
            },
            "elements": elements,
        }
        content = json.dumps(card, ensure_ascii=False)
        await self._send_message(chat_id, "interactive", content)

    async def show_typing(self, chat_id: str) -> None:
        """飞书不支持 typing 指示器，空操作。"""
        pass

    # ── 卡片消息（供 CardStreamStrategy 使用） ──

    async def send_card(self, chat_id: str, card: dict[str, Any]) -> str:
        """发送飞书卡片消息，返回 message_id。"""
        content = json.dumps(card, ensure_ascii=False)
        return await self._send_message(chat_id, "interactive", content)

    async def update_card(
        self, chat_id: str, message_id: str, card: dict[str, Any],
    ) -> bool:
        """更新已发送的卡片消息内容。"""
        if not self._ensure_client() or not message_id:
            return False
        try:
            from lark_oapi.api.im.v1 import PatchMessageRequest, PatchMessageRequestBody
            content = json.dumps(card, ensure_ascii=False)
            request = PatchMessageRequest.builder() \
                .message_id(message_id) \
                .request_body(
                    PatchMessageRequestBody.builder()
                    .content(content)
                    .build()
                ).build()
            resp = await asyncio.to_thread(
                self._client.im.v1.message.patch, request,
            )
            if not resp.success():
                logger.debug("飞书更新卡片失败: code=%s msg=%s", resp.code, resp.msg)
                return False
            return True
        except Exception:
            logger.debug("飞书更新卡片异常", exc_info=True)
            return False

    async def send_text_return_id(
        self, chat_id: str, text: str, reply_to: str | None = None,
    ) -> str:
        """发送文本并返回消息 ID。"""
        content = json.dumps({"text": text}, ensure_ascii=False)
        return await self._send_message(chat_id, "text", content)

    async def send_markdown_return_id(
        self, chat_id: str, text: str, reply_to: str | None = None,
    ) -> str:
        """发送 Markdown 卡片并返回消息 ID。"""
        card = {
            "header": {
                "title": {"tag": "plain_text", "content": "ExcelManus"},
                "template": "blue",
            },
            "elements": [
                {"tag": "div", "text": {"tag": "lark_md", "content": text[:FEISHU_MAX_MESSAGE_LEN]}},
            ],
        }
        content = json.dumps(card, ensure_ascii=False)
        return await self._send_message(chat_id, "interactive", content)
