"""阈值、闸合成、题包合成。禁止在 handler 里写裸 noul 阈值。"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from excelmanus.system_one.packs import PROFILE_NAMES, get_pack
from excelmanus.system_one.types import (
    Answer,
    ChoiceAnswer,
    Decision,
    Evaluation,
    GateLevel,
    NoulAnswer,
    ScoreAnswer,
)

# 阈值集中。安全家族偏紧，优化家族 fail-open。
T_DOM = 0.55
T_NEEDS_WRITE = 0.7
T_CODE = 0.8
T_SUGGEST = 0.6
T_CONTEXT = 0.8
T_CONTEXT_MARGIN = 0.15
T_CHAT = 0.85
T_VERBATIM = 0.7
T_BIG = 8000
T_TIGHT_CHARS = 2000
T_ALLOW = 0.99  # 高置信才自动放行；uncertain → ASK
T_DENY = 0.6
T_DESTRUCTIVE = 0.75
T_EXFIL = 0.7
T_BLAST_AUTO = 1.5
T_STICKY_TURNS = 2
T_PRUNE_KEEP = 4
T_PRUNE_IRRELEVANT = 0.35
T_PRUNE_BATCH = 3
T_RECOVERY_NEEDS_USER = 0.7
T_RECOVERY_RETRY = 0.8
_RM_RF = re.compile(
    r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*f\b|\brm\s+-[a-zA-Z]*f[a-zA-Z]*r\b",
    re.IGNORECASE,
)

_DOMAIN_TO_PROFILE: dict[str, str] = {
    "inspect_only": "inspect",
    "spreadsheet_write": "edit",
    "word_doc": "edit",
    "file_code": "file_code",
    "web_lookup": "web",
    "chitchat": "minimal",
    "mixed": "full",
}


@dataclass(frozen=True)
class JevSettings:
    enabled: GateLevel
    exposure: GateLevel
    mode_hint: bool
    observation: GateLevel
    ui_hint: bool
    model: str
    api_key: str | None
    timeout_seconds: float
    calibrated: bool = False
    protocol: str = ""
    base_url: str = ""
    verification: GateLevel = "enforce"
    recovery: GateLevel = "enforce"
    provider_id: str = ""


def noul_of(answer: Answer | None, default: float = 0.5) -> float:
    if isinstance(answer, NoulAnswer):
        return max(0.0, min(1.0, float(answer.noul)))
    return default


def choice_of(answer: Answer | None) -> ChoiceAnswer | None:
    return answer if isinstance(answer, ChoiceAnswer) else None


def score_of(answer: Answer | None) -> ScoreAnswer | None:
    return answer if isinstance(answer, ScoreAnswer) else None


def _legacy_key_and_protocol(config: Any) -> tuple[str | None, str, str, str, str]:
    from excelmanus.system_one.providers import (
        JevProviderRecord,
        record_from_mapping,
        resolve_jev_connection,
    )

    raw_providers = getattr(config, "jev_providers", None)
    records: list[JevProviderRecord] = []
    if isinstance(raw_providers, (list, tuple)):
        for item in raw_providers:
            if isinstance(item, JevProviderRecord):
                records.append(item)
            elif isinstance(item, Mapping):
                parsed = record_from_mapping(item)
                if parsed is not None:
                    records.append(parsed)
    gateway = getattr(config, "ai_gateway_api_key", None)
    typesafe = getattr(config, "typesafe_api_key", None)
    connection = resolve_jev_connection(
        records,
        str(getattr(config, "jev_active_provider", "") or ""),
        typesafe_key=typesafe,
        gateway_key=gateway,
        model_override=str(getattr(config, "jev_model", "") or ""),
    )
    return connection.api_key, connection.protocol, connection.model, connection.base_url, connection.provider_id


def settings_from(config: Any | None = None) -> JevSettings:
    if config is not None:
        key, protocol, model, base_url, provider_id = _legacy_key_and_protocol(config)
        enabled = str(getattr(config, "jev_enabled", "enforce") or "enforce")
        exposure = str(getattr(config, "jev_exposure", "enforce") or "enforce")
        observation = str(getattr(config, "jev_observation", "enforce") or "enforce")
        verification = str(getattr(config, "jev_verification", "enforce") or "enforce")
        return JevSettings(
            enabled=_as_gate(enabled),
            exposure=_as_gate(exposure),
            mode_hint=bool(getattr(config, "jev_mode_hint", True)),
            observation=_as_gate(observation),
            verification=_as_gate(verification),
            recovery=_as_gate(str(getattr(config, "jev_recovery", "enforce") or "enforce")),
            ui_hint=bool(getattr(config, "jev_ui_hint", True)),
            model=model,
            api_key=key,
            timeout_seconds=float(getattr(config, "jev_timeout_seconds", 1.5) or 1.5),
            calibrated=bool(getattr(config, "jev_calibrated", False)),
            protocol=protocol,
            base_url=base_url,
            provider_id=provider_id,
        )
    from excelmanus.config import _parse_bool, _parse_jev_gate, _parse_positive_float
    from excelmanus.settings_runtime import get_setting
    from excelmanus.system_one.providers import (
        GATEWAY_KEY_SETTING,
        JEV_ACTIVE_PROVIDER_SETTING,
        TYPESAFE_KEY_SETTING,
        load_jev_providers,
        resolve_jev_connection,
    )

    records = load_jev_providers()
    connection = resolve_jev_connection(
        records,
        get_setting(JEV_ACTIVE_PROVIDER_SETTING),
        typesafe_key=get_setting(TYPESAFE_KEY_SETTING),
        gateway_key=get_setting(GATEWAY_KEY_SETTING),
        model_override=get_setting("EXCELMANUS_JEV_MODEL"),
    )
    return JevSettings(
        enabled=_parse_jev_gate(get_setting("EXCELMANUS_JEV_ENABLED"), "EXCELMANUS_JEV_ENABLED", "enforce"),  # type: ignore[arg-type]
        exposure=_parse_jev_gate(get_setting("EXCELMANUS_JEV_EXPOSURE"), "EXCELMANUS_JEV_EXPOSURE", "enforce"),  # type: ignore[arg-type]
        mode_hint=_parse_bool(get_setting("EXCELMANUS_JEV_MODE_HINT"), "EXCELMANUS_JEV_MODE_HINT", True),
        observation=_parse_jev_gate(
            get_setting("EXCELMANUS_JEV_OBSERVATION"), "EXCELMANUS_JEV_OBSERVATION", "enforce"
        ),  # type: ignore[arg-type]
        verification=_parse_jev_gate(
            get_setting("EXCELMANUS_JEV_VERIFICATION"), "EXCELMANUS_JEV_VERIFICATION", "enforce"
        ),  # type: ignore[arg-type]
        recovery=_parse_jev_gate(
            get_setting("EXCELMANUS_JEV_RECOVERY"), "EXCELMANUS_JEV_RECOVERY", "enforce"
        ),  # type: ignore[arg-type]
        ui_hint=_parse_bool(get_setting("EXCELMANUS_JEV_UI_HINT"), "EXCELMANUS_JEV_UI_HINT", True),
        model=connection.model,
        api_key=connection.api_key,
        timeout_seconds=min(
            10.0,
            _parse_positive_float(
                get_setting("EXCELMANUS_JEV_TIMEOUT_SECONDS"),
                "EXCELMANUS_JEV_TIMEOUT_SECONDS",
                1.5,
            ),
        ),
        calibrated=_parse_bool(
            get_setting("EXCELMANUS_JEV_CALIBRATED"),
            "EXCELMANUS_JEV_CALIBRATED",
            False,
        ),
        protocol=connection.protocol,
        base_url=connection.base_url,
        provider_id=connection.provider_id,
    )


def _as_gate(value: str) -> GateLevel:
    raw = str(value or "off").strip().lower()
    # ``shadow`` was the pre-rollout observation mode.  Treat persisted legacy
    # values as the only enabled state so an upgrade cannot silently disable
    # an already configured JEV integration.
    if raw == "shadow":
        return "enforce"
    if raw in {"off", "enforce"}:
        return raw  # type: ignore[return-value]
    return "off"


def jev_key_configured(settings: JevSettings) -> bool:
    return bool((settings.api_key or "").strip())


def jev_is_active(settings: JevSettings) -> bool:
    """对话与 loop 是否接入 Jev：总闸开且密钥已配。"""
    return settings.enabled != "off" and jev_key_configured(settings)


def live_jev_config(config: Any | None = None) -> Any:
    """优先读进程内 live config，避免会话持有的副本在设置页关掉后仍评估。"""
    try:
        from excelmanus.api_app_state import get_config

        live = get_config()
    except Exception:
        live = None
    return live if live is not None else config


def live_jev_settings(config: Any | None = None) -> JevSettings:
    return settings_from(live_jev_config(config))


def effective_gate(master: GateLevel, child: GateLevel) -> GateLevel:
    """只有总闸和对应子闸都开启时，该题包才参与并直接生效。"""
    return "enforce" if master == "enforce" and child == "enforce" else "off"


def effective_flag(master: GateLevel, enabled: bool) -> GateLevel:
    return "enforce" if master == "enforce" and enabled else "off"


def gate_for_pack(pack_id: str, settings: JevSettings) -> GateLevel:
    spec = get_pack(pack_id)
    if spec.gate == "master":
        return settings.enabled
    if spec.gate == "exposure":
        return effective_gate(settings.enabled, settings.exposure)
    if spec.gate == "observation":
        return effective_gate(settings.enabled, settings.observation)
    if spec.gate == "verification":
        return effective_gate(settings.enabled, settings.verification)
    if spec.gate == "recovery":
        return effective_gate(settings.enabled, settings.recovery)
    if spec.gate == "ui_hint":
        return effective_flag(settings.enabled, settings.ui_hint)
    return "off"


def decision_is_applied(pack_id: str, settings: JevSettings) -> bool:
    """An enabled pack is live immediately; its deterministic host guard still applies."""
    return gate_for_pack(pack_id, settings) == "enforce"


def decision_can_apply(pack_id: str, decision: Decision, settings: JevSettings) -> bool:
    """Single host-side predicate for an evaluated decision's side effects."""
    return bool(decision.applied) and decision_is_applied(pack_id, settings)


