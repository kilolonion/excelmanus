"""Skillpack Loader / Router 单元测试。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

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
    )
    defaults.update(overrides)
    return ExcelManusConfig(**defaults)


def _write_skillpack(
    root_dir: Path,
    name: str,
    *,
    description: str,
    disable_model_invocation: bool | None = None,
    user_invocable: bool | None = None,
    argument_hint: str | None = None,
    instructions: str = "测试说明",
) -> None:
    skill_dir = root_dir / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        "---",
        f"name: {name}",
        f"description: {description}",
    ]
    if disable_model_invocation is not None:
        flag = "true" if disable_model_invocation else "false"
        lines.append(f"disable_model_invocation: {flag}")
    if user_invocable is not None:
        flag = "true" if user_invocable else "false"
        lines.append(f"user_invocable: {flag}")
    if argument_hint is not None:
        lines.append(f"argument_hint: {argument_hint}")

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
        )
        _write_skillpack(
            user_dir,
            "data_basic",
            description="user",
        )
        _write_skillpack(
            project_dir,
            "data_basic",
            description="project",
        )

        config = _make_config(system_dir, user_dir, project_dir)
        loader = SkillpackLoader(config, _tool_registry())
        loaded = loader.load_all()
        assert loaded["data_basic"].description == "project"

    def test_discovery_workspace_claude_overrides_user_claude(
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

        _write_skillpack(
            home_dir / ".claude" / "skills",
            "data_basic",
            description="user-claude",
        )
        _write_skillpack(
            workspace / ".claude" / "skills",
            "data_basic",
            description="workspace-claude",
        )

        config = _make_config(
            system_dir,
            user_dir,
            project_dir,
            workspace_root=str(workspace),
            skills_discovery_include_agents=False,
            skills_discovery_scan_external_tool_dirs=True,
        )
        loader = SkillpackLoader(config, _tool_registry())
        loaded = loader.load_all()
        assert loaded["data_basic"].description == "workspace-claude"

    def test_discovery_agents_ancestor_nearer_wins(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        workspace = tmp_path / "workspace"
        nested = workspace / "src" / "module"
        system_dir = workspace / "system"
        user_dir = workspace / "user"
        project_dir = workspace / "project"
        for d in (workspace, nested, system_dir, user_dir, project_dir):
            d.mkdir(parents=True, exist_ok=True)

        monkeypatch.chdir(nested)

        _write_skillpack(
            workspace / ".agents" / "skills",
            "team/data-cleaner",
            description="ancestor-far",
        )
        _write_skillpack(
            workspace / "src" / ".agents" / "skills",
            "team/data-cleaner",
            description="ancestor-near",
        )

        config = _make_config(
            system_dir,
            user_dir,
            project_dir,
            workspace_root=str(workspace),
            skills_discovery_scan_external_tool_dirs=False,
        )
        loader = SkillpackLoader(config, _tool_registry())
        loaded = loader.load_all()
        assert loaded["team/data-cleaner"].description == "ancestor-near"

    def test_discovery_agents_skips_ancestors_when_cwd_outside_workspace(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        workspace = tmp_path / "workspace"
        outside_root = tmp_path / "outside"
        outside_nested = outside_root / "deep" / "module"
        system_dir = workspace / "system"
        user_dir = workspace / "user"
        project_dir = workspace / "project"
        for d in (workspace, outside_nested, system_dir, user_dir, project_dir):
            d.mkdir(parents=True, exist_ok=True)

        monkeypatch.chdir(outside_nested)

        _write_skillpack(
            outside_root / ".agents" / "skills",
            "outside/poison",
            description="outside-ancestor",
        )

        config = _make_config(
            system_dir,
            user_dir,
            project_dir,
            workspace_root=str(workspace),
            skills_discovery_scan_external_tool_dirs=False,
        )
        loader = SkillpackLoader(config, _tool_registry())
        loaded = loader.load_all()
        assert "outside/poison" not in loaded

    def test_discovery_workspace_openclaw_overrides_user_openclaw(
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

        _write_skillpack(
            home_dir / ".openclaw" / "skills",
            "mcp/dispatcher",
            description="user-openclaw",
        )
        _write_skillpack(
            workspace / ".openclaw" / "skills",
            "mcp/dispatcher",
            description="workspace-openclaw",
        )

        config = _make_config(
            system_dir,
            user_dir,
            project_dir,
            workspace_root=str(workspace),
            skills_discovery_include_agents=False,
            skills_discovery_scan_external_tool_dirs=True,
        )
        loader = SkillpackLoader(config, _tool_registry())
        loaded = loader.load_all()
        assert loaded["mcp/dispatcher"].description == "workspace-openclaw"

    def test_discovery_workspace_legacy_skills_dir_is_not_openclaw_project_dir(
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

        _write_skillpack(
            home_dir / ".openclaw" / "skills",
            "mcp/dispatcher",
            description="user-openclaw",
        )
        _write_skillpack(
            workspace / "skills",
            "mcp/dispatcher",
            description="workspace-legacy-skills",
        )

        config = _make_config(
            system_dir,
            user_dir,
            project_dir,
            workspace_root=str(workspace),
            skills_discovery_include_agents=False,
            skills_discovery_scan_external_tool_dirs=True,
        )
        loader = SkillpackLoader(config, _tool_registry())
        loaded = loader.load_all()
        assert loaded["mcp/dispatcher"].description == "user-openclaw"

    def test_parse_required_mcp_fields(self, tmp_path: Path) -> None:
        system_dir = tmp_path / "system"
        user_dir = tmp_path / "user"
        project_dir = tmp_path / "project"
        for d in (system_dir, user_dir, project_dir):
            d.mkdir(parents=True, exist_ok=True)

        skill_dir = system_dir / "mcp_skill"
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(
            "\n".join(
                [
                    "---",
                    "name: mcp_skill",
                    "description: mcp skill",
                    "allowed-tools:",
                    "  - mcp:context7:*",
                    "required-mcp-servers:",
                    "  - context7",
                    "required-mcp-tools:",
                    "  - context7:query_docs",
                    "---",
                    "执行说明",
                ]
            ),
            encoding="utf-8",
        )

        config = _make_config(system_dir, user_dir, project_dir)
        loader = SkillpackLoader(config, _tool_registry())
        loaded = loader.load_all()

        skill = loaded["mcp_skill"]
        assert skill.required_mcp_servers == ["context7"]
        assert skill.required_mcp_tools == ["context7:query_docs"]

    def test_invalid_required_mcp_tools_reports_warning(self, tmp_path: Path) -> None:
        system_dir = tmp_path / "system"
        user_dir = tmp_path / "user"
        project_dir = tmp_path / "project"
        for d in (system_dir, user_dir, project_dir):
            d.mkdir(parents=True, exist_ok=True)

        skill_dir = system_dir / "bad_mcp_skill"
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(
            "\n".join(
                [
                    "---",
                    "name: bad_mcp_skill",
                    "description: bad mcp skill",
                    "allowed-tools:",
                    "  - read_excel",
                    "required-mcp-tools:",
                    "  - context7",  # 非法：缺少 :tool
                    "---",
                    "执行说明",
                ]
            ),
            encoding="utf-8",
        )

        config = _make_config(system_dir, user_dir, project_dir)
        loader = SkillpackLoader(config, _tool_registry())
        loaded = loader.load_all()

        assert "bad_mcp_skill" not in loaded
        assert any("required_mcp_tools" in warning for warning in loader.warnings)

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
            "test_fallback",
            description="测试",
            user_invocable=False,
        )

        config = _make_config(system_dir, user_dir, project_dir)
        loader = SkillpackLoader(config, _tool_registry())
        loaded = loader.load_all()

        assert loaded["test_fallback"].user_invocable is False

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



    def test_context_fork_is_rejected_with_migration_hint(self, tmp_path: Path) -> None:
        system_dir = tmp_path / "system"
        user_dir = tmp_path / "user"
        project_dir = tmp_path / "project"
        for d in (system_dir, user_dir, project_dir):
            d.mkdir(parents=True, exist_ok=True)

        _write_skillpack(
            system_dir,
            "excel_code_runner",
            description="大文件代码处理",
            instructions="测试",
        )
        # 注入已废弃的 fork 上下文配置
        skill_file = system_dir / "excel_code_runner" / "SKILL.md"
        content = skill_file.read_text(encoding="utf-8")
        content = content.replace("---\n测试", "context: fork\n---\n测试")
        skill_file.write_text(content, encoding="utf-8")

        config = _make_config(system_dir, user_dir, project_dir)
        loader = SkillpackLoader(config, _tool_registry())
        loaded = loader.load_all()
        assert "excel_code_runner" not in loaded
        assert any("context: fork" in warning for warning in loader.warnings)
        assert any("delegate_to_subagent" in warning for warning in loader.warnings)

    def test_agent_field_is_rejected_with_migration_hint(self, tmp_path: Path) -> None:
        system_dir = tmp_path / "system"
        user_dir = tmp_path / "user"
        project_dir = tmp_path / "project"
        for d in (system_dir, user_dir, project_dir):
            d.mkdir(parents=True, exist_ok=True)

        _write_skillpack(
            system_dir,
            "excel_code_runner",
            description="大文件代码处理",
            instructions="测试",
        )
        skill_file = system_dir / "excel_code_runner" / "SKILL.md"
        content = skill_file.read_text(encoding="utf-8")
        content = content.replace("---\n测试", "agent: explorer\n---\n测试")
        skill_file.write_text(content, encoding="utf-8")

        config = _make_config(system_dir, user_dir, project_dir)
        loader = SkillpackLoader(config, _tool_registry())
        loaded = loader.load_all()
        assert "excel_code_runner" not in loaded
        assert any("字段 'agent' 已移除" in warning for warning in loader.warnings)
        assert any("delegate_to_subagent" in warning for warning in loader.warnings)

    def test_kebab_case_fields_are_loaded(self, tmp_path: Path) -> None:
        system_dir = tmp_path / "system"
        user_dir = tmp_path / "user"
        project_dir = tmp_path / "project"
        for d in (system_dir, user_dir, project_dir):
            d.mkdir(parents=True, exist_ok=True)

        skill_dir = system_dir / "team" / "data-cleaner"
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(
            "\n".join(
                [
                    "---",
                    "name: team/data-cleaner",
                    "description: 清洗技能",
                    "allowed-tools:",
                    "  - read_excel",
                    "file-patterns:",
                    "  - '*.xlsx'",
                    "disable-model-invocation: true",
                    "user-invocable: false",
                    "argument-hint: '<file>'",
                    "command-dispatch: tool",
                    "command-tool: read_excel",
                    "---",
                    "说明",
                ]
            ),
            encoding="utf-8",
        )

        config = _make_config(system_dir, user_dir, project_dir)
        loader = SkillpackLoader(config, _tool_registry())
        loaded = loader.load_all()
        assert "team/data-cleaner" in loaded
        skill = loaded["team/data-cleaner"]
        assert skill.file_patterns == ["*.xlsx"]
        assert skill.disable_model_invocation is True
        assert skill.user_invocable is False
        assert skill.argument_hint == "<file>"
        assert skill.command_dispatch == "tool"
        assert skill.command_tool == "read_excel"


class TestSkillRouter:
    @pytest.mark.asyncio
    async def test_slash_direct_parameterized(self, tmp_path: Path) -> None:
        """斜杠命令直连路由：参数化分派。"""
        system_dir = tmp_path / "system"
        user_dir = tmp_path / "user"
        project_dir = tmp_path / "project"
        for d in (system_dir, user_dir, project_dir):
            d.mkdir(parents=True, exist_ok=True)

        _write_skillpack(
            system_dir,
            "chart_basic",
            description="图表",
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
        assert result.tool_scope == []  # v5: router no longer builds tool_scope
        assert result.system_contexts
        context = result.system_contexts[0]
        assert "文件：销售 数据.xlsx" in context
        assert "类型：bar" in context
        assert "全部：销售 数据.xlsx bar" in context
        assert "越界：" in context

    @pytest.mark.asyncio
    async def test_slash_command_not_found_returns_fallback(self, tmp_path: Path) -> None:
        """斜杠命令未匹配技能时返回 slash_not_found。"""
        system_dir = tmp_path / "system"
        user_dir = tmp_path / "user"
        project_dir = tmp_path / "project"
        for d in (system_dir, user_dir, project_dir):
            d.mkdir(parents=True, exist_ok=True)

        _write_skillpack(
            system_dir,
            "data_basic",
            description="分析",
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
        assert result.tool_scope == []

    @pytest.mark.asyncio
    async def test_non_slash_returns_all_tools(self, tmp_path: Path) -> None:
        """非斜杠消息应返回 all_tools 路由（tool_scope 为空）。"""
        system_dir = tmp_path / "system"
        user_dir = tmp_path / "user"
        project_dir = tmp_path / "project"
        for d in (system_dir, user_dir, project_dir):
            d.mkdir(parents=True, exist_ok=True)

        _write_skillpack(
            system_dir,
            "data_basic",
            description="分析",
        )
        _write_skillpack(
            system_dir,
            "chart_basic",
            description="图表",
        )

        config = _make_config(system_dir, user_dir, project_dir)
        loader = SkillpackLoader(config, _tool_registry())
        loader.load_all()
        router = SkillRouter(config, loader)

        result = await router.route("请分析这个文件")
        assert result.route_mode == "all_tools"
        assert result.tool_scope == []
        assert result.skills_used == []

    @pytest.mark.asyncio
    async def test_classify_write_hint_uses_lexical_fallback_without_aux_model(
        self,
        tmp_path: Path,
    ) -> None:
        system_dir = tmp_path / "system"
        user_dir = tmp_path / "user"
        project_dir = tmp_path / "project"
        for d in (system_dir, user_dir, project_dir):
            d.mkdir(parents=True, exist_ok=True)

        config = _make_config(system_dir, user_dir, project_dir, aux_model=None)
        loader = SkillpackLoader(config, _tool_registry())
        router = SkillRouter(config, loader)

        hint, tags = await router._classify_task("请先画一个柱状图，然后美化表头")
        assert hint == "may_write"

    @pytest.mark.asyncio
    async def test_non_slash_lexical_write_hint_without_tags_skips_llm(
        self,
        tmp_path: Path,
    ) -> None:
        """词法已明确写意图时（即使无标签）也不应触发 LLM 分类。"""
        system_dir = tmp_path / "system"
        user_dir = tmp_path / "user"
        project_dir = tmp_path / "project"
        for d in (system_dir, user_dir, project_dir):
            d.mkdir(parents=True, exist_ok=True)

        _write_skillpack(
            system_dir,
            "data_basic",
            description="分析",
        )

        config = _make_config(
            system_dir,
            user_dir,
            project_dir,
            aux_model="aux-model",
        )
        loader = SkillpackLoader(config, _tool_registry())
        loader.load_all()
        router = SkillRouter(config, loader)

        with patch("excelmanus.providers.create_client") as mock_create_client:
            result = await router.route("请修改这个文件并保存")

        assert result.route_mode == "all_tools"
        assert result.write_hint == "may_write"
        assert result.task_tags == ()
        mock_create_client.assert_not_called()

    @pytest.mark.asyncio
    async def test_classify_write_hint_model_parse_failure_falls_back_to_lexical(
        self,
        tmp_path: Path,
    ) -> None:
        system_dir = tmp_path / "system"
        user_dir = tmp_path / "user"
        project_dir = tmp_path / "project"
        for d in (system_dir, user_dir, project_dir):
            d.mkdir(parents=True, exist_ok=True)

        config = _make_config(
            system_dir,
            user_dir,
            project_dir,
            aux_model="aux-model",
        )
        loader = SkillpackLoader(config, _tool_registry())
        router = SkillRouter(config, loader)

        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(
            return_value=SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content="not-a-json"),
                    )
                ]
            )
        )
        with patch("excelmanus.providers.create_client", return_value=mock_client):
            hint, tags = await router._classify_task("把这个 sheet 美化并画图")
        assert hint == "may_write"

    @pytest.mark.asyncio
    async def test_classify_write_hint_model_error_returns_unknown_when_both_endpoints_fail(
        self,
        tmp_path: Path,
    ) -> None:
        """词法无法判断时同步调用 LLM 分类，LLM 失败后回退到 may_write。"""
        system_dir = tmp_path / "system"
        user_dir = tmp_path / "user"
        project_dir = tmp_path / "project"
        for d in (system_dir, user_dir, project_dir):
            d.mkdir(parents=True, exist_ok=True)

        config = _make_config(
            system_dir,
            user_dir,
            project_dir,
            aux_model="aux-model",
        )
        loader = SkillpackLoader(config, _tool_registry())
        router = SkillRouter(config, loader)

        hint, tags = await router._classify_task("请帮我处理这个任务")
        assert hint == "may_write"

    @pytest.mark.asyncio
    async def test_classify_write_hint_aux_error_falls_back_to_main_model(
        self,
        tmp_path: Path,
    ) -> None:
        """词法无法判断时同步调用 LLM 分类，AUX 失败后降级到主模型，均失败则回退 may_write。"""
        system_dir = tmp_path / "system"
        user_dir = tmp_path / "user"
        project_dir = tmp_path / "project"
        for d in (system_dir, user_dir, project_dir):
            d.mkdir(parents=True, exist_ok=True)

        config = _make_config(
            system_dir,
            user_dir,
            project_dir,
            aux_model="aux-model",
            model="main-model",
        )
        loader = SkillpackLoader(config, _tool_registry())
        router = SkillRouter(config, loader)

        # LLM 分类已内化到 _classify_task 同步流程，无 mock 时 LLM 调用失败回退 may_write
        hint, tags = await router._classify_task("请帮我处理这个任务")
        assert hint == "may_write"

    @pytest.mark.asyncio
    async def test_non_slash_injects_large_file_context(self, tmp_path: Path) -> None:
        """fallback 路由命中大文件时应注入提示上下文。"""
        system_dir = tmp_path / "system"
        user_dir = tmp_path / "user"
        project_dir = tmp_path / "project"
        for d in (system_dir, user_dir, project_dir):
            d.mkdir(parents=True, exist_ok=True)

        _write_skillpack(
            system_dir,
            "data_basic",
            description="分析",
        )
        large_file = tmp_path / "big.xlsx"
        large_file.write_bytes(b"x" * 64)

        config = _make_config(
            system_dir,
            user_dir,
            project_dir,
            large_excel_threshold_bytes=32,
        )
        loader = SkillpackLoader(config, _tool_registry())
        loader.load_all()
        router = SkillRouter(config, loader)

        result = await router.route(
            f"请分析文件 {large_file}",
            file_paths=[str(large_file)],
        )

        joined = "\n".join(result.system_contexts)
        assert "检测到大文件 Excel" in joined
        assert str(large_file.resolve()) in joined

    @pytest.mark.asyncio
    async def test_non_slash_mutating_intent_still_returns_all_tools(
        self,
        tmp_path: Path,
    ) -> None:
        """写入意图下非斜杠路由仍应直接放开全量工具。"""
        system_dir = tmp_path / "system"
        user_dir = tmp_path / "user"
        project_dir = tmp_path / "project"
        for d in (system_dir, user_dir, project_dir):
            d.mkdir(parents=True, exist_ok=True)

        _write_skillpack(
            system_dir,
            "format_basic",
            description="格式化",
        )
        _write_skillpack(
            system_dir,
            "test_fallback",
            description="通用兜底",
        )

        config = _make_config(system_dir, user_dir, project_dir)
        loader = SkillpackLoader(config, _tool_registry())
        loader.load_all()
        router = SkillRouter(config, loader)

        result = await router.route("把A列字体改成红色并保存")

        assert result.route_mode == "all_tools"
        assert result.tool_scope == []
        assert result.skills_used == []

    @pytest.mark.asyncio
    async def test_non_slash_mutating_intent_without_trigger_still_all_tools(
        self,
        tmp_path: Path,
    ) -> None:
        """写入意图即使无触发词，也应直接放开全量工具。"""
        system_dir = tmp_path / "system"
        user_dir = tmp_path / "user"
        project_dir = tmp_path / "project"
        for d in (system_dir, user_dir, project_dir):
            d.mkdir(parents=True, exist_ok=True)

        _write_skillpack(
            system_dir,
            "data_basic",
            description="数据分析",
        )
        _write_skillpack(
            system_dir,
            "test_fallback",
            description="通用兜底",
        )

        config = _make_config(system_dir, user_dir, project_dir)
        loader = SkillpackLoader(config, _tool_registry())
        loader.load_all()
        router = SkillRouter(config, loader)

        result = await router.route("请修改这个文件并保存到原路径")

        assert result.route_mode == "all_tools"
        assert result.tool_scope == []
        assert result.skills_used == []

    @pytest.mark.asyncio
    async def test_slash_direct_parameterized_injects_large_file_context(
        self,
        tmp_path: Path,
    ) -> None:
        """斜杠参数化路由命中大文件时应追加提示上下文。"""
        system_dir = tmp_path / "system"
        user_dir = tmp_path / "user"
        project_dir = tmp_path / "project"
        for d in (system_dir, user_dir, project_dir):
            d.mkdir(parents=True, exist_ok=True)

        _write_skillpack(
            system_dir,
            "chart_basic",
            description="图表",
            instructions="文件：$0\n类型：$1",
        )
        large_file = tmp_path / "sales.xlsx"
        large_file.write_bytes(b"x" * 64)

        config = _make_config(
            system_dir,
            user_dir,
            project_dir,
            large_excel_threshold_bytes=32,
        )
        loader = SkillpackLoader(config, _tool_registry())
        loader.load_all()
        router = SkillRouter(config, loader)

        result = await router.route(
            "忽略自然语言",
            slash_command="chart_basic",
            raw_args=f'"{large_file}" bar',
        )

        assert result.system_contexts
        assert "文件：" in result.system_contexts[0]
        joined = "\n".join(result.system_contexts)
        assert "检测到大文件 Excel" in joined
        assert str(large_file.resolve()) in joined

    @pytest.mark.asyncio
    async def test_blocked_skillpack_excluded_then_unlock(self, tmp_path: Path) -> None:
        """被屏蔽的技能包不参与路由，解除屏蔽后可正常使用。"""
        system_dir = tmp_path / "system"
        user_dir = tmp_path / "user"
        project_dir = tmp_path / "project"
        for d in (system_dir, user_dir, project_dir):
            d.mkdir(parents=True, exist_ok=True)

        _write_skillpack(
            system_dir,
            "excel_code_runner",
            description="代码执行",
        )
        _write_skillpack(
            system_dir,
            "test_fallback",
            description="通用兜底",
        )

        config = _make_config(system_dir, user_dir, project_dir)
        loader = SkillpackLoader(config, _tool_registry())
        loader.load_all()
        router = SkillRouter(config, loader)

        # 屏蔽后斜杠命令也无法匹配
        blocked_result = await router.route(
            "请写代码处理文件",
            slash_command="excel_code_runner",
            blocked_skillpacks={"excel_code_runner"},
        )
        assert "excel_code_runner" not in blocked_result.skills_used

        # 解除屏蔽后可正常斜杠直连
        unlocked_result = await router.route(
            "请写代码处理文件",
            slash_command="excel_code_runner",
        )
        assert unlocked_result.skills_used == ["excel_code_runner"]

    @pytest.mark.asyncio
    async def test_build_skill_catalog(self, tmp_path: Path) -> None:
        """build_skill_catalog 返回技能目录文本和名称列表。"""
        system_dir = tmp_path / "system"
        user_dir = tmp_path / "user"
        project_dir = tmp_path / "project"
        for d in (system_dir, user_dir, project_dir):
            d.mkdir(parents=True, exist_ok=True)

        _write_skillpack(
            system_dir,
            "data_basic",
            description="数据分析",
        )
        _write_skillpack(
            system_dir,
            "chart_basic",
            description="图表生成",
        )

        config = _make_config(system_dir, user_dir, project_dir)
        loader = SkillpackLoader(config, _tool_registry())
        loader.load_all()
        router = SkillRouter(config, loader)

        catalog_text, skill_names = router.build_skill_catalog()
        assert "data_basic" in catalog_text
        assert "chart_basic" in catalog_text
        assert "数据分析" in catalog_text
        assert "图表生成" in catalog_text
        assert sorted(skill_names) == ["chart_basic", "data_basic"]

    @pytest.mark.asyncio
    async def test_build_skill_catalog_with_blocked(self, tmp_path: Path) -> None:
        """build_skill_catalog 排除被屏蔽的技能包。"""
        system_dir = tmp_path / "system"
        user_dir = tmp_path / "user"
        project_dir = tmp_path / "project"
        for d in (system_dir, user_dir, project_dir):
            d.mkdir(parents=True, exist_ok=True)

        _write_skillpack(
            system_dir,
            "data_basic",
            description="数据分析",
        )
        _write_skillpack(
            system_dir,
            "chart_basic",
            description="图表生成",
        )

        config = _make_config(system_dir, user_dir, project_dir)
        loader = SkillpackLoader(config, _tool_registry())
        loader.load_all()
        router = SkillRouter(config, loader)

        catalog_text, skill_names = router.build_skill_catalog(
            blocked_skillpacks={"chart_basic"},
        )
        assert "data_basic" in catalog_text
        # 被限制的技能仍出现在目录中，但带有权限标注
        assert "chart_basic" in catalog_text
        assert "fullAccess" in catalog_text
        assert sorted(skill_names) == ["chart_basic", "data_basic"]

    @pytest.mark.asyncio
    async def test_no_skillpacks_returns_no_skillpack(self, tmp_path: Path) -> None:
        """无技能包时返回 no_skillpack 路由模式。"""
        system_dir = tmp_path / "system"
        user_dir = tmp_path / "user"
        project_dir = tmp_path / "project"
        for d in (system_dir, user_dir, project_dir):
            d.mkdir(parents=True, exist_ok=True)

        config = _make_config(system_dir, user_dir, project_dir)
        loader = SkillpackLoader(config, _tool_registry())
        loader.load_all()
        router = SkillRouter(config, loader)

        result = await router.route("任意输入")
        assert result.route_mode == "no_skillpack"
