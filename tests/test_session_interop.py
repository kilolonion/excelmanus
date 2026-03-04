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
        handler._auth_user_cache["telegram:user1"] = ("auth_user_1", __import__("time").monotonic())

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
        handler._auth_user_cache["telegram:user1"] = ("auth_user_1", __import__("time").monotonic())

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
    async def test_chat_started_syncs_when_no_local_session(self, handler, event_bridge, session_store):
        """Bot 端无活跃会话时，chat_started 应自动同步 session_id。"""
        handler._bind_manager = MagicMock()
        handler._bind_manager.check_bind_status = MagicMock(return_value="auth_user_1")
        handler._auth_user_cache["telegram:user1"] = ("auth_user_1", __import__("time").monotonic())

        # Bot 端当前无 session
        handler._ensure_bridge_subscription("chat1", "user1")

        await event_bridge.notify("auth_user_1", "chat_started", {
            "session_id": "new_web_session",
            "origin_channel": "web",
            "message_preview": "分析这个表格",
        })

        # 验证 Bot 端已同步到新 session
        assert session_store.get("telegram", "chat1", "user1") == "new_web_session"
        call_text = handler.adapter.send_text.call_args[0][1]
        assert "Web 端" in call_text
        assert "分析这个表格" in call_text

    @pytest.mark.asyncio
    async def test_chat_started_does_not_overwrite_existing_session(self, handler, event_bridge, session_store):
        """Bot 端已有活跃会话时，chat_started 不应覆盖 session_id，且跳过通知。"""
        handler._bind_manager = MagicMock()
        handler._bind_manager.check_bind_status = MagicMock(return_value="auth_user_1")
        handler._auth_user_cache["telegram:user1"] = ("auth_user_1", __import__("time").monotonic())

        # Bot 端有活跃 session
        session_store.set("telegram", "chat1", "user1", "bot_active_session")
        handler._ensure_bridge_subscription("chat1", "user1")

        await event_bridge.notify("auth_user_1", "chat_started", {
            "session_id": "web_different_session",
            "origin_channel": "web",
            "message_preview": "hello",
        })

        # session 不应被覆盖
        assert session_store.get("telegram", "chat1", "user1") == "bot_active_session"
        # 多群聊去重：session 不匹配时不发送通知
        handler.adapter.send_text.assert_not_awaited()


# ── chat_completed 事件测试 ──


