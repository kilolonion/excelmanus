"""Jev runtime contract: off skips, enforce applies, no shadow state."""

from types import SimpleNamespace

from excelmanus.config import _parse_jev_gate
from excelmanus.system_one.policy import decision_is_applied, gate_for_pack, settings_from


def _config(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "jev_experimental_enabled": True,
        "jev_enabled": "enforce",
        "jev_exposure": "enforce",
        "jev_observation": "enforce",
        "jev_verification": "enforce",
        "jev_recovery": "enforce",
        "jev_mode_hint": True,
        "jev_ui_hint": True,
        "jev_model": "jev-1.13.0",
        "typesafe_api_key": "ts_test_key",
        "ai_gateway_api_key": None,
        "jev_active_provider": "",
        "jev_providers": (),
        "jev_timeout_seconds": 1.5,
        "jev_calibrated": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_legacy_shadow_is_migrated_to_full_enable() -> None:
    assert _parse_jev_gate("shadow", "EXCELMANUS_JEV_ENABLED") == "enforce"
    settings = settings_from(_config(jev_enabled="shadow", jev_exposure="shadow"))
    assert gate_for_pack("exposure.turn", settings) == "enforce"
    assert decision_is_applied("exposure.turn", settings)


def test_master_or_child_off_disables_the_pack() -> None:
    settings = settings_from(_config(jev_enabled="off"))
    assert gate_for_pack("approval.tool_call", settings) == "off"
    assert not decision_is_applied("approval.tool_call", settings)

    settings = settings_from(_config(jev_exposure="off"))
    assert gate_for_pack("exposure.turn", settings) == "off"
    assert not decision_is_applied("exposure.turn", settings)
    assert gate_for_pack("observation.shape", settings) == "enforce"


def test_all_pack_gates_are_enabled_by_default() -> None:
    settings = settings_from(_config())
    for pack in (
        "context.resolve",
        "exposure.turn",
        "observation.shape",
        "mutation.verify",
        "recovery.next_step",
        "ui.surface",
        "approval.tool_call",
    ):
        assert gate_for_pack(pack, settings) == "enforce"
        assert decision_is_applied(pack, settings)
