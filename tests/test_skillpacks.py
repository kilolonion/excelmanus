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
            allowed_tools=["read_excel"],
            triggers=["分析"],
        )
        _write_skillpack(
            workspace / ".claude" / "skills",
            "data_basic",
            description="workspace-claude",
            allowed_tools=["read_excel"],
            triggers=["分析"],
        )

        config = _make_config(
            system_dir,
            user_dir,
            project_dir,
            workspace_root=str(workspace),
            skills_discovery_include_agents=False,
            skills_discovery_include_openclaw=False,
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
            allowed_tools=["read_excel"],
            triggers=["清洗"],
        )
        _write_skillpack(
            workspace / "src" / ".agents" / "skills",
            "team/data-cleaner",
            description="ancestor-near",
            allowed_tools=["read_excel"],
            triggers=["清洗"],
        )

        config = _make_config(
            system_dir,
            user_dir,
            project_dir,
            workspace_root=str(workspace),
            skills_discovery_include_claude=False,
            skills_discovery_include_openclaw=False,
        )
        loader = SkillpackLoader(config, _tool_registry())
        loaded = loader.load_all()
        assert loaded["team/data-cleaner"].description == "ancestor-near"

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
            allowed_tools=["read_excel"],
            triggers=["调度"],
        )
        _write_skillpack(
            workspace / ".openclaw" / "skills",
            "mcp/dispatcher",
            description="workspace-openclaw",
            allowed_tools=["read_excel"],
            triggers=["调度"],
        )

        config = _make_config(
            system_dir,
            user_dir,
            project_dir,
            workspace_root=str(workspace),
            skills_discovery_include_agents=False,
            skills_discovery_include_claude=False,
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
            allowed_tools=["read_excel"],
            triggers=["调度"],
        )
        _write_skillpack(
            workspace / "skills",
            "mcp/dispatcher",
            description="workspace-legacy-skills",
            allowed_tools=["read_excel"],
            triggers=["调度"],
        )

        config = _make_config(
            system_dir,
            user_dir,
            project_dir,
            workspace_root=str(workspace),
            skills_discovery_include_agents=False,
            skills_discovery_include_claude=False,
        )
        loader = SkillpackLoader(config, _tool_registry())
        loaded = loader.load_all()
        assert loaded["mcp/dispatcher"].description == "user-openclaw"

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

    def test_soft_validate_accepts_mcp_selectors(self, tmp_path: Path) -> None:
        system_dir = tmp_path / "system"
        user_dir = tmp_path / "user"
        project_dir = tmp_path / "project"
        for d in (system_dir, user_dir, project_dir):
            d.mkdir(parents=True, exist_ok=True)

        _write_skillpack(
            system_dir,
            "general_excel",
            description="test",
            allowed_tools=[
                "read_excel",
                "mcp:*",
                "mcp:context7:*",
                "mcp:context7:query_docs",
            ],
            triggers=["excel"],
        )

        config = _make_config(system_dir, user_dir, project_dir)
        loader = SkillpackLoader(config, _tool_registry())
        loader.load_all()
        assert not any("mcp:" in warning for warning in loader.warnings)

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

    def test_triggers_allows_empty_list(self, tmp_path: Path) -> None:
        """triggers 允许为空列表（兜底技能场景）。"""
        system_dir = tmp_path / "system"
        user_dir = tmp_path / "user"
        project_dir = tmp_path / "project"
        for d in (system_dir, user_dir, project_dir):
            d.mkdir(parents=True, exist_ok=True)

        skill_dir = system_dir / "general_excel"
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_file = skill_dir / "SKILL.md"
        skill_file.write_text(
            "\n".join(
                [
                    "---",
                    "name: general_excel",
                    "description: 兜底技能",
                    "allowed_tools:",
                    "  - read_excel",
                    "triggers: []",
                    "user_invocable: false",
                    "---",
                    "测试说明",
                ]
            ),
            encoding="utf-8",
        )

        config = _make_config(system_dir, user_dir, project_dir)
        loader = SkillpackLoader(config, _tool_registry())
        loaded = loader.load_all()
        assert "general_excel" in loaded
        assert loaded["general_excel"].triggers == []

    def test_allowed_tools_empty_list_is_allowed(self, tmp_path: Path) -> None:
        """allowed_tools 允许为空列表（仅注入上下文，不自动追加工具权限）。"""
        system_dir = tmp_path / "system"
        user_dir = tmp_path / "user"
        project_dir = tmp_path / "project"
        for d in (system_dir, user_dir, project_dir):
            d.mkdir(parents=True, exist_ok=True)

        skill_dir = system_dir / "broken_skill"
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_file = skill_dir / "SKILL.md"
        skill_file.write_text(
            "\n".join(
                [
                    "---",
                    "name: broken_skill",
                    "description: 非法技能",
                    "allowed_tools: []",
                    "triggers: []",
                    "---",
                    "测试说明",
                ]
            ),
            encoding="utf-8",
        )

        config = _make_config(system_dir, user_dir, project_dir)
        loader = SkillpackLoader(config, _tool_registry())
        parsed = loader._parse_skillpack_file(
            source="system",
            skill_dir=skill_dir,
            skill_file=skill_file,
        )
        assert parsed.allowed_tools == []

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
            allowed_tools=["read_excel"],
            triggers=["大文件"],
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
            allowed_tools=["read_excel"],
            triggers=["大文件"],
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
    async def test_non_slash_returns_fallback(self, tmp_path: Path) -> None:
        """非斜杠消息返回 fallback 结果（全量工具 + 技能目录）。"""
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

        config = _make_config(system_dir, user_dir, project_dir)
        loader = SkillpackLoader(config, _tool_registry())
        loader.load_all()
        router = SkillRouter(config, loader)

        result = await router.route("请分析这个文件")
        assert result.route_mode == "fallback"
        assert "list_skills" in result.tool_scope
        # 只读数据工具应在 fallback 模式下直接可用，无需 select_skill
        assert "filter_data" in result.tool_scope
        assert "analyze_data" in result.tool_scope
        # 技能目录通过 select_skill 元工具 description 传递，不再注入 system_contexts
        assert not any("可用技能" in ctx for ctx in result.system_contexts)

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
            allowed_tools=["read_excel"],
            triggers=["分析"],
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
            allowed_tools=["create_chart"],
            triggers=["图表"],
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
            allowed_tools=["read_excel"],
            triggers=["分析"],
        )
        _write_skillpack(
            system_dir,
            "chart_basic",
            description="图表生成",
            allowed_tools=["create_chart"],
            triggers=["图表"],
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
            allowed_tools=["read_excel"],
            triggers=["分析"],
        )
        _write_skillpack(
            system_dir,
            "chart_basic",
            description="图表生成",
            allowed_tools=["create_chart"],
            triggers=["图表"],
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