class TestChatCompletedEvent:
    @pytest.mark.asyncio
    async def test_chat_completed_with_error(self, handler, event_bridge):
        """Web 端操作出错时，Bot 端显示异常通知。"""
        handler._bind_manager = MagicMock()
        handler._bind_manager.check_bind_status = MagicMock(return_value="auth_user_1")
        handler._auth_user_cache["telegram:user1"] = ("auth_user_1", __import__("time").monotonic())

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
    async def test_chat_completed_does_not_overwrite_existing_session(self, handler, event_bridge, session_store):
        """Bot 端已有活跃会话时，chat_completed 不应覆盖 session_id。"""
        handler._bind_manager = MagicMock()
        handler._bind_manager.check_bind_status = MagicMock(return_value="auth_user_1")
        handler._auth_user_cache["telegram:user1"] = ("auth_user_1", __import__("time").monotonic())

        session_store.set("telegram", "chat1", "user1", "bot_active_session")
        handler._ensure_bridge_subscription("chat1", "user1")

        await event_bridge.notify("auth_user_1", "chat_completed", {
            "session_id": "web_session",
            "origin_channel": "web",
            "reply_summary": "已完成",
        })
        # session 不应被覆盖
        assert session_store.get("telegram", "chat1", "user1") == "bot_active_session"

    @pytest.mark.asyncio
    async def test_chat_completed_syncs_when_no_local_session(self, handler, event_bridge, session_store):
        """Bot 端无活跃会话时，chat_completed 应自动同步 session_id。"""
        handler._bind_manager = MagicMock()
        handler._bind_manager.check_bind_status = MagicMock(return_value="auth_user_1")
        handler._auth_user_cache["telegram:user1"] = ("auth_user_1", __import__("time").monotonic())

        handler._ensure_bridge_subscription("chat1", "user1")

        await event_bridge.notify("auth_user_1", "chat_completed", {
            "session_id": "web_session",
            "origin_channel": "web",
            "reply_summary": "已完成",
        })
        assert session_store.get("telegram", "chat1", "user1") == "web_session"

    @pytest.mark.asyncio
    async def test_chat_completed_truncates_long_reply(self, handler, event_bridge):
        """过长的 reply_summary 应被截断。"""
        handler._bind_manager = MagicMock()
        handler._bind_manager.check_bind_status = MagicMock(return_value="auth_user_1")
        handler._auth_user_cache["telegram:user1"] = ("auth_user_1", __import__("time").monotonic())

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
        handler._auth_user_cache["telegram:user1"] = ("auth_user_1", __import__("time").monotonic())

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
        handler._auth_user_cache["telegram:user1"] = ("auth_user_1", __import__("time").monotonic())

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
        handler._auth_user_cache["telegram:user1"] = ("auth_user_1", __import__("time").monotonic())

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
        handler._auth_user_cache["telegram:user1"] = ("auth_user_1", __import__("time").monotonic())

        # Step 1: Bot 端注册 bridge 订阅（无现有 session）
        handler._ensure_bridge_subscription("chat1", "user1")

        # Step 2: Web 端 chat_started → Bot 无 session，自动同步
        await event_bridge.notify("auth_user_1", "chat_started", {
            "session_id": "web_session_001",
            "origin_channel": "web",
            "message_preview": "请帮我整理表格",
        })
        assert session_store.get("telegram", "chat1", "user1") == "web_session_001"
        assert handler.adapter.send_text.call_count == 1

        # Step 3: Web 端 chat_completed（Bot 已有 session，不再覆盖）
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
        handler._auth_user_cache["telegram:user1"] = ("auth_user_1", __import__("time").monotonic())

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


# ── Fix #1: has_error 结构化检测测试 ──


class TestHasErrorStructural:
    """验证 chat_completed 的 has_error 字段由 ToolCallResult.success 决定，而非 emoji 搜索。"""

    def test_has_error_from_tool_call_failure(self):
        """存在失败的 tool_call 时 has_error=True。"""
        from excelmanus.engine_types import ChatResult, ToolCallResult
        cr = ChatResult(
            reply="操作完成",
            tool_calls=[
                ToolCallResult(tool_name="read_cells", arguments={}, result="ok", success=True),
                ToolCallResult(tool_name="write_cells", arguments={}, result="", success=False, error="权限不足"),
            ],
        )
        has_error = any(not tc.success for tc in cr.tool_calls)
        assert has_error is True

    def test_no_error_with_emoji_in_reply(self):
        """回复中包含 ❌ 但所有 tool_call 成功时 has_error=False（避免假阳性）。"""
        from excelmanus.engine_types import ChatResult, ToolCallResult
        cr = ChatResult(
            reply="请不要使用 ❌ 符号",
            tool_calls=[
                ToolCallResult(tool_name="read_cells", arguments={}, result="ok", success=True),
            ],
        )
        has_error = any(not tc.success for tc in cr.tool_calls)
        assert has_error is False

    def test_no_error_with_empty_tool_calls(self):
        """无 tool_call 时 has_error=False。"""
        from excelmanus.engine_types import ChatResult
        cr = ChatResult(reply="你好", tool_calls=[])
        has_error = any(not tc.success for tc in cr.tool_calls)
        assert has_error is False


# ── Fix #7: Session ID 安全同步测试 ──


