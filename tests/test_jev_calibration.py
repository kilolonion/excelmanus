"""片 D 标定骨架：离线 fixture 合成 + enforce 门禁。禁止打网。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from excelmanus.system_one import evaluate
from excelmanus.system_one.calibration import (
    SIGNED_ENFORCE_PACKS,
    calibration_allows_enforce,
    load_suite_states,
    run_offline_fixture,
    synthesize_sample,
)
from excelmanus.system_one.client import normalize_answers
from excelmanus.system_one.packs import get_pack
from excelmanus.system_one.policy import (
    T_ALLOW,
    JevSettings,
    settings_from,
    stamp_application,
    synthesize,
)
from excelmanus.system_one.types import ChoiceAnswer, Decision, Evaluation, NoulAnswer, ScoreAnswer


def _settings(**overrides: object) -> JevSettings:
    base = dict(
        enabled="enforce",
        exposure="enforce",
        mode_hint=True,
        present_as_auto=True,
        observation="enforce",
        ui_hint=True,
        model="jev-1.13.0",
        api_key="k",
        timeout_seconds=1.5,
        calibrated=False,
    )
    base.update(overrides)
    return JevSettings(**base)  # type: ignore[arg-type]


def test_signed_sets_are_empty() -> None:
    assert SIGNED_ENFORCE_PACKS == frozenset()


def test_calibration_blocks_enforce_without_signoff() -> None:
    unsigned = _settings(calibrated=False)
    flipped = _settings(calibrated=True)
    assert calibration_allows_enforce("exposure.turn", unsigned) is False
    assert calibration_allows_enforce("approval.tool_call", unsigned) is False
    # 配置打开但签字集合为空：仍不得 enforce。
    assert calibration_allows_enforce("exposure.turn", flipped) is False
    assert calibration_allows_enforce("approval.tool_call", flipped) is False


def test_stamp_application_unsigned_never_narrows() -> None:
    decision = Decision.noop("domain:inspect_only", profile="inspect", wire_narrow=True)
    stamped = stamp_application("exposure.turn", decision, _settings(calibrated=True, enabled="enforce"))
    assert stamped.applied is False
    assert stamped.extras.get("wire_narrow") is False
    assert stamped.extras.get("profile") == "inspect"


@pytest.mark.asyncio
async def test_evaluate_enforce_unsigned_stays_unapplied() -> None:
    cfg = SimpleNamespace(
        jev_enabled="enforce",
        jev_exposure="enforce",
        jev_mode_hint=True,
        jev_present_as_auto=True,
        jev_observation="enforce",
        jev_ui_hint=True,
        jev_model="jev-1.13.0",
        typesafe_api_key=None,
        jev_timeout_seconds=1.5,
        jev_calibrated=True,
    )
    exposure = await evaluate("exposure.turn", {"user_text": "帮我看看"}, config=cfg)
    assert exposure.applied is False
    assert exposure.extras.get("wire_narrow") is not True
    approval = await evaluate(
        "approval.tool_call",
        {"user_text": "删文件", "tool": {"name": "delete_file"}},
        config=cfg,
    )
    assert approval.kind == "ask"
    assert approval.applied is False


def test_mid_confidence_allow_is_ask_not_auto() -> None:
    decision = synthesize(
        "approval.tool_call",
        Evaluation(
            pack_id="approval.tool_call",
            answers={
                "action": ChoiceAnswer("allow", confidence=0.85),
                "destructive": NoulAnswer(0.1),
                "exfiltrating": NoulAnswer(0.1),
                "blast_radius": ScoreAnswer(score=1.0),
            },
        ),
    )
    assert 0.85 < T_ALLOW
    assert decision.kind == "ask"


def test_offline_fixture_synthesizes_without_network() -> None:
    rows = run_offline_fixture()
    assert rows
    failed = [row["id"] for row in rows if not row["ok"]]
    assert failed == []
    assert all(row["applied"] is False for row in rows)
    kinds = {row["id"]: row["kind"] for row in rows}
    assert kinds["A01"] == "ask"
    assert kinds["A-allow-not-auto"] == "ask"
    assert kinds["A-delete-cover"] == "deny"
    assert kinds["E01"] == "noop"


def test_suite_mapping_keeps_chinese_user_text() -> None:
    mapped = load_suite_states()
    by_id = {item["id"]: item for item in mapped}
    assert "帮我看看" in by_id["E01"]["state"]["user_text"]
    assert "华东区 8 月的销售额是多少？" in by_id["R01"]["state"]["user_text"]
    assert by_id["A01"]["pack_id"] == "approval.tool_call"
    assert by_id["A01"]["state"]["tool"]["name"] == "run_shell"
    assert by_id["E01"]["pack_id"] == "exposure.turn"
    assert not any("api_key" in (item["state"] or {}) for item in mapped)


def test_normalize_answers_accepts_fixture_dicts() -> None:
    spec = get_pack("exposure.turn")
    answers = normalize_answers(
        spec,
        {"domain": {"choice": "inspect_only", "confidence": 0.91}, "needs_write": {"noul": 0.08}},
    )
    sample = {
        "id": "x",
        "pack_id": "exposure.turn",
        "state": {"user_text": "华东区 8 月的销售额是多少？", "chat_mode": "write"},
        "answers": {"domain": {"choice": "inspect_only", "confidence": 0.91}, "needs_write": {"noul": 0.08}},
        "expect": {"kind": "noop", "profile": "inspect"},
    }
    row = synthesize_sample(sample)
    assert row["ok"] is True
    assert answers["domain"].choice == "inspect_only"


def test_settings_from_reads_calibrated_flag() -> None:
    parsed = settings_from(SimpleNamespace(
        jev_enabled="enforce",
        jev_exposure="enforce",
        jev_mode_hint=False,
        jev_present_as_auto=False,
        jev_observation="off",
        jev_ui_hint=False,
        jev_model="jev-1.13.0",
        typesafe_api_key=None,
        jev_timeout_seconds=1.5,
        jev_calibrated=True,
    ))
    assert parsed.calibrated is True
    assert calibration_allows_enforce("exposure.turn", parsed) is False
