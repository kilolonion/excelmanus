"""Skillpack Loader / Router 单元测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from excelmanus.config import ExcelManusConfig
from excelmanus.skillpacks import SkillpackLoader, SkillRouter
from excelmanus.skillpacks.loader import SkillpackValidationError
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
    disable_model_invocation: bool | None = None,
    user_invocable: bool | None = None,
    argument_hint: str | None = None,
    context: str | None = None,
    instructions: str = "测试说明",
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
    ]
    if disable_model_invocation is not None:
        flag = "true" if disable_model_invocation else "false"
        lines.append(f"disable_model_invocation: {flag}")
    if user_invocable is not None:
        flag = "true" if user_invocable else "false"
        lines.append(f"user_invocable: {flag}")
    if argument_hint is not None:
        lines.append(f"argument_hint: {argument_hint}")
    if context is not None:
        lines.append(f"context: {context}")

    lines.extend(["---", instructions])
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

    def test_user_invocable_default_true(self, tmp_path: Path) -> None:
        system_dir = tmp_path / "system"
        user_dir = tmp_path / "user"
        project_dir = tmp_path / "project"
        for d in (system_dir, user_dir, project_dir):
            d.mkdir(parents=True, exist_ok=True)

        _write_skillpack(
            system_dir,
            "data_basic",
            description="测试",
            allowed_tools=["read_excel"],
            triggers=["分析"],
        )

        config = _make_config(system_dir, user_dir, project_dir)
        loader = SkillpackLoader(config, _tool_registry())
        loaded = loader.load_all()

        assert loaded["data_basic"].user_invocable is True

    def test_user_invocable_false_from_frontmatter(self, tmp_path: Path) -> None:
        system_dir = tmp_path / "system"
        user_dir = tmp_path / "user"
        project_dir = tmp_path / "project"
        for d in (system_dir, user_dir, project_dir):
            d.mkdir(parents=True, exist_ok=True)

        _write_skillpack(
            system_dir,
            "general_excel",
            description="测试",
            allowed_tools=["read_excel"],
            triggers=["excel"],
            user_invocable=False,
        )

        config = _make_config(system_dir, user_dir, project_dir)
        loader = SkillpackLoader(config, _tool_registry())
        loaded = loader.load_all()

        assert loaded["general_excel"].user_invocable is False

    def test_argument_hint_defaults_to_empty_string(self, tmp_path: Path) -> None:
        system_dir = tmp_path / "system"
        user_dir = tmp_path / "user"
        project_dir = tmp_path / "project"
        for d in (system_dir, user_dir, project_dir):
            d.mkdir(parents=True, exist_ok=True)

        _write_skillpack(
            system_dir,
            "data_basic",
            description="测试",
            allowed_tools=["read_excel"],
            triggers=["分析"],
        )

        config = _make_config(system_dir, user_dir, project_dir)
        loader = SkillpackLoader(config, _tool_registry())
        loaded = loader.load_all()
        assert loaded["data_basic"].argument_hint == ""

    def test_argument_hint_parsed_from_frontmatter(self, tmp_path: Path) -> None:
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
            argument_hint="<file> <chart_type> <x_col> <y_col>",
        )

        config = _make_config(system_dir, user_dir, project_dir)
        loader = SkillpackLoader(config, _tool_registry())
        loaded = loader.load_all()
        assert loaded["chart_basic"].argument_hint == "<file> <chart_type> <x_col> <y_col>"

    def test_argument_hint_non_string_raises_validation_error(self, tmp_path: Path) -> None:
        system_dir = tmp_path / "system"
        user_dir = tmp_path / "user"
        project_dir = tmp_path / "project"
        for d in (system_dir, user_dir, project_dir):
            d.mkdir(parents=True, exist_ok=True)

        skill_dir = system_dir / "data_basic"
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_file = skill_dir / "SKILL.md"
        skill_file.write_text(
            "\n".join(
                [
                    "---",
                    "name: data_basic",
                    "description: 测试",
                    "allowed_tools:",
                    "  - read_excel",
                    "triggers:",
                    "  - 分析",
                    "argument_hint: 123",
                    "---",
                    "测试说明",
                ]
            ),
            encoding="utf-8",
        )

        config = _make_config(system_dir, user_dir, project_dir)
        loader = SkillpackLoader(config, _tool_registry())
        with pytest.raises(SkillpackValidationError):
            loader._parse_skillpack_file(
                source="system",
                skill_dir=skill_dir,
                skill_file=skill_file,
            )

    def test_context_defaults_to_inline(self, tmp_path: Path) -> None:
        system_dir = tmp_path / "system"
        user_dir = tmp_path / "user"
        project_dir = tmp_path / "project"
        for d in (system_dir, user_dir, project_dir):
            d.mkdir(parents=True, exist_ok=True)

        _write_skillpack(
            system_dir,
            "data_basic",
            description="测试",
            allowed_tools=["read_excel"],
            triggers=["分析"],
        )

        config = _make_config(system_dir, user_dir, project_dir)
        loader = SkillpackLoader(config, _tool_registry())
        loaded = loader.load_all()
        assert loaded["data_basic"].context == "inline"

    def test_context_fork_parsed_from_frontmatter(self, tmp_path: Path) -> None:
        system_dir = tmp_path / "system"
        user_dir = tmp_path / "user"
        project_dir = tmp_path / "project"
        for d in (system_dir, user_dir, project_dir):
            d.mkdir(parents=True, exist_ok=True)

        _write_skillpack(
            system_dir,
            "excel_code_runner",
            description="大文件代码处理",
            allowed_tools=["read_excel"],
            triggers=["大文件"],
            context="fork",
        )

        config = _make_config(system_dir, user_dir, project_dir)
        loader = SkillpackLoader(config, _tool_registry())
        loaded = loader.load_all()
        assert loaded["excel_code_runner"].context == "fork"


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
    async def test_slash_direct_parameterized(self, tmp_path: Path) -> None:
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
            instructions=(
                "文件：$0\n"
                "类型：$ARGUMENTS[1]\n"
                "全部：$ARGUMENTS\n"
                "越界：$7"
            ),
        )

        config = _make_config(system_dir, user_dir, project_dir)
        loader = SkillpackLoader(config, _tool_registry())
        loader.load_all()
        router = SkillRouter(config, loader)

        result = await router.route(
            "忽略自然语言",
            slash_command="chart_basic",
            raw_args='"销售 数据.xlsx" bar',
        )

        assert result.route_mode == "slash_direct"
        assert result.parameterized is True
        assert result.skills_used == ["chart_basic"]
        assert result.tool_scope == ["create_chart"]
        assert result.system_contexts
        context = result.system_contexts[0]
        assert "文件：销售 数据.xlsx" in context
        assert "类型：bar" in context
        assert "全部：销售 数据.xlsx bar" in context
        assert "越界：" in context

    @pytest.mark.asyncio
    async def test_slash_command_not_found_returns_fallback(self, tmp_path: Path) -> None:
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

        config = _make_config(system_dir, user_dir, project_dir)
        loader = SkillpackLoader(config, _tool_registry())
        loader.load_all()
        router = SkillRouter(config, loader)

        result = await router.route(
            "任意输入",
            slash_command="not_exists_skill",
            raw_args="a b",
        )

        assert result.route_mode == "slash_not_found"
        assert result.parameterized is False
        assert "list_skills" in result.tool_scope

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

    @pytest.mark.asyncio
    async def test_hint_direct_ignores_invocation_flags(self, tmp_path: Path) -> None:
        system_dir = tmp_path / "system"
        user_dir = tmp_path / "user"
        project_dir = tmp_path / "project"
        for d in (system_dir, user_dir, project_dir):
            d.mkdir(parents=True, exist_ok=True)

        _write_skillpack(
            system_dir,
            "export_batch",
            description="批量导出",
            allowed_tools=["read_excel"],
            triggers=["导出"],
            disable_model_invocation=True,
            user_invocable=False,
        )

        config = _make_config(system_dir, user_dir, project_dir)
        loader = SkillpackLoader(config, _tool_registry())
        loader.load_all()
        router = SkillRouter(config, loader)

        result = await router.route(
            "执行导出",
            skill_hints=["export_batch"],
        )
        assert result.route_mode == "hint_direct"
        assert result.skills_used == ["export_batch"]

    @pytest.mark.asyncio
    async def test_hint_not_found_skips_scoring(self, tmp_path: Path) -> None:
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

        config = _make_config(system_dir, user_dir, project_dir)
        loader = SkillpackLoader(config, _tool_registry())
        loader.load_all()
        router = SkillRouter(config, loader)

        result = await router.route(
            "请帮我分析这个文件",
            skill_hints=["not_exists_skill"],
        )

        assert result.route_mode == "hint_not_found"
        assert result.skills_used == []
        assert "list_skills" in result.tool_scope

    @pytest.mark.asyncio
    async def test_auto_route_excludes_disable_model_invocation(
        self, tmp_path: Path
    ) -> None:
        system_dir = tmp_path / "system"
        user_dir = tmp_path / "user"
        project_dir = tmp_path / "project"
        for d in (system_dir, user_dir, project_dir):
            d.mkdir(parents=True, exist_ok=True)

        _write_skillpack(
            system_dir,
            "export_batch",
            description="批量导出",
            allowed_tools=["read_excel"],
            triggers=["导出"],
            disable_model_invocation=True,
        )
        _write_skillpack(
            system_dir,
            "general_excel",
            description="通用兜底",
            allowed_tools=["read_excel"],
            triggers=["excel"],
        )

        config = _make_config(system_dir, user_dir, project_dir)
        loader = SkillpackLoader(config, _tool_registry())
        loader.load_all()
        router = SkillRouter(config, loader)

        result = await router.route("请导出所有报表")
        assert "export_batch" not in result.skills_used

    @pytest.mark.asyncio
    async def test_auto_route_excludes_user_invocable_false(self, tmp_path: Path) -> None:
        system_dir = tmp_path / "system"
        user_dir = tmp_path / "user"
        project_dir = tmp_path / "project"
        for d in (system_dir, user_dir, project_dir):
            d.mkdir(parents=True, exist_ok=True)

        _write_skillpack(
            system_dir,
            "general_excel",
            description="通用兜底",
            allowed_tools=["read_excel"],
            triggers=["excel"],
            user_invocable=False,
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

        result = await router.route("请处理 excel 文件")
        assert "general_excel" not in result.skills_used

    @pytest.mark.asyncio
    async def test_blocked_skillpack_excluded_then_unlock(self, tmp_path: Path) -> None:
        system_dir = tmp_path / "system"
        user_dir = tmp_path / "user"
        project_dir = tmp_path / "project"
        for d in (system_dir, user_dir, project_dir):
            d.mkdir(parents=True, exist_ok=True)

        _write_skillpack(
            system_dir,
            "excel_code_runner",
            description="代码执行",
            allowed_tools=["read_excel"],
            triggers=["代码"],
        )
        _write_skillpack(
            system_dir,
            "general_excel",
            description="通用兜底",
            allowed_tools=["read_excel"],
            triggers=["excel"],
        )

        config = _make_config(system_dir, user_dir, project_dir)
        loader = SkillpackLoader(config, _tool_registry())
        loader.load_all()
        router = SkillRouter(config, loader)

        blocked_result = await router.route(
            "请写代码处理文件",
            skill_hints=["excel_code_runner"],
            blocked_skillpacks={"excel_code_runner"},
        )
        assert "excel_code_runner" not in blocked_result.skills_used

        unlocked_result = await router.route(
            "请写代码处理文件",
            skill_hints=["excel_code_runner"],
        )
        assert unlocked_result.skills_used == ["excel_code_runner"]

    @pytest.mark.asyncio
    async def test_large_excel_adds_fork_hint_context(self, tmp_path: Path) -> None:
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

        big_file = tmp_path / "big.xlsx"
        big_file.write_bytes(b"0" * 2048)

        config = _make_config(
            system_dir,
            user_dir,
            project_dir,
            workspace_root=str(tmp_path),
            large_excel_threshold_bytes=1024,
        )
        loader = SkillpackLoader(config, _tool_registry())
        loader.load_all()
        router = SkillRouter(config, loader)

        result = await router.route("请分析 big.xlsx")
        assert "large_excel" in result.route_mode
        assert any("[ForkContextHint]" in ctx for ctx in result.system_contexts)
        assert any("big.xlsx" in ctx for ctx in result.system_contexts)
        assert result.fork_plan is not None
        assert "read_excel" in result.fork_plan.tool_scope
        assert "big.xlsx" in ",".join(result.fork_plan.detected_files)

    @pytest.mark.asyncio
    async def test_fork_context_skill_adds_hint(self, tmp_path: Path) -> None:
        system_dir = tmp_path / "system"
        user_dir = tmp_path / "user"
        project_dir = tmp_path / "project"
        for d in (system_dir, user_dir, project_dir):
            d.mkdir(parents=True, exist_ok=True)

        _write_skillpack(
            system_dir,
            "excel_code_runner",
            description="代码执行",
            allowed_tools=["read_excel"],
            triggers=["代码"],
            context="fork",
        )

        config = _make_config(system_dir, user_dir, project_dir)
        loader = SkillpackLoader(config, _tool_registry())
        loader.load_all()
        router = SkillRouter(config, loader)

        result = await router.route("请写代码处理这个文件")
        assert "fork" in result.route_mode
        assert any("[ForkContextSkill]" in ctx for ctx in result.system_contexts)
        assert result.fork_plan is not None
        assert "excel_code_runner" in result.fork_plan.source_skills
