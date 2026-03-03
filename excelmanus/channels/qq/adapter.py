"""QQ Bot 渠道适配器：实现 ChannelAdapter 接口。

QQ 官方机器人 SDK: https://bot.q.qq.com/wiki/develop/api-v2/
推荐库: botpy (qq-botpy)

此文件为占位实现，后续接入时填充具体逻辑。
"""

from __future__ import annotations

import logging

from excelmanus.channels.base import ChannelAdapter

logger = logging.getLogger("channels.qq")


class QQBotAdapter(ChannelAdapter):
    """QQ Bot 适配器（占位）。

    接入指南：
    1. 在 QQ 开放平台申请机器人，获取 AppID + Token + Secret
    2. pip install qq-botpy
    3. 实现以下抽象方法
    4. 在 handlers.py 中注册事件回调，将 QQ 消息转为 ChannelMessage

    QQ Bot 特性：
    - 支持 Markdown 卡片消息（ark）
    - 支持按钮交互（inline keyboard）
    - 文件通过富媒体消息发送
    - 群聊需要 @机器人 触发
    """

    name = "qq"

    def __init__(self, app_id: str = "", token: str = "", secret: str = "", **kwargs) -> None:
        self.app_id = app_id
        self.token = token
        self.secret = secret

    async def start(self) -> None:
        raise NotImplementedError("QQ Bot 适配器尚未实现，请参考 adapter.py 中的接入指南。")

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
