"""Host-facing Jev decision-plane runtime.

This is the narrow seam between Agent hooks and the transport/policy modules:
budgeting and evaluation failure semantics live here, while trace emission and
deterministic actuators stay outside.
"""

from __future__ import annotations

from collections.abc import Mapping
import asyncio
import time
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
    from excelmanus.system_one.policy import gate_for_pack, jev_is_active, settings_from

    settings = settings_from(config)
    if not jev_is_active(settings) or gate_for_pack(pack_id, settings) == "off":
        return Decision.noop("disabled")

    budget = turn_budget(engine)
    security = get_pack(pack_id).family == "security"
    # Reservations are synchronous so parallel tools cannot spend the same
    # remaining time. No budget is withheld for an automatic delivery review.
    timeout = max(0.001, settings.timeout_seconds)
    driver = getattr(engine, "_driver", None)
    remaining = getattr(driver, "remaining_turn_seconds", lambda: None)()
    if isinstance(remaining, (int, float)):
        if remaining <= 0:
            return Decision.ask("turn_deadline") if security else Decision.noop("turn_deadline")
        timeout = min(timeout, remaining)
    reserved_ms = 0.0 if security else min(timeout * 1000, budget.available_latency_ms())
    if not budget.reserve(
        security=security, latency_ms=reserved_ms,
    ):
        return Decision.noop(
            "budget_exhausted",
            transport="unavailable",
            budget=budget.snapshot(),
        )
    started = time.monotonic()
    try:
        async with asyncio.timeout(timeout if security else reserved_ms / 1000):
            return await evaluate(pack_id, state, config=config)
    except TimeoutError:
        return Decision.ask("timeout") if security else Decision.noop("timeout", transport="unavailable")
    finally:
        # Timeouts, cancellation and failed requests consume real latency too.
        budget.record((time.monotonic() - started) * 1000.0, reserved_ms=reserved_ms, security=security)
