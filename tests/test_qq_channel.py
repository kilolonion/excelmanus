"""QQ Bot 渠道单元测试。"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from excelmanus.channels.base import ChannelMessage, ChannelUser
from excelmanus.channels.qq.adapter import (
    PREFIX_C2C,
    PREFIX_GROUP,
    PREFIX_GUILD,
    QQ_MAX_MESSAGE_LEN,
    QQ_PASSIVE_REPLY_WINDOW,
    QQBotAdapter,
    parse_chat_id,
)


# ── parse_chat_id 测试 ──


class TestParseChatId:
    def test_group_prefix(self):
        t, id_ = parse_chat_id("group:abc123")
        assert t == "group"
        assert id_ == "abc123"

    def test_c2c_prefix(self):
        t, id_ = parse_chat_id("c2c:user456")
        assert t == "c2c"
        assert id_ == "user456"

    def test_guild_prefix(self):
        t, id_ = parse_chat_id("guild:ch789")
        assert t == "guild"
        assert id_ == "ch789"

    def test_no_prefix_defaults_guild(self):
        t, id_ = parse_chat_id("some_channel_id")
        assert t == "guild"
        assert id_ == "some_channel_id"

    def test_empty_id_after_prefix(self):
        t, id_ = parse_chat_id("group:")
        assert t == "group"
        assert id_ == ""


# ── QQBotAdapter 测试 ──


class TestQQBotAdapterCapabilities:
    def test_name(self):
        adapter = QQBotAdapter()
        assert adapter.name == "qq"

    def test_capabilities(self):
        adapter = QQBotAdapter()
        assert adapter.capabilities.supports_edit is False
        assert adapter.capabilities.supports_typing is False
        assert adapter.capabilities.max_message_length == QQ_MAX_MESSAGE_LEN
        assert adapter.capabilities.preferred_format == "plain"
        assert adapter.capabilities.passive_reply_window == QQ_PASSIVE_REPLY_WINDOW


class TestQQBotAdapterMsgId:
    def test_record_and_get_reply_msg_id(self):
        adapter = QQBotAdapter()
        chat_id = "group:abc"
        adapter.record_incoming_msg(chat_id, "msg123")

        msg_id = adapter._get_reply_msg_id(chat_id)
        assert msg_id == "msg123"

    def test_expired_msg_id(self):
        adapter = QQBotAdapter()
        chat_id = "c2c:user1"
        adapter._last_msg_ids[chat_id] = ("old_msg", time.monotonic() - 400)

        msg_id = adapter._get_reply_msg_id(chat_id)
        assert msg_id is None

    def test_msg_seq_increments(self):
        adapter = QQBotAdapter()
        chat_id = "group:abc"
        adapter.record_incoming_msg(chat_id, "msg1")

        seq1 = adapter._next_msg_seq(chat_id)
        seq2 = adapter._next_msg_seq(chat_id)
        assert seq1 == 1
        assert seq2 == 2

    def test_msg_seq_resets_on_new_incoming(self):
        adapter = QQBotAdapter()
        chat_id = "group:abc"
        adapter.record_incoming_msg(chat_id, "msg1")
        adapter._next_msg_seq(chat_id)
        adapter._next_msg_seq(chat_id)

        # New incoming resets seq
        adapter.record_incoming_msg(chat_id, "msg2")
        seq = adapter._next_msg_seq(chat_id)
        assert seq == 1

    def test_empty_msg_id_ignored(self):
        adapter = QQBotAdapter()
        chat_id = "group:abc"
        adapter.record_incoming_msg(chat_id, "")
        # Should not store empty msg_id
        assert adapter._get_reply_msg_id(chat_id) is None

    def test_get_reply_msg_id_returns_none_for_empty(self):
        adapter = QQBotAdapter()
        chat_id = "c2c:u1"
        # Force-store empty msg_id bypassing guard (edge case)
        adapter._last_msg_ids[chat_id] = ("", time.monotonic())
        assert adapter._get_reply_msg_id(chat_id) is None

    def test_cleanup_expired(self):
        adapter = QQBotAdapter()
        # Insert an expired entry
        adapter._last_msg_ids["group:old"] = ("m1", time.monotonic() - 400)
        adapter._msg_seq["group:old"] = 3
        # Insert a fresh entry
        adapter._last_msg_ids["group:new"] = ("m2", time.monotonic())
        adapter._msg_seq["group:new"] = 1

        adapter._cleanup_expired()

        assert "group:old" not in adapter._last_msg_ids
        assert "group:old" not in adapter._msg_seq
        assert "group:new" in adapter._last_msg_ids


class TestQQBotAdapterSend:
    @pytest.mark.asyncio
    async def test_send_text_group(self):
        adapter = QQBotAdapter()
        mock_api = AsyncMock()
        mock_api.post_group_message = AsyncMock(return_value={"id": "resp1"})
        adapter.set_api(mock_api)
        adapter.record_incoming_msg("group:g1", "in_msg_1")

        await adapter.send_text("group:g1", "hello group")

        mock_api.post_group_message.assert_called_once()
        call_kwargs = mock_api.post_group_message.call_args[1]
        assert call_kwargs["group_openid"] == "g1"
        assert call_kwargs["content"] == "hello group"
        assert call_kwargs["msg_type"] == 0
        assert call_kwargs["msg_id"] == "in_msg_1"

    @pytest.mark.asyncio
    async def test_send_text_c2c(self):
        adapter = QQBotAdapter()
        mock_api = AsyncMock()
        mock_api.post_c2c_message = AsyncMock(return_value={"id": "resp2"})
        adapter.set_api(mock_api)
        adapter.record_incoming_msg("c2c:u1", "in_msg_2")

        await adapter.send_text("c2c:u1", "hello user")

        mock_api.post_c2c_message.assert_called_once()
        call_kwargs = mock_api.post_c2c_message.call_args[1]
        assert call_kwargs["openid"] == "u1"
        assert call_kwargs["content"] == "hello user"

    @pytest.mark.asyncio
    async def test_send_text_guild(self):
        adapter = QQBotAdapter()
        mock_api = AsyncMock()
        mock_api.post_message = AsyncMock(return_value={"id": "resp3"})
        adapter.set_api(mock_api)
        adapter.record_incoming_msg("guild:ch1", "in_msg_3")

        await adapter.send_text("guild:ch1", "hello channel")

        mock_api.post_message.assert_called_once()
        call_kwargs = mock_api.post_message.call_args[1]
        assert call_kwargs["channel_id"] == "ch1"
        assert call_kwargs["content"] == "hello channel"

    @pytest.mark.asyncio
    async def test_send_text_no_api(self):
        adapter = QQBotAdapter()
        # Should not raise, just log warning
        await adapter.send_text("group:g1", "no api")

    @pytest.mark.asyncio
    async def test_send_text_api_error(self):
        adapter = QQBotAdapter()
        mock_api = AsyncMock()
        mock_api.post_group_message = AsyncMock(side_effect=Exception("network error"))
        adapter.set_api(mock_api)
        adapter.record_incoming_msg("group:g1", "in_msg")

        # Should not raise
        await adapter.send_text("group:g1", "will fail")

    @pytest.mark.asyncio
    async def test_send_markdown_delegates_to_text(self):
        adapter = QQBotAdapter()
        mock_api = AsyncMock()
        mock_api.post_group_message = AsyncMock(return_value={"id": "r"})
        adapter.set_api(mock_api)
        adapter.record_incoming_msg("group:g1", "m1")

        await adapter.send_markdown("group:g1", "**bold**")

        mock_api.post_group_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_file_fallback(self):
        adapter = QQBotAdapter()
        mock_api = AsyncMock()
        mock_api.post_c2c_message = AsyncMock(return_value={"id": "r"})
        adapter.set_api(mock_api)
        adapter.record_incoming_msg("c2c:u1", "m1")

        await adapter.send_file("c2c:u1", b"\x00" * 1024, "test.xlsx")

        # Should send a text message about the file
        mock_api.post_c2c_message.assert_called_once()
        content = mock_api.post_c2c_message.call_args[1]["content"]
        assert "test.xlsx" in content
        assert "1.0 KB" in content

    @pytest.mark.asyncio
    async def test_send_approval_card(self):
        adapter = QQBotAdapter()
        mock_api = AsyncMock()
        mock_api.post_group_message = AsyncMock(return_value={"id": "r"})
        adapter.set_api(mock_api)
        adapter.record_incoming_msg("group:g1", "m1")

        await adapter.send_approval_card(
            "group:g1", "apr_123", "write_cell", "yellow",
            {"cell": "A1", "value": "100"},
        )

        content = mock_api.post_group_message.call_args[1]["content"]
        assert "操作审批" in content
        assert "write_cell" in content
        assert "/approve apr_123" in content
        assert "/reject apr_123" in content

    @pytest.mark.asyncio
    async def test_send_question_card_with_options(self):
        adapter = QQBotAdapter()
        mock_api = AsyncMock()
        mock_api.post_c2c_message = AsyncMock(return_value={"id": "r"})
        adapter.set_api(mock_api)
        adapter.record_incoming_msg("c2c:u1", "m1")

        await adapter.send_question_card(
            "c2c:u1", "q_123", "选择操作", "请选择：",
            [{"label": "选项A"}, {"label": "选项B"}],
        )

        content = mock_api.post_c2c_message.call_args[1]["content"]
        assert "选项A" in content
        assert "选项B" in content
        assert "回复编号" in content

    @pytest.mark.asyncio
    async def test_send_question_card_no_options(self):
        adapter = QQBotAdapter()
        mock_api = AsyncMock()
        mock_api.post_c2c_message = AsyncMock(return_value={"id": "r"})
        adapter.set_api(mock_api)
        adapter.record_incoming_msg("c2c:u1", "m1")

        await adapter.send_question_card(
            "c2c:u1", "q_456", "", "输入名称", [],
        )

        content = mock_api.post_c2c_message.call_args[1]["content"]
        assert "直接回复文字即可" in content

    @pytest.mark.asyncio
    async def test_show_typing_noop(self):
        adapter = QQBotAdapter()
        # Should not raise
        await adapter.show_typing("group:g1")

    @pytest.mark.asyncio
    async def test_send_text_return_id(self):
        adapter = QQBotAdapter()
        mock_api = AsyncMock()
        mock_api.post_group_message = AsyncMock(return_value={"id": "sent_msg_1"})
        adapter.set_api(mock_api)
        adapter.record_incoming_msg("group:g1", "m1")

        msg_id = await adapter.send_text_return_id("group:g1", "test")
        assert msg_id == "sent_msg_1"

    @pytest.mark.asyncio
    async def test_send_text_return_id_none_result(self):
        adapter = QQBotAdapter()
        mock_api = AsyncMock()
        mock_api.post_group_message = AsyncMock(return_value=None)
        adapter.set_api(mock_api)
        adapter.record_incoming_msg("group:g1", "m1")

        msg_id = await adapter.send_text_return_id("group:g1", "test")
        assert msg_id == ""

    @pytest.mark.asyncio
    async def test_send_text_return_id_chunks_long_text(self):
        adapter = QQBotAdapter()
        mock_api = AsyncMock()
        mock_api.post_group_message = AsyncMock(return_value={"id": "first_msg"})
        adapter.set_api(mock_api)
        adapter.record_incoming_msg("group:g1", "m1")

        # Create text longer than QQ_MAX_MESSAGE_LEN
        long_text = "a" * 3000
        msg_id = await adapter.send_text_return_id("group:g1", long_text)
        assert msg_id == "first_msg"
        # Should have been called multiple times (chunked)
        assert mock_api.post_group_message.call_count >= 2


# ── handlers.py 测试 ──


class TestHandlersParseCommand:
    def test_valid_command(self):
        from excelmanus.channels.qq.handlers import _parse_command
        is_cmd, cmd, args = _parse_command("/help")
        assert is_cmd is True
        assert cmd == "help"
        assert args == []

    def test_command_with_args(self):
        from excelmanus.channels.qq.handlers import _parse_command
        is_cmd, cmd, args = _parse_command("/model gpt-4o")
        assert is_cmd is True
        assert cmd == "model"
        assert args == ["gpt-4o"]

    def test_not_a_command(self):
        from excelmanus.channels.qq.handlers import _parse_command
        is_cmd, cmd, args = _parse_command("hello world")
        assert is_cmd is False
        assert cmd == ""

    def test_command_with_multiple_args(self):
        from excelmanus.channels.qq.handlers import _parse_command
        is_cmd, cmd, args = _parse_command("/approve abc 123")
        assert is_cmd is True
        assert cmd == "approve"
        assert args == ["abc", "123"]


class TestHandlersCleanAt:
    def test_clean_at_prefix(self):
        from excelmanus.channels.qq.handlers import _clean_at_prefix
        assert _clean_at_prefix("<@!12345> hello") == "hello"
        assert _clean_at_prefix("<@!12345>  /help") == "/help"
        assert _clean_at_prefix("no at prefix") == "no at prefix"
        assert _clean_at_prefix("") == ""


class TestHandleGroupMessage:
    @pytest.mark.asyncio
    async def test_group_message_basic(self):
        from excelmanus.channels.qq.handlers import _handle_group_message

        adapter = QQBotAdapter()
        handler = AsyncMock()

        message = MagicMock()
        message.group_openid = "group_abc"
        message.id = "msg_001"
        message.content = "<@!999> 帮我分析数据"
        author = MagicMock()
        author.member_openid = "user_001"
        message.author = author

        await _handle_group_message(adapter, handler, message)

        handler.handle_message.assert_called_once()
        msg: ChannelMessage = handler.handle_message.call_args[0][0]
        assert msg.channel == "qq"
        assert msg.chat_id == "group:group_abc"
        assert msg.user.user_id == "user_001"
        assert msg.text == "帮我分析数据"
        assert msg.is_command is False

    @pytest.mark.asyncio
    async def test_group_message_command(self):
        from excelmanus.channels.qq.handlers import _handle_group_message

        adapter = QQBotAdapter()
        handler = AsyncMock()

        message = MagicMock()
        message.group_openid = "group_abc"
        message.id = "msg_002"
        message.content = "<@!999> /help"
        author = MagicMock()
        author.member_openid = "user_002"
        message.author = author

        await _handle_group_message(adapter, handler, message)

        msg: ChannelMessage = handler.handle_message.call_args[0][0]
        assert msg.is_command is True
        assert msg.command == "help"

    @pytest.mark.asyncio
    async def test_group_message_empty_content(self):
        from excelmanus.channels.qq.handlers import _handle_group_message

        adapter = QQBotAdapter()
        handler = AsyncMock()

        message = MagicMock()
        message.group_openid = "group_abc"
        message.id = "msg_003"
        message.content = "<@!999>  "
        author = MagicMock()
        author.member_openid = "user_003"
        message.author = author

        await _handle_group_message(adapter, handler, message)
        handler.handle_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_group_message_records_msg_id(self):
        from excelmanus.channels.qq.handlers import _handle_group_message

        adapter = QQBotAdapter()
        handler = AsyncMock()

        message = MagicMock()
        message.group_openid = "group_xyz"
        message.id = "msg_100"
        message.content = "hello"
        author = MagicMock()
        author.member_openid = "u1"
        message.author = author

        await _handle_group_message(adapter, handler, message)

        # Adapter should have cached the msg_id
        assert adapter._get_reply_msg_id("group:group_xyz") == "msg_100"

    @pytest.mark.asyncio
    async def test_group_message_no_user_openid(self):
        from excelmanus.channels.qq.handlers import _handle_group_message

        adapter = QQBotAdapter()
        handler = AsyncMock()

        message = MagicMock()
        message.group_openid = "group_abc"
        message.id = "msg_004"
        message.content = "<@!999> hello"
        author = MagicMock()
        author.member_openid = ""
        message.author = author

        await _handle_group_message(adapter, handler, message)
        handler.handle_message.assert_not_called()


class TestHandleC2CMessage:
    @pytest.mark.asyncio
    async def test_c2c_message_basic(self):
        from excelmanus.channels.qq.handlers import _handle_c2c_message

        adapter = QQBotAdapter()
        handler = AsyncMock()

        message = MagicMock()
        message.id = "msg_c2c_001"
        message.content = "你好"
        author = MagicMock()
        author.user_openid = "openid_abc"
        message.author = author

        await _handle_c2c_message(adapter, handler, message)

        handler.handle_message.assert_called_once()
        msg: ChannelMessage = handler.handle_message.call_args[0][0]
        assert msg.channel == "qq"
        assert msg.chat_id == "c2c:openid_abc"
        assert msg.user.user_id == "openid_abc"
        assert msg.text == "你好"

    @pytest.mark.asyncio
    async def test_c2c_message_command(self):
        from excelmanus.channels.qq.handlers import _handle_c2c_message

        adapter = QQBotAdapter()
        handler = AsyncMock()

        message = MagicMock()
        message.id = "msg_c2c_002"
        message.content = "/new"
        author = MagicMock()
        author.user_openid = "openid_def"
        message.author = author

        await _handle_c2c_message(adapter, handler, message)

        msg: ChannelMessage = handler.handle_message.call_args[0][0]
        assert msg.is_command is True
        assert msg.command == "new"

    @pytest.mark.asyncio
    async def test_c2c_message_no_user(self):
        from excelmanus.channels.qq.handlers import _handle_c2c_message

        adapter = QQBotAdapter()
        handler = AsyncMock()

        message = MagicMock()
        message.id = "msg_c2c_003"
        message.content = "hello"
        message.author = None

        await _handle_c2c_message(adapter, handler, message)
        handler.handle_message.assert_not_called()


class TestHandleGuildMessage:
    @pytest.mark.asyncio
    async def test_guild_message_basic(self):
        from excelmanus.channels.qq.handlers import _handle_guild_message

        adapter = QQBotAdapter()
        handler = AsyncMock()

        message = MagicMock()
        message.channel_id = "ch_001"
        message.id = "msg_guild_001"
        message.content = "<@!999> 查看数据"
        author = MagicMock()
        author.id = "guild_user_1"
        author.username = "Alice"
        message.author = author

        await _handle_guild_message(adapter, handler, message)

        handler.handle_message.assert_called_once()
        msg: ChannelMessage = handler.handle_message.call_args[0][0]
        assert msg.channel == "qq"
        assert msg.chat_id == "guild:ch_001"
        assert msg.user.user_id == "guild_user_1"
        assert msg.user.username == "Alice"
        assert msg.text == "查看数据"

    @pytest.mark.asyncio
    async def test_guild_message_no_user_id(self):
        from excelmanus.channels.qq.handlers import _handle_guild_message

        adapter = QQBotAdapter()
        handler = AsyncMock()

        message = MagicMock()
        message.channel_id = "ch_001"
        message.id = "msg_guild_002"
        message.content = "<@!999> hello"
        author = MagicMock()
        author.id = ""
        author.username = "Alice"
        message.author = author

        await _handle_guild_message(adapter, handler, message)
        handler.handle_message.assert_not_called()


# ── launcher.py QQ 分支测试 ──


class TestLauncherQQBuilder:
    def test_qq_in_channel_builders(self):
        from excelmanus.channels.launcher import _CHANNEL_BUILDERS
        assert "qq" in _CHANNEL_BUILDERS
        assert "build_qq_app" in _CHANNEL_BUILDERS["qq"]
