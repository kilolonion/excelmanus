"""迁移脚本回归测试。"""

from __future__ import annotations

from pathlib import Path

from excelmanus.skillpacks.models import Skillpack
from scripts.migrate_skills_to_standard import _build_frontmatter, migrate_skills


def test_build_frontmatter_does_not_emit_removed_context_fields() -> None:
    skill = Skillpack(
        name="demo",
        description="测试",
        allowed_tools=["read_excel"],
        triggers=["分析"],
        instructions="说明",
        source="project",
        root_dir="/tmp/demo",
    )

    frontmatter = _build_frontmatter(skill, inject_defaults=True)
    assert "context" not in frontmatter
    assert "agent" not in frontmatter


def test_migrate_skills_dry_run_succeeds_without_removed_field_access(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    extra_root = tmp_path / "extra"
    for path in (home, workspace, extra_root):
        path.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(tmp_path)

    skill_dir = extra_root / "demo"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                "name: demo",
                "description: 测试技能",
                "allowed-tools:",
                "  - read_excel",
                "triggers: []",
                "---",
                "说明正文",
            ]
        ),
        encoding="utf-8",
    )

    report = migrate_skills(
        workspace_root=workspace,
        inject_defaults=False,
        dry_run=True,
        extra_dirs=(str(extra_root),),
    )
    assert report.failed_files == []
    assert report.invalid_yaml == []
