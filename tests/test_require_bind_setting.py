"""渠道强制绑定动态配置测试。

覆盖：
- MessageHandler._require_bind property 优先级：env var > config_kv > default
- config_store 动态切换后立即生效
- env var 锁定时 config_kv 不生效
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from excelmanus.channels.base import ChannelMessage, ChannelUser
from excelmanus.channels.session_store import SessionStore


# ── Fake config store ──


class _FakeConfigStore:
    """Mock GlobalConfigStore."""

    def __init__(self):
        self._kv: dict[str, str] = {}

    def get(self, key: str, default: str = "") -> str:
        return self._kv.get(key, default)

    def set(self, key: str, value: str) -> None:
        self._kv[key] = value


# ── Fixtures ──


@pytest.fixture
def mock_adapter():
    adapter = MagicMock()
    adapter.name = "telegram"
    adapter.send_text = AsyncMock()
    adapter.show_typing = AsyncMock()
    adapter.send_progress = AsyncMock()
    adapter.send_file = AsyncMock()
    adapter.send_approval_card = AsyncMock()
    adapter.send_question_card = AsyncMock()
    adapter.update_approval_result = AsyncMock()
    adapter.update_question_result = AsyncMock()
    adapter.send_staged_card = AsyncMock()
    return adapter


@pytest.fixture
def config_store():
    return _FakeConfigStore()


@pytest.fixture
def handler(mock_adapter, config_store):
    from excelmanus.channels.message_handler import MessageHandler

    api_client = MagicMock()
    api_client.stream_chat = AsyncMock(return_value=iter([]))
    store = SessionStore()
    bind_manager = MagicMock()
    bind_manager.check_bind_status = MagicMock(return_value=None)

    h = MessageHandler(
        adapter=mock_adapter,
        api_client=api_client,
        session_store=store,
        bind_manager=bind_manager,
        config_store=config_store,
    )
    return h


# ── Tests ──


class TestRequireBindProperty:
    """_require_bind property 优先级和动态切换。"""

    def test_default_is_false(self, handler):
        """无 env var、无 config_kv 时默认 False。"""
        assert handler._require_bind is False

    def test_config_store_true(self, handler, config_store):
        """config_kv 设置 true 后 _require_bind 为 True。"""
        config_store.set("channel_require_bind", "true")
        assert handler._require_bind is True

    def test_config_store_false(self, handler, config_store):
        """config_kv 设置 false 后 _require_bind 为 False。"""
        config_store.set("channel_require_bind", "false")
        assert handler._require_bind is False

    def test_config_store_dynamic_toggle(self, handler, config_store):
        """config_kv 动态切换立即生效。"""
        assert handler._require_bind is False
        config_store.set("channel_require_bind", "true")
        assert handler._require_bind is True
        config_store.set("channel_require_bind", "false")
        assert handler._require_bind is False

    @patch.dict(os.environ, {"EXCELMANUS_CHANNEL_REQUIRE_BIND": "true"})
    def test_env_var_true_overrides_config(self, handler, config_store):
        """env var=true 时 config_kv 不生效。"""
        config_store.set("channel_require_bind", "false")
        assert handler._require_bind is True

    @patch.dict(os.environ, {"EXCELMANUS_CHANNEL_REQUIRE_BIND": "false"})
    def test_env_var_false_overrides_config(self, handler, config_store):
        """env var=false 时 config_kv 不生效。"""
        config_store.set("channel_require_bind", "true")
        assert handler._require_bind is False

    @patch.dict(os.environ, {"EXCELMANUS_CHANNEL_REQUIRE_BIND": "1"})
    def test_env_var_1(self, handler):
        """env var=1 等同 true。"""
        assert handler._require_bind is True

    @patch.dict(os.environ, {"EXCELMANUS_CHANNEL_REQUIRE_BIND": "yes"})
    def test_env_var_yes(self, handler):
        """env var=yes 等同 true。"""
        assert handler._require_bind is True

    def test_no_config_store_defaults_false(self, mock_adapter):
        """config_store=None 时默认 False。"""
        from excelmanus.channels.message_handler import MessageHandler

        h = MessageHandler(
            adapter=mock_adapter,
            api_client=MagicMock(),
            session_store=SessionStore(),
        )
        assert h._require_bind is False


class TestRequireBindEnforcement:
    """验证强制绑定开启后 handle_message 行为。"""

    @pytest.mark.asyncio
    async def test_unbound_user_blocked_when_require_bind(
        self, handler, config_store, mock_adapter,
    ):
        """强制绑定开启 + 未绑定用户 → 发送提示消息并拦截。"""
        config_store.set("channel_require_bind", "true")

        msg = ChannelMessage(
            channel="telegram",
            user=ChannelUser(user_id="999"),
            chat_id="chat1",
            text="hello",
            is_command=False,
            command="",
            command_args=[],
        )
        await handler.handle_message(msg)

        # 应发送绑定提示
        mock_adapter.send_text.assert_called()
        call_text = mock_adapter.send_text.call_args[0][1]
        assert "绑定" in call_text or "/bind" in call_text

    @pytest.mark.asyncio
    async def test_bind_command_allowed_when_require_bind(
        self, handler, config_store, mock_adapter,
    ):
        """强制绑定开启 + /bind 命令 → 不被拦截。"""
        config_store.set("channel_require_bind", "true")

        msg = ChannelMessage(
            channel="telegram",
            user=ChannelUser(user_id="999"),
            chat_id="chat1",
            text="/bind",
            is_command=True,
            command="bind",
            command_args=[],
        )
        await handler.handle_message(msg)

        # /bind 不应被拦截（应调用 _cmd_bind，而非发送强制绑定提示）
        calls = mock_adapter.send_text.call_args_list
        if calls:
            # 检查不是强制绑定拦截消息
            for call in calls:
                text = call[0][1]
                assert "此 Bot 要求绑定" not in text
