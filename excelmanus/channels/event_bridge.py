"""EventBridge：跨渠道实时事件推送。

当任一渠道发起的 chat 产生审批/问答/状态事件时，通过 EventBridge 推送到已绑定的其他渠道，
使用户能在任意渠道收到通知并操作。

架构：
    api.py _on_event → EventBridge.notify(auth_user_id, event_type, data)
                         ↓
    MessageHandler 注册的回调 → adapter.send_approval_card / send_question_card / send_text

支持事件类型：
    - approval / question：审批/问答卡片推送（所有渠道接收）
    - approval_resolved：审批已处理，清除其他渠道的待处理状态
    - chat_started / chat_completed：跨渠道聊天状态通知（受会话去重过滤）

已知限制：
    - Bot→Web 方向无实时推送。Web 前端通过 SessionSync 轮询 (2-5s) 感知 Bot 发起的变更，
      包括新会话发现 (15s 间隔) 和 inFlight 状态变化。如需改善可考虑 WebSocket 推送。
    - 仅已绑定用户（auth_user_id）可接收事件，匿名用户不注册订阅。
"""

from __future__ import annotations

import asyncio
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
        """向指定用户的所有订阅回调并行推送事件。

        使用 asyncio.gather 并行执行所有回调，避免慢渠道阻塞其他渠道的通知。

        Returns:
            成功投递的回调数量。
        """
        subs = self._subs.get(auth_user_id)
        if not subs:
            return 0

        snapshot = list(subs)  # 复制列表，防止回调中修改

        if len(snapshot) == 1:
            # 单订阅快速路径：无需 gather 开销
            try:
                await snapshot[0].callback(event_type, data)
                delivered = 1
            except Exception:
                logger.warning(
                    "EventBridge: callback failed for user=%s channel=%s",
                    auth_user_id, snapshot[0].channel,
                    exc_info=True,
                )
                delivered = 0
        else:
            # 多订阅并行执行
            results = await asyncio.gather(
                *(sub.callback(event_type, data) for sub in snapshot),
                return_exceptions=True,
            )
            delivered = 0
            for sub, result in zip(snapshot, results):
                if isinstance(result, BaseException):
                    logger.warning(
                        "EventBridge: callback failed for user=%s channel=%s: %s",
                        auth_user_id, sub.channel, result,
                        exc_info=(type(result), result, result.__traceback__),
                    )
                else:
                    delivered += 1

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
