"""片 I：apply_claimed_followup 入口 shadow。off 不打；shadow 不改 wire。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from excelmanus.config import ExcelManusConfig
from excelmanus.engine import AgentEngine
from excelmanus.system_one.host import maybe_record_turn_exposure, turn_exposure_profile
from excelmanus.system_one.types import Decision
from excelmanus.tools.catalog import catalog_from_engine
from excelmanus.tools.registry import ToolRegistry


def _config(**overrides: object) -> ExcelManusConfig:
    return ExcelManusConfig(
        api_key="test-key",
        base_url="https://test.example.com/v1",
        model="test-model",
        max_iterations=8,
        max_consecutive_failures=3,
        workspace_root=str(Path(__file__).resolve().parent),
        ai_gateway_api_key="vck_test",
        jev_experimental_enabled=True,
        **overrides,
    )


def _make_engine(**overrides: object) -> AgentEngine:
    return AgentEngine(config=_config(**overrides), registry=ToolRegistry())


def _stub_engine(**overrides: object) -> SimpleNamespace:
    base: dict[str, object] = {
        "config": _config(jev_enabled="shadow", jev_exposure="shadow"),
        "_subagent_config": None,
        "_is_host_session": True,
        "_current_chat_mode": "write",
        "_pending_plan_exit": None,
        "_turn_image_count": 0,
        "_exposure_last_tools": [],
        "_active_skills": [],
        "_turn_exposure": None,
        "_exposure_sticky": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _text_response(content: str):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=None))],
    )


def _inspect_decision() -> Decision:
    return Decision.noop(
        "domain:inspect_only",
        profile="inspect",
        domain="inspect_only",
        domain_confidence=0.9,
        mode_hint="keep",
        wire_narrow=False,
    )


@pytest.mark.asyncio
async def test_missing_turn_exposure_is_full() -> None:
    engine = _stub_engine(_turn_exposure=None)
    assert turn_exposure_profile(engine) == "full"


@pytest.mark.asyncio
async def test_entry_off_does_not_evaluate() -> None:
    engine = _stub_engine(config=_config(jev_enabled="off", jev_exposure="enforce"))
    with patch("excelmanus.system_one.evaluate", AsyncMock()) as mocked:
        await maybe_record_turn_exposure(engine, "看看这张表")
        mocked.assert_not_called()
    assert engine._turn_exposure is None


@pytest.mark.asyncio
async def test_entry_shadow_records_without_narrowing() -> None:
    engine = _stub_engine(config=_config(jev_enabled="shadow", jev_exposure="enforce"))
    with patch("excelmanus.system_one.evaluate", AsyncMock(return_value=_inspect_decision())) as mocked:
        await maybe_record_turn_exposure(engine, "看看这张表")
        mocked.assert_called_once()
        assert mocked.call_args.args[0] == "exposure.turn"
        assert mocked.call_args.args[1]["user_text"] == "看看这张表"
    record = engine._turn_exposure
    assert record is not None
    assert record["profile"] == "inspect"
    assert record["domain"] == "inspect_only"
    assert record["mode_hint"] == "keep"
    assert record["conf"] == pytest.approx(0.9)
    assert "latency_ms" in record
    assert record["wire_narrow"] is False
    assert record["applied"] is False
    sticky = engine._exposure_sticky
    assert sticky is not None
    assert sticky["last_profile"] == "inspect"
    assert sticky["narrowed"] is False


@pytest.mark.asyncio
async def test_entry_skips_child_and_compose_child() -> None:
    child = _stub_engine(_subagent_config=object(), config=_config(jev_enabled="shadow", jev_exposure="shadow"))
    with patch("excelmanus.system_one.evaluate", AsyncMock()) as mocked:
        await maybe_record_turn_exposure(child, "继续分析")
        mocked.assert_not_called()
    assert child._turn_exposure is None

    role_child = _stub_engine(
        _subagent_config=None,
        _is_host_session=False,
        config=_config(jev_enabled="shadow", jev_exposure="shadow"),
    )
    with patch("excelmanus.system_one.evaluate", AsyncMock()) as mocked:
        await maybe_record_turn_exposure(role_child, "继续分析")
        mocked.assert_not_called()


@pytest.mark.asyncio
async def test_followup_control_command_skips_evaluate() -> None:
    engine = _make_engine(jev_enabled="shadow", jev_exposure="shadow")
    with patch("excelmanus.system_one.evaluate", AsyncMock()) as mocked:
        await engine.followup("/plan on")
        mocked.assert_not_called()
        await engine.followup("/plan off")
        mocked.assert_not_called()
    assert engine._turn_exposure is None


@pytest.mark.asyncio
async def test_followup_shadow_clears_exposure_and_keeps_catalog() -> None:
    engine = _make_engine(jev_enabled="shadow", jev_exposure="shadow")
    engine._client.chat.completions.create = AsyncMock(return_value=_text_response("ok"))
    before = catalog_from_engine(engine)
    digest = before.digest() if before is not None else None
    with patch("excelmanus.system_one.evaluate", AsyncMock(return_value=_inspect_decision())) as mocked:
        await engine.followup("看看这张表")
        mocked.assert_called()
        assert "exposure.turn" in [c.args[0] for c in mocked.call_args_list]
    assert engine._turn_exposure is None
    after = catalog_from_engine(engine)
    assert after is not None and after.digest() == digest
