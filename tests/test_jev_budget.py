from __future__ import annotations

from excelmanus.system_one.budget import JevTurnBudget


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
