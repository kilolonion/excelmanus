"""Staged 文件管理功能单元测试。

覆盖:
- API Client: list_staged, apply_staged, discard_staged, undo_backup
- ChatResult.staging_event + stream_chat SSE 解析
- MessageHandler: /staged, /apply, /discard, /undoapply 命令
- MessageHandler: staged 回调按钮处理
- MessageHandler: staging_updated 自动通知
- ChannelAdapter: send_staged_card 默认实现
"""

from __future__ import annotations

import asyncio
from pathlib import PurePosixPath
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from excelmanus.channels.base import (
    ChannelAdapter,
    ChannelMessage,
    ChannelUser,
)
from excelmanus.channels.api_client import ChatResult, ExcelManusAPIClient
from excelmanus.channels.message_handler import MessageHandler
from excelmanus.channels.session_store import SessionStore

try:
    from excelmanus.channels.rate_limit import RateLimitConfig
except ImportError:
    RateLimitConfig = None  # type: ignore[assignment,misc]


# ── Mock Adapter ──

class MockAdapter(ChannelAdapter):
    """测试用 Mock 渠道适配器。"""

    name = "mock"

    def __init__(self):
        self.sent_texts: list[tuple[str, str]] = []
        self.sent_markdowns: list[tuple[str, str]] = []
        self.sent_files: list[tuple] = []
        self.sent_approvals: list[dict] = []
        self.sent_questions: list[dict] = []
        self.sent_staged_cards: list[dict] = []
        self.typing_calls: list[str] = []

    async def start(self):
        pass

    async def stop(self):
        pass

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
        })

    async def show_typing(self, chat_id):
        self.typing_calls.append(chat_id)

    async def send_staged_card(self, chat_id, files, pending_count, session_id):
        self.sent_staged_cards.append({
            "chat_id": chat_id, "files": files,
            "pending_count": pending_count, "session_id": session_id,
        })


@pytest.fixture
def handler_env(tmp_path):
    """创建 handler + adapter + api + store 测试环境。"""
    adapter = MockAdapter()
    api = AsyncMock(spec=ExcelManusAPIClient)
    store = SessionStore(store_path=tmp_path / "sessions.json")
    import inspect
    sig = inspect.signature(MessageHandler.__init__)
    kwargs: dict = {}
    if "rate_limit_config" in sig.parameters:
        kwargs["rate_limit_config"] = RateLimitConfig() if RateLimitConfig else None
    handler = MessageHandler(adapter=adapter, api_client=api, session_store=store, **kwargs)
    return handler, adapter, api, store


def _make_cmd(cmd: str, args: list[str] | None = None) -> ChannelMessage:
    return ChannelMessage(
        channel="mock",
        user=ChannelUser(user_id="1"),
        chat_id="100",
        is_command=True,
        command=cmd,
        command_args=args or [],
    )


def _make_callback(data: str) -> ChannelMessage:
    return ChannelMessage(
        channel="mock",
        user=ChannelUser(user_id="1"),
        chat_id="100",
        callback_data=data,
    )


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
    """Create a trackable async generator mock for api.stream_chat_events."""
    results = list(chat_results)
    idx = [0]
    call_log: list[dict] = []

    async def mock_fn(
        message, session_id=None, chat_mode="write", images=None,
        *, on_behalf_of=None, channel=None, **kwargs,
    ):
        call_log.append({
            "message": message,
            "session_id": session_id,
            "chat_mode": chat_mode,
            "images": images,
            "on_behalf_of": on_behalf_of,
            "channel": channel,
        })
        r = results[min(idx[0], len(results) - 1)]
        idx[0] += 1
        if isinstance(r, Exception):
            raise r
        for evt in _sse_events_from_result(r):
            yield evt

    mock_fn.calls = call_log  # type: ignore[attr-defined]
    return mock_fn


SAMPLE_STAGED_FILES = [
    {
        "original_path": "data/销售报表.xlsx",
        "backup_path": ".staging/data/销售报表.xlsx",
        "exists": True,
        "summary": {"cells_added": 12, "cells_removed": 3},
    },
    {
        "original_path": "data/客户数据.xlsx",
        "backup_path": ".staging/data/客户数据.xlsx",
        "exists": True,
        "summary": {"cells_changed": 5, "sheets_added": ["Sheet2"]},
    },
]


# ── API Client 方法测试 ──


