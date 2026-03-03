"""统一渠道框架单元测试。"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from excelmanus.channels.base import (
    ChannelAdapter,
    ChannelMessage,
    ChannelUser,
    FileAttachment,
    ImageAttachment,
)
from excelmanus.channels.api_client import ChatResult, ExcelManusAPIClient
from excelmanus.channels.message_handler import MessageHandler
from excelmanus.channels.registry import ChannelRegistry
from excelmanus.channels.session_store import SessionStore


def _sse_events_from_result(result: ChatResult):
    """Convert a ChatResult into SSE (event_type, data) tuples."""
    events = []
    if result.session_id:
        events.append(("session_init", {"session_id": result.session_id}))
    if result.reply:
        events.append(("text_delta", {"content": result.reply}))
    if result.approval:
        events.append(("pending_approval", result.approval))
    if result.question:
        events.append(("user_question", result.question))
    for dl in result.file_downloads:
        events.append(("file_download", dl))
    if result.staging_event:
        events.append(("staging_updated", result.staging_event))
    if result.error:
        events.append(("error", {"error": result.error}))
    return events


def _make_stream_mock(*chat_results):
    """Create a trackable async generator mock for api.stream_chat_events.

    Usage::

        mock_fn = _make_stream_mock(ChatResult(reply="hi", session_id="s1"))
        api.stream_chat_events = mock_fn
        ...
        assert len(mock_fn.calls) == 1
        assert mock_fn.calls[0]["message"] == "hello"
    """
    results = list(chat_results)
    idx = [0]

    async def mock_fn(message, session_id=None, chat_mode="write", images=None, **kwargs):
        mock_fn.calls.append({
            "message": message,
            "session_id": session_id,
            "chat_mode": chat_mode,
            "images": images,
            **kwargs,
        })
        r = results[min(idx[0], len(results) - 1)]
        idx[0] += 1
        if isinstance(r, Exception):
            raise r
        for evt in _sse_events_from_result(r):
            yield evt

    mock_fn.calls = []
    return mock_fn


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
            async def send_file(self, chat_id, data, filename): pass
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
        self.sent_files: list[tuple[str, bytes, str]] = []
        self.sent_approvals: list[dict] = []
        self.sent_questions: list[dict] = []
        self.typing_calls: list[str] = []

    async def start(self): pass
    async def stop(self): pass

    async def send_text(self, chat_id, text):
        self.sent_texts.append((chat_id, text))

    async def send_markdown(self, chat_id, text):
        self.sent_markdowns.append((chat_id, text))

    async def send_file(self, chat_id, data, filename):
        self.sent_files.append((chat_id, data, filename))

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
        mock_fn = _make_stream_mock(ChatResult(reply="你好！", session_id="s1"))
        api.stream_chat_events = mock_fn
        msg = ChannelMessage(
            channel="mock",
            user=ChannelUser(user_id="1"),
            chat_id="100",
            text="你好",
        )
        await handler.handle_message(msg)
        assert len(mock_fn.calls) == 1
        assert mock_fn.calls[0]["message"] == "你好"
        assert mock_fn.calls[0]["session_id"] is None
        assert store.get("mock", "100", "1") == "s1"
        assert len(adapter.sent_markdowns) == 1
        assert adapter.sent_markdowns[0][1] == "你好！"

    @pytest.mark.asyncio
    async def test_text_message_passes_channel(self, mock_handler):
        """_stream_chat_chunked 应将 adapter.name 作为 channel 传给 stream_chat_events。"""
        handler, adapter, api, store = mock_handler
        mock_fn = _make_stream_mock(ChatResult(reply="ok", session_id="s1"))
        api.stream_chat_events = mock_fn
        msg = ChannelMessage(
            channel="mock",
            user=ChannelUser(user_id="1"),
            chat_id="100",
            text="测试渠道传递",
        )
        await handler.handle_message(msg)
        assert len(mock_fn.calls) == 1
        assert mock_fn.calls[0].get("channel") == "mock"

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
        api.stream_chat_events.assert_not_called()

    @pytest.mark.asyncio
    async def test_approval_in_result(self, mock_handler):
        handler, adapter, api, store = mock_handler
        api.stream_chat_events = _make_stream_mock(ChatResult(
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
        api.stream_chat_events = _make_stream_mock(ChatResult(
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
        api.approve.assert_called_once_with("s1", "a1", "approve", on_behalf_of="channel_anon:mock:1")

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
        api.answer_question.assert_called_once_with("s1", "q1", "是", on_behalf_of="channel_anon:mock:1")

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
        api.abort.assert_called_once_with("s1", on_behalf_of="channel_anon:mock:1")
        assert "终止" in adapter.sent_texts[0][1]

    @pytest.mark.asyncio
    async def test_file_upload_buffers_without_text(self, mock_handler):
        """文件上传无文本 → 上传到工作区 + 缓冲 + 询问用户意图，不触发 AI。"""
        handler, adapter, api, store = mock_handler
        api.upload_to_workspace = AsyncMock(return_value="/workspace/test.xlsx")
        msg = ChannelMessage(
            channel="mock",
            user=ChannelUser(user_id="1"),
            chat_id="100",
            text="",
            files=[FileAttachment(filename="test.xlsx", data=b"\x00")],
        )
        await handler.handle_message(msg)
        api.upload_to_workspace.assert_called_once()
        # 不应立即调用 stream_chat
        api.stream_chat_events.assert_not_called()
        # 应询问用户
        assert any("已收到文件" in t[1] for t in adapter.sent_texts)
        # 文件应被缓冲
        assert "100:1" in handler._pending_files

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
        api.stream_chat_events.assert_not_called()

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
        api.answer_question.assert_called_once_with("s1", "q1", "我选择方案A", on_behalf_of="channel_anon:mock:1")
        api.stream_chat_events.assert_not_called()
        # pending 应被清除
        assert "100:1" not in handler._pending

    @pytest.mark.asyncio
    async def test_free_text_no_pending_goes_to_chat(self, mock_handler):
        """没有 pending 问题时，自由文本应正常走 stream_chat。"""
        handler, adapter, api, store = mock_handler
        mock_fn = _make_stream_mock(ChatResult(reply="普通回复", session_id="s1"))
        api.stream_chat_events = mock_fn

        msg = ChannelMessage(
            channel="mock",
            user=ChannelUser(user_id="1"),
            chat_id="100",
            text="普通问题",
        )
        await handler.handle_message(msg)

        assert len(mock_fn.calls) == 1
        assert mock_fn.calls[0]["message"] == "普通问题"
        assert mock_fn.calls[0]["session_id"] is None
        api.answer_question.assert_not_called()

    @pytest.mark.asyncio
    async def test_free_text_with_pending_approval_goes_to_chat(self, mock_handler):
        """pending 类型为 approval 时，自由文本不应拦截，仍走 stream_chat。"""
        handler, adapter, api, store = mock_handler
        mock_fn = _make_stream_mock(ChatResult(reply="ok", session_id="s1"))
        api.stream_chat_events = mock_fn

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

        assert len(mock_fn.calls) == 1
        api.answer_question.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_result(self, mock_handler):
        handler, adapter, api, store = mock_handler
        api.stream_chat_events = _make_stream_mock(ChatResult(session_id="s1"))
        msg = ChannelMessage(
            channel="mock",
            user=ChannelUser(user_id="1"),
            chat_id="100",
            text="hello",
        )
        await handler.handle_message(msg)
        assert any("未获得回复内容" in t[1] for t in adapter.sent_texts)

    @pytest.mark.asyncio
    async def test_group_chat_isolation(self, mock_handler):
        """同一用户在不同群聊中的 session 应互不干扰。"""
        handler, adapter, api, store = mock_handler
        api.stream_chat_events = _make_stream_mock(
            ChatResult(reply="群A回复", session_id="sA"),
            ChatResult(reply="群B回复", session_id="sB"),
        )

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


# ── 并发锁测试 ──


class TestConcurrencyLock:
    """验证 per-user 锁机制防止并发 stream_chat 竞争。"""

    @pytest.mark.asyncio
    async def test_concurrent_messages_serialized(self, mock_handler):
        """同一用户快速连发两条消息，stream_chat 应串行执行而非并发。"""
        handler, adapter, api, store = mock_handler
        call_order: list[str] = []

        async def slow_chat(message, session_id=None, chat_mode="write", images=None, **kwargs):
            call_order.append(f"start:{message}")
            await asyncio.sleep(0.1)
            call_order.append(f"end:{message}")
            yield ("session_init", {"session_id": "s1"})
            yield ("text_delta", {"content": f"re:{message}"})

        api.stream_chat_events = slow_chat

        msg_a = ChannelMessage(
            channel="mock", user=ChannelUser(user_id="1"),
            chat_id="100", text="msg_a",
        )
        msg_b = ChannelMessage(
            channel="mock", user=ChannelUser(user_id="1"),
            chat_id="100", text="msg_b",
        )

        # 并发发送
        await asyncio.gather(
            handler.handle_message(msg_a),
            handler.handle_message(msg_b),
        )

        # 验证串行：第一条的 end 必须在第二条的 start 之前
        assert call_order.index("end:msg_a") < call_order.index("start:msg_b") or \
               call_order.index("end:msg_b") < call_order.index("start:msg_a")

    @pytest.mark.asyncio
    async def test_concurrent_queue_notification(self, mock_handler):
        """当锁被占用时，后续消息应收到排队通知。"""
        handler, adapter, api, store = mock_handler
        gate = asyncio.Event()

        async def blocking_chat(message, session_id=None, chat_mode="write", images=None, **kwargs):
            await gate.wait()
            yield ("session_init", {"session_id": "s1"})
            yield ("text_delta", {"content": "done"})

        api.stream_chat_events = blocking_chat

        msg_a = ChannelMessage(
            channel="mock", user=ChannelUser(user_id="1"),
            chat_id="100", text="first",
        )
        msg_b = ChannelMessage(
            channel="mock", user=ChannelUser(user_id="1"),
            chat_id="100", text="second",
        )

        task_a = asyncio.create_task(handler.handle_message(msg_a))
        await asyncio.sleep(0.01)  # 让 task_a 获取锁

        task_b = asyncio.create_task(handler.handle_message(msg_b))
        await asyncio.sleep(0.01)  # 让 task_b 检测到锁被占用

        # task_b 应已发送排队通知
        queue_msgs = [t for t in adapter.sent_texts if "排队" in t[1]]
        assert len(queue_msgs) >= 1

        gate.set()
        await asyncio.gather(task_a, task_b)

    @pytest.mark.asyncio
    async def test_different_users_not_blocked(self, mock_handler):
        """不同用户的消息不应互相阻塞。"""
        handler, adapter, api, store = mock_handler
        concurrent_count: list[int] = [0]
        max_concurrent: list[int] = [0]

        async def tracking_chat(message, session_id=None, chat_mode="write", images=None, **kwargs):
            concurrent_count[0] += 1
            max_concurrent[0] = max(max_concurrent[0], concurrent_count[0])
            await asyncio.sleep(0.05)
            concurrent_count[0] -= 1
            yield ("session_init", {"session_id": "s1"})
            yield ("text_delta", {"content": "ok"})

        api.stream_chat_events = tracking_chat

        msg_u1 = ChannelMessage(
            channel="mock", user=ChannelUser(user_id="user1"),
            chat_id="100", text="hello",
        )
        msg_u2 = ChannelMessage(
            channel="mock", user=ChannelUser(user_id="user2"),
            chat_id="100", text="world",
        )

        await asyncio.gather(
            handler.handle_message(msg_u1),
            handler.handle_message(msg_u2),
        )

        # 两个不同用户应能并发执行
        assert max_concurrent[0] == 2

    @pytest.mark.asyncio
    async def test_abort_not_blocked_by_lock(self, mock_handler):
        """/abort 命令不受 per-user 锁限制，处理中仍可执行。"""
        handler, adapter, api, store = mock_handler
        store.set("mock", "100", "1", "s1")
        gate = asyncio.Event()
        abort_called = asyncio.Event()

        async def blocking_chat(message, session_id=None, chat_mode="write", images=None, **kwargs):
            await gate.wait()
            yield ("session_init", {"session_id": "s1"})
            yield ("text_delta", {"content": "done"})

        api.stream_chat_events = blocking_chat
        original_abort = AsyncMock(return_value={"status": "cancelled"})
        api.abort = original_abort

        msg_chat = ChannelMessage(
            channel="mock", user=ChannelUser(user_id="1"),
            chat_id="100", text="long task",
        )
        msg_abort = ChannelMessage(
            channel="mock", user=ChannelUser(user_id="1"),
            chat_id="100", is_command=True, command="abort",
        )

        task_chat = asyncio.create_task(handler.handle_message(msg_chat))
        await asyncio.sleep(0.01)  # 让 chat 获取锁

        # /abort 应立即执行，不被锁阻塞
        await asyncio.wait_for(handler.handle_message(msg_abort), timeout=1.0)
        api.abort.assert_called_once_with("s1", on_behalf_of="channel_anon:mock:1")

        gate.set()
        await task_chat

    @pytest.mark.asyncio
    async def test_user_lock_isolation_across_chats(self, mock_handler):
        """同一用户在不同群聊中的锁应互相独立。"""
        handler, adapter, api, store = mock_handler
        concurrent_count: list[int] = [0]
        max_concurrent: list[int] = [0]

        async def tracking_chat(message, session_id=None, chat_mode="write", images=None, **kwargs):
            concurrent_count[0] += 1
            max_concurrent[0] = max(max_concurrent[0], concurrent_count[0])
            await asyncio.sleep(0.05)
            concurrent_count[0] -= 1
            yield ("session_init", {"session_id": "s1"})
            yield ("text_delta", {"content": "ok"})

        api.stream_chat_events = tracking_chat

        msg_chat_a = ChannelMessage(
            channel="mock", user=ChannelUser(user_id="1"),
            chat_id="200", text="hello",
        )
        msg_chat_b = ChannelMessage(
            channel="mock", user=ChannelUser(user_id="1"),
            chat_id="300", text="world",
        )

        await asyncio.gather(
            handler.handle_message(msg_chat_a),
            handler.handle_message(msg_chat_b),
        )

        # 同一用户不同群聊应能并发（锁以 chat_id:user_id 为键）
        assert max_concurrent[0] == 2


class TestChatModeSwitch:
    """验证 /mode 命令和 chat_mode 传递到 stream_chat。"""

    @pytest.mark.asyncio
    async def test_mode_show_current(self, mock_handler):
        """/mode 无参数 → 显示当前模式（默认 write）。"""
        handler, adapter, api, store = mock_handler
        msg = ChannelMessage(
            channel="mock", user=ChannelUser(user_id="1"),
            chat_id="100", text="/mode",
            is_command=True, command="mode", command_args=[],
        )
        await handler.handle_message(msg)
        assert len(adapter.sent_texts) == 1
        text = adapter.sent_texts[0][1]
        assert "写入" in text
        assert "write" in text

    @pytest.mark.asyncio
    async def test_mode_switch_to_read(self, mock_handler):
        """/mode read → 切换成功。"""
        handler, adapter, api, store = mock_handler
        msg = ChannelMessage(
            channel="mock", user=ChannelUser(user_id="1"),
            chat_id="100", text="/mode read",
            is_command=True, command="mode", command_args=["read"],
        )
        await handler.handle_message(msg)
        assert "读取" in adapter.sent_texts[0][1]
        assert store.get_mode("mock", "100", "1") == "read"

    @pytest.mark.asyncio
    async def test_mode_switch_invalid(self, mock_handler):
        """/mode invalid → 报错。"""
        handler, adapter, api, store = mock_handler
        msg = ChannelMessage(
            channel="mock", user=ChannelUser(user_id="1"),
            chat_id="100", text="/mode invalid",
            is_command=True, command="mode", command_args=["invalid"],
        )
        await handler.handle_message(msg)
        assert "无效模式" in adapter.sent_texts[0][1]

    @pytest.mark.asyncio
    async def test_mode_switch_same(self, mock_handler):
        """/mode write 在默认模式下 → 提示已是当前模式。"""
        handler, adapter, api, store = mock_handler
        msg = ChannelMessage(
            channel="mock", user=ChannelUser(user_id="1"),
            chat_id="100", text="/mode write",
            is_command=True, command="mode", command_args=["write"],
        )
        await handler.handle_message(msg)
        assert "已是" in adapter.sent_texts[0][1]

    @pytest.mark.asyncio
    async def test_text_uses_current_mode(self, mock_handler):
        """切换到 read 后发文本 → stream_chat 收到 chat_mode='read'。"""
        handler, adapter, api, store = mock_handler
        store.set_mode("mock", "100", "1", "read")
        mock_fn = _make_stream_mock(ChatResult(reply="分析结果", session_id="s1"))
        api.stream_chat_events = mock_fn
        msg = ChannelMessage(
            channel="mock", user=ChannelUser(user_id="1"),
            chat_id="100", text="分析这个表格",
        )
        await handler.handle_message(msg)
        assert mock_fn.calls[0]["chat_mode"] == "read"

    @pytest.mark.asyncio
    async def test_new_resets_mode(self, mock_handler):
        """/new 后模式应重置为 write。"""
        handler, adapter, api, store = mock_handler
        store.set_mode("mock", "100", "1", "plan")
        msg = ChannelMessage(
            channel="mock", user=ChannelUser(user_id="1"),
            chat_id="100", text="/new",
            is_command=True, command="new", command_args=[],
        )
        await handler.handle_message(msg)
        assert store.get_mode("mock", "100", "1") == "write"

    # ── /sessions 命令测试 ──

    @pytest.mark.asyncio
    async def test_cmd_sessions_list(self, mock_handler):
        """/sessions 无参数 → 列出历史会话。"""
        handler, adapter, api, store = mock_handler
        api.list_sessions = AsyncMock(return_value=[
            {"session_id": "s1", "title": "会话一", "message_count": 5},
            {"session_id": "s2", "title": "会话二", "message_count": 3},
        ])
        msg = ChannelMessage(
            channel="mock", user=ChannelUser(user_id="1"),
            chat_id="100", text="/sessions",
            is_command=True, command="sessions", command_args=[],
        )
        await handler.handle_message(msg)
        text = adapter.sent_texts[0][1]
        assert "会话一" in text
        assert "会话二" in text
        assert "5条" in text

    @pytest.mark.asyncio
    async def test_cmd_sessions_switch(self, mock_handler):
        """/sessions 2 → 切换到第 2 个会话。"""
        handler, adapter, api, store = mock_handler
        api.list_sessions = AsyncMock(return_value=[
            {"session_id": "s1", "title": "会话一", "message_count": 5},
            {"session_id": "s2", "title": "会话二", "message_count": 3},
        ])
        msg = ChannelMessage(
            channel="mock", user=ChannelUser(user_id="1"),
            chat_id="100", text="/sessions 2",
            is_command=True, command="sessions", command_args=["2"],
        )
        await handler.handle_message(msg)
        assert store.get("mock", "100", "1") == "s2"
        assert "会话二" in adapter.sent_texts[0][1]

    @pytest.mark.asyncio
    async def test_cmd_sessions_out_of_range(self, mock_handler):
        """/sessions 99 → 编号超出范围。"""
        handler, adapter, api, store = mock_handler
        api.list_sessions = AsyncMock(return_value=[
            {"session_id": "s1", "title": "会话一", "message_count": 5},
        ])
        msg = ChannelMessage(
            channel="mock", user=ChannelUser(user_id="1"),
            chat_id="100", text="/sessions 99",
            is_command=True, command="sessions", command_args=["99"],
        )
        await handler.handle_message(msg)
        assert "超出范围" in adapter.sent_texts[0][1]

    @pytest.mark.asyncio
    async def test_cmd_sessions_empty(self, mock_handler):
        """/sessions 无会话 → 提示暂无。"""
        handler, adapter, api, store = mock_handler
        api.list_sessions = AsyncMock(return_value=[])
        msg = ChannelMessage(
            channel="mock", user=ChannelUser(user_id="1"),
            chat_id="100", text="/sessions",
            is_command=True, command="sessions", command_args=[],
        )
        await handler.handle_message(msg)
        assert "暂无" in adapter.sent_texts[0][1]

    # ── /history 命令测试 ──

    @pytest.mark.asyncio
    async def test_cmd_history(self, mock_handler):
        """/history → 列出轮次摘要。"""
        handler, adapter, api, store = mock_handler
        store.set("mock", "100", "1", "s1")
        api.list_turns = AsyncMock(return_value=[
            {"turn_index": 0, "user_message": "帮我分析数据", "tool_names": ["read_excel"]},
            {"turn_index": 1, "user_message": "修改第三列"},
        ])
        msg = ChannelMessage(
            channel="mock", user=ChannelUser(user_id="1"),
            chat_id="100", text="/history",
            is_command=True, command="history", command_args=[],
        )
        await handler.handle_message(msg)
        text = adapter.sent_texts[0][1]
        assert "帮我分析数据" in text
        assert "read_excel" in text
        assert "[0]" in text
        assert "[1]" in text

    @pytest.mark.asyncio
    async def test_cmd_history_no_session(self, mock_handler):
        """/history 无活跃会话 → 提示。"""
        handler, adapter, api, store = mock_handler
        msg = ChannelMessage(
            channel="mock", user=ChannelUser(user_id="1"),
            chat_id="100", text="/history",
            is_command=True, command="history", command_args=[],
        )
        await handler.handle_message(msg)
        assert "没有活跃" in adapter.sent_texts[0][1]

    # ── /rollback 命令测试 ──

    @pytest.mark.asyncio
    async def test_cmd_rollback_with_turn(self, mock_handler):
        """/rollback 1 → 回退到轮次 1。"""
        handler, adapter, api, store = mock_handler
        store.set("mock", "100", "1", "s1")
        api.rollback = AsyncMock(return_value={
            "status": "ok", "removed_messages": 3,
            "file_rollback_results": [{"path": "a.xlsx"}],
            "turn_index": 1,
        })
        msg = ChannelMessage(
            channel="mock", user=ChannelUser(user_id="1"),
            chat_id="100", text="/rollback 1",
            is_command=True, command="rollback", command_args=["1"],
        )
        await handler.handle_message(msg)
        api.rollback.assert_called_once_with("s1", 1, on_behalf_of="channel_anon:mock:1")
        text = adapter.sent_texts[0][1]
        assert "回退" in text
        assert "3" in text

    @pytest.mark.asyncio
    async def test_cmd_rollback_no_args_shows_history(self, mock_handler):
        """/rollback 无参数 → 展示轮次列表。"""
        handler, adapter, api, store = mock_handler
        store.set("mock", "100", "1", "s1")
        api.list_turns = AsyncMock(return_value=[
            {"turn_index": 0, "user_message": "hello"},
        ])
        msg = ChannelMessage(
            channel="mock", user=ChannelUser(user_id="1"),
            chat_id="100", text="/rollback",
            is_command=True, command="rollback", command_args=[],
        )
        await handler.handle_message(msg)
        text = adapter.sent_texts[0][1]
        assert "hello" in text

    @pytest.mark.asyncio
    async def test_cmd_rollback_no_session(self, mock_handler):
        """/rollback 无会话 → 提示。"""
        handler, adapter, api, store = mock_handler
        msg = ChannelMessage(
            channel="mock", user=ChannelUser(user_id="1"),
            chat_id="100", text="/rollback 0",
            is_command=True, command="rollback", command_args=["0"],
        )
        await handler.handle_message(msg)
        assert "没有活跃" in adapter.sent_texts[0][1]

    # ── /undo 命令测试 ──

    @pytest.mark.asyncio
    async def test_cmd_undo_success(self, mock_handler):
        """/undo → 撤销最近可撤销操作（后端返回最近在前）。"""
        handler, adapter, api, store = mock_handler
        store.set("mock", "100", "1", "s1")
        api.list_operations = AsyncMock(return_value=[
            {"approval_id": "a2", "tool_name": "create_file", "undoable": True},   # 最近
            {"approval_id": "a1", "tool_name": "write_excel", "undoable": False},   # 较早
        ])
        api.undo_operation = AsyncMock(return_value={
            "status": "ok", "message": "已回滚 create_file", "approval_id": "a2",
        })
        msg = ChannelMessage(
            channel="mock", user=ChannelUser(user_id="1"),
            chat_id="100", text="/undo",
            is_command=True, command="undo", command_args=[],
        )
        await handler.handle_message(msg)
        api.undo_operation.assert_called_once_with("s1", "a2", on_behalf_of="channel_anon:mock:1")
        text = adapter.sent_texts[0][1]
        assert "撤销" in text
        assert "create_file" in text

    @pytest.mark.asyncio
    async def test_cmd_undo_no_undoable(self, mock_handler):
        """/undo 无可撤销操作 → 提示。"""
        handler, adapter, api, store = mock_handler
        store.set("mock", "100", "1", "s1")
        api.list_operations = AsyncMock(return_value=[
            {"approval_id": "a1", "tool_name": "read_excel", "undoable": False},
        ])
        msg = ChannelMessage(
            channel="mock", user=ChannelUser(user_id="1"),
            chat_id="100", text="/undo",
            is_command=True, command="undo", command_args=[],
        )
        await handler.handle_message(msg)
        assert "没有可撤销" in adapter.sent_texts[0][1]

    @pytest.mark.asyncio
    async def test_cmd_undo_no_session(self, mock_handler):
        """/undo 无会话 → 提示。"""
        handler, adapter, api, store = mock_handler
        msg = ChannelMessage(
            channel="mock", user=ChannelUser(user_id="1"),
            chat_id="100", text="/undo",
            is_command=True, command="undo", command_args=[],
        )
        await handler.handle_message(msg)
        assert "没有活跃" in adapter.sent_texts[0][1]


# ── 图片消息测试 ──


class TestImageAttachment:
    def test_defaults(self):
        img = ImageAttachment(data=b"\x89PNG")
        assert img.media_type == "image/jpeg"
        assert img.detail == "auto"

    def test_custom_fields(self):
        img = ImageAttachment(data=b"\xff\xd8", media_type="image/png", detail="high")
        assert img.media_type == "image/png"
        assert img.detail == "high"


class TestChannelMessageImages:
    def test_defaults_empty_images(self):
        user = ChannelUser(user_id="1")
        msg = ChannelMessage(channel="test", user=user, chat_id="100")
        assert msg.images == []

    def test_with_images(self):
        user = ChannelUser(user_id="1")
        img = ImageAttachment(data=b"\xff\xd8", media_type="image/jpeg")
        msg = ChannelMessage(
            channel="telegram", user=user, chat_id="100",
            text="分析这张图", images=[img],
        )
        assert len(msg.images) == 1
        assert msg.images[0].media_type == "image/jpeg"
        assert msg.text == "分析这张图"


class TestImageMessageHandler:
    """验证图片消息通过 _handle_image_message 路由到 stream_chat(images=...)。"""

    @pytest.mark.asyncio
    async def test_image_message_calls_stream_chat_with_images(self, mock_handler):
        """发送图片消息 → stream_chat 应收到 base64 编码的 images 参数。"""
        handler, adapter, api, store = mock_handler
        mock_fn = _make_stream_mock(ChatResult(reply="这是一张图表", session_id="s1"))
        api.stream_chat_events = mock_fn

        img_data = b"\xff\xd8\xff\xe0fake-jpeg"
        msg = ChannelMessage(
            channel="mock", user=ChannelUser(user_id="1"),
            chat_id="100", text="这是什么图",
            images=[ImageAttachment(data=img_data, media_type="image/jpeg")],
        )
        await handler.handle_message(msg)

        assert len(mock_fn.calls) == 1
        call = mock_fn.calls[0]
        assert call["images"] is not None
        assert len(call["images"]) == 1
        assert call["images"][0]["media_type"] == "image/jpeg"
        assert call["images"][0]["detail"] == "auto"
        # data 应为 base64 编码
        import base64
        decoded = base64.b64decode(call["images"][0]["data"])
        assert decoded == img_data
        # caption 应作为 message 传递
        assert call["message"] == "这是什么图"

    @pytest.mark.asyncio
    async def test_image_no_caption_buffers(self, mock_handler):
        """无 caption 的图片 → 缓冲并询问用户意图。"""
        handler, adapter, api, store = mock_handler

        msg = ChannelMessage(
            channel="mock", user=ChannelUser(user_id="1"),
            chat_id="100", text="",
            images=[ImageAttachment(data=b"img")],
        )
        await handler.handle_message(msg)

        # 不应立即调用 AI
        api.stream_chat_events.assert_not_called()
        # 应询问用户
        assert any("已收到图片" in t[1] for t in adapter.sent_texts)

    @pytest.mark.asyncio
    async def test_image_then_text_saves_session(self, mock_handler):
        """图片 + 后续文本指令返回的 session_id 应被保存。"""
        handler, adapter, api, store = mock_handler
        api.stream_chat_events = _make_stream_mock(ChatResult(
            reply="ok", session_id="img-session",
        ))

        # Step 1: 图片（缓冲）
        msg1 = ChannelMessage(
            channel="mock", user=ChannelUser(user_id="1"),
            chat_id="100",
            images=[ImageAttachment(data=b"img")],
        )
        await handler.handle_message(msg1)

        # Step 2: 文本指令（触发处理）
        msg2 = ChannelMessage(
            channel="mock", user=ChannelUser(user_id="1"),
            chat_id="100", text="分析这张图",
        )
        await handler.handle_message(msg2)
        assert store.get("mock", "100", "1") == "img-session"

    @pytest.mark.asyncio
    async def test_pure_image_with_text_routes_to_image_handler(self, mock_handler):
        """纯图片消息（无文件）带 text 时，应走 _handle_image_message 而非 _handle_text。"""
        handler, adapter, api, store = mock_handler
        mock_fn = _make_stream_mock(ChatResult(reply="ok", session_id="s1"))
        api.stream_chat_events = mock_fn

        msg = ChannelMessage(
            channel="mock", user=ChannelUser(user_id="1"),
            chat_id="100", text="描述文字",
            images=[ImageAttachment(data=b"img")],
        )
        await handler.handle_message(msg)

        call = mock_fn.calls[0]
        # 应有 images 参数（走了 image 路径）
        assert call["images"] is not None
        assert len(call["images"]) == 1

    @pytest.mark.asyncio
    async def test_document_image_dual_channel(self, mock_handler):
        """图片文件上传后 + 文本指令，stream_chat 应同时收到 images 参数（双通道）。"""
        handler, adapter, api, store = mock_handler
        api.upload_to_workspace = AsyncMock(return_value="/workspace/photo.jpg")
        mock_fn = _make_stream_mock(ChatResult(reply="已分析图片", session_id="s1"))
        api.stream_chat_events = mock_fn

        # 第 1 步：发送图片文件（无文本）→ 缓冲
        msg1 = ChannelMessage(
            channel="mock", user=ChannelUser(user_id="1"),
            chat_id="100", text="",
            files=[FileAttachment(
                filename="photo.jpg", data=b"jpeg-data",
                mime_type="image/jpeg",
            )],
        )
        await handler.handle_message(msg1)
        api.upload_to_workspace.assert_called_once()
        assert len(mock_fn.calls) == 0  # 缓冲阶段不调用 AI

        # 第 2 步：发送文本指令 → 消费缓冲，触发 AI
        msg2 = ChannelMessage(
            channel="mock", user=ChannelUser(user_id="1"),
            chat_id="100", text="分析这张图",
        )
        await handler.handle_message(msg2)

        call = mock_fn.calls[0]
        assert call["images"] is not None
        assert len(call["images"]) == 1
        assert call["images"][0]["media_type"] == "image/jpeg"

    @pytest.mark.asyncio
    async def test_document_excel_no_images(self, mock_handler):
        """Excel 文件上传后 + 文本指令，不应附带 images 参数。"""
        handler, adapter, api, store = mock_handler
        api.upload_to_workspace = AsyncMock(return_value="/workspace/data.xlsx")
        mock_fn = _make_stream_mock(ChatResult(reply="已分析", session_id="s1"))
        api.stream_chat_events = mock_fn

        # 第 1 步：发送 Excel 文件（无文本）→ 缓冲
        msg1 = ChannelMessage(
            channel="mock", user=ChannelUser(user_id="1"),
            chat_id="100", text="",
            files=[FileAttachment(
                filename="data.xlsx", data=b"xlsx-data",
                mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )],
        )
        await handler.handle_message(msg1)

        # 第 2 步：发送文本指令 → 消费缓冲
        msg2 = ChannelMessage(
            channel="mock", user=ChannelUser(user_id="1"),
            chat_id="100", text="帮我分析",
        )
        await handler.handle_message(msg2)

        assert mock_fn.calls[0]["images"] is None

    @pytest.mark.asyncio
    async def test_image_buffers_without_text(self, mock_handler):
        """纯图片无文本 → 缓冲 + 询问用户。"""
        handler, adapter, api, store = mock_handler

        msg = ChannelMessage(
            channel="mock", user=ChannelUser(user_id="1"),
            chat_id="100",
            images=[ImageAttachment(data=b"img")],
        )
        await handler.handle_message(msg)
        api.stream_chat_events.assert_not_called()
        assert any("已收到图片" in t[1] for t in adapter.sent_texts)
        assert "100:1" in handler._pending_files

    @pytest.mark.asyncio
    async def test_image_error_handling(self, mock_handler):
        """图片 + 文本处理失败 → 发送错误消息。"""
        handler, adapter, api, store = mock_handler
        api.stream_chat_events = _make_stream_mock(RuntimeError("VLM 不可用"))

        msg = ChannelMessage(
            channel="mock", user=ChannelUser(user_id="1"),
            chat_id="100", text="分析图片",
            images=[ImageAttachment(data=b"img")],
        )
        await handler.handle_message(msg)
        assert any("出错了" in t[1] for t in adapter.sent_texts)


# ── 文件缓冲测试 ──


class TestPendingFileBuffer:
    """验证文件/图片发送后缓冲等待用户指令的行为。"""

    @pytest.mark.asyncio
    async def test_file_with_text_processes_immediately(self, mock_handler):
        """文件 + 文本（如 Telegram caption）→ 立即处理，不缓冲。"""
        handler, adapter, api, store = mock_handler
        api.upload_to_workspace = AsyncMock(return_value="/workspace/test.xlsx")
        mock_fn = _make_stream_mock(ChatResult(reply="已分析", session_id="s1"))
        api.stream_chat_events = mock_fn

        msg = ChannelMessage(
            channel="mock", user=ChannelUser(user_id="1"),
            chat_id="100", text="帮我分析这个表",
            files=[FileAttachment(filename="test.xlsx", data=b"\x00")],
        )
        await handler.handle_message(msg)

        api.upload_to_workspace.assert_called_once()
        assert len(mock_fn.calls) == 1
        assert "@file:test.xlsx" in mock_fn.calls[0]["message"]
        assert "帮我分析这个表" in mock_fn.calls[0]["message"]
        # 缓冲应已消费
        assert "100:1" not in handler._pending_files

    @pytest.mark.asyncio
    async def test_file_then_text_two_step(self, mock_handler):
        """文件（无文本）→ 缓冲；随后文本 → 合并处理。"""
        handler, adapter, api, store = mock_handler
        api.upload_to_workspace = AsyncMock(return_value="/workspace/data.csv")
        mock_fn = _make_stream_mock(ChatResult(reply="分析完毕", session_id="s1"))
        api.stream_chat_events = mock_fn

        # Step 1: 文件
        msg1 = ChannelMessage(
            channel="mock", user=ChannelUser(user_id="1"),
            chat_id="100", text="",
            files=[FileAttachment(filename="data.csv", data=b"a,b\n1,2")],
        )
        await handler.handle_message(msg1)
        assert "100:1" in handler._pending_files
        assert len(mock_fn.calls) == 0  # 缓冲阶段不调用 AI

        # Step 2: 文本指令
        msg2 = ChannelMessage(
            channel="mock", user=ChannelUser(user_id="1"),
            chat_id="100", text="统计每列的均值",
        )
        await handler.handle_message(msg2)
        assert len(mock_fn.calls) == 1
        assert "@file:data.csv" in mock_fn.calls[0]["message"]
        assert "统计每列的均值" in mock_fn.calls[0]["message"]
        # 缓冲应已消费
        assert "100:1" not in handler._pending_files

    @pytest.mark.asyncio
    async def test_multiple_files_accumulate(self, mock_handler):
        """连续发送多个文件 → 全部累积在缓冲中。"""
        handler, adapter, api, store = mock_handler
        api.upload_to_workspace = AsyncMock()
        mock_fn = _make_stream_mock(ChatResult(reply="ok", session_id="s1"))
        api.stream_chat_events = mock_fn

        for fname in ("a.xlsx", "b.csv"):
            msg = ChannelMessage(
                channel="mock", user=ChannelUser(user_id="1"),
                chat_id="100", text="",
                files=[FileAttachment(filename=fname, data=b"\x00")],
            )
            await handler.handle_message(msg)

        assert len(handler._pending_files["100:1"]) == 2
        assert len(mock_fn.calls) == 0  # 缓冲阶段不调用 AI

        # 发送指令 → 两个文件一起处理
        msg_text = ChannelMessage(
            channel="mock", user=ChannelUser(user_id="1"),
            chat_id="100", text="合并这两个文件",
        )
        await handler.handle_message(msg_text)
        assert len(mock_fn.calls) == 1
        assert "@file:a.xlsx" in mock_fn.calls[0]["message"]
        assert "@file:b.csv" in mock_fn.calls[0]["message"]

    @pytest.mark.asyncio
    async def test_image_then_text_two_step(self, mock_handler):
        """纯图片（无文本）→ 缓冲；随后文本 → 合并处理（vision）。"""
        handler, adapter, api, store = mock_handler
        mock_fn = _make_stream_mock(ChatResult(reply="图片分析结果", session_id="s1"))
        api.stream_chat_events = mock_fn

        # Step 1: 图片
        msg1 = ChannelMessage(
            channel="mock", user=ChannelUser(user_id="1"),
            chat_id="100",
            images=[ImageAttachment(data=b"png-data", media_type="image/png")],
        )
        await handler.handle_message(msg1)
        assert "100:1" in handler._pending_files
        assert len(mock_fn.calls) == 0  # 缓冲阶段不调用 AI

        # Step 2: 文本
        msg2 = ChannelMessage(
            channel="mock", user=ChannelUser(user_id="1"),
            chat_id="100", text="这张截图里的数据帮我整理",
        )
        await handler.handle_message(msg2)
        call = mock_fn.calls[0]
        assert call["images"] is not None
        assert len(call["images"]) == 1
        assert call["images"][0]["media_type"] == "image/png"

    @pytest.mark.asyncio
    async def test_new_command_clears_pending_files(self, mock_handler):
        """/new 命令应清除待处理文件缓冲。"""
        handler, adapter, api, store = mock_handler
        api.upload_to_workspace = AsyncMock()

        # 先上传一个文件
        msg1 = ChannelMessage(
            channel="mock", user=ChannelUser(user_id="1"),
            chat_id="100", text="",
            files=[FileAttachment(filename="test.xlsx", data=b"\x00")],
        )
        await handler.handle_message(msg1)
        assert "100:1" in handler._pending_files

        # /new
        msg2 = ChannelMessage(
            channel="mock", user=ChannelUser(user_id="1"),
            chat_id="100", is_command=True, command="new",
        )
        await handler.handle_message(msg2)
        assert "100:1" not in handler._pending_files

    @pytest.mark.asyncio
    async def test_pending_files_ttl_cleanup(self, mock_handler):
        """过期的待处理文件缓冲应被自动清理。"""
        handler, adapter, api, store = mock_handler
        from excelmanus.channels.message_handler import PendingFile

        pf = PendingFile(filename="old.xlsx")
        pf.created_at = 0.0  # 强制过期
        handler._pending_files["100:1"] = [pf]

        handler._cleanup_expired_pending()
        assert "100:1" not in handler._pending_files

    @pytest.mark.asyncio
    async def test_image_with_text_processes_immediately(self, mock_handler):
        """图片 + 文本 → 立即处理，不缓冲。"""
        handler, adapter, api, store = mock_handler
        mock_fn = _make_stream_mock(ChatResult(reply="ok", session_id="s1"))
        api.stream_chat_events = mock_fn

        msg = ChannelMessage(
            channel="mock", user=ChannelUser(user_id="1"),
            chat_id="100", text="OCR 识别文字",
            images=[ImageAttachment(data=b"img", media_type="image/jpeg")],
        )
        await handler.handle_message(msg)

        assert len(mock_fn.calls) == 1
        assert mock_fn.calls[0]["images"] is not None
        assert "100:1" not in handler._pending_files


# ── 健壮性改进测试 ──


class TestAPIClientRetry:
    """验证 _request() 指数退避重试逻辑。"""

    @pytest.mark.asyncio
    async def test_retry_on_transport_error(self):
        """TransportError 应触发重试，最终成功。"""
        import httpx

        client = ExcelManusAPIClient(
            api_url="http://fake-host:9999",
            max_retries=3,
            retry_base_delay=0.01,
        )
        call_count = [0]
        original_request = client._client.request

        async def mock_request(method, url, **kwargs):
            call_count[0] += 1
            if call_count[0] < 3:
                raise httpx.ConnectError("connection refused")
            # 第 3 次成功
            resp = httpx.Response(200, json={"ok": True}, request=httpx.Request(method, url))
            return resp

        client._client.request = mock_request
        try:
            resp = await client._request("GET", "http://fake-host:9999/test")
            assert resp.status_code == 200
            assert call_count[0] == 3
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_retry_on_503(self):
        """503 应触发重试。"""
        import httpx

        client = ExcelManusAPIClient(
            api_url="http://fake-host:9999",
            max_retries=2,
            retry_base_delay=0.01,
        )
        call_count = [0]

        async def mock_request(method, url, **kwargs):
            call_count[0] += 1
            if call_count[0] < 2:
                return httpx.Response(503, request=httpx.Request(method, url))
            return httpx.Response(200, json={"ok": True}, request=httpx.Request(method, url))

        client._client.request = mock_request
        try:
            resp = await client._request("GET", "http://fake-host:9999/test")
            assert resp.status_code == 200
            assert call_count[0] == 2
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_retry_exhausted_raises(self):
        """重试用尽应抛出最后的异常。"""
        import httpx

        client = ExcelManusAPIClient(
            api_url="http://fake-host:9999",
            max_retries=2,
            retry_base_delay=0.01,
        )

        async def always_fail(method, url, **kwargs):
            raise httpx.ConnectError("connection refused")

        client._client.request = always_fail
        try:
            with pytest.raises(httpx.ConnectError):
                await client._request("GET", "http://fake-host:9999/test")
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_no_retry_on_4xx(self):
        """4xx（非 429）不应重试。"""
        import httpx

        client = ExcelManusAPIClient(
            api_url="http://fake-host:9999",
            max_retries=3,
            retry_base_delay=0.01,
        )
        call_count = [0]

        async def mock_request(method, url, **kwargs):
            call_count[0] += 1
            return httpx.Response(404, request=httpx.Request(method, url))

        client._client.request = mock_request
        try:
            resp = await client._request("GET", "http://fake-host:9999/test")
            assert resp.status_code == 404
            assert call_count[0] == 1  # 不重试
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_retry_429_with_retry_after(self):
        """429 应尊重 Retry-After 头。"""
        import httpx

        client = ExcelManusAPIClient(
            api_url="http://fake-host:9999",
            max_retries=2,
            retry_base_delay=0.01,
        )
        call_count = [0]

        async def mock_request(method, url, **kwargs):
            call_count[0] += 1
            if call_count[0] < 2:
                return httpx.Response(
                    429,
                    headers={"Retry-After": "0.01"},
                    request=httpx.Request(method, url),
                )
            return httpx.Response(200, json={"ok": True}, request=httpx.Request(method, url))

        client._client.request = mock_request
        try:
            resp = await client._request("GET", "http://fake-host:9999/test")
            assert resp.status_code == 200
            assert call_count[0] == 2
        finally:
            await client.close()


class TestSendFileViaDownload:
    """验证 _send_chat_result 通过 download_file 获取字节再发送。"""

    @pytest.mark.asyncio
    async def test_file_download_sends_bytes(self, mock_handler):
        """文件下载应通过 api.download_file 获取字节传给 adapter.send_file。"""
        handler, adapter, api, store = mock_handler
        file_content = b"fake-excel-content"
        api.download_file = AsyncMock(return_value=(file_content, "result.xlsx"))
        api.stream_chat_events = _make_stream_mock(ChatResult(
            reply="完成",
            session_id="s1",
            file_downloads=[{"file_path": "/workspace/result.xlsx", "filename": "result.xlsx"}],
        ))
        msg = ChannelMessage(
            channel="mock", user=ChannelUser(user_id="1"),
            chat_id="100", text="处理数据",
        )
        await handler.handle_message(msg)
        # download_file 应被调用
        api.download_file.assert_called_once_with("/workspace/result.xlsx", on_behalf_of="channel_anon:mock:1")
        # adapter.send_file 应收到字节数据
        assert len(adapter.sent_files) == 1
        assert adapter.sent_files[0] == ("100", file_content, "result.xlsx")

    @pytest.mark.asyncio
    async def test_file_download_error_graceful(self, mock_handler):
        """download_file 失败不应阻止消息处理。"""
        handler, adapter, api, store = mock_handler
        api.download_file = AsyncMock(side_effect=RuntimeError("download failed"))
        api.stream_chat_events = _make_stream_mock(ChatResult(
            reply="部分完成",
            session_id="s1",
            file_downloads=[{"file_path": "/workspace/err.xlsx"}],
        ))
        msg = ChannelMessage(
            channel="mock", user=ChannelUser(user_id="1"),
            chat_id="100", text="处理",
        )
        await handler.handle_message(msg)
        # 文本回复仍应发送
        assert len(adapter.sent_markdowns) == 1
        # 文件发送失败，不应崩溃
        assert len(adapter.sent_files) == 0


class TestPendingTTL:
    """验证 pending 交互的 TTL 过期清理。"""

    @pytest.mark.asyncio
    async def test_expired_pending_cleanup(self, mock_handler):
        """过期的 pending 交互应被自动清理。"""
        handler, adapter, api, store = mock_handler
        from excelmanus.channels.message_handler import PendingInteraction, PENDING_TTL_SECONDS

        # 手动创建一个 "已过期" 的 pending
        pending = PendingInteraction(
            interaction_type="question",
            interaction_id="q-old",
            session_id="s1",
            chat_id="100",
        )
        # 将 created_at 设为很久以前
        pending.created_at = time.monotonic() - PENDING_TTL_SECONDS - 10
        handler._pending["100:1"] = pending

        # 发送一条普通消息，触发 cleanup
        mock_fn = _make_stream_mock(ChatResult(reply="ok", session_id="s1"))
        api.stream_chat_events = mock_fn
        msg = ChannelMessage(
            channel="mock", user=ChannelUser(user_id="1"),
            chat_id="100", text="hello",
        )
        await handler.handle_message(msg)

        # 过期的 pending 应已被清理
        assert "100:1" not in handler._pending
        # 消息不应被路由到 answer_question（因为 pending 已过期）
        assert len(mock_fn.calls) == 1

    @pytest.mark.asyncio
    async def test_unexpired_pending_not_cleaned(self, mock_handler):
        """未过期的 pending 交互不应被清理。"""
        handler, adapter, api, store = mock_handler
        from excelmanus.channels.message_handler import PendingInteraction

        store.set("mock", "100", "1", "s1")
        api.answer_question = AsyncMock(return_value={"status": "answered"})

        handler._pending["100:1"] = PendingInteraction(
            interaction_type="question",
            interaction_id="q1",
            session_id="s1",
            chat_id="100",
        )

        msg = ChannelMessage(
            channel="mock", user=ChannelUser(user_id="1"),
            chat_id="100", text="回答问题",
        )
        await handler.handle_message(msg)

        # 未过期的 pending 应正常路由到 answer_question
        api.answer_question.assert_called_once_with("s1", "q1", "回答问题", on_behalf_of="channel_anon:mock:1")


class TestPendingCreatedAt:
    """验证 PendingInteraction 带 created_at 时间戳。"""

    def test_created_at_auto_set(self):
        from excelmanus.channels.message_handler import PendingInteraction
        before = time.monotonic()
        p = PendingInteraction(
            interaction_type="approval",
            interaction_id="a1",
            session_id="s1",
            chat_id="100",
        )
        after = time.monotonic()
        assert before <= p.created_at <= after


# ── 并发模式测试 ──


class TestConcurrencyCommand:
    """验证 /concurrency 命令路由与状态切换。"""

    @pytest.mark.asyncio
    async def test_show_current_concurrency(self, mock_handler):
        """无参数时显示当前并发模式。"""
        handler, adapter, api, store = mock_handler
        msg = ChannelMessage(
            channel="mock", user=ChannelUser(user_id="1"),
            chat_id="100", is_command=True, command="concurrency",
        )
        await handler.handle_message(msg)
        assert len(adapter.sent_texts) == 1
        assert "排队" in adapter.sent_texts[0][1]

    @pytest.mark.asyncio
    async def test_switch_concurrency_to_steer(self, mock_handler):
        """切换到 steer 模式。"""
        handler, adapter, api, store = mock_handler
        msg = ChannelMessage(
            channel="mock", user=ChannelUser(user_id="1"),
            chat_id="100", is_command=True, command="concurrency",
            command_args=["steer"],
        )
        await handler.handle_message(msg)
        assert len(adapter.sent_texts) == 1
        assert "转向" in adapter.sent_texts[0][1]
        assert handler._get_user_concurrency("100", "1") == "steer"

    @pytest.mark.asyncio
    async def test_switch_concurrency_to_guide(self, mock_handler):
        """切换到 guide 模式。"""
        handler, adapter, api, store = mock_handler
        msg = ChannelMessage(
            channel="mock", user=ChannelUser(user_id="1"),
            chat_id="100", is_command=True, command="concurrency",
            command_args=["guide"],
        )
        await handler.handle_message(msg)
        assert "引导" in adapter.sent_texts[0][1]
        assert handler._get_user_concurrency("100", "1") == "guide"

    @pytest.mark.asyncio
    async def test_switch_concurrency_invalid(self, mock_handler):
        """无效模式参数应报错。"""
        handler, adapter, api, store = mock_handler
        msg = ChannelMessage(
            channel="mock", user=ChannelUser(user_id="1"),
            chat_id="100", is_command=True, command="concurrency",
            command_args=["invalid"],
        )
        await handler.handle_message(msg)
        assert "无效并发模式" in adapter.sent_texts[0][1]

    @pytest.mark.asyncio
    async def test_switch_concurrency_same_mode(self, mock_handler):
        """切换到当前已有模式应提示已是该模式。"""
        handler, adapter, api, store = mock_handler
        msg = ChannelMessage(
            channel="mock", user=ChannelUser(user_id="1"),
            chat_id="100", is_command=True, command="concurrency",
            command_args=["queue"],
        )
        await handler.handle_message(msg)
        assert "当前已是" in adapter.sent_texts[0][1]


class TestSteerMode:
    """验证 steer 并发模式。"""

    @pytest.mark.asyncio
    async def test_steer_no_existing_task_runs_normally(self, mock_handler):
        """steer 模式下无 in-flight task 时正常执行。"""
        handler, adapter, api, store = mock_handler
        handler._user_concurrency["100:1"] = "steer"
        store.set("mock", "100", "1", "s1")
        mock_fn = _make_stream_mock(ChatResult(reply="ok", session_id="s1"))
        api.stream_chat_events = mock_fn

        msg = ChannelMessage(
            channel="mock", user=ChannelUser(user_id="1"),
            chat_id="100", text="hello",
        )
        await handler.handle_message(msg)
        assert len(mock_fn.calls) == 1

    @pytest.mark.asyncio
    async def test_steer_cancels_existing_task(self, mock_handler):
        """steer 模式下有 in-flight task 时应中断旧任务并调用 abort。"""
        handler, adapter, api, store = mock_handler
        handler._user_concurrency["100:1"] = "steer"
        store.set("mock", "100", "1", "s1")
        api.abort = AsyncMock(return_value={"status": "cancelled"})
        api.stream_chat_events = _make_stream_mock(ChatResult(reply="new", session_id="s1"))

        # 直接注入一个尚未完成的 mock task 到 _user_tasks
        old_cancelled = asyncio.Event()

        async def _hang():
            try:
                await asyncio.sleep(100)
            except asyncio.CancelledError:
                old_cancelled.set()
                raise

        old_task = asyncio.create_task(_hang())
        handler._user_tasks["100:1"] = old_task
        # 让 old_task 启动
        await asyncio.sleep(0)

        msg_new = ChannelMessage(
            channel="mock", user=ChannelUser(user_id="1"),
            chat_id="100", text="new request",
        )
        await handler.handle_message(msg_new)

        # 旧任务应已被取消
        assert old_cancelled.is_set()
        # 应有中断提示
        assert any("中断" in t[1] for t in adapter.sent_texts)
        # _safe_abort 应被调度
        await asyncio.sleep(0.05)
        api.abort.assert_called_once()
        assert api.abort.call_args[0][0] == "s1"

    @pytest.mark.asyncio
    async def test_steer_safe_abort_swallows_errors(self, mock_handler):
        """_safe_abort 应吞掉异常，不抛出。"""
        handler, adapter, api, store = mock_handler
        api.abort = AsyncMock(side_effect=Exception("network error"))
        await handler._safe_abort("s1")  # 不应抛出


class TestGuideMode:
    """验证 guide 并发模式。"""

    @pytest.mark.asyncio
    async def test_guide_no_inflight_runs_normally(self, mock_handler):
        """guide 模式下无 in-flight 任务时正常执行。"""
        handler, adapter, api, store = mock_handler
        handler._user_concurrency["100:1"] = "guide"
        store.set("mock", "100", "1", "s1")
        mock_fn = _make_stream_mock(ChatResult(reply="ok", session_id="s1"))
        api.stream_chat_events = mock_fn

        msg = ChannelMessage(
            channel="mock", user=ChannelUser(user_id="1"),
            chat_id="100", text="hello",
        )
        await handler.handle_message(msg)
        assert len(mock_fn.calls) == 1

    @pytest.mark.asyncio
    async def test_guide_injects_when_locked(self, mock_handler):
        """guide 模式下 lock 已占用时应调用 guide_message 注入。"""
        handler, adapter, api, store = mock_handler
        handler._user_concurrency["100:1"] = "guide"
        store.set("mock", "100", "1", "s1")
        api.guide_message = AsyncMock(return_value={"status": "delivered", "in_flight": True})

        # 手动获取锁模拟 in-flight
        lock = handler._get_user_lock("100", "1")
        await lock.acquire()
        try:
            msg = ChannelMessage(
                channel="mock", user=ChannelUser(user_id="1"),
                chat_id="100", text="追加指令",
            )
            await handler.handle_message(msg)

            api.guide_message.assert_called_once_with("s1", "追加指令", on_behalf_of="channel_anon:mock:1")
            assert any("已送达" in t[1] for t in adapter.sent_texts)
        finally:
            lock.release()

    @pytest.mark.asyncio
    async def test_guide_no_session_warns(self, mock_handler):
        """guide 模式下 lock 已占用但无 session 时应警告。"""
        handler, adapter, api, store = mock_handler
        handler._user_concurrency["100:1"] = "guide"
        # 不设置 session

        lock = handler._get_user_lock("100", "1")
        await lock.acquire()
        try:
            msg = ChannelMessage(
                channel="mock", user=ChannelUser(user_id="1"),
                chat_id="100", text="追加",
            )
            await handler.handle_message(msg)
            assert any("无活跃会话" in t[1] for t in adapter.sent_texts)
        finally:
            lock.release()

    @pytest.mark.asyncio
    async def test_guide_api_failure(self, mock_handler):
        """guide_message API 失败时应显示错误。"""
        handler, adapter, api, store = mock_handler
        handler._user_concurrency["100:1"] = "guide"
        store.set("mock", "100", "1", "s1")
        api.guide_message = AsyncMock(side_effect=Exception("connection refused"))

        lock = handler._get_user_lock("100", "1")
        await lock.acquire()
        try:
            msg = ChannelMessage(
                channel="mock", user=ChannelUser(user_id="1"),
                chat_id="100", text="追加",
            )
            await handler.handle_message(msg)
            assert any("投递失败" in t[1] for t in adapter.sent_texts)
        finally:
            lock.release()


# ── Plan A: 模型与配额统一测试 ──


class TestModelQuotaUnification:
    """Plan A: 模型/配额命令携带 on_behalf_of + 权限拦截 + /quota 命令。"""

    @pytest.fixture
    def mock_handler(self, tmp_path):
        adapter = MockAdapter()
        api = AsyncMock(spec=ExcelManusAPIClient)
        store = SessionStore(store_path=tmp_path / "sessions.json")
        handler = MessageHandler(adapter=adapter, api_client=api, session_store=store)
        return handler, adapter, api, store

    # ── /model list 携带 on_behalf_of ──

    @pytest.mark.asyncio
    async def test_model_list_passes_on_behalf_of(self, mock_handler):
        """/model 无参数时 list_models 应携带 on_behalf_of。"""
        handler, adapter, api, store = mock_handler
        api.list_models = AsyncMock(return_value=[
            {"name": "gpt4", "model": "gpt-4o", "active": True, "description": ""},
        ])
        msg = ChannelMessage(
            channel="mock", user=ChannelUser(user_id="1"),
            chat_id="100", is_command=True, command="model", command_args=[],
        )
        await handler.handle_message(msg)
        api.list_models.assert_called_once_with(on_behalf_of="channel_anon:mock:1")
        assert any("可用模型" in t[1] for t in adapter.sent_texts)

    # ── /model <name> 携带 on_behalf_of ──

    @pytest.mark.asyncio
    async def test_model_switch_passes_on_behalf_of(self, mock_handler):
        """/model <name> 时 switch_model 应携带 on_behalf_of。"""
        handler, adapter, api, store = mock_handler
        api.switch_model = AsyncMock(return_value={"message": "ok"})
        msg = ChannelMessage(
            channel="mock", user=ChannelUser(user_id="1"),
            chat_id="100", is_command=True, command="model", command_args=["gpt4"],
        )
        await handler.handle_message(msg)
        api.switch_model.assert_called_once_with("gpt4", on_behalf_of="channel_anon:mock:1")
        assert any("已切换" in t[1] for t in adapter.sent_texts)

    # ── /addmodel 携带 on_behalf_of ──

    @pytest.mark.asyncio
    async def test_addmodel_passes_on_behalf_of(self, mock_handler):
        """/addmodel 应携带 on_behalf_of 调用 add_model。"""
        handler, adapter, api, store = mock_handler
        api.add_model = AsyncMock(return_value={"status": "created"})
        msg = ChannelMessage(
            channel="mock", user=ChannelUser(user_id="1"),
            chat_id="100", is_command=True, command="addmodel",
            command_args=["gpt4", "gpt-4o", "https://api.openai.com/v1", "sk-xxx", "desc"],
        )
        await handler.handle_message(msg)
        api.add_model.assert_called_once_with(
            "gpt4", "gpt-4o", "https://api.openai.com/v1", "sk-xxx", "desc",
            on_behalf_of="channel_anon:mock:1",
        )
        assert any("已添加" in t[1] for t in adapter.sent_texts)

    # ── /addmodel 403 → 友好提示 ──

    @pytest.mark.asyncio
    async def test_addmodel_403_shows_permission_error(self, mock_handler):
        """后端返回 403 时 /addmodel 应提示需要管理员权限。"""
        handler, adapter, api, store = mock_handler
        api.add_model = AsyncMock(
            side_effect=Exception("Client error '403 Forbidden'"),
        )
        msg = ChannelMessage(
            channel="mock", user=ChannelUser(user_id="1"),
            chat_id="100", is_command=True, command="addmodel",
            command_args=["gpt4", "gpt-4o", "https://api.openai.com/v1", "sk-xxx"],
        )
        await handler.handle_message(msg)
        text = adapter.sent_texts[0][1]
        assert "管理员权限" in text

    # ── /delmodel 携带 on_behalf_of ──

    @pytest.mark.asyncio
    async def test_delmodel_passes_on_behalf_of(self, mock_handler):
        """/delmodel 应携带 on_behalf_of 调用 delete_model。"""
        handler, adapter, api, store = mock_handler
        api.delete_model = AsyncMock(return_value={"status": "deleted"})
        msg = ChannelMessage(
            channel="mock", user=ChannelUser(user_id="1"),
            chat_id="100", is_command=True, command="delmodel",
            command_args=["gpt4"],
        )
        await handler.handle_message(msg)
        api.delete_model.assert_called_once_with("gpt4", on_behalf_of="channel_anon:mock:1")
        assert any("已删除" in t[1] for t in adapter.sent_texts)

    # ── /delmodel 403 → 友好提示 ──

    @pytest.mark.asyncio
    async def test_delmodel_403_shows_permission_error(self, mock_handler):
        """后端返回 403 时 /delmodel 应提示需要管理员权限。"""
        handler, adapter, api, store = mock_handler
        api.delete_model = AsyncMock(
            side_effect=Exception("Client error '403 Forbidden'"),
        )
        msg = ChannelMessage(
            channel="mock", user=ChannelUser(user_id="1"),
            chat_id="100", is_command=True, command="delmodel",
            command_args=["gpt4"],
        )
        await handler.handle_message(msg)
        text = adapter.sent_texts[0][1]
        assert "管理员权限" in text

    # ── /quota 正常输出 ──

    @pytest.mark.asyncio
    async def test_quota_shows_usage(self, mock_handler):
        """/quota 应展示 token 用量和配额。"""
        handler, adapter, api, store = mock_handler
        api.get_usage = AsyncMock(return_value={
            "daily_tokens": 1500,
            "monthly_tokens": 30000,
            "daily_limit": 10000,
            "monthly_limit": 200000,
            "daily_remaining": 8500,
            "monthly_remaining": 170000,
        })
        msg = ChannelMessage(
            channel="mock", user=ChannelUser(user_id="1"),
            chat_id="100", is_command=True, command="quota", command_args=[],
        )
        await handler.handle_message(msg)
        api.get_usage.assert_called_once_with(on_behalf_of="channel_anon:mock:1")
        text = adapter.sent_texts[0][1]
        assert "Token 用量" in text
        assert "1,500" in text
        assert "10,000" in text

    # ── /quota 无上限 ──

    @pytest.mark.asyncio
    async def test_quota_no_limit(self, mock_handler):
        """配额无上限时应显示（无上限）。"""
        handler, adapter, api, store = mock_handler
        api.get_usage = AsyncMock(return_value={
            "daily_tokens": 500,
            "monthly_tokens": 2000,
            "daily_limit": 0,
            "monthly_limit": 0,
            "daily_remaining": -1,
            "monthly_remaining": -1,
        })
        msg = ChannelMessage(
            channel="mock", user=ChannelUser(user_id="1"),
            chat_id="100", is_command=True, command="quota", command_args=[],
        )
        await handler.handle_message(msg)
        text = adapter.sent_texts[0][1]
        assert "无上限" in text

    # ── /quota 未绑定用户 → 提示绑定 ──

    @pytest.mark.asyncio
    async def test_quota_unbound_user_hint(self, mock_handler):
        """未绑定用户查询配额失败时应提示绑定。"""
        handler, adapter, api, store = mock_handler
        api.get_usage = AsyncMock(
            side_effect=Exception("Client error '401 Unauthorized'"),
        )
        msg = ChannelMessage(
            channel="mock", user=ChannelUser(user_id="1"),
            chat_id="100", is_command=True, command="quota", command_args=[],
        )
        await handler.handle_message(msg)
        text = adapter.sent_texts[0][1]
        assert "绑定" in text

    # ── /help 包含 /quota ──

    @pytest.mark.asyncio
    async def test_help_includes_quota(self, mock_handler):
        """/help 输出应包含 /quota 命令说明。"""
        handler, adapter, api, store = mock_handler
        msg = ChannelMessage(
            channel="mock", user=ChannelUser(user_id="1"),
            chat_id="100", is_command=True, command="help",
        )
        await handler.handle_message(msg)
        text = adapter.sent_texts[0][1]
        assert "/quota" in text
