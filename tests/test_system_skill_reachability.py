"""系统技能可达性测试。"""

from __future__ import annotations

from pathlib import Path

from excelmanus.config import ExcelManusConfig
from excelmanus.skillpacks.loader import SkillpackLoader
from excelmanus.tools import ToolRegistry

REPO_ROOT = Path(__file__).resolve().parents[1]
SYSTEM_SKILL_ROOT = REPO_ROOT / "excelmanus" / "skillpacks" / "system"


def _make_loader(tmp_path: Path) -> SkillpackLoader:
    user_dir = tmp_path / "user_skills"
    project_dir = tmp_path / "project_skills"
    user_dir.mkdir(parents=True, exist_ok=True)
    project_dir.mkdir(parents=True, exist_ok=True)
    config = ExcelManusConfig(
        api_key="test-key",
        base_url="https://test.example.com/v1",
        model="test-model",
        workspace_root=str(tmp_path),
        skills_system_dir=str(SYSTEM_SKILL_ROOT),
        skills_user_dir=str(user_dir),
        skills_project_dir=str(project_dir),
        skills_discovery_enabled=False,
    )
    return SkillpackLoader(config, ToolRegistry())


def test_system_skill_data_basic_loads_successfully(tmp_path: Path) -> None:
    loader = _make_loader(tmp_path)
    skillpacks = loader.load_all()
    assert "data_basic" in skillpacks
    assert skillpacks["data_basic"].description


def test_system_skills_no_general_excel(tmp_path: Path) -> None:
    """v5: general_excel fallback skillpack has been deleted."""
    loader = _make_loader(tmp_path)
    skillpacks = loader.load_all()
    assert "general_excel" not in skillpacks