def _mock_resp(data: dict) -> MagicMock:
    """创建 mock httpx.Response。"""
    resp = MagicMock()
    resp.json.return_value = data
    resp.raise_for_status = MagicMock()
    resp.status_code = 200
    return resp


class TestAPIClientStaged:
    @pytest.fixture
    def api_env(self):
        api = ExcelManusAPIClient(api_url="http://localhost:9999")
        api._request = AsyncMock()  # type: ignore[method-assign]
        return api

    @pytest.mark.asyncio
    async def test_list_staged(self, api_env):
        api = api_env
        api._request.return_value = _mock_resp({
            "files": SAMPLE_STAGED_FILES, "backup_enabled": True, "in_flight": False,
        })
        result = await api.list_staged("session-1")
        assert result["backup_enabled"] is True
        assert len(result["files"]) == 2
        api._request.assert_called_once()
        call_args, call_kwargs = api._request.call_args
        assert call_args == ("GET", "http://localhost:9999/api/v1/backup/list")
        assert call_kwargs["params"] == {"session_id": "session-1"}

    @pytest.mark.asyncio
    async def test_apply_staged(self, api_env):
        api = api_env
        api._request.return_value = _mock_resp({
            "status": "ok", "applied": [], "count": 2, "pending_count": 0,
        })
        result = await api.apply_staged("session-1")
        assert result["status"] == "ok"
        assert result["count"] == 2

    @pytest.mark.asyncio
    async def test_apply_staged_with_files(self, api_env):
        api = api_env
        api._request.return_value = _mock_resp({
            "status": "ok", "applied": [{"original": "a.xlsx", "backup": "b.xlsx"}],
            "count": 1, "pending_count": 1,
        })
        result = await api.apply_staged("session-1", files=["a.xlsx"])
        call_kwargs = api._request.call_args[1]
        assert call_kwargs["json"]["files"] == ["a.xlsx"]
        assert result["pending_count"] == 1

    @pytest.mark.asyncio
    async def test_discard_staged(self, api_env):
        api = api_env
        api._request.return_value = _mock_resp({
            "status": "ok", "discarded": "all", "pending_count": 0,
        })
        result = await api.discard_staged("session-1")
        assert result["discarded"] == "all"

    @pytest.mark.asyncio
    async def test_undo_backup(self, api_env):
        api = api_env
        api._request.return_value = _mock_resp({
            "status": "ok", "undone": "a.xlsx",
        })
        result = await api.undo_backup("s1", "a.xlsx", "undo/a.xlsx")
        call_kwargs = api._request.call_args[1]
        assert call_kwargs["json"]["original_path"] == "a.xlsx"
        assert call_kwargs["json"]["undo_path"] == "undo/a.xlsx"
        assert result["status"] == "ok"


# ── ChatResult.staging_event 测试 ──


class TestChatResultStaging:
    def test_staging_event_default_none(self):
        r = ChatResult()
        assert r.staging_event is None

    def test_staging_event_set(self):
        r = ChatResult(staging_event={"action": "new", "files": [], "pending_count": 1})
        assert r.staging_event["action"] == "new"


# ── /staged 命令测试 ──


class TestCmdStaged:
    @pytest.mark.asyncio
    async def test_staged_no_session(self, handler_env):
        handler, adapter, api, store = handler_env
        msg = _make_cmd("staged")
        await handler.handle_message(msg)
        assert any("没有活跃的会话" in t[1] for t in adapter.sent_texts)

    @pytest.mark.asyncio
    async def test_staged_backup_disabled(self, handler_env):
        handler, adapter, api, store = handler_env
        store.set("mock", "100", "1", "s1")
        api.list_staged = AsyncMock(return_value={"backup_enabled": False, "files": []})
        msg = _make_cmd("staged")
        await handler.handle_message(msg)
        assert any("未启用备份模式" in t[1] for t in adapter.sent_texts)

    @pytest.mark.asyncio
    async def test_staged_empty(self, handler_env):
        handler, adapter, api, store = handler_env
        store.set("mock", "100", "1", "s1")
        api.list_staged = AsyncMock(return_value={"backup_enabled": True, "files": []})
        msg = _make_cmd("staged")
        await handler.handle_message(msg)
        assert any("暂无待确认文件" in t[1] for t in adapter.sent_texts)

    @pytest.mark.asyncio
    async def test_staged_with_files(self, handler_env):
        handler, adapter, api, store = handler_env
        store.set("mock", "100", "1", "s1")
        api.list_staged = AsyncMock(return_value={
            "backup_enabled": True,
            "files": SAMPLE_STAGED_FILES,
        })
        msg = _make_cmd("staged")
        await handler.handle_message(msg)
        assert len(adapter.sent_staged_cards) == 1
        card = adapter.sent_staged_cards[0]
        assert card["pending_count"] == 2
        assert card["session_id"] == "s1"
        # 验证缓存
        pk = handler._pending_key("100", "1")
        assert pk in handler._staged_cache
        assert len(handler._staged_cache[pk]) == 2

    @pytest.mark.asyncio
    async def test_staged_api_error(self, handler_env):
        handler, adapter, api, store = handler_env
        store.set("mock", "100", "1", "s1")
        api.list_staged = AsyncMock(side_effect=Exception("timeout"))
        msg = _make_cmd("staged")
        await handler.handle_message(msg)
        assert any("获取 staged 文件失败" in t[1] for t in adapter.sent_texts)


