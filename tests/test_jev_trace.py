"""Jev / System One 时间线：事件形状、shadow 必发、不回放。禁止打网。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from excelmanus.api_sse import SessionStreamState, sse_event_to_sse
from excelmanus.config import ExcelManusConfig
from excelmanus.events import TRANSIENT_SSE_TYPES, EventType, ToolCallEvent
from excelmanus.system_one.host import maybe_record_turn_exposure
from excelmanus.system_one.trace import build_jev_trace_payload, emit_jev_trace, public_transport
from excelmanus.system_one.types import ChoiceAnswer, Decision, Evaluation, NoulAnswer


def _config(**overrides: object) -> ExcelManusConfig:
    values: dict[str, object] = {
        "api_key": "test-key",
        "base_url": "https://test.example.com/v1",
        "model": "test-model",
        "max_iterations": 8,
        "max_consecutive_failures": 3,
        "workspace_root": str(Path(__file__).resolve().parent),
        "ai_gateway_api_key": "vck_test",
    }
    values.update(overrides)
    return ExcelManusConfig(**values)  # type: ignore[arg-type]


def _decision() -> Decision:
    evaluation = Evaluation(
        pack_id="exposure.turn",
        answers={
            "domain": ChoiceAnswer("chitchat", confidence=0.91),
            "needs_write": NoulAnswer(0.02),
            "mode_mismatch": ChoiceAnswer("keep", confidence=0.8),
        },
        model="jev-1.13.0",
        latency_ms=312.4,
    )
    return Decision(
        kind="noop",
        reason="domain:chitchat",
        evaluation=evaluation,
        extras={
            "profile": "minimal",
            "domain": "chitchat",
            "domain_confidence": 0.91,
            "mode_hint": "keep",
            "wire_narrow": False,
            "transport": "gateway",
        },
        applied=False,
    )


def test_public_transport_labels() -> None:
    assert public_transport(None) == "unavailable"
    assert public_transport("") == "unavailable"
    assert public_transport("vck_test_not_real") == "gateway"
    assert public_transport("custom_gateway_key", "gateway") == "gateway"


def test_payload_shape_is_bounded_and_has_impact() -> None:
    payload = build_jev_trace_payload(
        "exposure.turn",
        _decision(),
        gate="enforce",
        transport="gateway",
    )
    assert payload["pack"] == "exposure.turn"
    assert payload["gate"] == "enforce"  # 二态契约：gate 只有 off/enforce
    assert payload["applied"] is False
    assert payload["transport"] == "gateway"
    assert payload["latency_ms"] == pytest.approx(312.4)
    assert payload["action"] == "minimal"
    assert payload["answers"]["domain"] == "chitchat"
    assert payload["answers"]["needs_write"] == pytest.approx(0.02)
    assert payload["answers"]["mode_hint"] == "keep"
    assert payload["evaluated"] is True
    assert payload["stage"] == "evaluation"
    assert "后续执行记录" in payload["impact"]
    blob = str(payload)
    assert "user_text" not in blob
    assert "api_key" not in blob
    assert "vck_" not in blob
    assert "state" not in payload


def test_trace_keeps_only_public_calibration_and_transport_provenance() -> None:
    payload = build_jev_trace_payload(
        "exposure.turn",
        _decision(),
        gate="enforce",
        transport="gateway",
    )
    assert len(payload["calibration_fingerprint"]) == 64
    assert payload["model"] == "jev-1.13.0"
    assert "api_key" not in str(payload)


def test_unavailable_payload_still_names_pack() -> None:
    payload = build_jev_trace_payload(
        "exposure.turn",
        Decision.noop("unavailable", profile="full", transport="unavailable"),
        gate="enforce",
        transport="unavailable",
    )
    assert payload["pack"] == "exposure.turn"
    assert payload["transport"] == "unavailable"
    assert payload["applied"] is False
    assert "不可用" in payload["impact"]


def test_emit_sends_jev_trace_on_enforce() -> None:
    captured: list[ToolCallEvent] = []
    engine = SimpleNamespace(
        config=_config(jev_enabled="enforce", jev_exposure="enforce"),
        _subagent_config=None,
        _is_host_session=True,
        _driver=SimpleNamespace(_on_event=None),
        _emit=lambda on_event, event: captured.append(event) or (on_event and on_event(event)),
    )
    emit_jev_trace(engine, _decision(), pack_id="exposure.turn", on_event=captured.append)
    traces = [item for item in captured if item.event_type == EventType.JEV_TRACE]
    assert traces
    assert traces[0].jev_trace["pack"] == "exposure.turn"
    assert traces[0].jev_trace["gate"] == "enforce"


def test_emit_is_silent_when_jev_is_inactive() -> None:
    captured: list[ToolCallEvent] = []
    off = SimpleNamespace(
        config=_config(jev_enabled="off"),
        _subagent_config=None,
        _is_host_session=True,
        _emit=lambda on_event, event: captured.append(event),
    )
    emit_jev_trace(off, _decision(), pack_id="exposure.turn", on_event=captured.append)
    assert captured == []
    no_key = SimpleNamespace(
        config=_config(jev_enabled="shadow", ai_gateway_api_key=None, typesafe_api_key=None),
        _subagent_config=None,
        _is_host_session=True,
        _emit=lambda on_event, event: captured.append(event),
    )
    emit_jev_trace(no_key, _decision(), pack_id="exposure.turn", on_event=captured.append)
    assert captured
    assert captured[0].jev_trace["applied"] is False


def test_child_does_not_emit() -> None:
    captured: list[ToolCallEvent] = []
    child = SimpleNamespace(
        config=_config(jev_enabled="shadow", jev_exposure="shadow"),
        _subagent_config=object(),
        _is_host_session=True,
        _emit=lambda on_event, event: captured.append(event),
    )
    emit_jev_trace(child, _decision(), pack_id="exposure.turn", on_event=captured.append)
    assert captured == []


@pytest.mark.asyncio
async def test_entry_shadow_emits_even_when_unavailable() -> None:
    captured: list[ToolCallEvent] = []
    engine = SimpleNamespace(
        config=_config(jev_enabled="shadow", jev_exposure="shadow"),
        _subagent_config=None,
        _is_host_session=True,
        _current_chat_mode="write",
        _pending_plan_exit=None,
        _turn_image_count=0,
        _exposure_last_tools=[],
        _active_skills=[],
        _turn_exposure=None,
        _exposure_sticky=None,
        _emit=lambda on_event, event: captured.append(event),
        _driver=SimpleNamespace(_on_event=captured.append),
    )
    await maybe_record_turn_exposure(engine, "你好", on_event=captured.append)
    traces = [item for item in captured if getattr(item, "event_type", None) == EventType.JEV_TRACE]
    assert traces
    assert traces[0].jev_trace["pack"] == "exposure.turn"
    assert traces[0].jev_trace["applied"] is False
    assert traces[0].jev_trace["transport"] == "unavailable"


@pytest.mark.asyncio
async def test_entry_off_does_not_emit_or_evaluate() -> None:
    captured: list[ToolCallEvent] = []
    engine = SimpleNamespace(
        config=_config(jev_enabled="off", jev_exposure="enforce"),
        _subagent_config=None,
        _is_host_session=True,
        _emit=lambda on_event, event: captured.append(event),
        _driver=SimpleNamespace(_on_event=None),
        _turn_exposure=None,
        _exposure_sticky=None,
        _current_chat_mode="write",
    )
    with patch("excelmanus.system_one.evaluate", AsyncMock()) as mocked:
        await maybe_record_turn_exposure(engine, "你好", on_event=captured.append)
        mocked.assert_not_called()
    traces = [item for item in captured if item.event_type == EventType.JEV_TRACE]
    assert traces == []
    assert engine._turn_exposure is None


@pytest.mark.asyncio
async def test_entry_shadow_without_key_does_not_connect() -> None:
    captured: list[ToolCallEvent] = []
    engine = SimpleNamespace(
        config=_config(
            jev_enabled="shadow",
            jev_exposure="shadow",
            ai_gateway_api_key=None,
            typesafe_api_key=None,
        ),
        _subagent_config=None,
        _is_host_session=True,
        _emit=lambda on_event, event: captured.append(event),
        _driver=SimpleNamespace(_on_event=None),
        _turn_exposure=None,
        _exposure_sticky=None,
        _current_chat_mode="write",
    )
    with patch("excelmanus.system_one.evaluate", AsyncMock()) as mocked:
        await maybe_record_turn_exposure(engine, "你好", on_event=captured.append)
        mocked.assert_not_called()
    assert captured == []
    assert engine._turn_exposure is None


def test_jev_trace_not_buffered_for_replay() -> None:
    state = SessionStreamState()
    assert EventType.JEV_TRACE in TRANSIENT_SSE_TYPES
    state.deliver(
        ToolCallEvent(
            event_type=EventType.JEV_TRACE,
            jev_trace={"pack": "exposure.turn", "gate": "shadow"},
        ),
    )
    assert state.event_buffer == []


def test_jev_trace_live_queue_still_delivers() -> None:
    state = SessionStreamState()
    queue = state.attach()
    event = ToolCallEvent(
        event_type=EventType.JEV_TRACE,
        jev_trace={"pack": "loop.wrap", "action": "continue"},
    )
    state.deliver(event)
    got = queue.get_nowait()
    assert got is not None
    assert got[1].event_type == EventType.JEV_TRACE


def test_sse_payload_is_named_jev_trace_and_strips_secrets() -> None:
    text = sse_event_to_sse(
        ToolCallEvent(
            event_type=EventType.JEV_TRACE,
            jev_trace={
                "pack": "exposure.turn",
                "gate": "shadow",
                "applied": False,
                "transport": "gateway",
                "latency_ms": 12,
                "action": "minimal",
                "kind": "noop",
                "reason": "domain:chitchat",
                "answers": {"domain": "chitchat"},
                "impact": "仅观察，未改 wire/审批/UI",
                "api_key": "should-not-leak",
                "user_text": "secret prompt",
            },
        )
    )
    assert text is not None
    assert "event: jev_trace" in text
    assert "should-not-leak" not in text
    assert "secret prompt" not in text
    assert "exposure.turn" in text
    assert "仅观察" in text
