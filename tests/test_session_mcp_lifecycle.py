"""Session 层 MCP 生命周期测试。"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from excelmanus.config import ExcelManusConfig
from excelmanus.mcp.manager import MCPManager
from excelmanus.session import SessionManager
from excelmanus.tools import ToolRegistry


def _make_config(**overrides) -> ExcelManusConfig:
    defaults = dict(
        api_key="test-key",
        base_url="https://test.example.com/v1",
        model="test-model",
        session_ttl_seconds=60,
        max_sessions=10,
        memory_enabled=False,
        workspace_root="/tmp/excelmanus-test-session-lifecycle",
        backup_enabled=False,
    )
    defaults.update(overrides)
    return ExcelManusConfig(**defaults)


@pytest.mark.asyncio
async def test_shared_mcp_manager_initialized_once_for_multiple_sessions() -> None:
    config = _make_config()
    registry = ToolRegistry()
    shared = MCPManager(config.workspace_root)
    shared.initialize = AsyncMock(return_value=None)  # type: ignore[method-assign]
    shared.shutdown = AsyncMock(return_value=None)  # type: ignore[method-assign]
    shared._auto_approved_tools = ["mcp_context7_query_docs"]

    manager = SessionManager(
        max_sessions=config.max_sessions,
        ttl_seconds=config.session_ttl_seconds,
        config=config,
        registry=registry,
        shared_mcp_manager=shared,
    )

    sid1, engine1 = await manager.acquire_for_chat(None)
    sid2, engine2 = await manager.acquire_for_chat(None)
    await manager.release_for_chat(sid1)
    await manager.release_for_chat(sid2)

    assert shared.initialize.await_count == 1  # type: ignore[attr-defined]
    assert engine1._approval.is_mcp_auto_approved("mcp_context7_query_docs")
    assert engine2._approval.is_mcp_auto_approved("mcp_context7_query_docs")


@pytest.mark.asyncio
async def test_delete_session_does_not_shutdown_shared_mcp() -> None:
    config = _make_config()
    registry = ToolRegistry()
    shared = MCPManager(config.workspace_root)
    shared.initialize = AsyncMock(return_value=None)  # type: ignore[method-assign]
    shared.shutdown = AsyncMock(return_value=None)  # type: ignore[method-assign]

    manager = SessionManager(
        max_sessions=config.max_sessions,
        ttl_seconds=config.session_ttl_seconds,
        config=config,
        registry=registry,
        shared_mcp_manager=shared,
    )

    sid, engine = await manager.acquire_for_chat(None)
    await manager.release_for_chat(sid)
    engine.shutdown_mcp = AsyncMock(return_value=None)  # type: ignore[method-assign]

    assert await manager.delete(sid) is True
    engine.shutdown_mcp.assert_not_awaited()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_session_manager_shutdown_calls_shared_manager_once() -> None:
    config = _make_config()
    registry = ToolRegistry()
    shared = MCPManager(config.workspace_root)
    shared.initialize = AsyncMock(return_value=None)  # type: ignore[method-assign]
    shared.shutdown = AsyncMock(return_value=None)  # type: ignore[method-assign]

    manager = SessionManager(
        max_sessions=config.max_sessions,
        ttl_seconds=config.session_ttl_seconds,
        config=config,
        registry=registry,
        shared_mcp_manager=shared,
    )

    sid1, _ = await manager.acquire_for_chat(None)
    sid2, _ = await manager.acquire_for_chat(None)
    await manager.release_for_chat(sid1)
    await manager.release_for_chat(sid2)

    await manager.shutdown()

    shared.shutdown.assert_awaited_once()  # type: ignore[attr-defined]
    assert await manager.get_active_count() == 0