# ── /apply 命令测试 ──


class TestCmdApply:
    @pytest.mark.asyncio
    async def test_apply_all(self, handler_env):
        handler, adapter, api, store = handler_env
        store.set("mock", "100", "1", "s1")
        api.apply_staged = AsyncMock(return_value={
            "status": "ok", "count": 2, "pending_count": 0,
            "applied": [
                {"original": "a.xlsx", "backup": "b.xlsx", "undo_path": "undo/a.xlsx"},
                {"original": "c.xlsx", "backup": "d.xlsx", "undo_path": "undo/c.xlsx"},
            ],
        })
        msg = _make_cmd("apply")
        await handler.handle_message(msg)
        api.apply_staged.assert_called_once_with("s1", None, on_behalf_of="channel_anon:mock:1")
        assert any("已应用 2 个文件" in t[1] for t in adapter.sent_texts)
        # 验证 undo 缓存
        pk = handler._pending_key("100", "1")
        assert len(handler._last_apply[pk]) == 2

    @pytest.mark.asyncio
    async def test_apply_by_index(self, handler_env):
        handler, adapter, api, store = handler_env
        store.set("mock", "100", "1", "s1")
        # 先缓存 staged 文件列表
        pk = handler._pending_key("100", "1")
        handler._staged_cache[pk] = SAMPLE_STAGED_FILES
        api.apply_staged = AsyncMock(return_value={
            "status": "ok", "count": 1, "pending_count": 1,
            "applied": [{"original": "data/销售报表.xlsx", "backup": "b.xlsx", "undo_path": "undo/x.xlsx"}],
        })
        msg = _make_cmd("apply", ["1"])
        await handler.handle_message(msg)
        api.apply_staged.assert_called_once_with(
            "s1", ["data/销售报表.xlsx"], on_behalf_of="channel_anon:mock:1",
        )
        assert any("已应用 1 个文件" in t[1] for t in adapter.sent_texts)

    @pytest.mark.asyncio
    async def test_apply_invalid_index(self, handler_env):
        handler, adapter, api, store = handler_env
        store.set("mock", "100", "1", "s1")
        pk = handler._pending_key("100", "1")
        handler._staged_cache[pk] = SAMPLE_STAGED_FILES
        msg = _make_cmd("apply", ["99"])
        await handler.handle_message(msg)
        assert any("无效编号" in t[1] for t in adapter.sent_texts)

    @pytest.mark.asyncio
    async def test_apply_no_cache(self, handler_env):
        handler, adapter, api, store = handler_env
        store.set("mock", "100", "1", "s1")
        msg = _make_cmd("apply", ["1"])
        await handler.handle_message(msg)
        assert any("请先使用 /staged" in t[1] for t in adapter.sent_texts)

    @pytest.mark.asyncio
    async def test_apply_409(self, handler_env):
        handler, adapter, api, store = handler_env
        store.set("mock", "100", "1", "s1")
        api.apply_staged = AsyncMock(side_effect=Exception("409 Conflict"))
        msg = _make_cmd("apply")
        await handler.handle_message(msg)
        assert any("会话正在处理中" in t[1] for t in adapter.sent_texts)

    @pytest.mark.asyncio
    async def test_apply_no_session(self, handler_env):
        handler, adapter, api, store = handler_env
        msg = _make_cmd("apply")
        await handler.handle_message(msg)
        assert any("没有活跃的会话" in t[1] for t in adapter.sent_texts)


