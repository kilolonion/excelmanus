"""Jev 决策对前端的瞬态投影；不进消息块。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from excelmanus.events import EventType, ToolCallEvent
from excelmanus.system_one.context import is_host_session
from excelmanus.logger import get_logger
from excelmanus.system_one.client import client_ready, detect_transport
from excelmanus.system_one.policy import gate_for_pack, jev_is_active, live_jev_settings
from excelmanus.system_one.types import ChoiceAnswer, Decision, NoulAnswer, ScoreAnswer

logger = get_logger("system_one.trace")

_PACK_ACTIONS: dict[str, tuple[str, ...]] = {
    "context.resolve": ("next",),
    "exposure.turn": ("profile",),
    "skill.pin": ("pin",),
    "observation.shape": ("shape",),
    "loop.wrap": ("next",),
    "ui.surface": ("surface",),
    "observation.prune": ("prune",),
    "mutation.verify": ("next",),
    "recovery.next_step": ("next",),
}

_ANSWER_KEYS = (
    "workspace", "target", "edit_intent",
    "domain",
    "needs_write",
    "mode_hint",
    "confidence",
    "is_chitchat",
    "shape",
    "surface",
    "next",
    "pin",
    "action",
    "still_relevant",
    "satisfied",
    "scope_ok",
    "missing_items",
    "retryable",
    "needs_user",
    "done_enough",
)

_BLOCKED_PAYLOAD_KEYS = frozenset(
    {
        "api_key",
        "ai_gateway_api_key",
        "typesafe_api_key",
        "authorization",
        "user_text",
        "state",
        "result_head",
        "candidates",
        "messages",
        "content",
        "prompt",
    }
)


def public_transport(api_key: str | None, protocol: str | None = None) -> str:
    """前端只认 gateway / typesafe / unavailable。"""
    if not client_ready(api_key, protocol):
        return "unavailable"
    if detect_transport(api_key, protocol) == "gateway":
        return "gateway"
    return "typesafe"


def synthesized_action(pack_id: str, decision: Decision) -> str:
    extras = decision.extras or {}
    if decision.kind == "outcome":
        return f"恢复结果 {extras.get('outcome') or ''}".rstrip()
    if pack_id == "approval.tool_call":
        return str(decision.kind or "ask")
    if pack_id == "observation.prune":
        return "prune" if extras.get("prune") else "keep"
    if pack_id == "skill.pin":
        pin = str(extras.get("pin") or "")
        return pin or "none"
    for key in _PACK_ACTIONS.get(pack_id, ()):
        value = extras.get(key)
        if value not in (None, ""):
            return str(value)
    return str(decision.kind or "noop")


def _scalar_answer(answer: Any) -> str | float | bool | None:
    if isinstance(answer, NoulAnswer):
        return round(float(answer.noul), 3)
    if isinstance(answer, ChoiceAnswer):
        return str(answer.choice)
    if isinstance(answer, ScoreAnswer):
        return round(float(answer.score), 3)
    if isinstance(answer, bool):
        return answer
    if isinstance(answer, (int, float)):
        return round(float(answer), 3)
    if answer is None:
        return None
    text = str(answer)
    return text if text else None


def curated_answers(pack_id: str, decision: Decision) -> dict[str, str | float | bool]:
    """只抽对执行面有意义的题面答案。禁止完整 state / 密钥 / 结果正文。"""
    out: dict[str, str | float | bool] = {}
    extras = dict(decision.extras or {})
    if pack_id == "context.resolve" and decision.reason == "context_advice":
        for key in ("workspace", "target", "edit_intent", "column", "read", "next"):
            out[key] = str(extras.get(key) or "")
        confidence = extras.get("confidence") or {}
        if isinstance(confidence, Mapping) and confidence:
            out["confidence"] = min(float(v) for v in confidence.values())
        candidate = extras.get("target_candidate")
        if isinstance(candidate, Mapping):
            out["target_source"] = str(candidate.get("source") or "")
        column = extras.get("column_candidate")
        if isinstance(column, Mapping):
            letter = str(column.get("column") or "")
            header = str(column.get("header") or "")[:60]
            out["column_header"] = f"{letter}:{header}".strip(":")
        routed = str(extras.get("routed_workspace") or "")
        if routed:
            out["routed_workspace"] = routed[:80]
        return out
    evaluation = decision.evaluation
    if evaluation is not None:
        for qid, answer in (evaluation.answers or {}).items():
            if qid not in _ANSWER_KEYS and qid not in {
                "mode_mismatch",
                "needs_skill",
                "destructive",
            }:
                continue
            key = "mode_hint" if qid == "mode_mismatch" else qid
            value = _scalar_answer(answer)
            if value is None:
                continue
            out[key] = value
            if isinstance(answer, ChoiceAnswer) and answer.confidence and "confidence" not in out:
                out["confidence"] = round(float(answer.confidence), 3)
    for key in (
        "domain",
        "mode_hint",
        "needs_write",
        "is_chitchat",
        "shape",
        "surface",
        "next",
        "pin",
        "missing_items",
    ):
        if key in out:
            continue
        value = _scalar_answer(extras.get(key))
        if value is None:
            continue
        out[key] = value
    if "confidence" not in out:
        for key in ("domain_confidence", "mode_hint_confidence", "next_confidence"):
            raw = extras.get(key)
            if isinstance(raw, (int, float)) and raw:
                out["confidence"] = round(float(raw), 3)
                break
    if pack_id == "approval.tool_call" and "action" not in out:
        out["action"] = str(decision.kind or "ask")
    return out


def impact_sentence(pack_id: str, decision: Decision, gate: str) -> str:
    reason = str(decision.reason or "")
    if gate == "off" or reason == "disabled":
        return "总闸或子闸关闭，未评估"
    if reason == "unavailable" or reason.startswith("error:") or reason.startswith("unavailable:"):
        return "评估不可用，执行面与接线前相同"
    if reason == "budget_exhausted":
        return "Jev 回合预算已用尽，执行面保持接线前行为"
    if reason == "provider_cooldown":
        return "Jev provider 处于冷却期，执行面保持接线前行为"
    if not decision.applied or gate != "enforce":
        return "本次未应用建议，任务继续按原流程处理"
    extras = decision.extras or {}
    if decision.kind == "outcome":
        return {
            "escaped": "建议后已摆脱同一失败",
            "repeated": "建议后仍重复同一失败",
            "not_continued": "建议后未再调用工具",
            "not_delivered": "建议未送达主模型",
            "stopped": "按熔断停止",
        }.get(str(extras.get("outcome") or ""), "恢复结果已记录")
    if pack_id == "context.resolve":
        routed = str(extras.get("routed_workspace") or "")
        if routed:
            return f"已将会话绑定到工作区「{routed[:40]}」，可在会话列表中纠正归属"
        return "已向主模型提供工作区、表格位置和澄清建议；未切换工作区或修改文件"
    if pack_id == "exposure.turn":
        profile = extras.get("profile") or "full"
        if extras.get("wire_narrow"):
            return f"已按 {profile} 收窄出网工具 schema"
        return f"已记录暴露面 {profile}，本回合未收窄 wire"
    if pack_id == "skill.pin":
        pin = extras.get("pin") or ""
        return f"已将技能 {pin} 置顶" if pin else "未置顶技能"
    if pack_id == "approval.tool_call":
        if decision.kind == "deny":
            return "已拒绝该高风险调用"
        if decision.kind == "auto":
            return "已自动放行该调用"
        return "仍走审批询问"
    if pack_id == "observation.shape":
        return f"已将观察裁成 {extras.get('shape') or 'keep'}"
    if pack_id == "loop.wrap":
        return f"步末建议 {extras.get('next') or 'continue'}（不中断循环）"
    if pack_id == "ui.surface":
        return f"已建议切换到 {extras.get('surface') or 'stay'}"
    if pack_id == "observation.prune":
        return "已将旧观察收成指针" if extras.get("prune") else "保留旧观察"
    if pack_id == "mutation.verify":
        missing = extras.get("missing_items")
        if isinstance(missing, (int, float)) and missing:
            return f"写入后验证建议 {extras.get('next') or 'none'}；缺证据事项 {int(missing)} 项"
        return f"写入后验证建议 {extras.get('next') or 'none'}"
    if pack_id == "recovery.next_step":
        return f"失败后建议 {extras.get('next') or 'stop'}"
    return "已应用到执行面"


def build_jev_trace_payload(
    pack_id: str,
    decision: Decision,
    *,
    gate: str,
    transport: str,
) -> dict[str, Any]:
    evaluation = decision.evaluation
    latency = float(getattr(evaluation, "latency_ms", 0.0) or 0.0) if evaluation else 0.0
    extras = decision.extras or {}
    transport = str(extras.get("transport") or transport or "unavailable")
    if transport == "typesafe_direct":
        transport = "typesafe"
    if transport not in {"gateway", "typesafe", "unavailable"}:
        transport = "unavailable"
    payload: dict[str, Any] = {
        "pack": pack_id,
        "gate": gate if gate in {"off", "enforce"} else "off",
        "applied": bool(decision.applied),
        "transport": transport,
        "latency_ms": round(latency, 1),
        "action": synthesized_action(pack_id, decision),
        "kind": str(decision.kind or "noop"),
        "reason": str(decision.reason or "")[:200],
        "answers": curated_answers(pack_id, decision),
        "impact": impact_sentence(pack_id, decision, gate),
        "provider_id": str(extras.get("provider_id") or "")[:80],
        "protocol": str(extras.get("protocol") or "")[:40],
        "model": str(getattr(evaluation, "model", "") or "")[:120] if evaluation else "",
    }
    try:
        from excelmanus.system_one.calibration import calibration_fingerprint

        payload["calibration_fingerprint"] = calibration_fingerprint(pack_id)
    except Exception:
        payload["calibration_fingerprint"] = ""
    if "outcome" in extras:
        payload["outcome"] = str(extras.get("outcome") or "")[:40]
        payload["source"] = str(extras.get("source") or "")[:40]
    budget = extras.get("budget")
    if isinstance(budget, Mapping):
        payload["budget"] = {
            "evaluations": int(budget.get("evaluations", 0) or 0),
            "max_evaluations": int(budget.get("max_evaluations", 0) or 0),
            "spent_latency_ms": round(float(budget.get("spent_latency_ms", 0.0) or 0.0), 1),
            "exhausted_reason": str(budget.get("exhausted_reason") or "")[:40],
        }
    return {key: value for key, value in payload.items() if key not in _BLOCKED_PAYLOAD_KEYS}


def _resolve_on_event(engine: Any, on_event: Any | None) -> Any | None:
    if callable(on_event):
        return on_event
    driver = getattr(engine, "_driver", None)
    fallback = getattr(driver, "_on_event", None)
    return fallback if callable(fallback) else None


def emit_jev_trace(
    engine: Any,
    decision: Decision,
    *,
    pack_id: str,
    on_event: Any | None = None,
) -> None:
    """所有 maybe_* 在 evaluate 之后调用。失败 / unavailable 也发。child 不发。"""
    if engine is None:
        return
    if not is_host_session(engine):
        return
    settings = live_jev_settings(getattr(engine, "config", None))
    if not jev_is_active(settings):
        return
    try:
        gate = gate_for_pack(pack_id, settings)
    except Exception:
        gate = "off"
    transport = public_transport(settings.api_key, settings.protocol)
    payload = build_jev_trace_payload(
        pack_id,
        decision,
        gate=gate,
        transport=transport,
    )
    driver = getattr(engine, "_driver", None)
    prepared = getattr(engine, "_prepared_request", None)
    payload.update(
        {
            "turn_id": str(getattr(driver, "turn_id", "") or "")[:80],
            "step_id": str(getattr(driver, "step_id", "") or "")[:80],
            "request_id": str(
                getattr(engine, "_open_request_id", "")
                or getattr(prepared, "request_id", "")
                or ""
            )[:120],
        }
    )
    event = ToolCallEvent(event_type=EventType.JEV_TRACE, jev_trace=payload)
    callback = _resolve_on_event(engine, on_event)
    try:
        emit = getattr(engine, "_emit", None)
        if callable(emit):
            emit(callback, event)
        elif callable(callback):
            callback(event)
    except Exception:
        logger.debug("jev_trace emit failed; continuing turn", exc_info=True)
