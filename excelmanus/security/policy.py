"""Execute-time knobs. Map existing chat-mode / subagent flags; do not invent a third world."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

ExecutionMode = Literal["read-only", "workspace-write"]
ApprovalPolicy = Literal["ask", "never"]
PermissionPreset = Literal["observe", "edit", "auto-edit"]


@dataclass(frozen=True)
class ExecutionPolicy:
    mode: ExecutionMode = "workspace-write"


def resolve_execution_policy(engine: Any) -> ExecutionPolicy:
    sub = getattr(engine, "_subagent_config", None)
    perm = str(getattr(sub, "permission_mode", "") or "")
    if perm == "readOnly":
        return ExecutionPolicy(mode="read-only")
    chat = str(getattr(engine, "_current_chat_mode", "write") or "write")
    if chat in {"read", "plan"}:
        return ExecutionPolicy(mode="read-only")
    # write / full_access → workspace-write. full_access does not escape.
    return ExecutionPolicy(mode="workspace-write")


def resolve_approval_policy(engine: Any) -> ApprovalPolicy:
    sub = getattr(engine, "_subagent_config", None)
    perm = str(getattr(sub, "permission_mode", "") or "")
    if perm in {"acceptEdits", "dontAsk"}:
        return "never"
    if bool(getattr(engine, "_full_access_enabled", False)):
        return "never"
    return "ask"


def writes_denied(engine: Any) -> bool:
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
