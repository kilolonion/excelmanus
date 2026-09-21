"""Legacy Jev calibration helpers.

The reports and fingerprints remain available for evaluation tooling, but
runtime application is controlled solely by the master and pack gates.
"""

from __future__ import annotations

import json
import hashlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from excelmanus.system_one.adapter import bound_state
from excelmanus.system_one.client import normalize_answers
from excelmanus.system_one.packs import get_pack
from excelmanus.system_one.policy import JevSettings, settings_from, synthesize
from excelmanus.system_one.types import Evaluation

# live 中文对照未签字。往这里加 pack_id 等于宣称「可以 enforce」——本刀禁止加。
SIGNED_ENFORCE_PACKS: frozenset[str] = frozenset()
SIGNED_ENFORCE_FAMILIES: frozenset[str] = frozenset()
# Optional signed provenance. Empty preserves the existing explicit pack/family
# signoff behavior used by local tests; production signoff should populate it.
SIGNED_ENFORCE_PROVENANCE: dict[str, str] = {}

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_FIXTURE = _REPO_ROOT / "bench" / "fixtures" / "jev_calibration" / "samples.json"
_SUITE_FILES = (
    "bench/cases/suite_write_approval.json",
    "bench/cases/suite_experiential.json",
    "bench/cases/suite_realistic.json",
)
_APPROVAL_TOOLS = frozenset({"run_shell", "delete_file", "run_code"})


def calibration_allows_enforce(pack_id: str, settings: JevSettings | None = None) -> bool:
    """Legacy report predicate retained for offline calibration tooling.

    Production host code no longer calls this function as a runtime gate.
    """
    cfg = settings if settings is not None else settings_from(None)
    if not cfg.calibrated:
        return False
    if pack_id in SIGNED_ENFORCE_PACKS:
        signed = SIGNED_ENFORCE_PROVENANCE.get(pack_id)
        return not signed or signed == calibration_fingerprint(pack_id)
    return get_pack(pack_id).family in SIGNED_ENFORCE_FAMILIES


def calibration_fingerprint(pack_id: str) -> str:
    """Stable digest of the pack questions/criteria used for signoff drift."""
    spec = get_pack(pack_id)
    payload = {
        "pack_id": spec.pack_id,
        "family": spec.family,
        "gate": spec.gate,
        "advisory_only": spec.advisory_only,
        "questions": [
            {
                "qid": item.qid,
                "kind": item.kind,
                "instructions": item.instructions,
                "criteria": item.criteria,
            }
            for item in spec.questions
        ],
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def fixture_path(root: Path | None = None) -> Path:
    if root is None:
        return _DEFAULT_FIXTURE
    return Path(root) / "bench" / "fixtures" / "jev_calibration" / "samples.json"


def load_samples(path: Path | None = None) -> list[dict[str, Any]]:
    target = path or _DEFAULT_FIXTURE
    raw = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise ValueError("jev calibration fixture must be an object")
    if raw.get("signed"):
        raise ValueError("fixture.signed must stay false until live 中文对照签字")
    samples = raw.get("samples")
    if not isinstance(samples, list) or not samples:
        raise ValueError("jev calibration fixture needs samples[]")
    return [dict(item) for item in samples if isinstance(item, Mapping)]


def synthesize_sample(sample: Mapping[str, Any]) -> dict[str, Any]:
    """固定 JSON 答案 → policy.synthesize。禁止打网。"""
    pack_id = str(sample.get("pack_id") or "")
    spec = get_pack(pack_id)
    state = bound_state(pack_id, sample.get("state") if isinstance(sample.get("state"), Mapping) else {})
    answers = normalize_answers(spec, sample.get("answers") or {})
    evaluation = Evaluation(pack_id=pack_id, answers=answers, model="fixture", skipped=False)
    decision = synthesize(pack_id, evaluation, state)
    expect: dict[str, Any] = {}
    expect_raw = sample.get("expect")
    if isinstance(expect_raw, Mapping):
        expect = {str(key): value for key, value in expect_raw.items()}
    return {
        "id": sample.get("id"),
        "pack_id": pack_id,
        "kind": decision.kind,
        "reason": decision.reason,
        "extras": dict(decision.extras),
        "applied": decision.applied,
        "expect": dict(expect),
        "ok": _sample_matches(decision, expect),
    }


def run_offline_fixture(path: Path | None = None) -> list[dict[str, Any]]:
    return [synthesize_sample(sample) for sample in load_samples(path)]


def load_suite_states(root: Path | None = None) -> list[dict[str, Any]]:
    """把现有 bench suite 映射成有界 state（不打网、不跑模型）。"""
    base = Path(root) if root is not None else _REPO_ROOT
    out: list[dict[str, Any]] = []
    for rel in _SUITE_FILES:
        path = base / rel
        if not path.is_file():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        cases = data.get("cases") if isinstance(data, Mapping) else None
        if not isinstance(cases, list):
            continue
        for case in cases:
            if not isinstance(case, Mapping):
                continue
            mapped = _state_from_case(case, source=rel)
            if mapped is not None:
                out.append(mapped)
    return out


def _state_from_case(case: Mapping[str, Any], *, source: str) -> dict[str, Any] | None:
    user_text = str(case.get("message") or "").strip()
    if not user_text:
        return None
    case_id = str(case.get("id") or "")
    chat_mode = str(case.get("chat_mode") or "write")
    assertions_raw = case.get("assertions")
    assertions: Mapping[str, Any] = assertions_raw if isinstance(assertions_raw, Mapping) else {}
    required = [str(item) for item in (assertions.get("required_tools") or ()) if str(item)]
    pack_id = "approval.tool_call" if any(name in _APPROVAL_TOOLS for name in required) else "exposure.turn"
    state: dict[str, Any] = {
        "user_text": user_text,
        "chat_mode": chat_mode,
    }
    if pack_id == "approval.tool_call":
        tool_name = next((name for name in required if name in _APPROVAL_TOOLS), "run_shell")
        state["tool"] = {"name": tool_name, "args": {}}
        state["policy"] = "ask"
    return {
        "id": case_id,
        "source": source,
        "pack_id": pack_id,
        "state": bound_state(pack_id, state),
    }


def decision_matches_expect(decision: Any, expect: Mapping[str, Any] | None) -> bool:
    """公开给 live 标定器对照用。expect 为空则不算失败。"""
    return _sample_matches(decision, expect or {})


def _sample_matches(decision: Any, expect: Mapping[str, Any]) -> bool:
    if not expect:
        return True
    kind = expect.get("kind")
    if kind is not None and decision.kind != kind:
        return False
    profile = expect.get("profile")
    if profile is not None and str(decision.extras.get("profile") or "") != str(profile):
        return False
    return True
