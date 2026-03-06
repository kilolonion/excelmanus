"""End-to-end integration tests for SSE seq mechanism.

Validates the full pipeline: chat_stream → stream_init → seq events →
client disconnect → event buffering → subscribe(after_seq) → replay.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Callable
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from excelmanus.engine import ChatResult
from excelmanus.events import EventType, ToolCallEvent

import excelmanus.api as api_module
from excelmanus.api import _SessionStreamState, app


# ── helpers ──


def _make_transport() -> ASGITransport:
    return ASGITransport(app=app, raise_app_exceptions=False)


def _parse_sse_events(raw: str) -> list[tuple[str, dict]]:
    """Parse SSE text into a list of (event_name, data_dict) tuples."""
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


def _setup_api_globals(session_manager: Any) -> None:
    api_module._session_manager = session_manager
    api_module._config = MagicMock()
    api_module._config.workspace_root = "/tmp/test"
    api_module._config_incomplete = False
    api_module._active_chat_tasks = {}
    api_module._session_stream_states = {}


def _cleanup_api_globals() -> None:
    api_module._session_manager = None
    api_module._config = None
    api_module._active_chat_tasks = {}
    api_module._session_stream_states = {}


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    _cleanup_api_globals()


def _make_mock_engine(
    on_event_emitter: Callable | None = None,
    chat_result: ChatResult | None = None,
) -> MagicMock:
    """Create a mock engine whose chat() calls on_event with deterministic events."""
    engine = MagicMock()
    engine.session_turn = 2
    engine.active_base_url = "http://test"
    engine.current_model = "test-model"
    engine._pool_account_id = None
    engine.last_route_result = MagicMock(
        route_mode="write", skills_used=[], tool_scope=[],
    )

    _result = chat_result or ChatResult(
        reply="done",
        iterations=1,
        prompt_tokens=10,
        completion_tokens=5,
        total_tokens=15,
    )

    async def _fake_chat(message, on_event=None, **kwargs):
        if on_event_emitter is not None and on_event is not None:
            await on_event_emitter(on_event)
        return _result

    engine.chat = AsyncMock(side_effect=_fake_chat)
    return engine


def _make_session_manager(engine: MagicMock, session_id: str = "e2e-session") -> MagicMock:
    sm = MagicMock()
    sm.acquire_for_chat = AsyncMock(return_value=(session_id, engine))
    sm.release_for_chat = AsyncMock()
    sm.flush_messages_sync = MagicMock()
    sm.get_engine = MagicMock(return_value=engine)
    return sm


# ── E2E Tests ──


class TestChatStreamEmitsSeqEvents:
    """Verify chat_stream emits stream_init and seq-annotated events."""

    @pytest.mark.asyncio
    async def test_stream_init_contains_stream_id_and_seq(self):
        """chat_stream should emit stream_init with stream_id and seq=1."""

        async def emit_one(on_event):
            on_event(ToolCallEvent(event_type=EventType.TEXT_DELTA, text_delta="hi"))

        engine = _make_mock_engine(on_event_emitter=emit_one)
        sm = _make_session_manager(engine)
        _setup_api_globals(sm)

        with patch.object(api_module, "_is_external_safe_mode", return_value=False), \
             patch.object(api_module, "_resolve_mentions", new_callable=AsyncMock, return_value=("hi", [])), \
             patch.object(api_module, "_config_incomplete", False):
            async with AsyncClient(transport=_make_transport(), base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/chat/stream",
                    json={"message": "hello"},
                )
                assert resp.status_code == 200
                parsed = _parse_sse_events(resp.text)
                names = [n for n, _ in parsed]
                assert "stream_init" in names

                si = next(d for n, d in parsed if n == "stream_init")
                assert "stream_id" in si
                assert si["seq"] == 1
                assert isinstance(si["stream_id"], str)
                assert len(si["stream_id"]) == 12

    @pytest.mark.asyncio
    async def test_text_delta_events_carry_incrementing_seq(self):
        """Each text_delta should have monotonically increasing seq + stream_id."""

        async def emit_three(on_event):
            on_event(ToolCallEvent(event_type=EventType.TEXT_DELTA, text_delta="a"))
            on_event(ToolCallEvent(event_type=EventType.TEXT_DELTA, text_delta="b"))
            on_event(ToolCallEvent(event_type=EventType.TEXT_DELTA, text_delta="c"))

        engine = _make_mock_engine(on_event_emitter=emit_three)
        sm = _make_session_manager(engine)
        _setup_api_globals(sm)

        with patch.object(api_module, "_is_external_safe_mode", return_value=False), \
             patch.object(api_module, "_resolve_mentions", new_callable=AsyncMock, return_value=("hi", [])), \
             patch.object(api_module, "_config_incomplete", False):
            async with AsyncClient(transport=_make_transport(), base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/chat/stream",
                    json={"message": "hello"},
                )
                parsed = _parse_sse_events(resp.text)
                td_events = [d for n, d in parsed if n == "text_delta"]

                assert len(td_events) == 3
                seqs = [d["seq"] for d in td_events]
                assert seqs == [1, 2, 3]

                # All should share the same stream_id
                stream_ids = {d["stream_id"] for d in td_events}
                assert len(stream_ids) == 1

                # stream_id should match the one from stream_init
                si = next(d for n, d in parsed if n == "stream_init")
                assert stream_ids.pop() == si["stream_id"]


class TestDisconnectBufferSubscribe:
    """Test the disconnect → buffer → subscribe(after_seq) → replay pipeline."""

    @pytest.mark.asyncio
    async def test_disconnect_buffers_then_subscribe_replays(self):
        """After client disconnect, events buffer. subscribe(after_seq) replays missed events.

        Simulates the scenario by directly populating stream_state's buffer
        (as if events were delivered while client was disconnected) then calling
        subscribe with after_seq to verify only missed events are replayed.
        """
        session_id = "e2e-disconnect"

        # Set up the state as if client disconnected after receiving seq=2:
        # - 5 events were delivered total
        # - Client received seq 1,2 before disconnecting
        # - Events 3,4,5 went to buffer
        stream_state = _SessionStreamState()
        stream_state.deliver(ToolCallEvent(event_type=EventType.TEXT_DELTA, text_delta="first"))
        stream_state.deliver(ToolCallEvent(event_type=EventType.TEXT_DELTA, text_delta="second"))
        stream_state.deliver(ToolCallEvent(event_type=EventType.TEXT_DELTA, text_delta="third"))
        stream_state.deliver(ToolCallEvent(event_type=EventType.TEXT_DELTA, text_delta="fourth"))
        stream_state.deliver(ToolCallEvent(event_type=EventType.TEXT_DELTA, text_delta="fifth"))

        # Mark chat task as completed
        done_future: asyncio.Future[ChatResult] = asyncio.get_running_loop().create_future()
        done_future.set_result(ChatResult(reply="done", iterations=1))

        sm = MagicMock()
        _setup_api_globals(sm)
        api_module._session_stream_states[session_id] = stream_state
        api_module._active_chat_tasks[session_id] = done_future  # type: ignore[assignment]

        # Subscribe with after_seq=2 → should get only seq 3,4,5
        with patch.object(api_module, "_has_session_access", new_callable=AsyncMock, return_value=True), \
             patch.object(api_module, "_is_external_safe_mode", return_value=False):
            async with AsyncClient(transport=_make_transport(), base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/chat/subscribe",
                    json={"session_id": session_id, "after_seq": 2},
                )
                assert resp.status_code == 200
                parsed = _parse_sse_events(resp.text)
                names = [n for n, _ in parsed]

                # Should have subscribe_resume, not resume_failed
                assert "subscribe_resume" in names
                assert "resume_failed" not in names

                td_events = [d for n, d in parsed if n == "text_delta"]
                assert len(td_events) == 3
                assert td_events[0]["content"] == "third"
                assert td_events[1]["content"] == "fourth"
                assert td_events[2]["content"] == "fifth"
                assert td_events[0]["seq"] == 3
                assert td_events[1]["seq"] == 4
                assert td_events[2]["seq"] == 5

    @pytest.mark.asyncio
    async def test_subscribe_content_order_matches_original(self):
        """Replayed events maintain the exact same content order as original emission."""
        session_id = "e2e-order"
        contents = ["alpha", "beta", "gamma", "delta", "epsilon"]

        stream_state = _SessionStreamState()
        api_module._session_stream_states[session_id] = stream_state
        for c in contents:
            stream_state.deliver(ToolCallEvent(event_type=EventType.TEXT_DELTA, text_delta=c))

        done_future: asyncio.Future[ChatResult] = asyncio.get_running_loop().create_future()
        done_future.set_result(ChatResult(reply="done", iterations=1))
        api_module._active_chat_tasks[session_id] = done_future  # type: ignore[assignment]

        sm = MagicMock()
        _setup_api_globals(sm)
        api_module._session_stream_states[session_id] = stream_state
        api_module._active_chat_tasks[session_id] = done_future  # type: ignore[assignment]

        with patch.object(api_module, "_has_session_access", new_callable=AsyncMock, return_value=True), \
             patch.object(api_module, "_is_external_safe_mode", return_value=False):
            async with AsyncClient(transport=_make_transport(), base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/chat/subscribe",
                    json={"session_id": session_id, "after_seq": 0},
                )
                parsed = _parse_sse_events(resp.text)
                td_events = [d for n, d in parsed if n == "text_delta"]

                replayed_contents = [d["content"] for d in td_events]
                assert replayed_contents == contents

                # Verify seq is strictly increasing
                replayed_seqs = [d["seq"] for d in td_events]
                assert replayed_seqs == list(range(1, len(contents) + 1))


class TestBufferOverflowResumeFailed:
    """Test that buffer overflow triggers resume_failed on subscribe."""

    @pytest.mark.asyncio
    async def test_small_buffer_overflow_sends_resume_failed(self):
        """With buffer_limit=3, delivering 6 events causes overflow → resume_failed."""
        session_id = "e2e-overflow"

        stream_state = _SessionStreamState(buffer_limit=3)
        # Deliver 6 events → first 3 dropped, buffer has seq 4,5,6
        for i in range(6):
            stream_state.deliver(
                ToolCallEvent(event_type=EventType.TEXT_DELTA, text_delta=f"msg-{i}")
            )

        assert stream_state.has_dropped
        assert len(stream_state.event_buffer) == 3

        done_future: asyncio.Future[ChatResult] = asyncio.get_running_loop().create_future()
        done_future.set_result(ChatResult(reply="done", iterations=1))

        sm = MagicMock()
        _setup_api_globals(sm)
        api_module._session_stream_states[session_id] = stream_state
        api_module._active_chat_tasks[session_id] = done_future  # type: ignore[assignment]

        with patch.object(api_module, "_has_session_access", new_callable=AsyncMock, return_value=True), \
             patch.object(api_module, "_is_external_safe_mode", return_value=False):
            async with AsyncClient(transport=_make_transport(), base_url="http://test") as client:
                # Client says after_seq=0 (expects from seq=1), but buffer starts at seq=4
                resp = await client.post(
                    "/api/v1/chat/subscribe",
                    json={"session_id": session_id, "after_seq": 0},
                )
                assert resp.status_code == 200
                parsed = _parse_sse_events(resp.text)
                names = [n for n, _ in parsed]

                assert "resume_failed" in names
                assert "subscribe_resume" not in names

                rf = next(d for n, d in parsed if n == "resume_failed")
                assert rf["reason"] == "buffer_overflow"
                assert rf["stream_id"] == stream_state.stream_id
                assert rf["after_seq"] == 0
                assert rf["available_from_seq"] == 4

    @pytest.mark.asyncio
    async def test_zero_buffer_overflow_sends_resume_failed(self):
        """buffer_limit=0 means all events are dropped → resume_failed."""
        session_id = "e2e-zero-buf"

        stream_state = _SessionStreamState(buffer_limit=0)
        stream_state.deliver(
            ToolCallEvent(event_type=EventType.TEXT_DELTA, text_delta="dropped")
        )

        assert stream_state.has_dropped
        assert len(stream_state.event_buffer) == 0

        done_future: asyncio.Future[ChatResult] = asyncio.get_running_loop().create_future()
        done_future.set_result(ChatResult(reply="done", iterations=1))

        sm = MagicMock()
        _setup_api_globals(sm)
        api_module._session_stream_states[session_id] = stream_state
        api_module._active_chat_tasks[session_id] = done_future  # type: ignore[assignment]

        with patch.object(api_module, "_has_session_access", new_callable=AsyncMock, return_value=True), \
             patch.object(api_module, "_is_external_safe_mode", return_value=False):
            async with AsyncClient(transport=_make_transport(), base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/chat/subscribe",
                    json={"session_id": session_id, "after_seq": 0},
                )
                parsed = _parse_sse_events(resp.text)
                names = [n for n, _ in parsed]

                assert "resume_failed" in names
                rf = next(d for n, d in parsed if n == "resume_failed")
                assert rf["reason"] == "buffer_overflow"
                assert rf["available_from_seq"] is None


class TestSubscribeActiveTaskStreamsRealtime:
    """Test subscribe during active chat task: replay buffer + receive realtime events."""

    @pytest.mark.asyncio
    async def test_subscribe_replays_buffer_and_receives_realtime(self):
        """When chat is still running, subscribe replays buffer then streams new events."""
        session_id = "e2e-active"

        stream_state = _SessionStreamState()
        # Pre-buffer 2 events (simulating events emitted while client was disconnected)
        stream_state.deliver(ToolCallEvent(event_type=EventType.TEXT_DELTA, text_delta="buffered-1"))
        stream_state.deliver(ToolCallEvent(event_type=EventType.TEXT_DELTA, text_delta="buffered-2"))

        # Create a chat task that will emit one more event after a short delay
        # then complete, allowing subscribe to finish cleanly.
        async def _fake_chat_task():
            # Small delay to let subscribe attach and start reading
            await asyncio.sleep(0.15)
            # Emit one more event through stream_state (goes to subscriber queue)
            stream_state.deliver(ToolCallEvent(event_type=EventType.TEXT_DELTA, text_delta="realtime-3"))
            await asyncio.sleep(0.05)
            return ChatResult(reply="done", iterations=1)

        chat_task = asyncio.create_task(_fake_chat_task())

        sm = MagicMock()
        engine = MagicMock()
        engine.last_route_result = MagicMock(route_mode="write", skills_used=[], tool_scope=[])
        engine.session_turn = 2
        sm.get_engine.return_value = engine
        _setup_api_globals(sm)
        api_module._session_stream_states[session_id] = stream_state
        api_module._active_chat_tasks[session_id] = chat_task

        with patch.object(api_module, "_has_session_access", new_callable=AsyncMock, return_value=True), \
             patch.object(api_module, "_is_external_safe_mode", return_value=False):
            async with AsyncClient(transport=_make_transport(), base_url="http://test", timeout=10.0) as client:
                resp = await client.post(
                    "/api/v1/chat/subscribe",
                    json={"session_id": session_id, "after_seq": 0},
                )
                assert resp.status_code == 200
                parsed = _parse_sse_events(resp.text)
                names = [n for n, _ in parsed]

                assert "subscribe_resume" in names
                resume = next(d for n, d in parsed if n == "subscribe_resume")
                assert resume["status"] == "reconnected"
                assert resume["buffered_count"] == 2

                td_events = [d for n, d in parsed if n == "text_delta"]
                # Should have all 3: 2 buffered + 1 realtime
                assert len(td_events) == 3
                assert td_events[0]["content"] == "buffered-1"
                assert td_events[0]["seq"] == 1
                assert td_events[1]["content"] == "buffered-2"
                assert td_events[1]["seq"] == 2
                assert td_events[2]["content"] == "realtime-3"
                assert td_events[2]["seq"] == 3

        if not chat_task.done():
            chat_task.cancel()
            try:
                await chat_task
            except asyncio.CancelledError:
                pass


class TestMixedEventTypes:
    """Test that seq mechanism works correctly with mixed event types."""

    @pytest.mark.asyncio
    async def test_mixed_events_all_carry_seq(self):
        """Different event types (text_delta, tool_call_start/end) all get seq injected."""

        async def emit_mixed(on_event):
            on_event(ToolCallEvent(
                event_type=EventType.TOOL_CALL_START,
                tool_call_id="tc1",
                tool_name="read_excel",
                arguments={"file_path": "test.xlsx"},
                iteration=1,
            ))
            on_event(ToolCallEvent(
                event_type=EventType.TOOL_CALL_END,
                tool_call_id="tc1",
                tool_name="read_excel",
                success=True,
                result="OK",
                iteration=1,
            ))
            on_event(ToolCallEvent(
                event_type=EventType.TEXT_DELTA,
                text_delta="result text",
            ))

        engine = _make_mock_engine(on_event_emitter=emit_mixed)
        sm = _make_session_manager(engine)
        _setup_api_globals(sm)

        with patch.object(api_module, "_is_external_safe_mode", return_value=False), \
             patch.object(api_module, "_resolve_mentions", new_callable=AsyncMock, return_value=("hi", [])), \
             patch.object(api_module, "_config_incomplete", False):
            async with AsyncClient(transport=_make_transport(), base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/chat/stream",
                    json={"message": "hello"},
                )
                parsed = _parse_sse_events(resp.text)

                # Collect engine events that carry seq (exclude stream_init which also has "seq")
                seq_events = [
                    (n, d) for n, d in parsed
                    if "seq" in d and n != "stream_init"
                ]

                assert len(seq_events) == 3
                seqs = [d["seq"] for _, d in seq_events]
                assert seqs == [1, 2, 3]

                # Verify event types
                event_names = [n for n, _ in seq_events]
                assert event_names == ["tool_call_start", "tool_call_end", "text_delta"]

    @pytest.mark.asyncio
    async def test_subscribe_replays_mixed_types_preserving_order(self):
        """Buffer replay preserves order across different event types."""
        session_id = "e2e-mixed"

        stream_state = _SessionStreamState()
        stream_state.deliver(ToolCallEvent(
            event_type=EventType.TOOL_CALL_START,
            tool_call_id="tc1", tool_name="write_cell",
            arguments={}, iteration=1,
        ))
        stream_state.deliver(ToolCallEvent(
            event_type=EventType.TOOL_CALL_END,
            tool_call_id="tc1", tool_name="write_cell",
            success=True, result="done", iteration=1,
        ))
        stream_state.deliver(ToolCallEvent(
            event_type=EventType.TEXT_DELTA, text_delta="summary",
        ))

        done_future: asyncio.Future[ChatResult] = asyncio.get_running_loop().create_future()
        done_future.set_result(ChatResult(reply="done", iterations=1))

        sm = MagicMock()
        _setup_api_globals(sm)
        api_module._session_stream_states[session_id] = stream_state
        api_module._active_chat_tasks[session_id] = done_future  # type: ignore[assignment]

        with patch.object(api_module, "_has_session_access", new_callable=AsyncMock, return_value=True), \
             patch.object(api_module, "_is_external_safe_mode", return_value=False):
            async with AsyncClient(transport=_make_transport(), base_url="http://test") as client:
                # Subscribe from seq=0 → replay all 3
                resp = await client.post(
                    "/api/v1/chat/subscribe",
                    json={"session_id": session_id, "after_seq": 0},
                )
                parsed = _parse_sse_events(resp.text)
                seq_events = [(n, d) for n, d in parsed if "seq" in d]

                assert len(seq_events) == 3
                assert seq_events[0][0] == "tool_call_start"
                assert seq_events[0][1]["seq"] == 1
                assert seq_events[1][0] == "tool_call_end"
                assert seq_events[1][1]["seq"] == 2
                assert seq_events[2][0] == "text_delta"
                assert seq_events[2][1]["seq"] == 3
                assert seq_events[2][1]["content"] == "summary"

                # Subscribe from seq=1 → only seq 2,3
                # Need to re-setup since stream_state was drained
                stream_state2 = _SessionStreamState()
                api_module._session_stream_states[session_id] = stream_state2
                stream_state2.deliver(ToolCallEvent(
                    event_type=EventType.TOOL_CALL_START,
                    tool_call_id="tc1", tool_name="write_cell",
                    arguments={}, iteration=1,
                ))
                stream_state2.deliver(ToolCallEvent(
                    event_type=EventType.TOOL_CALL_END,
                    tool_call_id="tc1", tool_name="write_cell",
                    success=True, result="done", iteration=1,
                ))
                stream_state2.deliver(ToolCallEvent(
                    event_type=EventType.TEXT_DELTA, text_delta="summary",
                ))
                done_future2: asyncio.Future[ChatResult] = asyncio.get_running_loop().create_future()
                done_future2.set_result(ChatResult(reply="done", iterations=1))
                api_module._active_chat_tasks[session_id] = done_future2  # type: ignore[assignment]

                resp2 = await client.post(
                    "/api/v1/chat/subscribe",
                    json={"session_id": session_id, "after_seq": 1},
                )
                parsed2 = _parse_sse_events(resp2.text)
                seq_events2 = [(n, d) for n, d in parsed2 if "seq" in d]
                assert len(seq_events2) == 2
                assert seq_events2[0][1]["seq"] == 2
                assert seq_events2[1][1]["seq"] == 3