def flag_is_applied(enabled: bool, settings: JevSettings, *, pack_id: str) -> bool:
    """Boolean child switches follow the same master/enabled contract."""
    return effective_flag(settings.enabled, enabled) == "enforce"


def stamp_application(pack_id: str, decision: Decision, settings: JevSettings) -> Decision:
    """Disabled packs cannot affect the host; enabled packs apply immediately."""
    extras = dict(decision.extras)
    if not decision_is_applied(pack_id, settings):
        extras["wire_narrow"] = False
        return Decision(
            kind=decision.kind,
            reason=decision.reason,
            evaluation=decision.evaluation,
            extras=extras,
            applied=False,
        )
    return Decision(
        kind=decision.kind,
        reason=decision.reason,
        evaluation=decision.evaluation,
        extras=decision.extras,
        applied=True,
    )


def synthesize(pack_id: str, evaluation: Evaluation, state: Mapping[str, Any] | None = None) -> Decision:
    spec = get_pack(pack_id)
    state = state or {}
    if spec.pack_id == "context.resolve":
        return _synthesize_context(evaluation, state)
    if spec.pack_id == "exposure.turn":
        return _synthesize_exposure(evaluation, state)
    if spec.pack_id == "observation.shape":
        return _synthesize_observation(evaluation, state)
    if spec.pack_id == "ui.surface":
        return _synthesize_ui(evaluation, state)
    if spec.pack_id == "approval.tool_call":
        return _synthesize_approval(evaluation)
    if spec.pack_id == "skill.pin":
        return _synthesize_skill_pin(evaluation, state)
    if spec.pack_id == "loop.wrap":
        return _synthesize_loop_wrap(evaluation)
    if spec.pack_id == "observation.prune":
        return _synthesize_prune(evaluation, state)
    if spec.pack_id == "mutation.verify":
        return _synthesize_mutation_verify(evaluation, state)
    if spec.pack_id == "recovery.next_step":
        return _synthesize_recovery(evaluation, state)
    return Decision.noop("unknown_pack")