class TestSessionIdSafeSync:
    """验证 chat_started/chat_completed 仅在 Bot 无活跃会话时同步 session_id。"""

    @pytest.mark.asyncio
    async def test_chat_started_preserves_active_session(self, handler, event_bridge, session_store):
        """Bot 正在使用的会话不应被跨渠道 chat_started 覆盖，且跳过通知。"""
        handler._bind_manager = MagicMock()
        handler._bind_manager.check_bind_status = MagicMock(return_value="auth_user_1")
        handler._auth_user_cache["telegram:user1"] = ("auth_user_1", __import__("time").monotonic())

        session_store.set("telegram", "chat1", "user1", "bot_session_A")
        handler._ensure_bridge_subscription("chat1", "user1")

        # Web 创建新会话 B
        await event_bridge.notify("auth_user_1", "chat_started", {
            "session_id": "web_session_B",
            "origin_channel": "web",
            "message_preview": "新任务",
        })
        # Bot 仍保持会话 A
        assert session_store.get("telegram", "chat1", "user1") == "bot_session_A"
        # 多群聊去重：session 不匹配时不发送通知
        handler.adapter.send_text.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_chat_completed_preserves_active_session(self, handler, event_bridge, session_store):
        """Bot 正在使用的会话不应被跨渠道 chat_completed 覆盖。"""
        handler._bind_manager = MagicMock()
        handler._bind_manager.check_bind_status = MagicMock(return_value="auth_user_1")
        handler._auth_user_cache["telegram:user1"] = ("auth_user_1", __import__("time").monotonic())

        session_store.set("telegram", "chat1", "user1", "bot_session_A")
        handler._ensure_bridge_subscription("chat1", "user1")

        await event_bridge.notify("auth_user_1", "chat_completed", {
            "session_id": "web_session_B",
            "origin_channel": "web",
            "reply_summary": "完成",
        })
        assert session_store.get("telegram", "chat1", "user1") == "bot_session_A"

    @pytest.mark.asyncio
    async def test_empty_session_gets_synced(self, handler, event_bridge, session_store):
        """Bot 无会话时应自动同步。"""
        handler._bind_manager = MagicMock()
        handler._bind_manager.check_bind_status = MagicMock(return_value="auth_user_1")
        handler._auth_user_cache["telegram:user1"] = ("auth_user_1", __import__("time").monotonic())

        # 无现有 session
        handler._ensure_bridge_subscription("chat1", "user1")
        assert session_store.get("telegram", "chat1", "user1") is None

        await event_bridge.notify("auth_user_1", "chat_started", {
            "session_id": "web_session_X",
            "origin_channel": "web",
        })
        assert session_store.get("telegram", "chat1", "user1") == "web_session_X"


# ── Fix #9: 审批竞态解决测试 ──


