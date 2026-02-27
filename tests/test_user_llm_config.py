"""测试用户自定义 LLM 配置接入 SessionManager 的核心逻辑。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from unittest.mock import MagicMock, patch

import pytest

from excelmanus.config import ExcelManusConfig


# ── 最小化 fixtures ──────────────────────────────────────


def _minimal_config(**overrides) -> ExcelManusConfig:
    defaults = dict(
        api_key="global-key",
        base_url="https://api.global.com/v1",
        model="gpt-4o",
    )
    defaults.update(overrides)
    return ExcelManusConfig(**defaults)


@dataclass
class _FakeUserRecord:
    id: str = "user-123"
    llm_api_key: str | None = None
    llm_base_url: str | None = None
    llm_model: str | None = None


class _FakeUserStore:
    """最小化 UserStore 替身。"""

    def __init__(self, records: dict[str, _FakeUserRecord] | None = None):
        self._records = records or {}

    def get_by_id(self, user_id: str) -> _FakeUserRecord | None:
        return self._records.get(user_id)


# ── 测试 ──────────────────────────────────────────────────


class TestUserLLMConfigOverride:
    """验证 SessionManager._create_engine_with_history 正确读取用户 LLM 配置。"""

    def _make_session_manager(self, config, user_store=None):
        """创建一个最小化的 SessionManager 用于测试。"""
        from excelmanus.session import SessionManager

        # Mock 掉不需要的依赖
        registry = MagicMock()
        sm = SessionManager(
            max_sessions=10,
            ttl_seconds=300,
            config=config,
            registry=registry,
            user_store=user_store,
        )
        return sm

    @patch("excelmanus.session.AgentEngine")
    @patch("excelmanus.session.IsolatedWorkspace")
    def test_user_with_custom_config_overrides_global(
        self, mock_ws_cls, mock_engine_cls
    ):
        """用户设置了自定义 LLM 配置时，engine 应使用用户的配置。"""
        mock_ws = MagicMock()
        mock_ws.root_dir = "/tmp/test-ws/users/user-123"
        mock_ws_cls.resolve.return_value = mock_ws

        mock_engine = MagicMock()
        mock_engine_cls.return_value = mock_engine

        user_store = _FakeUserStore({
            "user-123": _FakeUserRecord(
                id="user-123",
                llm_api_key="user-key-abc",
                llm_base_url="https://api.user.com/v1",
                llm_model="claude-sonnet-4",
            ),
        })

        config = _minimal_config()
        sm = self._make_session_manager(config, user_store=user_store)

        # 禁用不相关的组件
        sm._create_memory_components = MagicMock(return_value=(None, None))
        sm._resolve_user_config_store = MagicMock(return_value=None)

        engine = sm._create_engine_with_history("sess-1", user_id="user-123")

        # 验证传给 AgentEngine 的 config 使用了用户的值
        call_kwargs = mock_engine_cls.call_args
        engine_config = call_kwargs.kwargs.get("config") or call_kwargs[1].get("config")
        assert engine_config.api_key == "user-key-abc"
        assert engine_config.base_url == "https://api.user.com/v1"
        assert engine_config.model == "claude-sonnet-4"

    @patch("excelmanus.session.AgentEngine")
    @patch("excelmanus.session.IsolatedWorkspace")
    def test_user_without_custom_config_uses_global(
        self, mock_ws_cls, mock_engine_cls
    ):
        """用户未设置自定义配置时，engine 应使用全局配置。"""
        mock_ws = MagicMock()
        mock_ws.root_dir = "/tmp/test-ws/users/user-456"
        mock_ws_cls.resolve.return_value = mock_ws

        mock_engine = MagicMock()
        mock_engine_cls.return_value = mock_engine

        user_store = _FakeUserStore({
            "user-456": _FakeUserRecord(id="user-456"),  # 无自定义配置
        })

        config = _minimal_config()
        sm = self._make_session_manager(config, user_store=user_store)
        sm._create_memory_components = MagicMock(return_value=(None, None))
        sm._resolve_user_config_store = MagicMock(return_value=None)

        engine = sm._create_engine_with_history("sess-2", user_id="user-456")

        call_kwargs = mock_engine_cls.call_args
        engine_config = call_kwargs.kwargs.get("config") or call_kwargs[1].get("config")
        assert engine_config.api_key == "global-key"
        assert engine_config.base_url == "https://api.global.com/v1"
        assert engine_config.model == "gpt-4o"

    @patch("excelmanus.session.AgentEngine")
    @patch("excelmanus.session.IsolatedWorkspace")
    def test_user_partial_override(
        self, mock_ws_cls, mock_engine_cls
    ):
        """用户仅设置部分字段时，未设置字段应继承全局配置。"""
        mock_ws = MagicMock()
        mock_ws.root_dir = "/tmp/test-ws/users/user-789"
        mock_ws_cls.resolve.return_value = mock_ws

        mock_engine = MagicMock()
        mock_engine_cls.return_value = mock_engine

        # 用户只设置了 model，没有自定义 api_key 和 base_url
        user_store = _FakeUserStore({
            "user-789": _FakeUserRecord(
                id="user-789",
                llm_model="qwen-plus",
            ),
        })

        config = _minimal_config()
        sm = self._make_session_manager(config, user_store=user_store)
        sm._create_memory_components = MagicMock(return_value=(None, None))
        sm._resolve_user_config_store = MagicMock(return_value=None)

        engine = sm._create_engine_with_history("sess-3", user_id="user-789")

        call_kwargs = mock_engine_cls.call_args
        engine_config = call_kwargs.kwargs.get("config") or call_kwargs[1].get("config")
        # model 被用户覆盖
        assert engine_config.model == "qwen-plus"
        # api_key 和 base_url 继承全局
        assert engine_config.api_key == "global-key"
        assert engine_config.base_url == "https://api.global.com/v1"

    @patch("excelmanus.session.AgentEngine")
    @patch("excelmanus.session.IsolatedWorkspace")
    def test_no_user_store_uses_global(
        self, mock_ws_cls, mock_engine_cls
    ):
        """未注入 UserStore 时（认证未启用），全局配置不受影响。"""
        mock_ws = MagicMock()
        mock_ws.root_dir = "/tmp/test-ws/users/user-000"
        mock_ws_cls.resolve.return_value = mock_ws

        mock_engine = MagicMock()
        mock_engine_cls.return_value = mock_engine

        config = _minimal_config()
        sm = self._make_session_manager(config, user_store=None)
        sm._create_memory_components = MagicMock(return_value=(None, None))
        sm._resolve_user_config_store = MagicMock(return_value=None)

        engine = sm._create_engine_with_history("sess-4", user_id="user-000")

        call_kwargs = mock_engine_cls.call_args
        engine_config = call_kwargs.kwargs.get("config") or call_kwargs[1].get("config")
        assert engine_config.api_key == "global-key"
        assert engine_config.base_url == "https://api.global.com/v1"
        assert engine_config.model == "gpt-4o"

    @patch("excelmanus.session.AgentEngine")
    @patch("excelmanus.session.IsolatedWorkspace")
    def test_anonymous_session_uses_global(
        self, mock_ws_cls, mock_engine_cls
    ):
        """匿名会话（user_id=None）不触发用户配置查询。"""
        mock_ws = MagicMock()
        mock_ws.root_dir = "/tmp/test-ws"
        mock_ws_cls.resolve.return_value = mock_ws

        mock_engine = MagicMock()
        mock_engine_cls.return_value = mock_engine

        user_store = _FakeUserStore()
        config = _minimal_config()
        sm = self._make_session_manager(config, user_store=user_store)
        sm._create_memory_components = MagicMock(return_value=(None, None))
        sm._resolve_user_config_store = MagicMock(return_value=None)

        engine = sm._create_engine_with_history("sess-5", user_id=None)

        call_kwargs = mock_engine_cls.call_args
        engine_config = call_kwargs.kwargs.get("config") or call_kwargs[1].get("config")
        assert engine_config.api_key == "global-key"
        assert engine_config.model == "gpt-4o"
