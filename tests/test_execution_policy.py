"""P4 knobs: map existing chat-mode / subagent flags. Do not invent a third world."""

from __future__ import annotations

from types import SimpleNamespace

from excelmanus.security.policy import (
    knobs_for_preset,
    preset_from_engine,
    resolve_approval_policy,
    resolve_execution_policy,
    writes_denied,
)


def _eng(**kwargs: object) -> SimpleNamespace:
    base = {
        "_current_chat_mode": "write",
        "_full_access_enabled": False,
        "_subagent_config": None,
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_read_mode_is_read_only() -> None:
    e = _eng(_current_chat_mode="read")
    assert resolve_execution_policy(e).mode == "read-only"
    assert writes_denied(e)
    assert resolve_approval_policy(e) == "ask"


def test_write_mode_is_workspace_write() -> None:
    e = _eng(_current_chat_mode="write")
    assert resolve_execution_policy(e).mode == "workspace-write"
    assert not writes_denied(e)
    assert resolve_approval_policy(e) == "ask"


def test_plan_mode_is_read_only() -> None:
    e = _eng(_current_chat_mode="plan")
    assert resolve_execution_policy(e).mode == "read-only"
    assert writes_denied(e)


def test_full_access_is_workspace_write_not_escape() -> None:
    e = _eng(_current_chat_mode="write", _full_access_enabled=True)
    assert resolve_execution_policy(e).mode == "workspace-write"
    assert resolve_approval_policy(e) == "never"


def test_subagent_readonly_forces_read_only() -> None:
    e = _eng(
        _current_chat_mode="write",
        _subagent_config=SimpleNamespace(permission_mode="readOnly"),
    )
    assert resolve_execution_policy(e).mode == "read-only"
    assert writes_denied(e)


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