# ── /discard 命令测试 ──


class TestCmdDiscard:
    @pytest.mark.asyncio
    async def test_discard_all(self, handler_env):
        handler, adapter, api, store = handler_env
        store.set("mock", "100", "1", "s1")
        api.discard_staged = AsyncMock(return_value={
            "status": "ok", "discarded": "all", "pending_count": 0,
        })
        msg = _make_cmd("discard")
        await handler.handle_message(msg)
        api.discard_staged.assert_called_once_with("s1", None, on_behalf_of="channel_anon:mock:1")
        assert any("已丢弃全部文件" in t[1] for t in adapter.sent_texts)

    @pytest.mark.asyncio
    async def test_discard_by_index(self, handler_env):
        handler, adapter, api, store = handler_env
        store.set("mock", "100", "1", "s1")
        pk = handler._pending_key("100", "1")
        handler._staged_cache[pk] = SAMPLE_STAGED_FILES
        api.discard_staged = AsyncMock(return_value={
            "status": "ok", "discarded": 1, "pending_count": 1,
        })
        msg = _make_cmd("discard", ["2"])
        await handler.handle_message(msg)
        api.discard_staged.assert_called_once_with(
            "s1", ["data/客户数据.xlsx"], on_behalf_of="channel_anon:mock:1",
        )
        assert any("已丢弃 1 个文件" in t[1] for t in adapter.sent_texts)

    @pytest.mark.asyncio
    async def test_discard_409(self, handler_env):
        handler, adapter, api, store = handler_env
        store.set("mock", "100", "1", "s1")
        api.discard_staged = AsyncMock(side_effect=Exception("409 Conflict"))
        msg = _make_cmd("discard")
        await handler.handle_message(msg)
        assert any("会话正在处理中" in t[1] for t in adapter.sent_texts)


# ── /undoapply 命令测试 ──


class TestCmdUndoapply:
    @pytest.mark.asyncio
    async def test_undoapply_success(self, handler_env):
        handler, adapter, api, store = handler_env
        store.set("mock", "100", "1", "s1")
        pk = handler._pending_key("100", "1")
        handler._last_apply[pk] = [
            {"original_path": "a.xlsx", "undo_path": "undo/a.xlsx"},
        ]
        api.undo_backup = AsyncMock(return_value={"status": "ok"})
        msg = _make_cmd("undoapply")
        await handler.handle_message(msg)
        api.undo_backup.assert_called_once_with(
            "s1", "a.xlsx", "undo/a.xlsx", on_behalf_of="channel_anon:mock:1",
        )
        assert any("已撤销 1 个文件的 apply" in t[1] for t in adapter.sent_texts)
        # 缓存已清空
        assert pk not in handler._last_apply

    @pytest.mark.asyncio
    async def test_undoapply_no_cache(self, handler_env):
        handler, adapter, api, store = handler_env
        store.set("mock", "100", "1", "s1")
        msg = _make_cmd("undoapply")
        await handler.handle_message(msg)
        assert any("没有可撤销的 apply" in t[1] for t in adapter.sent_texts)

    @pytest.mark.asyncio
    async def test_undoapply_partial_failure(self, handler_env):
        handler, adapter, api, store = handler_env
        store.set("mock", "100", "1", "s1")
        pk = handler._pending_key("100", "1")
        handler._last_apply[pk] = [
            {"original_path": "a.xlsx", "undo_path": "undo/a.xlsx"},
            {"original_path": "b.xlsx", "undo_path": "undo/b.xlsx"},
        ]
        api.undo_backup = AsyncMock(side_effect=[
            {"status": "ok"},
            Exception("file not found"),
        ])
        msg = _make_cmd("undoapply")
        await handler.handle_message(msg)
        texts = " ".join(t[1] for t in adapter.sent_texts)
        assert "已撤销 1 个文件" in texts
        assert "撤销失败" in texts


# ── Staged 回调按钮测试 ──


