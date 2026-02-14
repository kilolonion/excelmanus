"""防止系统技能文档回归到旧工具名。"""

from __future__ import annotations

from pathlib import Path


def test_system_skills_do_not_reference_legacy_run_python_script() -> None:
    root = Path("excelmanus/skillpacks/system")
    offenders: list[str] = []
    for skill_file in root.rglob("SKILL.md"):
        text = skill_file.read_text(encoding="utf-8")
        if "run_python_script" in text:
            offenders.append(str(skill_file))
    assert not offenders, f"发现旧工具名残留: {offenders}"
