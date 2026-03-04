"""QQ Bot 渠道单元测试。"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from excelmanus.channels.base import ChannelMessage, ChannelUser
from excelmanus.channels.qq.adapter import (
    MAX_PASSIVE_SEQ,
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
    async def test_send_file_raises_not_implemented(self):
        """send_file 抛出 NotImplementedError，由 handler 3 级回退处理。"""
        adapter = QQBotAdapter()
        with pytest.raises(NotImplementedError):
            await adapter.send_file("c2c:u1", b"\x00" * 1024, "test.xlsx")

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


# ── P0: 被动/主动消息切换 + msg_seq 上限 测试 ──


class TestGetReplyContext:
    """P0-1/P0-2: _get_reply_context 被动/主动切换逻辑。"""

    def test_passive_within_window_and_seq(self):
        """msg_id 有效且 seq 未超限 → 返回 (msg_id, seq)。"""
        adapter = QQBotAdapter()
        adapter.record_incoming_msg("group:g1", "msg_001")

        msg_id, seq = adapter._get_reply_context("group:g1")
        assert msg_id == "msg_001"
        assert seq == 1

    def test_passive_seq_increments(self):
        """多次调用 seq 递增。"""
        adapter = QQBotAdapter()
        adapter.record_incoming_msg("group:g1", "msg_001")

        _, seq1 = adapter._get_reply_context("group:g1")
        _, seq2 = adapter._get_reply_context("group:g1")
        _, seq3 = adapter._get_reply_context("group:g1")
        assert seq1 == 1
        assert seq2 == 2
        assert seq3 == 3

    def test_active_after_seq_exceeds_max(self):
        """seq 超过 MAX_PASSIVE_SEQ 后降级为主动消息。"""
        adapter = QQBotAdapter()
        adapter.record_incoming_msg("group:g1", "msg_001")

        # 消耗完所有被动 seq
        for i in range(MAX_PASSIVE_SEQ):
            msg_id, seq = adapter._get_reply_context("group:g1")
            assert msg_id == "msg_001"
            assert seq == i + 1

        # 第 MAX_PASSIVE_SEQ+1 次应降级
        msg_id, seq = adapter._get_reply_context("group:g1")
        assert msg_id is None
        assert seq is None

    def test_active_when_window_expired(self):
        """窗口过期后降级为主动消息。"""
        adapter = QQBotAdapter()
        # 强制设置过期 msg_id
        adapter._last_msg_ids["group:g1"] = ("old_msg", time.monotonic() - 400)
        adapter._msg_seq["group:g1"] = 1

        msg_id, seq = adapter._get_reply_context("group:g1")
        assert msg_id is None
        assert seq is None

    def test_active_for_unknown_chat(self):
        """未记录过的 chat_id → 主动消息。"""
        adapter = QQBotAdapter()
        msg_id, seq = adapter._get_reply_context("group:unknown")
        assert msg_id is None
        assert seq is None

    def test_new_incoming_resets_seq_for_context(self):
        """新入站消息重置 seq 后，_get_reply_context 重新从 1 开始。"""
        adapter = QQBotAdapter()
        adapter.record_incoming_msg("group:g1", "msg_001")

        # 消耗完所有 seq
        for _ in range(MAX_PASSIVE_SEQ):
            adapter._get_reply_context("group:g1")
        # 已降级
        msg_id, _ = adapter._get_reply_context("group:g1")
        assert msg_id is None

        # 新消息到达，重置
        adapter.record_incoming_msg("group:g1", "msg_002")
        msg_id, seq = adapter._get_reply_context("group:g1")
        assert msg_id == "msg_002"
        assert seq == 1


class TestSendToChatPassiveActive:
    """P0-1/P0-2: _send_to_chat 被动/主动消息路由。"""

    @pytest.mark.asyncio
    async def test_group_passive_includes_msg_id(self):
        """被动回复时 kwargs 包含 msg_id 和 msg_seq。"""
        adapter = QQBotAdapter()
        mock_api = AsyncMock()
        mock_api.post_group_message = AsyncMock(return_value={"id": "r1"})
        adapter.set_api(mock_api)
        adapter.record_incoming_msg("group:g1", "in_msg")

        await adapter.send_text("group:g1", "hello")

        kw = mock_api.post_group_message.call_args[1]
        assert kw["msg_id"] == "in_msg"
        assert kw["msg_seq"] == 1

    @pytest.mark.asyncio
    async def test_group_active_omits_msg_id(self):
        """主动消息时 kwargs 不包含 msg_id 和 msg_seq。"""
        adapter = QQBotAdapter()
        mock_api = AsyncMock()
        mock_api.post_group_message = AsyncMock(return_value={"id": "r1"})
        adapter.set_api(mock_api)
        # 不记录 incoming msg → 无 msg_id → 主动消息

        await adapter.send_text("group:g1", "hello active")

        kw = mock_api.post_group_message.call_args[1]
        assert "msg_id" not in kw
        assert "msg_seq" not in kw
        assert kw["content"] == "hello active"

    @pytest.mark.asyncio
    async def test_group_switches_to_active_after_seq_limit(self):
        """前 N 条被动，第 N+1 条切换为主动。"""
        adapter = QQBotAdapter()
        mock_api = AsyncMock()
        mock_api.post_group_message = AsyncMock(return_value={"id": "r"})
        adapter.set_api(mock_api)
        adapter.record_incoming_msg("group:g1", "in_msg")

        # 发送 MAX_PASSIVE_SEQ 条被动消息
        for i in range(MAX_PASSIVE_SEQ):
            await adapter.send_text("group:g1", f"msg{i}")
            kw = mock_api.post_group_message.call_args[1]
            assert "msg_id" in kw, f"msg{i} should be passive"
            assert kw["msg_seq"] == i + 1

        # 第 MAX_PASSIVE_SEQ+1 条应为主动
        await adapter.send_text("group:g1", "active msg")
        kw = mock_api.post_group_message.call_args[1]
        assert "msg_id" not in kw
        assert "msg_seq" not in kw

    @pytest.mark.asyncio
    async def test_c2c_passive_includes_msg_id(self):
        adapter = QQBotAdapter()
        mock_api = AsyncMock()
        mock_api.post_c2c_message = AsyncMock(return_value={"id": "r"})
        adapter.set_api(mock_api)
        adapter.record_incoming_msg("c2c:u1", "in_msg")

        await adapter.send_text("c2c:u1", "hello")

        kw = mock_api.post_c2c_message.call_args[1]
        assert kw["msg_id"] == "in_msg"
        assert kw["msg_seq"] == 1

    @pytest.mark.asyncio
    async def test_c2c_active_omits_msg_id(self):
        adapter = QQBotAdapter()
        mock_api = AsyncMock()
        mock_api.post_c2c_message = AsyncMock(return_value={"id": "r"})
        adapter.set_api(mock_api)
        # 窗口过期
        adapter._last_msg_ids["c2c:u1"] = ("old", time.monotonic() - 400)

        await adapter.send_text("c2c:u1", "hello active")

        kw = mock_api.post_c2c_message.call_args[1]
        assert "msg_id" not in kw
        assert "msg_seq" not in kw

    @pytest.mark.asyncio
    async def test_guild_passive_includes_msg_id(self):
        adapter = QQBotAdapter()
        mock_api = AsyncMock()
        mock_api.post_message = AsyncMock(return_value={"id": "r"})
        adapter.set_api(mock_api)
        adapter.record_incoming_msg("guild:ch1", "in_msg")

        await adapter.send_text("guild:ch1", "hello")

        kw = mock_api.post_message.call_args[1]
        assert kw["msg_id"] == "in_msg"
        # guild 不传 msg_seq
        assert "msg_seq" not in kw

    @pytest.mark.asyncio
    async def test_guild_active_omits_msg_id(self):
        adapter = QQBotAdapter()
        mock_api = AsyncMock()
        mock_api.post_message = AsyncMock(return_value={"id": "r"})
        adapter.set_api(mock_api)
        # 无 incoming msg

        await adapter.send_text("guild:ch1", "hello active")

        kw = mock_api.post_message.call_args[1]
        assert "msg_id" not in kw

    @pytest.mark.asyncio
    async def test_mixed_passive_then_active_then_reset(self):
        """完整生命周期：被动 → 超限主动 → 新消息重置 → 被动。"""
        adapter = QQBotAdapter()
        mock_api = AsyncMock()
        mock_api.post_group_message = AsyncMock(return_value={"id": "r"})
        adapter.set_api(mock_api)
        adapter.record_incoming_msg("group:g1", "msg_A")

        # 被动阶段
        for i in range(MAX_PASSIVE_SEQ):
            await adapter.send_text("group:g1", f"p{i}")
            assert "msg_id" in mock_api.post_group_message.call_args[1]

        # 主动阶段
        await adapter.send_text("group:g1", "active1")
        assert "msg_id" not in mock_api.post_group_message.call_args[1]
        await adapter.send_text("group:g1", "active2")
        assert "msg_id" not in mock_api.post_group_message.call_args[1]

        # 新用户消息重置
        adapter.record_incoming_msg("group:g1", "msg_B")
        await adapter.send_text("group:g1", "back to passive")
        kw = mock_api.post_group_message.call_args[1]
        assert kw["msg_id"] == "msg_B"
        assert kw["msg_seq"] == 1


class TestKeepaliveInterval:
    """P0-1 补充：keepalive_interval 缩短到 150s。"""

    def test_batch_strategy_keepalive_default(self):
        """BatchSendStrategy 默认 keepalive_interval 应为 150s。"""
        from excelmanus.channels.output_manager import BatchSendStrategy
        adapter = QQBotAdapter()
        strategy = BatchSendStrategy(adapter, "group:g1")
        assert strategy._keepalive_interval == 150.0

    def test_batch_strategy_keepalive_within_passive_window(self):
        """keepalive_interval 应小于被动回复窗口。"""
        from excelmanus.channels.output_manager import BatchSendStrategy
        adapter = QQBotAdapter()
        strategy = BatchSendStrategy(adapter, "group:g1")
        assert strategy._keepalive_interval < QQ_PASSIVE_REPLY_WINDOW


class TestMaxPassiveSeqConstant:
    """MAX_PASSIVE_SEQ 常量基础验证。"""

    def test_value_is_positive(self):
        assert MAX_PASSIVE_SEQ > 0

    def test_value_is_reasonable(self):
        assert 3 <= MAX_PASSIVE_SEQ <= 10


class TestLauncherQQBuilder:
    def test_qq_in_channel_builders(self):
        from excelmanus.channels.launcher import _CHANNEL_BUILDERS
        assert "qq" in _CHANNEL_BUILDERS
        assert "build_qq_app" in _CHANNEL_BUILDERS["qq"]


class TestBuildQQAppEventBridge:
    """验证 build_qq_app 正确传递 event_bridge 到 MessageHandler。"""

    def test_event_bridge_param_accepted(self):
        """build_qq_app 签名包含 event_bridge 参数。"""
        import inspect
        from excelmanus.channels.qq.handlers import build_qq_app
        sig = inspect.signature(build_qq_app)
        assert "event_bridge" in sig.parameters

    def test_event_bridge_passed_to_handler(self):
        """event_bridge 被传递到 MessageHandler 实例。"""
        import sys

        mock_botpy = MagicMock()
        mock_botpy.Client = type("FakeClient", (), {
            "__init__": lambda self, **kw: None,
        })
        mock_botpy.Intents = MagicMock()
        fake_bridge = MagicMock()

        with patch.dict(sys.modules, {"botpy": mock_botpy}):
            with patch("excelmanus.channels.qq.handlers.MessageHandler") as MockHandler:
                from excelmanus.channels.qq.handlers import build_qq_app
                build_qq_app(
                    app_id="test_id",
                    secret="test_secret",
                    event_bridge=fake_bridge,
                )
                MockHandler.assert_called_once()
                call_kwargs = MockHandler.call_args[1]
                assert call_kwargs["event_bridge"] is fake_bridge

    def test_event_bridge_none_by_default(self):
        """不传 event_bridge 时默认为 None。"""
        import sys

        mock_botpy = MagicMock()
        mock_botpy.Client = type("FakeClient", (), {
            "__init__": lambda self, **kw: None,
        })
        mock_botpy.Intents = MagicMock()

        with patch.dict(sys.modules, {"botpy": mock_botpy}):
            with patch("excelmanus.channels.qq.handlers.MessageHandler") as MockHandler:
                from excelmanus.channels.qq.handlers import build_qq_app
                build_qq_app(app_id="test_id", secret="test_secret")
                call_kwargs = MockHandler.call_args[1]
                assert call_kwargs["event_bridge"] is None


class TestLauncherQQNotification:
    """验证 ChannelLauncher.send_notification 支持 QQ 渠道。"""

    @pytest.mark.asyncio
    async def test_send_notification_qq_basic(self):
        """QQ 渠道通知：调用 adapter.send_text 并返回 True。"""
        from excelmanus.channels.launcher import ChannelLauncher

        launcher = ChannelLauncher([])
        mock_adapter = AsyncMock()
        mock_handler = MagicMock()
        mock_handler.adapter = mock_adapter
        launcher._handlers["qq"] = mock_handler
        launcher._apps["qq"] = MagicMock()  # send_notification 检查 _apps

        result = await launcher.send_notification("qq", "c2c:user123", "hello notification")
        assert result is True
        mock_adapter.send_text.assert_called_once_with("c2c:user123", "hello notification")

    @pytest.mark.asyncio
    async def test_send_notification_qq_strips_html(self):
        """QQ 渠道通知：剥离 HTML 标签。"""
        from excelmanus.channels.launcher import ChannelLauncher

        launcher = ChannelLauncher([])
        mock_adapter = AsyncMock()
        mock_handler = MagicMock()
        mock_handler.adapter = mock_adapter
        launcher._handlers["qq"] = mock_handler
        launcher._apps["qq"] = MagicMock()

        await launcher.send_notification("qq", "group:g1", "<b>加粗</b>文本<br/>换行")
        sent_text = mock_adapter.send_text.call_args[0][1]
        assert "<b>" not in sent_text
        assert "<br/>" not in sent_text
        assert "加粗" in sent_text
        assert "文本" in sent_text
        assert "换行" in sent_text

    @pytest.mark.asyncio
    async def test_send_notification_qq_no_handler(self):
        """QQ handler 不可用时返回 False。"""
        from excelmanus.channels.launcher import ChannelLauncher

        launcher = ChannelLauncher([])
        launcher._apps["qq"] = MagicMock()
        # No handler set

        result = await launcher.send_notification("qq", "c2c:u1", "test")
        assert result is False

    @pytest.mark.asyncio
    async def test_send_notification_qq_no_app(self):
        """QQ app 不在 _apps 中时返回 False。"""
        from excelmanus.channels.launcher import ChannelLauncher

        launcher = ChannelLauncher([])
        # No _apps["qq"] set

        result = await launcher.send_notification("qq", "c2c:u1", "test")
        assert result is False

    @pytest.mark.asyncio
    async def test_send_notification_qq_exception_returns_false(self):
        """QQ 发送失败时捕获异常返回 False。"""
        from excelmanus.channels.launcher import ChannelLauncher

        launcher = ChannelLauncher([])
        mock_adapter = AsyncMock()
        mock_adapter.send_text.side_effect = Exception("network error")
        mock_handler = MagicMock()
        mock_handler.adapter = mock_adapter
        launcher._handlers["qq"] = mock_handler
        launcher._apps["qq"] = MagicMock()

        result = await launcher.send_notification("qq", "c2c:u1", "test")
        assert result is False


class TestGroupMessageNickname:
    """验证群消息和 C2C 消息正确提取 nickname。"""

    @pytest.mark.asyncio
    async def test_group_message_with_nickname(self):
        """群消息 author 有 nickname 时正确填充 username/display_name。"""
        from excelmanus.channels.qq.handlers import _handle_group_message

        adapter = QQBotAdapter()
        handler = AsyncMock()

        message = MagicMock()
        message.group_openid = "group_abc"
        message.id = "msg_n01"
        message.content = "<@!999> hello"
        author = MagicMock()
        author.member_openid = "user_001"
        author.nickname = "张三"
        message.author = author

        await _handle_group_message(adapter, handler, message)

        msg = handler.handle_message.call_args[0][0]
        assert msg.user.username == "张三"
        assert msg.user.display_name == "张三"

    @pytest.mark.asyncio
    async def test_group_message_without_nickname(self):
        """群消息 author 无 nickname 时 username/display_name 为空字符串。"""
        from excelmanus.channels.qq.handlers import _handle_group_message

        adapter = QQBotAdapter()
        handler = AsyncMock()

        message = MagicMock()
        message.group_openid = "group_abc"
        message.id = "msg_n02"
        message.content = "<@!999> hello"
        author = MagicMock(spec=[])  # spec=[] 确保没有额外属性
        author.member_openid = "user_002"
        message.author = author

        await _handle_group_message(adapter, handler, message)

        msg = handler.handle_message.call_args[0][0]
        assert msg.user.username == ""
        assert msg.user.display_name == ""

    @pytest.mark.asyncio
    async def test_c2c_message_with_nickname(self):
        """C2C 消息 author 有 nickname 时正确填充。"""
        from excelmanus.channels.qq.handlers import _handle_c2c_message

        adapter = QQBotAdapter()
        handler = AsyncMock()

        message = MagicMock()
        message.id = "msg_n03"
        message.content = "hello"
        author = MagicMock()
        author.user_openid = "openid_abc"
        author.nickname = "李四"
        message.author = author

        await _handle_c2c_message(adapter, handler, message)

        msg = handler.handle_message.call_args[0][0]
        assert msg.user.username == "李四"
        assert msg.user.display_name == "李四"

    @pytest.mark.asyncio
    async def test_c2c_message_without_nickname(self):
        """C2C 消息 author 无 nickname 时为空字符串。"""
        from excelmanus.channels.qq.handlers import _handle_c2c_message

        adapter = QQBotAdapter()
        handler = AsyncMock()

        message = MagicMock()
        message.id = "msg_n04"
        message.content = "hello"
        author = MagicMock(spec=[])
        author.user_openid = "openid_def"
        message.author = author

        await _handle_c2c_message(adapter, handler, message)

        msg = handler.handle_message.call_args[0][0]
        assert msg.user.username == ""
        assert msg.user.display_name == ""


class TestBatchSendKeepaliveOnToolStart:
    """验证 BatchSendStrategy 在后续 on_tool_start 中也触发 keepalive 检查。"""

    @pytest.mark.asyncio
    async def test_keepalive_triggered_on_second_tool_start(self):
        """第二个工具 start 时如果超过 keepalive 间隔应发送保活消息。"""
        from excelmanus.channels.output_manager import BatchSendStrategy

        adapter = AsyncMock()
        adapter.capabilities = QQBotAdapter().capabilities
        strategy = BatchSendStrategy(adapter, "group:g1", keepalive_interval=0.01)

        # 第一个工具：不再发送独立"处理中"（由 _delayed_hint 负责）
        await strategy.on_tool_start("tool_a")
        first_call_count = adapter.send_text.call_count
        assert first_call_count == 0  # on_tool_start 不发独立消息

        # 等待超过 keepalive 间隔
        await asyncio.sleep(0.02)

        # 第二个工具：应触发 keepalive
        await strategy.on_tool_start("tool_b")
        assert adapter.send_text.call_count > first_call_count

    @pytest.mark.asyncio
    async def test_no_keepalive_when_within_interval(self):
        """keepalive 间隔内不应重复发送保活消息。"""
        from excelmanus.channels.output_manager import BatchSendStrategy

        adapter = AsyncMock()
        adapter.capabilities = QQBotAdapter().capabilities
        strategy = BatchSendStrategy(adapter, "group:g1", keepalive_interval=999)

        await strategy.on_tool_start("tool_a")
        count_after_first = adapter.send_text.call_count
        assert count_after_first == 0  # on_tool_start 不发独立消息

        # 立即第二个工具 — 间隔未到，不应触发额外发送
        await strategy.on_tool_start("tool_b")
        assert adapter.send_text.call_count == count_after_first


class TestStripMarkdown:
    """验证 _strip_markdown 将 Markdown/HTML 混合文本降级为纯文本。"""

    def test_html_tags_stripped(self):
        from excelmanus.channels.qq.adapter import _strip_markdown
        assert "<b>" not in _strip_markdown("<b>bold</b> text")
        assert "bold" in _strip_markdown("<b>bold</b> text")

    def test_bold_asterisks(self):
        from excelmanus.channels.qq.adapter import _strip_markdown
        assert _strip_markdown("**bold**") == "bold"

    def test_italic_asterisks(self):
        from excelmanus.channels.qq.adapter import _strip_markdown
        assert _strip_markdown("*italic*") == "italic"

    def test_bold_underscores(self):
        from excelmanus.channels.qq.adapter import _strip_markdown
        assert _strip_markdown("__bold__") == "bold"

    def test_strikethrough(self):
        from excelmanus.channels.qq.adapter import _strip_markdown
        assert _strip_markdown("~~deleted~~") == "deleted"

    def test_inline_code(self):
        from excelmanus.channels.qq.adapter import _strip_markdown
        assert _strip_markdown("`code`") == "code"

    def test_link(self):
        from excelmanus.channels.qq.adapter import _strip_markdown
        result = _strip_markdown("[click here](https://example.com)")
        assert result == "click here"
        assert "https://" not in result

    def test_image(self):
        from excelmanus.channels.qq.adapter import _strip_markdown
        result = _strip_markdown("![alt text](https://img.png)")
        assert "alt text" in result
        assert "https://" not in result

    def test_heading(self):
        from excelmanus.channels.qq.adapter import _strip_markdown
        result = _strip_markdown("### Title\ncontent")
        assert "###" not in result
        assert "Title" in result
        assert "content" in result

    def test_blockquote(self):
        from excelmanus.channels.qq.adapter import _strip_markdown
        result = _strip_markdown("> quoted text")
        assert result.strip() == "quoted text"

    def test_horizontal_rule(self):
        from excelmanus.channels.qq.adapter import _strip_markdown
        result = _strip_markdown("above\n---\nbelow")
        assert "---" not in result
        assert "above" in result
        assert "below" in result

    def test_fenced_code_block(self):
        from excelmanus.channels.qq.adapter import _strip_markdown
        result = _strip_markdown("```python\nprint('hello')\n```")
        assert "```" not in result
        assert "print('hello')" in result

    def test_fenced_code_block_preserves_html(self):
        """回归测试：代码块内的 HTML 标签不应被剥离。"""
        from excelmanus.channels.qq.adapter import _strip_markdown
        result = _strip_markdown('```html\n<div class="test">hello</div>\n```')
        assert "<div" in result
        assert "hello" in result
        assert "</div>" in result

    def test_mixed_markdown_html(self):
        from excelmanus.channels.qq.adapter import _strip_markdown
        result = _strip_markdown("<b>**bold**</b> and [link](url)")
        assert "<b>" not in result
        assert "**" not in result
        assert "[" not in result
        assert "bold" in result
        assert "link" in result

    def test_plain_text_unchanged(self):
        from excelmanus.channels.qq.adapter import _strip_markdown
        assert _strip_markdown("hello world") == "hello world"

    def test_empty_string(self):
        from excelmanus.channels.qq.adapter import _strip_markdown
        assert _strip_markdown("") == ""


class TestCloseDownloadClient:
    """验证 close_download_client 正确释放全局 HTTP 客户端。"""

    @pytest.mark.asyncio
    async def test_close_when_client_exists(self):
        import excelmanus.channels.qq.handlers as h
        from excelmanus.channels.qq.handlers import close_download_client

        mock_client = AsyncMock()
        mock_client.is_closed = False
        h._download_client = mock_client

        await close_download_client()

        mock_client.aclose.assert_called_once()
        assert h._download_client is None

    @pytest.mark.asyncio
    async def test_close_when_no_client(self):
        import excelmanus.channels.qq.handlers as h
        from excelmanus.channels.qq.handlers import close_download_client

        h._download_client = None
        # Should not raise
        await close_download_client()
        assert h._download_client is None

    @pytest.mark.asyncio
    async def test_close_when_already_closed(self):
        import excelmanus.channels.qq.handlers as h
        from excelmanus.channels.qq.handlers import close_download_client

        mock_client = AsyncMock()
        mock_client.is_closed = True
        h._download_client = mock_client

        await close_download_client()
        # Should not call aclose on already-closed client
        mock_client.aclose.assert_not_called()
