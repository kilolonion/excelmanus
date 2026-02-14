#!/usr/bin/env python3
"""将历史 Skill frontmatter 一次性迁移为标准协议格式。"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from excelmanus.config import ExcelManusConfig
from excelmanus.skillpacks.frontmatter import FrontmatterError, serialize_frontmatter
from excelmanus.skillpacks.loader import SkillpackLoader, SkillpackValidationError
from excelmanus.tools import ToolRegistry


@dataclass
class MigrationReport:
    migrated_count: int = 0
    failed_files: list[str] = field(default_factory=list)
    name_conflicts: list[dict[str, Any]] = field(default_factory=list)
    invalid_yaml: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "migrated_count": self.migrated_count,
            "failed_files": self.failed_files,
            "name_conflicts": self.name_conflicts,
            "invalid_yaml": self.invalid_yaml,
        }


def _build_config(workspace_root: Path, extra_dirs: tuple[str, ...]) -> ExcelManusConfig:
    return ExcelManusConfig(
        api_key="migration-placeholder",
        base_url="https://example.invalid/v1",
        model="migration-placeholder",
        workspace_root=str(workspace_root),
        skills_discovery_enabled=True,
        skills_discovery_scan_workspace_ancestors=True,
        skills_discovery_include_agents=True,
        skills_discovery_include_claude=True,
        skills_discovery_include_openclaw=True,
        skills_discovery_extra_dirs=extra_dirs,
    )


def _build_frontmatter(skill, *, inject_defaults: bool) -> dict[str, Any]:
    frontmatter: dict[str, Any] = {
        "name": skill.name,
        "description": skill.description,
    }

    if inject_defaults or skill.allowed_tools:
        frontmatter["allowed-tools"] = list(skill.allowed_tools)
    if inject_defaults or skill.triggers:
        frontmatter["triggers"] = list(skill.triggers)
    if inject_defaults or skill.file_patterns:
        frontmatter["file-patterns"] = list(skill.file_patterns)
    if inject_defaults or skill.resources:
        frontmatter["resources"] = list(skill.resources)
    if inject_defaults or int(skill.priority) != 0:
        frontmatter["priority"] = int(skill.priority)
    if inject_defaults or skill.version != "1.0.0":
        frontmatter["version"] = skill.version
    if inject_defaults or skill.disable_model_invocation:
        frontmatter["disable-model-invocation"] = bool(skill.disable_model_invocation)
    if inject_defaults or not skill.user_invocable:
        frontmatter["user-invocable"] = bool(skill.user_invocable)
    if inject_defaults or skill.argument_hint:
        frontmatter["argument-hint"] = skill.argument_hint
    if inject_defaults or skill.context != "normal":
        frontmatter["context"] = skill.context
    if skill.agent:
        frontmatter["agent"] = skill.agent
    if inject_defaults or skill.hooks:
        frontmatter["hooks"] = dict(skill.hooks)
    if skill.model:
        frontmatter["model"] = skill.model
    if inject_defaults or skill.metadata:
        frontmatter["metadata"] = dict(skill.metadata)
    if inject_defaults or skill.command_dispatch != "none":
        frontmatter["command-dispatch"] = skill.command_dispatch
    if skill.command_tool:
        frontmatter["command-tool"] = skill.command_tool
    if inject_defaults or skill.required_mcp_servers:
        frontmatter["required-mcp-servers"] = list(skill.required_mcp_servers)
    if inject_defaults or skill.required_mcp_tools:
        frontmatter["required-mcp-tools"] = list(skill.required_mcp_tools)

    for key, value in dict(skill.extensions).items():
        if key in frontmatter:
            continue
        frontmatter[key] = value

    return frontmatter


def _iter_skill_files(loader: SkillpackLoader) -> list[tuple[str, Path, Path]]:
    files: list[tuple[str, Path, Path]] = []
    seen_paths: set[str] = set()
    roots = loader._iter_discovery_roots()
    for source, root in roots:
        if not root.exists() or not root.is_dir():
            continue
        for skill_file in sorted(root.rglob("SKILL.md"), key=lambda p: str(p).lower()):
            resolved = str(skill_file.resolve())
            if resolved in seen_paths:
                continue
            seen_paths.add(resolved)
            files.append((source, root, skill_file))
    return files


def migrate_skills(
    *,
    workspace_root: Path,
    inject_defaults: bool,
    dry_run: bool,
    extra_dirs: tuple[str, ...],
) -> MigrationReport:
    config = _build_config(workspace_root=workspace_root, extra_dirs=extra_dirs)
    loader = SkillpackLoader(config=config, tool_registry=ToolRegistry())
    report = MigrationReport()

    name_to_file: dict[str, str] = {}
    skill_files = _iter_skill_files(loader)

    for source, _root, skill_file in skill_files:
        rel_path = str(skill_file)
        try:
            text = skill_file.read_text(encoding="utf-8")
            frontmatter, body = SkillpackLoader._split_frontmatter(text=text, skill_file=skill_file)
            normalized = loader._normalize_frontmatter(frontmatter)
            name_value = normalized.get("name")
            if isinstance(name_value, str) and name_value.strip():
                name = name_value.strip()
                if name in name_to_file:
                    report.name_conflicts.append({
                        "name": name,
                        "winner": rel_path,
                        "shadowed": name_to_file[name],
                    })
                name_to_file[name] = rel_path
        except (FrontmatterError, SkillpackValidationError):
            report.invalid_yaml.append(rel_path)
            report.failed_files.append(rel_path)
            continue
        except Exception:
            report.failed_files.append(rel_path)
            continue

        try:
            skill = loader._parse_skillpack_file(
                source=source,
                skill_dir=skill_file.parent,
                skill_file=skill_file,
            )
            frontmatter_dict = _build_frontmatter(skill, inject_defaults=inject_defaults)
            frontmatter_text = serialize_frontmatter(frontmatter_dict)
            body_text = body.rstrip("\n")
            rewritten = f"---\n{frontmatter_text}\n---\n{body_text}\n"
            if rewritten != text:
                if not dry_run:
                    skill_file.write_text(rewritten, encoding="utf-8")
                report.migrated_count += 1
        except Exception:
            report.failed_files.append(rel_path)

    return report


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="迁移 SKILL.md frontmatter 到标准通用协议格式。",
    )
    parser.add_argument(
        "--workspace-root",
        default=".",
        help="工作区根目录（默认当前目录）。",
    )
    parser.add_argument(
        "--inject-defaults",
        action="store_true",
        help="写入默认字段值（默认仅写入非默认/非空字段）。",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅统计不写入文件。",
    )
    parser.add_argument(
        "--extra-dir",
        action="append",
        default=[],
        help="额外扫描目录，可重复传入。",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    workspace_root = Path(args.workspace_root).expanduser().resolve()
    report = migrate_skills(
        workspace_root=workspace_root,
        inject_defaults=bool(args.inject_defaults),
        dry_run=bool(args.dry_run),
        extra_dirs=tuple(str(item) for item in args.extra_dir if str(item).strip()),
    )
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