def _synthesize_context(evaluation: Evaluation, state: Mapping[str, Any]) -> Decision:
    def pick(qid: str, fallback: str) -> str:
        answer = choice_of(evaluation.answers.get(qid))
        if answer is None or not T_CONTEXT <= answer.confidence <= 1:
            return fallback
        if answer.probabilities:
            # Confidence describes the distribution; it is not P(selected).
            selected = answer.probabilities.get(answer.choice)
            if selected is None or any(not 0 <= v <= 1 for v in answer.probabilities.values()):
                return fallback
            others = [v for k, v in answer.probabilities.items() if k != answer.choice]
            if others and selected - max(others) < T_CONTEXT_MARGIN:
                return fallback
        return answer.choice

    def candidate(choice: str, prefix: str, field: str) -> dict[str, Any] | None:
        if choice not in {f"{prefix}{i}" for i in range(10)}:
            return None
        rows = state.get(field) or []
        index = int(choice[1:])
        if index >= len(rows) or not isinstance(rows[index], Mapping):
            return None
        return dict(rows[index])

    workspace = pick("workspace", "ask")
    workspace_candidate = candidate(workspace, "w", "workspaces")
    if workspace_candidate:
        workspace = "existing"
    elif workspace not in {"current", "new_blank", "ask", "none"}:
        workspace = "ask"
    if workspace == "current" and not state.get("current_workspace"):
        workspace = "ask"
    target = pick("target", "ask")
    target_candidate = candidate(target, "t", "targets")
    if target_candidate:
        target = "resolved"
    elif target not in {"ask", "none"}:
        target = "ask"
    edit_intent = pick("edit_intent", "unclear")
    if edit_intent not in {"specified", "from_context", "unclear", "no_edit"}:
        edit_intent = "unclear"
    # Creating a blank workspace cannot satisfy a request that needs an existing file.
    if workspace == "new_blank" and target != "none":
        workspace = "ask"
    explicit = [row for row in state.get("targets", []) if row.get("source") == "explicit_mention"]
    if explicit and target_candidate and target_candidate not in explicit:
        target, target_candidate = "ask", None
    current_id = (state.get("current_workspace") or {}).get("id")
    if workspace_candidate and workspace_candidate.get("id") == current_id:
        workspace = "current"
    if workspace == "existing":
        # The target candidates are scoped to the CURRENT workspace only.
        target, target_candidate = "ask", None
    has_columns = bool(state.get("columns"))
    column = pick("column", "ask" if has_columns else "none")
    column_candidate = candidate(column, "c", "columns")
    if column_candidate:
        column = "matched"
    elif column not in {"ask", "none"}:
        column = "ask" if has_columns else "none"
    if workspace == "existing" or target == "none":
        # Columns live in the current workspace's files; a different workspace or
        # a request with no spreadsheet target cannot carry a column suggestion.
        column, column_candidate = "none", None
    read = pick("read", "none")
    if read not in {"overview", "selection", "column_sample", "formulas", "none"}:
        read = "none"
    suggestion = None
    if target_candidate and read != "none":
        from excelmanus.system_one.sheet_advice import read_suggestion

        suggestion = read_suggestion(target_candidate, column_candidate, read)
    confidence = {}
    for qid in ("workspace", "target", "edit_intent", "column", "read"):
        answer = choice_of(evaluation.answers.get(qid))
        confidence[qid] = round(answer.confidence, 3) if answer and 0 <= answer.confidence <= 1 else 0.0
    next_step = "clarify" if "ask" in {workspace, target} or edit_intent == "unclear" else (
        "inspect_candidate" if suggestion else (
            "inspect_target" if target == "resolved" else "continue"
        )
    )
    return Decision(
        kind="noop", reason="context_advice", evaluation=evaluation,
        extras={"workspace": workspace, "target": target, "edit_intent": edit_intent,
                "workspace_candidate": workspace_candidate, "target_candidate": target_candidate,
                "column": column, "column_candidate": column_candidate,
                "read": read, "read_suggestion": suggestion,
                "confidence": confidence, "next": next_step},
    )


