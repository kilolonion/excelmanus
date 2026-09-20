"""Jev / TypeSafe System One 对外薄 API。

片 I / C 入口与审批 shadow。片 J/K/L/N/O/F/M/P/E 已接线；G 保留为显式
calibration hook，但主循环不再自动调用，因为当前没有消费者。片 D 未签字：
即使 ENABLED=enforce 也不得 applied，不改 wire / UI / 偏好 / model_text / 目录 / 循环 / 冷修剪 / 审批结果。
context.resolve 是独立的纯建议题包；enforce 可向主模型补充上下文建议，不调用执行器。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from excelmanus.system_one.adapter import bound_state
from excelmanus.system_one.client import SystemOneUnavailable, client_ready, system_one
from excelmanus.system_one.log import record_shadow
from excelmanus.system_one.packs import get_pack
from excelmanus.system_one.policy import gate_for_pack, settings_from, stamp_application, synthesize
from excelmanus.system_one.trace import public_transport
from excelmanus.system_one.types import Decision

__all__ = ["Decision", "evaluate"]


def _with_transport(decision: Decision, transport: str) -> Decision:
    extras = dict(decision.extras or {})
    extras["transport"] = transport
    return Decision(
        kind=decision.kind,
        reason=decision.reason,
        evaluation=decision.evaluation,
        extras=extras,
        applied=decision.applied,
    )


def _with_provenance(decision: Decision, settings: Any) -> Decision:
    extras = dict(decision.extras or {})
    extras.update({"provider_id": str(getattr(settings, "provider_id", "") or ""), "protocol": str(getattr(settings, "protocol", "") or "")})
    return Decision(
        kind=decision.kind,
        reason=decision.reason,
        evaluation=decision.evaluation,
        extras=extras,
        applied=decision.applied,
    )


def _unavailable_decision(pack_id: str, reason: str, *, transport: str = "unavailable") -> Decision:
    spec = get_pack(pack_id)
    extras: dict[str, Any] = {"transport": transport}
    if spec.family == "security":
        return Decision(kind="ask", reason=reason, extras=extras, applied=False)
    if pack_id == "exposure.turn":
        extras.update({"profile": "full", "wire_narrow": False})
    elif pack_id == "observation.shape":
        extras["shape"] = "keep"
    elif pack_id == "ui.surface":
        extras.update({"surface": "stay", "suppress_heuristic": False})
    elif pack_id == "skill.pin":
        extras["pin"] = ""
    elif pack_id == "loop.wrap":
        extras["next"] = "continue"
    elif pack_id == "observation.prune":
        extras["prune"] = False
    elif pack_id == "mutation.verify":
        extras.update({"satisfied": 0.5, "next": "none"})
    elif pack_id == "recovery.next_step":
        extras["next"] = "none"
    return Decision.noop(reason, **extras)


async def evaluate(
    pack_id: str,
    state: Mapping[str, Any] | None = None,
    *,
    config: Any | None = None,
) -> Decision:
    """评估一个题包。没装 SDK / 没密钥 / 总闸 off 时 fail-open（安全家族 fail-closed=ASK）。

    片 I 在 apply_claimed_followup 入口评估；片 C 在 create_pending 前 shadow。
    L4 wire 收窄只在 ``decision.applied``（总闸+子闸 enforce 且标定已签字）。
    """
    settings = settings_from(config)
    gate = gate_for_pack(pack_id, settings)
    from excelmanus.system_one.breaker import allow, provider_key, record_failure, record_success

    connection_key = provider_key(settings.provider_id, settings.protocol, settings.model, settings.base_url)
    transport = public_transport(settings.api_key, settings.protocol)
    if gate == "off":
        return _unavailable_decision(pack_id, "disabled", transport="unavailable")
    spec = get_pack(pack_id)
    api_key = settings.api_key
    if not client_ready(api_key, settings.protocol):
        decision = _unavailable_decision(pack_id, "unavailable")
        record_shadow(pack_id=pack_id, gate=gate, decision=decision)
        return decision
    if not allow(connection_key):
        decision = _with_provenance(_unavailable_decision(pack_id, "provider_cooldown"), settings)
        record_shadow(pack_id=pack_id, gate=gate, decision=decision)
        return decision
    assert api_key
    bounded = bound_state(pack_id, state)
    try:
        evaluation = await system_one(
            spec,
            bounded,
            model=settings.model,
            api_key=api_key,
            timeout_seconds=settings.timeout_seconds,
            protocol=settings.protocol,
            base_url=settings.base_url,
        )
    except SystemOneUnavailable as exc:
        record_failure(connection_key, str(exc))
        decision = _unavailable_decision(pack_id, f"error:{exc}")
        if spec.family == "security":
            # 高风险 fail-closed：维持 ASK，不放行。
            decision = Decision.ask(f"error:{exc}")
            decision = _with_transport(decision, "unavailable")
        decision = _with_provenance(decision, settings)
        record_shadow(pack_id=pack_id, gate=gate, decision=decision)
        return decision
    except Exception as exc:
        record_failure(connection_key, type(exc).__name__)
        decision = _unavailable_decision(pack_id, f"error:{type(exc).__name__}")
        if spec.family == "security":
            decision = Decision.ask(f"error:{type(exc).__name__}")
            decision = _with_transport(decision, "unavailable")
        decision = _with_provenance(decision, settings)
        record_shadow(pack_id=pack_id, gate=gate, decision=decision)
        return decision

    decision = synthesize(pack_id, evaluation, bounded)
    record_success(connection_key)
    evaluation = type(evaluation)(
        pack_id=evaluation.pack_id,
        answers=evaluation.answers,
        model=evaluation.model,
        latency_ms=evaluation.latency_ms,
        usage=evaluation.usage,
        skipped=evaluation.skipped,
        skip_reason=evaluation.skip_reason,
        provider_id=settings.provider_id,
        protocol=settings.protocol,
    )
    decision = Decision(
        kind=decision.kind,
        reason=decision.reason,
        evaluation=evaluation,
        extras=decision.extras,
        applied=decision.applied,
    )
    decision = stamp_application(pack_id, decision, settings)
    decision = _with_provenance(_with_transport(decision, transport), settings)
    record_shadow(pack_id=pack_id, gate=gate, decision=decision, evaluation=evaluation)
    return decision