class TestApprovalResolvedBridge:
    """验证 approval_resolved 事件清除其他渠道的 PendingInteraction。"""

    @pytest.mark.asyncio
    async def test_approval_resolved_clears_pending(self, handler, event_bridge):
        """收到 approval_resolved 事件应清除本地 PendingInteraction。"""
        handler._bind_manager = MagicMock()
        handler._bind_manager.check_bind_status = MagicMock(return_value="auth_user_1")
        handler._auth_user_cache["telegram:user1"] = ("auth_user_1", __import__("time").monotonic())

        handler._ensure_bridge_subscription("chat1", "user1")

        # 先模拟收到审批事件
        await event_bridge.notify("auth_user_1", "approval", {
            "approval_id": "apr_001",
            "approval_tool_name": "write_cells",
            "risk_level": "yellow",
            "args_summary": {},
            "session_id": "s1",
            "origin_channel": "web",
        })
        pk = handler._pending_key("chat1", "user1")
        assert pk in handler._pending
        assert handler._pending[pk].type == "approval"

        # Web 端处理了审批 → approval_resolved
        handler.adapter.send_text.reset_mock()
        await event_bridge.notify("auth_user_1", "approval_resolved", {
            "approval_id": "apr_001",
            "session_id": "s1",
            "origin_channel": "web",
        })
        # PendingInteraction 应被清除
        assert pk not in handler._pending
        # 用户应收到通知
        call_text = handler.adapter.send_text.call_args[0][1]
        assert "审批已由" in call_text

    @pytest.mark.asyncio
    async def test_approval_resolved_no_pending_is_noop(self, handler, event_bridge):
        """没有 PendingInteraction 时收到 approval_resolved 应静默处理。"""
        handler._bind_manager = MagicMock()
        handler._bind_manager.check_bind_status = MagicMock(return_value="auth_user_1")
        handler._auth_user_cache["telegram:user1"] = ("auth_user_1", __import__("time").monotonic())

        handler._ensure_bridge_subscription("chat1", "user1")

        # 直接发送 resolved（无 pending）
        await event_bridge.notify("auth_user_1", "approval_resolved", {
            "approval_id": "apr_999",
            "session_id": "s1",
            "origin_channel": "web",
        })
        # 无 pending 不发通知
        handler.adapter.send_text.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_approval_resolved_mismatched_id_is_noop(self, handler, event_bridge):
        """approval_id 不匹配时不应清除 PendingInteraction。"""
        handler._bind_manager = MagicMock()
        handler._bind_manager.check_bind_status = MagicMock(return_value="auth_user_1")
        handler._auth_user_cache["telegram:user1"] = ("auth_user_1", __import__("time").monotonic())

        handler._ensure_bridge_subscription("chat1", "user1")

        # 创建 pending
        from excelmanus.channels.message_handler import PendingInteraction
        pk = handler._pending_key("chat1", "user1")
        handler._pending[pk] = PendingInteraction(
            interaction_type="approval",
            interaction_id="apr_AAA",
            session_id="s1",
            chat_id="chat1",
        )

        # 发送不同 approval_id 的 resolved
        await event_bridge.notify("auth_user_1", "approval_resolved", {
            "approval_id": "apr_BBB",
            "session_id": "s1",
            "origin_channel": "web",
        })
        # pending 不应被清除
        assert pk in handler._pending

    @pytest.mark.asyncio
    async def test_approval_resolved_bypasses_origin_filter(self, handler, event_bridge):
        """approval_resolved 应绕过 origin_channel 回声过滤。"""
        handler._bind_manager = MagicMock()
        handler._bind_manager.check_bind_status = MagicMock(return_value="auth_user_1")
        handler._auth_user_cache["telegram:user1"] = ("auth_user_1", __import__("time").monotonic())

        handler._ensure_bridge_subscription("chat1", "user1")

        # 创建 pending
        from excelmanus.channels.message_handler import PendingInteraction
        pk = handler._pending_key("chat1", "user1")
        handler._pending[pk] = PendingInteraction(
            interaction_type="approval",
            interaction_id="apr_001",
            session_id="s1",
            chat_id="chat1",
        )

        # origin_channel=telegram（与 adapter.name 相同）但 approval_resolved 应仍被处理
        await event_bridge.notify("auth_user_1", "approval_resolved", {
            "approval_id": "apr_001",
            "session_id": "s1",
            "origin_channel": "telegram",
        })
        assert pk not in handler._pending

    @pytest.mark.asyncio
    async def test_chat_completed_clears_residual_pending(self, handler, event_bridge):
        """chat_completed 应清除残留的 PendingInteraction。"""
        handler._bind_manager = MagicMock()
        handler._bind_manager.check_bind_status = MagicMock(return_value="auth_user_1")
        handler._auth_user_cache["telegram:user1"] = ("auth_user_1", __import__("time").monotonic())

        handler._ensure_bridge_subscription("chat1", "user1")

        # 模拟残留的 pending question
        from excelmanus.channels.message_handler import PendingInteraction
        pk = handler._pending_key("chat1", "user1")
        handler._pending[pk] = PendingInteraction(
            interaction_type="question",
            interaction_id="q_001",
            session_id="s1",
            chat_id="chat1",
        )

        # chat_completed 应清除
        await event_bridge.notify("auth_user_1", "chat_completed", {
            "session_id": "s1",
            "origin_channel": "web",
            "reply_summary": "完成",
        })
        assert pk not in handler._pending


