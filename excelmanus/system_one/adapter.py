"""有界 state：只送本问需要的字段，不送对话史、文件字节、密钥。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

_SECRET_KEYS = frozenset(
    {
        "api_key",
        "token",
        "password",
        "secret",
        "authorization",
        "typesafe_api_key",
        "ai_gateway_api_key",
        "typesafe-api-key",
        "credential",
    }
)
_MAX_USER_TEXT = 500
_MAX_HEAD = 300
_MAX_LIST = 10
_MAX_TOOL_ARGS = 8

_PACK_FIELDS: dict[str, tuple[str, ...]] = {
    "exposure.turn": (
        "user_text",
        "chat_mode",
        "families",
        "has_pending_plan",
        "image_count",
        "last_turn_tools",
        "skill_names",
    ),
    "observation.shape": (
        "user_text",
        "tool",
        "result_chars",
        "coverage",
        "result_head",
        "files_touched",
        "success",
        "re_fetchable",
        "spillable",
    ),
    "ui.surface": (
        "user_text",
        "tools_used",
        "files_written",
        "turn_outcome",
        "candidate_files",
    ),
    "approval.tool_call": (
        "user_text",
        "chat_mode",
        "tool",
        "code_tier",
        "policy",
        "path",
        "content_version",
    ),
    "skill.pin": (
        "user_text",
        "candidates",
        "skill_names",
    ),
    "loop.wrap": (
        "user_text",
        "iteration",
        "consecutive_failures",
        "last_tools",
        "last_error",
    ),
    "observation.prune": (
        "user_text",
        "tool",
        "result_chars",
        "result_head",
        "success",
        "re_fetchable",
    ),
    "mutation.verify": (
        "user_text",
        "chat_mode",
        "turn_outcome",
        "tools_used",
        "files_written",
        "verification_facts",
        "write_operations",
    ),
    "recovery.next_step": (
        "user_text",
        "iteration",
        "consecutive_failures",
        "last_error",
        "last_tools",
        "error_facts",
    ),
}


def _clip_text(value: object, limit: int) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[:limit]


def _clip_list(value: object, limit: int = _MAX_LIST) -> list[Any]:
    if not isinstance(value, (list, tuple)):
        return []
    out: list[Any] = []
    for item in value[:limit]:
        if isinstance(item, str):
            out.append(_clip_text(item, 120))
        elif isinstance(item, (int, float, bool)) or item is None:
            out.append(item)
        elif isinstance(item, dict):
            out.append(_clip_mapping(item, depth=1))
        else:
            out.append(str(item)[:80])
    return out


def _is_secret_key(key: str) -> bool:
    lowered = key.strip().lower().replace("-", "_")
    compact = lowered.replace("_", "")
    if lowered in _SECRET_KEYS:
        return True
    if compact in {"apikey", "accesskey", "bearertoken"}:
        return True
    return any(part in lowered for part in ("api_key", "secret", "password", "token"))


def _clip_mapping(value: Mapping[str, Any], *, depth: int) -> dict[str, Any]:
    if depth <= 0:
        return {}
    out: dict[str, Any] = {}
    for raw_key, raw_val in list(value.items())[:24]:
        key = str(raw_key)
        if _is_secret_key(key):
            continue
        if isinstance(raw_val, (bytes, bytearray)):
            continue
        if isinstance(raw_val, str):
            out[key] = _clip_text(raw_val, _MAX_HEAD if depth == 1 else 80)
        elif isinstance(raw_val, Mapping):
            out[key] = _clip_mapping(raw_val, depth=depth - 1)
        elif isinstance(raw_val, (list, tuple)):
            out[key] = _clip_list(raw_val, 5)
        elif isinstance(raw_val, (int, float, bool)) or raw_val is None:
            out[key] = raw_val
        else:
            out[key] = str(raw_val)[:80]
    return out


def _clip_tool(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        name = str(getattr(value, "name", "") or "")
        return {"name": name} if name else {}
    name = str(value.get("name") or "")
    args = value.get("args") or value.get("arguments") or {}
    clipped_args: dict[str, Any] = {}
    if isinstance(args, Mapping):
        for key, raw in list(args.items())[:_MAX_TOOL_ARGS]:
            if _is_secret_key(str(key)):
                continue
            if isinstance(raw, (bytes, bytearray)):
                continue
            if isinstance(raw, str):
                clipped_args[str(key)] = _clip_text(raw, 120)
            elif isinstance(raw, Mapping):
                clipped_args[str(key)] = _clip_mapping(raw, depth=1)
            elif isinstance(raw, (list, tuple)):
                clipped_args[str(key)] = _clip_list(raw, 5)
            elif isinstance(raw, (int, float, bool)) or raw is None:
                clipped_args[str(key)] = raw
            else:
                clipped_args[str(key)] = str(raw)[:120]
    out: dict[str, Any] = {}
    if name:
        out["name"] = name
    if clipped_args:
        out["args"] = clipped_args
    return out


def bound_state(pack_id: str, state: Mapping[str, Any] | None) -> dict[str, Any]:
    """裁剪有界 state。缺字段给安全默认值，从不透传对话史/字节/密钥。"""
    src = dict(state or {})
    allowed = _PACK_FIELDS.get(pack_id)
    if allowed is None:
        allowed = ("user_text", "chat_mode")
    out: dict[str, Any] = {}
    for key in allowed:
        if key == "user_text":
            out[key] = _clip_text(src.get("user_text"), _MAX_USER_TEXT)
        elif key == "result_head":
            out[key] = _clip_text(src.get("result_head"), _MAX_HEAD)
        elif key == "tool":
            out[key] = _clip_tool(src.get("tool"))
        elif key in {
            "last_turn_tools",
            "skill_names",
            "files_touched",
            "files_written",
            "candidate_files",
            "families",
            "tools_used",
            "last_tools",
            "candidates",
        }:
            out[key] = _clip_list(src.get(key))
        elif key == "has_pending_plan":
            out[key] = bool(src.get("has_pending_plan"))
        elif key in {"success", "re_fetchable", "spillable"}:
            out[key] = bool(src.get(key))
        elif key == "image_count":
            try:
                out[key] = max(0, int(src.get("image_count") or 0))
            except (TypeError, ValueError):
                out[key] = 0
        elif key == "result_chars":
            try:
                out[key] = max(0, int(src.get("result_chars") or 0))
            except (TypeError, ValueError):
                out[key] = 0
        elif key in src:
            raw = src[key]
            if isinstance(raw, Mapping):
                out[key] = _clip_mapping(raw, depth=2)
            elif isinstance(raw, (list, tuple)):
                out[key] = _clip_list(raw)
            elif isinstance(raw, (bytes, bytearray)):
                continue
            elif _is_secret_key(key):
                continue
            else:
                out[key] = _clip_text(raw, _MAX_HEAD) if isinstance(raw, str) else raw
        else:
            if key in {"chat_mode", "turn_outcome", "policy", "code_tier"}:
                out[key] = str(src.get(key) or "")
    out.pop("messages", None)
    out.pop("history", None)
    out.pop("transcript", None)
    return out


def exposure_state_from_engine(engine: Any, user_text: str) -> dict[str, Any]:
    families = getattr(engine, "_catalog_families", None)
    if not families:
        try:
            from excelmanus.tools.catalog import _workspace_flags

            flags = _workspace_flags(engine)
            families = flags.get("families") or ()
        except Exception:
            families = ()
    skills: list[str] = []
    for skill in getattr(engine, "_active_skills", None) or ():
        name = getattr(skill, "name", None)
        if name:
            skills.append(str(name))
    return bound_state(
        "exposure.turn",
        {
            "user_text": user_text,
            "chat_mode": getattr(engine, "_current_chat_mode", "write"),
            "families": list(families),
            "has_pending_plan": bool(getattr(engine, "_pending_plan_exit", None)),
            "image_count": int(getattr(engine, "_turn_image_count", 0) or 0),
            "last_turn_tools": list(getattr(engine, "_exposure_last_tools", None) or ()),
            "skill_names": skills,
        },
    )


def last_user_text(engine: Any) -> str:
    memory = getattr(engine, "memory", None) or getattr(engine, "_memory", None)
    if memory is None:
        return ""
    getter = getattr(memory, "get_messages", None)
    rows: list[Any] = []
    if callable(getter):
        try:
            candidate = getter() or []
            rows = list(candidate) if isinstance(candidate, (list, tuple)) else []
        except Exception:
            rows = []
    else:
        rows = list(getattr(memory, "messages", None) or [])
    for item in reversed(rows):
        if not isinstance(item, Mapping) or item.get("role") != "user":
            continue
        content = item.get("content")
        if isinstance(content, str) and content.strip():
            return _clip_text(content, _MAX_USER_TEXT)
        if isinstance(content, list):
            texts = [
                str(part.get("text") or "")
                for part in content
                if isinstance(part, Mapping) and part.get("type") == "text"
            ]
            joined = "".join(texts).strip()
            if joined:
                return _clip_text(joined, _MAX_USER_TEXT)
    return ""


def approval_state_from_engine(
    engine: Any,
    *,
    tool_name: str,
    arguments: Mapping[str, Any] | None,
    code_tier: str | None = None,
) -> dict[str, Any]:
    from excelmanus.security.policy import resolve_approval_policy

    args = dict(arguments or {})
    path = str(args.get("file_path") or args.get("path") or "")
    return bound_state(
        "approval.tool_call",
        {
            "user_text": last_user_text(engine),
            "chat_mode": getattr(engine, "_current_chat_mode", "write"),
            "tool": {"name": tool_name, "args": args},
            "code_tier": code_tier or "",
            "policy": resolve_approval_policy(engine),
            "path": path,
            "content_version": str(args.get("expected_version") or args.get("content_version") or ""),
        },
    )


def ui_surface_state_from_engine(
    engine: Any,
    chat_result: Any | None = None,
    *,
    turn_outcome: str = "ok",
) -> dict[str, Any]:
    files_written: list[str] = []
    state = getattr(engine, "_state", None)
    if state is not None:
        files_written = [
            str(item)
            for item in (getattr(state, "affected_files", None) or [])
            if item
        ][:3]
    tools: list[str] = []
    for item in getattr(chat_result, "tool_calls", None) or ():
        name = str(getattr(item, "tool_name", "") or getattr(item, "name", "") or "")
        if name:
            tools.append(name)
    return bound_state(
        "ui.surface",
        {
            "user_text": last_user_text(engine),
            "tools_used": tools[:10],
            "files_written": files_written,
            "turn_outcome": turn_outcome or "ok",
            "candidate_files": files_written[:3],
        },
    )


def mutation_verify_state_from_engine(engine: Any, chat_result: Any | None = None) -> dict[str, Any]:
    """Project deterministic post-write facts for ``mutation.verify``."""
    state = getattr(engine, "_state", None)
    affected = [
        str(item)
        for item in (getattr(state, "affected_files", None) or [])
        if item
    ][:10]
    operations = list(getattr(state, "write_operations_log", None) or [])[:10] if state else []
    tools: list[str] = []
    for item in getattr(chat_result, "tool_calls", None) or ():
        name = str(getattr(item, "tool_name", "") or getattr(item, "name", "") or "")
        if name:
            tools.append(name)
    verification_facts = {
        "success": bool(getattr(chat_result, "success", True)) if chat_result is not None else True,
        "truncated": bool(getattr(chat_result, "truncated", False)) if chat_result is not None else False,
        "affected_file_count": len(affected),
        "write_operation_count": len(operations),
        "has_version_conflict": any("conflict" in str(item).lower() for item in operations),
    }
    return bound_state(
        "mutation.verify",
        {
            "user_text": last_user_text(engine),
            "chat_mode": getattr(engine, "_current_chat_mode", "write"),
            "turn_outcome": "ok" if verification_facts["success"] else "fail",
            "tools_used": tools[:10],
            "files_written": affected,
            "verification_facts": verification_facts,
            "write_operations": operations,
        },
    )


def recovery_state_from_engine(
    engine: Any,
    tool_results: list[Any],
    *,
    reason: str = "breaker",
) -> dict[str, Any]:
    # Only the trailing failure streak caused this breaker. Earlier failures
    # separated by a successful tool must not bias the recovery suggestion.
    failures: list[Any] = []
    for item in reversed(tool_results):
        if bool(getattr(item, "success", False)):
            break
        failures.append(item)
    failures.reverse()
    errors: list[dict[str, Any]] = []
    for item in failures[-5:]:
        structured = getattr(item, "structured", None)
        tool_error = getattr(structured, "error", None)
        fields = getattr(tool_error, "fields", {}) if tool_error is not None else {}
        if not isinstance(fields, Mapping):
            fields = {}
        code = str(
            getattr(tool_error, "code", "")
            or fields.get("error_code")
            or getattr(item, "error", "")
            or "TOOL_ERROR"
        )
        kind = str(
            getattr(item, "error_kind", "")
            or fields.get("failure_class")
            or ""
        )
        retryable = kind in {"retryable", "transient"}
        errors.append({
            "tool": str(getattr(item, "tool_name", "") or ""),
            "error_code": code[:80],
            "failure_class": kind[:60],
            "error": str(fields.get("message") or getattr(item, "error", "") or "")[:160],
            "remediation": str(fields.get("remediation") or "")[:160],
            "rejected": kind in {"permission_denied", "approval_denied", "approval_timeout", "blocked"},
            "committed": bool(fields.get("committed") or fields.get("write_committed")),
            "retryable": retryable,
        })
    safe_to_retry = bool(errors) and all(
        item.get("retryable") and not item.get("rejected") and not item.get("committed")
        for item in errors
    )
    return bound_state(
        "recovery.next_step",
        {
            "user_text": last_user_text(engine),
            "iteration": int(getattr(engine, "_last_iteration_count", 0) or 0),
            "consecutive_failures": len(failures),
            "breaker_triggered": True,
            "safe_to_retry": safe_to_retry,
            "last_error": str(reason or "breaker"),
            "last_tools": [str(getattr(item, "tool_name", "") or "") for item in tool_results[-10:]],
            "error_facts": errors,
        },
    )


def observation_state_from_engine(
    engine: Any,
    *,
    tool_name: str,
    arguments: Mapping[str, Any] | None,
    result: Any,
    re_fetchable: bool,
    spillable: bool,
) -> dict[str, Any]:
    text = str(getattr(result, "model_text", "") or "")
    coverage = getattr(result, "coverage", None)
    ui = getattr(result, "ui_meta", None)
    files: list[str] = []
    if ui is not None:
        files = [str(item) for item in (getattr(ui, "files", None) or ()) if item]
    return bound_state(
        "observation.shape",
        {
            "user_text": last_user_text(engine),
            "tool": {"name": tool_name, "args": dict(arguments or {})},
            "result_chars": len(text),
            "coverage": dict(coverage) if isinstance(coverage, Mapping) else {},
            "result_head": text,
            "files_touched": files,
            "success": bool(getattr(result, "success", True)),
            "re_fetchable": re_fetchable,
            "spillable": spillable,
        },
    )
