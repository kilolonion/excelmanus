"""SSE 重连机制测试：覆盖 _SessionStreamState 与 /chat/subscribe 端点。"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from excelmanus.config import ExcelManusConfig
from excelmanus.events import EventType, ToolCallEvent
from excelmanus.session import SessionManager
from excelmanus.engine import ChatResult

import excelmanus.api as api_module
from excelmanus.api import _SessionStreamState, app


# ── 辅助函数 ──────────────────────────────────────────────


def _test_config(**overrides) -> ExcelManusConfig:
    defaults = dict(
        api_key="test-key",
        base_url="https://test.example.com/v1",
        model="test-model",
        workspace_root="/tmp/excelmanus-test-subscribe",
    )
    defaults.update(overrides)
    return ExcelManusConfig(**defaults)


def _make_transport():
    return ASGITransport(app=app, raise_app_exceptions=False)


def _make_event(event_type: EventType, **kwargs) -> ToolCallEvent:
    return ToolCallEvent(event_type=event_type, **kwargs)


# ── _SessionStreamState 单元测试 ─────────────────────────


class TestSessionStreamState:
    """_SessionStreamState 的核心行为测试。"""

    def test_deliver_with_subscriber_puts_to_queue(self):
        """有订阅者时，deliver 应将事件入队。"""
        state = _SessionStreamState()
        q = state.attach()
        event = _make_event(EventType.TEXT_DELTA, text_delta="hello")
        state.deliver(event)
        assert not q.empty()
        assert q.get_nowait() is event

    def test_deliver_without_subscriber_buffers(self):
        """无订阅者时，deliver 应缓冲事件。"""
        state = _SessionStreamState()
        event = _make_event(EventType.TEXT_DELTA, text_delta="hello")
        state.deliver(event)
        assert len(state.event_buffer) == 1
        assert state.event_buffer[0] is event

    def test_detach_switches_to_buffering(self):
        """detach 后，deliver 应切换到缓冲模式。"""
        state = _SessionStreamState()
        q = state.attach()
        state.detach()
        event = _make_event(EventType.TEXT_DELTA, text_delta="hello")
        state.deliver(event)
        assert q.empty()
        assert len(state.event_buffer) == 1

    def test_attach_creates_new_queue(self):
        """attach 应创建新的订阅者队列。"""
        state = _SessionStreamState()
        q1 = state.attach()
        q2 = state.attach()
        assert q1 is not q2
        assert state.subscriber_queue is q2

    def test_drain_buffer_returns_and_clears(self):
        """drain_buffer 应返回缓冲事件并清空缓冲区。"""
        state = _SessionStreamState()
        e1 = _make_event(EventType.TEXT_DELTA, text_delta="a")
        e2 = _make_event(EventType.TOOL_CALL_START, tool_name="test")
        state.deliver(e1)
        state.deliver(e2)
        buf = state.drain_buffer()
        assert len(buf) == 2
        assert buf[0] is e1
        assert buf[1] is e2
        assert len(state.event_buffer) == 0

    def test_buffer_limit(self):
        """缓冲区不应超过 buffer_limit。"""
        state = _SessionStreamState(buffer_limit=3)
        for i in range(5):
            state.deliver(_make_event(EventType.TEXT_DELTA, text_delta=str(i)))
        assert len(state.event_buffer) == 3

    def test_reconnect_flow(self):
        """模拟完整断连-重连流程：attach → detach → deliver → attach → drain。"""
        state = _SessionStreamState()
        # 首次连接
        q1 = state.attach()
        state.deliver(_make_event(EventType.TEXT_DELTA, text_delta="live1"))
        assert q1.get_nowait().text_delta == "live1"

        # 断连
        state.detach()
        state.deliver(_make_event(EventType.TEXT_DELTA, text_delta="buffered1"))
        state.deliver(_make_event(EventType.TOOL_CALL_START, tool_name="test"))

        # 重连
        q2 = state.attach()
        buf = state.drain_buffer()
        assert len(buf) == 2
        assert buf[0].text_delta == "buffered1"

        # 新的实时事件
        state.deliver(_make_event(EventType.TEXT_DELTA, text_delta="live2"))
        assert q2.get_nowait().text_delta == "live2"


# ── /chat/subscribe 端点集成测试 ──────────────────────────


def _setup_api_globals(session_manager, config=None):
    """设置 api_module 全局变量以供测试使用。"""
    api_module._session_manager = session_manager
    api_module._config = config or _test_config()
    api_module._active_chat_tasks = {}
    api_module._session_stream_states = {}


def _cleanup_api_globals():
    api_module._session_manager = None
    api_module._config = None
    api_module._active_chat_tasks = {}
    api_module._session_stream_states = {}


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    _cleanup_api_globals()


@pytest.mark.asyncio
async def test_subscribe_no_active_task_returns_done():
    """没有活跃任务时，subscribe 应返回 done。"""
    sm = MagicMock(spec=SessionManager)
    _setup_api_globals(sm)

    # 模拟 _has_session_access 返回 True
    with patch.object(api_module, "_has_session_access", new_callable=AsyncMock, return_value=True):
        async with AsyncClient(transport=_make_transport(), base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/chat/subscribe",
                json={"session_id": "test-session"},
            )
            assert resp.status_code == 200
            lines = resp.text.strip().split("\n")
            events = []
            for line in lines:
                if line.startswith("event:"):
                    events.append(line.split(":", 1)[1].strip())
            assert "session_init" in events
            assert "subscribe_resume" in events
            assert "done" in events


@pytest.mark.asyncio
async def test_subscribe_with_active_task_streams_events():
    """有活跃任务时，subscribe 应重放缓冲事件并流式推送新事件。"""
    sm = MagicMock(spec=SessionManager)
    _setup_api_globals(sm)

    session_id = "active-session"

    # 创建一个 stream state 并缓冲事件（模拟断连后）
    stream_state = _SessionStreamState()
    api_module._session_stream_states[session_id] = stream_state
    stream_state.deliver(_make_event(EventType.TEXT_DELTA, text_delta="buffered text"))

    # 创建一个模拟的活跃 chat task（很快完成）
    async def _fake_chat():
        await asyncio.sleep(0.1)
        return ChatResult(reply="done", iterations=1)

    chat_task = asyncio.create_task(_fake_chat())
    api_module._active_chat_tasks[session_id] = chat_task

    # 模拟 engine 用于生成回复
    mock_engine = MagicMock()
    mock_engine.last_route_result = MagicMock(
        route_mode="write", skills_used=[], tool_scope=[]
    )
    sm.get_engine.return_value = mock_engine

    with patch.object(api_module, "_has_session_access", new_callable=AsyncMock, return_value=True), \
         patch.object(api_module, "_is_external_safe_mode", return_value=False):
        async with AsyncClient(transport=_make_transport(), base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/chat/subscribe",
                json={"session_id": session_id},
            )
            assert resp.status_code == 200
            text = resp.text

            # 应包含 subscribe_resume reconnected 事件
            assert "subscribe_resume" in text
            assert "reconnected" in text

            # 应包含重放的 text_delta 事件（缓冲内容）
            assert "text_delta" in text
            assert "buffered text" in text

            # 应包含 done 事件
            assert "event: done" in text

    # Cleanup
    if not chat_task.done():
        chat_task.cancel()
        try:
            await chat_task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_subscribe_skip_replay_discards_buffer():
    """skip_replay=true 时，缓冲事件应被丢弃，不出现在 SSE 流中。"""
    sm = MagicMock(spec=SessionManager)
    _setup_api_globals(sm)

    session_id = "skip-replay-session"

    stream_state = _SessionStreamState()
    api_module._session_stream_states[session_id] = stream_state
    stream_state.deliver(_make_event(EventType.TEXT_DELTA, text_delta="should be skipped"))
    stream_state.deliver(_make_event(EventType.TOOL_CALL_START, tool_name="write_cells"))

    async def _fake_chat():
        await asyncio.sleep(0.1)
        return ChatResult(reply="done", iterations=1)

    chat_task = asyncio.create_task(_fake_chat())
    api_module._active_chat_tasks[session_id] = chat_task

    mock_engine = MagicMock()
    mock_engine.last_route_result = MagicMock(
        route_mode="write", skills_used=[], tool_scope=[]
    )
    sm.get_engine.return_value = mock_engine

    with patch.object(api_module, "_has_session_access", new_callable=AsyncMock, return_value=True), \
         patch.object(api_module, "_is_external_safe_mode", return_value=False):
        async with AsyncClient(transport=_make_transport(), base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/chat/subscribe",
                json={"session_id": session_id, "skip_replay": True},
            )
            assert resp.status_code == 200
            text = resp.text

            # 缓冲事件不应出现
            assert "should be skipped" not in text
            assert "write_cells" not in text

            # 仍应包含 subscribe_resume 和 done
            assert "subscribe_resume" in text
            assert "reconnected" in text
            assert "event: done" in text

            # buffered_count 应为 0
            assert '"buffered_count": 0' in text or '"buffered_count":0' in text

    if not chat_task.done():
        chat_task.cancel()
        try:
            await chat_task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_subscribe_access_denied():
    """无权限时返回 404。"""
    sm = MagicMock(spec=SessionManager)
    _setup_api_globals(sm)

    with patch.object(api_module, "_has_session_access", new_callable=AsyncMock, return_value=False):
        async with AsyncClient(transport=_make_transport(), base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/chat/subscribe",
                json={"session_id": "denied-session"},
            )
            assert resp.status_code == 404