def _synthesize_exposure(evaluation: Evaluation, state: Mapping[str, Any]) -> Decision:
    answers = evaluation.answers
    domain = choice_of(answers.get("domain"))
    chat_mode = str(state.get("chat_mode") or "write")
    if (
        domain is None
        or domain.choice == "mixed"
        or domain.choice not in _DOMAIN_TO_PROFILE
        or domain.confidence < T_DOM
    ):
        profile = "full"
        reason = "low_confidence_or_mixed"
    else:
        profile = _DOMAIN_TO_PROFILE[domain.choice]
        reason = f"domain:{domain.choice}"
    if noul_of(answers.get("needs_write")) >= T_NEEDS_WRITE and profile == "inspect":
        profile = "edit"
        reason = "needs_write_promoted"
    mode = choice_of(answers.get("mode_mismatch"))
    if (
        mode is not None
        and mode.choice == "suggest_write"
        and chat_mode != "write"
        and profile in {"inspect", "minimal"}
    ):
        profile = "edit"
        reason = "mode_mismatch_write_fallback"
    if profile not in PROFILE_NAMES:
        profile = "full"
    mode_hint = mode.choice if mode is not None else "keep"
    mode_conf = float(mode.confidence) if mode is not None else 0.0
    return Decision(
        kind="noop",
        reason=reason,
        evaluation=evaluation,
        extras={
            "profile": profile,
            "mode_hint": mode_hint,
            "mode_hint_confidence": mode_conf,
            "wire_narrow": False,
            "domain": domain.choice if domain is not None else "mixed",
            "domain_confidence": domain.confidence if domain is not None else 0.0,
            "is_chitchat": noul_of(answers.get("is_chitchat")),
        },
        applied=False,
    )


