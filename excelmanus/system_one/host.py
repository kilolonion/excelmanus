"""Host 接线：回合入口评估、审批、工具预加载、回合末 UI 面、F/G/M/P。

总闸或对应子闸关闭时跳过该题包；开启时决策直接进入确定性宿主守卫。
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from excelmanus.events import EventType, ToolCallEvent
from excelmanus.logger import get_logger
from excelmanus.system_one.adapter import exposure_state_from_engine
from excelmanus.system_one.budget import reset_turn_budget
from excelmanus.system_one.context import is_child_session
from excelmanus.system_one.log import record_jev_decision
from excelmanus.system_one.packs import PROFILE_NAMES
from excelmanus.system_one.policy import (
    T_BIG,
    T_CHAT,
    T_CODE,
    T_PRUNE_BATCH,
    T_PRUNE_KEEP,
    T_TIGHT_CHARS,
    decision_can_apply,
    decision_is_applied,
    flag_is_applied,
    gate_for_pack,
    is_known_dangerous_call,
    jev_is_active,
    live_jev_config,
    live_jev_settings,
    next_sticky_profile,
)
from excelmanus.system_one.types import Decision
from excelmanus.system_one.trace import emit_jev_trace, record_host_effect

logger = get_logger("system_one.host")
_MAX_OBSERVATION_EVALUATIONS_PER_TURN = 3
# UI navigation is a tail enhancement.  It must not hold a completed answer
# behind the provider's general Jev timeout (which users may configure higher).
_UI_HINT_MAX_WAIT_SECONDS = 0.5

# 第一期 E 只覆盖这三类；Tier B 写表不进此集合。
_E_APPROVAL_TOOLS = frozenset({"run_shell", "delete_file", "run_code"})


async def _eval_traced(
    engine: Any,
    pack_id: str,
    state: Mapping[str, Any] | None,
    *,
    on_event: Any | None = None,
) -> Decision:
    """evaluate 之后必发 jev_trace。失败 / unavailable 也发。"""
    from excelmanus.system_one.runtime import evaluate_for_host

    config = live_jev_config(getattr(engine, "config", None))
    try:
        decision = await evaluate_for_host(engine, pack_id, state, config=config)
    except Exception as exc:
        logger.debug("%s evaluate failed", pack_id, exc_info=True)
        if pack_id == "approval.tool_call":
            decision = Decision(
                kind="ask",
                reason=f"unavailable:{type(exc).__name__}",
                applied=False,
            )
        else:
            decision = Decision.noop(f"error:{type(exc).__name__}")
        settings = live_jev_settings(getattr(engine, "config", None))
        try:
            gate = gate_for_pack(pack_id, settings)
        except Exception:
            gate = "off"
        if not settings.experimental_enabled or gate == "off":
            return decision
        record_jev_decision(pack_id=pack_id, gate=gate, decision=decision)
        emit_jev_trace(engine, decision, pack_id=pack_id, on_event=on_event)
        return decision
    if not isinstance(decision, Decision):
        # Adapters must return Decision. Treat malformed provider results as a
        # no-op so tracing never lets a bad integration break the host turn.
        decision = Decision.noop("invalid_decision")
    # Settings can change while the provider request is in flight.
    current = live_jev_settings(getattr(engine, "config", None))
    if not jev_is_active(current) or gate_for_pack(pack_id, current) == "off":
        return Decision.noop("disabled")
    emit_jev_trace(engine, decision, pack_id=pack_id, on_event=on_event)
    return decision


def _jev_connected(engine: Any) -> bool:
    return jev_is_active(live_jev_settings(getattr(engine, "config", None)))


def clear_turn_exposure(engine: Any) -> None:
    engine._turn_exposure = None  # type: ignore[attr-defined]
    # Discovery belongs to the session. The builder intersects it with current
    # authorization; resetting it on every turn rewrites the tools prefix.
    engine._tools_cache = None  # type: ignore[attr-defined]
    engine._skill_pin = None  # type: ignore[attr-defined]
    engine._skill_pin_evaluated = False  # type: ignore[attr-defined]
    engine._skill_pin_reported = False
    engine._jev_context_decision = None
    engine._loop_wrap = None  # type: ignore[attr-defined]
    engine._jev_loop_evaluations = 0
    engine._jev_loop_result_count = 0
    engine._jev_observation_evaluations = 0  # type: ignore[attr-defined]
    engine._jev_turn_budget = None  # type: ignore[attr-defined]
    engine._mutation_verification = None  # type: ignore[attr-defined]
    engine._recovery_hint = None  # type: ignore[attr-defined]
    engine._jev_context_input = None
    engine._jev_metrics = {
        "evaluated": 0,
        "effects": 0,
        "outcomes": 0,
        "unavailable": 0,
        "latency_ms": 0.0,
    }


def jev_turn_metrics(engine: Any) -> dict[str, Any]:
    """Return a bounded per-turn metric snapshot for diagnostics only."""
    raw = getattr(engine, "_jev_metrics", None)
    if not isinstance(raw, Mapping):
        return {}
    try:
        latency = round(max(0.0, float(raw.get("latency_ms", 0.0) or 0.0)), 1)
    except (TypeError, ValueError):
        latency = 0.0
    return {
        "evaluated": max(0, int(raw.get("evaluated", 0) or 0)),
        "effects": max(0, int(raw.get("effects", 0) or 0)),
        "outcomes": max(0, int(raw.get("outcomes", 0) or 0)),
        "unavailable": max(0, int(raw.get("unavailable", 0) or 0)),
        "latency_ms": latency,
    }


def remember_turn_tools(engine: Any, chat_result: Any) -> None:
    names: list[str] = []
    for item in getattr(chat_result, "tool_calls", None) or ():
        name = str(getattr(item, "tool_name", "") or "")
        if name:
            names.append(name)
    engine._exposure_last_tools = names[:10]  # type: ignore[attr-defined]


def turn_exposure_profile(engine: Any) -> str:
    record = getattr(engine, "_turn_exposure", None)
    if not isinstance(record, Mapping):
        return "full"
    return str(record.get("profile") or "full")


def turn_wire_profile(engine: Any) -> str:
    """初始披露的类别偏置；full 表示无偏置，仍使用常驻核心集合。"""
    if is_child_session(engine):
        return "full"
    settings = live_jev_settings(getattr(engine, "config", None))
    if not jev_is_active(settings) or not decision_is_applied("exposure.turn", settings):
        return "full"
    record = getattr(engine, "_turn_exposure", None)
    if not isinstance(record, Mapping):
        return "full"
    if not bool(record.get("applied")) or not bool(record.get("wire_narrow")):
        return "full"
    sticky = str(record.get("sticky_profile") or "full")
    if sticky not in PROFILE_NAMES:
        return "full"
    return sticky


async def maybe_record_turn_exposure(
    engine: Any,
    user_text: str,
    *,
    on_event: Any | None = None,
    budget: Any = None,
    reset: bool = True,
) -> None:
    """片 I：控制命令之后、USER_PROMPT_SUBMIT 落定后，选择工具预加载类别。

    片 K 复用本评估的 mode_hint；工具调用方式由主模型逐步选择。
    """
    from excelmanus.system_one.budget import JevTurnBudget

    if reset:
        clear_turn_exposure(engine)
        if isinstance(budget, JevTurnBudget):
            engine._jev_turn_budget = budget
        else:
            reset_turn_budget(engine)
    elif isinstance(budget, JevTurnBudget):
        # The concurrent entry path initializes the turn once before creating
        # both tasks.  A caller-supplied budget still wins without clearing the
        # context decision produced by the sibling task.
        engine._jev_turn_budget = budget
    if is_child_session(engine):
        return
    if not _jev_connected(engine):
        return
    config = live_jev_config(getattr(engine, "config", None))
    settings = live_jev_settings(config)
    if gate_for_pack("exposure.turn", settings) == "off":
        return
    state = exposure_state_from_engine(engine, user_text)
    decision = await _eval_traced(engine, "exposure.turn", state, on_event=on_event)
    extras = dict(decision.extras)
    proposed = str(extras.get("profile") or "full")
    sticky_in = getattr(engine, "_exposure_sticky", None)
    effective, sticky_out = next_sticky_profile(
        sticky_in if isinstance(sticky_in, Mapping) else None,
        proposed,
    )
    engine._exposure_sticky = sticky_out  # type: ignore[attr-defined]
    applied = decision_can_apply("exposure.turn", decision, settings)
    wire_narrow = applied and effective != "full"
    evaluation = decision.evaluation
    engine._turn_exposure = {  # type: ignore[attr-defined]
        "profile": proposed,
        "sticky_profile": effective,
        "domain": extras.get("domain") or "mixed",
        "mode_hint": extras.get("mode_hint") or "keep",
        "mode_hint_confidence": float(extras.get("mode_hint_confidence") or 0.0),
        "conf": float(extras.get("domain_confidence") or 0.0),
        "latency_ms": float(getattr(evaluation, "latency_ms", 0.0) or 0.0) if evaluation else 0.0,
        "wire_narrow": wire_narrow,
        "gate": "enforce" if applied else gate_for_pack("exposure.turn", settings),
        "applied": applied,
        "is_chitchat": float(extras.get("is_chitchat") or 0.0),
    }
    if (
        applied
        and float(extras.get("is_chitchat") or 0.0) >= T_CHAT
        and proposed == "minimal"
    ):
        engine._turn_exposure["sticky_profile"] = "minimal"  # type: ignore[index]
        engine._turn_exposure["wire_narrow"] = True  # type: ignore[index]
    engine._tools_cache = None  # type: ignore[attr-defined]
    maybe_enqueue_mode_switch(engine, on_event=on_event)


_MODE_SWITCH_CARDS: dict[str, dict[str, str]] = {
    "suggest_write": {
        "target": "write",
        "text": "本轮需要写入，切到编辑模式？",
        "confirm": "切换到编辑",
        "confirm_desc": "切到编辑模式后可以改文件",
        "keep": "保持观察",
        "keep_desc": "留在当前模式，不切换",
    },
    "suggest_plan": {
        "target": "plan",
        "text": "进入计划模式？",
        "confirm": "进入计划",
        "confirm_desc": "先写计划，改文件需批准",
        "keep": "继续编辑",
        "keep_desc": "留在编辑模式",
    },
}


def maybe_enqueue_mode_switch(engine: Any, *, on_event: Any | None = None) -> None:
    """片 K：高置信失配才发卡。Jev 永不直接改 chat_mode。本回合不阻断 LLM。"""
    if is_child_session(engine):
        return
    settings = live_jev_settings(getattr(engine, "config", None))
    if not jev_is_active(settings) or not flag_is_applied(settings.mode_hint, settings, pack_id="exposure.turn"):
        return
    record = getattr(engine, "_turn_exposure", None)
    if not isinstance(record, Mapping) or not record.get("applied"):
        return
    hint = str(record.get("mode_hint") or "keep")
    conf = float(record.get("mode_hint_confidence") or 0.0)
    if conf < T_CODE:
        return
    chat = str(getattr(engine, "_current_chat_mode", "write") or "write")
    if chat == "write" and hint == "suggest_read":
        return
    spec = _MODE_SWITCH_CARDS.get(hint)
    if spec is None:
        return
    target = spec["target"]
    if chat == target:
        return
    flow = getattr(engine, "_question_flow", None)
    enqueue = getattr(flow, "enqueue", None)
    if not callable(enqueue):
        return
    if getattr(flow, "has_pending", lambda: False)():
        return
    try:
        pending = enqueue(
            {
                "header": "模式建议",
                "text": spec["text"],
                "options": [
                    {"label": spec["confirm"], "description": spec["confirm_desc"]},
                    {"label": spec["keep"], "description": spec["keep_desc"]},
                ],
            },
            "mode_switch",
        )
    except Exception:
        logger.debug("mode switch enqueue failed; continuing turn", exc_info=True)
        return
    actions = getattr(engine, "_system_question_actions", None)
    if isinstance(actions, dict) and pending is not None:
        from excelmanus.engine_utils import _SYSTEM_Q_MODE_SWITCH

        actions[getattr(pending, "question_id", "")] = {
            "type": _SYSTEM_Q_MODE_SWITCH,
            "target": target,
        }
    handler = getattr(engine, "_interaction_handler", None)
    emit = getattr(handler, "emit_user_question_event", None)
    if callable(emit) and pending is not None:
        emit(question=pending, on_event=on_event, iteration=0)
        record_host_effect(
            engine, "exposure.turn", action=hint, changed=True,
            impact="已加入模式确认问题，等待用户选择", on_event=on_event,
        )


def _observation_must_keep(result: Any) -> bool:
    if not bool(getattr(result, "success", True)):
        return True
    ui = getattr(result, "ui_meta", None)
    if ui is not None and (
        getattr(ui, "download", None)
        or getattr(ui, "diff", None)
        or getattr(ui, "text_diff", None)
    ):
        return True
    value = getattr(result, "value", None)
    if isinstance(value, Mapping) and (value.get("download") or value.get("diff")):
        return True
    coverage = getattr(result, "coverage", None)
    if isinstance(coverage, Mapping) and coverage.get("spill_retrieve"):
        return True
    return False


def _re_fetchable(tool_name: str, arguments: Mapping[str, Any] | None) -> bool:
    from excelmanus.tools.policy import READ_ONLY_SAFE_TOOLS

    if tool_name not in READ_ONLY_SAFE_TOOLS:
        return False
    args = arguments or {}
    return bool(args.get("file_path") or args.get("path") or args.get("sheet"))


def _pointer_text(tool_name: str, arguments: Mapping[str, Any] | None) -> str:
    args = arguments or {}
    parts: list[str] = []
    for key in ("file_path", "path", "sheet", "range"):
        raw = args.get(key)
        if raw:
            parts.append(str(raw))
    inner = ",".join(parts)
    return f"[已收起：{tool_name}({inner}) — 重调同参可取回]"


def _head_tail(text: str, cap: int) -> str:
    if cap <= 0 or len(text) <= cap:
        return text
    keep = max(120, cap // 2)
    return (
        f"{text[:keep]}\n…\n{text[-keep:]}\n"
        f"[结果已收紧，原始长度: {len(text)} 字符]"
    )


def _apply_observation_shape(
    result: Any,
    shape: str,
    *,
    tool_name: str,
    arguments: Mapping[str, Any] | None,
    engine: Any,
) -> Any:
    from dataclasses import replace

    from excelmanus.engine_core.tool_result import ToolResult

    if not isinstance(result, ToolResult) or shape == "keep":
        return result
    text = str(result.model_text or "")
    coverage = dict(result.coverage or {})
    if shape in {"pointer", "truncate"}:
        root = getattr(getattr(engine, "config", None), "workspace_root", None)
        if not root:
            return result
        from excelmanus.engine_core.spill import SpillStore

        # Re-running a read may return a changed workbook. Preserve this exact
        # observation before removing any of it from the model's context.
        locator = SpillStore(str(root)).put(text)
        retrieval = f"[完整结果：read_text_file(file_path='{locator}')；这是本次结果的固定句柄]"
        coverage.update({"declared": True, "truncated": True, "kind": "truncated"})
        coverage["spill_locator"] = str(locator)
        return replace(
            result,
            model_text=retrieval + ("\n" + _head_tail(text, T_TIGHT_CHARS) if shape == "truncate" else ""),
            truncated=True,
            coverage=coverage,
        )
    if shape == "spill":
        root = getattr(getattr(engine, "config", None), "workspace_root", None)
        if not root:
            return result
        from excelmanus.engine_core.spill import SpillStore, project_for_wire

        projection = project_for_wire(text, store=SpillStore(str(root)))
        if not projection.spilled:
            return result
        coverage.update({"declared": True, "truncated": True, "kind": "truncated"})
        return replace(
            result,
            model_text=projection.model_text,
            truncated=True,
            coverage=coverage,
        )
    return result


async def maybe_shape_observation(
    engine: Any,
    result: Any,
    *,
    tool_name: str,
    arguments: Mapping[str, Any] | None = None,
) -> Any:
    """片 N：仅超大结果在 finalize_content 前选档。默认不改 model_text。"""
    if engine is None or is_child_session(engine) or result is None:
        return result
    if _observation_must_keep(result):
        return result
    text = str(getattr(result, "model_text", "") or "")
    if len(text) <= T_BIG:
        return result
    if not _jev_connected(engine):
        return result
    settings = live_jev_settings(getattr(engine, "config", None))
    if gate_for_pack("observation.shape", settings) == "off":
        return result
    evaluations = int(getattr(engine, "_jev_observation_evaluations", 0) or 0)
    if evaluations >= _MAX_OBSERVATION_EVALUATIONS_PER_TURN:
        return result
    engine._jev_observation_evaluations = evaluations + 1  # type: ignore[attr-defined]
    from excelmanus.system_one.adapter import observation_state_from_engine

    args = dict(arguments or {})
    state = observation_state_from_engine(
        engine,
        tool_name=tool_name,
        arguments=args,
        result=result,
        re_fetchable=_re_fetchable(tool_name, args),
        spillable=bool(getattr(getattr(engine, "config", None), "workspace_root", None)),
    )
    decision = await _eval_traced(engine, "observation.shape", state)
    if not decision_can_apply("observation.shape", decision, settings):
        return result
    shape = str(decision.extras.get("shape") or "keep")
    shaped = _apply_observation_shape(
        result,
        shape,
        tool_name=tool_name,
        arguments=args,
        engine=engine,
    )
    if shaped.model_text != result.model_text:
        record_host_effect(
            engine, "observation.shape", action=shape, changed=True,
            impact=f"工具结果由 {len(result.model_text)} 字符缩为 {len(shaped.model_text)} 字符，保留完整结果句柄",
        )
    return shaped


_UI_SURFACES = frozenset(
    {"stay", "side_panel", "sheet_full", "compare", "files_tab", "none"},
)


def _has_pending_interaction(engine: Any) -> bool:
    flow = getattr(engine, "_question_flow", None)
    has_q = getattr(flow, "has_pending", None)
    if callable(has_q) and has_q():
        return True
    has_a = getattr(engine, "has_pending_approval", None)
    if callable(has_a) and has_a():
        return True
    return False


def _turn_outcome(engine: Any, chat_result: Any) -> str:
    if bool(getattr(chat_result, "truncated", False)):
        return "fail"
    calls = list(getattr(chat_result, "tool_calls", None) or [])
    failures = sum(not bool(getattr(item, "success", False)) for item in calls)
    successes = len(calls) - failures
    if failures and not successes:
        return "fail"
    if failures:
        return "partial"
    return "ok"


async def maybe_emit_ui_hint(
    engine: Any,
    chat_result: Any,
    *,
    on_event: Any | None = None,
) -> None:
    """片 O：reply 已产出、done 之前。开启后直接应用 UI 建议，失败静默。"""
    if engine is None or is_child_session(engine):
        return
    if not callable(on_event):
        on_event = getattr(getattr(engine, "_driver", None), "_on_event", None)
    if not callable(on_event):
        return
    if _has_pending_interaction(engine):
        return
    outcome = _turn_outcome(engine, chat_result)
    if outcome == "fail":
        return
    if not _jev_connected(engine):
        return
    settings = live_jev_settings(getattr(engine, "config", None))
    if gate_for_pack("ui.surface", settings) == "off":
        return
    from excelmanus.system_one.adapter import ui_surface_state_from_engine

    state = ui_surface_state_from_engine(engine, chat_result, turn_outcome=outcome)
    if not state.get("candidate_files"):
        return
    try:
        decision = await asyncio.wait_for(
            _eval_traced(engine, "ui.surface", state, on_event=on_event),
            timeout=min(_UI_HINT_MAX_WAIT_SECONDS, max(0.05, float(settings.timeout_seconds))),
        )
    except asyncio.TimeoutError:
        # A late navigation preference is disposable.  Keep the timeout
        # visible in the JEV timeline, but let the completed reply continue
        # without a UI_HINT event.
        decision = Decision.noop("timeout", transport="unavailable")
        current = live_jev_settings(getattr(engine, "config", None))
        if not current.experimental_enabled or gate_for_pack("ui.surface", current) == "off":
            return
        record_jev_decision(
            pack_id="ui.surface",
            gate=gate_for_pack("ui.surface", current),
            decision=decision,
        )
        emit_jev_trace(engine, decision, pack_id="ui.surface", on_event=on_event)
    if not decision_can_apply("ui.surface", decision, settings):
        return
    extras = decision.extras or {}
    surface = str(extras.get("surface") or "stay")
    if surface not in _UI_SURFACES:
        surface = "stay"
    candidates = [str(item) for item in (state.get("candidate_files") or []) if item]
    file_path = str(extras.get("file_path") or (candidates[0] if len(candidates) == 1 else ""))
    file_b = str(extras.get("file_b") or "")
    if file_path not in candidates:
        file_path = ""
    if file_b not in candidates or file_b == file_path:
        file_b = ""
    if surface in {"side_panel", "sheet_full", "compare"} and not file_path:
        return
    if surface == "compare" and not file_b:
        return
    if surface in {"stay", "none"} and not extras.get("suppress_heuristic"):
        return
    event = ToolCallEvent(
        event_type=EventType.UI_HINT,
        ui_hint_surface=surface,
        ui_hint_file_path=file_path,
        ui_hint_sheet="",
        ui_hint_reason=str(decision.reason or ""),
        ui_hint_suppress_auto_open=bool(extras.get("suppress_heuristic")),
        excel_file_b=file_b if surface == "compare" else "",
    )
    try:
        emit = getattr(engine, "_emit", None)
        if callable(emit):
            emit(on_event, event)
        elif callable(on_event):
            on_event(event)
        else:
            return
        record_host_effect(
            engine, "ui.surface", action=surface, stage="sent",
            impact="界面建议已发出，是否执行以界面处理记录为准", on_event=on_event,
        )
    except Exception:
        logger.debug("ui_hint emit failed; continuing turn", exc_info=True)


async def maybe_verify_mutation(
    engine: Any,
    chat_result: Any,
    *,
    on_event: Any | None = None,
) -> str:
    """Compatibility no-op: the primary agent owns verification and delivery.

    Keep this import surface for older integrations, but never evaluate a
    secondary reviewer or inject instructions that reopen a completed reply.
    Tool-level write receipts and version checks remain independent.
    """
    return ""


def should_check_delivery(engine: Any) -> bool:
    """Compatibility no-op; automatic delivery review has been removed."""
    return False


async def maybe_suggest_recovery(
    engine: Any,
    tool_results: list[Any],
    *,
    on_event: Any | None = None,
    breaker_triggered: bool = False,
) -> str:
    """Give bounded recovery advice once per turn, preserving the breaker."""
    if engine is None or is_child_session(engine) or getattr(engine, "_recovery_hint", None) is not None:
        return ""
    if not any(not bool(getattr(item, "success", False)) for item in tool_results):
        return ""
    settings = live_jev_settings(getattr(engine, "config", None))
    if not jev_is_active(settings) or gate_for_pack("recovery.next_step", settings) == "off":
        return ""
    from excelmanus.system_one.adapter import recovery_state_from_engine

    state = recovery_state_from_engine(engine, tool_results, breaker_triggered=breaker_triggered)
    if int(state.get("consecutive_failures") or 0) < 2 and not breaker_triggered:
        return ""
    errors = state.get("error_facts", [])
    classes = {item.get("failure_class") for item in errors}
    # Stable error codes already carry the right recovery. No semantic call is
    # needed to refresh a missing sheet/version or respect a rejection.
    source = "jev"
    if breaker_triggered or classes & {"permission_denied", "approval_denied", "approval_timeout", "blocked"}:
        decision = Decision(kind="noop", reason="deterministic_stop", extras={"next": "stop"}, applied=True)
        source = "deterministic"
    elif any(item.get("committed") or item.get("commit_unknown") for item in errors):
        decision = Decision(kind="noop", reason="commit_requires_inspection", extras={"next": "inspect_more"}, applied=True)
        source = "deterministic"
    elif classes and classes <= {"not_found", "conflict"}:
        decision = Decision(kind="noop", reason="refresh_target", extras={"next": "inspect_more"}, applied=True)
        source = "deterministic"
    else:
        decision = await _eval_traced(engine, "recovery.next_step", state, on_event=on_event)
    engine._recovery_hint = {  # type: ignore[attr-defined]
        "next": str((decision.extras or {}).get("next") or "stop"),
        "retryable": float((decision.extras or {}).get("retryable") or 0.0),
        "needs_user": float((decision.extras or {}).get("needs_user") or 0.0),
        "applied": decision_can_apply("recovery.next_step", decision, settings),
        "reason": decision.reason,
        "source": source,
        "breaker_triggered": bool(breaker_triggered),
        "delivered": False,
        "result_count": len(tool_results),
        "error_codes": [item.get("error_code") for item in errors],
    }
    if not decision_can_apply("recovery.next_step", decision, settings):
        return ""
    action = engine._recovery_hint["next"]
    record_jev_decision(
        pack_id="recovery.next_step", gate=gate_for_pack("recovery.next_step", settings),
        decision=Decision(
            kind="noop", reason="recovery_advice_created", applied=True,
            extras={"stage": "advice", "next": action, "source": source},
        ),
    )
    if breaker_triggered:
        return "Jev 恢复建议：本轮已触发连续失败停止条件，请先核对错误与目标信息，再发起后续请求。"
    return {
        "retry": "失败可能是暂时性的；先核对工具返回的恢复步骤，仅在确认未提交且允许重试时尝试一次，勿重放已拒绝或可能已提交的写入。",
        "inspect_more": "请先做有界的只读检查，刷新文件、工作表或内容版本，再决定下一步；勿直接重复失败的写入。",
        "ask_user": "辅助判断：错误可能涉及范围或意图不明确。若原请求和已有信息足以确定则继续处理；只有确实缺少必要信息时才询问用户。",
        "stop": "本次失败不适合继续重复操作。请说明失败原因和已完成部分；权限、审批与停止条件仍然有效。",
    }.get(action, "")


def emit_recovery_outcome(engine: Any, *, on_event: Any | None = None) -> None:
    """回合末把恢复建议的采纳结果记成一条 outcome 决策。child 不发。"""
    if engine is None or is_child_session(engine):
        return
    hint = getattr(engine, "_recovery_hint", None)
    if not isinstance(hint, dict) or not hint.get("applied"):
        return
    if (
        str(hint.get("next") or "") == "stop"
        and str(hint.get("reason") or "") == "deterministic_stop"
        and bool(hint.get("breaker_triggered"))
    ):
        outcome = "stopped"
    elif not hint.get("delivered"):
        outcome = "not_delivered"
    elif hint.get("following_success") is None:
        outcome = "not_continued"
    elif hint.get("same_failure_repeated"):
        outcome = "repeated"
    elif hint.get("following_success") is False:
        outcome = "different_failure"
    else:
        outcome = "escaped"
    settings = live_jev_settings(getattr(engine, "config", None))
    try:
        gate = gate_for_pack("recovery.next_step", settings)
    except Exception:
        gate = "off"
    if not settings.experimental_enabled or gate == "off":
        return
    decision = Decision(
        kind="outcome",
        reason=f"recovery_outcome:{outcome}",
        applied=True,
        extras={
            "next": str(hint.get("next") or ""),
            "source": str(hint.get("source") or "jev"),
            "outcome": outcome,
            "stage": "outcome",
        },
    )
    record_jev_decision(pack_id="recovery.next_step", gate=gate, decision=decision)
    emit_jev_trace(engine, decision, pack_id="recovery.next_step", on_event=on_event)


def _has_at_mention(text: str) -> bool:
    try:
        from excelmanus.mentions.parser import MentionParser

        return bool(MentionParser.parse(text or "").mentions)
    except Exception:
        return False


def should_skip_skill_catalog_snapshot(engine: Any) -> bool:
    """片 M：高置信寒暄且无图/无 pending/无 @ 才跳过目录快照。"""
    if engine is None or is_child_session(engine):
        return False
    settings = live_jev_settings(getattr(engine, "config", None))
    if not jev_is_active(settings) or not decision_is_applied("exposure.turn", settings):
        return False
    record = getattr(engine, "_turn_exposure", None)
    if not isinstance(record, Mapping) or not record.get("applied"):
        return False
    if float(record.get("is_chitchat") or 0.0) < T_CHAT:
        return False
    if int(getattr(engine, "_turn_image_count", 0) or 0) > 0:
        return False
    if _has_pending_interaction(engine):
        return False
    from excelmanus.system_one.adapter import last_user_text

    if _has_at_mention(last_user_text(engine)):
        return False
    return True


def _skill_entries(engine: Any) -> list[tuple[str, str]]:
    from excelmanus.prompt.skill_catalog import collect_skill_entries

    router = getattr(engine, "_skill_router", None)
    loader = getattr(router, "_loader", None) if router is not None else None
    getter = getattr(loader, "get_skillpacks", None)
    if not callable(getter):
        return []
    try:
        packs = getter() or {}
    except Exception:
        return []
    blocked: set[str] = set()
    resolver = getattr(engine, "_skill_resolver", None)
    if resolver is not None and not getattr(engine, "_full_access_enabled", True):
        raw_getter = getattr(resolver, "blocked_skillpacks", None)
        raw = raw_getter() if callable(raw_getter) else raw_getter
        if isinstance(raw, (set, list, tuple, frozenset)):
            blocked = {str(item) for item in raw}
    if not isinstance(packs, Mapping):
        return []
    return collect_skill_entries(packs, blocked=blocked)


async def maybe_pin_skills(engine: Any) -> None:
    """片 F：高置信才置顶/标 likely match。不得 skill()。"""
    if engine is None or is_child_session(engine):
        return
    if not _jev_connected(engine):
        return
    if getattr(engine, "_skill_pin_evaluated", False):
        return
    engine._skill_pin_evaluated = True  # type: ignore[attr-defined]
    settings = live_jev_settings(getattr(engine, "config", None))
    if gate_for_pack("skill.pin", settings) == "off":
        return
    entries = _skill_entries(engine)
    if not entries:
        return
    from excelmanus.system_one.adapter import last_user_text
    from excelmanus.prompt.skill_catalog import is_warmup_ping
    from excelmanus.system_one.evidence import rank_skills

    text = last_user_text(engine)
    if is_warmup_ping(text):
        return
    active = {str(getattr(skill, "name", "")) for skill in getattr(engine, "_active_skills", ()) or ()}
    entries = rank_skills(text, [(name, desc) for name, desc in entries if name not in active])
    if not entries:
        return
    candidates = [{"name": name, "desc": desc[:160]} for name, desc in entries]
    decision = await _eval_traced(
        engine,
        "skill.pin",
        {
            "user_text": text,
            "candidates": candidates,
            "skill_names": [name for name, _desc in entries[:10]],
        },
    )
    if not decision_can_apply("skill.pin", decision, settings):
        return
    pin = str((decision.extras or {}).get("pin") or "")
    if pin and pin in {name for name, _desc in entries}:
        engine._skill_pin = pin  # type: ignore[attr-defined]


async def maybe_suggest_loop_wrap(
    engine: Any, *, tool_results: list[Any] | None = None,
    iteration: int = 0, on_event: Any | None = None,
) -> str:
    """Evaluate fresh evidence at most twice, delivering at most one useful suggestion."""
    if engine is None or is_child_session(engine):
        return ""
    if not _jev_connected(engine):
        return ""
    previous = getattr(engine, "_loop_wrap", None)
    if isinstance(previous, Mapping) and previous.get("next") != "continue":
        return ""
    if int(getattr(engine, "_jev_loop_evaluations", 0) or 0) >= 2:
        return ""
    if len(tool_results or []) <= int(getattr(engine, "_jev_loop_result_count", 0) or 0):
        return ""
    settings = live_jev_settings(getattr(engine, "config", None))
    if gate_for_pack("loop.wrap", settings) == "off":
        return ""
    from excelmanus.system_one.evidence import loop_state

    state = loop_state(engine, tool_results or [], iteration=iteration)
    if state is None:
        return ""
    engine._jev_loop_evaluations = int(getattr(engine, "_jev_loop_evaluations", 0) or 0) + 1
    engine._jev_loop_result_count = len(tool_results or [])
    decision = await _eval_traced(
        engine,
        "loop.wrap",
        state,
        on_event=on_event,
    )
    extras = dict(decision.extras or {})
    if not decision_can_apply("loop.wrap", decision, settings):
        engine._loop_wrap = {**extras, "applied": False}  # type: ignore[attr-defined]
        return ""
    engine._loop_wrap = {**extras, "applied": True}  # type: ignore[attr-defined]
    return {
        "continue": "",
        "retry": "请检查上一步是否需要纠正；只有确认允许且尚未提交的操作才可重试。",
        "ask_user": "请检查是否缺少用户必须补充的信息，必要时提出一个简短问题。",
        "stop": "现有结果可能已覆盖用户请求。请对照实际证据收尾；发现缺项时继续处理，勿仅凭此建议宣告完成。",
    }.get(str(extras.get("next") or "continue"), "")


async def maybe_advise_after_tools(
    engine: Any, tool_results: list[Any], *, breaker_triggered: bool = False,
    iteration: int = 0, on_event: Any | None = None,
    failed_tool_call_id: str = "",
) -> str:
    """Consume Jev advice at the completed tool-batch boundary, before the next LLM call."""
    if is_child_session(engine) or not tool_results or not _jev_connected(engine):
        return ""
    if _has_pending_interaction(engine):
        return ""
    if any(getattr(item, "error", "") in {"CANCELLED", "USER_EDIT_PENDING"} for item in tool_results):
        return ""
    previous = getattr(engine, "_recovery_hint", None)
    if isinstance(previous, dict) and previous.get("delivered"):
        following = tool_results[int(previous.get("result_count") or 0):]
        if following:
            from excelmanus.system_one.adapter import recovery_state_from_engine

            facts = recovery_state_from_engine(engine, following, breaker_triggered=breaker_triggered)
            previous["following_success"] = bool(following[-1].success)
            previous["same_failure_repeated"] = any(
                item.get("error_code") in previous.get("error_codes", []) for item in facts.get("error_facts", [])
            )
            previous["different_failure"] = bool(
                following and not previous["following_success"]
                and not previous["same_failure_repeated"]
            )
    if not tool_results[-1].success:
        advice = await maybe_suggest_recovery(
            engine, tool_results, breaker_triggered=breaker_triggered, on_event=on_event,
        )
    else:
        # Successful reads and writes are facts for the primary agent, not a
        # reason for an auxiliary model to decide when the task should end.
        return ""
    if advice and not breaker_triggered:
        annotate = getattr(engine._memory, "annotate_tool_result", None)
        delivered = bool(callable(annotate) and annotate(
            failed_tool_call_id, source="Jev 错误恢复", text=advice,
        ))
        # Missing provenance is not a reason to fabricate a hidden user request.
        if delivered and isinstance(getattr(engine, "_recovery_hint", None), dict):
            engine._recovery_hint["delivered"] = True
            hint = engine._recovery_hint
            record_host_effect(
                engine, "recovery.next_step", action=str(hint["next"]),
                impact="恢复建议已附于对应失败工具结果，尚不能确认是否遵循",
                delivered=True, source=str(hint["source"]), on_event=on_event,
            )
    return advice if breaker_triggered else ""


def _tool_call_lookup(messages: list[Any]) -> dict[str, tuple[str, dict[str, Any]]]:
    import json

    found: dict[str, tuple[str, dict[str, Any]]] = {}
    for msg in messages:
        if not isinstance(msg, Mapping) or msg.get("role") != "assistant":
            continue
        for call in msg.get("tool_calls") or ():
            if not isinstance(call, Mapping):
                continue
            call_id = str(call.get("id") or "")
            fn = call.get("function") if isinstance(call.get("function"), Mapping) else {}
            name = str((fn or {}).get("name") or call.get("name") or "")
            raw_args = (fn or {}).get("arguments") or call.get("arguments") or {}
            args: dict[str, Any] = {}
            if isinstance(raw_args, Mapping):
                args = dict(raw_args)
            elif isinstance(raw_args, str) and raw_args.strip():
                try:
                    parsed = json.loads(raw_args)
                except (json.JSONDecodeError, ValueError):
                    parsed = {}
                if isinstance(parsed, Mapping):
                    args = dict(parsed)
            if call_id and name:
                found[call_id] = (name, args)
    return found


def _prune_stub(tool_name: str, arguments: Mapping[str, Any] | None) -> str:
    pointer = _pointer_text(tool_name, arguments)
    return pointer.replace("重调同参可取回", "已省略；重调同参可取回")


async def maybe_prune_observations(
    engine: Any, memory: Any, *, protected_indices: set[int] | None = None,
) -> int:
    """片 P：L1 机械 pruner 之前。最近 K / 错误 / pending / 不可重放永不剪。"""
    if engine is None or memory is None or is_child_session(engine):
        return 0
    if not _jev_connected(engine):
        return 0
    settings = live_jev_settings(getattr(engine, "config", None))
    if gate_for_pack("observation.prune", settings) == "off":
        return 0
    msgs = list(getattr(memory, "messages", None) or [])
    tool_idxs = [i for i, msg in enumerate(msgs) if isinstance(msg, Mapping) and msg.get("role") == "tool"]
    if _has_pending_interaction(engine):
        if protected_indices is not None:
            protected_indices.update(tool_idxs)
        return 0
    protected = set(tool_idxs[-T_PRUNE_KEEP:]) if tool_idxs else set()
    if protected_indices is not None:
        protected_indices.update(protected)
    lookup = _tool_call_lookup(msgs)
    from excelmanus.system_one.adapter import last_user_text

    pruned = 0
    scanned = 0
    user_text = last_user_text(engine)
    applied_ok = decision_is_applied("observation.prune", settings)
    for idx in tool_idxs:
        if idx in protected:
            continue
        msg = msgs[idx]
        content = msg.get("content")
        if not isinstance(content, str) or len(content) <= T_BIG:
            continue
        if content.startswith("spill:") or "已省略" in content or "已收起" in content:
            continue
        import re

        if re.search(r'"status"\s*:\s*"error"', content):
            if protected_indices is not None:
                protected_indices.add(idx)
            continue
        call_id = str(msg.get("tool_call_id") or "")
        tool_name, args = lookup.get(call_id, (str(msg.get("name") or ""), {}))
        if not tool_name or not _re_fetchable(tool_name, args):
            if protected_indices is not None:
                protected_indices.add(idx)
            continue
        if scanned >= T_PRUNE_BATCH:
            continue
        scanned += 1
        decision = await _eval_traced(
            engine,
            "observation.prune",
            {
                "user_text": user_text,
                "tool": {"name": tool_name, "args": args},
                "result_chars": len(content),
                "result_head": content[:300],
                "success": True,
                "re_fetchable": True,
            },
        )
        if not applied_ok or not decision.applied:
            continue
        if not (decision.extras or {}).get("prune"):
            if protected_indices is not None:
                protected_indices.add(idx)
            continue
        from excelmanus.engine_core.spill import SpillStore

        root = getattr(getattr(engine, "config", None), "workspace_root", None)
        if not root:
            if protected_indices is not None:
                protected_indices.add(idx)
            continue
        locator = SpillStore(str(root)).put(content)
        stub = f"[已省略旧结果；原始结果可用 read_text_file(file_path='{locator}') 取回]"
        msg["content"] = stub
        emit = getattr(memory, "_emit_replace", None)
        if callable(emit):
            emit(msg, kind="tool/result")
        pruned += 1
        record_host_effect(
            engine, "observation.prune", action="prune", changed=True,
            impact=f"旧结果由 {len(content)} 字符缩为 {len(stub)} 字符，保留固定取回入口",
        )
    return pruned


def approval_gate_action(decision: Decision | None) -> str:
    """片 E：applied deny/auto 才改变路径；其余一律 ask（今天的 create_pending）。"""
    if decision is None or not decision.applied:
        return "ask"
    if decision.kind == "deny":
        return "deny"
    if decision.kind == "auto":
        return "auto"
    return "ask"


def _stamp_host_approval(decision: Decision, settings: Any) -> Decision:
    applied = bool(decision.applied) and decision_is_applied("approval.tool_call", settings)
    # A Jev ``auto`` answer changes authorization.  Keep deny fail-closed and
    # allow ASK/deny under the normal pack gate, but require the explicit
    # calibration switch before an answer can skip the human approval card.
    auto_calibrated = False
    if applied and decision.kind == "auto":
        try:
            from excelmanus.system_one.calibration import calibration_allows_enforce

            auto_calibrated = calibration_allows_enforce("approval.tool_call", settings)
        except Exception:
            auto_calibrated = False
    if applied and decision.kind == "auto" and not auto_calibrated:
        extras = dict(decision.extras or {})
        extras["auto_blocked"] = "uncalibrated_or_unsigned"
        return Decision(
            kind="ask",
            reason="auto_requires_calibration",
            evaluation=decision.evaluation,
            extras=extras,
            applied=True,
        )
    if applied == decision.applied:
        return decision
    return Decision(
        kind=decision.kind,
        reason=decision.reason,
        evaluation=decision.evaluation,
        extras=dict(decision.extras or {}),
        applied=applied,
    )


async def maybe_jev_approval(
    engine: Any,
    *,
    tool_name: str,
    arguments: Mapping[str, Any] | None,
    code_tier: str | None = None,
) -> Decision | None:
    """片 E：Jev 决策通过确定性门后改变 HookDecision / pending。

    第一期只覆盖 ``run_shell`` / ``delete_file`` / 非 Green ``run_code``。
    不可达 fail-closed 为 ASK（绝不是 ALLOW）。child / never / Green / 只读 / Tier B 不评估。
    """
    if engine is None or is_child_session(engine):
        return None
    if tool_name not in _E_APPROVAL_TOOLS:
        return None
    from excelmanus.security.policy import resolve_approval_policy
    from excelmanus.tools.policy import READ_ONLY_SAFE_TOOLS

    if resolve_approval_policy(engine) == "never":
        return None
    if tool_name in READ_ONLY_SAFE_TOOLS:
        return None
    if str(code_tier or "").upper() == "GREEN":
        return None
    if not _jev_connected(engine):
        return None
    config = live_jev_config(getattr(engine, "config", None))
    settings = live_jev_settings(config)
    if gate_for_pack("approval.tool_call", settings) == "off":
        return None
    root = getattr(config, "workspace_root", None) if config is not None else None
    if is_known_dangerous_call(tool_name, arguments, str(root) if root else None):
        applied = decision_is_applied("approval.tool_call", settings)
        decision = Decision(kind="deny", reason="known_dangerous", applied=applied)
        current = live_jev_settings(config)
        if current.experimental_enabled and gate_for_pack("approval.tool_call", current) != "off":
            record_jev_decision(
                pack_id="approval.tool_call",
                gate="enforce" if applied else gate_for_pack("approval.tool_call", current),
                decision=decision,
            )
        emit_jev_trace(engine, decision, pack_id="approval.tool_call")
        return decision
    from excelmanus.system_one.adapter import approval_state_from_engine as _approval_state

    state = _approval_state(
        engine,
        tool_name=tool_name,
        arguments=arguments,
        code_tier=code_tier,
    )
    decision = await _eval_traced(engine, "approval.tool_call", state)
    return _stamp_host_approval(decision, settings)


# Import compatibility for callers migrating from the old helper name.
maybe_shadow_approval = maybe_jev_approval
