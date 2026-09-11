"""技能目录：user-role 快照，不进 schema description。"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from typing import Any

_SKILL_DESC_MAX = 160


def collect_skill_entries(
    skills: Mapping[str, Any] | Iterable[tuple[str, str]],
    *,
    blocked: set[str] | None = None,
) -> list[tuple[str, str]]:
    blocked = blocked or set()
    items: list[tuple[str, str]] = []
    if isinstance(skills, Mapping):
        for name in sorted(skills):
            skill = skills[name]
            if bool(getattr(skill, "disable_model_invocation", False)):
                continue
            desc = str(getattr(skill, "description", "") or "").strip()
            items.append((str(name), desc))
    else:
        items = [(str(name), str(desc or "").strip()) for name, desc in skills]
    if blocked:
        clipped: list[tuple[str, str]] = []
        for name, desc in items:
            if name in blocked:
                suffix = "（需要更高权限）"
                clipped.append((name, (desc + suffix).strip() if desc else suffix.strip()))
            else:
                clipped.append((name, desc))
        return clipped
    return items


def render_available_skills(
    skills: Mapping[str, Any] | Iterable[tuple[str, str]],
    *,
    blocked: set[str] | None = None,
) -> str:
    """渲染 ``<system-reminder>`` 目录。digest 只看 (name, desc)。"""
    items = collect_skill_entries(skills, blocked=blocked)
    if not items:
        return ""

    lines = [
        "<system-reminder>",
        "A skill is a reusable set of task-specific instructions. The following skills are available in this session:",
        "",
        "<available_skills>",
    ]
    for name, desc in items:
        clipped = desc if len(desc) <= _SKILL_DESC_MAX else desc[: _SKILL_DESC_MAX - 1] + "…"
        if clipped:
            lines.append(f"- `{name}`: {clipped}")
        else:
            lines.append(f"- `{name}`")
    lines.extend(
        [
            "</available_skills>",
            "",
            "This catalog contains summaries only; do not treat a summary as the skill's instructions.",
            "If the user already invoked a skill with /name and its body is in this conversation, "
            "do not load that skill again.",
            "</system-reminder>",
        ]
    )
    return "\n".join(lines)


def catalog_digest(text: str) -> str:
    import hashlib

    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


def catalog_entries_digest(entries: Iterable[tuple[str, str]]) -> str:
    import hashlib

    canonical = "\n".join(
        json.dumps([name, desc], ensure_ascii=False) for name, desc in entries
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


_SKILL_GESTURE_RE = re.compile(r"(?:^|\s)/([A-Za-z][A-Za-z0-9_-]*)(?=\s|$)")


def parse_skill_gesture(text: str) -> str | None:
    """用户来源文本里的 ``/kebab-name`` 手势。控制命令不算技能。"""
    match = _SKILL_GESTURE_RE.search(text or "")
    if match is None:
        return None
    name = match.group(1)
    from excelmanus.control_commands import NORMALIZED_ALIAS_TO_CANONICAL_CONTROL_COMMAND, normalize_control_command

    if normalize_control_command(f"/{name}") in NORMALIZED_ALIAS_TO_CANONICAL_CONTROL_COMMAND:
        return None
    return name


def render_skill_invocation(name: str, body: str) -> str:
    return (
        f'<skill-invocation name="{name}">\n'
        f"{(body or '').strip()}\n"
        f"</skill-invocation>"
    )


def prepare_skill_followup(
    engine: Any,
    *,
    user_message: str,
    slash_command: str | None,
    route_result: Any,
) -> tuple[Any, str]:
    """斜杠技能改为 user-role invocation。返回 (改写后的 route, invocation 文本)。"""
    from excelmanus.skillpacks.router import SkillMatchResult

    name = (slash_command or "").strip() or (parse_skill_gesture(user_message) or "")
    if not name:
        return route_result, ""
    route_mode = getattr(route_result, "route_mode", "") or ""
    if route_mode in {"slash_not_found", "slash_not_user_invocable"}:
        return route_result, ""

    body = ""
    contexts = list(getattr(route_result, "system_contexts", None) or [])
    if contexts and (route_mode == "slash_direct" or slash_command):
        body = "\n\n".join(str(item) for item in contexts if str(item).strip())
    if not body:
        router = getattr(engine, "_skill_router", None)
        loader = getattr(router, "_loader", None) if router is not None else None
        packs: dict[str, Any] = {}
        getter = getattr(loader, "get_skillpacks", None)
        if callable(getter):
            try:
                packs = getter() or {}
            except Exception:
                packs = {}
        finder = getattr(router, "_find_skill_by_name", None) if router is not None else None
        skill = finder(skillpacks=packs, name=name) if callable(finder) else packs.get(name)
        if skill is None:
            return route_result, ""
        render = getattr(skill, "render_context_instructions_only", None)
        body = render() if callable(render) else str(getattr(skill, "instructions", "") or "")

    used = list(getattr(route_result, "skills_used", None) or [])
    if name not in used:
        used.append(name)
    rewritten = SkillMatchResult(
        skills_used=used,
        tool_scope=[],
        route_mode="all_tools",
        system_contexts=[],
        parameterized=bool(getattr(route_result, "parameterized", False)),
    )
    return rewritten, render_skill_invocation(name, body)


def attach_skill_catalog(engine: Any) -> str:
    """digest 变化时把技能目录作为 user 消息追加。digest 只看 (name, desc)。"""
    router = getattr(engine, "_skill_router", None)
    loader = getattr(router, "_loader", None) if router is not None else None
    getter = getattr(loader, "get_skillpacks", None)
    if not callable(getter):
        return ""
    try:
        packs = getter() or {}
    except Exception:
        return ""
    blocked: set[str] = set()
    resolver = getattr(engine, "_skill_resolver", None)
    if resolver is not None and not getattr(engine, "_full_access_enabled", True):
        getter = getattr(resolver, "blocked_skillpacks", None)
        raw = getter() if callable(getter) else getter
        if raw:
            blocked = set(raw)
    entries = collect_skill_entries(packs, blocked=blocked)
    if not entries:
        return ""
    digest = catalog_entries_digest(entries)
    if digest == getattr(engine, "_skill_catalog_digest", None):
        return ""
    text = render_available_skills(entries)
    if not text:
        return ""
    engine._skill_catalog_digest = digest
    memory = getattr(engine, "_memory", None) or getattr(engine, "memory", None)
    if memory is not None:
        memory.add_user_message(text, hidden=True, prompt_kind="skill_catalog")
    return text