def _synthesize_observation(evaluation: Evaluation, state: Mapping[str, Any]) -> Decision:
    answers = evaluation.answers
    if state.get("success") is False:
        return Decision(kind="noop", reason="keep_error", evaluation=evaluation, extras={"shape": "keep"})
    if noul_of(answers.get("user_wants_verbatim")) >= T_VERBATIM:
        return Decision(kind="noop", reason="verbatim", evaluation=evaluation, extras={"shape": "keep"})
    shape = choice_of(answers.get("shape"))
    chosen = shape.choice if shape is not None and shape.choice in {"keep", "truncate", "spill", "pointer"} else "keep"
    if shape is None or (shape.confidence < T_SUGGEST and chosen != "keep"):
        chosen = "keep"
        reason = "low_confidence"
    else:
        reason = f"shape:{chosen}"
    re_fetchable = bool(state.get("re_fetchable"))
    spillable = bool(state.get("spillable"))
    if chosen == "pointer" and not re_fetchable:
        chosen = "spill" if spillable else "truncate"
        reason = "pointer_downgraded"
    if chosen == "spill" and not spillable:
        chosen = "truncate"
        reason = "spill_downgraded"
    return Decision(
        kind="noop",
        reason=reason,
        evaluation=evaluation,
        extras={"shape": chosen},
        applied=False,
    )


def _synthesize_ui(evaluation: Evaluation, state: Mapping[str, Any]) -> Decision:
    surface = choice_of(evaluation.answers.get("surface"))
    chosen = surface.choice if surface is not None else "stay"
    if surface is None or surface.confidence < T_SUGGEST:
        return Decision(
            kind="noop",
            reason="low_confidence",
            evaluation=evaluation,
            extras={"surface": "stay", "suppress_heuristic": False},
        )
    suppress = chosen == "stay" and surface.confidence >= T_CODE
    return Decision(
        kind="noop",
        reason=f"surface:{chosen}",
        evaluation=evaluation,
        extras={"surface": chosen, "suppress_heuristic": suppress},
        applied=False,
    )


def _synthesize_approval(evaluation: Evaluation) -> Decision:
    answers = evaluation.answers
    action = choice_of(answers.get("action"))
    destructive = noul_of(answers.get("destructive"))
    exfil = noul_of(answers.get("exfiltrating"))
    blast = score_of(answers.get("blast_radius"))
    blast_score = blast.score if blast is not None else 99.0
    if destructive >= T_DESTRUCTIVE or exfil >= T_EXFIL:
        return Decision(kind="deny", reason="destructive_or_exfil", evaluation=evaluation)
    if action is not None and action.choice == "deny" and action.confidence >= T_DENY:
        return Decision(kind="deny", reason="action_deny", evaluation=evaluation)
    if (
        action is not None
        and action.choice == "allow"
        and action.confidence >= T_ALLOW
        and blast_score < T_BLAST_AUTO
    ):
        return Decision(kind="auto", reason="allow_high_confidence", evaluation=evaluation)
    return Decision(kind="ask", reason="uncertain", evaluation=evaluation)


