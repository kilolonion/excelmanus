"""Execute-time knobs. Four axes stay independent; presets pack, they do not enforce."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

ExecutionMode = Literal["read-only", "workspace-write", "full-access"]
ApprovalPolicy = Literal["ask", "never"]
PermissionPreset = Literal["observe", "edit", "auto-edit"]

# Plan / read-only sandbox treat these write_effect values as writes.
RESTRICTED_WRITE_EFFECTS: frozenset[str] = frozenset(
    {"workspace_write", "external_write", "dynamic", "unknown"}
)


@dataclass(frozen=True)
class ExecutionPolicy:
    mode: ExecutionMode = "workspace-write"


def is_plan_active(engine: Any) -> bool:
    """Plan is a collaboration stance, not a sandbox mode.

    ``chat_mode`` is the only runtime permission fact.
    ``is_plan_active(engine) := _current_chat_mode == "plan"``.
    Leftover ``_plan_active`` is ignored (migrate old snapshots explicitly).
    """
    return str(getattr(engine, "_current_chat_mode", "") or "") == "plan"


def resolve_execution_policy(engine: Any) -> ExecutionPolicy:
    """Sandbox only. Plan must not map to read-only here."""
    sub = getattr(engine, "_subagent_config", None)
    perm = str(getattr(sub, "permission_mode", "") or "")
    if perm == "readOnly":
        return ExecutionPolicy(mode="read-only")
    chat = str(getattr(engine, "_current_chat_mode", "write") or "write")
    if chat == "read":
        return ExecutionPolicy(mode="read-only")
    if bool(getattr(engine, "_full_access_enabled", False)):
        return ExecutionPolicy(mode="full-access")
    # write / plan / auto_approve → workspace-write.
    return ExecutionPolicy(mode="workspace-write")


def resolve_approval_policy(engine: Any) -> ApprovalPolicy:
    """Ask vs never. Orthogonal to sandbox. Subagents are pinned to never."""
    if getattr(engine, "_subagent_config", None) is not None:
        return "never"
    if bool(getattr(engine, "_full_access_enabled", False)):
        return "never"
    if bool(getattr(engine, "_auto_approve_enabled", False)):
        return "never"
    return "ask"


def writes_denied(engine: Any) -> bool:
    """True only when sandbox is read-only. Plan is not included."""
    return resolve_execution_policy(engine).mode == "read-only"


def knobs_for_preset(preset: PermissionPreset) -> tuple[ExecutionPolicy, ApprovalPolicy]:
    """UI preset → execute-time knobs. Runtime still reads only the two knobs."""
    if preset == "observe":
        return ExecutionPolicy(mode="read-only"), "ask"
    if preset == "auto-edit":
        return ExecutionPolicy(mode="workspace-write"), "never"
    return ExecutionPolicy(mode="workspace-write"), "ask"


def preset_from_engine(engine: Any) -> PermissionPreset:
    policy = resolve_execution_policy(engine)
    approval = resolve_approval_policy(engine)
    if policy.mode == "read-only":
        return "observe"
    if approval == "never":
        return "auto-edit"
    return "edit"
