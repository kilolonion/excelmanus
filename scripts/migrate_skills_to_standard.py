"""Migrate SKILL.md frontmatter to the current standard schema.

This module is intentionally lightweight so it can be imported by tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Any, Iterable

from excelmanus.skillpacks.frontmatter import (
    FrontmatterError,
    parse_frontmatter as parse_frontmatter_text,
    parse_scalar as parse_frontmatter_scalar,
    serialize_frontmatter as serialize_frontmatter_text,
)
from excelmanus.skillpacks.models import Skillpack

_FRONTMATTER_PATTERN = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)


@dataclass
class MigrationReport:
    """迁移结果摘要。"""

    migrated_files: list[str] = field(default_factory=list)
    skipped_files: list[str] = field(default_factory=list)
    failed_files: list[str] = field(default_factory=list)
    invalid_yaml: list[str] = field(default_factory=list)


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """将 SKILL.md 拆分为 frontmatter 字典与正文。"""
    matched = _FRONTMATTER_PATTERN.match(text)
    if not matched:
        return {}, text
    frontmatter_raw, body = matched.groups()
    payload = parse_frontmatter_text(frontmatter_raw)
    return payload, body


def _pick(frontmatter: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in frontmatter:
            return frontmatter[key]
    return None


def _as_bool(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        parsed = parse_frontmatter_scalar(value)
        if isinstance(parsed, bool):
            return parsed
    raise ValueError(f"expected bool, got {value!r}")


def _as_str(value: Any, *, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, str):
        return value
    raise ValueError(f"expected str, got {value!r}")


def _as_optional_str(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    raise ValueError(f"expected optional str, got {value!r}")


def _as_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"expected list[str], got {value!r}")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError(f"expected list[str], got {value!r}")
        if item.strip():
            result.append(item)
    return result


def _extract_skillpack(frontmatter: dict[str, Any], body: str, skill_file: Path) -> Skillpack:
    name = _as_str(_pick(frontmatter, "name")).strip()
    description = _as_str(_pick(frontmatter, "description")).strip()
    if not name or not description:
        raise ValueError("frontmatter requires non-empty name and description")

    file_patterns = _as_str_list(_pick(frontmatter, "file_patterns", "file-patterns"))
    resources = _as_str_list(_pick(frontmatter, "resources"))
    version = _as_str(_pick(frontmatter, "version"), default="1.0.0")
    disable_model_invocation = _as_bool(
        _pick(frontmatter, "disable_model_invocation", "disable-model-invocation"),
        default=False,
    )
    user_invocable = _as_bool(
        _pick(frontmatter, "user_invocable", "user-invocable"),
        default=True,
    )
    argument_hint = _as_str(_pick(frontmatter, "argument_hint", "argument-hint"), default="")

    raw_hooks = _pick(frontmatter, "hooks")
    hooks = raw_hooks if isinstance(raw_hooks, dict) else {}
    raw_metadata = _pick(frontmatter, "metadata")
    metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
    model = _as_optional_str(_pick(frontmatter, "model"))

    command_dispatch = _as_str(
        _pick(frontmatter, "command_dispatch", "command-dispatch"),
        default="none",
    )
    if command_dispatch not in {"none", "tool"}:
        command_dispatch = "none"
    command_tool = _as_optional_str(_pick(frontmatter, "command_tool", "command-tool"))

    required_mcp_servers = _as_str_list(
        _pick(frontmatter, "required_mcp_servers", "required-mcp-servers")
    )
    required_mcp_tools = _as_str_list(
        _pick(frontmatter, "required_mcp_tools", "required-mcp-tools")
    )

    known_keys = {
        "name",
        "description",
        "file_patterns",
        "file-patterns",
        "resources",
        "version",
        "disable_model_invocation",
        "disable-model-invocation",
        "user_invocable",
        "user-invocable",
        "argument_hint",
        "argument-hint",
        "context",
        "agent",
        "hooks",
        "model",
        "metadata",
        "command_dispatch",
        "command-dispatch",
        "command_tool",
        "command-tool",
        "required_mcp_servers",
        "required-mcp-servers",
        "required_mcp_tools",
        "required-mcp-tools",
        "allowed_tools",
        "allowed-tools",
        "triggers",
        "priority",
    }
    extensions = {k: v for k, v in frontmatter.items() if k not in known_keys}

    return Skillpack(
        name=name,
        description=description,
        instructions=body.strip(),
        source="project",
        root_dir=str(skill_file.parent),
        file_patterns=file_patterns,
        resources=resources,
        version=version,
        disable_model_invocation=disable_model_invocation,
        user_invocable=user_invocable,
        argument_hint=argument_hint,
        hooks=hooks,
        model=model,
        metadata=metadata,
        command_dispatch=command_dispatch,
        command_tool=command_tool,
        required_mcp_servers=required_mcp_servers,
        required_mcp_tools=required_mcp_tools,
        extensions=extensions,
    )


def _build_frontmatter(skill: Skillpack, inject_defaults: bool) -> dict[str, Any]:
    """构建规范化 frontmatter。

    `context`/`agent` 已废弃，故意不包含。
    """
    payload: dict[str, Any] = {
        "name": skill.name,
        "description": skill.description,
    }
    if inject_defaults or skill.file_patterns:
        payload["file-patterns"] = list(skill.file_patterns)
    if inject_defaults or skill.resources:
        payload["resources"] = list(skill.resources)
    if inject_defaults or skill.version != "1.0.0":
        payload["version"] = skill.version
    if inject_defaults or skill.disable_model_invocation:
        payload["disable-model-invocation"] = bool(skill.disable_model_invocation)
    if inject_defaults or not skill.user_invocable:
        payload["user-invocable"] = bool(skill.user_invocable)
    if inject_defaults or skill.argument_hint:
        payload["argument-hint"] = skill.argument_hint
    if inject_defaults or skill.command_dispatch != "none":
        payload["command-dispatch"] = skill.command_dispatch
    if skill.command_tool:
        payload["command-tool"] = skill.command_tool
    if skill.hooks:
        payload["hooks"] = skill.hooks
    if skill.model is not None:
        payload["model"] = skill.model
    if skill.metadata:
        payload["metadata"] = skill.metadata
    if skill.required_mcp_servers:
        payload["required-mcp-servers"] = list(skill.required_mcp_servers)
    if skill.required_mcp_tools:
        payload["required-mcp-tools"] = list(skill.required_mcp_tools)
    if skill.extensions:
        payload.update(skill.extensions)
    return payload


def _iter_roots(workspace_root: Path, extra_dirs: Iterable[str]) -> list[Path]:
    roots: list[Path] = [workspace_root / ".agents" / "skills"]
    for raw in extra_dirs:
        roots.append(Path(raw).expanduser())
    seen: set[Path] = set()
    ordered: list[Path] = []
    for root in roots:
        resolved = root.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        ordered.append(resolved)
    return ordered


def _render_skill(frontmatter: dict[str, Any], body: str) -> str:
    fm = serialize_frontmatter_text(frontmatter)
    stripped_body = body.lstrip("\n")
    if stripped_body:
        return f"---\n{fm}\n---\n{stripped_body}"
    return f"---\n{fm}\n---\n"


def migrate_skills(
    *,
    workspace_root: str | Path,
    inject_defaults: bool = False,
    dry_run: bool = False,
    extra_dirs: tuple[str, ...] = (),
) -> MigrationReport:
    """将可发现的 SKILL.md 文件迁移为规范化 frontmatter。"""
    report = MigrationReport()
    workspace = Path(workspace_root).expanduser().resolve()
    for root in _iter_roots(workspace, extra_dirs):
        if not root.exists() or not root.is_dir():
            continue
        for skill_file in sorted(root.rglob("SKILL.md")):
            try:
                raw = skill_file.read_text(encoding="utf-8")
            except OSError:
                report.failed_files.append(str(skill_file))
                continue

            try:
                frontmatter, body = _split_frontmatter(raw)
            except FrontmatterError:
                report.invalid_yaml.append(str(skill_file))
                continue

            try:
                skill = _extract_skillpack(frontmatter, body, skill_file)
                normalized = _build_frontmatter(skill, inject_defaults=inject_defaults)
                rendered = _render_skill(normalized, body)
                if not dry_run and rendered != raw:
                    skill_file.write_text(rendered, encoding="utf-8")
                report.migrated_files.append(str(skill_file))
            except Exception:
                report.failed_files.append(str(skill_file))
    return report