class TestStagedCallback:
    @pytest.mark.asyncio
    async def test_apply_staged_callback_all(self, handler_env):
        handler, adapter, api, store = handler_env
        store.set("mock", "100", "1", "s1")
        api.apply_staged = AsyncMock(return_value={
            "status": "ok", "count": 2, "pending_count": 0,
            "applied": [
                {"original": "a.xlsx", "backup": "b.xlsx", "undo_path": "undo/a.xlsx"},
            ],
        })
        msg = _make_callback("apply_staged:s1:all")
        await handler.handle_message(msg)
        api.apply_staged.assert_called_once_with("s1", None, on_behalf_of="channel_anon:mock:1")
        assert any("已应用 2 个文件" in t[1] for t in adapter.sent_texts)

    @pytest.mark.asyncio
    async def test_discard_staged_callback_all(self, handler_env):
        handler, adapter, api, store = handler_env
        store.set("mock", "100", "1", "s1")
        api.discard_staged = AsyncMock(return_value={
            "status": "ok", "discarded": "all", "pending_count": 0,
        })
        msg = _make_callback("discard_staged:s1:all")
        await handler.handle_message(msg)
        api.discard_staged.assert_called_once_with("s1", None, on_behalf_of="channel_anon:mock:1")
        assert any("已丢弃全部文件" in t[1] for t in adapter.sent_texts)

    @pytest.mark.asyncio
    async def test_apply_staged_callback_by_index(self, handler_env):
        handler, adapter, api, store = handler_env
        store.set("mock", "100", "1", "s1")
        pk = handler._pending_key("100", "1")
        handler._staged_cache[pk] = SAMPLE_STAGED_FILES
        api.apply_staged = AsyncMock(return_value={
            "status": "ok", "count": 1, "pending_count": 1,
            "applied": [{"original": "data/销售报表.xlsx", "backup": "b.xlsx", "undo_path": "undo/x.xlsx"}],
        })
        msg = _make_callback("apply_staged:s1:0")
        await handler.handle_message(msg)
        api.apply_staged.assert_called_once_with(
            "s1", ["data/销售报表.xlsx"], on_behalf_of="channel_anon:mock:1",
        )

    @pytest.mark.asyncio
    async def test_staged_callback_expired_index(self, handler_env):
        handler, adapter, api, store = handler_env
        store.set("mock", "100", "1", "s1")
        msg = _make_callback("apply_staged:s1:5")
        await handler.handle_message(msg)
        assert any("文件索引已过期" in t[1] for t in adapter.sent_texts)

    @pytest.mark.asyncio
    async def test_staged_callback_409(self, handler_env):
        handler, adapter, api, store = handler_env
        store.set("mock", "100", "1", "s1")
        api.apply_staged = AsyncMock(side_effect=Exception("409 Conflict"))
        msg = _make_callback("apply_staged:s1:all")
        await handler.handle_message(msg)
        assert any("会话正在处理中" in t[1] for t in adapter.sent_texts)


# ── staging_updated 自动通知测试 ──


