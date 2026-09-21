"""P4 knobs: four axes stay independent. Presets pack, they do not enforce."""

from __future__ import annotations

from types import SimpleNamespace

from excelmanus.security.policy import (
    is_plan_active,
    knobs_for_preset,
    preset_from_engine,
    resolve_approval_policy,
    resolve_execution_policy,
    writes_denied,
)
from excelmanus.tools.context import capability_from_engine, intersect_capability


def _eng(**kwargs: object) -> SimpleNamespace:
    base = {
        "_current_chat_mode": "write",
        "_full_access_enabled": False,
        "_subagent_config": None,
        "_plan_active": False,
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_read_mode_is_read_only() -> None:
    e = _eng(_current_chat_mode="read")
    assert resolve_execution_policy(e).mode == "read-only"
    assert writes_denied(e)
    assert resolve_approval_policy(e) == "ask"
    assert not is_plan_active(e)


def test_write_mode_is_workspace_write() -> None:
    e = _eng(_current_chat_mode="write")
    assert resolve_execution_policy(e).mode == "workspace-write"
    assert not writes_denied(e)
    assert resolve_approval_policy(e) == "ask"


def test_plan_mode_keeps_workspace_write_sandbox() -> None:
    e = _eng(_current_chat_mode="plan")
    assert resolve_execution_policy(e).mode == "workspace-write"
    assert not writes_denied(e)
    assert is_plan_active(e)
    assert resolve_approval_policy(e) == "ask"


def test_stale_plan_flag_does_not_override_write_chat_mode() -> None:
    """Runtime no longer treats leftover _plan_active as plan when chat_mode is write."""
    e = _eng(_current_chat_mode="write", _plan_active=True)
    assert resolve_execution_policy(e).mode == "workspace-write"
    assert not writes_denied(e)
    assert not is_plan_active(e)


def test_full_access_is_a_distinct_execution_mode() -> None:
    e = _eng(_current_chat_mode="write", _full_access_enabled=True)
    assert resolve_execution_policy(e).mode == "full-access"
    assert resolve_approval_policy(e) == "never"
    assert not writes_denied(e)
    capability = capability_from_engine(e)
    assert capability.approval == "never"
    assert capability.full_access is True


def test_auto_approve_skips_confirmation_without_full_access() -> None:
    e = _eng(_auto_approve_enabled=True)
    assert resolve_execution_policy(e).mode == "workspace-write"
    assert resolve_approval_policy(e) == "never"
    assert capability_from_engine(e).full_access is False


def test_child_capability_inherits_host_full_access() -> None:
    parent = capability_from_engine(
        _eng(_current_chat_mode="write", _full_access_enabled=True)
    )
    child = intersect_capability(
        parent,
        permission_mode="acceptEdits",
        allowed_tools=None,
    )
    assert child.full_access is True


def test_full_access_chat_mode_string_is_workspace_write() -> None:
    e = _eng(_current_chat_mode="full_access")
    assert resolve_execution_policy(e).mode == "workspace-write"
    assert not writes_denied(e)


def test_subagent_readonly_forces_read_only() -> None:
    e = _eng(
        _current_chat_mode="write",
        _subagent_config=SimpleNamespace(permission_mode="readOnly"),
    )
    assert resolve_execution_policy(e).mode == "read-only"
    assert writes_denied(e)
    assert resolve_approval_policy(e) == "never"


def test_subagent_accept_edits_skips_ask_respects_mode() -> None:
    e = _eng(
        _current_chat_mode="write",
        _subagent_config=SimpleNamespace(permission_mode="acceptEdits"),
    )
    assert resolve_execution_policy(e).mode == "workspace-write"
    assert resolve_approval_policy(e) == "never"

    readonly_parent = _eng(
        _current_chat_mode="read",
        _subagent_config=SimpleNamespace(permission_mode="acceptEdits"),
    )
    assert resolve_execution_policy(readonly_parent).mode == "read-only"
    assert writes_denied(readonly_parent)
    assert resolve_approval_policy(readonly_parent) == "never"


def test_subagent_dont_ask_is_never() -> None:
    e = _eng(_subagent_config=SimpleNamespace(permission_mode="dontAsk"))
    assert resolve_approval_policy(e) == "never"
    assert resolve_execution_policy(e).mode == "workspace-write"


def test_subagent_default_pins_approval_never() -> None:
    e = _eng(
        _current_chat_mode="write",
        _subagent_config=SimpleNamespace(permission_mode="default"),
    )
    assert resolve_approval_policy(e) == "never"
    assert resolve_execution_policy(e).mode == "workspace-write"


def test_preset_observe_edit_auto() -> None:
    observe, ask = knobs_for_preset("observe")
    assert observe.mode == "read-only"
    assert ask == "ask"
    edit, ask2 = knobs_for_preset("edit")
    assert edit.mode == "workspace-write"
    assert ask2 == "ask"
    auto, never = knobs_for_preset("auto-edit")
    assert auto.mode == "workspace-write"
    assert never == "never"


def test_preset_from_engine_maps_knobs() -> None:
    assert preset_from_engine(_eng(_current_chat_mode="read")) == "observe"
    assert preset_from_engine(_eng(_current_chat_mode="write")) == "edit"
    assert preset_from_engine(
        _eng(_current_chat_mode="write", _full_access_enabled=True)
    ) == "auto-edit"
    # Plan is not observe: sandbox stays workspace-write.
    assert preset_from_engine(_eng(_current_chat_mode="plan")) == "edit"
