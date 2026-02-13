"""AgentEngine 单元测试：覆盖 Tool Calling 循环核心逻辑。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from excelmanus.config import ExcelManusConfig
from excelmanus.engine import AgentEngine, ChatResult, ToolCallResult
from excelmanus.events import EventType
from excelmanus.skillpacks import SkillMatchResult, Skillpack
from excelmanus.tools import ToolRegistry
from excelmanus.tools.registry import ToolDef


# ── 辅助工厂 ──────────────────────────────────────────────


def _make_config(**overrides) -> ExcelManusConfig:
    """创建测试用配置。"""
    defaults = {
        "api_key": "test-key",
        "base_url": "https://test.example.com/v1",
        "model": "test-model",
        "max_iterations": 20,
        "max_consecutive_failures": 3,
        "workspace_root": ".",
    }
    defaults.update(overrides)
    return ExcelManusConfig(**defaults)


def _make_registry_with_tools() -> ToolRegistry:
    """创建包含简单测试工具的 ToolRegistry。"""
    registry = ToolRegistry()

    def add_numbers(a: int, b: int) -> int:
        return a + b

    def fail_tool() -> str:
        raise RuntimeError("工具执行失败")

    tools = [
        ToolDef(
            name="add_numbers",
            description="两数相加",
            input_schema={
                "type": "object",
                "properties": {
                    "a": {"type": "integer"},
                    "b": {"type": "integer"},
                },
                "required": ["a", "b"],
            },
            func=add_numbers,
        ),
        ToolDef(
            name="fail_tool",
            description="总是失败的工具",
            input_schema={"type": "object", "properties": {}},
            func=fail_tool,
        ),
    ]
    registry.register_tools(tools)
    return registry


def _make_text_response(content: str) -> MagicMock:
    """构造一个纯文本 LLM 响应（无 tool_calls）。"""
    message = SimpleNamespace(content=content, tool_calls=None)
    choice = SimpleNamespace(message=message)
    response = SimpleNamespace(choices=[choice])
    return response


def _make_tool_call_response(
    tool_calls: list[tuple[str, str, str]],
    content: str | None = None,
) -> MagicMock:
    """构造一个包含 tool_calls 的 LLM 响应。

    Args:
        tool_calls: [(tool_call_id, tool_name, arguments_json), ...]
        content: 可选的文本内容
    """
    tc_objects = []
    for call_id, name, args in tool_calls:
        tc = SimpleNamespace(
            id=call_id,
            function=SimpleNamespace(name=name, arguments=args),
        )
        tc_objects.append(tc)

    message = SimpleNamespace(content=content, tool_calls=tc_objects)
    choice = SimpleNamespace(message=message)
    response = SimpleNamespace(choices=[choice])
    return response


# ── 测试用例 ──────────────────────────────────────────────


class TestAgentEngineInit:
    """AgentEngine 初始化测试。"""

    def test_creates_async_client(self) -> None:
        """验证初始化时创建 AsyncOpenAI 客户端。"""
        config = _make_config()
        registry = ToolRegistry()
        engine = AgentEngine(config, registry)
        assert engine._client is not None
        assert engine._config is config
        assert engine._registry is registry


class TestControlCommandFullAccess:
    """会话级 /fullAccess 控制命令测试。"""

    @pytest.mark.asyncio
    async def test_status_defaults_to_restricted(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)

        result = await engine.chat("/fullAccess status")
        assert "restricted" in result
        assert engine.full_access_enabled is False
        assert engine.last_route_result.route_mode == "control_command"

    @pytest.mark.asyncio
    async def test_on_then_off(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)

        on_result = await engine.chat("/fullAccess")
        assert "full_access" in on_result
        assert engine.full_access_enabled is True
        assert engine.last_route_result.route_mode == "control_command"

        off_result = await engine.chat("/fullAccess off")
        assert "restricted" in off_result
        assert engine.full_access_enabled is False
        assert engine.last_route_result.route_mode == "control_command"

    @pytest.mark.asyncio
    async def test_command_does_not_invoke_llm_or_write_memory(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)

        mocked_create = AsyncMock(return_value=_make_text_response("不应被调用"))
        engine._client.chat.completions.create = mocked_create
        before_count = len(engine.memory.get_messages())

        result = await engine.chat("/full_access status")
        assert "restricted" in result
        mocked_create.assert_not_called()
        after_count = len(engine.memory.get_messages())
        assert before_count == after_count == 1

    @pytest.mark.asyncio
    async def test_route_blocked_skillpacks_switch_with_full_access(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)

        route_result = SkillMatchResult(
            skills_used=[],
            tool_scope=[],
            route_mode="llm_confirm",
            system_contexts=[],
        )
        mock_router = MagicMock()
        mock_router.route = AsyncMock(return_value=route_result)
        engine._skill_router = mock_router

        engine._client.chat.completions.create = AsyncMock(
            return_value=_make_text_response("ok")
        )
        await engine.chat("普通请求")
        _, kwargs_default = mock_router.route.call_args
        assert kwargs_default["blocked_skillpacks"] == {"excel_code_runner"}

        await engine.chat("/fullAccess on")
        mock_router.route.reset_mock()
        engine._client.chat.completions.create = AsyncMock(
            return_value=_make_text_response("ok2")
        )
        await engine.chat("普通请求2")
        _, kwargs_unlocked = mock_router.route.call_args
        assert kwargs_unlocked["blocked_skillpacks"] is None


class TestControlCommandSubagent:
    """会话级 /subagent 控制命令测试。"""

    @pytest.mark.asyncio
    async def test_status_defaults_to_enabled(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)

        result = await engine.chat("/subagent status")
        assert "enabled" in result
        assert engine.subagent_enabled is True
        assert engine.last_route_result.route_mode == "control_command"

    @pytest.mark.asyncio
    async def test_off_then_on(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)

        off_result = await engine.chat("/subagent off")
        assert "已关闭" in off_result
        assert engine.subagent_enabled is False
        assert engine.last_route_result.route_mode == "control_command"

        on_result = await engine.chat("/subagent on")
        assert "已开启" in on_result
        assert engine.subagent_enabled is True
        assert engine.last_route_result.route_mode == "control_command"

    @pytest.mark.asyncio
    async def test_no_args_defaults_to_status(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)

        result = await engine.chat("/subagent")
        assert "当前 fork 子代理状态" in result
        assert engine.subagent_enabled is True

    @pytest.mark.asyncio
    async def test_alias_sub_agent_supported(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)

        result = await engine.chat("/sub_agent off")
        assert "已关闭" in result
        assert engine.subagent_enabled is False

    @pytest.mark.asyncio
    async def test_command_does_not_invoke_llm_or_write_memory(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)

        mocked_create = AsyncMock(return_value=_make_text_response("不应被调用"))
        engine._client.chat.completions.create = mocked_create
        before_count = len(engine.memory.get_messages())

        result = await engine.chat("/subagent off")
        assert "已关闭" in result
        mocked_create.assert_not_called()
        after_count = len(engine.memory.get_messages())
        assert before_count == after_count == 1


class TestManualSkillSlashCommand:
    """手动 Skill 斜杠命令解析与路由。"""

    @pytest.mark.asyncio
    async def test_slash_skill_command_maps_to_slash_route_args(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)

        route_result = SkillMatchResult(
            skills_used=["data_basic"],
            tool_scope=[],
            route_mode="hint_direct",
            system_contexts=[],
        )
        mock_loader = MagicMock()
        mock_loader.get_skillpacks.return_value = {"data_basic": MagicMock()}
        mock_router = MagicMock()
        mock_router._loader = mock_loader
        mock_router.route = AsyncMock(return_value=route_result)
        engine._skill_router = mock_router
        engine._client.chat.completions.create = AsyncMock(
            return_value=_make_text_response("ok")
        )

        result = await engine.chat("/data_basic 请分析这个文件")
        assert result == "ok"

        _, kwargs = mock_router.route.call_args
        assert kwargs["slash_command"] == "data_basic"
        assert kwargs["raw_args"] == "请分析这个文件"

    @pytest.mark.asyncio
    async def test_slash_skill_command_overrides_external_skill_hints(self) -> None:
        """斜杠命令优先级高于外部 skill_hints（skill_hints 已不再传递给 route）。"""
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)

        route_result = SkillMatchResult(
            skills_used=["data_basic"],
            tool_scope=[],
            route_mode="hint_direct",
            system_contexts=[],
        )
        mock_loader = MagicMock()
        mock_loader.get_skillpacks.return_value = {"data_basic": MagicMock()}
        mock_router = MagicMock()
        mock_router._loader = mock_loader
        mock_router.route = AsyncMock(return_value=route_result)
        engine._skill_router = mock_router
        engine._client.chat.completions.create = AsyncMock(
            return_value=_make_text_response("ok")
        )

        await engine.chat("/data_basic 请分析", skill_hints=["chart_basic"])
        _, kwargs = mock_router.route.call_args
        # skill_hints 已从 route 接口中移除，不再传递
        assert "skill_hints" not in kwargs
        assert kwargs["slash_command"] == "data_basic"
        assert kwargs["raw_args"] == "请分析"

    @pytest.mark.asyncio
    async def test_explicit_slash_command_arguments_pass_through(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)

        route_result = SkillMatchResult(
            skills_used=["data_basic"],
            tool_scope=[],
            route_mode="slash_direct",
            system_contexts=[],
            parameterized=True,
        )
        mock_loader = MagicMock()
        mock_loader.get_skillpacks.return_value = {"data_basic": MagicMock()}
        mock_router = MagicMock()
        mock_router._loader = mock_loader
        mock_router.route = AsyncMock(return_value=route_result)
        engine._skill_router = mock_router
        engine._client.chat.completions.create = AsyncMock(
            return_value=_make_text_response("ok")
        )

        await engine.chat(
            "执行技能",
            slash_command="data_basic",
            raw_args='"sales data.xlsx" bar',
        )
        _, kwargs = mock_router.route.call_args
        assert kwargs["slash_command"] == "data_basic"
        assert kwargs["raw_args"] == '"sales data.xlsx" bar'

    def test_resolve_skill_command_normalizes_dash_and_underscore(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)

        mock_loader = MagicMock()
        mock_loader.get_skillpacks.return_value = {"data_basic": MagicMock()}
        mock_router = MagicMock()
        mock_router._loader = mock_loader
        engine._skill_router = mock_router

        assert engine.resolve_skill_command("/data_basic") == "data_basic"
        assert engine.resolve_skill_command("/data-basic 参数") == "data_basic"
        assert engine.resolve_skill_command("/DATA_BASIC") == "data_basic"

    def test_resolve_skill_command_ignores_path_like_input(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)

        mock_loader = MagicMock()
        mock_loader.get_skillpacks.return_value = {"data_basic": MagicMock()}
        mock_router = MagicMock()
        mock_router._loader = mock_loader
        engine._skill_router = mock_router

        assert engine.resolve_skill_command("/Users/test/file.xlsx") is None
        assert engine.resolve_skill_command("/tmp/data.xlsx") is None


class TestForkSubagentExecution:
    """fork 子代理执行流程测试（fork_plan 已移除，子代理不再自动触发）。"""

    @pytest.mark.asyncio
    async def test_chat_skips_fork_after_fork_plan_removed(self) -> None:
        """fork_plan 已从 SkillMatchResult 移除，子代理不再自动触发。"""
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)

        route_result = SkillMatchResult(
            skills_used=["excel_code_runner"],
            tool_scope=["add_numbers"],
            route_mode="hint_direct+fork",
            system_contexts=["[Skillpack] excel_code_runner"],
        )
        engine._route_skills = AsyncMock(return_value=route_result)

        main_reply = _make_text_response("主代理执行完成。")
        engine._client.chat.completions.create = AsyncMock(return_value=main_reply)

        events = []

        def _on_event(event) -> None:
            events.append(event)

        result = await engine.chat("请处理这个大文件", on_event=_on_event)
        assert result == "主代理执行完成。"
        # 只调用一次 LLM（主代理），不再触发 fork 子代理
        assert engine._client.chat.completions.create.call_count == 1
        assert "+fork_executed" not in engine.last_route_result.route_mode
        event_types = [e.event_type for e in events]
        assert EventType.SUBAGENT_START not in event_types

    @pytest.mark.asyncio
    async def test_chat_skips_fork_when_subagent_disabled(self) -> None:
        config = _make_config(subagent_enabled=False)
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)

        route_result = SkillMatchResult(
            skills_used=["excel_code_runner"],
            tool_scope=["add_numbers"],
            route_mode="hint_direct+fork",
            system_contexts=["[Skillpack] excel_code_runner"],
        )
        engine._route_skills = AsyncMock(return_value=route_result)

        main_reply = _make_text_response("主代理执行完成。")
        engine._client.chat.completions.create = AsyncMock(return_value=main_reply)

        result = await engine.chat("请处理这个大文件")
        assert result == "主代理执行完成。"
        assert engine._client.chat.completions.create.call_count == 1
        assert "+fork_executed" not in engine.last_route_result.route_mode


class TestExploreDataSubagent:
    """explore_data 子代理工具测试（task5）。"""

    @pytest.mark.asyncio
    async def test_explore_data_tool_call_runs_subagent_and_emits_events(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        engine._execute_subagent_loop = AsyncMock(return_value="探查摘要")

        tc = SimpleNamespace(
            id="call_1",
            function=SimpleNamespace(
                name="explore_data",
                arguments=json.dumps(
                    {"task": "探查销量异常", "file_paths": ["sales.xlsx"]}
                ),
            ),
        )

        events = []
        result = await engine._execute_tool_call(
            tc=tc,
            tool_scope=["explore_data"],
            on_event=events.append,
            iteration=1,
        )

        assert result.success is True
        assert result.result == "探查摘要"
        event_types = [e.event_type for e in events]
        assert EventType.SUBAGENT_START in event_types
        assert EventType.SUBAGENT_SUMMARY in event_types
        assert EventType.SUBAGENT_END in event_types

    @pytest.mark.asyncio
    async def test_explore_data_rejects_invalid_file_paths_type(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)

        tc = SimpleNamespace(
            id="call_1",
            function=SimpleNamespace(
                name="explore_data",
                arguments=json.dumps(
                    {"task": "探查销量异常", "file_paths": "sales.xlsx"}
                ),
            ),
        )

        result = await engine._execute_tool_call(
            tc=tc,
            tool_scope=["explore_data"],
            on_event=None,
            iteration=1,
        )

        assert result.success is False
        assert "file_paths 必须为字符串数组" in result.result

    @pytest.mark.asyncio
    async def test_execute_subagent_loop_circuit_breaker(self) -> None:
        config = _make_config(subagent_max_consecutive_failures=2)
        registry = ToolRegistry()

        def fail_read_excel() -> str:
            raise RuntimeError("读取失败")

        registry.register_tool(
            ToolDef(
                name="read_excel",
                description="读取表格",
                input_schema={"type": "object", "properties": {}},
                func=fail_read_excel,
            )
        )
        engine = AgentEngine(config, registry)

        # 每轮都让子代理调用 read_excel，触发连续失败熔断
        tool_resp_1 = _make_tool_call_response([("c1", "read_excel", "{}")])
        tool_resp_2 = _make_tool_call_response([("c2", "read_excel", "{}")])
        engine._client.chat.completions.create = AsyncMock(
            side_effect=[tool_resp_1, tool_resp_2]
        )

        summary = await engine._execute_subagent_loop(
            system_prompt="测试提示",
            tool_scope=["read_excel"],
            max_iterations=5,
        )
        assert "子代理连续 2 次工具调用失败" in summary

    @pytest.mark.asyncio
    async def test_execute_subagent_loop_iteration_limit(self) -> None:
        config = _make_config()
        registry = ToolRegistry()

        def ok_read_excel() -> str:
            return "ok"

        registry.register_tool(
            ToolDef(
                name="read_excel",
                description="读取表格",
                input_schema={"type": "object", "properties": {}},
                func=ok_read_excel,
            )
        )
        engine = AgentEngine(config, registry)

        # 单轮始终返回 tool_call，触发迭代上限返回
        tool_resp = _make_tool_call_response([("c1", "read_excel", "{}")])
        engine._client.chat.completions.create = AsyncMock(side_effect=[tool_resp])

        summary = await engine._execute_subagent_loop(
            system_prompt="测试提示",
            tool_scope=["read_excel"],
            max_iterations=1,
        )
        assert "子代理达到最大迭代次数（1）" in summary


class TestMetaToolDefinitions:
    """元工具定义结构与动态更新测试（task6.4）。"""

    def test_build_meta_tools_schema_structure(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)

        mock_router = MagicMock()
        mock_router.build_skill_catalog.return_value = (
            "可用技能：\n- data_basic：数据处理\n- chart_basic：图表生成",
            ["data_basic", "chart_basic"],
        )
        engine._skill_router = mock_router

        meta_tools = engine._build_meta_tools()
        assert len(meta_tools) == 2
        by_name = {tool["function"]["name"]: tool for tool in meta_tools}
        assert "select_skill" in by_name
        assert "explore_data" in by_name

        select_tool = by_name["select_skill"]["function"]
        select_params = select_tool["parameters"]
        assert "Skill_Catalog" in select_tool["description"]
        assert select_params["required"] == ["skill_name"]
        assert select_params["properties"]["skill_name"]["enum"] == [
            "data_basic",
            "chart_basic",
        ]
        assert "reason" in select_params["properties"]

        explore_tool = by_name["explore_data"]["function"]
        explore_params = explore_tool["parameters"]
        assert explore_params["required"] == ["task"]
        assert explore_params["properties"]["file_paths"]["type"] == "array"

    def test_build_meta_tools_reflects_updated_catalog(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)

        mock_router = MagicMock()
        mock_router.build_skill_catalog.side_effect = [
            ("可用技能：\n- data_basic：数据处理", ["data_basic"]),
            (
                "可用技能：\n- data_basic：数据处理\n- chart_basic：图表生成",
                ["data_basic", "chart_basic"],
            ),
        ]
        engine._skill_router = mock_router

        first = engine._build_meta_tools()
        second = engine._build_meta_tools()

        first_enum = first[0]["function"]["parameters"]["properties"]["skill_name"]["enum"]
        second_enum = second[0]["function"]["parameters"]["properties"]["skill_name"]["enum"]
        assert first_enum == ["data_basic"]
        assert second_enum == ["data_basic", "chart_basic"]


class TestMetaToolScopeUpdate:
    """元工具调用后同轮更新工具范围（task6.1）。"""

    @pytest.mark.asyncio
    async def test_select_skill_updates_scope_within_same_iteration(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)

        data_skill = Skillpack(
            name="data_basic",
            description="数据处理技能",
            allowed_tools=["add_numbers"],
            triggers=["数据"],
            instructions="使用 add_numbers 进行测试。",
            source="system",
            root_dir="/tmp/data_basic",
        )

        mock_loader = MagicMock()
        mock_loader.get_skillpacks.return_value = {"data_basic": data_skill}
        mock_loader.get_skillpack.return_value = data_skill

        mock_router = MagicMock()
        mock_router._loader = mock_loader
        mock_router._find_skill_by_name = MagicMock(return_value=data_skill)
        mock_router.build_skill_catalog.return_value = (
            "可用技能：\n- data_basic：数据处理技能",
            ["data_basic"],
        )
        engine._skill_router = mock_router

        route_result = SkillMatchResult(
            skills_used=[],
            tool_scope=["select_skill"],
            route_mode="slash_direct",
            system_contexts=[],
        )
        engine._route_skills = AsyncMock(return_value=route_result)

        first_resp = _make_tool_call_response(
            [
                ("call_1", "select_skill", json.dumps({"skill_name": "data_basic"})),
                ("call_2", "add_numbers", json.dumps({"a": 1, "b": 2})),
            ]
        )
        second_resp = _make_text_response("done")
        engine._client.chat.completions.create = AsyncMock(
            side_effect=[first_resp, second_resp]
        )

        result = await engine.chat("测试同轮切换")
        assert result == "done"

        tool_msgs = [m for m in engine.memory.get_messages() if m.get("role") == "tool"]
        add_numbers_msg = next(m for m in tool_msgs if m.get("tool_call_id") == "call_2")
        assert "3" in add_numbers_msg.get("content", "")


class TestChatPureText:
    """纯文本回复场景（Requirement 1.3）。"""

    @pytest.mark.asyncio
    async def test_returns_text_when_no_tool_calls(self) -> None:
        """LLM 返回纯文本时，直接返回该文本。"""
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)

        mock_response = _make_text_response("你好，这是回复。")
        engine._client.chat.completions.create = AsyncMock(
            return_value=mock_response
        )

        result = await engine.chat("你好")
        assert result == "你好，这是回复。"

    @pytest.mark.asyncio
    async def test_empty_content_returns_empty_string(self) -> None:
        """LLM 返回 content=None 时，返回空字符串。"""
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)

        mock_response = _make_text_response("")
        # 模拟 content 为 None
        mock_response.choices[0].message.content = None
        engine._client.chat.completions.create = AsyncMock(
            return_value=mock_response
        )

        result = await engine.chat("测试")
        assert result == ""


class TestChatToolCalling:
    """Tool Calling 循环场景（Requirements 1.1, 1.2, 1.9）。"""

    @pytest.mark.asyncio
    async def test_single_tool_call_then_text(self) -> None:
        """单个 tool_call 执行后，LLM 返回文本结束循环。"""
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)

        # 第一轮：LLM 返回 tool_call
        tool_response = _make_tool_call_response(
            [("call_1", "add_numbers", json.dumps({"a": 3, "b": 5}))]
        )
        # 第二轮：LLM 返回纯文本
        text_response = _make_text_response("3 + 5 = 8")

        engine._client.chat.completions.create = AsyncMock(
            side_effect=[tool_response, text_response]
        )

        result = await engine.chat("计算 3 + 5")
        assert result == "3 + 5 = 8"

    @pytest.mark.asyncio
    async def test_multiple_tool_calls_in_single_response(self) -> None:
        """单轮响应包含多个 tool_calls（Requirement 1.9）。"""
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)

        # 第一轮：LLM 返回两个 tool_calls
        tool_response = _make_tool_call_response([
            ("call_1", "add_numbers", json.dumps({"a": 1, "b": 2})),
            ("call_2", "add_numbers", json.dumps({"a": 3, "b": 4})),
        ])
        # 第二轮：LLM 返回纯文本
        text_response = _make_text_response("结果分别是 3 和 7")

        engine._client.chat.completions.create = AsyncMock(
            side_effect=[tool_response, text_response]
        )

        result = await engine.chat("分别计算 1+2 和 3+4")
        assert result == "结果分别是 3 和 7"

    @pytest.mark.asyncio
    async def test_tool_result_fed_back_to_memory(self) -> None:
        """工具执行结果被正确回填到对话记忆。"""
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)

        tool_response = _make_tool_call_response(
            [("call_1", "add_numbers", json.dumps({"a": 10, "b": 20}))]
        )
        text_response = _make_text_response("结果是 30")

        engine._client.chat.completions.create = AsyncMock(
            side_effect=[tool_response, text_response]
        )

        await engine.chat("计算 10 + 20")

        # 检查记忆中包含 tool result 消息
        messages = engine.memory.get_messages()
        tool_msgs = [m for m in messages if m.get("role") == "tool"]
        assert len(tool_msgs) == 1
        assert "30" in tool_msgs[0]["content"]

    @pytest.mark.asyncio
    async def test_preserves_assistant_extra_fields_for_tool_message(self) -> None:
        """assistant tool 消息应保留扩展字段（供应商兼容）。"""
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)

        message = SimpleNamespace(
            content=None,
            reasoning_content="internal-thought",
            tool_calls=[
                SimpleNamespace(
                    id="call_1",
                    function=SimpleNamespace(
                        name="add_numbers",
                        arguments=json.dumps({"a": 1, "b": 2}),
                    ),
                )
            ],
        )
        tool_response = SimpleNamespace(choices=[SimpleNamespace(message=message)])
        text_response = _make_text_response("done")

        engine._client.chat.completions.create = AsyncMock(
            side_effect=[tool_response, text_response]
        )

        result = await engine.chat("计算")
        assert result == "done"

        msgs = engine.memory.get_messages()
        assistant_with_tool = [m for m in msgs if m.get("tool_calls")]
        assert len(assistant_with_tool) == 1
        assert assistant_with_tool[0].get("reasoning_content") == "internal-thought"


class TestChatToolError:
    """工具异常处理场景（Requirement 1.5）。"""

    @pytest.mark.asyncio
    async def test_tool_error_fed_back_as_tool_message(self) -> None:
        """工具执行异常被捕获并作为 tool message 反馈给 LLM。"""
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)

        # 第一轮：调用会失败的工具
        tool_response = _make_tool_call_response(
            [("call_1", "fail_tool", "{}")]
        )
        # 第二轮：LLM 收到错误后返回文本
        text_response = _make_text_response("工具执行出错了，请检查。")

        engine._client.chat.completions.create = AsyncMock(
            side_effect=[tool_response, text_response]
        )

        result = await engine.chat("执行失败工具")
        assert "工具执行出错" in result or "检查" in result

        # 验证错误信息被回填到记忆
        messages = engine.memory.get_messages()
        tool_msgs = [m for m in messages if m.get("role") == "tool"]
        assert len(tool_msgs) == 1
        assert "错误" in tool_msgs[0]["content"]

    @pytest.mark.asyncio
    async def test_malformed_arguments_should_not_execute_tool(self) -> None:
        """参数 JSON 非法时不应执行工具函数。"""
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)

        bad_args_response = _make_tool_call_response(
            [("call_1", "add_numbers", '{"a": 1')]
        )
        text_response = _make_text_response("已处理")
        engine._client.chat.completions.create = AsyncMock(
            side_effect=[bad_args_response, text_response]
        )

        with patch(
            "excelmanus.engine.asyncio.to_thread", new_callable=AsyncMock
        ) as mock_to_thread:
            result = await engine.chat("坏参数测试")
            assert result == "已处理"
            # 参数解析失败后不应执行工具
            mock_to_thread.assert_not_called()

        msgs = engine.memory.get_messages()
        tool_msgs = [m for m in msgs if m.get("role") == "tool"]
        assert len(tool_msgs) == 1
        assert "参数解析错误" in tool_msgs[0]["content"]


class TestConsecutiveFailureCircuitBreaker:
    """连续失败熔断场景（Requirement 1.6）。"""

    @pytest.mark.asyncio
    async def test_circuit_breaker_after_consecutive_failures(self) -> None:
        """连续 3 次工具失败后，熔断终止并返回错误摘要。"""
        config = _make_config(max_consecutive_failures=3)
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)

        # 构造 3 轮连续失败的 tool_call 响应
        fail_responses = [
            _make_tool_call_response([
                (f"call_{i}", "fail_tool", "{}")
            ])
            for i in range(1, 4)
        ]

        engine._client.chat.completions.create = AsyncMock(
            side_effect=fail_responses
        )

        result = await engine.chat("连续失败测试")
        assert "连续" in result
        assert "失败" in result

    @pytest.mark.asyncio
    async def test_success_resets_failure_counter(self) -> None:
        """成功的工具调用重置连续失败计数。"""
        config = _make_config(max_consecutive_failures=3)
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)

        # 第一轮：失败
        fail_resp_1 = _make_tool_call_response([("c1", "fail_tool", "{}")])
        # 第二轮：成功（重置计数）
        success_resp = _make_tool_call_response(
            [("c2", "add_numbers", json.dumps({"a": 1, "b": 1}))]
        )
        # 第三轮：失败
        fail_resp_2 = _make_tool_call_response([("c3", "fail_tool", "{}")])
        # 第四轮：纯文本结束
        text_resp = _make_text_response("完成")

        engine._client.chat.completions.create = AsyncMock(
            side_effect=[fail_resp_1, success_resp, fail_resp_2, text_resp]
        )

        result = await engine.chat("混合成功失败")
        # 不应触发熔断，应正常返回文本
        assert result == "完成"

    @pytest.mark.asyncio
    async def test_circuit_breaker_keeps_tool_call_result_pairs(self) -> None:
        """单轮多 tool_calls 熔断后，也应为每个 tool_call 回填结果。"""
        config = _make_config(max_consecutive_failures=1)
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)

        # 单轮两个失败调用；第一次失败即触发熔断
        tool_response = _make_tool_call_response(
            [
                ("call_1", "fail_tool", "{}"),
                ("call_2", "fail_tool", "{}"),
            ]
        )
        engine._client.chat.completions.create = AsyncMock(
            side_effect=[tool_response]
        )

        result = await engine.chat("触发熔断")
        assert "终止执行" in result

        msgs = engine.memory.get_messages()
        tool_results = [m for m in msgs if m.get("role") == "tool"]
        assert {m["tool_call_id"] for m in tool_results} == {"call_1", "call_2"}


class TestIterationLimit:
    """迭代上限保护场景（Requirement 1.4）。"""

    @pytest.mark.asyncio
    async def test_truncates_at_max_iterations(self) -> None:
        """达到迭代上限时截断返回。"""
        config = _make_config(max_iterations=3)
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)

        # 每轮都返回 tool_call，永不返回纯文本
        tool_responses = [
            _make_tool_call_response(
                [(f"call_{i}", "add_numbers", json.dumps({"a": i, "b": i}))]
            )
            for i in range(1, 5)  # 多准备几个
        ]

        engine._client.chat.completions.create = AsyncMock(
            side_effect=tool_responses
        )

        result = await engine.chat("无限循环测试")
        assert "最大迭代次数" in result or "3" in result


class TestAsyncToolExecution:
    """异步工具执行场景（Requirement 1.10）。"""

    @pytest.mark.asyncio
    async def test_blocking_tool_runs_in_thread(self) -> None:
        """阻塞型工具通过 asyncio.to_thread 隔离执行。"""
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)

        tool_response = _make_tool_call_response(
            [("call_1", "add_numbers", json.dumps({"a": 5, "b": 10}))]
        )
        text_response = _make_text_response("结果是 15")

        engine._client.chat.completions.create = AsyncMock(
            side_effect=[tool_response, text_response]
        )

        # 使用 patch 验证 asyncio.to_thread 被调用
        with patch("excelmanus.engine.asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
            mock_to_thread.return_value = 15
            result = await engine.chat("计算 5 + 10")

            # 验证 to_thread 被调用
            mock_to_thread.assert_called_once()
            call_args = mock_to_thread.call_args
            # 第一个参数是 registry.call_tool
            assert call_args[0][1] == "add_numbers"


class TestClearMemory:
    """清除记忆测试。"""

    def test_clear_memory(self) -> None:
        """clear_memory 清除对话历史。"""
        config = _make_config()
        registry = ToolRegistry()
        engine = AgentEngine(config, registry)

        engine.memory.add_user_message("测试消息")
        assert len(engine.memory.get_messages()) > 1  # system + user

        engine.clear_memory()
        # 清除后只剩 system prompt
        assert len(engine.memory.get_messages()) == 1
        assert engine.memory.get_messages()[0]["role"] == "system"


class TestDataModels:
    """数据模型测试。"""

    def test_tool_call_result_defaults(self) -> None:
        """ToolCallResult 默认值正确。"""
        r = ToolCallResult(
            tool_name="test", arguments={}, result="ok", success=True
        )
        assert r.error is None
        assert r.success is True

    def test_chat_result_defaults(self) -> None:
        """ChatResult 默认值正确。"""
        r = ChatResult(reply="hello")
        assert r.tool_calls == []
        assert r.iterations == 0
        assert r.truncated is False


# ── 属性测试（Property-Based Tests）────────────────────────
# 使用 hypothesis 框架，每项至少 100 次迭代

import string
from hypothesis import given, settings, assume
from hypothesis import strategies as st


# ── 辅助策略 ──────────────────────────────────────────────

# 生成合法的工具名称
tool_name_st = st.from_regex(r"[a-z][a-z0-9_]{2,20}", fullmatch=True)

# 生成非空文本内容
nonempty_text_st = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "Z")),
    min_size=1,
    max_size=200,
)

# 生成 tool_call_id
tool_call_id_st = st.from_regex(r"call_[a-z0-9]{4,10}", fullmatch=True)


# ---------------------------------------------------------------------------
# Property 1：消息构建完整性
# **Validates: Requirements 1.1, 1.7**
# ---------------------------------------------------------------------------


@given(
    history=st.lists(
        st.tuples(
            st.sampled_from(["user", "assistant"]),
            nonempty_text_st,
        ),
        min_size=0,
        max_size=10,
    ),
    new_input=nonempty_text_st,
)
@settings(max_examples=100)
def test_property_1_message_construction_completeness(
    history: list[tuple[str, str]],
    new_input: str,
) -> None:
    """Property 1：消息构建完整性。

    对于任意历史与新输入，构建出的消息序列必须保持：
    - system 在首位
    - 历史有序
    - 新用户消息在末位

    **Validates: Requirements 1.1, 1.7**
    """
    from excelmanus.memory import ConversationMemory, _DEFAULT_SYSTEM_PROMPT

    config = _make_config()
    mem = ConversationMemory(config)

    # 填充历史消息
    for role, content in history:
        if role == "user":
            mem.add_user_message(content)
        else:
            mem.add_assistant_message(content)

    # 添加新用户消息
    mem.add_user_message(new_input)

    messages = mem.get_messages()

    # 不变量 1：system 消息在首位
    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == _DEFAULT_SYSTEM_PROMPT

    # 不变量 2：最后一条消息是新用户输入
    assert messages[-1]["role"] == "user"
    assert messages[-1]["content"] == new_input

    # 不变量 3：消息序列长度 = 1(system) + 历史条数 + 1(新输入)
    # 注意：截断可能减少历史条数，但 system 和最后一条始终保留
    assert len(messages) >= 2  # 至少 system + 新输入

    # 不变量 4：所有消息 role 合法
    valid_roles = {"system", "user", "assistant", "tool"}
    for m in messages:
        assert m["role"] in valid_roles


@given(
    n_tools=st.integers(min_value=1, max_value=5),
)
@settings(max_examples=100)
def test_property_1_tools_schema_attached(n_tools: int) -> None:
    """Property 1 补充：Engine 构建请求时附全量 tools schema。

    **Validates: Requirements 1.1, 1.7**
    """
    registry = ToolRegistry()
    tools = []
    for i in range(n_tools):
        tools.append(
            ToolDef(
                name=f"tool_{i}",
                description=f"测试工具 {i}",
                input_schema={"type": "object", "properties": {}},
                func=lambda: "ok",
            )
        )
    registry.register_tools(tools)

    schemas = registry.get_openai_schemas()

    # 不变量：schema 数量等于注册的工具数量
    assert len(schemas) == n_tools

    # 不变量：每个 schema 包含必要字段
    for s in schemas:
        assert s["type"] == "function"
        # 兼容两种格式：扁平结构或嵌套 function 结构
        if "function" in s:
            assert "name" in s["function"]
            assert "description" in s["function"]
            assert "parameters" in s["function"]
        else:
            assert "name" in s
            assert "description" in s
            assert "parameters" in s


# ---------------------------------------------------------------------------
# Property 2：Tool Call 解析与调用
# **Validates: Requirements 1.2**
# ---------------------------------------------------------------------------


@given(
    n_calls=st.integers(min_value=1, max_value=4),
    a_values=st.lists(st.integers(min_value=0, max_value=100), min_size=4, max_size=4),
    b_values=st.lists(st.integers(min_value=0, max_value=100), min_size=4, max_size=4),
)
@settings(max_examples=100)
@pytest.mark.asyncio
async def test_property_2_tool_call_parsing_and_invocation(
    n_calls: int,
    a_values: list[int],
    b_values: list[int],
) -> None:
    """Property 2：Tool Call 解析与调用。

    对于任意包含 tool_calls 的响应，Engine 必须正确解析并逐个调用工具，
    且 tool_call_id 对应一致。

    **Validates: Requirements 1.2**
    """
    config = _make_config()
    registry = _make_registry_with_tools()
    engine = AgentEngine(config, registry)

    # 构造 n_calls 个 tool_calls
    tc_list = []
    expected_results = []
    for i in range(n_calls):
        call_id = f"call_{i}"
        a, b = a_values[i], b_values[i]
        tc_list.append((call_id, "add_numbers", json.dumps({"a": a, "b": b})))
        expected_results.append((call_id, str(a + b)))

    tool_response = _make_tool_call_response(tc_list)
    text_response = _make_text_response("完成")

    engine._client.chat.completions.create = AsyncMock(
        side_effect=[tool_response, text_response]
    )

    result = await engine.chat("测试多工具调用")

    # 不变量 1：返回纯文本结果
    assert result == "完成"

    # 不变量 2：记忆中包含正确数量的 tool result 消息
    messages = engine.memory.get_messages()
    tool_msgs = [m for m in messages if m.get("role") == "tool"]
    assert len(tool_msgs) == n_calls

    # 不变量 3：每个 tool_call_id 都有对应的 tool result
    for call_id, expected_val in expected_results:
        matching = [m for m in tool_msgs if m.get("tool_call_id") == call_id]
        assert len(matching) == 1, f"tool_call_id {call_id} 应有且仅有一个对应结果"
        assert expected_val in matching[0]["content"]


# ---------------------------------------------------------------------------
# Property 3：纯文本终止循环
# **Validates: Requirements 1.3**
# ---------------------------------------------------------------------------


@given(
    reply_text=nonempty_text_st,
)
@settings(max_examples=100)
@pytest.mark.asyncio
async def test_property_3_pure_text_terminates_loop(reply_text: str) -> None:
    """Property 3：纯文本终止循环。

    对于任意不含 tool_calls 的响应，Engine 必须立即终止循环并返回文本。

    **Validates: Requirements 1.3**
    """
    config = _make_config()
    registry = _make_registry_with_tools()
    engine = AgentEngine(config, registry)

    mock_response = _make_text_response(reply_text)
    engine._client.chat.completions.create = AsyncMock(return_value=mock_response)

    result = await engine.chat("任意输入")

    # 不变量 1：返回的文本与 LLM 响应一致
    assert result == reply_text

    # 不变量 2：LLM 只被调用了一次（立即终止，无循环）
    assert engine._client.chat.completions.create.call_count == 1

    # 不变量 3：记忆中包含 user + assistant 消息
    messages = engine.memory.get_messages()
    roles = [m["role"] for m in messages]
    assert roles[0] == "system"
    assert "user" in roles
    assert "assistant" in roles
    # 不应有 tool 消息
    assert "tool" not in roles


# ---------------------------------------------------------------------------
# Property 4：迭代上限保护
# **Validates: Requirements 1.4**
# ---------------------------------------------------------------------------


@given(
    max_iter=st.integers(min_value=1, max_value=10),
)
@settings(max_examples=100)
@pytest.mark.asyncio
async def test_property_4_iteration_limit_protection(max_iter: int) -> None:
    """Property 4：迭代上限保护。

    当连续 N 轮均需要工具调用时，Engine 在第 N 轮后必须终止。

    **Validates: Requirements 1.4**
    """
    config = _make_config(max_iterations=max_iter)
    registry = _make_registry_with_tools()
    engine = AgentEngine(config, registry)

    # 构造无限 tool_call 响应（每轮都返回 tool_call，永不返回纯文本）
    infinite_tool_responses = [
        _make_tool_call_response(
            [(f"call_{i}", "add_numbers", json.dumps({"a": i, "b": i}))]
        )
        for i in range(max_iter + 5)  # 多准备几个
    ]

    engine._client.chat.completions.create = AsyncMock(
        side_effect=infinite_tool_responses
    )

    result = await engine.chat("无限循环测试")

    # 不变量 1：LLM 被调用的次数不超过 max_iter
    assert engine._client.chat.completions.create.call_count <= max_iter

    # 不变量 2：返回结果包含迭代上限提示
    assert "最大迭代次数" in result or str(max_iter) in result


# ---------------------------------------------------------------------------
# Property 5：工具异常反馈
# **Validates: Requirements 1.5**
# ---------------------------------------------------------------------------


@given(
    error_msg=st.text(
        alphabet=st.characters(whitelist_categories=("L", "N")),
        min_size=1,
        max_size=100,
    ),
)
@settings(max_examples=100)
@pytest.mark.asyncio
async def test_property_5_tool_exception_feedback(error_msg: str) -> None:
    """Property 5：工具异常反馈。

    任意工具异常必须被捕获并作为 tool message 反馈给 LLM，不直接向调用方抛出。

    **Validates: Requirements 1.5**
    """
    # 创建一个会抛出指定异常的工具
    def failing_tool() -> str:
        raise RuntimeError(error_msg)

    registry = ToolRegistry()
    registry.register_tools([
        ToolDef(
            name="custom_fail",
            description="自定义失败工具",
            input_schema={"type": "object", "properties": {}},
            func=failing_tool,
        ),
    ])

    config = _make_config(max_consecutive_failures=10)  # 高阈值避免熔断
    engine = AgentEngine(config, registry)

    # 第一轮：调用会失败的工具
    tool_response = _make_tool_call_response([("call_err", "custom_fail", "{}")])
    # 第二轮：LLM 返回纯文本
    text_response = _make_text_response("已处理错误")

    engine._client.chat.completions.create = AsyncMock(
        side_effect=[tool_response, text_response]
    )

    # 不变量 1：chat 不应抛出异常（异常被内部捕获）
    result = await engine.chat("测试异常反馈")

    # 不变量 2：返回正常文本
    assert result == "已处理错误"

    # 不变量 3：记忆中包含 tool result 消息，且内容包含错误信息
    messages = engine.memory.get_messages()
    tool_msgs = [m for m in messages if m.get("role") == "tool"]
    assert len(tool_msgs) >= 1
    # 错误信息被反馈到 tool message 中
    assert any("错误" in m["content"] or "error" in m["content"].lower() for m in tool_msgs)


# ---------------------------------------------------------------------------
# Property 6：连续失败熔断
# **Validates: Requirements 1.6**
# ---------------------------------------------------------------------------


@given(
    max_failures=st.integers(min_value=1, max_value=5),
)
@settings(max_examples=100)
@pytest.mark.asyncio
async def test_property_6_consecutive_failure_circuit_breaker(
    max_failures: int,
) -> None:
    """Property 6：连续失败熔断。

    连续 M 次工具失败后，Engine 必须终止并返回错误摘要。

    **Validates: Requirements 1.6**
    """
    registry = ToolRegistry()
    registry.register_tools([
        ToolDef(
            name="always_fail",
            description="总是失败",
            input_schema={"type": "object", "properties": {}},
            func=lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        ),
    ])

    config = _make_config(
        max_consecutive_failures=max_failures,
        max_iterations=max_failures + 5,  # 确保不会先触发迭代上限
    )
    engine = AgentEngine(config, registry)

    # 构造足够多的失败 tool_call 响应
    fail_responses = [
        _make_tool_call_response([(f"call_{i}", "always_fail", "{}")])
        for i in range(max_failures + 3)
    ]

    engine._client.chat.completions.create = AsyncMock(side_effect=fail_responses)

    result = await engine.chat("熔断测试")

    # 不变量 1：返回结果包含失败/熔断相关信息
    assert "失败" in result or "终止" in result or "错误" in result

    # 不变量 2：LLM 调用次数不超过 max_failures（在第 max_failures 轮熔断）
    assert engine._client.chat.completions.create.call_count <= max_failures


# ---------------------------------------------------------------------------
# Property 20：异步不阻塞
# **Validates: Requirements 1.10, 5.7**
# ---------------------------------------------------------------------------


@given(
    n_calls=st.integers(min_value=1, max_value=3),
)
@settings(max_examples=100)
@pytest.mark.asyncio
async def test_property_20_async_non_blocking(n_calls: int) -> None:
    """Property 20：异步不阻塞。

    并发请求场景下，阻塞工具执行不得阻塞主事件循环。
    验证 asyncio.to_thread 被用于工具执行。

    **Validates: Requirements 1.10, 5.7**
    """
    config = _make_config()
    registry = _make_registry_with_tools()
    engine = AgentEngine(config, registry)

    # 构造 n_calls 个 tool_calls 在单轮响应中
    tc_list = [
        (f"call_{i}", "add_numbers", json.dumps({"a": i, "b": i}))
        for i in range(n_calls)
    ]
    tool_response = _make_tool_call_response(tc_list)
    text_response = _make_text_response("完成")

    engine._client.chat.completions.create = AsyncMock(
        side_effect=[tool_response, text_response]
    )

    with patch(
        "excelmanus.engine.asyncio.to_thread", new_callable=AsyncMock
    ) as mock_to_thread:
        # 模拟 to_thread 返回工具结果
        mock_to_thread.side_effect = [i + i for i in range(n_calls)]

        result = await engine.chat("异步测试")

        # 不变量 1：asyncio.to_thread 被调用了 n_calls 次
        assert mock_to_thread.call_count == n_calls

        # 不变量 2：每次调用的第一个参数是 registry.call_tool
        for call in mock_to_thread.call_args_list:
            assert call[0][0] == registry.call_tool

        # 不变量 3：每次调用传入了正确的工具名称
        called_tool_names = [call[0][1] for call in mock_to_thread.call_args_list]
        assert all(name == "add_numbers" for name in called_tool_names)


class TestApprovalFlow:
    """Accept 门禁主流程测试。"""

    def _make_registry_with_write_tool(self, workspace: Path) -> ToolRegistry:
        registry = ToolRegistry()

        def write_text_file(
            file_path: str,
            content: str,
            overwrite: bool = True,
            encoding: str = "utf-8",
        ) -> str:
            target = workspace / file_path
            if target.exists() and not overwrite:
                return json.dumps({"status": "error", "error": "exists"}, ensure_ascii=False)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding=encoding)
            return json.dumps({"status": "success", "file": file_path}, ensure_ascii=False)

        registry.register_tools([
            ToolDef(
                name="write_text_file",
                description="写文件",
                input_schema={
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string"},
                        "content": {"type": "string"},
                        "overwrite": {"type": "boolean"},
                        "encoding": {"type": "string"},
                    },
                    "required": ["file_path", "content"],
                },
                func=write_text_file,
            ),
        ])
        return registry

    @pytest.mark.asyncio
    async def test_high_risk_tool_requires_accept(self, tmp_path: Path) -> None:
        config = _make_config(workspace_root=str(tmp_path))
        registry = self._make_registry_with_write_tool(tmp_path)
        engine = AgentEngine(config, registry)

        tool_response = _make_tool_call_response([
            ("call_1", "write_text_file", json.dumps({"file_path": "a.txt", "content": "hello"}))
        ])
        engine._client.chat.completions.create = AsyncMock(side_effect=[tool_response])

        first_reply = await engine.chat("写入文件")
        assert "待确认" in first_reply
        assert "accept" in first_reply
        assert not (tmp_path / "a.txt").exists()
        assert engine._approval.pending is not None
        approval_id = engine._approval.pending.approval_id

        blocked = await engine.chat("继续执行")
        assert "存在待确认操作" in blocked

        accept_reply = await engine.chat(f"/accept {approval_id}")
        assert "已执行待确认操作" in accept_reply
        assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "hello"
        assert (tmp_path / "outputs" / "approvals" / approval_id / "manifest.json").exists()

    @pytest.mark.asyncio
    async def test_reject_pending(self, tmp_path: Path) -> None:
        config = _make_config(workspace_root=str(tmp_path))
        registry = self._make_registry_with_write_tool(tmp_path)
        engine = AgentEngine(config, registry)

        tool_response = _make_tool_call_response([
            ("call_1", "write_text_file", json.dumps({"file_path": "b.txt", "content": "world"}))
        ])
        engine._client.chat.completions.create = AsyncMock(side_effect=[tool_response])
        await engine.chat("写文件")
        assert engine._approval.pending is not None
        approval_id = engine._approval.pending.approval_id

        reject_reply = await engine.chat(f"/reject {approval_id}")
        assert "已拒绝" in reject_reply
        assert engine._approval.pending is None
        assert not (tmp_path / "b.txt").exists()

    @pytest.mark.asyncio
    async def test_undo_after_accept(self, tmp_path: Path) -> None:
        config = _make_config(workspace_root=str(tmp_path))
        registry = self._make_registry_with_write_tool(tmp_path)
        engine = AgentEngine(config, registry)

        tool_response = _make_tool_call_response([
            ("call_1", "write_text_file", json.dumps({"file_path": "c.txt", "content": "undo"}))
        ])
        engine._client.chat.completions.create = AsyncMock(side_effect=[tool_response])
        await engine.chat("写文件")
        assert engine._approval.pending is not None
        approval_id = engine._approval.pending.approval_id
        await engine.chat(f"/accept {approval_id}")
        assert (tmp_path / "c.txt").exists()

        undo_reply = await engine.chat(f"/undo {approval_id}")
        assert "已回滚" in undo_reply
        assert not (tmp_path / "c.txt").exists()

    @pytest.mark.asyncio
    async def test_fullaccess_bypass_accept(self, tmp_path: Path) -> None:
        config = _make_config(workspace_root=str(tmp_path))
        registry = self._make_registry_with_write_tool(tmp_path)
        engine = AgentEngine(config, registry)

        on_reply = await engine.chat("/fullAccess on")
        assert "已开启" in on_reply

        tool_response = _make_tool_call_response([
            ("call_1", "write_text_file", json.dumps({"file_path": "d.txt", "content": "full"}))
        ])
        text_response = _make_text_response("完成")
        engine._client.chat.completions.create = AsyncMock(
            side_effect=[tool_response, text_response]
        )

        reply = await engine.chat("直接写")
        assert reply == "完成"
        assert engine._approval.pending is None
        assert (tmp_path / "d.txt").read_text(encoding="utf-8") == "full"
