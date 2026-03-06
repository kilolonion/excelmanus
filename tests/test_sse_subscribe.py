"""SSE resume behavior tests for SessionStreamState and /chat/subscribe."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from excelmanus.engine import ChatResult
from excelmanus.events import EventType, ToolCallEvent
from excelmanus.api_sse import inject_seq_into_sse, sse_format

import excelmanus.api as api_module
from excelmanus.api import _SessionStreamState, app


def _make_transport() -> ASGITransport:
    return ASGITransport(app=app, raise_app_exceptions=False)


def _make_event(event_type: EventType, **kwargs) -> ToolCallEvent:
    return ToolCallEvent(event_type=event_type, **kwargs)


def _parse_sse_events(raw: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    cur_event = ""
    for line in raw.splitlines():
        if line.startswith("event:"):
            cur_event = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            cur_data = line.split(":", 1)[1].strip()
            try:
                payload = json.loads(cur_data)
            except json.JSONDecodeError:
                payload = {}
            events.append((cur_event, payload))
            cur_event = ""
    return events


def _setup_api_globals(session_manager):
    api_module._session_manager = session_manager
    api_module._config = MagicMock()
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


# ── SessionStreamState unit tests ──


class TestSessionStreamState:
    def test_deliver_with_subscriber_enqueues_seq_event(self):
        state = _SessionStreamState()
        q = state.attach()
        event = _make_event(EventType.TEXT_DELTA, text_delta="hello")

        seq = state.deliver(event)

        assert seq == 1
        queued = q.get_nowait()
        assert queued == (1, event)

    def test_deliver_without_subscriber_buffers_seq_event(self):
        state = _SessionStreamState()
        event = _make_event(EventType.TEXT_DELTA, text_delta="buffered")

        seq = state.deliver(event)

        assert seq == 1
        assert len(state.event_buffer) == 1
        assert state.event_buffer[0] == (1, event)

    def test_seq_monotonically_increases(self):
        state = _SessionStreamState()
        s1 = state.deliver(_make_event(EventType.TEXT_DELTA, text_delta="a"))
        s2 = state.deliver(_make_event(EventType.TEXT_DELTA, text_delta="b"))
        s3 = state.deliver(_make_event(EventType.TEXT_DELTA, text_delta="c"))

        assert s1 == 1
        assert s2 == 2
        assert s3 == 3
        assert state.current_seq == 3

    def test_stream_id_is_generated(self):
        state = _SessionStreamState()
        assert isinstance(state.stream_id, str)
        assert len(state.stream_id) == 12

    def test_buffer_overflow_keeps_latest_drops_oldest(self):
        state = _SessionStreamState(buffer_limit=3)
        for i in range(5):
            state.deliver(_make_event(EventType.TEXT_DELTA, text_delta=str(i)))

        assert len(state.event_buffer) == 3
        assert [e.text_delta for _, e in state.event_buffer] == ["2", "3", "4"]
        assert [s for s, _ in state.event_buffer] == [3, 4, 5]
        assert state._dropped_count == 2
        assert state.has_dropped is True

    def test_zero_buffer_limit_drops_all(self):
        state = _SessionStreamState(buffer_limit=0)
        state.deliver(_make_event(EventType.TEXT_DELTA, text_delta="x"))

        assert len(state.event_buffer) == 0
        assert state._dropped_count == 1
        assert state.has_dropped is True

    def test_drain_buffer_returns_seq_events_and_clears(self):
        state = _SessionStreamState()
        state.deliver(_make_event(EventType.TEXT_DELTA, text_delta="a"))
        state.deliver(_make_event(EventType.TEXT_DELTA, text_delta="b"))

        buf = state.drain_buffer()

        assert isinstance(buf, list)
        assert len(buf) == 2
        assert buf[0][0] == 1  # seq
        assert buf[1][0] == 2
        assert len(state.event_buffer) == 0

    def test_drain_buffer_with_after_seq_filters(self):
        state = _SessionStreamState()
        state.deliver(_make_event(EventType.TEXT_DELTA, text_delta="a"))  # seq=1
        state.deliver(_make_event(EventType.TEXT_DELTA, text_delta="b"))  # seq=2
        state.deliver(_make_event(EventType.TEXT_DELTA, text_delta="c"))  # seq=3

        buf = state.drain_buffer(after_seq=1)

        assert len(buf) == 2
        assert buf[0][0] == 2
        assert buf[1][0] == 3

    def test_drain_buffer_after_seq_beyond_buffer_returns_empty(self):
        state = _SessionStreamState()
        state.deliver(_make_event(EventType.TEXT_DELTA, text_delta="a"))  # seq=1

        buf = state.drain_buffer(after_seq=10)

        assert len(buf) == 0

    def test_first_buffered_seq(self):
        state = _SessionStreamState()
        assert state.first_buffered_seq is None

        state.deliver(_make_event(EventType.TEXT_DELTA, text_delta="a"))
        assert state.first_buffered_seq == 1

    def test_attach_detach_cycle(self):
        state = _SessionStreamState()
        q = state.attach()
        assert state.subscriber_queue is q

        state.detach()
        assert state.subscriber_queue is None

    def test_events_across_attach_detach_maintain_order(self):
        state = _SessionStreamState()
        q = state.attach()
        state.deliver(_make_event(EventType.TEXT_DELTA, text_delta="a"))
        state.detach()
        state.deliver(_make_event(EventType.TEXT_DELTA, text_delta="b"))
        q2 = state.attach()
        state.deliver(_make_event(EventType.TEXT_DELTA, text_delta="c"))

        seq_a, evt_a = q.get_nowait()
        assert evt_a.text_delta == "a"
        assert seq_a == 1
        assert [e.text_delta for _, e in state.event_buffer] == ["b"]
        seq_c, evt_c = q2.get_nowait()
        assert evt_c.text_delta == "c"
        assert seq_c == 3


# ── inject_seq_into_sse tests ──


class TestInjectSeq:
    def test_inject_seq_into_sse_adds_fields(self):
        sse = sse_format("text_delta", {"content": "hi"})
        result = inject_seq_into_sse(sse, 42, "abc123")
        # parse the data line
        for line in result.splitlines():
            if line.startswith("data:"):
                data = json.loads(line.split(":", 1)[1].strip())
                assert data["seq"] == 42
                assert data["stream_id"] == "abc123"
                assert data["content"] == "hi"
                break
        else:
            pytest.fail("No data line found")

    def test_inject_seq_preserves_event_type(self):
        sse = sse_format("tool_call_start", {"tool_name": "test"})
        result = inject_seq_into_sse(sse, 1, "sid")
        assert result.startswith("event: tool_call_start\n")


# ── Subscribe endpoint tests ──


@pytest.mark.asyncio
async def test_subscribe_no_active_task_returns_done():
    sm = MagicMock()
    _setup_api_globals(sm)

    with patch.object(api_module, "_has_session_access", new_callable=AsyncMock, return_value=True):
        async with AsyncClient(transport=_make_transport(), base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/chat/subscribe",
                json={"session_id": "missing"},
            )
            assert resp.status_code == 200
            events = [name for name, _ in _parse_sse_events(resp.text)]
            assert "session_init" in events
            assert "subscribe_resume" in events
            assert "done" in events


@pytest.mark.asyncio
async def test_subscribe_completed_task_replays_buffered_with_seq():
    sm = MagicMock()
    _setup_api_globals(sm)

    session_id = "completed-session"
    stream_state = _SessionStreamState()
    api_module._session_stream_states[session_id] = stream_state
    stream_state.deliver(_make_event(EventType.TEXT_DELTA, text_delta="buffered"))

    done_task: asyncio.Future[ChatResult] = asyncio.get_running_loop().create_future()
    done_task.set_result(ChatResult(reply="done", iterations=1))
    api_module._active_chat_tasks[session_id] = done_task  # type: ignore[assignment]

    with patch.object(api_module, "_has_session_access", new_callable=AsyncMock, return_value=True), \
         patch.object(api_module, "_is_external_safe_mode", return_value=False):
        async with AsyncClient(transport=_make_transport(), base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/chat/subscribe",
                json={"session_id": session_id},
            )
            assert resp.status_code == 200
            parsed = _parse_sse_events(resp.text)
            names = [name for name, _ in parsed]
            assert "subscribe_resume" in names
            assert "text_delta" in names
            assert "done" in names

            resume = next(d for n, d in parsed if n == "subscribe_resume")
            assert resume.get("status") == "completed"
            assert resume.get("buffered_count", 0) >= 1
            assert "stream_id" in resume

            # Verify seq is injected into replayed events
            td = next(d for n, d in parsed if n == "text_delta")
            assert "seq" in td
            assert td["seq"] == 1

    assert session_id not in api_module._session_stream_states


@pytest.mark.asyncio
async def test_subscribe_after_seq_skips_already_received():
    sm = MagicMock()
    _setup_api_globals(sm)

    session_id = "after-seq-session"
    stream_state = _SessionStreamState()
    api_module._session_stream_states[session_id] = stream_state
    stream_state.deliver(_make_event(EventType.TEXT_DELTA, text_delta="first"))   # seq=1
    stream_state.deliver(_make_event(EventType.TEXT_DELTA, text_delta="second"))  # seq=2
    stream_state.deliver(_make_event(EventType.TEXT_DELTA, text_delta="third"))   # seq=3

    done_task: asyncio.Future[ChatResult] = asyncio.get_running_loop().create_future()
    done_task.set_result(ChatResult(reply="done", iterations=1))
    api_module._active_chat_tasks[session_id] = done_task  # type: ignore[assignment]

    with patch.object(api_module, "_has_session_access", new_callable=AsyncMock, return_value=True), \
         patch.object(api_module, "_is_external_safe_mode", return_value=False):
        async with AsyncClient(transport=_make_transport(), base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/chat/subscribe",
                json={"session_id": session_id, "after_seq": 1},
            )
            assert resp.status_code == 200
            parsed = _parse_sse_events(resp.text)
            td_events = [d for n, d in parsed if n == "text_delta"]
            # Should only have seq=2 and seq=3, not seq=1
            assert len(td_events) == 2
            assert td_events[0]["seq"] == 2
            assert td_events[1]["seq"] == 3


@pytest.mark.asyncio
async def test_subscribe_resume_failed_on_buffer_overflow():
    sm = MagicMock()
    _setup_api_globals(sm)

    session_id = "overflow-session"
    stream_state = _SessionStreamState(buffer_limit=2)
    api_module._session_stream_states[session_id] = stream_state
    # Deliver 4 events with buffer_limit=2 → 2 dropped
    for i in range(4):
        stream_state.deliver(_make_event(EventType.TEXT_DELTA, text_delta=str(i)))
    # Buffer now has seq=3,4; seq=1,2 were dropped

    done_task: asyncio.Future[ChatResult] = asyncio.get_running_loop().create_future()
    done_task.set_result(ChatResult(reply="done", iterations=1))
    api_module._active_chat_tasks[session_id] = done_task  # type: ignore[assignment]

    with patch.object(api_module, "_has_session_access", new_callable=AsyncMock, return_value=True), \
         patch.object(api_module, "_is_external_safe_mode", return_value=False):
        async with AsyncClient(transport=_make_transport(), base_url="http://test") as client:
            # Client says it last received seq=0 (expecting seq=1), but buffer starts at seq=3
            resp = await client.post(
                "/api/v1/chat/subscribe",
                json={"session_id": session_id, "after_seq": 0},
            )
            assert resp.status_code == 200
            parsed = _parse_sse_events(resp.text)
            names = [name for name, _ in parsed]
            # Should get resume_failed, not subscribe_resume
            assert "resume_failed" in names
            assert "subscribe_resume" not in names

            rf = next(d for n, d in parsed if n == "resume_failed")
            assert rf["reason"] == "buffer_overflow"


@pytest.mark.asyncio
async def test_subscribe_no_gap_when_after_seq_matches():
    """When after_seq matches exactly, no gap → normal resume."""
    sm = MagicMock()
    _setup_api_globals(sm)

    session_id = "no-gap-session"
    stream_state = _SessionStreamState(buffer_limit=3)
    api_module._session_stream_states[session_id] = stream_state
    stream_state.deliver(_make_event(EventType.TEXT_DELTA, text_delta="a"))  # seq=1
    stream_state.deliver(_make_event(EventType.TEXT_DELTA, text_delta="b"))  # seq=2
    stream_state.deliver(_make_event(EventType.TEXT_DELTA, text_delta="c"))  # seq=3

    done_task: asyncio.Future[ChatResult] = asyncio.get_running_loop().create_future()
    done_task.set_result(ChatResult(reply="done", iterations=1))
    api_module._active_chat_tasks[session_id] = done_task  # type: ignore[assignment]

    with patch.object(api_module, "_has_session_access", new_callable=AsyncMock, return_value=True), \
         patch.object(api_module, "_is_external_safe_mode", return_value=False):
        async with AsyncClient(transport=_make_transport(), base_url="http://test") as client:
            # Client received up to seq=2, expects seq=3 → first_buf=1, after_seq+1=3
            # But buffer starts at 1, so 1 <= 3 → no gap
            resp = await client.post(
                "/api/v1/chat/subscribe",
                json={"session_id": session_id, "after_seq": 2},
            )
            assert resp.status_code == 200
            parsed = _parse_sse_events(resp.text)
            names = [name for name, _ in parsed]
            assert "subscribe_resume" in names
            assert "resume_failed" not in names
            td_events = [d for n, d in parsed if n == "text_delta"]
            assert len(td_events) == 1
            assert td_events[0]["seq"] == 3


@pytest.mark.asyncio
async def test_subscribe_skip_replay_omits_buffered():
    sm = MagicMock()
    _setup_api_globals(sm)

    session_id = "skip-session"
    stream_state = _SessionStreamState()
    api_module._session_stream_states[session_id] = stream_state
    stream_state.deliver(_make_event(EventType.TEXT_DELTA, text_delta="should-skip"))

    done_task: asyncio.Future[ChatResult] = asyncio.get_running_loop().create_future()
    done_task.set_result(ChatResult(reply="done", iterations=1))
    api_module._active_chat_tasks[session_id] = done_task  # type: ignore[assignment]

    with patch.object(api_module, "_has_session_access", new_callable=AsyncMock, return_value=True), \
         patch.object(api_module, "_is_external_safe_mode", return_value=False):
        async with AsyncClient(transport=_make_transport(), base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/chat/subscribe",
                json={"session_id": session_id, "skip_replay": True},
            )
            assert resp.status_code == 200
            parsed = _parse_sse_events(resp.text)
            names = [name for name, _ in parsed]
            assert "text_delta" not in names


@pytest.mark.asyncio
async def test_subscribe_active_task_replays_then_streams():
    sm = MagicMock()
    _setup_api_globals(sm)

    session_id = "active-session"
    stream_state = _SessionStreamState()
    api_module._session_stream_states[session_id] = stream_state
    stream_state.deliver(_make_event(EventType.TEXT_DELTA, text_delta="buffered"))

    async def _fake_chat():
        await asyncio.sleep(0.05)
        return ChatResult(reply="done", iterations=1)

    chat_task = asyncio.create_task(_fake_chat())
    api_module._active_chat_tasks[session_id] = chat_task

    mock_engine = MagicMock()
    mock_engine.last_route_result = MagicMock(route_mode="write", skills_used=[], tool_scope=[])
    sm.get_engine.return_value = mock_engine

    with patch.object(api_module, "_has_session_access", new_callable=AsyncMock, return_value=True), \
         patch.object(api_module, "_is_external_safe_mode", return_value=False):
        async with AsyncClient(transport=_make_transport(), base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/chat/subscribe",
                json={"session_id": session_id},
            )
            assert resp.status_code == 200
            parsed = _parse_sse_events(resp.text)
            names = [name for name, _ in parsed]
            assert "subscribe_resume" in names
            assert "text_delta" in names
            assert "done" in names

            resume = next(d for n, d in parsed if n == "subscribe_resume")
            assert resume.get("status") == "reconnected"
            assert "stream_id" in resume

    if not chat_task.done():
        chat_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await chat_task


@pytest.mark.asyncio
async def test_subscribe_accepts_stream_id_and_after_seq_fields():
    """Verify _SubscribeRequest accepts new fields without 422."""
    sm = MagicMock()
    _setup_api_globals(sm)

    with patch.object(api_module, "_has_session_access", new_callable=AsyncMock, return_value=True):
        async with AsyncClient(transport=_make_transport(), base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/chat/subscribe",
                json={
                    "session_id": "test-session",
                    "stream_id": "abc123",
                    "after_seq": 5,
                },
            )
            # Should not be 422 (validation error)
            assert resp.status_code == 200
