"""EventBridge：跨渠道实时事件推送。

当任一渠道发起的 chat 产生审批/问答/状态事件时，通过 EventBridge 推送到
其他渠道，使用户能在任意渠道收到通知并操作。

单用户架构下所有订阅共享同一进程。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

logger = logging.getLogger("excelmanus.channels.event_bridge")

BridgeCallback = Callable[[str, dict[str, Any]], Awaitable[None]]


@dataclass
class _Subscription:
    channel: str
    chat_id: str
    callback: BridgeCallback


class EventBridge:
    """进程级事件总线：向所有已注册渠道回调推送。"""

    def __init__(self) -> None:
        self._subs: list[_Subscription] = []

    def subscribe(
        self,
        channel: str,
        chat_id: str,
        callback: BridgeCallback,
    ) -> None:
        """注册事件回调。同一 (channel, chat_id) 只保留最新回调。"""
        for i, s in enumerate(self._subs):
            if s.channel == channel and s.chat_id == chat_id:
                self._subs[i] = _Subscription(channel=channel, chat_id=chat_id, callback=callback)
                return
        self._subs.append(_Subscription(channel=channel, chat_id=chat_id, callback=callback))
        logger.debug("EventBridge: subscribed channel=%s chat=%s", channel, chat_id)

    def unsubscribe(
        self,
        channel: str | None = None,
        chat_id: str | None = None,
    ) -> None:
        if channel is None:
            self._subs.clear()
            return
        if chat_id is None:
            self._subs = [s for s in self._subs if s.channel != channel]
        else:
            self._subs = [
                s for s in self._subs
                if not (s.channel == channel and s.chat_id == chat_id)
            ]

    async def notify(
        self,
        event_type: str,
        data: dict[str, Any],
    ) -> int:
        """向全部订阅回调并行推送事件。"""
        snapshot = list(self._subs)
        if not snapshot:
            return 0

        if len(snapshot) == 1:
            try:
                await snapshot[0].callback(event_type, data)
                return 1
            except Exception:
                logger.warning(
                    "EventBridge: callback failed for channel=%s",
                    snapshot[0].channel,
                    exc_info=True,
                )
                return 0

        results = await asyncio.gather(
            *[sub.callback(event_type, data) for sub in snapshot],
            return_exceptions=True,
        )
        delivered = 0
        for sub, result in zip(snapshot, results):
            if isinstance(result, Exception):
                logger.warning(
                    "EventBridge: callback failed for channel=%s: %s",
                    sub.channel,
                    result,
                )
            else:
                delivered += 1
        return delivered
