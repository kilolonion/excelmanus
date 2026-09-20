"""上下文窗口热更新：设置页必须同步到已打开的对话。"""
from __future__ import annotations

from dataclasses import replace

import pytest

from excelmanus.config import (
    ExcelManusConfig,
    ModelProfile,
    is_context_window_user_pinned,
)
from excelmanus.context_budget import ContextBudget
from excelmanus.session import SessionManager
from excelmanus.tools import ToolRegistry


@pytest.fixture
def config() -> ExcelManusConfig:
    return ExcelManusConfig(
        api_key="test-key",
        base_url="https://test.example.com/v1",
        model="test-model",
        max_context_tokens=128_000,
        compaction_threshold_ratio=0.85,
        session_ttl_seconds=60,
        max_sessions=5,
        memory_enabled=False,
        workspace_root="/tmp/excelmanus-test-ctx",
        models=(
            ModelProfile(
                name="flash",
                model="deepseek-flash",
                api_key="test-key",
                base_url="https://test.example.com/v1",
            ),
        ),
    )


@pytest.fixture
def registry() -> ToolRegistry:
    return ToolRegistry()


@pytest.fixture(autouse=True)
def disable_real_mcp_config(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from excelmanus.settings_runtime import override_settings

    config_file = tmp_path / "mcp.empty.json"
    config_file.write_text('{"mcpServers": {}}', encoding="utf-8")
    override_settings({"EXCELMANUS_MCP_CONFIG": str(config_file)})


@pytest.fixture
def manager(config: ExcelManusConfig, registry: ToolRegistry) -> SessionManager:
    return SessionManager(
        max_sessions=config.max_sessions,
        ttl_seconds=config.session_ttl_seconds,
        config=config,
        registry=registry,
    )


class TestIsContextWindowUserPinned:
    def test_env_pins_even_when_value_matches_inference(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from excelmanus.settings_runtime import override_settings

        override_settings({"EXCELMANUS_MAX_CONTEXT_TOKENS": "200000"})
        assert is_context_window_user_pinned(200_000, "test-model") is True

    def test_value_mismatch_pins_without_env(self) -> None:
        assert is_context_window_user_pinned(400_000, "test-model") is True

    def test_inferred_default_is_not_pinned(self) -> None:
        assert is_context_window_user_pinned(256_000, "test-model") is False


class TestContextBudgetSetBaseTokens:
    def test_set_base_tokens_clears_adaptive_override(self) -> None:
        budget = ContextBudget(base_tokens=0, model="test-model")
        assert budget.max_tokens == 256_000
        budget.set_override(80_000, adaptive=True)
        assert budget.max_tokens == 80_000
        assert budget.set_base_tokens(1_000_000) == 1_000_000
        assert budget.max_tokens == 1_000_000
        assert budget.is_user_overridden is True


class TestApplyContextOptimization:
    @pytest.mark.asyncio
    async def test_engine_compaction_status_follows_settings(
        self, manager: SessionManager,
    ) -> None:
        sid, engine = await manager.acquire_for_chat(None)
        await manager.release_for_chat(sid)

        assert engine.get_compaction_status()["max_tokens"] == 128_000

        engine.apply_context_optimization(max_context_tokens=1_000_000)

        assert engine.max_context_tokens == 1_000_000
        status = engine.get_compaction_status()
        assert status["max_tokens"] == 1_000_000
        assert engine._config.max_context_tokens == 1_000_000

    @pytest.mark.asyncio
    async def test_threshold_update_does_not_reset_window(
        self, manager: SessionManager,
    ) -> None:
        sid, engine = await manager.acquire_for_chat(None)
        await manager.release_for_chat(sid)
        engine.apply_context_optimization(max_context_tokens=400_000)

        engine.apply_context_optimization(compaction_threshold_ratio=0.7)

        assert engine.max_context_tokens == 400_000
        assert engine.get_compaction_status()["threshold_ratio"] == 0.7

    @pytest.mark.asyncio
    async def test_pinned_window_survives_model_switch(
        self, manager: SessionManager, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from excelmanus.settings_runtime import override_settings

        override_settings({"EXCELMANUS_MAX_CONTEXT_TOKENS": "128000"})
        sid, engine = await manager.acquire_for_chat(None)
        await manager.release_for_chat(sid)

        engine.apply_context_optimization(max_context_tokens=128_000)
        engine.switch_model("flash")

        assert engine.current_model == "deepseek-flash"
        assert engine.max_context_tokens == 128_000
        assert engine.get_compaction_status()["max_tokens"] == 128_000


class TestBroadcastContextOptimization:
    @pytest.mark.asyncio
    async def test_broadcast_updates_all_sessions(
        self, manager: SessionManager,
    ) -> None:
        sid1, engine1 = await manager.acquire_for_chat(None)
        await manager.release_for_chat(sid1)
        sid2, engine2 = await manager.acquire_for_chat(None)
        await manager.release_for_chat(sid2)

        await manager.broadcast_context_optimization(max_context_tokens=1_000_000)

        assert engine1.get_compaction_status()["max_tokens"] == 1_000_000
        assert engine2.get_compaction_status()["max_tokens"] == 1_000_000

    @pytest.mark.asyncio
    async def test_config_copy_also_updated(
        self, config: ExcelManusConfig, registry: ToolRegistry,
    ) -> None:
        copied_config = replace(config, workspace_root="/tmp/user-workspace-ctx")
        mgr = SessionManager(
            max_sessions=5,
            ttl_seconds=60,
            config=copied_config,
            registry=registry,
        )
        sid, engine = await mgr.acquire_for_chat(None)
        await mgr.release_for_chat(sid)

        assert engine.get_compaction_status()["max_tokens"] == 128_000

        await mgr.broadcast_context_optimization(max_context_tokens=500_000)

        assert engine.max_context_tokens == 500_000
        assert engine.get_compaction_status()["max_tokens"] == 500_000
        assert engine._config.max_context_tokens == 500_000
