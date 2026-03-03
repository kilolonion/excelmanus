"""飞书渠道适配器：实现 ChannelAdapter 接口。

飞书开放平台: https://open.feishu.cn/
推荐库: lark-oapi (飞书官方 SDK)

此文件为占位实现，后续接入时填充具体逻辑。
"""

from __future__ import annotations

import logging

from excelmanus.channels.base import ChannelAdapter

logger = logging.getLogger("channels.feishu")


class FeishuAdapter(ChannelAdapter):
    """飞书适配器（占位）。

    接入指南：
    1. 在飞书开放平台创建应用，获取 App ID + App Secret
    2. 启用机器人能力，配置事件订阅（接收消息等）
    3. pip install lark-oapi
    4. 实现以下抽象方法
    5. 在 handlers.py 中注册 webhook 回调，将飞书消息转为 ChannelMessage

    飞书特性：
    - 支持富文本卡片消息（Interactive Card）
    - 支持按钮交互（action block）
    - 文件通过上传 API + file_key 发送
    - 支持 Webhook 和长连接两种模式
    """

    name = "feishu"

    def __init__(self, app_id: str = "", app_secret: str = "", **kwargs) -> None:
        self.app_id = app_id
        self.app_secret = app_secret

    async def start(self) -> None:
        raise NotImplementedError("飞书适配器尚未实现，请参考 adapter.py 中的接入指南。")

    async def stop(self) -> None:
        raise NotImplementedError

    async def send_text(self, chat_id: str, text: str) -> None:
        raise NotImplementedError

    async def send_markdown(self, chat_id: str, text: str) -> None:
        raise NotImplementedError

    async def send_file(self, chat_id: str, file_path: str, filename: str) -> None:
        raise NotImplementedError

    async def send_approval_card(
        self,
        chat_id: str,
        approval_id: str,
        tool_name: str,
        risk_level: str,
        args_summary: dict[str, str],
    ) -> None:
        raise NotImplementedError

    async def send_question_card(
        self,
        chat_id: str,
        question_id: str,
        header: str,
        text: str,
        options: list[dict[str, str]],
    ) -> None:
        raise NotImplementedError

    async def show_typing(self, chat_id: str) -> None:
        raise NotImplementedError
