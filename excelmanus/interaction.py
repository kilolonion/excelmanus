"""交互注册表：管理 ask_user / approval 的 asyncio.Future 阻塞等待。

当工具调用 ask_user 或触发审批时，创建一个 Future 并在此注册。
前端通过专用 API 端点提交回答/决策后，resolve 对应的 Future，
使工具调用在同一轮 _tool_calling_loop 内继续执行。
"""

from __future__ import annotations

import asyncio
from typing import Any

from excelmanus.logger import get_logger

logger = get_logger("interaction")

# 默认超时 10 分钟
DEFAULT_INTERACTION_TIMEOUT: float = 600.0


class InteractionRegistry:
    """管理 interaction_id → asyncio.Future 映射。

    线程安全注意：所有操作都应在同一个事件循环线程中调用。
    """

    def __init__(self) -> None:
        self._futures: dict[str, asyncio.Future[Any]] = {}

    def create(self, interaction_id: str) -> asyncio.Future[Any]:
        """注册一个新的交互等待。

        如果同 ID 已存在且未完成，先取消旧的。

        Returns:
            可 await 的 Future，resolve 后返回用户提交的 payload。
        """
        old = self._futures.get(interaction_id)
        if old is not None and not old.done():
            old.cancel()
            logger.warning("覆盖未完成的交互: %s", interaction_id)

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
        fut: asyncio.Future[Any] = loop.create_future()
        self._futures[interaction_id] = fut
        logger.debug("创建交互等待: %s", interaction_id)
        return fut

    def resolve(self, interaction_id: str, payload: Any) -> bool:
        """提交用户回答/决策，resolve 对应的 Future。

        Future 在 resolve 后仍保留在 dict 中，由 cleanup_done() 统一清理。

        Returns:
            True 表示成功 resolve；False 表示 ID 不存在或已完成。
        """
        fut = self._futures.get(interaction_id)
        if fut is None:
            logger.warning("尝试 resolve 不存在的交互: %s", interaction_id)
            return False
        if fut.done():
            logger.warning("尝试 resolve 已完成的交互: %s", interaction_id)
            return False
        fut.set_result(payload)
        logger.debug("交互已 resolve: %s", interaction_id)
        return True

    def cancel(self, interaction_id: str) -> bool:
        """取消单个交互。

        Returns:
            True 表示成功取消。
        """
        fut = self._futures.pop(interaction_id, None)
        if fut is None or fut.done():
            return False
        fut.cancel()
        logger.debug("交互已取消: %s", interaction_id)
        return True

    def cancel_all(self) -> int:
        """取消所有未完成的交互。

        Returns:
            已取消的数量。
        """
        cancelled = 0
        for iid, fut in list(self._futures.items()):
            if not fut.done():
                fut.cancel()
                cancelled += 1
        self._futures.clear()
        if cancelled > 0:
            logger.info("批量取消 %d 个未完成交互", cancelled)
        return cancelled

    def has_pending(self, interaction_id: str | None = None) -> bool:
        """检查是否有未完成的交互。

        Args:
            interaction_id: 指定 ID 检查；None 表示检查是否有任何未完成交互。
        """
        if interaction_id is not None:
            fut = self._futures.get(interaction_id)
            return fut is not None and not fut.done()
        return any(not f.done() for f in self._futures.values())

    @property
    def pending_count(self) -> int:
        """当前未完成交互数量。"""
        return sum(1 for f in self._futures.values() if not f.done())

    def cleanup_done(self) -> int:
        """清理已完成/已取消的 Future，释放内存。

        Returns:
            清理的数量。
        """
        done_ids = [iid for iid, f in self._futures.items() if f.done()]
        for iid in done_ids:
            del self._futures[iid]
        return len(done_ids)
