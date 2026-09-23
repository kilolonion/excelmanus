from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from excelmanus.config import ExcelManusConfig
from excelmanus.system_one.budget import JevTurnBudget
from excelmanus.system_one.runtime import evaluate_for_host
from excelmanus.system_one.types import Decision


def _config() -> ExcelManusConfig:
    return ExcelManusConfig(
        api_key="test-key",
        base_url="https://test.example.com/v1",
        model="test-model",
        max_iterations=8,
        max_consecutive_failures=3,
        workspace_root=str(Path(__file__).resolve().parent),
        ai_gateway_api_key="vck_test",
        jev_enabled="enforce",
        jev_exposure="enforce",
        jev_observation="enforce",
        jev_verification="enforce",
        jev_recovery="enforce",
    )


def _engine(budget: JevTurnBudget) -> SimpleNamespace:
    return SimpleNamespace(
        config=_config(),
        _jev_turn_budget=budget,
        _mutation_verification=None,
        _driver=None,
    )


def test_optimization_budget_stops_after_limit() -> None:
    budget = JevTurnBudget(max_evaluations=2, max_latency_ms=10_000)
    assert budget.reserve() is True
    budget.record(10)
    assert budget.reserve() is True
    assert budget.reserve() is False
    assert budget.exhausted_reason == "evaluation_limit"


def test_security_reservation_survives_optimization_budget() -> None:
    budget = JevTurnBudget(max_evaluations=1, max_latency_ms=10_000)
    assert budget.reserve() is True
    assert budget.reserve() is False
    assert budget.reserve(security=True) is True


@pytest.mark.asyncio
async def test_loop_wrap_does_not_reserve_a_delivery_review_slot() -> None:
    """The removed delivery reviewer cannot consume an optimization slot."""
    engine = _engine(JevTurnBudget(max_evaluations=2, max_latency_ms=1000))
    with patch(
        "excelmanus.system_one.evaluate",
        AsyncMock(return_value=Decision.noop("ok")),
    ):
        config = engine.config
        first = await evaluate_for_host(engine, "loop.wrap", {"user_text": "x"}, config=config)
        assert first.reason != "budget_exhausted"
        second = await evaluate_for_host(engine, "loop.wrap", {"user_text": "x"}, config=config)
        assert second.reason != "budget_exhausted"
        third = await evaluate_for_host(engine, "loop.wrap", {"user_text": "x"}, config=config)
        assert third.reason == "budget_exhausted"


@pytest.mark.asyncio
async def test_loop_wrap_does_not_withhold_delivery_review_latency() -> None:
    """All available latency can serve an actual optimization call."""
    budget = JevTurnBudget(max_evaluations=4, max_latency_ms=600)
    engine = _engine(budget)
    captured: list[float] = []

    async def fake_evaluate(*_args: object, **_kwargs: object) -> Decision:
        captured.append(engine._jev_turn_budget.reserved_latency_ms)
        return Decision.noop("ok")

    with patch("excelmanus.system_one.evaluate", AsyncMock(side_effect=fake_evaluate)):
        decision = await evaluate_for_host(
            engine, "loop.wrap", {"user_text": "x"}, config=engine.config,
        )
    assert decision.reason != "budget_exhausted"
    assert captured == [600.0]