class TestStagingAutoNotify:
    @pytest.mark.asyncio
    async def test_staging_event_triggers_card(self, handler_env):
        handler, adapter, api, store = handler_env
        api.stream_chat_events = _make_stream_mock(ChatResult(
            reply="已修改文件",
            session_id="s1",
            staging_event={
                "action": "new",
                "files": [{"path": "data/test.xlsx"}],
                "pending_count": 1,
            },
        ))
        api.list_staged = AsyncMock(return_value={
            "backup_enabled": True,
            "files": [{"original_path": "data/test.xlsx", "exists": True}],
        })
        msg = ChannelMessage(
            channel="mock",
            user=ChannelUser(user_id="1"),
            chat_id="100",
            text="帮我修改文件",
        )
        await handler.handle_message(msg)
        assert len(adapter.sent_staged_cards) == 1

    @pytest.mark.asyncio
    async def test_staging_event_finish_hint_triggers_card(self, handler_env):
        """后端实际发射 action='finish_hint' 时也应触发 staged card。"""
        handler, adapter, api, store = handler_env
        api.stream_chat_events = _make_stream_mock(ChatResult(
            reply="完成",
            session_id="s1",
            staging_event={
                "action": "finish_hint",
                "files": [{"path": "data/test.xlsx"}],
                "pending_count": 1,
            },
        ))
        api.list_staged = AsyncMock(return_value={
            "backup_enabled": True,
            "files": [{"original_path": "data/test.xlsx", "exists": True}],
        })
        msg = ChannelMessage(
            channel="mock",
            user=ChannelUser(user_id="1"),
            chat_id="100",
            text="修改",
        )
        await handler.handle_message(msg)
        assert len(adapter.sent_staged_cards) == 1

    @pytest.mark.asyncio
    async def test_staging_event_non_new_ignored(self, handler_env):
        handler, adapter, api, store = handler_env
        api.stream_chat_events = _make_stream_mock(ChatResult(
            reply="已应用",
            session_id="s1",
            staging_event={
                "action": "applied",
                "files": [],
                "pending_count": 0,
            },
        ))
        msg = ChannelMessage(
            channel="mock",
            user=ChannelUser(user_id="1"),
            chat_id="100",
            text="test",
        )
        await handler.handle_message(msg)
        assert len(adapter.sent_staged_cards) == 0

    @pytest.mark.asyncio
    async def test_staging_event_fallback_text(self, handler_env):
        handler, adapter, api, store = handler_env
        api.stream_chat_events = _make_stream_mock(ChatResult(
            reply="done",
            session_id="s1",
            staging_event={
                "action": "new",
                "files": [{"path": "data/x.xlsx"}],
                "pending_count": 1,
            },
        ))
        api.list_staged = AsyncMock(side_effect=Exception("fail"))
        msg = ChannelMessage(
            channel="mock",
            user=ChannelUser(user_id="1"),
            chat_id="100",
            text="test",
        )
        await handler.handle_message(msg)
        # 降级为文本通知
        assert any("文件待确认" in t[1] for t in adapter.sent_texts)

    @pytest.mark.asyncio
    async def test_no_staging_event_no_card(self, handler_env):
        handler, adapter, api, store = handler_env
        api.stream_chat_events = _make_stream_mock(ChatResult(
            reply="hello", session_id="s1",
        ))
        msg = ChannelMessage(
            channel="mock",
            user=ChannelUser(user_id="1"),
            chat_id="100",
            text="hi",
        )
        await handler.handle_message(msg)
        assert len(adapter.sent_staged_cards) == 0

    @pytest.mark.asyncio
    async def test_empty_result_with_staging_event_not_empty_msg(self, handler_env):
        """staging_event 存在时不应触发"无回复内容"。"""
        handler, adapter, api, store = handler_env
        api.stream_chat_events = _make_stream_mock(ChatResult(
            reply="",
            session_id="s1",
            staging_event={
                "action": "new",
                "files": [{"path": "x.xlsx"}],
                "pending_count": 1,
            },
        ))
        api.list_staged = AsyncMock(return_value={
            "backup_enabled": True,
            "files": [{"original_path": "x.xlsx", "exists": True}],
        })
        msg = ChannelMessage(
            channel="mock",
            user=ChannelUser(user_id="1"),
            chat_id="100",
            text="test",
        )
        await handler.handle_message(msg)
        # 不应有"无回复内容"
        assert not any("无回复内容" in t[1] for t in adapter.sent_texts)


# ── ChannelAdapter.send_staged_card 默认实现测试 ──


class TestDefaultStagedCard:
    @pytest.mark.asyncio
    async def test_empty_files(self, handler_env):
        handler, adapter, api, store = handler_env
        # 直接调用基类默认实现
        base_adapter = adapter
        # MockAdapter 覆盖了 send_staged_card，所以测试用原始基类
        from excelmanus.channels.base import ChannelAdapter
        original_method = ChannelAdapter.send_staged_card
        # 临时用基类方法
        adapter.sent_texts.clear()
        await original_method(adapter, "100", [], 0, "s1")
        assert any("暂无待确认文件" in t[1] for t in adapter.sent_texts)

    @pytest.mark.asyncio
    async def test_with_summary(self, handler_env):
        handler, adapter, api, store = handler_env
        from excelmanus.channels.base import ChannelAdapter
        original_method = ChannelAdapter.send_staged_card
        adapter.sent_texts.clear()
        await original_method(adapter, "100", SAMPLE_STAGED_FILES, 2, "s1")
        text = adapter.sent_texts[0][1]
        assert "2 个文件待确认" in text
        assert "销售报表.xlsx" in text
        assert "+12" in text
        assert "/apply" in text


# ── Help 更新验证 ──


class TestHelpUpdated:
    @pytest.mark.asyncio
    async def test_help_contains_staged_commands(self, handler_env):
        handler, adapter, api, store = handler_env
        msg = _make_cmd("help")
        await handler.handle_message(msg)
        help_text = adapter.sent_texts[0][1]
        assert "/staged" in help_text
        assert "/apply" in help_text
        assert "/discard" in help_text
        assert "/undoapply" in help_text
        assert "文件管理" in help_text