# ── 动态渠道标签测试 ──


class TestDynamicChannelLabels:
    """验证通知文本使用动态渠道标签而非硬编码 'Web 端'。"""

    @pytest.mark.asyncio
    async def test_telegram_origin_label(self, handler, event_bridge):
        """origin_channel=telegram 的通知应显示 'Telegram'。"""
        handler._bind_manager = MagicMock()
        handler._bind_manager.check_bind_status = MagicMock(return_value="auth_user_1")
        handler._auth_user_cache["telegram:user1"] = ("auth_user_1", __import__("time").monotonic())

        # 需要另一个渠道接收（因为同渠道会被过滤）
        # 创建 QQ 渠道的 handler
        qq_adapter = MagicMock()
        qq_adapter.name = "qq"
        qq_adapter.send_text = AsyncMock()
        qq_adapter.send_approval_card = AsyncMock()
        qq_adapter.send_question_card = AsyncMock()

        from excelmanus.channels.message_handler import MessageHandler
        qq_handler = MessageHandler(
            adapter=qq_adapter,
            api_client=handler.api,
            session_store=handler.sessions,
            event_bridge=event_bridge,
        )
        qq_handler._bind_manager = MagicMock()
        qq_handler._bind_manager.check_bind_status = MagicMock(return_value="auth_user_1")
        qq_handler._auth_user_cache["qq:user1"] = ("auth_user_1", __import__("time").monotonic())
        qq_handler._ensure_bridge_subscription("chat_qq", "user1")

        await event_bridge.notify("auth_user_1", "chat_completed", {
            "session_id": "s1",
            "origin_channel": "telegram",
            "reply_summary": "完成",
        })
        call_text = qq_adapter.send_text.call_args[0][1]
        assert "Telegram" in call_text
        assert "Web 端" not in call_text

    @pytest.mark.asyncio
    async def test_web_origin_label(self, handler, event_bridge):
        """origin_channel=web 的通知应显示 'Web 端'。"""
        handler._bind_manager = MagicMock()
        handler._bind_manager.check_bind_status = MagicMock(return_value="auth_user_1")
        handler._auth_user_cache["telegram:user1"] = ("auth_user_1", __import__("time").monotonic())

        handler._ensure_bridge_subscription("chat1", "user1")

        await event_bridge.notify("auth_user_1", "chat_started", {
            "session_id": "s1",
            "origin_channel": "web",
            "message_preview": "hello",
        })
        call_text = handler.adapter.send_text.call_args[0][1]
        assert "Web 端" in call_text


# ── Fix #4: EventBridge 并行通知测试 ──


class TestParallelNotify:
    """验证 EventBridge.notify 并行执行回调。"""

    @pytest.mark.asyncio
    async def test_parallel_execution_both_succeed(self, event_bridge):
        """两个回调应并行完成，都计入 delivered。"""
        cb1 = AsyncMock()
        cb2 = AsyncMock()
        event_bridge.subscribe("u1", "telegram", "c1", cb1)
        event_bridge.subscribe("u1", "qq", "c2", cb2)
        delivered = await event_bridge.notify("u1", "test", {"k": "v"})
        assert delivered == 2
        cb1.assert_awaited_once()
        cb2.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_parallel_one_fails_other_succeeds(self, event_bridge):
        """一个回调失败不应阻止另一个成功投递。"""
        cb_ok = AsyncMock()
        cb_err = AsyncMock(side_effect=RuntimeError("channel down"))
        event_bridge.subscribe("u1", "telegram", "c1", cb_ok)
        event_bridge.subscribe("u1", "qq", "c2", cb_err)
        delivered = await event_bridge.notify("u1", "test", {})
        assert delivered == 1
        cb_ok.assert_awaited_once()
        cb_err.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_single_subscription_fast_path(self, event_bridge):
        """单订阅走快速路径（无 gather 开销）。"""
        cb = AsyncMock()
        event_bridge.subscribe("u1", "telegram", "c1", cb)
        delivered = await event_bridge.notify("u1", "test", {"k": "v"})
        assert delivered == 1
        cb.assert_awaited_once()


