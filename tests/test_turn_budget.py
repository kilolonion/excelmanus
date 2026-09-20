from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from excelmanus.agent.budget import TurnBudget, TurnBudgetExceeded
from excelmanus.agent.session import AgentEngine
from excelmanus.config import ExcelManusConfig
from excelmanus.tools.registry import ToolRegistry


def test_budget_accounts_provider_usage_and_estimated_cost() -> None:
    budget = TurnBudget(max_tokens=100, max_cost_usd=0.01, input_cost_per_1k_usd=0.02)
    budget.record_usage({"prompt_tokens": 400, "completion_tokens": 100})

    assert budget.total_tokens == 500
    assert budget.cost_usd == pytest.approx(0.008)
    assert budget.exhausted_reason == "tokens"
    assert budget.snapshot()["total_tokens"] == 500


def test_explicit_provider_cost_wins_over_estimate() -> None:
    budget = TurnBudget(max_cost_usd=0.5, input_cost_per_1k_usd=99)
    budget.record_usage(SimpleNamespace(prompt_tokens=1_000, completion_tokens=0, cost_usd=0.2))

    assert budget.cost_usd == pytest.approx(0.2)
    assert budget.exhausted_reason == ""


def test_wall_clock_budget_is_shared_and_fails_closed() -> None:
    budget = TurnBudget(deadline_mono=time.monotonic() - 0.01)

    with pytest.raises(TurnBudgetExceeded) as exc:
        budget.reserve_tool()

    assert exc.value.kind == "wall_clock"
    assert budget.exhausted_reason == "wall_clock"


@pytest.mark.asyncio
async def test_agent_stops_before_processing_tools_when_token_budget_is_hit(tmp_path) -> None:
    engine = AgentEngine(
        ExcelManusConfig(
            api_key="test-key",
            base_url="https://test.example/v1",
            model="test-model",
            workspace_root=str(tmp_path),
            turn_token_budget=10,
        ),
        ToolRegistry(),
    )
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="partial", tool_calls=None))],
        usage=SimpleNamespace(prompt_tokens=7, completion_tokens=4),
    )
    engine._client.chat.completions.create = AsyncMock(return_value=response)

    result = await engine.followup("预算测试")

    assert result.truncated is True
    assert "token" in result.reply
    assert engine._driver.current_turn()["status"] == "truncated"
