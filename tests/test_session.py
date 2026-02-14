"""SessionManager 单元测试与属性测试。"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from excelmanus.config import ExcelManusConfig
from excelmanus.session import (
    SessionBusyError,
    SessionLimitExceededError,
    SessionManager,
)
from excelmanus.tools import ToolRegistry


# ── 辅助函数 ──────────────────────────────────────────────


async def _create_session(
    manager: SessionManager, session_id: str | None = None
) -> tuple[str, object]:
    """创建会话并立即释放锁，用于测试中不需要并发保护的场景。"""
    sid, engine = await manager.acquire_for_chat(session_id)
    await manager.release_for_chat(sid)
    return sid, engine


# ── Fixtures ──────────────────────────────────────────────


@pytest.fixture
def config() -> ExcelManusConfig:
    """创建测试用配置。"""
    return ExcelManusConfig(
        api_key="test-key",
        base_url="https://test.example.com/v1",
        model="test-model",
        session_ttl_seconds=60,
        max_sessions=5,
        memory_enabled=False,
        workspace_root="/tmp/excelmanus-test-session",
    )


@pytest.fixture
def registry() -> ToolRegistry:
    """创建空的 ToolRegistry。"""
    return ToolRegistry()


@pytest.fixture(autouse=True)
def disable_real_mcp_config(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """为会话测试注入空 MCP 配置，避免连接本机真实 MCP。"""
    config_file = tmp_path / "mcp.empty.json"
    config_file.write_text('{"mcpServers": {}}', encoding="utf-8")
    monkeypatch.setenv("EXCELMANUS_MCP_CONFIG", str(config_file))


@pytest.fixture
def manager(config: ExcelManusConfig, registry: ToolRegistry) -> SessionManager:
    """创建 SessionManager 实例。"""
    return SessionManager(
        max_sessions=config.max_sessions,
        ttl_seconds=config.session_ttl_seconds,
        config=config,
        registry=registry,
    )


# ── 单元测试 ──────────────────────────────────────────────


class TestGetOrCreate:
    """acquire_for_chat + release_for_chat 创建/复用会话测试。"""

    @pytest.mark.asyncio
    async def test_create_new_session_with_none_id(
        self, manager: SessionManager
    ) -> None:
        """session_id 为 None 时应创建新会话并返回 UUID。"""
        sid, engine = await _create_session(manager)
        assert sid is not None
        assert len(sid) == 36  # UUID4 格式
        assert engine is not None
        assert await manager.get_active_count() == 1

    @pytest.mark.asyncio
    async def test_create_new_session_with_unknown_id(
        self, manager: SessionManager
    ) -> None:
        """传入不存在的 session_id 时应创建新会话并使用该 ID。"""
        sid, engine = await _create_session(manager, "custom-id-123")
        assert sid == "custom-id-123"
        assert engine is not None
        assert await manager.get_active_count() == 1

    @pytest.mark.asyncio
    async def test_reuse_existing_session(self, manager: SessionManager) -> None:
        """传入已有 session_id 时应复用同一 engine。"""
        sid1, engine1 = await _create_session(manager)
        sid2, engine2 = await _create_session(manager, sid1)
        assert sid1 == sid2
        assert engine1 is engine2
        assert await manager.get_active_count() == 1

    @pytest.mark.asyncio
    async def test_session_limit_exceeded(self, manager: SessionManager) -> None:
        """超过最大会话数时应抛出 SessionLimitExceededError。"""
        # 填满 5 个会话
        for _ in range(5):
            await _create_session(manager)
        assert await manager.get_active_count() == 5

        # 第 6 个应失败
        with pytest.raises(SessionLimitExceededError, match="上限"):
            await _create_session(manager)

    @pytest.mark.asyncio
    async def test_reuse_does_not_count_toward_limit(
        self, manager: SessionManager
    ) -> None:
        """复用已有会话不应触发容量上限。"""
        sids = []
        for _ in range(5):
            sid, _ = await _create_session(manager)
            sids.append(sid)

        # 复用不应报错
        sid, engine = await _create_session(manager, sids[0])
        assert sid == sids[0]


class TestAcquireForChat:
    """acquire_for_chat / release_for_chat 方法测试。"""

    @pytest.mark.asyncio
    async def test_acquire_new_session_marks_in_flight(
        self, manager: SessionManager
    ) -> None:
        """创建会话用于 chat 时应标记 in_flight=True。"""
        sid, _ = await manager.acquire_for_chat(None)
        assert manager._sessions[sid].in_flight is True

    @pytest.mark.asyncio
    async def test_same_session_busy_raises(
        self, manager: SessionManager
    ) -> None:
        """同一会话并发进入时，第二个请求应被拒绝。"""
        sid, _ = await manager.acquire_for_chat("same-session")
        with pytest.raises(SessionBusyError, match="正在处理中"):
            await manager.acquire_for_chat(sid)

    @pytest.mark.asyncio
    async def test_release_clears_in_flight(
        self, manager: SessionManager
    ) -> None:
        """release_for_chat 应清除 in_flight 标记。"""
        sid, _ = await manager.acquire_for_chat("release-session")
        await manager.release_for_chat(sid)
        assert manager._sessions[sid].in_flight is False


class TestDelete:
    """delete 方法测试。"""

    @pytest.mark.asyncio
    async def test_delete_existing_session(self, manager: SessionManager) -> None:
        """删除已有会话应返回 True。"""
        sid, _ = await _create_session(manager)
        result = await manager.delete(sid)
        assert result is True
        assert await manager.get_active_count() == 0

    @pytest.mark.asyncio
    async def test_delete_nonexistent_session(self, manager: SessionManager) -> None:
        """删除不存在的会话应返回 False。"""
        result = await manager.delete("nonexistent-id")
        assert result is False

    @pytest.mark.asyncio
    async def test_delete_frees_capacity(self, manager: SessionManager) -> None:
        """删除会话后应释放容量，允许创建新会话。"""
        sids = []
        for _ in range(5):
            sid, _ = await _create_session(manager)
            sids.append(sid)

        # 删除一个
        await manager.delete(sids[0])
        assert await manager.get_active_count() == 4

        # 现在可以创建新的
        sid, engine = await _create_session(manager)
        assert engine is not None
        assert await manager.get_active_count() == 5

    @pytest.mark.asyncio
    async def test_delete_busy_session_raises(self, manager: SessionManager) -> None:
        """会话正在处理时删除应失败，避免资源竞态。"""
        sid, _ = await manager.acquire_for_chat("busy-session")
        with pytest.raises(SessionBusyError, match="正在处理中"):
            await manager.delete(sid)

        await manager.release_for_chat(sid)
        result = await manager.delete(sid)
        assert result is True

    @pytest.mark.asyncio
    async def test_delete_triggers_memory_extraction(self, manager: SessionManager) -> None:
        """删除会话应触发 extract_and_save_memory。"""
        sid, engine = await _create_session(manager)
        engine.extract_and_save_memory = AsyncMock(return_value=None)  # type: ignore[method-assign]

        result = await manager.delete(sid)
        assert result is True
        engine.extract_and_save_memory.assert_awaited_once()  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_delete_memory_extraction_error_is_ignored(
        self, manager: SessionManager
    ) -> None:
        """记忆提取失败应被吞掉，不影响删除结果。"""
        sid, engine = await _create_session(manager)
        engine.extract_and_save_memory = AsyncMock(side_effect=RuntimeError("boom"))  # type: ignore[method-assign]

        result = await manager.delete(sid)
        assert result is True
        engine.extract_and_save_memory.assert_awaited_once()  # type: ignore[attr-defined]


class TestCleanupExpired:
    """cleanup_expired 方法测试。"""

    @pytest.mark.asyncio
    async def test_cleanup_removes_expired_sessions(
        self, manager: SessionManager
    ) -> None:
        """超过 TTL 的会话应被清理。"""
        sid, _ = await _create_session(manager)
        assert await manager.get_active_count() == 1

        # 模拟时间流逝：注入 now = last_access + ttl + 1
        future_time = manager._sessions[sid].last_access + 61
        removed = await manager.cleanup_expired(now=future_time)
        assert removed == 1
        assert await manager.get_active_count() == 0

    @pytest.mark.asyncio
    async def test_cleanup_keeps_active_sessions(
        self, manager: SessionManager
    ) -> None:
        """未超过 TTL 的会话不应被清理。"""
        sid, _ = await _create_session(manager)
        # now 刚好等于 last_access，远未过期
        current_time = manager._sessions[sid].last_access
        removed = await manager.cleanup_expired(now=current_time)
        assert removed == 0
        assert await manager.get_active_count() == 1

    @pytest.mark.asyncio
    async def test_cleanup_partial_expiry(self, manager: SessionManager) -> None:
        """部分过期时只清理过期的会话。"""
        sid1, _ = await _create_session(manager)
        sid2, _ = await _create_session(manager)

        # 手动设置 sid1 的 last_access 为很早之前
        manager._sessions[sid1].last_access = 0.0

        # 以 sid2 的时间为基准清理
        now = manager._sessions[sid2].last_access + 30  # 30s < 60s TTL
        removed = await manager.cleanup_expired(now=now)
        assert removed == 1
        assert await manager.get_active_count() == 1
        assert sid2 in manager._sessions

    @pytest.mark.asyncio
    async def test_cleanup_with_no_sessions(self, manager: SessionManager) -> None:
        """空容器清理应返回 0。"""
        removed = await manager.cleanup_expired()
        assert removed == 0

    @pytest.mark.asyncio
    async def test_cleanup_frees_capacity(self, manager: SessionManager) -> None:
        """清理过期会话后应释放容量。"""
        for _ in range(5):
            await _create_session(manager)
        assert await manager.get_active_count() == 5

        # 将所有会话标记为过期
        for entry in manager._sessions.values():
            entry.last_access = 0.0

        removed = await manager.cleanup_expired(now=61.0)
        assert removed == 5
        assert await manager.get_active_count() == 0

        # 现在可以创建新会话
        sid, engine = await _create_session(manager)
        assert engine is not None

    @pytest.mark.asyncio
    async def test_cleanup_skips_in_flight_sessions(
        self, manager: SessionManager
    ) -> None:
        """执行中的会话不应被 TTL 清理。"""
        sid, _ = await manager.acquire_for_chat("inflight-session")
        manager._sessions[sid].last_access = 0.0

        removed = await manager.cleanup_expired(now=61.0)
        assert removed == 0
        assert await manager.get_active_count() == 1

    @pytest.mark.asyncio
    async def test_cleanup_triggers_memory_extraction_for_expired_sessions(
        self, manager: SessionManager
    ) -> None:
        """清理过期会话时应触发 extract_and_save_memory。"""
        sid, engine = await _create_session(manager)
        engine.extract_and_save_memory = AsyncMock(return_value=None)  # type: ignore[method-assign]
        manager._sessions[sid].last_access = 0.0

        removed = await manager.cleanup_expired(now=61.0)
        assert removed == 1
        engine.extract_and_save_memory.assert_awaited_once()  # type: ignore[attr-defined]


class TestBackgroundCleanupLifecycle:
    """后台清理任务生命周期测试。"""

    @pytest.mark.asyncio
    async def test_background_cleanup_removes_expired_sessions(
        self, manager: SessionManager
    ) -> None:
        """启动后台清理后，过期会话应被自动回收。"""
        sid, _ = await _create_session(manager)
        manager._sessions[sid].last_access = 0.0

        await manager.start_background_cleanup(interval_seconds=1)
        try:
            for _ in range(30):
                if await manager.get_active_count() == 0:
                    break
                await asyncio.sleep(0.05)
            assert await manager.get_active_count() == 0
        finally:
            await manager.stop_background_cleanup()

    @pytest.mark.asyncio
    async def test_start_and_stop_background_cleanup_are_idempotent(
        self, manager: SessionManager
    ) -> None:
        """重复 start/stop 不应创建重复任务或抛错。"""
        await manager.start_background_cleanup(interval_seconds=10)
        first_task = manager._cleanup_task
        assert first_task is not None

        await manager.start_background_cleanup(interval_seconds=1)
        second_task = manager._cleanup_task
        assert second_task is first_task

        await manager.stop_background_cleanup()
        assert manager._cleanup_task is None

        await manager.stop_background_cleanup()
        assert manager._cleanup_task is None

    @pytest.mark.asyncio
    async def test_shutdown_stops_background_cleanup_task(
        self, manager: SessionManager
    ) -> None:
        """shutdown 必须停止后台清理任务并清空句柄。"""
        await manager.start_background_cleanup(interval_seconds=10)
        cleanup_task = manager._cleanup_task
        assert cleanup_task is not None

        await manager.shutdown()
        assert manager._cleanup_task is None
        assert cleanup_task.done()


class TestConcurrencySafety:
    """并发安全测试。"""

    @pytest.mark.asyncio
    async def test_concurrent_create_respects_limit(
        self, config: ExcelManusConfig, registry: ToolRegistry
    ) -> None:
        """并发创建会话时不应超过最大会话数限制。"""
        mgr = SessionManager(
            max_sessions=3,
            ttl_seconds=60,
            config=config,
            registry=registry,
        )

        results: list[tuple[str, object] | Exception] = []

        async def create_one() -> tuple[str, object] | Exception:
            try:
                sid, engine = await mgr.acquire_for_chat(None)
                await mgr.release_for_chat(sid)
                return (sid, engine)
            except SessionLimitExceededError as e:
                return e

        # 并发创建 10 个
        tasks = [create_one() for _ in range(10)]
        results = await asyncio.gather(*tasks)

        successes = [r for r in results if not isinstance(r, Exception)]
        failures = [r for r in results if isinstance(r, Exception)]

        assert len(successes) == 3
        assert len(failures) == 7
        assert await mgr.get_active_count() == 3


# ── Property 18：会话 TTL 清理（属性测试） ────────────────

from hypothesis import given, strategies as st


class TestProperty18SessionTTLCleanup:
    """Property 18：超过 session_ttl_seconds 的空闲会话必须被清理。

    **Validates: Requirements 5.8, 5.10, 6.7**
    """

    @given(
        ttl=st.integers(min_value=1, max_value=7200),
        idle_extra=st.integers(min_value=1, max_value=3600),
        n_sessions=st.integers(min_value=1, max_value=20),
    )
    @pytest.mark.asyncio
    async def test_expired_sessions_always_cleaned(
        self, ttl: int, idle_extra: int, n_sessions: int
    ) -> None:
        """任意 TTL 和空闲时间组合下，超时会话必须被全部清理。"""
        config = ExcelManusConfig(api_key="test-key", base_url="https://test.example.com/v1", model="test-model", max_sessions=1000)
        registry = ToolRegistry()
        mgr = SessionManager(
            max_sessions=1000,
            ttl_seconds=ttl,
            config=config,
            registry=registry,
        )

        # 创建 n 个会话
        for _ in range(n_sessions):
            sid, _ = await mgr.acquire_for_chat(None)
            await mgr.release_for_chat(sid)

        # 将所有会话标记为 base_time 访问
        base_time = 10000.0
        for entry in mgr._sessions.values():
            entry.last_access = base_time

        # 清理时间 = base_time + ttl + idle_extra（一定超时）
        now = base_time + ttl + idle_extra
        removed = await mgr.cleanup_expired(now=now)

        assert removed == n_sessions
        assert await mgr.get_active_count() == 0

    @given(
        ttl=st.integers(min_value=2, max_value=7200),
        n_sessions=st.integers(min_value=1, max_value=20),
    )
    @pytest.mark.asyncio
    async def test_active_sessions_never_cleaned(
        self, ttl: int, n_sessions: int
    ) -> None:
        """未超时的会话不应被清理。"""
        config = ExcelManusConfig(api_key="test-key", base_url="https://test.example.com/v1", model="test-model", max_sessions=1000)
        registry = ToolRegistry()
        mgr = SessionManager(
            max_sessions=1000,
            ttl_seconds=ttl,
            config=config,
            registry=registry,
        )

        for _ in range(n_sessions):
            sid, _ = await mgr.acquire_for_chat(None)
            await mgr.release_for_chat(sid)

        # 将所有会话标记为 base_time 访问
        base_time = 10000.0
        for entry in mgr._sessions.values():
            entry.last_access = base_time

        # 清理时间 = base_time（刚访问，未超时）
        removed = await mgr.cleanup_expired(now=base_time)

        assert removed == 0
        assert await mgr.get_active_count() == n_sessions
