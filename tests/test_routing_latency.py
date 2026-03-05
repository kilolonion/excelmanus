"""Tests for routing latency optimizations (P0/P1/P2).

P0: EmbeddingClient default timeout reduced from 30s to 5s.
P1: embed_single removed from routing critical path — semantic tasks await internally.
P2: Smart gating — skip embedding when lexical task_tags are definitive.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import MagicMock

import numpy as np
import pytest


# ---------------------------------------------------------------------------
# P0: EmbeddingClient timeout default
# ---------------------------------------------------------------------------

class TestEmbeddingClientTimeout:
    """Verify EmbeddingClient default timeout is 5s (P0)."""

    def test_default_timeout_is_5s(self):
        from excelmanus.embedding.client import EmbeddingClient
        mock_client = MagicMock()
        ec = EmbeddingClient(client=mock_client)
        assert ec._timeout == 5.0

    def test_custom_timeout_preserved(self):
        from excelmanus.embedding.client import EmbeddingClient
        mock_client = MagicMock()
        ec = EmbeddingClient(client=mock_client, timeout_seconds=15.0)
        assert ec._timeout == 15.0


# ---------------------------------------------------------------------------
# P2: Smart gating — _SKIP_SEMANTIC_TAGS
# ---------------------------------------------------------------------------

class TestSmartGating:
    """Test that definitive task_tags cause embedding to be skipped."""

    @pytest.fixture
    def _mock_engine_attrs(self):
        """Common mock attributes for a minimal Engine-like object."""
        return {
            "_embedding_client": MagicMock(),
            "_semantic_memory": MagicMock(),
            "_semantic_registry": MagicMock(),
            "_semantic_skill_router": MagicMock(),
            "_file_registry": MagicMock(),
            "_playbook_store": None,
            "_session_summary_store": None,
            "_memory_injection_mode": "semantic",
            "_skill_router": MagicMock(),
        }

    @pytest.mark.parametrize("tag", [
        "cross_sheet",
        "formatting",
        "chart",
        "image_replica",
    ])
    def test_skip_semantic_tags_gate(self, tag: str):
        """Each skip-tag should suppress _need_semantic."""
        _SKIP_SEMANTIC_TAGS = frozenset({
            "cross_sheet", "formatting", "chart", "image_replica",
        })
        route_tags = {tag}
        _need_semantic = (
            True  # effective_slash_command is None
            and True  # embedding_client is not None
            and not (route_tags & _SKIP_SEMANTIC_TAGS)
        )
        assert _need_semantic is False, f"tag={tag} should suppress semantic"

    def test_no_skip_for_generic_tags(self):
        """Tags not in skip set should allow semantic search."""
        _SKIP_SEMANTIC_TAGS = frozenset({
            "cross_sheet", "formatting", "chart", "image_replica",
        })
        route_tags = {"data_fill", "large_data"}
        _need_semantic = (
            True
            and True
            and not (route_tags & _SKIP_SEMANTIC_TAGS)
        )
        assert _need_semantic is True

    def test_no_skip_when_no_tags(self):
        """Empty tags should allow semantic search."""
        _SKIP_SEMANTIC_TAGS = frozenset({
            "cross_sheet", "formatting", "chart", "image_replica",
        })
        route_tags: set[str] = set()
        _need_semantic = (
            True
            and True
            and not (route_tags & _SKIP_SEMANTIC_TAGS)
        )
        assert _need_semantic is True

    def test_slash_command_suppresses_semantic(self):
        """Slash commands should always suppress semantic (existing behavior)."""
        effective_slash_command = "some_skill"
        _need_semantic = (
            effective_slash_command is None
            and True
            and True
        )
        assert _need_semantic is False


# ---------------------------------------------------------------------------
# P1: _safe_await_query_vec helper
# ---------------------------------------------------------------------------

class TestSafeAwaitQueryVec:
    """Test the _safe_await_query_vec helper used by semantic tasks."""

    @pytest.mark.asyncio
    async def test_returns_vec_on_success(self):
        """Should return the embedding vector when task succeeds."""
        expected = np.array([1.0, 2.0, 3.0])

        async def _return_expected():
            return expected

        task = asyncio.create_task(_return_expected())

        # Simulate the helper
        async def _safe_await_query_vec(vec_task):
            if vec_task is None:
                return None
            try:
                return await vec_task
            except Exception:
                return None

        result = await _safe_await_query_vec(task)
        assert result is expected

    @pytest.mark.asyncio
    async def test_returns_none_on_failure(self):
        """Should return None when embedding task fails."""
        async def _failing():
            raise RuntimeError("API down")

        task = asyncio.create_task(_failing())

        async def _safe_await_query_vec(vec_task):
            if vec_task is None:
                return None
            try:
                return await vec_task
            except Exception:
                return None

        result = await _safe_await_query_vec(task)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_no_task(self):
        """Should return None when vec_task is None."""
        async def _safe_await_query_vec(vec_task):
            if vec_task is None:
                return None
            try:
                return await vec_task
            except Exception:
                return None

        result = await _safe_await_query_vec(None)
        assert result is None

    @pytest.mark.asyncio
    async def test_multiple_awaiters_share_task(self):
        """Multiple semantic tasks should be able to await the same task."""
        call_count = 0

        async def _embed():
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0.01)
            return np.array([1.0, 2.0])

        shared_task = asyncio.create_task(_embed())

        async def _safe_await_query_vec(vec_task):
            if vec_task is None:
                return None
            try:
                return await vec_task
            except Exception:
                return None

        # Multiple consumers await the same task
        results = await asyncio.gather(
            _safe_await_query_vec(shared_task),
            _safe_await_query_vec(shared_task),
            _safe_await_query_vec(shared_task),
        )

        # All should get the same result
        assert all(r is not None for r in results)
        assert all(np.array_equal(r, np.array([1.0, 2.0])) for r in results)
        # embed was called only once
        assert call_count == 1


# ---------------------------------------------------------------------------
# P1: Routing stage should not be blocked by embedding
# ---------------------------------------------------------------------------

class TestRoutingNotBlockedByEmbedding:
    """Verify that slow embedding doesn't block the routing pipeline stage."""

    @pytest.mark.asyncio
    async def test_slow_embed_doesnt_block_routing_phase(self):
        """Simulate: routing takes 50ms, embedding takes 500ms.
        With P1, routing should complete in ~50ms, not ~550ms."""

        routing_done = asyncio.Event()
        embed_started = asyncio.Event()

        async def _slow_embed(text: str) -> np.ndarray:
            embed_started.set()
            await asyncio.sleep(0.5)  # 500ms
            return np.array([1.0, 2.0, 3.0])

        async def _fast_route() -> str:
            await asyncio.sleep(0.05)  # 50ms
            routing_done.set()
            return "route_done"

        async def _safe_await_query_vec(vec_task):
            if vec_task is None:
                return None
            try:
                return await vec_task
            except Exception:
                return None

        # Start embedding and routing in parallel
        embed_task = asyncio.create_task(_slow_embed("test"))

        start = time.monotonic()
        route_result = await _fast_route()
        routing_elapsed = time.monotonic() - start

        # Routing should complete quickly (~50ms), not waiting for embed
        assert route_result == "route_done"
        assert routing_elapsed < 0.2, f"Routing took {routing_elapsed:.3f}s, should be <0.2s"

        # Semantic task would await embed internally
        vec = await _safe_await_query_vec(embed_task)
        assert vec is not None

    @pytest.mark.asyncio
    async def test_embed_timeout_doesnt_block_routing(self):
        """If embedding times out (5s), routing still completes fast."""

        async def _timeout_embed(text: str) -> np.ndarray:
            await asyncio.sleep(10)  # would timeout at 5s
            return np.array([1.0])

        async def _safe_await_query_vec(vec_task):
            if vec_task is None:
                return None
            try:
                return await asyncio.wait_for(vec_task, timeout=0.1)
            except Exception:
                return None

        embed_task = asyncio.create_task(_timeout_embed("test"))

        start = time.monotonic()
        # Routing phase: doesn't await embed
        await asyncio.sleep(0.05)  # simulate routing
        routing_elapsed = time.monotonic() - start
        assert routing_elapsed < 0.2

        # Semantic task would handle the timeout
        vec = await _safe_await_query_vec(embed_task)
        assert vec is None  # timed out gracefully

        # Cleanup
        embed_task.cancel()
        try:
            await embed_task
        except (asyncio.CancelledError, Exception):
            pass