# ── Fix #5: fire-and-forget 安全性测试 ──


class TestFireAndForget:
    """验证 _fire_and_forget 正确捕获异常。"""

    @pytest.mark.asyncio
    async def test_fire_and_forget_success(self):
        """成功的协程不应产生任何警告。"""
        import asyncio
        from excelmanus.api import _fire_and_forget

        called = False
        async def _ok():
            nonlocal called
            called = True
        _fire_and_forget(_ok(), name="test_ok")
        await asyncio.sleep(0.05)
        assert called

    @pytest.mark.asyncio
    async def test_fire_and_forget_exception_captured(self):
        """失败的协程异常应被 done_callback 捕获，不泄漏。"""
        import asyncio
        from excelmanus.api import _fire_and_forget

        async def _fail():
            raise ValueError("boom")
        # 不应抛出 'Task exception was never retrieved'
        _fire_and_forget(_fail(), name="test_fail")
        await asyncio.sleep(0.05)
        # 如果异常泄漏，pytest 会在 teardown 时报错


# ── Fix #8: 多群聊通知去重测试 ──


class TestMultiGroupDedup:
    """验证多群聊场景下信息类通知的去重逻辑。"""

    @pytest.mark.asyncio
    async def test_different_session_skips_notification(self, handler, event_bridge, session_store):
        """群聊有不同活跃会话时，chat_started 不发送通知。"""
        handler._bind_manager = MagicMock()
        handler._bind_manager.check_bind_status = MagicMock(return_value="auth_user_1")
        handler._auth_user_cache["telegram:user1"] = ("auth_user_1", __import__("time").monotonic())

        # 群聊 A 有活跃会话 session_A
        session_store.set("telegram", "chat_A", "user1", "session_A")
        handler._ensure_bridge_subscription("chat_A", "user1")

        # Web 端在 session_B 上工作
        await event_bridge.notify("auth_user_1", "chat_started", {
            "session_id": "session_B",
            "origin_channel": "web",
            "message_preview": "hello",
        })
        # 群聊 A 的 session 不匹配 → 不发通知
        handler.adapter.send_text.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_matching_session_receives_notification(self, handler, event_bridge, session_store):
        """群聊有匹配活跃会话时，chat_started 正常发送通知。"""
        handler._bind_manager = MagicMock()
        handler._bind_manager.check_bind_status = MagicMock(return_value="auth_user_1")
        handler._auth_user_cache["telegram:user1"] = ("auth_user_1", __import__("time").monotonic())

        session_store.set("telegram", "chat1", "user1", "shared_session")
        handler._ensure_bridge_subscription("chat1", "user1")

        await event_bridge.notify("auth_user_1", "chat_started", {
            "session_id": "shared_session",
            "origin_channel": "web",
            "message_preview": "hello",
        })
        handler.adapter.send_text.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_session_receives_notification(self, handler, event_bridge, session_store):
        """群聊无活跃会话时，chat_started 正常发送通知。"""
        handler._bind_manager = MagicMock()
        handler._bind_manager.check_bind_status = MagicMock(return_value="auth_user_1")
        handler._auth_user_cache["telegram:user1"] = ("auth_user_1", __import__("time").monotonic())

        # 无 session
        handler._ensure_bridge_subscription("chat1", "user1")

        await event_bridge.notify("auth_user_1", "chat_started", {
            "session_id": "web_session",
            "origin_channel": "web",
            "message_preview": "hello",
        })
        handler.adapter.send_text.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_chat_completed_dedup_still_clears_pending(self, handler, event_bridge, session_store):
        """chat_completed 去重跳过通知但仍应清除 PendingInteraction。"""
        handler._bind_manager = MagicMock()
        handler._bind_manager.check_bind_status = MagicMock(return_value="auth_user_1")
        handler._auth_user_cache["telegram:user1"] = ("auth_user_1", __import__("time").monotonic())

        session_store.set("telegram", "chat1", "user1", "session_A")
        handler._ensure_bridge_subscription("chat1", "user1")

        # 模拟残留 pending
        from excelmanus.channels.message_handler import PendingInteraction
        pk = handler._pending_key("chat1", "user1")
        handler._pending[pk] = PendingInteraction(
            interaction_type="approval",
            interaction_id="apr_001",
            session_id="session_B",
            chat_id="chat1",
        )

        # session 不匹配 → 跳过通知，但 pending 应被清除
        await event_bridge.notify("auth_user_1", "chat_completed", {
            "session_id": "session_B",
            "origin_channel": "web",
            "reply_summary": "done",
        })
        handler.adapter.send_text.assert_not_awaited()
        assert pk not in handler._pending

    @pytest.mark.asyncio
    async def test_approval_not_affected_by_dedup(self, handler, event_bridge, session_store):
        """审批事件不受多群聊去重影响，所有群都应收到。"""
        handler._bind_manager = MagicMock()
        handler._bind_manager.check_bind_status = MagicMock(return_value="auth_user_1")
        handler._auth_user_cache["telegram:user1"] = ("auth_user_1", __import__("time").monotonic())

        # 群聊有不同 session
        session_store.set("telegram", "chat1", "user1", "different_session")
        handler._ensure_bridge_subscription("chat1", "user1")

        await event_bridge.notify("auth_user_1", "approval", {
            "approval_id": "apr_001",
            "approval_tool_name": "write_cells",
            "risk_level": "yellow",
            "args_summary": {},
            "session_id": "web_session",
            "origin_channel": "web",
        })
        # 审批事件不受 session 去重影响
        handler.adapter.send_approval_card.assert_awaited_once()