def _synthesize_skill_pin(evaluation: Evaluation, state: Mapping[str, Any]) -> Decision:
    needs = noul_of(evaluation.answers.get("needs_skill"))
    pick = choice_of(evaluation.answers.get("pick"))
    candidates = [
        str(item.get("name") or "")
        for item in (state.get("candidates") or [])
        if isinstance(item, Mapping) and item.get("name")
    ]
    index_map = {"first": 0, "second": 1, "third": 2}
    pin = ""
    if pick is not None and pick.choice in index_map:
        idx = index_map[pick.choice]
        if 0 <= idx < len(candidates):
            pin = candidates[idx]
    elif pick is not None and pick.choice in set(candidates):
        pin = pick.choice
    low = (
        needs < T_CODE
        or pick is None
        or pick.choice in {"", "none"}
        or pick.confidence < T_SUGGEST
        or not pin
    )
    if low:
        return Decision(
            kind="noop",
            reason="low_confidence",
            evaluation=evaluation,
            extras={"pin": "", "needs_skill": needs},
        )
    return Decision(
        kind="noop",
        reason=f"pin:{pin}",
        evaluation=evaluation,
        extras={"pin": pin, "needs_skill": needs},
    )


def _synthesize_loop_wrap(evaluation: Evaluation) -> Decision:
    nxt = choice_of(evaluation.answers.get("next"))
    chosen = nxt.choice if nxt is not None else "continue"
    if nxt is None or nxt.confidence < T_SUGGEST or chosen not in {
        "continue", "retry", "ask_user", "stop",
    }:
        return Decision(
            kind="noop",
            reason="low_confidence",
            evaluation=evaluation,
            extras={
                "next": "continue",
                "done_enough": noul_of(evaluation.answers.get("done_enough")),
            },
        )
    return Decision(
        kind="noop",
        reason=f"next:{chosen}",
        evaluation=evaluation,
        extras={
            "next": chosen,
            "next_confidence": float(nxt.confidence),
            "done_enough": noul_of(evaluation.answers.get("done_enough")),
            "needs_more_context": noul_of(evaluation.answers.get("needs_more_context")),
        },
    )


def _synthesize_prune(evaluation: Evaluation, state: Mapping[str, Any]) -> Decision:
    if state.get("success") is False or not state.get("re_fetchable"):
        return Decision(
            kind="noop",
            reason="keep_protected",
            evaluation=evaluation,
            extras={"prune": False},
        )
    relevant = noul_of(evaluation.answers.get("still_relevant"))
    prune = relevant <= T_PRUNE_IRRELEVANT
    return Decision(
        kind="noop",
        reason="prune" if prune else "keep",
        evaluation=evaluation,
        extras={"prune": prune, "still_relevant": relevant},
    )


def _synthesize_mutation_verify(evaluation: Evaluation, state: Mapping[str, Any]) -> Decision:
    satisfied = noul_of(evaluation.answers.get("satisfied"))
    scope_ok = noul_of(evaluation.answers.get("scope_ok"), default=0.5)
    next_answer = choice_of(evaluation.answers.get("next"))
    next_action = next_answer.choice if next_answer is not None else "none"
    if next_action not in {"none", "inspect_more", "ask_user"}:
        next_action = "none"
    facts = state.get("verification_facts")
    facts = facts if isinstance(facts, Mapping) else {}
    # Deterministic evidence outranks the evaluator's optimistic answer.  A
    # failed write, a sampled/truncated read-back, a mismatch, or a version
    # conflict always needs a bounded read-only follow-up before completion.
    evidence_incomplete = bool(
        facts.get("has_incomplete_evidence")
        or facts.get("mismatch_count")
        or facts.get("evidence_truncated")
        or facts.get("has_version_conflict")
        or (
            int(facts.get("write_operation_count") or 0) > 0
            and int(facts.get("write_evidence_count") or 0) == 0
        )
    )
    if satisfied < T_SUGGEST or scope_ok < T_SUGGEST:
        return Decision(
            kind="noop",
            reason="low_confidence_or_incomplete_evidence",
            evaluation=evaluation,
            extras={
                "satisfied": satisfied,
                "scope_ok": scope_ok,
                "next": "inspect_more",
            },
        )
    if evidence_incomplete:
        return Decision(
            kind="noop",
            reason="deterministic_evidence_requires_followup",
            evaluation=evaluation,
            extras={
                "satisfied": satisfied,
                "scope_ok": scope_ok,
                "next": "inspect_more",
                "next_confidence": float(next_answer.confidence) if next_answer is not None else 0.0,
            },
        )
    if next_answer is None or next_answer.confidence < T_SUGGEST:
        next_action = "none" if satisfied >= T_CODE and scope_ok >= T_CODE else "inspect_more"
    return Decision(
        kind="noop",
        reason=f"next:{next_action}",
        evaluation=evaluation,
        extras={
            "satisfied": satisfied,
            "scope_ok": scope_ok,
            "next": next_action,
            "next_confidence": float(next_answer.confidence) if next_answer is not None else 0.0,
        },
    )


