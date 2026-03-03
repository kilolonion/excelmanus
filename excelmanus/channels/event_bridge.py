"""EventBridge：跨渠道实时事件推送。

当 Web 端发起的 chat 产生审批/问答事件时，通过 EventBridge 推送到已绑定的 Bot 渠道，
使 Bot 用户能收到通知并操作。

架构：
    api.py _on_event → EventBridge.notify(auth_user_id, event_type, data)
                         ↓
    MessageHandler 注册的回调 → adapter.send_approval_card / send_question_card
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Awaitable

logger = logging.getLogger("excelmanus.channels.event_bridge")

# 回调签名：async def callback(event_type: str, data: dict) -> None
BridgeCallback = Callable[[str, dict[str, Any]], Awaitable[None]]


@dataclass
class _Subscription:
    """单个订阅记录。"""
    channel: str
    chat_id: str
    callback: BridgeCallback


class EventBridge:
    """按 auth_user_id 路由事件到已注册的 Bot 渠道回调。

    线程安全：所有操作在同一事件循环中执行（asyncio 单线程模型）。
    """

    def __init__(self) -> None:
        self._subs: dict[str, list[_Subscription]] = {}

    def subscribe(
        self,
        auth_user_id: str,
        channel: str,
        chat_id: str,
        callback: BridgeCallback,
    ) -> None:
        """注册事件回调。同一 (user, channel, chat_id) 只保留最新回调。"""
        subs = self._subs.setdefault(auth_user_id, [])
        # 去重：替换相同 channel+chat_id 的旧回调
        for i, s in enumerate(subs):
            if s.channel == channel and s.chat_id == chat_id:
                subs[i] = _Subscription(channel=channel, chat_id=chat_id, callback=callback)
                return
        subs.append(_Subscription(channel=channel, chat_id=chat_id, callback=callback))
        logger.debug(
            "EventBridge: subscribed user=%s channel=%s chat=%s",
            auth_user_id, channel, chat_id,
        )

    def unsubscribe(
        self,
        auth_user_id: str,
        channel: str | None = None,
        chat_id: str | None = None,
    ) -> None:
        """取消订阅。

        行为：
        - channel=None: 取消该用户全部订阅
        - channel!=None 且 chat_id=None: 取消该用户该渠道全部订阅
        - channel!=None 且 chat_id!=None: 仅取消该用户该渠道下指定 chat 订阅
        """
        if channel is None:
            self._subs.pop(auth_user_id, None)
            return

        subs = self._subs.get(auth_user_id)
        if not subs:
            return

        if chat_id is None:
            kept = [s for s in subs if s.channel != channel]
        else:
            kept = [s for s in subs if not (s.channel == channel and s.chat_id == chat_id)]

        if kept:
            self._subs[auth_user_id] = kept
        else:
            del self._subs[auth_user_id]

    async def notify(
        self,
        auth_user_id: str,
        event_type: str,
        data: dict[str, Any],
    ) -> int:
        """向指定用户的所有订阅回调推送事件。

        Returns:
            成功投递的回调数量。
        """
        subs = self._subs.get(auth_user_id)
        if not subs:
            return 0

        delivered = 0
        for sub in list(subs):  # 复制列表，防止回调中修改
            try:
                await sub.callback(event_type, data)
                delivered += 1
            except Exception:
                logger.warning(
                    "EventBridge: callback failed for user=%s channel=%s",
                    auth_user_id, sub.channel,
                    exc_info=True,
                )
        if delivered:
            logger.debug(
                "EventBridge: notified user=%s event=%s delivered=%d",
                auth_user_id, event_type, delivered,
            )
        return delivered

    @property
    def subscription_count(self) -> int:
        """当前总订阅数。"""
        return sum(len(subs) for subs in self._subs.values())