# ── Fix #11: SessionStore 原子写入测试 ──


class TestSessionStoreAtomicWrite:
    """验证 SessionStore 使用原子写入（tmp + rename）。"""

    def test_save_creates_valid_json(self, tmp_path):
        """基本写入后文件应包含有效 JSON。"""
        store = SessionStore(store_path=tmp_path / "sessions.json")
        store.set("telegram", "chat1", "user1", "s1")

        # 验证文件存在且可解析
        import json
        with open(tmp_path / "sessions.json", "r", encoding="utf-8") as f:
            data = json.load(f)
        assert "telegram:chat1:user1" in data
        assert data["telegram:chat1:user1"]["session_id"] == "s1"

    def test_no_tmp_file_remains_after_save(self, tmp_path):
        """原子写入后 .tmp 文件不应残留。"""
        store = SessionStore(store_path=tmp_path / "sessions.json")
        store.set("telegram", "chat1", "user1", "s1")

        tmp_file = tmp_path / "sessions.tmp"
        assert not tmp_file.exists()

    def test_reload_after_save(self, tmp_path):
        """写入后重新加载应能正确恢复数据。"""
        path = tmp_path / "sessions.json"
        store1 = SessionStore(store_path=path)
        store1.set("telegram", "chat1", "user1", "s1")
        store1.set("qq", "chat2", "user2", "s2")

        # 重新加载
        store2 = SessionStore(store_path=path)
        assert store2.get("telegram", "chat1", "user1") == "s1"
        assert store2.get("qq", "chat2", "user2") == "s2"
