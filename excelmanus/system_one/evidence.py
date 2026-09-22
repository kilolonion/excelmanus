"""Small, deterministic retrieval and current-step evidence for Jev."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any


def _terms(text: str) -> set[str]:
    text = text.casefold()
    words = set(re.findall(r"[a-z0-9_]{2,}", text))
    for run in re.findall(r"[\u3400-\u9fff]+", text):
        words.update(run[i:i + 2] for i in range(len(run) - 1))
    return words - {"the", "and", "for", "with", "this", "that", "可以", "进行", "用户", "使用"}


def rank_skills(text: str, entries: list[tuple[str, str]], *, limit: int = 3) -> list[tuple[str, str]]:
    """Recall from the complete catalog before applying the small choice pack."""
    query = _terms(text)
    ranked: list[tuple[int, str, str]] = []
    for name, description in entries:
        score = 4 * len(query & _terms(name)) + len(query & _terms(description))
        if name and name.casefold() in text.casefold():
            score += 20
        if score:
            ranked.append((score, name, description))
    ranked.sort(key=lambda row: (-row[0], row[1]))
    return [(name, description) for _, name, description in ranked[:limit]]


_DISCOVERY_TOOLS = frozenset({
    "introspect_capability", "tool_detail", "skill", "task_create", "task_update",
    "list_subagents", "sleep", "list_directory",
})


def loop_state(engine: Any, results: list[Any], *, iteration: int) -> dict[str, Any] | None:
    """Use this turn's actual observations, never last turn's tool-name cache."""
    from excelmanus.system_one.adapter import bound_state, last_user_text

    substantive = [r for r in results if getattr(r, "tool_name", "") not in _DISCOVERY_TOOLS]
    if not substantive or not any(getattr(r, "success", False) for r in substantive):
        return None
    observations = []
    for item in substantive[-5:]:
        structured = getattr(item, "structured", None)
        result_text = str(getattr(structured, "model_text", None) or getattr(item, "result", ""))
        observations.append({
            "tool": str(getattr(item, "tool_name", "")),
            "success": bool(getattr(item, "success", False)),
            "result_head": result_text[:300],
            "error": str(getattr(item, "error", "") or "")[:160],
            "truncated": bool(getattr(structured, "truncated", False)) or len(result_text) > 300,
        })
    tasks = getattr(getattr(engine, "_task_store", None), "current", None)
    pending = []
    for item in getattr(tasks, "items", ()) or ():
        status = getattr(item, "status", "")
        status = getattr(status, "value", status)
        if status not in {"completed", "done"}:
            pending.append(str(getattr(item, "title", ""))[:160])
    return bound_state("loop.wrap", {
        "user_text": last_user_text(engine),
        "iteration": iteration,
        "last_tools": [str(getattr(r, "tool_name", "")) for r in results[-10:]],
        "observations": observations,
        "pending_items": pending[:10],
        "has_more_results": len(substantive) > 5,
    })


def delivery_requires_inspection(state: Mapping[str, Any]) -> bool:
    """Evidence checks remain usable when the auxiliary provider is unavailable."""
    facts = state.get("verification_facts") or {}
    return bool(
        facts.get("has_incomplete_evidence") or facts.get("mismatch_count")
        or facts.get("evidence_truncated") or facts.get("has_version_conflict")
        or facts.get("failed_tool_count")
        or (facts.get("write_operation_count") and not facts.get("write_evidence_count"))
        or facts.get("checklist_truncated")
        or facts.get("task_verification_failed")
    )
