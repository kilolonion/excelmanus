"""对外入口：followup / steer / inject。

斜杠与待答问题走控制面，不经 inbox 进模型历史。实现在 ``session_api``。
"""

from __future__ import annotations

from typing import Any

from excelmanus.agent.inbox import InboxItem


async def followup(engine: Any, content: str, **kwargs: Any) -> Any:
    """用户后续 → inbox ``next-turn`` + wakeup。"""
    return await engine.followup(content, **kwargs)


def steer(engine: Any, content: str) -> InboxItem:
    """步中插话 → ``next-step``。当前 turn 下一步可见，不打断当前工具批。"""
    return engine.steer(content)


def inject(engine: Any, content: str) -> InboxItem:
    """guide / hook 注入 → ``next-step``，不单独唤醒。"""
    return engine.inject(content)
