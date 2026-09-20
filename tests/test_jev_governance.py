from __future__ import annotations

from unittest.mock import patch

import pytest

from excelmanus.system_one import evaluate
from excelmanus.system_one.breaker import allow, provider_key, record_failure, record_success, reset
from excelmanus.system_one.calibration import calibration_fingerprint, calibration_allows_enforce
from excelmanus.system_one.policy import JevSettings


def test_provider_breaker_cools_after_repeated_failures() -> None:
    reset()
    key = provider_key("p", "gateway", "m", "https://example.test")
    assert allow(key) is True
    record_failure(key, "timeout")
    assert allow(key) is True
    record_failure(key, "timeout")
    assert allow(key) is False
    record_success(key)
    assert allow(key) is True


def test_calibration_fingerprint_changes_with_pack_prompt() -> None:
    first = calibration_fingerprint("mutation.verify")
    assert len(first) == 64
    with patch("excelmanus.system_one.packs.PACKS", dict(__import__("excelmanus.system_one.packs", fromlist=["PACKS"]).PACKS)):
        assert calibration_fingerprint("mutation.verify") == first


def test_signed_provenance_mismatch_blocks_enforce(monkeypatch: pytest.MonkeyPatch) -> None:
    from excelmanus.system_one import calibration

    settings = JevSettings(
        enabled="enforce", exposure="enforce", mode_hint=False,
        observation="off", ui_hint=False,
        model="m", api_key="k", timeout_seconds=1.0, calibrated=True,
    )
    monkeypatch.setattr(calibration, "SIGNED_ENFORCE_PACKS", frozenset({"mutation.verify"}))
    monkeypatch.setattr(calibration, "SIGNED_ENFORCE_PROVENANCE", {"mutation.verify": "stale"})
    assert calibration_allows_enforce("mutation.verify", settings) is False
