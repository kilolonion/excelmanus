"""SessionManager 单元测试与属性测试。"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from excelmanus.config import ExcelManusConfig
from excelmanus.engine import AgentEngine
from excelmanus.session import (
    SessionBusyError,
    SessionLimitExceededError,
    SessionNotFoundError,
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
    async def test_create_new_session_starts_manifest_prewarm(
        self, manager: SessionManager
    ) -> None:
        """创建新会话时应触发 engine 的后台 manifest 预热。"""
        with patch(
            "excelmanus.session.AgentEngine.start_workspace_manifest_prewarm",
            return_value=True,
        ) as prewarm_mock:
            sid, _engine = await manager.acquire_for_chat(None)
            await manager.release_for_chat(sid)

        prewarm_mock.assert_called_once()

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

    @pytest.mark.asyncio
    async def test_multiple_sessions_do_not_crash_on_shared_registry(
        self, manager: SessionManager
    ) -> None:
        """Bug 修复回归：共享 ToolRegistry 时第二个会话不应因工具重复注册而崩溃。

        AgentEngine 在 __init__ 中会注册会话级工具（task_tools / skill_tools）。
        若多个会话共享同一 ToolRegistry 实例，第二个会话必定抛出 ToolRegistryError。
        修复方案：AgentEngine 通过 registry.fork() 获得 per-session 独立副本。
        """
        sid1, engine1 = await _create_session(manager)
        # 第二个会话不应抛出任何异常
        sid2, engine2 = await _create_session(manager)

        assert sid1 != sid2
        assert engine1 is not engine2
        # 两个会话的 registry 是独立实例（fork 出来的）
        assert engine1._registry is not engine2._registry
        # 但都包含会话级工具
        assert "task_create" in engine1._registry.get_tool_names()
        assert "task_create" in engine2._registry.get_tool_names()


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

    @pytest.mark.asyncio
    async def test_user_session_uses_user_workspace(
        self, manager: SessionManager, config: ExcelManusConfig
    ) -> None:
        """认证场景下传入 user_id 时，engine 应绑定该用户工作区。"""
        sid, engine = await manager.acquire_for_chat(None, user_id="user-abc")
        await manager.release_for_chat(sid)

        assert str(engine._config.workspace_root).endswith("/users/user-abc")
        assert engine._config.workspace_root != config.workspace_root


class TestSessionDetail:
    """get_session_detail 方法测试。"""

    @pytest.mark.asyncio
    async def test_get_session_detail_includes_mode_and_model_state(
        self, manager: SessionManager
    ) -> None:
        """活跃会话详情应返回模式开关与当前模型信息。"""
        sid, _ = await _create_session(manager, "detail-session")

        detail = await manager.get_session_detail(sid)

        assert detail["full_access_enabled"] is False
        assert detail["chat_mode"] == "write"
        assert detail["current_model"] == manager._sessions[sid].engine.current_model
        assert (
            detail["current_model_name"]
            == manager._sessions[sid].engine.current_model_name
        )

    @pytest.mark.asyncio
    async def test_get_session_detail_sqlite_fallback_has_default_mode_and_model_state(
        self, config: ExcelManusConfig, registry: ToolRegistry
    ) -> None:
        """SQLite 历史回退详情也应保持与活跃会话一致的字段形状。"""
        chat_history = MagicMock()
        chat_history.session_exists.return_value = True
        chat_history.load_messages.return_value = [{"role": "user", "content": "历史消息"}]

        manager = SessionManager(
            max_sessions=config.max_sessions,
            ttl_seconds=config.session_ttl_seconds,
            config=config,
            registry=registry,
            chat_history=chat_history,
        )

        detail = await manager.get_session_detail("history-only")

        assert detail["full_access_enabled"] is False
        assert detail["chat_mode"] == "write"
        assert detail["current_model"] is None
        assert detail["current_model_name"] is None


class TestRollbackSession:
    """rollback_session 方法测试。"""

    @pytest.mark.asyncio
    async def test_rollback_sqlite_only_session_rebuilds_persisted_messages(
        self, config: ExcelManusConfig, registry: ToolRegistry
    ) -> None:
        """SQLite 历史会话回退后，应清空并重建持久化消息。"""
        chat_history = MagicMock()
        chat_history.session_exists.return_value = True
        chat_history.load_messages.return_value = [
            {"role": "user", "content": "原始问题"},
            {"role": "assistant", "content": "原始回复"},
            {"role": "user", "content": "第二轮问题"},
            {"role": "assistant", "content": "第二轮回复"},
        ]

        manager = SessionManager(
            max_sessions=config.max_sessions,
            ttl_seconds=config.session_ttl_seconds,
            config=config,
            registry=registry,
            chat_history=chat_history,
        )

        result = await manager.rollback_session(
            "history-only",
            0,
            new_message="编辑后问题",
        )

        assert result["turn_index"] == 0
        assert result["removed_messages"] == 3
        chat_history.clear_messages.assert_called_once_with("history-only")
        chat_history.save_turn_messages.assert_called_once()

        save_call = chat_history.save_turn_messages.call_args
        assert save_call.args[0] == "history-only"
        persisted_messages = save_call.args[1]
        assert persisted_messages == [{"role": "user", "content": "编辑后问题"}]
        assert save_call.kwargs["turn_number"] == 0

    @pytest.mark.asyncio
    async def test_rollback_missing_session_raises_not_found(
        self, manager: SessionManager
    ) -> None:
        """不存在的会话回退应抛出 SessionNotFoundError。"""
        with pytest.raises(SessionNotFoundError, match="不存在"):
            await manager.rollback_session("missing-session", 0)


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
    def test_expired_sessions_always_cleaned(
        self, ttl: int, idle_extra: int, n_sessions: int
    ) -> None:
        """任意 TTL 和空闲时间组合下，超时会话必须被全部清理。"""
        import asyncio
        from unittest.mock import AsyncMock, patch

        loop = asyncio.new_event_loop()
        try:
            async def _inner() -> None:
                config = ExcelManusConfig(api_key="test-key", base_url="https://test.example.com/v1", model="test-model", max_sessions=1000)
                registry = ToolRegistry()
                mgr = SessionManager(
                    max_sessions=1000,
                    ttl_seconds=ttl,
                    config=config,
                    registry=registry,
                )

                # 创建 n 个会话（mock 掉 MCP 初始化和 manifest 预热避免 hang）
                with patch.object(AgentEngine, "initialize_mcp", new_callable=AsyncMock), \
                     patch.object(AgentEngine, "start_workspace_manifest_prewarm", return_value=False):
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

            loop.run_until_complete(_inner())
        finally:
            loop.close()

    @given(
        ttl=st.integers(min_value=2, max_value=7200),
        n_sessions=st.integers(min_value=1, max_value=20),
    )
    def test_active_sessions_never_cleaned(
        self, ttl: int, n_sessions: int
    ) -> None:
        """未超时的会话不应被清理。"""
        import asyncio
        from unittest.mock import AsyncMock, patch

        loop = asyncio.new_event_loop()
        try:
            async def _inner() -> None:
                config = ExcelManusConfig(api_key="test-key", base_url="https://test.example.com/v1", model="test-model", max_sessions=1000)
                registry = ToolRegistry()
                mgr = SessionManager(
                    max_sessions=1000,
                    ttl_seconds=ttl,
                    config=config,
                    registry=registry,
                )

                with patch.object(AgentEngine, "initialize_mcp", new_callable=AsyncMock), \
                     patch.object(AgentEngine, "start_workspace_manifest_prewarm", return_value=False):
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

            loop.run_until_complete(_inner())
        finally:
            loop.close()


class TestArchiveSession:
    """archive_session 方法测试。"""

    @pytest.mark.asyncio
    async def test_archive_sqlite_only_session(
        self, config: ExcelManusConfig, registry: ToolRegistry
    ) -> None:
        """仅存在于 SQLite 中的历史会话可被归档。"""
        chat_history = MagicMock()
        chat_history.session_exists.return_value = True
        chat_history.update_session = MagicMock()

        mgr = SessionManager(
            max_sessions=5,
            ttl_seconds=60,
            config=config,
            registry=registry,
            chat_history=chat_history,
        )

        result = await mgr.archive_session("sqlite-only", archive=True)
        assert result is True
        chat_history.update_session.assert_called_once_with("sqlite-only", status="archived")

    @pytest.mark.asyncio
    async def test_unarchive_sqlite_only_session(
        self, config: ExcelManusConfig, registry: ToolRegistry
    ) -> None:
        """仅存在于 SQLite 中的归档会话可被取消归档。"""
        chat_history = MagicMock()
        chat_history.session_exists.return_value = True
        chat_history.update_session = MagicMock()

        mgr = SessionManager(
            max_sessions=5,
            ttl_seconds=60,
            config=config,
            registry=registry,
            chat_history=chat_history,
        )

        result = await mgr.archive_session("sqlite-only", archive=False)
        assert result is True
        chat_history.update_session.assert_called_once_with("sqlite-only", status="active")

    @pytest.mark.asyncio
    async def test_archive_in_memory_session(
        self, config: ExcelManusConfig, registry: ToolRegistry
    ) -> None:
        """内存中的活跃会话可被归档（通过 SQLite 持久化状态）。"""
        chat_history = MagicMock()
        chat_history.session_exists.return_value = False
        chat_history.update_session = MagicMock()

        mgr = SessionManager(
            max_sessions=5,
            ttl_seconds=60,
            config=config,
            registry=registry,
            chat_history=chat_history,
        )

        sid, _ = await _create_session(mgr)
        result = await mgr.archive_session(sid, archive=True)
        assert result is True
        chat_history.update_session.assert_called_once_with(sid, status="archived")

    @pytest.mark.asyncio
    async def test_archive_nonexistent_session_returns_false(
        self, manager: SessionManager
    ) -> None:
        """归档不存在的会话应返回 False。"""
        result = await manager.archive_session("nonexistent", archive=True)
        assert result is False

    @pytest.mark.asyncio
    async def test_archive_without_chat_history_returns_false(
        self, manager: SessionManager
    ) -> None:
        """无 ChatHistoryStore 时，归档内存会话应返回 False。"""
        sid, _ = await _create_session(manager)
        # manager 默认无 chat_history
        result = await manager.archive_session(sid, archive=True)
        assert result is False

    @pytest.mark.asyncio
    async def test_archive_persists_messages_before_status_update(
        self, config: ExcelManusConfig, registry: ToolRegistry
    ) -> None:
        """归档内存会话时，应先持久化消息再更新状态。"""
        chat_history = MagicMock()
        chat_history.session_exists.return_value = False
        chat_history.update_session = MagicMock()

        call_order: list[str] = []
        orig_save = chat_history.save_turn_messages
        orig_update = chat_history.update_session

        def track_save(*a, **kw):
            call_order.append("save")
            return orig_save(*a, **kw)

        def track_update(*a, **kw):
            call_order.append("update")
            return orig_update(*a, **kw)

        chat_history.save_turn_messages = track_save
        chat_history.update_session = track_update

        mgr = SessionManager(
            max_sessions=5,
            ttl_seconds=60,
            config=config,
            registry=registry,
            chat_history=chat_history,
        )

        sid, _ = await _create_session(mgr)
        await mgr.archive_session(sid, archive=True)

        # update_session（状态更新）应在 save_turn_messages 之后
        if "save" in call_order:
            assert call_order.index("save") < call_order.index("update")
