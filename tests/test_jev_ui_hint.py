"""片 O：ui.surface / UI_HINT inert 接线。默认不发事件；禁止打网。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from excelmanus.api_sse import SessionStreamState, sse_event_to_sse
from excelmanus.config import ExcelManusConfig
from excelmanus.engine_types import ChatResult, ToolCallResult
from excelmanus.events import EventType, ToolCallEvent
from excelmanus.system_one.host import maybe_emit_ui_hint
from excelmanus.system_one.types import Decision


def _config(**overrides: object) -> ExcelManusConfig:
    return ExcelManusConfig(
        api_key="test-key",
        base_url="https://test.example.com/v1",
        model="test-model",
        max_iterations=8,
        max_consecutive_failures=3,
        workspace_root=str(Path(__file__).resolve().parent),
        ai_gateway_api_key="vck_test",
        **overrides,
    )


def _sign(monkeypatch: pytest.MonkeyPatch, *packs: str) -> None:
    monkeypatch.setattr(
        "excelmanus.system_one.calibration.SIGNED_ENFORCE_PACKS",
        frozenset(packs),
    )


def _ok_result(*, truncated: bool = False) -> ChatResult:
    return ChatResult(
        reply="done",
        tool_calls=[
            ToolCallResult(
                tool_name="apply_spreadsheet_changes",
                arguments={"file_path": "a.xlsx"},
                result="ok",
                success=True,
            ),
        ],
        truncated=truncated,
    )


def _stub(**overrides: object) -> SimpleNamespace:
    base: dict[str, object] = {
        "config": _config(),
        "_subagent_config": None,
        "_is_host_session": True,
        "_current_chat_mode": "write",
        "_question_flow": SimpleNamespace(has_pending=lambda: False),
        "has_pending_approval": lambda: False,
        "_state": SimpleNamespace(
            affected_files=["a.xlsx", "b.xlsx"],
            last_failure_count=0,
            last_success_count=1,
        ),
        "_current_surface": "unknown",
        "_turn_read_files": [],
        "memory": SimpleNamespace(get_messages=lambda: [{"role": "user", "content": "看看结果"}]),
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _hints(events: list[ToolCallEvent]) -> list[ToolCallEvent]:
    return [item for item in events if item.event_type == EventType.UI_HINT]


def _traces(events: list[ToolCallEvent]) -> list[ToolCallEvent]:
    return [item for item in events if item.event_type == EventType.JEV_TRACE]


def _surface_decision(
    *,
    surface: str = "side_panel",
    suppress: bool = False,
    applied: bool = True,
) -> Decision:
    return Decision(
        kind="noop",
        reason=f"surface:{surface}",
        extras={"surface": surface, "suppress_heuristic": suppress, "file_path": "./a.xlsx", "file_b": "./b.xlsx"},
        applied=applied,
    )


@pytest.mark.asyncio
async def test_ui_hint_off_does_not_evaluate_or_emit() -> None:
    # 二态契约默认全开；ui_hint 子闸关掉时不评估也不发事件。
    engine = _stub(config=_config(jev_ui_hint=False))
    captured: list[ToolCallEvent] = []
    with patch("excelmanus.system_one.evaluate", AsyncMock()) as mocked:
        await maybe_emit_ui_hint(engine, _ok_result(), on_event=captured.append)
        mocked.assert_not_called()
    assert captured == []


@pytest.mark.asyncio
async def test_unsigned_enforce_shadows_without_event() -> None:
    engine = _stub(
        config=_config(jev_enabled="enforce", jev_ui_hint=True),
    )
    captured: list[ToolCallEvent] = []
    with patch(
        "excelmanus.system_one.evaluate",
        AsyncMock(return_value=_surface_decision(applied=False)),
    ) as mocked:
        await maybe_emit_ui_hint(engine, _ok_result(), on_event=captured.append)
        mocked.assert_called_once()
        assert mocked.call_args.args[0] == "ui.surface"
    assert _hints(captured) == []
    assert _traces(captured)


@pytest.mark.asyncio
async def test_applied_decision_emits_hint_without_signoff() -> None:
    # 二态契约：enforce 下 applied 决策直接发 UI_HINT，不再需要签字。
    engine = _stub(
        config=_config(
            jev_enabled="enforce",
            jev_ui_hint=True,
            jev_calibrated=True,
        ),
    )
    captured: list[ToolCallEvent] = []
    with patch(
        "excelmanus.system_one.evaluate",
        AsyncMock(return_value=_surface_decision(applied=True)),
    ):
        await maybe_emit_ui_hint(engine, _ok_result(), on_event=captured.append)
    hints = _hints(captured)
    assert len(hints) == 1
    assert hints[0].ui_hint_surface == "side_panel"
    assert _traces(captured)


@pytest.mark.asyncio
async def test_signed_enforce_emits_hint_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _sign(monkeypatch, "ui.surface")
    engine = _stub(
        config=_config(
            jev_enabled="enforce",
            jev_ui_hint=True,
            jev_calibrated=True,
        ),
    )
    captured: list[ToolCallEvent] = []
    with patch(
        "excelmanus.system_one.evaluate",
        AsyncMock(return_value=_surface_decision(surface="side_panel")),
    ):
        await maybe_emit_ui_hint(engine, _ok_result(), on_event=captured.append)
    hints = _hints(captured)
    assert len(hints) == 1
    event = hints[0]
    assert event.event_type == EventType.UI_HINT
    assert event.ui_hint_surface == "side_panel"
    assert event.ui_hint_file_path == "./a.xlsx"
    assert event.ui_hint_suppress_auto_open is False
    assert _traces(captured)


@pytest.mark.asyncio
async def test_stay_high_confidence_sets_suppress_auto_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _sign(monkeypatch, "ui.surface")
    engine = _stub(
        config=_config(
            jev_enabled="enforce",
            jev_ui_hint=True,
            jev_calibrated=True,
        ),
    )
    captured: list[ToolCallEvent] = []
    with patch(
        "excelmanus.system_one.evaluate",
        AsyncMock(return_value=_surface_decision(surface="stay", suppress=True)),
    ):
        await maybe_emit_ui_hint(engine, _ok_result(), on_event=captured.append)
    hints = _hints(captured)
    assert hints[0].ui_hint_surface == "stay"
    assert hints[0].ui_hint_suppress_auto_open is True


@pytest.mark.asyncio
async def test_fail_turn_does_not_evaluate(monkeypatch: pytest.MonkeyPatch) -> None:
    _sign(monkeypatch, "ui.surface")
    engine = _stub(
        config=_config(
            jev_enabled="enforce",
            jev_ui_hint=True,
            jev_calibrated=True,
        ),
    )
    with patch("excelmanus.system_one.evaluate", AsyncMock()) as mocked:
        await maybe_emit_ui_hint(engine, _ok_result(truncated=True), on_event=lambda _e: None)
        mocked.assert_not_called()


@pytest.mark.asyncio
async def test_pending_interaction_does_not_evaluate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _sign(monkeypatch, "ui.surface")
    engine = _stub(
        config=_config(
            jev_enabled="enforce",
            jev_ui_hint=True,
            jev_calibrated=True,
        ),
        _question_flow=SimpleNamespace(has_pending=lambda: True),
    )
    with patch("excelmanus.system_one.evaluate", AsyncMock()) as mocked:
        await maybe_emit_ui_hint(engine, _ok_result(), on_event=lambda _e: None)
        mocked.assert_not_called()


@pytest.mark.asyncio
async def test_child_session_does_not_evaluate(monkeypatch: pytest.MonkeyPatch) -> None:
    _sign(monkeypatch, "ui.surface")
    engine = _stub(
        config=_config(
            jev_enabled="enforce",
            jev_ui_hint=True,
            jev_calibrated=True,
        ),
        _subagent_config={"name": "child"},
    )
    with patch("excelmanus.system_one.evaluate", AsyncMock()) as mocked:
        await maybe_emit_ui_hint(engine, _ok_result(), on_event=lambda _e: None)
        mocked.assert_not_called()


@pytest.mark.asyncio
async def test_emit_failure_is_silent(monkeypatch: pytest.MonkeyPatch) -> None:
    _sign(monkeypatch, "ui.surface")
    engine = _stub(
        config=_config(
            jev_enabled="enforce",
            jev_ui_hint=True,
            jev_calibrated=True,
        ),
    )

    def _boom(_event: ToolCallEvent) -> None:
        raise RuntimeError("queue closed")

    with patch(
        "excelmanus.system_one.evaluate",
        AsyncMock(return_value=_surface_decision()),
    ):
        await maybe_emit_ui_hint(engine, _ok_result(), on_event=_boom)


@pytest.mark.asyncio
async def test_slow_ui_hint_does_not_hold_completed_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _stub(
        config=_config(
            jev_enabled="enforce",
            jev_ui_hint=True,
            jev_calibrated=True,
        ),
    )
    captured: list[ToolCallEvent] = []

    async def slow(*_args, **_kwargs):
        await asyncio.sleep(1)

    monkeypatch.setattr("excelmanus.system_one.host._UI_HINT_MAX_WAIT_SECONDS", 0.01)
    with patch("excelmanus.system_one.host._eval_traced", slow):
        await maybe_emit_ui_hint(engine, _ok_result(), on_event=captured.append)
    assert _hints(captured) == []
    traces = _traces(captured)
    assert traces and traces[-1].jev_trace["reason"] == "timeout"


def test_ui_hint_not_buffered_for_replay() -> None:
    stream = SessionStreamState()
    seq = stream.deliver(
        ToolCallEvent(event_type=EventType.UI_HINT, ui_hint_surface="side_panel"),
    )
    assert seq == 1
    assert stream.event_buffer == []


def test_ui_hint_live_queue_still_delivers() -> None:
    stream = SessionStreamState()
    queue = stream.attach()
    event = ToolCallEvent(event_type=EventType.UI_HINT, ui_hint_surface="files_tab")
    stream.deliver(event)
    seq, got = queue.get_nowait()
    assert seq == 1
    assert got.event_type == EventType.UI_HINT
    assert stream.event_buffer == []


def test_sse_payload_is_flat_and_named_ui_hint() -> None:
    text = sse_event_to_sse(
        ToolCallEvent(
            event_type=EventType.UI_HINT,
            ui_hint_surface="side_panel",
            ui_hint_file_path="a.xlsx",
            ui_hint_sheet="明细",
            ui_hint_reason="surface:side_panel",
            ui_hint_suppress_auto_open=True,
        ),
    )
    assert text is not None
    assert "event: ui_hint" in text
    payload = json.loads(text.split("data: ", 1)[1].strip())
    assert payload == {
        "surface": "side_panel",
        "file_path": "a.xlsx",
        "sheet": "明细",
        "reason": "surface:side_panel",
        "suppress_auto_open": True,
    }
    assert "event_type" not in payload
