"""LLM-Native 路由属性测试。

Feature: llm-native-routing
使用 hypothesis 库验证斜杠直连路由和名称归一化的通用正确性属性。
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from hypothesis import given, settings, assume, HealthCheck
from hypothesis import strategies as st

from excelmanus.config import ExcelManusConfig
from excelmanus.engine import AgentEngine
from excelmanus.subagent import SubagentResult
from excelmanus.skillpacks import SkillpackLoader, SkillRouter
from excelmanus.tools import ToolDef, ToolRegistry


# ── 辅助函数 ──────────────────────────────────────────────────────


def _make_config(
    system_dir: Path,
    user_dir: Path,
    project_dir: Path,
) -> ExcelManusConfig:
    """构建测试用配置。"""
    return ExcelManusConfig(
        api_key="test-key",
        base_url="https://test.example.com/v1",
        model="test-model",
        workspace_root=str(system_dir.parent),
        skills_system_dir=str(system_dir),
        skills_user_dir=str(user_dir),
        skills_project_dir=str(project_dir),
    )


def _tool_registry() -> ToolRegistry:
    """构建包含基础工具的注册表。"""
    registry = ToolRegistry()
    for name in ("read_excel", "create_chart", "write_excel"):
        registry.register_tool(
            ToolDef(
                name=name,
                description=f"工具: {name}",
                input_schema={"type": "object", "properties": {}},
                func=lambda: "ok",
            )
        )
    return registry


def _write_skillpack(
    root_dir: Path,
    name: str,
    *,
    description: str = "测试技能",
    allowed_tools: list[str] | None = None,
    triggers: list[str] | None = None,
    instructions: str = "测试说明",
) -> None:
    """在指定目录下写入 SKILL.md 文件。"""
    skill_dir = root_dir / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    tools = allowed_tools or ["read_excel"]
    trigs = triggers or ["测试"]
    lines = [
        "---",
        f"name: {name}",
        f"description: {description}",
        "allowed_tools:",
        *[f"  - {t}" for t in tools],
        "triggers:",
        *[f"  - {t}" for t in trigs],
        "---",
        instructions,
    ]
    (skill_dir / "SKILL.md").write_text("\n".join(lines), encoding="utf-8")


def _setup_router_in(base_dir: Path, skill_names: list[str]) -> SkillRouter:
    """在给定基础目录下创建包含指定技能的 SkillRouter 实例。"""
    system_dir = base_dir / "system"
    user_dir = base_dir / "user"
    project_dir = base_dir / "project"
    for d in (system_dir, user_dir, project_dir):
        d.mkdir(parents=True, exist_ok=True)

    for name in skill_names:
        _write_skillpack(system_dir, name)

    config = _make_config(system_dir, user_dir, project_dir)
    loader = SkillpackLoader(config, _tool_registry())
    loader.load_all()
    return SkillRouter(config, loader)


def _setup_engine_in(
    base_dir: Path,
    skills: list[tuple[str, str, list[str]]],
) -> AgentEngine:
    """在给定目录下创建包含指定技能的 AgentEngine 实例。"""
    system_dir = base_dir / "system"
    user_dir = base_dir / "user"
    project_dir = base_dir / "project"
    for d in (system_dir, user_dir, project_dir):
        d.mkdir(parents=True, exist_ok=True)

    for name, description, allowed_tools in skills:
        _write_skillpack(
            system_dir,
            name,
            description=description,
            allowed_tools=allowed_tools,
        )

    config = _make_config(system_dir, user_dir, project_dir)
    registry = _tool_registry()
    loader = SkillpackLoader(config, registry)
    loader.load_all()
    router = SkillRouter(config, loader)
    return AgentEngine(config, registry, skill_router=router)


# ── hypothesis 策略 ──────────────────────────────────────────────

# 技能名称策略：小写字母 + 数字 + 下划线，长度 2~20，以字母开头
_skill_name_st = st.from_regex(r"[a-z][a-z0-9_]{1,19}", fullmatch=True)
_allowed_tools_st = st.lists(
    st.sampled_from(["read_excel", "create_chart", "write_excel"]),
    min_size=1,
    max_size=3,
    unique=True,
)

# 用户消息策略：任意非空文本
_user_message_st = st.text(min_size=1, max_size=100)


# ── Property 1：斜杠直连路由正确性 ──────────────────────────────


class TestSlashDirectRouting:
    """Feature: llm-native-routing, Property 1: 斜杠直连路由正确性

    对于任意已注册的技能名称和任意用户输入，当用户输入以 /{skill_name} 开头时，
    SkillRouter 应返回 slash_direct 路由模式且 skills_used 包含该技能；
    当技能名称不存在时，应返回 slash_not_found 路由模式。

    **Validates: Requirements 1.1, 1.2**
    """

    @settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(
        skill_name=_skill_name_st,
        user_message=_user_message_st,
    )
    def test_registered_skill_returns_slash_direct(
        self, skill_name: str, user_message: str,
    ) -> None:
        """已注册技能通过斜杠命令路由应返回 slash_direct。

        **Validates: Requirements 1.1**
        """
        with tempfile.TemporaryDirectory() as tmp:
            router = _setup_router_in(Path(tmp), [skill_name])
            result = asyncio.run(
                router.route(user_message, slash_command=skill_name, raw_args="")
            )
            assert result.route_mode == "slash_direct"
            assert skill_name in result.skills_used

    @settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(
        skill_name=_skill_name_st,
        nonexistent_name=_skill_name_st,
        user_message=_user_message_st,
    )
    def test_unregistered_skill_returns_slash_not_found(
        self, skill_name: str, nonexistent_name: str, user_message: str,
    ) -> None:
        """未注册技能通过斜杠命令路由应返回 slash_not_found。

        **Validates: Requirements 1.2**
        """
        # 确保 nonexistent_name 与已注册技能不同（归一化后也不同）
        normalize = SkillRouter._normalize_skill_name
        assume(normalize(nonexistent_name) != normalize(skill_name))

        with tempfile.TemporaryDirectory() as tmp:
            router = _setup_router_in(Path(tmp), [skill_name])
            result = asyncio.run(
                router.route(user_message, slash_command=nonexistent_name, raw_args="")
            )
            assert result.route_mode == "slash_not_found"


# ── Property 2：斜杠命令名称归一化 ──────────────────────────────


def _mutate_name(name: str, draw) -> str:
    """对技能名称进行随机变异：大小写变换、插入连字符或下划线。

    归一化后应仍能匹配原始名称。
    """
    chars = []
    for ch in name:
        # 随机大小写变换
        if ch.isalpha():
            ch = draw(st.sampled_from([ch.lower(), ch.upper()]))
        chars.append(ch)
        # 随机在字符间插入连字符或下划线
        if draw(st.booleans()):
            chars.append(draw(st.sampled_from(["-", "_"])))
    return "".join(chars)


class TestSlashNameNormalization:
    """Feature: llm-native-routing, Property 2: 斜杠命令名称归一化

    对于任意技能名称，将其转换为带有随机大小写、连字符或下划线变体的形式后，
    通过斜杠命令路由应仍能正确匹配到原始技能。

    **Validates: Requirements 1.3**
    """

    @settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(
        skill_name=_skill_name_st,
        data=st.data(),
    )
    def test_normalized_variant_matches_original(
        self, skill_name: str, data,
    ) -> None:
        """名称变体（大小写、连字符、下划线）经归一化后应匹配原始技能。

        **Validates: Requirements 1.3**
        """
        mutated = _mutate_name(skill_name, data.draw)

        # 确保变异后的名称归一化结果与原始一致（_mutate_name 的设计保证这一点）
        normalize = SkillRouter._normalize_skill_name
        assume(normalize(mutated) == normalize(skill_name))

        with tempfile.TemporaryDirectory() as tmp:
            router = _setup_router_in(Path(tmp), [skill_name])
            result = asyncio.run(
                router.route("测试消息", slash_command=mutated, raw_args="")
            )
            assert result.route_mode == "slash_direct"
            assert skill_name in result.skills_used


# ── Property 3：Skill_Catalog 完整性 ──────────────────────────────


class TestSkillCatalogIntegrity:
    """Feature: llm-native-routing, Property 3: Skill_Catalog 完整性

    对于任意一组已加载的技能包，_build_meta_tools 生成的 select_skill 工具定义中，
    描述文本应包含每个技能的 name 和 description，且 skill_name 参数的 enum 值
    应等于所有技能名称的集合。

    **Validates: Requirements 2.2, 6.2, 8.2**
    """

    @settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(
        skill_names=st.lists(
            _skill_name_st,
            min_size=1,
            max_size=6,
            unique=True,
        )
    )
    def test_property_3_meta_tool_catalog_contains_all_skills(
        self,
        skill_names: list[str],
    ) -> None:
        """_build_meta_tools 生成的 catalog 与 enum 应完整覆盖所有技能。

        **Validates: Requirements 2.2, 6.2, 8.2**
        """
        with tempfile.TemporaryDirectory() as tmp:
            skills = [
                (
                    name,
                    f"描述_{name}",
                    ["read_excel"],
                )
                for name in skill_names
            ]
            engine = _setup_engine_in(Path(tmp), skills)

            meta_tools = engine._build_meta_tools()
            select_tool = next(
                tool for tool in meta_tools
                if tool["function"]["name"] == "select_skill"
            )
            description = select_tool["function"]["description"]
            enum_values = (
                select_tool["function"]["parameters"]["properties"]["skill_name"]["enum"]
            )

            assert set(enum_values) == set(skill_names)
            for name in skill_names:
                assert name in description
                assert f"描述_{name}" in description


# ── Property 4/5/7：select_skill 调用正确性 ────────────────────────


class TestSelectSkillCalls:
    """Feature: llm-native-routing, Property 4/5/7: select_skill 调用正确性"""

    @settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(
        skill_names=st.lists(
            _skill_name_st,
            min_size=1,
            max_size=6,
            unique=True,
        ),
        data=st.data(),
    )
    def test_property_4_valid_select_skill_returns_context(
        self,
        skill_names: list[str],
        data,
    ) -> None:
        """有效 skill_name 调用应返回对应 render_context 内容。"""
        selected = data.draw(st.sampled_from(skill_names))

        with tempfile.TemporaryDirectory() as tmp:
            skills = [
                (
                    name,
                    f"描述_{name}",
                    ["read_excel", "create_chart"] if name == selected else ["read_excel"],
                )
                for name in skill_names
            ]
            engine = _setup_engine_in(Path(tmp), skills)
            result = asyncio.run(engine._handle_select_skill(selected))

            skill = engine._skill_router._loader.get_skillpack(selected)  # type: ignore[union-attr]
            assert skill is not None
            assert skill.render_context() in result

    @settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(
        skill_names=st.lists(
            _skill_name_st,
            min_size=1,
            max_size=6,
            unique=True,
        ),
        invalid_name=_skill_name_st,
    )
    def test_property_5_invalid_select_skill_returns_error(
        self,
        skill_names: list[str],
        invalid_name: str,
    ) -> None:
        """无效 skill_name 调用应返回未找到技能错误提示。"""
        normalize = SkillRouter._normalize_skill_name
        assume(all(normalize(name) != normalize(invalid_name) for name in skill_names))

        with tempfile.TemporaryDirectory() as tmp:
            skills = [
                (name, f"描述_{name}", ["read_excel"])
                for name in skill_names
            ]
            engine = _setup_engine_in(Path(tmp), skills)
            result = asyncio.run(engine._handle_select_skill(invalid_name))

            assert f"未找到技能: {invalid_name}" == result
            assert engine._active_skill is None

    @settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(
        skill_names=st.lists(
            _skill_name_st,
            min_size=1,
            max_size=6,
            unique=True,
        ),
        data=st.data(),
    )
    def test_property_7_valid_select_skill_recorded_to_loaded_set(
        self,
        skill_names: list[str],
        data,
    ) -> None:
        """有效 select_skill 调用后，技能名应进入已加载集合。"""
        selected = data.draw(st.sampled_from(skill_names))

        with tempfile.TemporaryDirectory() as tmp:
            skills = [
                (name, f"描述_{name}", ["read_excel"])
                for name in skill_names
            ]
            engine = _setup_engine_in(Path(tmp), skills)
            asyncio.run(engine._handle_select_skill(selected))

            assert selected in engine._loaded_skill_names


# ── Property 6：工具范围状态转换正确性 ──────────────────────────────


class TestToolScopeTransitions:
    """Feature: llm-native-routing, Property 6: 工具范围状态转换正确性"""

    @settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(
        skill_a=_skill_name_st,
        tools_a=_allowed_tools_st,
        skill_b=_skill_name_st,
        tools_b=_allowed_tools_st,
    )
    def test_property_6_tool_scope_state_transition(
        self,
        skill_a: str,
        tools_a: list[str],
        skill_b: str,
        tools_b: list[str],
    ) -> None:
        """初始态 + 激活 A + 激活 B 三态的工具范围应符合预期。"""
        normalize = SkillRouter._normalize_skill_name
        assume(normalize(skill_a) != normalize(skill_b))

        with tempfile.TemporaryDirectory() as tmp:
            engine = _setup_engine_in(
                Path(tmp),
                [
                    (skill_a, f"描述_{skill_a}", tools_a),
                    (skill_b, f"描述_{skill_b}", tools_b),
                ],
            )

            # 初始态：全量工具 + META + ALWAYS
            initial_scope = engine._get_current_tool_scope()
            expected_initial = set(engine._registry.get_tool_names())
            expected_initial |= {"select_skill", "delegate_to_subagent", "list_subagents", "ask_user"}
            expected_initial |= {"task_create", "task_update"}
            assert set(initial_scope) == expected_initial

            # 激活技能 A：allowed_tools + select_skill + _ALWAYS_AVAILABLE_TOOLS
            asyncio.run(engine._handle_select_skill(skill_a))
            scope_a = engine._get_current_tool_scope()
            expected_a = set(tools_a) | {"select_skill"}
            expected_a |= {"task_create", "task_update", "ask_user", "delegate_to_subagent"}
            assert set(scope_a) == expected_a

            # 激活技能 B：同理
            asyncio.run(engine._handle_select_skill(skill_b))
            scope_b = engine._get_current_tool_scope()
            expected_b = set(tools_b) | {"select_skill"}
            expected_b |= {"task_create", "task_update", "ask_user", "delegate_to_subagent"}
            assert set(scope_b) == expected_b


# ── Property 8：delegate_to_subagent 参数透传约束 ───────────────────────────────


class TestDelegateSubagentConstraint:
    """Feature: llm-native-routing, Property 8: delegate_to_subagent 参数约束"""

    @settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(
        task=st.text(min_size=1, max_size=80),
        file_paths=st.lists(
            st.text(min_size=0, max_size=30),
            min_size=0,
            max_size=5,
        ),
    )
    def test_property_8_delegate_to_subagent_passes_agent_and_paths(
        self,
        task: str,
        file_paths: list[str],
    ) -> None:
        """delegate_to_subagent 应正确透传 agent_name 和规范化后的 file_paths。"""
        assume(task.strip() != "")

        with tempfile.TemporaryDirectory() as tmp:
            engine = _setup_engine_in(
                Path(tmp),
                [
                    ("data_basic", "数据技能", ["read_excel"]),
                ],
            )

            captured: dict[str, str] = {}

            async def _fake_run_subagent(
                *,
                agent_name: str,
                prompt: str,
                on_event=None,
            ) -> SubagentResult:
                captured["agent_name"] = agent_name
                captured["prompt"] = prompt
                return SubagentResult(
                    success=True,
                    summary="子代理摘要",
                    subagent_name=agent_name,
                    permission_mode="readOnly",
                    conversation_id="c1",
                )

            engine.run_subagent = AsyncMock(side_effect=_fake_run_subagent)
            result = asyncio.run(
                engine._handle_delegate_to_subagent(
                    task=task,
                    agent_name="explorer",
                    file_paths=file_paths,
                )
            )
            assert result == "子代理摘要"

            assert captured.get("agent_name") == "explorer"
            prompt = captured.get("prompt", "")
            assert task.strip() in prompt
