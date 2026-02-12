"""Skillpack Loader / Router 单元测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from excelmanus.config import ExcelManusConfig
from excelmanus.skillpacks import SkillpackLoader, SkillRouter
from excelmanus.tools import ToolDef, ToolRegistry


def _make_config(
    system_dir: Path,
    user_dir: Path,
    project_dir: Path,
    **overrides,
) -> ExcelManusConfig:
    defaults = dict(
        api_key="test-key",
        base_url="https://test.example.com/v1",
        model="test-model",
        skills_system_dir=str(system_dir),
        skills_user_dir=str(user_dir),
        skills_project_dir=str(project_dir),
        skills_skip_llm_confirm=True,
        skills_fastpath_min_score=3,
        skills_fastpath_min_gap=1,
    )
    defaults.update(overrides)
    return ExcelManusConfig(**defaults)


def _write_skillpack(
    root_dir: Path,
    name: str,
    *,
    description: str,
    allowed_tools: list[str],
    triggers: list[str],
) -> None:
    skill_dir = root_dir / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        "---",
        f"name: {name}",
        f"description: {description}",
        "allowed_tools:",
        *[f"  - {item}" for item in allowed_tools],
        "triggers:",
        *[f"  - {item}" for item in triggers],
        "---",
        "测试说明",
    ]
    (skill_dir / "SKILL.md").write_text("\n".join(lines), encoding="utf-8")


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
    registry.register_tool(
        ToolDef(
            name="create_chart",
            description="图表",
            input_schema={"type": "object", "properties": {}},
            func=lambda: "ok",
        )
    )
    return registry


class TestSkillpackLoader:
    def test_project_overrides_user_and_system(self, tmp_path: Path) -> None:
        system_dir = tmp_path / "system"
        user_dir = tmp_path / "user"
        project_dir = tmp_path / "project"
        for d in (system_dir, user_dir, project_dir):
            d.mkdir(parents=True, exist_ok=True)

        _write_skillpack(
            system_dir,
            "data_basic",
            description="system",
            allowed_tools=["read_excel"],
            triggers=["分析"],
        )
        _write_skillpack(
            user_dir,
            "data_basic",
            description="user",
            allowed_tools=["read_excel"],
            triggers=["分析"],
        )
        _write_skillpack(
            project_dir,
            "data_basic",
            description="project",
            allowed_tools=["read_excel"],
            triggers=["分析"],
        )

        config = _make_config(system_dir, user_dir, project_dir)
        loader = SkillpackLoader(config, _tool_registry())
        loaded = loader.load_all()
        assert loaded["data_basic"].description == "project"

    def test_soft_validate_unknown_allowed_tools(self, tmp_path: Path) -> None:
        system_dir = tmp_path / "system"
        user_dir = tmp_path / "user"
        project_dir = tmp_path / "project"
        for d in (system_dir, user_dir, project_dir):
            d.mkdir(parents=True, exist_ok=True)

        _write_skillpack(
            system_dir,
            "general_excel",
            description="test",
            allowed_tools=["read_excel", "unknown_tool"],
            triggers=["excel"],
        )

        config = _make_config(system_dir, user_dir, project_dir)
        loader = SkillpackLoader(config, _tool_registry())
        loader.load_all()
        assert any("unknown_tool" in warning for warning in loader.warnings)


class TestSkillRouter:
    @pytest.mark.asyncio
    async def test_hint_direct(self, tmp_path: Path) -> None:
        system_dir = tmp_path / "system"
        user_dir = tmp_path / "user"
        project_dir = tmp_path / "project"
        for d in (system_dir, user_dir, project_dir):
            d.mkdir(parents=True, exist_ok=True)

        _write_skillpack(
            system_dir,
            "chart_basic",
            description="图表",
            allowed_tools=["create_chart"],
            triggers=["图表"],
        )
        _write_skillpack(
            system_dir,
            "data_basic",
            description="分析",
            allowed_tools=["read_excel"],
            triggers=["分析"],
        )

        config = _make_config(system_dir, user_dir, project_dir)
        loader = SkillpackLoader(config, _tool_registry())
        loader.load_all()
        router = SkillRouter(config, loader)

        result = await router.route(
            "帮我分析并画图",
            skill_hints=["chart_basic"],
        )
        assert result.route_mode == "hint_direct"
        assert result.skills_used == ["chart_basic"]
        assert result.tool_scope == ["create_chart"]

    @pytest.mark.asyncio
    async def test_confident_direct(self, tmp_path: Path) -> None:
        system_dir = tmp_path / "system"
        user_dir = tmp_path / "user"
        project_dir = tmp_path / "project"
        for d in (system_dir, user_dir, project_dir):
            d.mkdir(parents=True, exist_ok=True)

        _write_skillpack(
            system_dir,
            "data_basic",
            description="分析",
            allowed_tools=["read_excel"],
            triggers=["分析"],
        )
        _write_skillpack(
            system_dir,
            "chart_basic",
            description="图表",
            allowed_tools=["create_chart"],
            triggers=["图表"],
        )

        config = _make_config(
            system_dir,
            user_dir,
            project_dir,
            skills_fastpath_min_score=3,
            skills_fastpath_min_gap=1,
        )
        loader = SkillpackLoader(config, _tool_registry())
        loader.load_all()
        router = SkillRouter(config, loader)

        result = await router.route("请分析这个文件")
        assert result.route_mode == "confident_direct"
        assert result.skills_used == ["data_basic"]
        assert result.tool_scope == ["read_excel"]
