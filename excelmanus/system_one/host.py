"""Host 接线：回合入口评估、审批 shadow/E、L4 wire 收窄、回合末 UI 面、F/G/M/P。

缺省不改 envelope.tools / 不发 ui_hint / 不改目录 / 不改循环 / 不冷修剪 / 不改审批结果。
仅当对应闸为 enforce 且标定已签字时才 applied。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from excelmanus.events import EventType, ToolCallEvent
from excelmanus.logger import get_logger
from excelmanus.system_one.adapter import exposure_state_from_engine
from excelmanus.system_one.context import is_child_session
from excelmanus.system_one.log import record_shadow
from excelmanus.system_one.packs import PROFILE_NAMES, resolve_profile_tools
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
from excelmanus.system_one.trace import emit_jev_trace
from excelmanus.tools.runtime import RUN_CODE_NAME, schema_tool_name

logger = get_logger("system_one.host")
_MAX_OBSERVATION_EVALUATIONS_PER_TURN = 3

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
    from excelmanus.system_one import evaluate

    config = live_jev_config(getattr(engine, "config", None))
    try:
        decision = await evaluate(pack_id, state, config=config)
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
            gate = "shadow"
        record_shadow(pack_id=pack_id, gate=gate, decision=decision)
        emit_jev_trace(engine, decision, pack_id=pack_id, on_event=on_event)
        return decision
    emit_jev_trace(engine, decision, pack_id=pack_id, on_event=on_event)
    return decision


def _jev_connected(engine: Any) -> bool:
    return jev_is_active(live_jev_settings(getattr(engine, "config", None)))


def clear_turn_exposure(engine: Any) -> None:
    engine._turn_exposure = None  # type: ignore[attr-defined]
    engine._skill_pin = None  # type: ignore[attr-defined]
    engine._skill_pin_evaluated = False  # type: ignore[attr-defined]
    engine._loop_wrap = None  # type: ignore[attr-defined]
    engine._jev_observation_evaluations = 0  # type: ignore[attr-defined]


def clear_turn_present_as(engine: Any) -> None:
    engine._turn_present_as = None  # type: ignore[attr-defined]


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
    """L4 用来 ∩ 的 sticky profile。闸未 applied / child / 缺失 → full。"""
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


def narrow_exposure_schemas(
    engine: Any,
    schemas: Sequence[Mapping[str, Any]] | Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """catalog.tools ∩ PROFILE[sticky]。full / 未 applied 原样返回。

    先 ``collapse_schemas(present_as)`` 再求交。code 坍成 ``{run_code}`` 时
    交集保持 ``run_code``，避免 inspect 把逃生舱掏空。
    """
    rows = [dict(item) for item in schemas]
    names = [schema_tool_name(item) for item in rows]
    remaining = {name for name in names if name}
    if remaining <= {RUN_CODE_NAME}:
        return rows
    profile = turn_wire_profile(engine)
    if profile == "full":
        return rows
    allowed = resolve_profile_tools(profile, names)
    return [item for item in rows if schema_tool_name(item) in allowed]


async def maybe_record_turn_exposure(
    engine: Any,
    user_text: str,
    *,
    on_event: Any | None = None,
) -> None:
    """片 I：控制命令之后、USER_PROMPT_SUBMIT 落定后。L4 是否收窄看 applied。

    片 K/L 复用本评估的 mode_hint / present_hint；未签字不发卡、不写回合覆盖。
    """
    clear_turn_exposure(engine)
    clear_turn_present_as(engine)
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
        "present_hint": extras.get("present_hint"),
        "conf": float(extras.get("domain_confidence") or 0.0),
        "latency_ms": float(getattr(evaluation, "latency_ms", 0.0) or 0.0) if evaluation else 0.0,
        "wire_narrow": wire_narrow,
        "gate": "enforce" if applied else "shadow",
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
    maybe_apply_turn_present_as(engine)
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


def maybe_apply_turn_present_as(engine: Any) -> None:
    """片 L：仅 write + 签字 enforce 才写回合覆盖。不改持久偏好。"""
    if is_child_session(engine):
        return
    chat = str(getattr(engine, "_current_chat_mode", "write") or "write")
    if chat != "write":
        return
    settings = live_jev_settings(getattr(engine, "config", None))
    if not jev_is_active(settings) or not flag_is_applied(settings.present_as_auto, settings, pack_id="exposure.turn"):
        return
    record = getattr(engine, "_turn_exposure", None)
    if not isinstance(record, Mapping):
        return
    hint = record.get("present_hint")
    if hint not in {"native", "code"}:
        return
    engine._turn_present_as = hint  # type: ignore[attr-defined]
    engine._tools_cache = None  # type: ignore[attr-defined]


def maybe_enqueue_mode_switch(engine: Any, *, on_event: Any | None = None) -> None:
    """片 K：高置信失配才发卡。Jev 永不直接改 chat_mode。本回合不阻断 LLM。"""
    if is_child_session(engine):
        return
    settings = live_jev_settings(getattr(engine, "config", None))
    if not jev_is_active(settings) or not flag_is_applied(settings.mode_hint, settings, pack_id="exposure.turn"):
        return
    record = getattr(engine, "_turn_exposure", None)
    if not isinstance(record, Mapping):
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
    if shape == "pointer":
        coverage.update({"declared": True, "truncated": True, "kind": "truncated"})
        return replace(
            result,
            model_text=_pointer_text(tool_name, arguments),
            truncated=True,
            coverage=coverage,
        )
    if shape == "truncate":
        coverage.update({"declared": True, "truncated": True, "kind": "truncated"})
        return replace(
            result,
            model_text=_head_tail(text, T_TIGHT_CHARS),
            truncated=True,
            coverage=coverage,
        )
    if shape == "spill":
        root = getattr(getattr(engine, "config", None), "workspace_root", None)
        if not root:
            coverage.update({"declared": True, "truncated": True, "kind": "truncated"})
            return replace(
                result,
                model_text=_head_tail(text, T_TIGHT_CHARS),
                truncated=True,
                coverage=coverage,
            )
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
    return _apply_observation_shape(
        result,
        shape,
        tool_name=tool_name,
        arguments=args,
        engine=engine,
    )


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
    state = getattr(engine, "_state", None)
    failures = int(getattr(state, "last_failure_count", 0) or 0) if state is not None else 0
    successes = int(getattr(state, "last_success_count", 0) or 0) if state is not None else 0
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
    """片 O：reply 已产出、done 之前。未签字只 shadow；applied 才发 UI_HINT。失败静默。"""
    if engine is None or is_child_session(engine):
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
    decision = await _eval_traced(engine, "ui.surface", state, on_event=on_event)
    if not decision_can_apply("ui.surface", decision, settings):
        return
    extras = decision.extras or {}
    surface = str(extras.get("surface") or "stay")
    if surface not in _UI_SURFACES:
        surface = "stay"
    candidates = [str(item) for item in (state.get("candidate_files") or []) if item][:3]
    file_path = candidates[0] if candidates else ""
    file_b = candidates[1] if len(candidates) > 1 else ""
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
    except Exception:
        logger.debug("ui_hint emit failed; continuing turn", exc_info=True)


def _has_at_mention(text: str) -> bool:
    try:
        from excelmanus.mentions.parser import MentionParser

        return bool(MentionParser.parse(text or "").mentions)
    except Exception:
        return False


def should_skip_skill_catalog_snapshot(engine: Any) -> bool:
    """片 M：高置信寒暄且无图/无 pending/无 @ 才跳过目录快照。未签字不跳。"""
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
    """片 F：高置信才置顶/标 likely match。不得 skill()。未签字目录不变。"""
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

    candidates = [{"name": name, "desc": desc[:80]} for name, desc in entries[:3]]
    decision = await _eval_traced(
        engine,
        "skill.pin",
        {
            "user_text": last_user_text(engine),
            "candidates": candidates,
            "skill_names": [name for name, _desc in entries[:10]],
        },
    )
    if not decision_can_apply("skill.pin", decision, settings):
        return
    pin = str((decision.extras or {}).get("pin") or "")
    if pin and pin in {name for name, _desc in entries}:
        engine._skill_pin = pin  # type: ignore[attr-defined]


async def maybe_suggest_loop_wrap(engine: Any) -> None:
    """Legacy explicit hook for pack G.

    The main loop does not call this helper: its result had no consumer and
    evaluating it on every step only added latency and cost. Keep the helper
    for offline calibration/tests until a real loop consumer exists.
    """
    if engine is None or is_child_session(engine):
        return
    if not _jev_connected(engine):
        return
    settings = live_jev_settings(getattr(engine, "config", None))
    if gate_for_pack("loop.wrap", settings) == "off":
        return
    from excelmanus.system_one.adapter import last_user_text

    state = getattr(engine, "_state", None)
    decision = await _eval_traced(
        engine,
        "loop.wrap",
        {
            "user_text": last_user_text(engine),
            "iteration": int(getattr(state, "last_iteration_count", 0) or 0) if state else 0,
            "consecutive_failures": int(getattr(engine, "_last_failure_count", 0) or 0),
            "last_tools": list(getattr(engine, "_exposure_last_tools", None) or ())[:10],
            "last_error": "",
        },
    )
    extras = dict(decision.extras or {})
    if not decision_can_apply("loop.wrap", decision, settings):
        engine._loop_wrap = {**extras, "applied": False}  # type: ignore[attr-defined]
        return
    engine._loop_wrap = {**extras, "applied": True}  # type: ignore[attr-defined]


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


async def maybe_prune_observations(engine: Any, memory: Any) -> int:
    """片 P：L1 机械 pruner 之前。未签字不剪。最近 K / 错误 / pending / 不可重放永不剪。"""
    if engine is None or memory is None or is_child_session(engine):
        return 0
    if _has_pending_interaction(engine):
        return 0
    if not _jev_connected(engine):
        return 0
    settings = live_jev_settings(getattr(engine, "config", None))
    if gate_for_pack("observation.prune", settings) == "off":
        return 0
    msgs = list(getattr(memory, "messages", None) or [])
    tool_idxs = [i for i, msg in enumerate(msgs) if isinstance(msg, Mapping) and msg.get("role") == "tool"]
    protected = set(tool_idxs[-T_PRUNE_KEEP:]) if tool_idxs else set()
    lookup = _tool_call_lookup(msgs)
    from excelmanus.system_one.adapter import last_user_text

    pruned = 0
    scanned = 0
    user_text = last_user_text(engine)
    applied_ok = decision_is_applied("observation.prune", settings)
    for idx in tool_idxs:
        if scanned >= T_PRUNE_BATCH:
            break
        if idx in protected:
            continue
        msg = msgs[idx]
        content = msg.get("content")
        if not isinstance(content, str) or len(content) <= T_BIG:
            continue
        if content.startswith("spill:") or "已省略" in content or "已收起" in content:
            continue
        if '"status": "error"' in content or '"status":"error"' in content:
            continue
        call_id = str(msg.get("tool_call_id") or "")
        tool_name, args = lookup.get(call_id, (str(msg.get("name") or ""), {}))
        if not tool_name or not _re_fetchable(tool_name, args):
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
        if not applied_ok or not decision.applied or not (decision.extras or {}).get("prune"):
            continue
        stub = _prune_stub(tool_name, args)
        msg["content"] = stub
        emit = getattr(memory, "_emit_replace", None)
        if callable(emit):
            emit(msg, kind="tool/result")
        pruned += 1
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
    if applied == decision.applied:
        return decision
    return Decision(
        kind=decision.kind,
        reason=decision.reason,
        evaluation=decision.evaluation,
        extras=dict(decision.extras or {}),
        applied=applied,
    )


async def maybe_shadow_approval(
    engine: Any,
    *,
    tool_name: str,
    arguments: Mapping[str, Any] | None,
    code_tier: str | None = None,
) -> Decision | None:
    """片 C：create_pending 之前只写影子。片 E：applied 才改变 HookDecision / pending。

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
        record_shadow(
            pack_id="approval.tool_call",
            gate="enforce" if applied else "shadow",
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
