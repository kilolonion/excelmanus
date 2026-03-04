"""Bot↔Web 会话互通测试。

覆盖：
- EventBridge 跨渠道事件推送（chat_started / chat_completed / approval / question）
- origin_channel 回声过滤
- Bot 端自动同步 Web 创建的会话 ID
- MessageHandler bridge 回调对新事件类型的处理
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from excelmanus.channels.base import ChannelMessage, ChannelUser
from excelmanus.channels.event_bridge import EventBridge
from excelmanus.channels.session_store import SessionStore


# ── Fixtures ──


@pytest.fixture
def event_bridge():
    return EventBridge()


@pytest.fixture
def session_store(tmp_path):
    return SessionStore(store_path=tmp_path / "sessions.json")


@pytest.fixture
def mock_adapter():
    adapter = MagicMock()
    adapter.name = "telegram"
    adapter.send_text = AsyncMock()
    adapter.send_markdown = AsyncMock()
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
def mock_api():
    api = MagicMock()
    api.stream_chat_events = AsyncMock(return_value=AsyncMock(__aiter__=lambda s: s, __anext__=AsyncMock(side_effect=StopAsyncIteration)))
    api.list_sessions = AsyncMock(return_value=[])
    api.abort = AsyncMock()
    api.guide_message = AsyncMock()
    return api


@pytest.fixture
def handler(mock_adapter, mock_api, session_store, event_bridge):
    from excelmanus.channels.message_handler import MessageHandler
    h = MessageHandler(
        adapter=mock_adapter,
        api_client=mock_api,
        session_store=session_store,
        event_bridge=event_bridge,
    )
    return h


def _make_msg(text="hello", user_id="user1", chat_id="chat1"):
    return ChannelMessage(
        chat_id=chat_id,
        user=ChannelUser(user_id=user_id, username="testuser"),
        text=text,
    )


# ── EventBridge 基础测试 ──


class TestEventBridgeBasics:
    @pytest.mark.asyncio
    async def test_notify_returns_delivered_count(self, event_bridge):
        cb = AsyncMock()
        event_bridge.subscribe("u1", "telegram", "c1", cb)
        delivered = await event_bridge.notify("u1", "chat_completed", {"session_id": "s1"})
        assert delivered == 1
        cb.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_notify_unknown_user_returns_zero(self, event_bridge):
        delivered = await event_bridge.notify("unknown", "chat_completed", {})
        assert delivered == 0

    @pytest.mark.asyncio
    async def test_multiple_subscriptions_all_notified(self, event_bridge):
        cb1 = AsyncMock()
        cb2 = AsyncMock()
        event_bridge.subscribe("u1", "telegram", "c1", cb1)
        event_bridge.subscribe("u1", "qq", "c2", cb2)
        delivered = await event_bridge.notify("u1", "chat_completed", {"session_id": "s1"})
        assert delivered == 2
        cb1.assert_awaited_once()
        cb2.assert_awaited_once()


# ── origin_channel 回声过滤测试 ──


class TestOriginChannelFiltering:
    @pytest.mark.asyncio
    async def test_bridge_callback_skips_self_origin(self, handler, event_bridge):
        """Bot 端收到 origin_channel=telegram 的事件应跳过（防回声）。"""
        # 模拟绑定用户
        handler._bind_manager = MagicMock()
        handler._bind_manager.check_bind_status = MagicMock(return_value="auth_user_1")
        handler._auth_user_cache["telegram:user1"] = "auth_user_1"

        # 注册 bridge 订阅
        handler._ensure_bridge_subscription("chat1", "user1")

        # 发送 origin=telegram 的事件 → 应被跳过
        delivered = await event_bridge.notify("auth_user_1", "chat_completed", {
            "session_id": "s1",
            "origin_channel": "telegram",
            "reply_summary": "test reply",
        })
        assert delivered == 1  # 回调被调用
        handler.adapter.send_text.assert_not_awaited()  # 但内部跳过了

    @pytest.mark.asyncio
    async def test_bridge_callback_processes_different_origin(self, handler, event_bridge):
        """Bot 端收到 origin_channel=web 的事件应正常处理。"""
        handler._bind_manager = MagicMock()
        handler._bind_manager.check_bind_status = MagicMock(return_value="auth_user_1")
        handler._auth_user_cache["telegram:user1"] = "auth_user_1"

        handler._ensure_bridge_subscription("chat1", "user1")

        await event_bridge.notify("auth_user_1", "chat_completed", {
            "session_id": "s1",
            "origin_channel": "web",
            "reply_summary": "任务完成",
            "tool_count": 3,
        })
        handler.adapter.send_text.assert_awaited_once()
        call_text = handler.adapter.send_text.call_args[0][1]
        assert "Web 端已完成操作" in call_text
        assert "3 个工具调用" in call_text


# ── chat_started 事件测试 ──


class TestChatStartedEvent:
    @pytest.mark.asyncio
    async def test_chat_started_syncs_session_id(self, handler, event_bridge, session_store):
        """Web 端开始 chat 时，Bot 端自动同步 session_id。"""
        handler._bind_manager = MagicMock()
        handler._bind_manager.check_bind_status = MagicMock(return_value="auth_user_1")
        handler._auth_user_cache["telegram:user1"] = "auth_user_1"

        # Bot 端当前使用旧 session
        session_store.set("telegram", "chat1", "user1", "old_session")
        handler._ensure_bridge_subscription("chat1", "user1")

        # Web 端发起新 session
        await event_bridge.notify("auth_user_1", "chat_started", {
            "session_id": "new_web_session",
            "origin_channel": "web",
            "message_preview": "分析这个表格",
        })

        # 验证 Bot 端已同步到新 session
        assert session_store.get("telegram", "chat1", "user1") == "new_web_session"
        call_text = handler.adapter.send_text.call_args[0][1]
        assert "Web 端正在处理" in call_text
        assert "分析这个表格" in call_text

    @pytest.mark.asyncio
    async def test_chat_started_same_session_no_sync(self, handler, event_bridge, session_store):
        """Web 端使用相同 session 时，不需要同步。"""
        handler._bind_manager = MagicMock()
        handler._bind_manager.check_bind_status = MagicMock(return_value="auth_user_1")
        handler._auth_user_cache["telegram:user1"] = "auth_user_1"

        session_store.set("telegram", "chat1", "user1", "same_session")
        handler._ensure_bridge_subscription("chat1", "user1")

        await event_bridge.notify("auth_user_1", "chat_started", {
            "session_id": "same_session",
            "origin_channel": "web",
            "message_preview": "hello",
        })

        # session 不变
        assert session_store.get("telegram", "chat1", "user1") == "same_session"


# ── chat_completed 事件测试 ──


class TestChatCompletedEvent:
    @pytest.mark.asyncio
    async def test_chat_completed_with_error(self, handler, event_bridge):
        """Web 端操作出错时，Bot 端显示异常通知。"""
        handler._bind_manager = MagicMock()
        handler._bind_manager.check_bind_status = MagicMock(return_value="auth_user_1")
        handler._auth_user_cache["telegram:user1"] = "auth_user_1"

        handler._ensure_bridge_subscription("chat1", "user1")

        await event_bridge.notify("auth_user_1", "chat_completed", {
            "session_id": "s1",
            "origin_channel": "web",
            "reply_summary": "出错了",
            "has_error": True,
        })
        call_text = handler.adapter.send_text.call_args[0][1]
        assert "操作出现异常" in call_text

    @pytest.mark.asyncio
    async def test_chat_completed_syncs_session(self, handler, event_bridge, session_store):
        """chat_completed 也应自动同步 session_id。"""
        handler._bind_manager = MagicMock()
        handler._bind_manager.check_bind_status = MagicMock(return_value="auth_user_1")
        handler._auth_user_cache["telegram:user1"] = "auth_user_1"

        session_store.set("telegram", "chat1", "user1", "old_session")
        handler._ensure_bridge_subscription("chat1", "user1")

        await event_bridge.notify("auth_user_1", "chat_completed", {
            "session_id": "new_session_from_web",
            "origin_channel": "web",
            "reply_summary": "已完成",
        })
        assert session_store.get("telegram", "chat1", "user1") == "new_session_from_web"

    @pytest.mark.asyncio
    async def test_chat_completed_truncates_long_reply(self, handler, event_bridge):
        """过长的 reply_summary 应被截断。"""
        handler._bind_manager = MagicMock()
        handler._bind_manager.check_bind_status = MagicMock(return_value="auth_user_1")
        handler._auth_user_cache["telegram:user1"] = "auth_user_1"

        handler._ensure_bridge_subscription("chat1", "user1")

        long_reply = "A" * 500
        await event_bridge.notify("auth_user_1", "chat_completed", {
            "session_id": "s1",
            "origin_channel": "web",
            "reply_summary": long_reply,
        })
        call_text = handler.adapter.send_text.call_args[0][1]
        # 200 char truncation + "…"
        assert "…" in call_text
        assert len(call_text) < 500


# ── Approval/Question 跨渠道推送测试 ──


class TestApprovalQuestionBridge:
    @pytest.mark.asyncio
    async def test_approval_from_web_pushed_to_bot(self, handler, event_bridge):
        """Web 端审批事件应推送到 Bot 并创建 PendingInteraction。"""
        handler._bind_manager = MagicMock()
        handler._bind_manager.check_bind_status = MagicMock(return_value="auth_user_1")
        handler._auth_user_cache["telegram:user1"] = "auth_user_1"

        handler._ensure_bridge_subscription("chat1", "user1")

        await event_bridge.notify("auth_user_1", "approval", {
            "approval_id": "apr_123",
            "approval_tool_name": "write_cells",
            "risk_level": "yellow",
            "args_summary": {"sheet": "Sheet1"},
            "session_id": "s1",
            "origin_channel": "web",
        })
        handler.adapter.send_approval_card.assert_awaited_once()
        pk = handler._pending_key("chat1", "user1")
        assert pk in handler._pending
        assert handler._pending[pk].type == "approval"
        assert handler._pending[pk].id == "apr_123"

    @pytest.mark.asyncio
    async def test_question_from_web_pushed_to_bot(self, handler, event_bridge):
        """Web 端问答事件应推送到 Bot 并创建 PendingInteraction。"""
        handler._bind_manager = MagicMock()
        handler._bind_manager.check_bind_status = MagicMock(return_value="auth_user_1")
        handler._auth_user_cache["telegram:user1"] = "auth_user_1"

        handler._ensure_bridge_subscription("chat1", "user1")

        await event_bridge.notify("auth_user_1", "question", {
            "id": "q_456",
            "header": "确认",
            "text": "要继续吗？",
            "options": [{"label": "是"}, {"label": "否"}],
            "session_id": "s1",
            "origin_channel": "web",
        })
        handler.adapter.send_question_card.assert_awaited_once()
        pk = handler._pending_key("chat1", "user1")
        assert pk in handler._pending
        assert handler._pending[pk].type == "question"

    @pytest.mark.asyncio
    async def test_approval_from_same_channel_skipped(self, handler, event_bridge):
        """同渠道的审批事件应被跳过。"""
        handler._bind_manager = MagicMock()
        handler._bind_manager.check_bind_status = MagicMock(return_value="auth_user_1")
        handler._auth_user_cache["telegram:user1"] = "auth_user_1"

        handler._ensure_bridge_subscription("chat1", "user1")

        await event_bridge.notify("auth_user_1", "approval", {
            "approval_id": "apr_789",
            "origin_channel": "telegram",  # 与 adapter.name 相同
            "session_id": "s1",
        })
        handler.adapter.send_approval_card.assert_not_awaited()


# ── SessionStore 会话共享测试 ──


class TestSessionStoreSharing:
    def test_bot_and_web_share_session_via_user_id(self, session_store):
        """同一 auth_user_id 下 bot 和 web 使用相同 session_id 应能互通。"""
        session_store.set("telegram", "chat1", "user1", "shared_session_123")
        session_store.set_auth_user_id("telegram", "chat1", "user1", "auth_u1")

        sid = session_store.get("telegram", "chat1", "user1")
        assert sid == "shared_session_123"
        assert session_store.get_auth_user_id("telegram", "chat1", "user1") == "auth_u1"

    def test_backfill_updates_all_entries(self, session_store):
        """绑定后 backfill 应更新该用户所有条目。"""
        session_store.set("telegram", "chat_a", "user1", "s1")
        session_store.set("telegram", "chat_b", "user1", "s2")
        count = session_store.backfill_auth_user_id("telegram", "user1", "auth_u1")
        assert count == 2
        assert session_store.get_auth_user_id("telegram", "chat_a", "user1") == "auth_u1"
        assert session_store.get_auth_user_id("telegram", "chat_b", "user1") == "auth_u1"


# ── 端到端会话互通场景 ──


class TestEndToEndInterop:
    @pytest.mark.asyncio
    async def test_web_chat_then_bot_receives_notification(self, handler, event_bridge, session_store):
        """模拟 Web 端完成一次 chat 后 Bot 端收到通知并同步会话的完整流程。"""
        handler._bind_manager = MagicMock()
        handler._bind_manager.check_bind_status = MagicMock(return_value="auth_user_1")
        handler._auth_user_cache["telegram:user1"] = "auth_user_1"

        # Step 1: Bot 端注册 bridge 订阅
        handler._ensure_bridge_subscription("chat1", "user1")

        # Step 2: Web 端 chat_started
        await event_bridge.notify("auth_user_1", "chat_started", {
            "session_id": "web_session_001",
            "origin_channel": "web",
            "message_preview": "请帮我整理表格",
        })
        assert session_store.get("telegram", "chat1", "user1") == "web_session_001"
        assert handler.adapter.send_text.call_count == 1

        # Step 3: Web 端 chat_completed
        handler.adapter.send_text.reset_mock()
        await event_bridge.notify("auth_user_1", "chat_completed", {
            "session_id": "web_session_001",
            "origin_channel": "web",
            "reply_summary": "表格已整理完毕，共修改 15 行。",
            "tool_count": 2,
        })
        assert handler.adapter.send_text.call_count == 1
        text = handler.adapter.send_text.call_args[0][1]
        assert "Web 端已完成操作" in text
        assert "表格已整理完毕" in text

    @pytest.mark.asyncio
    async def test_bot_chat_then_web_receives_no_echo(self, handler, event_bridge):
        """Bot 端发起 chat 时，Bot 端不应收到自身的 bridge 通知。"""
        handler._bind_manager = MagicMock()
        handler._bind_manager.check_bind_status = MagicMock(return_value="auth_user_1")
        handler._auth_user_cache["telegram:user1"] = "auth_user_1"

        handler._ensure_bridge_subscription("chat1", "user1")

        # Bot 端发起的事件（origin=telegram）
        await event_bridge.notify("auth_user_1", "chat_started", {
            "session_id": "bot_session_001",
            "origin_channel": "telegram",
        })
        handler.adapter.send_text.assert_not_awaited()

        await event_bridge.notify("auth_user_1", "chat_completed", {
            "session_id": "bot_session_001",
            "origin_channel": "telegram",
            "reply_summary": "done",
        })
        handler.adapter.send_text.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unbound_user_no_bridge_subscription(self, handler, event_bridge):
        """未绑定用户不应注册 bridge 订阅。"""
        handler._bind_manager = MagicMock()
        handler._bind_manager.check_bind_status = MagicMock(return_value=None)

        handler._ensure_bridge_subscription("chat1", "user1")
        assert event_bridge.subscription_count == 0

    @pytest.mark.asyncio
    async def test_multiple_channels_receive_notification(self, event_bridge):
        """同一用户绑定多个渠道时，所有渠道都应收到通知。"""
        tg_cb = AsyncMock()
        qq_cb = AsyncMock()
        event_bridge.subscribe("auth_u1", "telegram", "chat_tg", tg_cb)
        event_bridge.subscribe("auth_u1", "qq", "chat_qq", qq_cb)

        # Web 端发送事件
        delivered = await event_bridge.notify("auth_u1", "chat_completed", {
            "session_id": "s1",
            "origin_channel": "web",
            "reply_summary": "done",
        })
        assert delivered == 2
        tg_cb.assert_awaited_once()
        qq_cb.assert_awaited_once()
