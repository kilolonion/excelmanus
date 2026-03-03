"""统一渠道框架单元测试。"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from excelmanus.channels.base import (
    ChannelAdapter,
    ChannelMessage,
    ChannelUser,
    FileAttachment,
)
from excelmanus.channels.api_client import ChatResult, ExcelManusAPIClient
from excelmanus.channels.message_handler import MessageHandler
from excelmanus.channels.registry import ChannelRegistry
from excelmanus.channels.session_store import SessionStore


# ── base.py 测试 ──


class TestChannelMessage:
    def test_defaults(self):
        user = ChannelUser(user_id="123")
        msg = ChannelMessage(channel="test", user=user, chat_id="456")
        assert msg.text == ""
        assert msg.files == []
        assert msg.callback_data is None
        assert msg.is_command is False
        assert msg.command == ""
        assert msg.command_args == []

    def test_with_files(self):
        user = ChannelUser(user_id="1", username="alice", display_name="Alice")
        att = FileAttachment(filename="test.xlsx", data=b"\x00\x01", mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        msg = ChannelMessage(
            channel="telegram",
            user=user,
            chat_id="100",
            text="分析这个",
            files=[att],
        )
        assert len(msg.files) == 1
        assert msg.files[0].filename == "test.xlsx"
        assert msg.user.display_name == "Alice"


class TestSplitMessage:
    def test_short_message(self):
        parts = ChannelAdapter.split_message("hello", max_len=100)
        assert parts == ["hello"]

    def test_long_message_at_newline(self):
        text = "line1\n" * 50  # 300 chars
        parts = ChannelAdapter.split_message(text, max_len=100)
        assert len(parts) > 1
        for part in parts:
            assert len(part) <= 100

    def test_long_message_no_newline(self):
        text = "a" * 200
        parts = ChannelAdapter.split_message(text, max_len=100)
        assert len(parts) == 2
        assert parts[0] == "a" * 100
        assert parts[1] == "a" * 100


# ── registry.py 测试 ──


class TestChannelRegistry:
    def test_register_and_create(self):
        registry = ChannelRegistry()

        class DummyAdapter(ChannelAdapter):
            name = "dummy"
            async def start(self): pass
            async def stop(self): pass
            async def send_text(self, chat_id, text): pass
            async def send_markdown(self, chat_id, text): pass
            async def send_file(self, chat_id, file_path, filename): pass
            async def send_approval_card(self, chat_id, approval_id, tool_name, risk_level, args_summary): pass
            async def send_question_card(self, chat_id, question_id, header, text, options): pass
            async def show_typing(self, chat_id): pass

        registry.register("dummy", DummyAdapter)
        assert "dummy" in registry
        assert registry.available == ["dummy"]

        adapter = registry.create("dummy")
        assert adapter.name == "dummy"

    def test_create_unknown(self):
        registry = ChannelRegistry()
        with pytest.raises(KeyError, match="未注册的渠道"):
            registry.create("nonexistent")


# ── session_store.py 测试 ──


class TestSessionStore:
    def test_set_get_remove(self, tmp_path):
        store = SessionStore(store_path=tmp_path / "sessions.json")
        assert store.get("tg", "100", "user1") is None

        store.set("tg", "100", "user1", "session-abc")
        assert store.get("tg", "100", "user1") == "session-abc"

        store.remove("tg", "100", "user1")
        assert store.get("tg", "100", "user1") is None

    def test_persistence(self, tmp_path):
        path = tmp_path / "sessions.json"
        store1 = SessionStore(store_path=path)
        store1.set("tg", "100", "u1", "s1")

        store2 = SessionStore(store_path=path)
        assert store2.get("tg", "100", "u1") == "s1"

    def test_ttl_expiry(self, tmp_path):
        store = SessionStore(store_path=tmp_path / "sessions.json", ttl_seconds=0.001)
        store.set("tg", "100", "u1", "s1")
        import time
        time.sleep(0.01)
        assert store.get("tg", "100", "u1") is None

    def test_cleanup_expired(self, tmp_path):
        store = SessionStore(store_path=tmp_path / "sessions.json", ttl_seconds=0.001)
        store.set("tg", "100", "u1", "s1")
        store.set("tg", "100", "u2", "s2")
        import time
        time.sleep(0.01)
        cleaned = store.cleanup_expired()
        assert cleaned == 2


# ── api_client.py 测试 ──


class TestChatResult:
    def test_defaults(self):
        r = ChatResult()
        assert r.reply == ""
        assert r.session_id == ""
        assert r.tool_calls == []
        assert r.approval is None
        assert r.question is None
        assert r.file_downloads == []
        assert r.error is None


# ── message_handler.py 测试 ──


class MockAdapter(ChannelAdapter):
    """用于测试的模拟适配器。"""

    name = "mock"

    def __init__(self):
        self.sent_texts: list[tuple[str, str]] = []
        self.sent_markdowns: list[tuple[str, str]] = []
        self.sent_files: list[tuple[str, str, str]] = []
        self.sent_approvals: list[dict] = []
        self.sent_questions: list[dict] = []
        self.typing_calls: list[str] = []

    async def start(self): pass
    async def stop(self): pass

    async def send_text(self, chat_id, text):
        self.sent_texts.append((chat_id, text))

    async def send_markdown(self, chat_id, text):
        self.sent_markdowns.append((chat_id, text))

    async def send_file(self, chat_id, file_path, filename):
        self.sent_files.append((chat_id, file_path, filename))

    async def send_approval_card(self, chat_id, approval_id, tool_name, risk_level, args_summary):
        self.sent_approvals.append({
            "chat_id": chat_id, "approval_id": approval_id,
            "tool_name": tool_name, "risk_level": risk_level,
        })

    async def send_question_card(self, chat_id, question_id, header, text, options):
        self.sent_questions.append({
            "chat_id": chat_id, "question_id": question_id,
            "header": header, "text": text, "options": options,
        })

    async def show_typing(self, chat_id):
        self.typing_calls.append(chat_id)


@pytest.fixture
def mock_handler(tmp_path):
    adapter = MockAdapter()
    api = AsyncMock(spec=ExcelManusAPIClient)
    store = SessionStore(store_path=tmp_path / "sessions.json")
    handler = MessageHandler(adapter=adapter, api_client=api, session_store=store)
    return handler, adapter, api, store


class TestMessageHandler:
    @pytest.mark.asyncio
    async def test_cmd_start(self, mock_handler):
        handler, adapter, api, store = mock_handler
        msg = ChannelMessage(
            channel="mock",
            user=ChannelUser(user_id="1"),
            chat_id="100",
            is_command=True,
            command="start",
        )
        await handler.handle_message(msg)
        assert len(adapter.sent_texts) == 1
        assert "ExcelManus Bot 已就绪" in adapter.sent_texts[0][1]

    @pytest.mark.asyncio
    async def test_cmd_help(self, mock_handler):
        handler, adapter, api, store = mock_handler
        msg = ChannelMessage(
            channel="mock",
            user=ChannelUser(user_id="1"),
            chat_id="100",
            is_command=True,
            command="help",
        )
        await handler.handle_message(msg)
        assert len(adapter.sent_texts) == 1
        assert "/new" in adapter.sent_texts[0][1]

    @pytest.mark.asyncio
    async def test_cmd_new(self, mock_handler):
        handler, adapter, api, store = mock_handler
        store.set("mock", "100", "1", "old-session")
        msg = ChannelMessage(
            channel="mock",
            user=ChannelUser(user_id="1"),
            chat_id="100",
            is_command=True,
            command="new",
        )
        await handler.handle_message(msg)
        assert store.get("mock", "100", "1") is None
        assert "新建对话" in adapter.sent_texts[0][1]

    @pytest.mark.asyncio
    async def test_text_message(self, mock_handler):
        handler, adapter, api, store = mock_handler
        api.stream_chat = AsyncMock(return_value=ChatResult(
            reply="你好！", session_id="s1",
        ))
        msg = ChannelMessage(
            channel="mock",
            user=ChannelUser(user_id="1"),
            chat_id="100",
            text="你好",
        )
        await handler.handle_message(msg)
        api.stream_chat.assert_called_once()
        call_args = api.stream_chat.call_args
        assert call_args[0] == ("你好", None)
        assert store.get("mock", "100", "1") == "s1"
        assert len(adapter.sent_markdowns) == 1
        assert adapter.sent_markdowns[0][1] == "你好！"

    @pytest.mark.asyncio
    async def test_user_blocked(self, mock_handler):
        handler, adapter, api, store = mock_handler
        handler.allowed_users = {"999"}
        msg = ChannelMessage(
            channel="mock",
            user=ChannelUser(user_id="1"),
            chat_id="100",
            text="hello",
        )
        await handler.handle_message(msg)
        assert "无权限" in adapter.sent_texts[0][1]
        api.stream_chat.assert_not_called()

    @pytest.mark.asyncio
    async def test_approval_in_result(self, mock_handler):
        handler, adapter, api, store = mock_handler
        api.stream_chat = AsyncMock(return_value=ChatResult(
            reply="需要审批",
            session_id="s1",
            approval={
                "approval_id": "a1",
                "approval_tool_name": "write_excel",
                "risk_level": "yellow",
                "args_summary": {"file": "test.xlsx"},
            },
        ))
        msg = ChannelMessage(
            channel="mock",
            user=ChannelUser(user_id="1"),
            chat_id="100",
            text="写入数据",
        )
        await handler.handle_message(msg)
        assert len(adapter.sent_approvals) == 1
        assert adapter.sent_approvals[0]["approval_id"] == "a1"

    @pytest.mark.asyncio
    async def test_question_in_result(self, mock_handler):
        handler, adapter, api, store = mock_handler
        api.stream_chat = AsyncMock(return_value=ChatResult(
            reply="",
            session_id="s1",
            question={
                "id": "q1",
                "header": "确认",
                "text": "要删除吗？",
                "options": [{"label": "是"}, {"label": "否"}],
            },
        ))
        msg = ChannelMessage(
            channel="mock",
            user=ChannelUser(user_id="1"),
            chat_id="100",
            text="删除文件",
        )
        await handler.handle_message(msg)
        assert len(adapter.sent_questions) == 1
        assert adapter.sent_questions[0]["question_id"] == "q1"

    @pytest.mark.asyncio
    async def test_approval_callback(self, mock_handler):
        handler, adapter, api, store = mock_handler
        store.set("mock", "100", "1", "s1")
        api.approve = AsyncMock(return_value={"status": "resolved"})

        msg = ChannelMessage(
            channel="mock",
            user=ChannelUser(user_id="1"),
            chat_id="100",
            callback_data="approve:a1",
        )
        await handler.handle_message(msg)
        api.approve.assert_called_once_with("s1", "a1", "approve")

    @pytest.mark.asyncio
    async def test_answer_callback(self, mock_handler):
        handler, adapter, api, store = mock_handler
        store.set("mock", "100", "1", "s1")
        api.answer_question = AsyncMock(return_value={"status": "answered"})

        msg = ChannelMessage(
            channel="mock",
            user=ChannelUser(user_id="1"),
            chat_id="100",
            callback_data="answer:q1:是",
        )
        await handler.handle_message(msg)
        api.answer_question.assert_called_once_with("s1", "q1", "是")

    @pytest.mark.asyncio
    async def test_unknown_command(self, mock_handler):
        handler, adapter, api, store = mock_handler
        msg = ChannelMessage(
            channel="mock",
            user=ChannelUser(user_id="1"),
            chat_id="100",
            is_command=True,
            command="unknown",
        )
        await handler.handle_message(msg)
        assert "未知命令" in adapter.sent_texts[0][1]

    @pytest.mark.asyncio
    async def test_cmd_abort_no_session(self, mock_handler):
        handler, adapter, api, store = mock_handler
        msg = ChannelMessage(
            channel="mock",
            user=ChannelUser(user_id="1"),
            chat_id="100",
            is_command=True,
            command="abort",
        )
        await handler.handle_message(msg)
        assert "没有活跃" in adapter.sent_texts[0][1]

    @pytest.mark.asyncio
    async def test_cmd_abort_with_session(self, mock_handler):
        handler, adapter, api, store = mock_handler
        store.set("mock", "100", "1", "s1")
        api.abort = AsyncMock(return_value={"status": "cancelled"})
        msg = ChannelMessage(
            channel="mock",
            user=ChannelUser(user_id="1"),
            chat_id="100",
            is_command=True,
            command="abort",
        )
        await handler.handle_message(msg)
        api.abort.assert_called_once_with("s1")
        assert "终止" in adapter.sent_texts[0][1]

    @pytest.mark.asyncio
    async def test_file_upload(self, mock_handler):
        handler, adapter, api, store = mock_handler
        api.upload_to_workspace = AsyncMock(return_value="/workspace/test.xlsx")
        api.stream_chat = AsyncMock(return_value=ChatResult(
            reply="已分析", session_id="s1",
        ))
        msg = ChannelMessage(
            channel="mock",
            user=ChannelUser(user_id="1"),
            chat_id="100",
            text="",
            files=[FileAttachment(filename="test.xlsx", data=b"\x00")],
        )
        await handler.handle_message(msg)
        api.upload_to_workspace.assert_called_once()
        api.stream_chat.assert_called_once()

    @pytest.mark.asyncio
    async def test_file_upload_unsupported(self, mock_handler):
        handler, adapter, api, store = mock_handler
        msg = ChannelMessage(
            channel="mock",
            user=ChannelUser(user_id="1"),
            chat_id="100",
            files=[FileAttachment(filename="test.txt", data=b"hello")],
        )
        await handler.handle_message(msg)
        assert "不支持" in adapter.sent_texts[0][1]
        api.stream_chat.assert_not_called()

    @pytest.mark.asyncio
    async def test_free_text_answer_routes_to_answer_question(self, mock_handler):
        """当 AI 发问后，用户直接回复文字应路由到 answer_question 而非 stream_chat。"""
        handler, adapter, api, store = mock_handler
        store.set("mock", "100", "1", "s1")
        api.answer_question = AsyncMock(return_value={"status": "answered"})

        # 模拟 AI 已发问，pending 中有待回答的问题
        from excelmanus.channels.message_handler import PendingInteraction
        handler._pending["100:1"] = PendingInteraction(
            interaction_type="question",
            interaction_id="q1",
            session_id="s1",
            chat_id="100",
        )

        msg = ChannelMessage(
            channel="mock",
            user=ChannelUser(user_id="1"),
            chat_id="100",
            text="我选择方案A",
        )
        await handler.handle_message(msg)

        # 应调用 answer_question 而非 stream_chat
        api.answer_question.assert_called_once_with("s1", "q1", "我选择方案A")
        api.stream_chat.assert_not_called()
        # pending 应被清除
        assert "100:1" not in handler._pending

    @pytest.mark.asyncio
    async def test_free_text_no_pending_goes_to_chat(self, mock_handler):
        """没有 pending 问题时，自由文本应正常走 stream_chat。"""
        handler, adapter, api, store = mock_handler
        api.stream_chat = AsyncMock(return_value=ChatResult(
            reply="普通回复", session_id="s1",
        ))

        msg = ChannelMessage(
            channel="mock",
            user=ChannelUser(user_id="1"),
            chat_id="100",
            text="普通问题",
        )
        await handler.handle_message(msg)

        api.stream_chat.assert_called_once()
        assert api.stream_chat.call_args[0] == ("普通问题", None)
        api.answer_question.assert_not_called()

    @pytest.mark.asyncio
    async def test_free_text_with_pending_approval_goes_to_chat(self, mock_handler):
        """pending 类型为 approval 时，自由文本不应拦截，仍走 stream_chat。"""
        handler, adapter, api, store = mock_handler
        api.stream_chat = AsyncMock(return_value=ChatResult(
            reply="ok", session_id="s1",
        ))

        from excelmanus.channels.message_handler import PendingInteraction
        handler._pending["100:1"] = PendingInteraction(
            interaction_type="approval",
            interaction_id="a1",
            session_id="s1",
            chat_id="100",
        )

        msg = ChannelMessage(
            channel="mock",
            user=ChannelUser(user_id="1"),
            chat_id="100",
            text="随便说点什么",
        )
        await handler.handle_message(msg)

        api.stream_chat.assert_called_once()
        api.answer_question.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_result(self, mock_handler):
        handler, adapter, api, store = mock_handler
        api.stream_chat = AsyncMock(return_value=ChatResult(session_id="s1"))
        msg = ChannelMessage(
            channel="mock",
            user=ChannelUser(user_id="1"),
            chat_id="100",
            text="hello",
        )
        await handler.handle_message(msg)
        assert any("无回复内容" in t[1] for t in adapter.sent_texts)

    @pytest.mark.asyncio
    async def test_group_chat_isolation(self, mock_handler):
        """同一用户在不同群聊中的 session 应互不干扰。"""
        handler, adapter, api, store = mock_handler
        api.stream_chat = AsyncMock(side_effect=[
            ChatResult(reply="群A回复", session_id="sA"),
            ChatResult(reply="群B回复", session_id="sB"),
        ])

        # 用户 1 在群 200 发消息
        msg_a = ChannelMessage(
            channel="mock", user=ChannelUser(user_id="1"),
            chat_id="200", text="hello A",
        )
        await handler.handle_message(msg_a)
        assert store.get("mock", "200", "1") == "sA"

        # 同一用户在群 300 发消息
        msg_b = ChannelMessage(
            channel="mock", user=ChannelUser(user_id="1"),
            chat_id="300", text="hello B",
        )
        await handler.handle_message(msg_b)
        assert store.get("mock", "300", "1") == "sB"

        # 群 200 的 session 不应被覆盖
        assert store.get("mock", "200", "1") == "sA"
        # 两者独立
        assert store.get("mock", "200", "1") != store.get("mock", "300", "1")