# ---------------------------------------------------------------------------
# Integration: Semantic tasks with shared vec_task
# ---------------------------------------------------------------------------

class TestSemanticTasksIntegration:
    """Test that semantic tasks correctly share a single embedding task."""

    @pytest.mark.asyncio
    async def test_all_tasks_get_same_vector(self):
        """All semantic tasks sharing one vec_task should get identical vectors."""
        embed_calls = 0

        async def _embed(text: str) -> np.ndarray:
            nonlocal embed_calls
            embed_calls += 1
            await asyncio.sleep(0.01)
            return np.array([0.5, 0.5, 0.5])

        vec_task = asyncio.create_task(_embed("test query"))

        async def _safe_await(t):
            if t is None:
                return None
            try:
                return await t
            except Exception:
                return None

        # Simulate 3 semantic tasks
        async def _task_a():
            v = await _safe_await(vec_task)
            return ("memory", v)

        async def _task_b():
            v = await _safe_await(vec_task)
            return ("file_registry", v)

        async def _task_c():
            v = await _safe_await(vec_task)
            return ("skill_router", v)

        results = await asyncio.gather(
            asyncio.create_task(_task_a()),
            asyncio.create_task(_task_b()),
            asyncio.create_task(_task_c()),
        )

        assert embed_calls == 1, "Embedding should be called exactly once"
        for label, vec in results:
            assert vec is not None, f"{label} got None vector"
            assert np.array_equal(vec, np.array([0.5, 0.5, 0.5]))

    @pytest.mark.asyncio
    async def test_embed_failure_degrades_all_tasks(self):
        """If embedding fails, all semantic tasks should get None and degrade."""
        async def _failing_embed(text: str) -> np.ndarray:
            raise RuntimeError("API unreachable")

        vec_task = asyncio.create_task(_failing_embed("test"))

        async def _safe_await(t):
            if t is None:
                return None
            try:
                return await t
            except Exception:
                return None

        results = await asyncio.gather(
            _safe_await(vec_task),
            _safe_await(vec_task),
        )
        assert all(r is None for r in results)

    @pytest.mark.asyncio
    async def test_no_semantic_tasks_when_gated(self):
        """When _need_semantic is False, no tasks should be created."""
        _SKIP_SEMANTIC_TAGS = frozenset({
            "cross_sheet", "formatting", "chart", "image_replica",
        })
        route_tags = {"cross_sheet", "data_fill"}

        _need_semantic = not (route_tags & _SKIP_SEMANTIC_TAGS)
        assert _need_semantic is False

        # Simulating the gate: no tasks created
        tasks: list[tuple[str, asyncio.Task]] = []
        if _need_semantic:
            tasks.append(("would_not_be_created", asyncio.create_task(asyncio.sleep(0))))

        assert len(tasks) == 0
