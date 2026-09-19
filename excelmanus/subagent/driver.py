"""one-shot 进程内驱动：组合 → 一回合 → settle → dispose。"""

from __future__ import annotations

import asyncio
from typing import Any

from excelmanus.logger import get_logger
from excelmanus.subagent.child import compose_child
from excelmanus.subagent.models import SubagentConfig, SubagentDescriptor, SubagentResult
from excelmanus.subagent.projection import wrap_on_event
from excelmanus.subagent.result import chat_to_result, failure_result

logger = get_logger("subagent.driver")


class InProcessDriver:
    """本地 one-shot。超时/取消映射 aborted。"""

    def __init__(self) -> None:
        self._child: Any = None
        self._task: asyncio.Task[Any] | None = None
        self._disposed = False

    def compose(self, parent: Any, config: SubagentConfig) -> Any:
        """组合窗口。发布 start 之前调用，失败不发事件。"""
        child = compose_child(parent, config)
        self._child = child
        return child

    async def run(
        self,
        parent: Any,
        config: SubagentConfig,
        *,
        prompt: str,
        descriptor: SubagentDescriptor,
        on_event: Any = None,
        timeout: float | None = None,
        child: Any = None,
    ) -> SubagentResult:
        child = child or self._child or self.compose(parent, config)
        self._disposed = False
        self._child = child
        projected = wrap_on_event(on_event, descriptor)
        child._driver._on_event = projected
        item = child._driver.enqueue_followup(prompt, extra={"on_event": projected})

        async def _kick() -> Any:
            await child._driver.kick()
            return item.result

        try:
            self._task = asyncio.create_task(_kick())
            chat = await asyncio.wait_for(
                self._task,
                timeout=timeout if timeout and timeout > 0 else None,
            )
        except asyncio.TimeoutError:
            return failure_result(
                config=config,
                conversation_id=descriptor.run_id,
                stop_reason="aborted",
                message=f"子代理 {config.name} 执行超时，已终止。",
            )
        except asyncio.CancelledError:
            return failure_result(
                config=config,
                conversation_id=descriptor.run_id,
                stop_reason="aborted",
                message=f"子代理 {config.name} 已取消。",
            )
        except Exception as exc:
            logger.warning("子 Driver 执行失败: %s", exc, exc_info=True)
            return failure_result(
                config=config,
                conversation_id=descriptor.run_id,
                stop_reason="error",
                message=str(exc),
            )
        finally:
            await self._dispose_quietly()

        if chat is None:
            return failure_result(
                config=config,
                conversation_id=descriptor.run_id,
                stop_reason="error",
                message="子 Driver 没有返回结果。",
            )
        return chat_to_result(chat, config=config, conversation_id=descriptor.run_id)

    async def _dispose_quietly(self) -> None:
        """清理不覆盖调用方的取消状态；shield 让 dispose 在后台完成。"""
        try:
            await asyncio.shield(self.dispose())
        except asyncio.CancelledError:
            pass

    async def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        child = self._child
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.warning("子 Driver 清理 task 失败", exc_info=True)
        if child is not None:
            dispatcher = getattr(child, "_tool_dispatcher", None)
            cancel = getattr(dispatcher, "request_cancel", None)
            if callable(cancel):
                cancel()
        self._child = None
        self._task = None