def _synthesize_recovery(evaluation: Evaluation, state: Mapping[str, Any]) -> Decision:
    next_answer = choice_of(evaluation.answers.get("next"))
    retryable = noul_of(evaluation.answers.get("retryable"))
    needs_user = noul_of(evaluation.answers.get("needs_user"))
    valid = next_answer is not None and next_answer.choice in {"retry", "inspect_more", "ask_user", "stop"}
    action = next_answer.choice if valid and next_answer is not None else "none"
    reason = f"next:{action}"
    if not valid or next_answer is None or not (T_SUGGEST <= next_answer.confidence <= 1.0):
        action, reason = "none", "low_confidence"
    elif state.get("breaker_triggered"):
        action, reason = "stop", "breaker_stopped"
    elif any(
        item.get("failure_class") in {"permission_denied", "approval_denied", "approval_timeout", "blocked"}
        for item in state.get("error_facts", []) if isinstance(item, Mapping)
    ):
        action, reason = "stop", "permission_boundary"
    elif needs_user >= T_RECOVERY_NEEDS_USER:
        action, reason = "ask_user", "needs_user"
    elif action == "retry" and (not state.get("safe_to_retry") or retryable < T_RECOVERY_RETRY):
        action, reason = "inspect_more", "retry_not_evidenced"
    return Decision(
        kind="noop",
        reason=reason,
        evaluation=evaluation,
        extras={
            "next": action,
            "retryable": retryable,
            "needs_user": needs_user,
            "next_confidence": float(next_answer.confidence) if next_answer is not None else 0.0,
        },
    )


def next_sticky_profile(
    prev: Mapping[str, Any] | None,
    proposed: str,
) -> tuple[str, dict[str, Any]]:
    """连续 2 回合同一类别才预加载该 profile，类别改变则回无偏置的 full。

    L4 仅在 ``decision_is_applied`` 时应用该初始集合，授权目录始终不变。
    """
    profile = proposed if proposed in PROFILE_NAMES else "full"
    last = str((prev or {}).get("last_profile") or "full")
    streak = int((prev or {}).get("streak") or 0)
    narrowed = bool((prev or {}).get("narrowed"))
    if profile == "full":
        return "full", {"last_profile": "full", "streak": 0, "narrowed": False}
    if narrowed:
        if profile == last:
            return profile, {"last_profile": profile, "streak": streak + 1, "narrowed": True}
        return "full", {"last_profile": profile, "streak": 1, "narrowed": False}
    if profile == last:
        streak = streak + 1
        if streak >= T_STICKY_TURNS:
            return profile, {"last_profile": profile, "streak": streak, "narrowed": True}
        return "full", {"last_profile": profile, "streak": streak, "narrowed": False}
    return "full", {"last_profile": profile, "streak": 1, "narrowed": False}


def is_known_dangerous_call(
    tool_name: str,
    arguments: Mapping[str, Any] | None,
    workspace_root: str | None = None,
) -> bool:
    """确定性危险形：不叫 Jev，始终由确定性规则拒绝。"""
    args = arguments or {}
    name = str(tool_name or "")
    if name == "run_shell":
        cmd = str(args.get("command") or args.get("cmd") or "")
        return bool(_RM_RF.search(cmd))
    if name == "delete_file":
        raw = str(args.get("file_path") or args.get("path") or "").strip()
        if raw in {"", ".", "./", "/", "\\"}:
            return True
        if workspace_root:
            try:
                root = Path(workspace_root).expanduser().resolve()
                target = Path(raw)
                if not target.is_absolute():
                    target = root / raw
                return target.resolve() == root
            except (OSError, RuntimeError, ValueError):
                return False
    return False
