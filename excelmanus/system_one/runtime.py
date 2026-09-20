"""Host-facing Jev decision-plane runtime.

This is the narrow seam between Agent hooks and the transport/policy modules:
budgeting and evaluation failure semantics live here, while trace emission and
deterministic actuators stay outside.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from excelmanus.system_one.budget import turn_budget
from excelmanus.system_one.packs import get_pack
from excelmanus.system_one.types import Decision


async def evaluate_for_host(
    engine: Any,
    pack_id: str,
    state: Mapping[str, Any] | None,
    *,
    config: Any | None = None,
) -> Decision:
    """Evaluate one pack under the current turn budget.

    Security packs bypass the optimization budget; their transport/policy
    failure remains fail-closed in ``system_one.evaluate``.
    """
    from excelmanus.system_one import evaluate

    budget = turn_budget(engine)
    security = get_pack(pack_id).family == "security"
    if not budget.reserve(security=security):
        return Decision.noop(
            "budget_exhausted",
            transport="unavailable",
            budget=budget.snapshot(),
        )
    decision = await evaluate(pack_id, state, config=config)
    budget.record(getattr(decision.evaluation, "latency_ms", 0.0) if decision.evaluation else 0.0)
    return decision
