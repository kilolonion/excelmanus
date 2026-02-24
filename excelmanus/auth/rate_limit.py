"""多用户 API 端点的速率限制。

使用内存滑动窗口计数器。生产环境多 worker 部署时，
应切换为 Redis 后端存储。
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field

from fastapi import HTTPException, Request, status

logger = logging.getLogger(__name__)


@dataclass
class _WindowEntry:
    """在滑动窗口中追踪请求时间戳。"""
    timestamps: list[float] = field(default_factory=list)

    def count_in_window(self, window_seconds: float) -> int:
        now = time.monotonic()
        cutoff = now - window_seconds
        self.timestamps = [t for t in self.timestamps if t > cutoff]
        return len(self.timestamps)

    def record(self) -> None:
        self.timestamps.append(time.monotonic())


class RateLimiter:
    """基于内存的按用户速率限制器，支持可配置的限制参数。"""

    def __init__(
        self,
        *,
        requests_per_minute: int = 30,
        requests_per_hour: int = 300,
        chat_per_minute: int = 10,
        chat_per_hour: int = 100,
    ) -> None:
        self._rpm = requests_per_minute
        self._rph = requests_per_hour
        self._cpm = chat_per_minute
        self._cph = chat_per_hour
        self._general: dict[str, _WindowEntry] = defaultdict(_WindowEntry)
        self._chat: dict[str, _WindowEntry] = defaultdict(_WindowEntry)

    def check_general(self, user_id: str) -> None:
        """检查通用 API 速率限制。超限时抛出 429。"""
        entry = self._general[user_id]
        if entry.count_in_window(60) >= self._rpm:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"请求频率过高，每分钟最多 {self._rpm} 次请求",
            )
        if entry.count_in_window(3600) >= self._rph:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"请求频率过高，每小时最多 {self._rph} 次请求",
            )
        entry.record()

    def check_chat(self, user_id: str) -> None:
        """检查对话专用速率限制。超限时抛出 429。"""
        entry = self._chat[user_id]
        if entry.count_in_window(60) >= self._cpm:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"对话频率过高，每分钟最多 {self._cpm} 次对话",
            )
        if entry.count_in_window(3600) >= self._cph:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"对话频率过高，每小时最多 {self._cph} 次对话",
            )
        entry.record()

    def check_send_code(self, email: str) -> None:
        """检查邮件验证码发送速率限制（按邮箱地址限流）。

        限制：每分钟 1 次，每小时 5 次。
        """
        entry = self._general[f"send_code:{email.lower()}"]
        if entry.count_in_window(60) >= 1:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="发送过于频繁，请 1 分钟后再试",
            )
        if entry.count_in_window(3600) >= 5:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="今日发送次数过多，请稍后再试",
            )
        entry.record()

    def cleanup_stale(self, max_age_seconds: float = 7200) -> int:
        """移除无近期活动的条目。返回移除数量。"""
        now = time.monotonic()
        removed = 0
        for store in (self._general, self._chat):
            stale_keys = [
                k for k, v in store.items()
                if not v.timestamps or (now - max(v.timestamps)) > max_age_seconds
            ]
            for k in stale_keys:
                del store[k]
                removed += 1
        return removed
