from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from excelmanus.config import ExcelManusConfig
from excelmanus.events import EventType
from excelmanus.system_one import evaluate
from excelmanus.system_one.policy import gate_for_pack, jev_is_active, settings_from
from excelmanus.system_one.runtime import evaluate_for_host
from excelmanus.system_one.trace import emit_jev_trace
from excelmanus.system_one.types import Decision


def _config(*, experimental: bool) -> ExcelManusConfig:
    return ExcelManusConfig(
        api_key="test-key",
        base_url="https://test.example.com/v1",
        model="test-model",
        workspace_root=str(Path(__file__).resolve().parent),
        ai_gateway_api_key="vck_test",
        jev_experimental_enabled=experimental,
        jev_enabled="enforce",
        jev_exposure="enforce",
        jev_observation="enforce",
        jev_verification="enforce",
        jev_recovery="enforce",
    )


def test_experimental_gate_is_outermost_for_all_packs() -> None:
    settings = settings_from(_config(experimental=False))
    assert settings.experimental_enabled is False
    assert jev_is_active(settings) is False
    for pack_id in ("context.resolve", "exposure.turn", "observation.shape", "approval.tool_call"):
        assert gate_for_pack(pack_id, settings) == "off"


def test_persisted_runtime_switch_also_blocks_jev() -> None:
    from excelmanus.settings_runtime import using_values

    with using_values({
        "EXCELMANUS_JEV_EXPERIMENTAL_ENABLED": "false",
        "EXCELMANUS_JEV_ENABLED": "enforce",
        "EXCELMANUS_AI_GATEWAY_API_KEY": "vck_test",
    }):
        settings = settings_from(None)
    assert settings.experimental_enabled is False
    assert jev_is_active(settings) is False
    assert gate_for_pack("exposure.turn", settings) == "off"


@pytest.mark.asyncio
async def test_disabled_experimental_feature_skips_remote_evaluation_and_logging() -> None:
    engine = SimpleNamespace(
        config=_config(experimental=False),
        _jev_turn_budget=None,
        _driver=None,
    )
    with patch("excelmanus.system_one.evaluate", AsyncMock()) as remote, \
         patch("excelmanus.system_one.record_jev_decision") as record:
        decision = await evaluate_for_host(engine, "exposure.turn", {"user_text": "x"}, config=engine.config)
        assert decision.reason == "disabled"
        remote.assert_not_awaited()
        record.assert_not_called()

    direct = await evaluate("exposure.turn", {"user_text": "x"}, config=_config(experimental=False))
    assert direct.reason == "disabled"


def test_disabled_experimental_feature_emits_no_trace() -> None:
    captured = []
    engine = SimpleNamespace(
        config=_config(experimental=False),
        _subagent_config=None,
        _is_host_session=True,
        _driver=SimpleNamespace(_on_event=None),
        _emit=lambda on_event, event: captured.append(event),
    )
    emit_jev_trace(engine, Decision.noop("disabled"), pack_id="exposure.turn", on_event=captured.append)
    assert [item for item in captured if item.event_type == EventType.JEV_TRACE] == []
