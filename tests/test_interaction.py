"""InteractionRegistry 单元测试。"""

from __future__ import annotations

import asyncio

import pytest

from excelmanus.interaction import InteractionRegistry, DEFAULT_INTERACTION_TIMEOUT


@pytest.fixture()
def registry() -> InteractionRegistry:
    return InteractionRegistry()


class TestCreate:
    def test_create_returns_future(self, registry: InteractionRegistry) -> None:
        fut = registry.create("q1")
        assert isinstance(fut, asyncio.Future)
        assert not fut.done()

    def test_create_overwrites_existing(self, registry: InteractionRegistry) -> None:
        fut1 = registry.create("q1")
        fut2 = registry.create("q1")
        assert fut1.cancelled()
        assert not fut2.done()

    def test_pending_count(self, registry: InteractionRegistry) -> None:
        registry.create("q1")
        registry.create("q2")
        assert registry.pending_count == 2


class TestResolve:
    @pytest.mark.asyncio
    async def test_resolve_sets_result(self, registry: InteractionRegistry) -> None:
        fut = registry.create("q1")
        ok = registry.resolve("q1", {"answer": "yes"})
        assert ok is True
        result = await fut
        assert result == {"answer": "yes"}

    def test_resolve_nonexistent_returns_false(self, registry: InteractionRegistry) -> None:
        assert registry.resolve("missing", {}) is False

    @pytest.mark.asyncio
    async def test_resolve_already_done_returns_false(self, registry: InteractionRegistry) -> None:
        fut = registry.create("q1")
        registry.resolve("q1", "first")
        # 尝试再次 resolve（Future 已 done，仍在 dict 中）
        assert registry.resolve("q1", "second") is False
        assert await fut == "first"


class TestCancel:
    def test_cancel_single(self, registry: InteractionRegistry) -> None:
        fut = registry.create("q1")
        ok = registry.cancel("q1")
        assert ok is True
        assert fut.cancelled()

    def test_cancel_nonexistent(self, registry: InteractionRegistry) -> None:
        assert registry.cancel("missing") is False

    def test_cancel_all(self, registry: InteractionRegistry) -> None:
        f1 = registry.create("q1")
        f2 = registry.create("q2")
        count = registry.cancel_all()
        assert count == 2
        assert f1.cancelled()
        assert f2.cancelled()
        assert registry.pending_count == 0


class TestHasPending:
    def test_has_pending_specific(self, registry: InteractionRegistry) -> None:
        registry.create("q1")
        assert registry.has_pending("q1") is True
        assert registry.has_pending("q2") is False

    def test_has_pending_any(self, registry: InteractionRegistry) -> None:
        assert registry.has_pending() is False
        registry.create("q1")
        assert registry.has_pending() is True

    @pytest.mark.asyncio
    async def test_has_pending_after_resolve(self, registry: InteractionRegistry) -> None:
        registry.create("q1")
        registry.resolve("q1", "done")
        # Future done 但还在 dict 中 → cleanup_done 后才消失
        # has_pending 检查 .done() 所以返回 False
        assert registry.has_pending("q1") is False


class TestCleanupDone:
    @pytest.mark.asyncio
    async def test_cleanup_removes_done(self, registry: InteractionRegistry) -> None:
        registry.create("q1")
        registry.create("q2")
        registry.resolve("q1", "done")
        cleaned = registry.cleanup_done()
        assert cleaned == 1
        assert registry.pending_count == 1


class TestAwaitWithTimeout:
    """测试 asyncio.wait_for 集成。"""

    @pytest.mark.asyncio
    async def test_await_resolves(self, registry: InteractionRegistry) -> None:
        fut = registry.create("q1")

        async def _resolve_later():
            await asyncio.sleep(0.05)
            registry.resolve("q1", "answer")

        asyncio.create_task(_resolve_later())
        result = await asyncio.wait_for(fut, timeout=2.0)
        assert result == "answer"

    @pytest.mark.asyncio
    async def test_await_timeout(self, registry: InteractionRegistry) -> None:
        fut = registry.create("q1")
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(fut, timeout=0.05)

    @pytest.mark.asyncio
    async def test_await_cancelled(self, registry: InteractionRegistry) -> None:
        fut = registry.create("q1")
        registry.cancel("q1")
        with pytest.raises(asyncio.CancelledError):
            await fut


class TestDefaultTimeout:
    def test_default_timeout_value(self) -> None:
        assert DEFAULT_INTERACTION_TIMEOUT == 600.0
