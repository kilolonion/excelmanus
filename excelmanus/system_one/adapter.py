"""有界 state：只送本问需要的字段，不送完整对话史、文件字节、密钥。"""

from __future__ import annotations

import re
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
    "context.resolve": (
        "user_text", "current_workspace", "workspaces", "targets", "recent_context",
        "workspaces_truncated", "targets_truncated",
        "columns", "columns_truncated",
    ),
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
        "last_tools",
        "observations",
        "pending_items",
        "has_more_results",
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
        "write_evidence",
        "write_operations",
        "checklist",
    ),
    "recovery.next_step": (
        "user_text",
        "iteration",
        "consecutive_failures",
        "last_error",
        "last_tools",
        "error_facts",
        "breaker_triggered",
        "safe_to_retry",
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
    """裁剪有界 state。缺字段给安全默认值，不透传完整对话史/字节/密钥。"""
    src = dict(state or {})
    allowed = _PACK_FIELDS.get(pack_id)
    if allowed is None:
        allowed = ("user_text", "chat_mode")
    out: dict[str, Any] = {}
    for key in allowed:
        if key == "user_text":
            out[key] = _clip_text(src.get("user_text"), 2000 if pack_id == "context.resolve" else _MAX_USER_TEXT)
        elif key == "result_head":
            out[key] = _clip_text(src.get("result_head"), _MAX_HEAD)
        elif key == "tool":
            out[key] = _clip_tool(src.get("tool"))
        elif key in {"candidate_files", "files_written"}:
            # A clipped path points to a different file. Drop oversize paths
            # instead of turning them into plausible but invalid identities.
            raw = src.get(key) or []
            out[key] = [item for item in raw[:10] if isinstance(item, str) and len(item) <= 300]
        elif key in {
            "last_turn_tools",
            "skill_names",
            "files_touched",
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


def last_user_text(engine: Any, *, limit: int | None = _MAX_USER_TEXT) -> str:
    memory = getattr(engine, "memory", None) or getattr(engine, "_memory", None)
    if memory is None:
        return ""
    # get_messages strips internal markers for the provider. Use the durable
    # messages first so an injected Jev suggestion cannot become user intent.
    durable = getattr(memory, "messages", None)
    getter = getattr(memory, "get_messages", None)
    rows: list[Any] = []
    if isinstance(durable, (list, tuple)):
        rows = list(durable)
    elif callable(getter):
        try:
            candidate = getter() or []
            rows = list(candidate) if isinstance(candidate, (list, tuple)) else []
        except Exception:
            rows = []
    else:
        rows = list(getattr(memory, "messages", None) or [])
    for item in reversed(rows):
        if (
            not isinstance(item, Mapping) or item.get("role") != "user"
            or item.get("_ui_hidden") or item.get("_prompt_kind")
        ):
            continue
        content = item.get("content")
        if isinstance(content, str) and content.strip():
            return content if limit is None else _clip_text(content, limit)
        if isinstance(content, list):
            texts = [
                str(part.get("text") or "")
                for part in content
                if isinstance(part, Mapping) and part.get("type") == "text"
            ]
            joined = "".join(texts).strip()
            if joined:
                return joined if limit is None else _clip_text(joined, limit)
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
    from pathlib import Path
    from excelmanus.workspace.identity import IdentityError, resolve_canonical

    files_written: list[str] = []
    state = getattr(engine, "_state", None)
    if state is not None:
        files_written = [
            str(item)
            for item in (getattr(state, "affected_files", None) or [])
            if item
        ]
    candidates = list(reversed(files_written))
    tools: list[str] = []
    for item in getattr(chat_result, "tool_calls", None) or ():
        name = str(getattr(item, "tool_name", "") or getattr(item, "name", "") or "")
        if name:
            tools.append(name)
        arguments = getattr(item, "arguments", None)
        if isinstance(arguments, Mapping) and bool(getattr(item, "success", False)):
            path = str(arguments.get("file_path") or arguments.get("path") or "")
            if path:
                candidates.append(path)
    incoming = getattr(engine, "_jev_context_input", None)
    sheet_context = incoming.get("sheet_context") if isinstance(incoming, Mapping) else None
    current_id = str(getattr(getattr(engine, "_workspace_ref", None), "workspace_id", "") or "")
    if isinstance(sheet_context, Mapping) and current_id and sheet_context.get("workspace_id") == current_id:
        current_path = str(sheet_context.get("path") or "")
        if current_path:
            candidates.append(current_path)
    root = (
        getattr(getattr(engine, "_workspace_ref", None), "root", None)
        or getattr(getattr(engine, "config", None), "workspace_root", None)
    )

    def canonical_files(paths: list[str]) -> list[str]:
        found: list[str] = []
        if not root:
            return found
        for path in paths:
            if not path or len(path) > 300:
                continue
            try:
                ident = resolve_canonical(root, path)
                if Path(ident.relative).suffix.lower() not in {".xlsx", ".xlsm", ".xls", ".xlsb", ".csv", ".tsv"}:
                    continue
                if len(ident.public) <= 300 and ident.public not in found:
                    found.append(ident.public)
            except (IdentityError, ValueError, OSError):
                continue
        return found

    candidates = canonical_files(candidates)
    # Literal references in the current request outrank an arbitrary first file.
    user_text = last_user_text(engine)
    candidates.sort(key=lambda path: Path(path).name not in user_text)
    return bound_state(
        "ui.surface",
        {
            "user_text": user_text,
            "tools_used": tools[-10:],
            "files_written": canonical_files(files_written)[:10],
            "turn_outcome": turn_outcome or "ok",
            "candidate_files": candidates[:3],
        },
    )


_CHECKLIST_SPLIT_RE = re.compile(
    r"[，,；;、。]|以及|另外|然后|同时|并且?|再(?:把|将|给)?|\band\b",
    re.IGNORECASE,
)
_CHECKLIST_GREETINGS = frozenset(
    {"你好", "您好", "hi", "hello", "hey", "谢谢", "thanks", "在吗", "早上好", "下午好", "晚上好"}
)


def delivery_checklist(engine: Any, *, limit: int | None = 5) -> list[dict[str, Any]]:
    """用户请求的可核对事项清单（≤5 项），供 mutation.verify 逐项判定。

    优先取任务清单的子任务标题；无任务时把 user_text 按子句切分；
    仍为空则整段回退为单项。
    """
    items: list[str] = []
    task_facts: list[dict[str, Any]] = []
    store = getattr(engine, "_task_store", None)
    task_list = getattr(store, "current", None) if store is not None else None
    if task_list is not None:
        for item in getattr(task_list, "items", None) or ():
            title = str(getattr(item, "title", "") or "").strip()
            if title:
                items.append(title)
                criteria = getattr(item, "verification_criteria", None)
                if isinstance(criteria, Mapping):
                    task_facts.append({
                        "target_file": str(criteria.get("target_file") or "")[:300],
                        "target_sheet": str(criteria.get("target_sheet") or "")[:100],
                        "target_range": str(criteria.get("target_range") or "")[:100],
                        "passed": criteria.get("passed"),
                    })
                    continue
                task_facts.append({
                    "target_file": str(getattr(criteria, "target_file", "") or "")[:300],
                    "target_sheet": str(getattr(criteria, "target_sheet", "") or "")[:100],
                    "target_range": str(getattr(criteria, "target_range", "") or "")[:100],
                    "passed": getattr(criteria, "passed", None),
                })
    if not items:
        text = last_user_text(engine, limit=None).strip()
        for part in _CHECKLIST_SPLIT_RE.split(text):
            piece = part.strip(" \t，,；;、。")
            if len(piece) < 4:
                continue
            if piece.lower() in _CHECKLIST_GREETINGS:
                continue
            items.append(piece)
        if not items and text:
            items.append(text.strip())
    return [
        {
            "id": f"item_{index + 1}", "text": _clip_text(text, 80),
            "text_truncated": len(text) > 80,
            **(task_facts[index] if index < len(task_facts) else {}),
        }
        for index, text in enumerate(items[:limit])
    ]


def mutation_verify_state_from_engine(engine: Any, chat_result: Any | None = None) -> dict[str, Any]:
    """Project deterministic post-write facts for ``mutation.verify``."""
    state = getattr(engine, "_state", None)
    affected = [
        str(item)
        for item in (getattr(state, "affected_files", None) or [])
        if item
    ][:10]
    all_operations = list(getattr(state, "write_operations_log", None) or []) if state else []
    operations = all_operations[-10:]
    tools: list[str] = []
    write_evidence: list[dict[str, Any]] = []
    failed_tools = 0
    for item in getattr(chat_result, "tool_calls", None) or ():
        name = str(getattr(item, "tool_name", "") or getattr(item, "name", "") or "")
        if name:
            tools.append(name)
        # ToolResult.value.meta.write_verification is the deterministic
        # post-commit read-back produced by the write path.  Project only
        # bounded counts/status, never cell contents or workbook bytes.
        structured = getattr(item, "structured", None)
        value = getattr(structured, "value", None) if structured is not None else None
        meta = value.get("meta") if isinstance(value, Mapping) else None
        verification = meta.get("write_verification") if isinstance(meta, Mapping) else None
        if not isinstance(verification, Mapping) and isinstance(value, Mapping):
            verification = value.get("write_verification")
        if isinstance(verification, Mapping):
            mismatches = verification.get("mismatches") or []
            style_changes = verification.get("style_changes") or []
            style_mismatches = verification.get("style_mismatches") or []
            structure_changes = verification.get("structure_changes") or []
            write_evidence.append({
                "tool": name,
                "file_path": str((getattr(item, "arguments", None) or {}).get("file_path") or ""),
                "sheet": str(verification.get("sheet") or ""),
                "status": str(verification.get("status") or "unknown"),
                "skipped": bool(verification.get("skipped")),
                "reason": str(verification.get("reason") or "")[:160],
                "error_code": str(verification.get("error_code") or ""),
                "verification_kind": str(verification.get("verification_kind") or ""),
                "total_changes": max(0, int(verification.get("total_changes") or 0)),
                "value_changes": len(verification.get("value_changes") or []),
                "formula_changes": len(verification.get("formula_changes") or []),
                "formula_intended": max(0, int(verification.get("formula_intended_count") or 0)),
                "formula_verified": max(0, int(verification.get("formula_verified_count") or 0)),
                "formula_overwritten": max(0, int(verification.get("formula_overwritten_count") or 0)),
                "style_ok_count": sum(len(c.get("ok") or []) for c in style_changes if isinstance(c, Mapping)),
                "style_mismatch_count": len(style_mismatches) if isinstance(style_mismatches, list) else 0,
                "structure_verified": sum(
                    1 for c in structure_changes if isinstance(c, Mapping) and c.get("verified") is True
                ) if isinstance(structure_changes, list) else 0,
                "structure_unverified": sum(
                    1 for c in structure_changes if isinstance(c, Mapping) and c.get("verified") is False
                ) if isinstance(structure_changes, list) else 0,
                "mismatch_count": max(int(verification.get("mismatch_count") or 0), len(mismatches) if isinstance(mismatches, list) else 0),
                "truncated": bool(verification.get("truncated")),
                "sampled": bool(verification.get("sampled")),
                "content_version": str(verification.get("content_version") or "")[:120],
            })
        if not bool(getattr(item, "success", True)):
            failed_tools += 1
    style_mismatch_total = sum(int(item.get("style_mismatch_count") or 0) for item in write_evidence)
    formula_overwritten_total = sum(int(item.get("formula_overwritten") or 0) for item in write_evidence)
    structure_unverified_total = sum(int(item.get("structure_unverified") or 0) for item in write_evidence)
    checklist = delivery_checklist(engine, limit=None)
    verification_facts = {
        "success": bool(getattr(chat_result, "success", True)) if chat_result is not None else True,
        "truncated": bool(getattr(chat_result, "truncated", False)) if chat_result is not None else False,
        "affected_file_count": len(affected),
        "write_operation_count": len(all_operations),
        "failed_tool_count": failed_tools,
        "write_evidence_count": len(write_evidence),
        "verified_write_count": sum(
            1 for item in write_evidence if item.get("status") in {"success", "ok"} and not item.get("skipped")
        ),
        "mismatch_count": sum(int(item.get("mismatch_count") or 0) for item in write_evidence),
        "style_mismatch_count": style_mismatch_total,
        "formula_overwritten_count": formula_overwritten_total,
        "structure_unverified_count": structure_unverified_total,
        "evidence_truncated": len(write_evidence) > 10 or len(all_operations) > 10,
        "has_incomplete_evidence": bool(
            not write_evidence or len(write_evidence) < len(all_operations)
            or any(item.get("truncated") or item.get("sampled") or item.get("skipped") for item in write_evidence)
            or any(item.get("status") not in {"success", "ok"} for item in write_evidence)
            or style_mismatch_total > 0
            or formula_overwritten_total > 0
            or structure_unverified_total > 0
        ),
        "has_version_conflict": any("conflict" in str(item).lower() for item in operations),
        "checklist_count": len(checklist),
        "checklist_truncated": len(checklist) > 5 or any(item["text_truncated"] for item in checklist),
        "task_verification_failed": any(item.get("passed") is False for item in checklist),
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
            "write_evidence": write_evidence[-10:],
            "write_operations": operations,
            "checklist": checklist[:5],
        },
    )


def recovery_state_from_engine(
    engine: Any,
    tool_results: list[Any],
    *,
    reason: str = "breaker",
    breaker_triggered: bool = True,
) -> dict[str, Any]:
    from excelmanus.engine_core.error_payload import failure_class_for_error_code
    from excelmanus.tools.policy import READ_ONLY_SAFE_TOOLS
    # Only the trailing failure streak caused this breaker. Earlier failures
    # separated by a successful tool must not bias the recovery suggestion.
    failures: list[Any] = []
    for item in reversed(tool_results):
        if bool(getattr(item, "success", False)):
            break
        error_text = str(getattr(item, "error", "") or "")
        if error_text.startswith("工具未执行：连续 ") and "已触发熔断" in error_text:
            continue
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
            fields.get("failure_class") or failure_class_for_error_code(code)
        )
        retryable = getattr(item, "error_kind", "") in {"retryable", "transient"} or fields.get("retryable") is True
        tool_name = str(getattr(item, "tool_name", "") or "")
        committed = fields.get("committed", fields.get("write_committed"))
        errors.append({
            "tool": tool_name,
            "error_code": code[:80],
            "failure_class": kind[:60],
            "error": str(fields.get("message") or getattr(item, "error", "") or "")[:160],
            "remediation": str(fields.get("remediation") or "")[:160],
            "rejected": kind in {"permission_denied", "approval_denied", "approval_timeout", "blocked"},
            "committed": committed is True,
            "commit_unknown": tool_name not in READ_ONLY_SAFE_TOOLS and committed is not False,
            "retryable": retryable,
        })
    safe_to_retry = bool(errors) and all(
        item.get("retryable") and not item.get("rejected") and not item.get("committed") and not item.get("commit_unknown")
        for item in errors
    )
    return bound_state(
        "recovery.next_step",
        {
            "user_text": last_user_text(engine),
            "iteration": int(getattr(engine, "_last_iteration_count", 0) or 0),
            "consecutive_failures": len(failures),
            "breaker_triggered": breaker_triggered,
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
