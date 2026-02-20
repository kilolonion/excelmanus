"""Skillpack 协议文档契约测试。"""

from __future__ import annotations

from pathlib import Path
import re

import pytest

from excelmanus.config import ExcelManusConfig
from excelmanus.skillpacks.loader import SkillpackLoader
from excelmanus.tools import ToolDef, ToolRegistry

REPO_ROOT = Path(__file__).resolve().parents[1]
README_PATH = REPO_ROOT / "README.md"
SKILLPACK_PROTOCOL_PATH = REPO_ROOT / "docs" / "skillpack_protocol.md"
TASKS_ROOT = REPO_ROOT / "tasks"
SYSTEM_SKILLPACK_ROOT = REPO_ROOT / "excelmanus" / "skillpacks" / "system"
HISTORY_NOTICE_MARKER = "历史文档声明（Skillpack 协议）"
LEGACY_TERM_PATTERN = re.compile(
    r"hint_direct|confident_direct|llm_confirm|fork_plan|Skillpack\.context"
)


def _tool_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register_tool(
        ToolDef(
            name="read_excel",
            description="读取",
            input_schema={"type": "object", "properties": {}},
            func=lambda: "ok",
        )
    )
    return registry


def _extract_readme_skillpack_list(text: str) -> set[str]:
    marker = "内置 Skillpacks："
    start = text.find(marker)
    if start == -1:
        marker = "当前内置（system）Skillpacks："
        start = text.find(marker)
    assert start != -1, "README 缺少内置 Skillpack 清单段落"

    skills: set[str] = set()
    started = False
    for line in text[start + len(marker) :].splitlines():
        stripped = line.strip()
        if not stripped and started:
            break
        match = re.match(r"-\s+`([a-z0-9._/-]+)`", stripped)
        if match:
            skills.add(match.group(1))
            started = True
        elif started and stripped and not stripped.startswith("-"):
            break
    assert skills, "README 内置 Skillpack 清单为空"
    return skills


class TestSkillpackDocsContract:
    def test_protocol_has_hook_section_and_compat_matrix(self) -> None:
        text = SKILLPACK_PROTOCOL_PATH.read_text(encoding="utf-8")
        assert "## 7. Hook 协议" in text
        assert "PreToolUse` / `preToolUse` / `pre_tool_use" in text
        assert "DENY > ASK > ALLOW > CONTINUE" in text
        assert "EXCELMANUS_HOOKS_COMMAND_ENABLED=false" in text

    def test_readme_hook_semantics_match_runtime_contract(self) -> None:
        text = README_PATH.read_text(encoding="utf-8")
        assert "PreToolUse` / `preToolUse` / `pre_tool_use" in text
        assert "`EXCELMANUS_HOOKS_COMMAND_ENABLED=true`" in text
        assert "命令" in text

    def test_readme_openclaw_row_matches_new_protocol(self) -> None:
        text = README_PATH.read_text(encoding="utf-8")

        row_pattern = re.compile(
            r"EXCELMANUS_SKILLS_DISCOVERY_INCLUDE_OPENCLAW.*?"
            r"`\.openclaw/skills`/`~/.openclaw/skills`",
            re.DOTALL,
        )
        assert row_pattern.search(text), (
            "README 的 OpenClaw 环境变量描述必须使用 "
            "`.openclaw/skills`/`~/.openclaw/skills`"
        )

    def test_loader_discovery_uses_openclaw_project_dir_not_workspace_skills(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        workspace = tmp_path / "workspace"
        home_dir = tmp_path / "home"
        system_dir = workspace / "system"
        user_dir = home_dir / ".excelmanus" / "skillpacks"
        project_dir = workspace / "project"
        for d in (workspace, home_dir, system_dir, user_dir, project_dir):
            d.mkdir(parents=True, exist_ok=True)

        monkeypatch.setenv("HOME", str(home_dir))
        monkeypatch.chdir(workspace)

        cfg = ExcelManusConfig(
            api_key="test-key",
            base_url="https://test.example.com/v1",
            model="test-model",
            workspace_root=str(workspace),
            skills_system_dir=str(system_dir),
            skills_user_dir=str(user_dir),
            skills_project_dir=str(project_dir),
            skills_discovery_include_agents=False,
            skills_discovery_include_claude=False,
            skills_discovery_include_openclaw=True,
        )
        loader = SkillpackLoader(cfg, _tool_registry())
        roots = loader._iter_discovery_roots()
        root_paths = {str(path) for _, path in roots}

        expected_project_openclaw = str((workspace / ".openclaw" / "skills").resolve())
        legacy_project_skills = str((workspace / "skills").resolve())
        expected_user_openclaw = str((home_dir / ".openclaw" / "skills").resolve())

        assert expected_project_openclaw in root_paths
        assert expected_user_openclaw in root_paths
        assert legacy_project_skills not in root_paths

    def test_readme_system_skillpack_list_matches_filesystem(self) -> None:
        text = README_PATH.read_text(encoding="utf-8")
        readme_skills = _extract_readme_skillpack_list(text)
        filesystem_skills = {
            path.parent.name for path in SYSTEM_SKILLPACK_ROOT.glob("*/SKILL.md")
        }

        assert readme_skills == filesystem_skills, (
            "README 内置 Skillpack 清单与文件系统不一致："
            f" readme={sorted(readme_skills)} filesystem={sorted(filesystem_skills)}"
        )

    def test_legacy_term_task_docs_have_history_notice(self) -> None:
        missing_notice: list[str] = []
        for path in sorted(TASKS_ROOT.rglob("*.md")):
            if "archive" in path.parts:
                continue
            text = path.read_text(encoding="utf-8")
            if LEGACY_TERM_PATTERN.search(text) and HISTORY_NOTICE_MARKER not in text:
                missing_notice.append(str(path.relative_to(REPO_ROOT)))

        assert not missing_notice, (
            "以下任务文档命中旧术语但缺少历史声明：\n"
            + "\n".join(missing_notice)
        )
