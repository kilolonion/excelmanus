"""AUX 配置热更新广播回归测试。

验证前端修改 AUX 模型后，变更能传播到已存活的 AgentEngine 实例，
使 explorer/verifier 等子代理使用新的 AUX 模型而非过时快照。
"""
from __future__ import annotations

import asyncio
from dataclasses import replace
from unittest.mock import MagicMock, patch

import pytest

from excelmanus.config import ExcelManusConfig
from excelmanus.session import SessionManager
from excelmanus.tools import ToolRegistry


@pytest.fixture
def config() -> ExcelManusConfig:
    return ExcelManusConfig(
        api_key="test-key",
        base_url="https://test.example.com/v1",
        model="test-model",
        aux_model="qwen-flash-old",
        aux_api_key="aux-key-old",
        aux_base_url="https://aux-old.example.com/v1",
        session_ttl_seconds=60,
        max_sessions=5,
        memory_enabled=False,
        workspace_root="/tmp/excelmanus-test-aux",
    )


@pytest.fixture
def registry() -> ToolRegistry:
    return ToolRegistry()


@pytest.fixture(autouse=True)
def disable_real_mcp_config(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_file = tmp_path / "mcp.empty.json"
    config_file.write_text('{"mcpServers": {}}', encoding="utf-8")
    monkeypatch.setenv("EXCELMANUS_MCP_CONFIG", str(config_file))


@pytest.fixture
def manager(config: ExcelManusConfig, registry: ToolRegistry) -> SessionManager:
    return SessionManager(
        max_sessions=config.max_sessions,
        ttl_seconds=config.session_ttl_seconds,
        config=config,
        registry=registry,
    )


class TestUpdateAuxConfig:
    """AgentEngine.update_aux_config 单元测试。"""

    @pytest.mark.asyncio
    async def test_engine_aux_model_updated(self, manager: SessionManager) -> None:
        """update_aux_config 应更新 engine._config.aux_model。"""
        sid, engine = await manager.acquire_for_chat(None)
        await manager.release_for_chat(sid)

        assert engine._config.aux_model == "qwen-flash-old"

        engine.update_aux_config(
            aux_model="gpt-4o-mini",
            aux_api_key="new-key",
            aux_base_url="https://aux-new.example.com/v1",
        )

        assert engine._config.aux_model == "gpt-4o-mini"
        assert engine._config.aux_api_key == "new-key"
        assert engine._config.aux_base_url == "https://aux-new.example.com/v1"

    @pytest.mark.asyncio
    async def test_router_model_follows_aux(self, manager: SessionManager) -> None:
        """更新 AUX 后路由模型应同步切换。"""
        sid, engine = await manager.acquire_for_chat(None)
        await manager.release_for_chat(sid)

        assert engine._router_model == "qwen-flash-old"
        assert engine._router_follow_active_model is False

        engine.update_aux_config(
            aux_model="gpt-4o-mini",
            aux_api_key="new-key",
            aux_base_url=None,
        )

        assert engine._router_model == "gpt-4o-mini"
        assert engine._router_follow_active_model is False

    @pytest.mark.asyncio
    async def test_advisor_model_follows_aux(self, manager: SessionManager) -> None:
        """更新 AUX 后窗口感知顾问模型应同步切换。"""
        sid, engine = await manager.acquire_for_chat(None)
        await manager.release_for_chat(sid)

        assert engine._advisor_model == "qwen-flash-old"

        engine.update_aux_config(
            aux_model="gpt-4o-mini",
            aux_api_key=None,
            aux_base_url=None,
        )

        assert engine._advisor_model == "gpt-4o-mini"
        assert engine._advisor_follow_active_model is False

    @pytest.mark.asyncio
    async def test_clear_aux_falls_back_to_active_model(
        self, manager: SessionManager
    ) -> None:
        """清除 AUX 模型后应回退到跟随主模型。"""
        sid, engine = await manager.acquire_for_chat(None)
        await manager.release_for_chat(sid)

        engine.update_aux_config(
            aux_model=None,
            aux_api_key=None,
            aux_base_url=None,
        )

        assert engine._router_model == engine._active_model
        assert engine._router_follow_active_model is True
        assert engine._advisor_model == engine._active_model
        assert engine._advisor_follow_active_model is True


class TestBroadcastAuxConfig:
    """SessionManager.broadcast_aux_config 广播测试。"""

    @pytest.mark.asyncio
    async def test_broadcast_updates_all_sessions(
        self, manager: SessionManager
    ) -> None:
        """广播应更新所有活跃会话的 AUX 配置。"""
        sid1, engine1 = await manager.acquire_for_chat(None)
        await manager.release_for_chat(sid1)
        sid2, engine2 = await manager.acquire_for_chat(None)
        await manager.release_for_chat(sid2)

        assert engine1._config.aux_model == "qwen-flash-old"
        assert engine2._config.aux_model == "qwen-flash-old"

        await manager.broadcast_aux_config(
            aux_model="gpt-4o-mini",
            aux_api_key="new-key",
            aux_base_url="https://aux-new.example.com/v1",
        )

        assert engine1._config.aux_model == "gpt-4o-mini"
        assert engine2._config.aux_model == "gpt-4o-mini"
        assert engine1._router_model == "gpt-4o-mini"
        assert engine2._router_model == "gpt-4o-mini"

    @pytest.mark.asyncio
    async def test_auth_mode_copy_also_updated(
        self, config: ExcelManusConfig, registry: ToolRegistry
    ) -> None:
        """认证模式下 replace() 创建的 config 副本也应被广播更新。

        这是 bug 的核心场景：认证用户的 engine 持有 config 副本，
        全局 _config 更新不会自动传播，必须依赖广播。
        """
        # 模拟认证场景：engine 持有 replace() 副本
        copied_config = replace(config, workspace_root="/tmp/user-workspace")
        assert copied_config is not config
        assert copied_config.aux_model == "qwen-flash-old"

        mgr = SessionManager(
            max_sessions=5,
            ttl_seconds=60,
            config=copied_config,
            registry=registry,
        )
        sid, engine = await mgr.acquire_for_chat(None)
        await mgr.release_for_chat(sid)

        # 模拟全局 _config 被 API 更新但 engine 的副本未变
        assert engine._config.aux_model == "qwen-flash-old"

        # 广播应直接更新 engine 的副本
        await mgr.broadcast_aux_config(
            aux_model="gpt-4o-mini",
            aux_api_key="new-key",
            aux_base_url="https://aux-new.example.com/v1",
        )

        assert engine._config.aux_model == "gpt-4o-mini"
        assert engine._router_model == "gpt-4o-mini"
