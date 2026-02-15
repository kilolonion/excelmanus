"""AgentEngine 单元测试：覆盖 Tool Calling 循环核心逻辑。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from excelmanus.config import ExcelManusConfig, ModelProfile
from excelmanus.engine import AgentEngine, ChatResult, DelegateSubagentOutcome, ToolCallResult
from excelmanus.events import EventType
from excelmanus.hooks import HookAgentAction, HookDecision, HookEvent, HookResult
from excelmanus.mcp.manager import add_tool_prefix
from excelmanus.memory import TokenCounter
from excelmanus.plan_mode import PendingPlanState, PlanDraft
from excelmanus.skillpacks import SkillMatchResult, Skillpack
from excelmanus.subagent import SubagentConfig, SubagentResult
from excelmanus.task_list import TaskStatus
from excelmanus.tools import ToolRegistry, task_tools
from excelmanus.tools.registry import ToolDef
from excelmanus.window_perception import AdvisorContext, PerceptionBudget, WindowState, WindowType


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
        assert isinstance(result, ChatResult)
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
        assert "当前 subagent 状态" in result
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

    @pytest.mark.asyncio
    async def test_list_command_returns_catalog(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)

        result = await engine.chat("/subagent list")
        assert "explorer" in result
        assert "analyst" in result

    @pytest.mark.asyncio
    async def test_run_command_with_agent_routes_to_delegate(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        engine._delegate_to_subagent = AsyncMock(
            return_value=DelegateSubagentOutcome(
                reply="执行完成",
                success=True,
                picked_agent="explorer",
                task_text="分析这个文件",
                normalized_paths=[],
                subagent_result=None,
            )
        )

        result = await engine.chat("/subagent run explorer -- 分析这个文件")
        assert result == "执行完成"
        engine._delegate_to_subagent.assert_awaited_once_with(
            task="分析这个文件",
            agent_name="explorer",
            on_event=None,
        )

    @pytest.mark.asyncio
    async def test_run_command_without_agent_routes_to_delegate(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        engine._delegate_to_subagent = AsyncMock(
            return_value=DelegateSubagentOutcome(
                reply="执行完成",
                success=True,
                picked_agent="explorer",
                task_text="分析这个文件",
                normalized_paths=[],
                subagent_result=None,
            )
        )

        result = await engine.chat("/subagent run -- 分析这个文件")
        assert result == "执行完成"
        engine._delegate_to_subagent.assert_awaited_once_with(
            task="分析这个文件",
            agent_name=None,
            on_event=None,
        )


class TestModelSwitchConsistency:
    """模型切换与路由模型一致性测试。"""

    def test_switch_model_syncs_router_when_router_model_not_configured(self) -> None:
        config = _make_config(
            model="main-a",
            models=(
                ModelProfile(
                    name="alt",
                    model="main-b",
                    api_key="alt-key",
                    base_url="https://alt.example.com/v1",
                    description="备选模型",
                ),
            ),
        )
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)

        assert engine._router_follow_active_model is True
        assert engine._router_model == "main-a"
        assert engine._router_client is engine._client

        msg = engine.switch_model("alt")
        assert "已切换到模型" in msg
        assert engine._active_model == "main-b"
        assert engine._router_model == "main-b"
        assert engine._router_client is engine._client

    def test_switch_model_keeps_router_when_router_model_configured(self) -> None:
        config = _make_config(
            model="main-a",
            router_model="router-fixed",
            router_api_key="router-key",
            router_base_url="https://router.example.com/v1",
            models=(
                ModelProfile(
                    name="alt",
                    model="main-b",
                    api_key="alt-key",
                    base_url="https://alt.example.com/v1",
                    description="备选模型",
                ),
            ),
        )
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        old_router_client = engine._router_client

        assert engine._router_follow_active_model is False
        assert engine._router_model == "router-fixed"

        engine.switch_model("alt")
        assert engine._active_model == "main-b"
        assert engine._router_model == "router-fixed"
        assert engine._router_client is old_router_client
        assert engine._router_client is not engine._client

    @pytest.mark.asyncio
    async def test_window_perception_advisor_follows_router_model_after_switch(self) -> None:
        config = _make_config(
            model="main-a",
            models=(
                ModelProfile(
                    name="alt",
                    model="main-b",
                    api_key="alt-key",
                    base_url="https://alt.example.com/v1",
                    description="备选模型",
                ),
            ),
            window_perception_advisor_mode="hybrid",
        )
        engine = AgentEngine(config, _make_registry_with_tools())
        engine.switch_model("alt")
        engine._router_client.chat.completions.create = AsyncMock(
            return_value=_make_text_response('{"task_type":"GENERAL_BROWSE","advices":[]}')
        )

        _ = await engine._run_window_perception_advisor_async(
            windows=[WindowState(id="w1", type=WindowType.SHEET, title="A")],
            active_window_id="w1",
            budget=PerceptionBudget(),
            context=AdvisorContext(turn_number=1, task_type="GENERAL_BROWSE"),
        )

        _, kwargs = engine._router_client.chat.completions.create.call_args
        assert kwargs["model"] == "main-b"

    @pytest.mark.asyncio
    async def test_window_perception_advisor_retries_once_on_transient_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        class _TransientError(Exception):
            def __init__(self) -> None:
                super().__init__("rate limit")
                self.status_code = 429
                self.response = SimpleNamespace(headers={"Retry-After": "0.1"})

        config = _make_config(
            window_perception_advisor_mode="hybrid",
            window_perception_advisor_timeout_ms=20_000,
        )
        engine = AgentEngine(config, _make_registry_with_tools())
        mocked_create = AsyncMock(
            side_effect=[
                _TransientError(),
                _make_text_response('{"task_type":"GENERAL_BROWSE","advices":[]}'),
            ]
        )
        engine._router_client.chat.completions.create = mocked_create
        mocked_sleep = AsyncMock(return_value=None)
        monkeypatch.setattr("excelmanus.engine.asyncio.sleep", mocked_sleep)

        plan = await engine._run_window_perception_advisor_async(
            windows=[WindowState(id="w1", type=WindowType.SHEET, title="A")],
            active_window_id="w1",
            budget=PerceptionBudget(),
            context=AdvisorContext(turn_number=1, task_type="GENERAL_BROWSE"),
        )

        assert plan is not None
        assert mocked_create.await_count == 2
        mocked_sleep.assert_awaited_once()

    def test_is_transient_window_advisor_exception_detects_nested_connect_error(self) -> None:
        wrapped = RuntimeError("Gemini API 请求失败: ")
        wrapped.__cause__ = httpx.ConnectError("")
        assert AgentEngine._is_transient_window_advisor_exception(wrapped) is True

    def test_extract_retry_after_seconds_from_nested_exception(self) -> None:
        class _RateLimitError(Exception):
            def __init__(self) -> None:
                super().__init__("rate limited")
                self.response = SimpleNamespace(headers={"Retry-After": "0.6"})

        wrapped = RuntimeError("Gemini API 请求失败: ")
        wrapped.__cause__ = _RateLimitError()

        retry_after = AgentEngine._extract_retry_after_seconds(wrapped)
        assert retry_after == pytest.approx(0.6)

    @pytest.mark.asyncio
    async def test_window_perception_advisor_retries_on_wrapped_connect_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        wrapped = RuntimeError("Gemini API 请求失败: ")
        wrapped.__cause__ = httpx.ConnectError("")

        config = _make_config(
            window_perception_advisor_mode="hybrid",
            window_perception_advisor_timeout_ms=20_000,
        )
        engine = AgentEngine(config, _make_registry_with_tools())
        mocked_create = AsyncMock(
            side_effect=[
                wrapped,
                _make_text_response('{"task_type":"GENERAL_BROWSE","advices":[]}'),
            ]
        )
        engine._router_client.chat.completions.create = mocked_create
        mocked_sleep = AsyncMock(return_value=None)
        monkeypatch.setattr("excelmanus.engine.asyncio.sleep", mocked_sleep)

        plan = await engine._run_window_perception_advisor_async(
            windows=[WindowState(id="w1", type=WindowType.SHEET, title="A")],
            active_window_id="w1",
            budget=PerceptionBudget(),
            context=AdvisorContext(turn_number=1, task_type="GENERAL_BROWSE"),
        )

        assert plan is not None
        assert mocked_create.await_count == 2
        mocked_sleep.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_window_perception_advisor_timeout_does_not_retry(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def _slow_response(**_kwargs):
            await asyncio.sleep(0.2)
            return _make_text_response('{"task_type":"GENERAL_BROWSE","advices":[]}')

        config = _make_config(
            window_perception_advisor_mode="hybrid",
            window_perception_advisor_timeout_ms=20,
        )
        engine = AgentEngine(config, _make_registry_with_tools())
        mocked_create = AsyncMock(side_effect=_slow_response)
        engine._router_client.chat.completions.create = mocked_create

        def _unexpected_retry_delay(_exc: Exception) -> float:
            raise AssertionError("不应进入重试分支")

        monkeypatch.setattr(
            AgentEngine,
            "_window_advisor_retry_delay_seconds",
            staticmethod(_unexpected_retry_delay),
        )

        plan = await engine._run_window_perception_advisor_async(
            windows=[WindowState(id="w1", type=WindowType.SHEET, title="A")],
            active_window_id="w1",
            budget=PerceptionBudget(),
            context=AdvisorContext(turn_number=1, task_type="GENERAL_BROWSE"),
        )

        assert plan is None
        assert mocked_create.await_count == 1


class TestSystemMessageMode:
    """system_message_mode 行为测试。"""

    def test_auto_mode_defaults_to_replace(self) -> None:
        config = _make_config(system_message_mode="auto")
        engine = AgentEngine(config, _make_registry_with_tools())
        assert engine._effective_system_mode() == "replace"

    def test_build_system_prompts_replace_mode_splits_system_messages(self) -> None:
        config = _make_config(system_message_mode="replace")
        engine = AgentEngine(config, _make_registry_with_tools())
        prompts = engine._build_system_prompts(["[Skillpack] data_basic\n描述：测试"])
        assert len(prompts) == 2
        assert "[Skillpack] data_basic" in prompts[1]

    def test_build_system_prompts_merge_mode_merges_into_single_message(self) -> None:
        config = _make_config(system_message_mode="merge")
        engine = AgentEngine(config, _make_registry_with_tools())
        prompts = engine._build_system_prompts(["[Skillpack] data_basic\n描述：测试"])
        assert len(prompts) == 1
        assert "[Skillpack] data_basic" in prompts[0]

    @pytest.mark.asyncio
    async def test_auto_mode_fallback_merges_messages_after_provider_compat_error(self) -> None:
        config = _make_config(system_message_mode="auto")
        engine = AgentEngine(config, _make_registry_with_tools())
        mocked_create = AsyncMock(
            side_effect=[
                RuntimeError("at most one system message is supported"),
                _make_text_response("ok"),
            ]
        )
        engine._client.chat.completions.create = mocked_create

        response = await engine._create_chat_completion_with_system_fallback(
            {
                "model": config.model,
                "messages": [
                    {"role": "system", "content": "S1"},
                    {"role": "system", "content": "S2"},
                    {"role": "user", "content": "hello"},
                ],
            }
        )

        assert response.choices[0].message.content == "ok"
        assert mocked_create.call_count == 2
        retry_messages = mocked_create.call_args_list[1].kwargs["messages"]
        assert retry_messages[0]["role"] == "system"
        assert "S1" in retry_messages[0]["content"]
        assert "S2" in retry_messages[0]["content"]
        assert sum(1 for msg in retry_messages if msg.get("role") == "system") == 1
        assert engine._system_mode_fallback == "merge"


class TestContextBudgetAndHardCap:
    """上下文预算与工具结果全局硬截断测试。"""

    @pytest.mark.asyncio
    async def test_tool_loop_messages_fit_max_context_budget(self) -> None:
        config = _make_config(max_context_tokens=3000)
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        engine.memory.add_user_message("测试上下文预算")

        route_result = SkillMatchResult(
            skills_used=[],
            tool_scope=["add_numbers"],
            route_mode="fallback",
            system_contexts=["X" * 6000],
        )
        mocked_create = AsyncMock(return_value=_make_text_response("ok"))
        engine._client.chat.completions.create = mocked_create

        result = await engine._tool_calling_loop(route_result, on_event=None)
        assert result.reply == "ok"
        assert mocked_create.call_count == 1

        _, kwargs = mocked_create.call_args
        sent_messages = kwargs["messages"]
        total_tokens = sum(TokenCounter.count_message(m) for m in sent_messages)
        assert total_tokens <= int(config.max_context_tokens * 0.9)

    @pytest.mark.asyncio
    async def test_tool_loop_returns_actionable_error_when_system_prompt_itself_over_budget(self) -> None:
        config = _make_config(max_context_tokens=20)
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        engine.memory.add_user_message("测试极小上下文")
        mocked_create = AsyncMock(return_value=_make_text_response("不应调用"))
        engine._client.chat.completions.create = mocked_create

        route_result = SkillMatchResult(
            skills_used=[],
            tool_scope=[],
            route_mode="fallback",
            system_contexts=[],
        )
        result = await engine._tool_calling_loop(route_result, on_event=None)

        assert "系统上下文过长" in result.reply
        mocked_create.assert_not_called()

    @pytest.mark.asyncio
    async def test_execute_tool_call_applies_global_hard_cap(self) -> None:
        def long_tool() -> str:
            return "A" * 500

        registry = ToolRegistry()
        registry.register_tools([
            ToolDef(
                name="long_tool",
                description="长文本工具",
                input_schema={"type": "object", "properties": {}},
                func=long_tool,
                max_result_chars=0,
            ),
        ])

        config = _make_config(tool_result_hard_cap_chars=80)
        engine = AgentEngine(config, registry)
        tc = SimpleNamespace(
            id="call_long",
            function=SimpleNamespace(name="long_tool", arguments="{}"),
        )

        result = await engine._execute_tool_call(
            tc,
            tool_scope=["long_tool"],
            on_event=None,
            iteration=1,
            route_result=None,
        )

        assert result.success is True
        assert "结果已全局截断" in result.result
        assert "上限: 80 字符" in result.result

    @pytest.mark.asyncio
    async def test_window_perception_enriches_json_tool_result(self) -> None:
        def read_excel() -> str:
            return json.dumps(
                {
                    "file": "sales.xlsx",
                    "shape": {"rows": 20, "columns": 5},
                    "preview": [{"产品": "A", "金额": 100}],
                },
                ensure_ascii=False,
            )

        registry = ToolRegistry()
        registry.register_tools([
            ToolDef(
                name="read_excel",
                description="读取",
                input_schema={"type": "object", "properties": {}},
                func=read_excel,
                max_result_chars=0,
            ),
        ])
        config = _make_config()
        engine = AgentEngine(config, registry)
        tc = SimpleNamespace(
            id="call_read",
            function=SimpleNamespace(name="read_excel", arguments="{}"),
        )
        result = await engine._execute_tool_call(
            tc=tc,
            tool_scope=["read_excel"],
            on_event=None,
            iteration=1,
            route_result=None,
        )

        assert "───────────── 环境感知 ─────────────" in result.result
        assert "📊 文件: sales.xlsx" in result.result
        assert "_environment_perception" not in result.result
        json_part, _sep, _tail = result.result.partition("\n\n───────────── 环境感知 ─────────────")
        payload = json.loads(json_part)
        assert payload["file"] == "sales.xlsx"

    @pytest.mark.asyncio
    async def test_window_perception_block_contains_scroll_status_and_style_details(self) -> None:
        def read_excel() -> str:
            return json.dumps(
                {
                    "file": "sales.xlsx",
                    "sheet": "Q1",
                    "shape": {"rows": 5000, "columns": 30},
                    "preview": [
                        {"产品": "A", "销售额": 12500, "达成率": "106.6%"},
                        {"产品": "B", "销售额": 8300, "达成率": "90.4%"},
                    ],
                    "styles": {
                        "style_classes": {"s0": {"font": {"bold": True}}},
                        "merged_ranges": ["F1:H1"],
                    },
                    "conditional_formatting": [
                        {"range": "D2:D7", "type": "cellIs", "operator": "greaterThan"},
                    ],
                    "column_widths": {"A": 12.0, "B": 15.0},
                    "row_heights": {"1": 24.0, "2": 18.0},
                },
                ensure_ascii=False,
            )

        registry = ToolRegistry()
        registry.register_tools([
            ToolDef(
                name="read_excel",
                description="读取",
                input_schema={"type": "object", "properties": {}},
                func=read_excel,
                max_result_chars=0,
            ),
        ])
        engine = AgentEngine(_make_config(), registry)
        tc = SimpleNamespace(
            id="call_read",
            function=SimpleNamespace(name="read_excel", arguments="{}"),
        )
        result = await engine._execute_tool_call(
            tc=tc,
            tool_scope=["read_excel"],
            on_event=None,
            iteration=1,
            route_result=None,
        )

        assert "滚动条位置:" in result.result
        assert "状态栏: SUM=" in result.result
        assert "列宽: A=12, B=15" in result.result
        assert "行高: 1=24, 2=18" in result.result
        assert "合并单元格: F1:H1" in result.result
        assert "条件格式效果: D2:D7: 条件着色（cellIs/greaterThan）" in result.result

    @pytest.mark.asyncio
    async def test_window_perception_can_be_disabled(self) -> None:
        def read_excel() -> str:
            return json.dumps(
                {"file": "sales.xlsx", "shape": {"rows": 20, "columns": 5}},
                ensure_ascii=False,
            )

        registry = ToolRegistry()
        registry.register_tools([
            ToolDef(
                name="read_excel",
                description="读取",
                input_schema={"type": "object", "properties": {}},
                func=read_excel,
                max_result_chars=0,
            ),
        ])
        config = _make_config(window_perception_enabled=False)
        engine = AgentEngine(config, registry)
        tc = SimpleNamespace(
            id="call_read",
            function=SimpleNamespace(name="read_excel", arguments="{}"),
        )
        result = await engine._execute_tool_call(
            tc=tc,
            tool_scope=["read_excel"],
            on_event=None,
            iteration=1,
            route_result=None,
        )

        payload = json.loads(result.result)
        assert payload["file"] == "sales.xlsx"
        assert "环境感知" not in result.result
        assert engine._effective_window_return_mode() == "enriched"

    @pytest.mark.asyncio
    async def test_window_perception_notice_is_injected_into_system_prompts(self) -> None:
        def read_excel(file_path: str, sheet_name: str) -> str:
            return json.dumps(
                {
                    "file": file_path,
                    "sheet": sheet_name,
                    "shape": {"rows": 200, "columns": 12},
                    "preview": [{"产品": "A", "金额": 100}],
                },
                ensure_ascii=False,
            )

        registry = ToolRegistry()
        registry.register_tools([
            ToolDef(
                name="read_excel",
                description="读取",
                input_schema={
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string"},
                        "sheet_name": {"type": "string"},
                    },
                    "required": ["file_path", "sheet_name"],
                },
                func=read_excel,
                max_result_chars=0,
            ),
        ])
        config = _make_config(system_message_mode="replace")
        engine = AgentEngine(config, registry)

        tc = SimpleNamespace(
            id="call_read",
            function=SimpleNamespace(
                name="read_excel",
                arguments=json.dumps({"file_path": "sales.xlsx", "sheet_name": "Q1"}),
            ),
        )
        await engine._execute_tool_call(
            tc=tc,
            tool_scope=["read_excel"],
            on_event=None,
            iteration=1,
            route_result=None,
        )

        prompts, error = engine._prepare_system_prompts_for_request([])
        assert error is None
        merged_prompt = "\n\n".join(prompts)
        assert "## 窗口感知上下文" in merged_prompt
        assert "sales.xlsx" in merged_prompt
        assert "Q1" in merged_prompt

    @pytest.mark.asyncio
    async def test_window_perception_anchored_returns_confirmation(self) -> None:
        def read_excel() -> str:
            return json.dumps(
                {
                    "file": "sales.xlsx",
                    "sheet": "Q1",
                    "shape": {"rows": 20, "columns": 5},
                    "columns": ["日期", "产品", "数量", "单价", "金额"],
                    "preview": [{"日期": "2024-01-01", "产品": "A", "数量": 1, "单价": 100, "金额": 100}],
                },
                ensure_ascii=False,
            )

        registry = ToolRegistry()
        registry.register_tools([
            ToolDef(
                name="read_excel",
                description="读取",
                input_schema={"type": "object", "properties": {}},
                func=read_excel,
                max_result_chars=0,
            ),
        ])
        config = _make_config(window_return_mode="anchored")
        engine = AgentEngine(config, registry)
        tc = SimpleNamespace(
            id="call_read",
            function=SimpleNamespace(name="read_excel", arguments="{}"),
        )
        result = await engine._execute_tool_call(
            tc=tc,
            tool_scope=["read_excel"],
            on_event=None,
            iteration=1,
            route_result=None,
        )

        assert result.result.startswith("✅ [")
        assert "read_excel: A1:J25" in result.result
        assert "意图: aggregate" in result.result
        assert "数据已融入窗口，请优先引用窗口内容。" in result.result
        assert "环境感知" not in result.result

    @pytest.mark.asyncio
    async def test_window_perception_unified_returns_compact_confirmation(self) -> None:
        def read_excel(file_path: str, sheet_name: str, range: str) -> str:
            return json.dumps(
                {
                    "file": file_path,
                    "sheet": sheet_name,
                    "shape": {"rows": 20, "columns": 5},
                    "columns": ["日期", "产品", "数量", "单价", "金额"],
                    "preview": [{"日期": "2024-01-01", "产品": "A", "数量": 1, "单价": 100, "金额": 100}],
                },
                ensure_ascii=False,
            )

        registry = ToolRegistry()
        registry.register_tools([
            ToolDef(
                name="read_excel",
                description="读取",
                input_schema={
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string"},
                        "sheet_name": {"type": "string"},
                        "range": {"type": "string"},
                    },
                    "required": ["file_path", "sheet_name", "range"],
                },
                func=read_excel,
                max_result_chars=0,
            ),
        ])
        config = _make_config(window_return_mode="unified")
        engine = AgentEngine(config, registry)
        tc = SimpleNamespace(
            id="call_read",
            function=SimpleNamespace(
                name="read_excel",
                arguments=json.dumps(
                    {"file_path": "sales.xlsx", "sheet_name": "Q1", "range": "A1:E10"},
                    ensure_ascii=False,
                ),
            ),
        )
        result = await engine._execute_tool_call(
            tc=tc,
            tool_scope=["read_excel"],
            on_event=None,
            iteration=1,
            route_result=None,
        )

        assert result.result.startswith("✅ [")
        assert "read_excel: A1:E10" in result.result
        assert "| 意图=aggregate" in result.result
        assert "首行预览" not in result.result
        assert "环境感知" not in result.result

    @pytest.mark.asyncio
    async def test_window_perception_adaptive_gpt_defaults_to_unified(self) -> None:
        def read_excel(file_path: str, sheet_name: str, range: str) -> str:
            return json.dumps(
                {
                    "file": file_path,
                    "sheet": sheet_name,
                    "shape": {"rows": 20, "columns": 5},
                    "columns": ["日期", "产品", "数量", "单价", "金额"],
                    "preview": [{"日期": "2024-01-01", "产品": "A", "数量": 1, "单价": 100, "金额": 100}],
                },
                ensure_ascii=False,
            )

        registry = ToolRegistry()
        registry.register_tools([
            ToolDef(
                name="read_excel",
                description="读取",
                input_schema={
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string"},
                        "sheet_name": {"type": "string"},
                        "range": {"type": "string"},
                    },
                    "required": ["file_path", "sheet_name", "range"],
                },
                func=read_excel,
                max_result_chars=0,
            ),
        ])
        config = _make_config(
            model="gpt-5.3",
            window_return_mode="adaptive",
        )
        engine = AgentEngine(config, registry)
        tc = SimpleNamespace(
            id="call_read",
            function=SimpleNamespace(
                name="read_excel",
                arguments=json.dumps(
                    {"file_path": "sales.xlsx", "sheet_name": "Q1", "range": "A1:E10"},
                    ensure_ascii=False,
                ),
            ),
        )
        result = await engine._execute_tool_call(
            tc=tc,
            tool_scope=["read_excel"],
            on_event=None,
            iteration=1,
            route_result=None,
        )

        assert result.result.startswith("✅ [")
        assert "read_excel: A1:E10" in result.result
        assert "| 意图=aggregate" in result.result
        assert "首行预览" not in result.result
        assert "环境感知" not in result.result
        assert engine._effective_window_return_mode() == "unified"

    @pytest.mark.asyncio
    async def test_window_perception_adaptive_repeat_tripwire_downgrades_to_anchored(self) -> None:
        def read_excel(file_path: str, sheet_name: str, range: str) -> str:
            return json.dumps(
                {
                    "file": file_path,
                    "sheet": sheet_name,
                    "shape": {"rows": 20, "columns": 5},
                    "columns": ["日期", "产品", "数量", "单价", "金额"],
                    "preview": [{"日期": "2024-01-01", "产品": "A", "数量": 1, "单价": 100, "金额": 100}],
                },
                ensure_ascii=False,
            )

        registry = ToolRegistry()
        registry.register_tools([
            ToolDef(
                name="read_excel",
                description="读取",
                input_schema={
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string"},
                        "sheet_name": {"type": "string"},
                        "range": {"type": "string"},
                    },
                    "required": ["file_path", "sheet_name", "range"],
                },
                func=read_excel,
                max_result_chars=0,
            ),
        ])
        config = _make_config(
            model="gpt-5.3",
            window_return_mode="adaptive",
        )
        engine = AgentEngine(config, registry)
        tc = SimpleNamespace(
            id="call_read",
            function=SimpleNamespace(
                name="read_excel",
                arguments=json.dumps(
                    {"file_path": "sales.xlsx", "sheet_name": "Q1", "range": "A1:E10"},
                    ensure_ascii=False,
                ),
            ),
        )

        first = await engine._execute_tool_call(
            tc=tc,
            tool_scope=["read_excel"],
            on_event=None,
            iteration=1,
            route_result=None,
        )
        second = await engine._execute_tool_call(
            tc=tc,
            tool_scope=["read_excel"],
            on_event=None,
            iteration=2,
            route_result=None,
        )
        third = await engine._execute_tool_call(
            tc=tc,
            tool_scope=["read_excel"],
            on_event=None,
            iteration=3,
            route_result=None,
        )

        assert "首行预览" not in first.result
        assert "提示=当前意图[aggregate]下此数据已在窗口" in second.result
        assert "意图: aggregate" in third.result
        assert "提示: 当前意图[aggregate]下此数据已在窗口" in third.result
        assert "───────────── 环境感知 ─────────────" not in third.result
        assert engine._effective_window_return_mode() == "anchored"

    @pytest.mark.asyncio
    async def test_window_perception_adaptive_model_switch_keeps_downgraded_state(self) -> None:
        def read_excel(file_path: str, sheet_name: str, range: str) -> str:
            return json.dumps(
                {
                    "file": file_path,
                    "sheet": sheet_name,
                    "shape": {"rows": 20, "columns": 5},
                    "columns": ["日期", "产品", "数量", "单价", "金额"],
                    "preview": [{"日期": "2024-01-01", "产品": "A", "数量": 1, "单价": 100, "金额": 100}],
                },
                ensure_ascii=False,
            )

        registry = ToolRegistry()
        registry.register_tools([
            ToolDef(
                name="read_excel",
                description="读取",
                input_schema={
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string"},
                        "sheet_name": {"type": "string"},
                        "range": {"type": "string"},
                    },
                    "required": ["file_path", "sheet_name", "range"],
                },
                func=read_excel,
                max_result_chars=0,
            ),
        ])
        config = _make_config(
            model="gpt-5.3",
            window_return_mode="adaptive",
            models=(
                ModelProfile(
                    name="deepseek",
                    model="deepseek-chat",
                    api_key="test-key-2",
                    base_url="https://deepseek.example.com/v1",
                    description="切换模型",
                ),
            ),
        )
        engine = AgentEngine(config, registry)
        tc = SimpleNamespace(
            id="call_read",
            function=SimpleNamespace(
                name="read_excel",
                arguments=json.dumps(
                    {"file_path": "sales.xlsx", "sheet_name": "Q1", "range": "A1:E10"},
                    ensure_ascii=False,
                ),
            ),
        )
        await engine._execute_tool_call(
            tc=tc,
            tool_scope=["read_excel"],
            on_event=None,
            iteration=1,
            route_result=None,
        )
        await engine._execute_tool_call(
            tc=tc,
            tool_scope=["read_excel"],
            on_event=None,
            iteration=2,
            route_result=None,
        )
        await engine._execute_tool_call(
            tc=tc,
            tool_scope=["read_excel"],
            on_event=None,
            iteration=3,
            route_result=None,
        )
        assert engine._effective_window_return_mode() == "anchored"

        switch_message = engine.switch_model("deepseek")
        assert "已切换到模型" in switch_message
        assert engine._effective_window_return_mode() == "anchored"

    @pytest.mark.asyncio
    async def test_window_perception_adaptive_ingest_failures_trigger_downgrade(self) -> None:
        def read_excel(file_path: str, sheet_name: str, range: str) -> str:
            return json.dumps(
                {
                    "file": file_path,
                    "sheet": sheet_name,
                    "shape": {"rows": 20, "columns": 5},
                    "columns": ["日期", "产品", "数量", "单价", "金额"],
                    "preview": [{"日期": "2024-01-01", "产品": "A", "数量": 1, "单价": 100, "金额": 100}],
                },
                ensure_ascii=False,
            )

        registry = ToolRegistry()
        registry.register_tools([
            ToolDef(
                name="read_excel",
                description="读取",
                input_schema={
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string"},
                        "sheet_name": {"type": "string"},
                        "range": {"type": "string"},
                    },
                    "required": ["file_path", "sheet_name", "range"],
                },
                func=read_excel,
                max_result_chars=0,
            ),
        ])
        config = _make_config(model="gpt-5.3", window_return_mode="adaptive")
        engine = AgentEngine(config, registry)
        tc = SimpleNamespace(
            id="call_read",
            function=SimpleNamespace(
                name="read_excel",
                arguments=json.dumps(
                    {"file_path": "sales.xlsx", "sheet_name": "Q1", "range": "A1:E10"},
                    ensure_ascii=False,
                ),
            ),
        )

        original_apply = engine._window_perception._apply_ingest

        def _raise_ingest(*_args, **_kwargs):
            raise RuntimeError("ingest boom")

        engine._window_perception._apply_ingest = _raise_ingest  # type: ignore[assignment]
        try:
            await engine._execute_tool_call(
                tc=tc,
                tool_scope=["read_excel"],
                on_event=None,
                iteration=1,
                route_result=None,
            )
            await engine._execute_tool_call(
                tc=tc,
                tool_scope=["read_excel"],
                on_event=None,
                iteration=2,
                route_result=None,
            )
        finally:
            engine._window_perception._apply_ingest = original_apply  # type: ignore[assignment]

        assert engine._effective_window_return_mode() == "anchored"

    @pytest.mark.asyncio
    async def test_window_perception_unified_repeat_and_fallback_to_enriched(self) -> None:
        def read_excel(file_path: str, sheet_name: str, range: str) -> str:
            return json.dumps(
                {
                    "file": file_path,
                    "sheet": sheet_name,
                    "shape": {"rows": 20, "columns": 5},
                    "columns": ["日期", "产品", "数量", "单价", "金额"],
                    "preview": [{"日期": "2024-01-01", "产品": "A", "数量": 1, "单价": 100, "金额": 100}],
                },
                ensure_ascii=False,
            )

        def write_cells() -> str:
            return json.dumps({"status": "success"}, ensure_ascii=False)

        registry = ToolRegistry()
        registry.register_tools([
            ToolDef(
                name="read_excel",
                description="读取",
                input_schema={
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string"},
                        "sheet_name": {"type": "string"},
                        "range": {"type": "string"},
                    },
                    "required": ["file_path", "sheet_name", "range"],
                },
                func=read_excel,
                max_result_chars=0,
            ),
            ToolDef(
                name="write_cells",
                description="写入",
                input_schema={"type": "object", "properties": {}},
                func=write_cells,
                max_result_chars=0,
            ),
        ])
        config = _make_config(window_return_mode="unified")
        engine = AgentEngine(config, registry)

        read_tc = SimpleNamespace(
            id="call_read",
            function=SimpleNamespace(
                name="read_excel",
                arguments=json.dumps(
                    {"file_path": "sales.xlsx", "sheet_name": "Q1", "range": "A1:E10"},
                    ensure_ascii=False,
                ),
            ),
        )
        first = await engine._execute_tool_call(
            tc=read_tc,
            tool_scope=["read_excel", "write_cells"],
            on_event=None,
            iteration=1,
            route_result=None,
        )
        second = await engine._execute_tool_call(
            tc=read_tc,
            tool_scope=["read_excel", "write_cells"],
            on_event=None,
            iteration=2,
            route_result=None,
        )
        third = await engine._execute_tool_call(
            tc=read_tc,
            tool_scope=["read_excel", "write_cells"],
            on_event=None,
            iteration=3,
            route_result=None,
        )

        assert "⚠️ 此数据已在窗口" not in first.result
        assert "提示=当前意图[aggregate]下此数据已在窗口" in second.result
        assert "───────────── 环境感知 ─────────────" in third.result

        write_tc = SimpleNamespace(
            id="call_write",
            function=SimpleNamespace(name="write_cells", arguments="{}"),
        )
        _ = await engine._execute_tool_call(
            tc=write_tc,
            tool_scope=["read_excel", "write_cells"],
            on_event=None,
            iteration=4,
            route_result=None,
        )
        after_write = await engine._execute_tool_call(
            tc=read_tc,
            tool_scope=["read_excel", "write_cells"],
            on_event=None,
            iteration=5,
            route_result=None,
        )
        assert "⚠️ 此数据已在窗口" not in after_write.result
        assert "───────────── 环境感知 ─────────────" not in after_write.result

    @pytest.mark.asyncio
    async def test_enriched_mode_hides_focus_window_tool(self) -> None:
        config = _make_config(window_return_mode="enriched")
        registry = _make_registry_with_tools()
        registry.register_tool(
            ToolDef(
                name="focus_window",
                description="窗口聚焦",
                input_schema={"type": "object", "properties": {}},
                func=lambda: "ok",
            )
        )
        engine = AgentEngine(config, registry)
        route_result = SkillMatchResult(
            skills_used=[],
            tool_scope=["focus_window", "add_numbers"],
            route_mode="fallback",
            system_contexts=[],
        )
        scope = engine._get_current_tool_scope(route_result=route_result)
        assert "focus_window" not in scope

        anchored_engine = AgentEngine(_make_config(window_return_mode="anchored"), registry)
        anchored_scope = anchored_engine._get_current_tool_scope(route_result=route_result)
        assert "focus_window" in anchored_scope

        adaptive_enriched_engine = AgentEngine(
            _make_config(window_return_mode="adaptive", model="deepseek-chat"),
            registry,
        )
        adaptive_enriched_scope = adaptive_enriched_engine._get_current_tool_scope(
            route_result=route_result
        )
        assert "focus_window" not in adaptive_enriched_scope

        adaptive_unified_engine = AgentEngine(
            _make_config(window_return_mode="adaptive", model="gpt-5.2"),
            registry,
        )
        adaptive_unified_scope = adaptive_unified_engine._get_current_tool_scope(
            route_result=route_result
        )
        assert "focus_window" in adaptive_unified_scope

    @pytest.mark.asyncio
    async def test_window_perception_anchored_notice_is_data_window_and_tail(self) -> None:
        def read_excel() -> str:
            return json.dumps(
                {
                    "file": "sales.xlsx",
                    "sheet": "Q1",
                    "shape": {"rows": 20, "columns": 5},
                    "preview": [{"产品": "A", "金额": 100}],
                },
                ensure_ascii=False,
            )

        registry = ToolRegistry()
        registry.register_tools([
            ToolDef(
                name="read_excel",
                description="读取",
                input_schema={"type": "object", "properties": {}},
                func=read_excel,
                max_result_chars=0,
            ),
        ])
        config = _make_config(window_return_mode="anchored", system_message_mode="replace")
        engine = AgentEngine(config, registry)
        tc = SimpleNamespace(
            id="call_read",
            function=SimpleNamespace(name="read_excel", arguments="{}"),
        )
        await engine._execute_tool_call(
            tc=tc,
            tool_scope=["read_excel"],
            on_event=None,
            iteration=1,
            route_result=None,
        )

        prompts, error = engine._prepare_system_prompts_for_request(["## SkillCtx\n内容"])
        assert error is None
        assert prompts[-1].startswith("## 数据窗口")

    @pytest.mark.asyncio
    async def test_window_perception_notice_respects_budget_and_window_limit(self) -> None:
        def read_excel(file_path: str, sheet_name: str) -> str:
            preview = [
                {
                    "产品": f"产品{i}",
                    "备注": "超长内容" * 30,
                    "说明": "X" * 240,
                }
                for i in range(25)
            ]
            return json.dumps(
                {
                    "file": file_path,
                    "sheet": sheet_name,
                    "shape": {"rows": 5000, "columns": 30},
                    "preview": preview,
                },
                ensure_ascii=False,
            )

        registry = ToolRegistry()
        registry.register_tools([
            ToolDef(
                name="read_excel",
                description="读取",
                input_schema={
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string"},
                        "sheet_name": {"type": "string"},
                    },
                    "required": ["file_path", "sheet_name"],
                },
                func=read_excel,
                max_result_chars=0,
            ),
        ])
        config = _make_config(
            window_perception_system_budget_tokens=400,
            window_perception_max_windows=2,
            window_perception_minimized_tokens=40,
        )
        engine = AgentEngine(config, registry)

        for idx, file_name in enumerate(("q1.xlsx", "q2.xlsx", "q3.xlsx"), start=1):
            tc = SimpleNamespace(
                id=f"call_read_{idx}",
                function=SimpleNamespace(
                    name="read_excel",
                    arguments=json.dumps({"file_path": file_name, "sheet_name": "Q1"}),
                ),
            )
            await engine._execute_tool_call(
                tc=tc,
                tool_scope=["read_excel"],
                on_event=None,
                iteration=idx,
                route_result=None,
            )

        notice = engine._build_window_perception_notice()
        tokens = TokenCounter.count_message({"role": "system", "content": notice})
        assert tokens <= config.window_perception_system_budget_tokens
        assert "## 窗口感知上下文" in notice
        assert "q3.xlsx" in notice
        assert "q1.xlsx" not in notice

    @pytest.mark.asyncio
    async def test_window_perception_lifecycle_ages_to_background_and_suspended(self) -> None:
        def read_excel(file_path: str, sheet_name: str) -> str:
            return json.dumps(
                {
                    "file": file_path,
                    "sheet": sheet_name,
                    "shape": {"rows": 2004, "columns": 12},
                    "preview": [{"订单编号": "ORD-1", "日期": "2025-01-01", "金额": 100}],
                },
                ensure_ascii=False,
            )

        registry = ToolRegistry()
        registry.register_tools([
            ToolDef(
                name="read_excel",
                description="读取",
                input_schema={
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string"},
                        "sheet_name": {"type": "string"},
                    },
                    "required": ["file_path", "sheet_name"],
                },
                func=read_excel,
                max_result_chars=0,
            ),
        ])
        config = _make_config(
            window_perception_background_after_idle=1,
            window_perception_suspend_after_idle=3,
            window_perception_terminate_after_idle=5,
        )
        engine = AgentEngine(config, registry)

        async def _read(file_path: str, iteration: int) -> None:
            tc = SimpleNamespace(
                id=f"call_read_{iteration}",
                function=SimpleNamespace(
                    name="read_excel",
                    arguments=json.dumps({"file_path": file_path, "sheet_name": "Q1"}),
                ),
            )
            await engine._execute_tool_call(
                tc=tc,
                tool_scope=["read_excel"],
                on_event=None,
                iteration=iteration,
                route_result=None,
            )

        await _read("sales.xlsx", 1)
        notice1 = engine._build_window_perception_notice()
        assert "【窗口 · sales.xlsx / Q1】" in notice1

        await _read("catalog.xlsx", 2)
        notice2 = engine._build_window_perception_notice()
        assert "【窗口 · catalog.xlsx / Q1】" in notice2
        assert "【后台 · sales.xlsx / Q1】" in notice2

        notice3 = engine._build_window_perception_notice()
        assert "【后台 · sales.xlsx / Q1】" in notice3
        assert "【后台 · catalog.xlsx / Q1】" in notice3

        notice4 = engine._build_window_perception_notice()
        assert "【挂起 · sales.xlsx / Q1" in notice4
        assert "【后台 · catalog.xlsx / Q1】" in notice4

    @pytest.mark.asyncio
    async def test_window_perception_terminated_window_can_reactivate(self) -> None:
        def read_excel(file_path: str, sheet_name: str) -> str:
            return json.dumps(
                {
                    "file": file_path,
                    "sheet": sheet_name,
                    "shape": {"rows": 500, "columns": 8},
                    "preview": [{"列A": 1, "列B": 2}],
                },
                ensure_ascii=False,
            )

        registry = ToolRegistry()
        registry.register_tools([
            ToolDef(
                name="read_excel",
                description="读取",
                input_schema={
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string"},
                        "sheet_name": {"type": "string"},
                    },
                    "required": ["file_path", "sheet_name"],
                },
                func=read_excel,
                max_result_chars=0,
            ),
        ])
        config = _make_config(
            window_perception_background_after_idle=1,
            window_perception_suspend_after_idle=2,
            window_perception_terminate_after_idle=3,
        )
        engine = AgentEngine(config, registry)

        tc = SimpleNamespace(
            id="call_read_init",
            function=SimpleNamespace(
                name="read_excel",
                arguments=json.dumps({"file_path": "reactivate.xlsx", "sheet_name": "Q1"}),
            ),
        )
        await engine._execute_tool_call(
            tc=tc,
            tool_scope=["read_excel"],
            on_event=None,
            iteration=1,
            route_result=None,
        )

        notice1 = engine._build_window_perception_notice()
        assert "reactivate.xlsx" in notice1

        _ = engine._build_window_perception_notice()  # idle=1
        _ = engine._build_window_perception_notice()  # idle=2
        notice4 = engine._build_window_perception_notice()  # idle=3 -> terminated
        assert "reactivate.xlsx" not in notice4

        tc2 = SimpleNamespace(
            id="call_read_reopen",
            function=SimpleNamespace(
                name="read_excel",
                arguments=json.dumps({"file_path": "reactivate.xlsx", "sheet_name": "Q1"}),
            ),
        )
        await engine._execute_tool_call(
            tc=tc2,
            tool_scope=["read_excel"],
            on_event=None,
            iteration=2,
            route_result=None,
        )
        notice5 = engine._build_window_perception_notice()
        assert "【窗口 · reactivate.xlsx / Q1】" in notice5

    @pytest.mark.asyncio
    async def test_window_perception_hybrid_advisor_is_non_blocking(self) -> None:
        def read_excel(file_path: str, sheet_name: str) -> str:
            return json.dumps(
                {
                    "file": file_path,
                    "sheet": sheet_name,
                    "shape": {"rows": 200, "columns": 8},
                    "preview": [{"列A": 1, "列B": 2}],
                },
                ensure_ascii=False,
            )

        async def _slow_response(**_kwargs):
            await asyncio.sleep(0.2)
            return _make_text_response(
                '{"task_type":"GENERAL_BROWSE","advices":[]}'
            )

        registry = ToolRegistry()
        registry.register_tools([
            ToolDef(
                name="read_excel",
                description="读取",
                input_schema={
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string"},
                        "sheet_name": {"type": "string"},
                    },
                    "required": ["file_path", "sheet_name"],
                },
                func=read_excel,
                max_result_chars=0,
            ),
        ])
        config = _make_config(
            window_perception_advisor_mode="hybrid",
            window_perception_advisor_trigger_window_count=1,
            window_perception_advisor_trigger_turn=1,
            window_perception_advisor_timeout_ms=1000,
        )
        engine = AgentEngine(config, registry)
        engine._router_client.chat.completions.create = AsyncMock(side_effect=_slow_response)

        tc = SimpleNamespace(
            id="call_read_init",
            function=SimpleNamespace(
                name="read_excel",
                arguments=json.dumps({"file_path": "sales.xlsx", "sheet_name": "Q1"}),
            ),
        )
        await engine._execute_tool_call(
            tc=tc,
            tool_scope=["read_excel"],
            on_event=None,
            iteration=1,
            route_result=None,
        )

        started = time.monotonic()
        notice = engine._build_window_perception_notice()
        elapsed = time.monotonic() - started
        assert "sales.xlsx" in notice
        assert elapsed < 0.15

    @pytest.mark.asyncio
    async def test_window_perception_hybrid_advisor_applies_cached_plan_next_turn(self) -> None:
        def read_excel(file_path: str, sheet_name: str) -> str:
            return json.dumps(
                {
                    "file": file_path,
                    "sheet": sheet_name,
                    "shape": {"rows": 2004, "columns": 12},
                    "preview": [{"订单编号": "ORD-1", "日期": "2025-01-01", "金额": 100}],
                },
                ensure_ascii=False,
            )

        registry = ToolRegistry()
        registry.register_tools([
            ToolDef(
                name="read_excel",
                description="读取",
                input_schema={
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string"},
                        "sheet_name": {"type": "string"},
                    },
                    "required": ["file_path", "sheet_name"],
                },
                func=read_excel,
                max_result_chars=0,
            ),
        ])
        config = _make_config(
            window_perception_advisor_mode="hybrid",
            window_perception_advisor_trigger_window_count=1,
            window_perception_advisor_trigger_turn=1,
            window_perception_advisor_plan_ttl_turns=2,
        )
        engine = AgentEngine(config, registry)
        engine._router_client.chat.completions.create = AsyncMock(
            return_value=_make_text_response('{"task_type":"GENERAL_BROWSE","advices":[]}')
        )

        async def _read(file_path: str, iteration: int) -> None:
            tc = SimpleNamespace(
                id=f"call_read_{iteration}",
                function=SimpleNamespace(
                    name="read_excel",
                    arguments=json.dumps({"file_path": file_path, "sheet_name": "Q1"}),
                ),
            )
            await engine._execute_tool_call(
                tc=tc,
                tool_scope=["read_excel"],
                on_event=None,
                iteration=iteration,
                route_result=None,
            )

        await _read("sales.xlsx", 1)
        await _read("catalog.xlsx", 2)

        first_notice = engine._build_window_perception_notice()
        assert "sales.xlsx / Q1" in first_notice
        assert "catalog.xlsx / Q1" in first_notice

        plan_text = '{"task_type":"GENERAL_BROWSE","advices":[{"window_id":"sheet_1","tier":"suspended","reason":"done"}]}'
        engine._router_client.chat.completions.create = AsyncMock(
            return_value=_make_text_response(plan_text)
        )

        _ = engine._build_window_perception_notice()
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        second_notice = engine._build_window_perception_notice()
        assert "【挂起 · sales.xlsx / Q1" in second_notice

    @pytest.mark.asyncio
    async def test_window_perception_hybrid_advisor_falls_back_when_router_fails(self) -> None:
        def read_excel(file_path: str, sheet_name: str) -> str:
            return json.dumps(
                {
                    "file": file_path,
                    "sheet": sheet_name,
                    "shape": {"rows": 2004, "columns": 12},
                    "preview": [{"订单编号": "ORD-1", "日期": "2025-01-01", "金额": 100}],
                },
                ensure_ascii=False,
            )

        registry = ToolRegistry()
        registry.register_tools([
            ToolDef(
                name="read_excel",
                description="读取",
                input_schema={
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string"},
                        "sheet_name": {"type": "string"},
                    },
                    "required": ["file_path", "sheet_name"],
                },
                func=read_excel,
                max_result_chars=0,
            ),
        ])
        config = _make_config(
            window_perception_advisor_mode="hybrid",
            window_perception_advisor_trigger_window_count=1,
            window_perception_advisor_trigger_turn=1,
        )
        engine = AgentEngine(config, registry)
        engine._router_client.chat.completions.create = AsyncMock(
            side_effect=RuntimeError("router failed")
        )

        async def _read(file_path: str, iteration: int) -> None:
            tc = SimpleNamespace(
                id=f"call_read_{iteration}",
                function=SimpleNamespace(
                    name="read_excel",
                    arguments=json.dumps({"file_path": file_path, "sheet_name": "Q1"}),
                ),
            )
            await engine._execute_tool_call(
                tc=tc,
                tool_scope=["read_excel"],
                on_event=None,
                iteration=iteration,
                route_result=None,
            )

        await _read("sales.xlsx", 1)
        await _read("catalog.xlsx", 2)
        _ = engine._build_window_perception_notice()
        await asyncio.sleep(0)
        fallback_notice = engine._build_window_perception_notice()
        assert "【后台 · sales.xlsx / Q1】" in fallback_notice


class TestTaskUpdateFailureSemantics:
    """task_update 失败语义与事件一致性测试。"""

    @pytest.mark.asyncio
    async def test_invalid_transition_returns_failure_and_no_task_item_updated_event(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        registry.register_tools(task_tools.get_tools())
        engine = AgentEngine(config, registry)
        engine._task_store.create("测试任务", ["子任务A"])

        tc = SimpleNamespace(
            id="call_task_update_1",
            function=SimpleNamespace(
                name="task_update",
                arguments=json.dumps({"task_index": 0, "status": "completed"}),
            ),
        )

        events: list = []
        result = await engine._execute_tool_call(
            tc=tc,
            tool_scope=["task_update"],
            on_event=events.append,
            iteration=1,
            route_result=None,
        )

        assert result.success is False
        assert "非法状态转换" in result.result
        assert all(
            event.event_type != EventType.TASK_ITEM_UPDATED
            for event in events
        )
        assert engine._task_store.current is not None
        assert engine._task_store.current.items[0].status == TaskStatus.PENDING


class TestPlanModeControl:
    """plan mode 控制命令与执行流测试。"""

    @pytest.mark.asyncio
    async def test_plan_status_on_off(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)

        status = await engine.chat("/plan status")
        assert "disabled" in status.reply

        turn_on = await engine.chat("/plan on")
        assert "已开启" in turn_on.reply
        assert engine.plan_mode_enabled is True

        turn_off = await engine.chat("/plan off")
        assert "已关闭" in turn_off.reply
        assert engine.plan_mode_enabled is False

    @pytest.mark.asyncio
    async def test_planmode_alias_returns_tombstone_message(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)

        result = await engine.chat("/planmode on")
        assert "命令已移除，请使用 /plan ..." in result.reply
        assert engine.plan_mode_enabled is False

    @pytest.mark.asyncio
    async def test_plan_mode_alias_returns_tombstone_message(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)

        result = await engine.chat("/plan_mode status")
        assert "命令已移除，请使用 /plan ..." in result.reply
        assert engine.plan_mode_enabled is False

    @pytest.mark.asyncio
    async def test_plan_mode_message_generates_pending_plan_only(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        engine._plan_mode_enabled = True
        engine._route_skills = AsyncMock()

        draft = PlanDraft(
            plan_id="pln_test_001",
            markdown="# 计划\n\n## 任务清单\n- [ ] A",
            title="测试计划",
            subtasks=["A"],
            file_path=".excelmanus/plans/plan_test.md",
            source="plan_mode",
            objective="请规划测试任务",
            created_at_utc="2026-02-13T00:00:00Z",
        )

        async def _fake_create_pending(**kwargs):
            engine._pending_plan = PendingPlanState(draft=draft)
            return draft, None

        engine._create_pending_plan_draft = AsyncMock(side_effect=_fake_create_pending)
        result = await engine.chat("请规划测试任务")
        assert "待你审批" in result.reply
        assert engine._route_skills.await_count == 0
        assert engine._pending_plan is not None

    @pytest.mark.asyncio
    async def test_plan_approve_from_plan_mode_creates_tasklist_and_executes(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        engine._plan_mode_enabled = True

        draft = PlanDraft(
            plan_id="pln_test_approve_1",
            markdown="# 自动化计划\n\n## 任务清单\n- [ ] 第一步\n- [ ] 第二步",
            title="自动化计划",
            subtasks=["第一步", "第二步"],
            file_path=".excelmanus/plans/plan_test.md",
            source="plan_mode",
            objective="执行自动化任务",
            created_at_utc="2026-02-13T00:00:00Z",
        )
        engine._pending_plan = PendingPlanState(draft=draft)
        engine._route_skills = AsyncMock(
            return_value=SkillMatchResult(
                skills_used=[],
                tool_scope=["add_numbers"],
                route_mode="fallback",
                system_contexts=[],
            )
        )
        engine._client.chat.completions.create = AsyncMock(
            return_value=_make_text_response("执行完成")
        )

        result = await engine.chat("/plan approve pln_test_approve_1")
        assert "执行完成" in result.reply
        assert engine.plan_mode_enabled is False
        assert engine._task_store.current is not None
        assert engine._task_store.current.title == "自动化计划"
        assert "来源: .excelmanus/plans/plan_test.md" in (engine._approved_plan_context or "")

    @pytest.mark.asyncio
    async def test_task_create_hook_enters_pending_plan(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)

        draft = PlanDraft(
            plan_id="pln_task_create_hook",
            markdown="# 计划\n\n## 任务清单\n- [ ] A",
            title="任务清单",
            subtasks=["A"],
            file_path=".excelmanus/plans/plan_hook.md",
            source="task_create_hook",
            objective="草稿任务",
            created_at_utc="2026-02-13T00:00:00Z",
        )

        async def _fake_create_pending(**kwargs):
            engine._pending_plan = PendingPlanState(
                draft=draft,
                tool_call_id="call_tc",
                route_to_resume=kwargs.get("route_to_resume"),
            )
            return draft, None

        engine._create_pending_plan_draft = AsyncMock(side_effect=_fake_create_pending)
        tc = SimpleNamespace(
            id="call_tc",
            function=SimpleNamespace(
                name="task_create",
                arguments=json.dumps({"title": "任务清单", "subtasks": ["A"]}),
            ),
        )
        route_result = SkillMatchResult(
            skills_used=[],
            tool_scope=["task_create"],
            route_mode="fallback",
            system_contexts=[],
        )
        result = await engine._execute_tool_call(
            tc=tc,
            tool_scope=["task_create"],
            on_event=None,
            iteration=1,
            route_result=route_result,
        )
        assert result.success is True
        assert result.pending_plan is True
        assert result.defer_tool_result is True
        assert engine._task_store.current is None

    @pytest.mark.asyncio
    async def test_pending_plan_blocks_and_reject_unblocks(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        draft = PlanDraft(
            plan_id="pln_block_1",
            markdown="# 计划\n\n## 任务清单\n- [ ] A",
            title="阻塞计划",
            subtasks=["A"],
            file_path=".excelmanus/plans/plan_block.md",
            source="plan_mode",
            objective="阻塞目标",
            created_at_utc="2026-02-13T00:00:00Z",
        )
        engine._pending_plan = PendingPlanState(draft=draft)

        blocked = await engine.chat("继续执行")
        assert "待你审批" in blocked.reply

        rejected = await engine.chat("/plan reject pln_block_1")
        assert "已拒绝计划" in rejected.reply
        assert engine._pending_plan is None

    def test_build_system_prompts_includes_approved_plan_context(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        engine._approved_plan_context = "来源: .excelmanus/plans/plan_x.md\n# 计划"

        prompts = engine._build_system_prompts([])
        assert len(prompts) == 1
        assert "## 已批准计划上下文" in prompts[0]
        assert "plan_x.md" in prompts[0]


class TestManualSkillSlashCommand:
    """手动 Skill 斜杠命令解析与路由。"""

    @pytest.mark.asyncio
    async def test_route_mode_is_all_tools_when_skill_router_missing(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        engine._skill_router = None
        engine._client.chat.completions.create = AsyncMock(
            return_value=_make_text_response("ok")
        )

        result = await engine.chat("请读取数据")
        assert result == "ok"
        assert engine.last_route_result.route_mode == "all_tools"

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

    def test_resolve_skill_command_supports_namespace(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)

        mock_loader = MagicMock()
        mock_loader.get_skillpacks.return_value = {
            "team/data-cleaner": MagicMock(),
        }
        mock_router = MagicMock()
        mock_router._loader = mock_loader
        engine._skill_router = mock_router

        assert (
            engine.resolve_skill_command("/team/data-cleaner --mode fast")
            == "team/data-cleaner"
        )
        assert engine.resolve_skill_command("/team/data-cleaner.xlsx") is None

    def test_resolve_skill_command_respects_user_invocable(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)

        mock_loader = MagicMock()
        mock_loader.get_skillpacks.return_value = {
            "private_skill": Skillpack(
                name="private_skill",
                description="private",
                allowed_tools=["add_numbers"],
                triggers=[],
                instructions="",
                source="project",
                root_dir="/tmp/private",
                user_invocable=False,
            )
        }
        mock_router = MagicMock()
        mock_router._loader = mock_loader
        engine._skill_router = mock_router

        assert engine.resolve_skill_command("/private_skill run") is None

    @pytest.mark.asyncio
    async def test_chat_rejects_slash_for_not_user_invocable_skill(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)

        engine._route_skills = AsyncMock(
            return_value=SkillMatchResult(
                skills_used=[],
                tool_scope=[],
                route_mode="slash_not_user_invocable",
                system_contexts=[],
            )
        )
        result = await engine.chat(
            "/private_skill do",
            slash_command="private_skill",
            raw_args="do",
        )
        assert isinstance(result, ChatResult)
        assert "不允许手动调用" in result.reply


class TestForkPathRemoved:
    """fork 链路已硬移除，仅保留显式 delegate_to_subagent。"""

    @pytest.mark.asyncio
    async def test_chat_with_active_skill_no_longer_auto_delegates(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)

        engine._active_skill = Skillpack(
            name="excel_code_runner",
            description="代码处理",
            allowed_tools=[],
            triggers=[],
            instructions="",
            source="project",
            root_dir="/tmp/skill",
        )
        route_result = SkillMatchResult(
            skills_used=["excel_code_runner"],
            tool_scope=["add_numbers"],
            route_mode="fallback",
            system_contexts=["[Skillpack] excel_code_runner"],
        )
        engine._route_skills = AsyncMock(return_value=route_result)
        engine._delegate_to_subagent = AsyncMock(
            return_value=DelegateSubagentOutcome(
                reply="不应被调用",
                success=True,
            )
        )
        engine._client.chat.completions.create = AsyncMock(
            return_value=_make_text_response("主代理执行完成。")
        )

        result = await engine.chat("请处理这个大文件")
        assert result.reply == "主代理执行完成。"
        engine._delegate_to_subagent.assert_not_awaited()
        engine._client.chat.completions.create.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_select_skill_success_no_longer_triggers_auto_delegate(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        engine._memory.add_user_message("请分析销售趋势")
        engine._delegate_to_subagent = AsyncMock()

        async def _fake_execute_tool_call(*args, **kwargs) -> ToolCallResult:
            engine._active_skill = Skillpack(
                name="team/analyst",
                description="普通技能",
                allowed_tools=["add_numbers"],
                triggers=[],
                instructions="",
                source="project",
                root_dir="/tmp/skill",
            )
            return ToolCallResult(
                tool_name="select_skill",
                arguments={"skill_name": "team/analyst"},
                result="OK",
                success=True,
            )

        engine._execute_tool_call = AsyncMock(side_effect=_fake_execute_tool_call)
        engine._client.chat.completions.create = AsyncMock(
            side_effect=[
                _make_tool_call_response(
                    [("call_1", "select_skill", json.dumps({"skill_name": "team/analyst"}))]
                ),
                _make_text_response("主代理继续执行。"),
            ]
        )

        route_result = SkillMatchResult(
            skills_used=[],
            tool_scope=["select_skill"],
            route_mode="fallback",
            system_contexts=[],
        )
        result = await engine._tool_calling_loop(route_result, on_event=None)

        assert result.reply == "主代理继续执行。"
        engine._delegate_to_subagent.assert_not_awaited()
        assert engine._client.chat.completions.create.call_count == 2

    def test_engine_has_no_run_fork_skill_entrypoint(self) -> None:
        engine = AgentEngine(_make_config(), _make_registry_with_tools())
        assert not hasattr(engine, "_run_fork_skill")


class TestDelegateSubagent:
    """delegate_to_subagent 元工具测试。"""

    @pytest.mark.asyncio
    async def test_delegate_tool_call_runs_subagent_and_returns_summary(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        engine._delegate_to_subagent = AsyncMock(
            return_value=DelegateSubagentOutcome(
                reply="子代理摘要",
                success=True,
                picked_agent="explorer",
                task_text="探查销量异常",
                normalized_paths=["sales.xlsx"],
                subagent_result=None,
            )
        )

        tc = SimpleNamespace(
            id="call_1",
            function=SimpleNamespace(
                name="delegate_to_subagent",
                arguments=json.dumps(
                    {"task": "探查销量异常", "file_paths": ["sales.xlsx"]},
                ),
            ),
        )

        result = await engine._execute_tool_call(
            tc=tc,
            tool_scope=["delegate_to_subagent"],
            on_event=None,
            iteration=1,
        )

        assert result.success is True
        assert result.result == "子代理摘要"

    @pytest.mark.asyncio
    async def test_delegate_updates_window_perception_context_from_subagent(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)

        engine.run_subagent = AsyncMock(
            return_value=SubagentResult(
                success=True,
                summary="子代理摘要",
                subagent_name="explorer",
                permission_mode="readOnly",
                conversation_id="conv_1",
                observed_files=["./examples/bench/stress_test_comprehensive.xlsx"],
            )
        )

        result = await engine._handle_delegate_to_subagent(
            task="查找包含销售明细工作表的文件",
            agent_name="explorer",
            file_paths=None,
        )
        assert result == "子代理摘要"

        notice = engine._build_window_perception_notice()
        assert "examples/bench/stress_test_comprehensive.xlsx" in notice

        prompts = engine._build_system_prompts([])
        assert len(prompts) == 1
        assert "examples/bench/stress_test_comprehensive.xlsx" in prompts[0]

    @pytest.mark.asyncio
    async def test_run_subagent_passes_window_context_and_enricher(self) -> None:
        config = _make_config(window_perception_enabled=True)
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)

        engine._subagent_registry = MagicMock()
        engine._subagent_registry.get.return_value = SubagentConfig(
            name="explorer",
            description="测试",
            allowed_tools=["read_excel"],
            permission_mode="readOnly",
        )
        engine._subagent_executor.run = AsyncMock(
            return_value=SubagentResult(
                success=True,
                summary="ok",
                subagent_name="explorer",
                permission_mode="readOnly",
                conversation_id="conv_x",
            )
        )

        engine._window_perception.observe_subagent_context(
            candidate_paths=["./examples/bench/stress_test_comprehensive.xlsx"],
            subagent_name="explorer",
            task="预热窗口",
        )

        result = await engine.run_subagent(agent_name="explorer", prompt="请分析")

        assert result.success is True
        kwargs = engine._subagent_executor.run.await_args.kwargs
        assert "窗口感知上下文" in kwargs["parent_context"]
        assert callable(kwargs["tool_result_enricher"])

    @pytest.mark.asyncio
    async def test_delegate_pending_approval_asks_user_and_supports_fullaccess_retry(
        self,
        tmp_path: Path,
    ) -> None:
        config = _make_config(workspace_root=str(tmp_path))
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)

        pending = engine._approval.create_pending(
            tool_name="run_code",
            arguments={"script": "print('ok')"},
            tool_scope=["run_code"],
        )

        engine.run_subagent = AsyncMock(
            side_effect=[
                SubagentResult(
                    success=False,
                    summary="子代理命中高风险操作",
                    subagent_name="analyst",
                    permission_mode="default",
                    conversation_id="conv_1",
                    pending_approval_id=pending.approval_id,
                ),
                SubagentResult(
                    success=True,
                    summary="重试完成",
                    subagent_name="analyst",
                    permission_mode="default",
                    conversation_id="conv_2",
                ),
            ]
        )

        tc = SimpleNamespace(
            id="call_1",
            function=SimpleNamespace(
                name="delegate_to_subagent",
                arguments=json.dumps(
                    {
                        "task": "统计城市销售额",
                        "agent_name": "analyst",
                        "file_paths": ["examples/bench/stress_test_comprehensive.xlsx"],
                    },
                    ensure_ascii=False,
                ),
            ),
        )

        first = await engine._execute_tool_call(
            tc=tc,
            tool_scope=["delegate_to_subagent"],
            on_event=None,
            iteration=1,
        )
        assert first.success is True
        assert first.pending_question is True
        assert engine.has_pending_question() is True
        prompt = engine._question_flow.format_prompt()
        assert "fullAccess" in prompt
        assert pending.approval_id in prompt

        resumed = await engine.chat("2")
        assert "已开启 fullAccess" in resumed.reply
        assert "已拒绝待确认操作" in resumed.reply
        assert "重试完成" in resumed.reply
        assert engine.full_access_enabled is True
        assert engine._approval.pending is None
        assert engine.run_subagent.await_count == 2

    @pytest.mark.asyncio
    async def test_delegate_rejects_invalid_file_paths_type(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)

        tc = SimpleNamespace(
            id="call_1",
            function=SimpleNamespace(
                name="delegate_to_subagent",
                arguments=json.dumps(
                    {"task": "探查销量异常", "file_paths": "sales.xlsx"}
                ),
            ),
        )

        result = await engine._execute_tool_call(
            tc=tc,
            tool_scope=["delegate_to_subagent"],
            on_event=None,
            iteration=1,
        )

        assert result.success is False
        assert "file_paths 必须为字符串数组" in result.result

    @pytest.mark.asyncio
    async def test_delegate_rejects_invalid_agent_name_type(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)

        tc = SimpleNamespace(
            id="call_1",
            function=SimpleNamespace(
                name="delegate_to_subagent",
                arguments=json.dumps(
                    {"task": "探查销量异常", "agent_name": 123},
                ),
            ),
        )

        result = await engine._execute_tool_call(
            tc=tc,
            tool_scope=["delegate_to_subagent"],
            on_event=None,
            iteration=1,
        )
        assert result.success is False
        assert "agent_name 必须为字符串" in result.result

    @pytest.mark.asyncio
    async def test_auto_select_subagent_uses_description_catalog(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        engine._subagent_registry = MagicMock()
        engine._subagent_registry.list_all.return_value = [
            SubagentConfig(
                name="explorer",
                description="目录与 Excel 结构探查",
            ),
            SubagentConfig(
                name="analyst",
                description="统计分析与异常定位",
            ),
        ]
        engine._subagent_registry.build_catalog.return_value = (
            "可用子代理：\n- explorer：目录与 Excel 结构探查\n- analyst：统计分析与异常定位",
            ["explorer", "analyst"],
        )
        engine._router_client.chat.completions.create = AsyncMock(
            return_value=_make_text_response('{"agent_name":"explorer"}')
        )

        picked = await engine._auto_select_subagent(
            task="请先总结这个文件夹结构",
            file_paths=["data"],
        )

        assert picked == "explorer"
        _, kwargs = engine._router_client.chat.completions.create.call_args
        messages = kwargs["messages"]
        assert "候选子代理：" in messages[1]["content"]
        assert "explorer" in messages[1]["content"]
        assert "analyst" in messages[1]["content"]
        assert "相关文件：data" in messages[1]["content"]

    @pytest.mark.asyncio
    async def test_auto_select_subagent_fallbacks_to_explorer_on_invalid_choice(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        engine._subagent_registry = MagicMock()
        engine._subagent_registry.list_all.return_value = [
            SubagentConfig(name="explorer", description="目录探查"),
            SubagentConfig(name="writer", description="写入改造"),
        ]
        engine._subagent_registry.build_catalog.return_value = (
            "可用子代理：\n- explorer：目录探查\n- writer：写入改造",
            ["explorer", "writer"],
        )
        engine._router_client.chat.completions.create = AsyncMock(
            return_value=_make_text_response('{"agent_name":"unknown"}')
        )

        picked = await engine._auto_select_subagent(
            task="请分析一下",
            file_paths=[],
        )

        assert picked == "explorer"


class TestAskUserFlow:
    """ask_user 挂起恢复与队列行为测试。"""

    @staticmethod
    def _ask_question_payload(
        *,
        header: str = "实现方案",
        text: str = "请选择实现方案",
        multi_select: bool = False,
    ) -> dict:
        return {
            "question": {
                "header": header,
                "text": text,
                "options": [
                    {"label": "方案A", "description": "快速实现"},
                    {"label": "方案B", "description": "稳健实现"},
                ],
                "multiSelect": multi_select,
            }
        }

    @pytest.mark.asyncio
    async def test_ask_user_suspends_and_resumes_without_reroute(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)

        route_result = SkillMatchResult(
            skills_used=[],
            tool_scope=["ask_user", "add_numbers"],
            route_mode="llm_confirm",
            system_contexts=[],
        )
        engine._route_skills = AsyncMock(return_value=route_result)

        ask_response = _make_tool_call_response(
            [
                (
                    "call_q1",
                    "ask_user",
                    json.dumps(self._ask_question_payload(), ensure_ascii=False),
                )
            ]
        )
        do_work_response = _make_tool_call_response(
            [("call_add", "add_numbers", json.dumps({"a": 1, "b": 2}))]
        )
        final_response = _make_text_response("已按你的选择完成，结果是 3。")
        engine._client.chat.completions.create = AsyncMock(
            side_effect=[ask_response, do_work_response, final_response]
        )

        first = await engine.chat("请完成任务")
        assert "请先回答这个问题后再继续" in first.reply
        assert engine.has_pending_question() is True
        assert engine._route_skills.await_count == 1

        resumed = await engine.chat("1")
        assert resumed.reply == "已按你的选择完成，结果是 3。"
        assert engine.has_pending_question() is False
        # 回答问题后直接恢复执行，不应重新路由
        assert engine._route_skills.await_count == 1

        tool_msgs = [m for m in engine.memory.get_messages() if m.get("role") == "tool"]
        ask_msg = next(m for m in tool_msgs if m.get("tool_call_id") == "call_q1")
        ask_payload = json.loads(ask_msg["content"])
        assert ask_payload["question_id"].startswith("qst_")
        assert ask_payload["multi_select"] is False
        assert ask_payload["selected_options"][0]["label"] == "方案A"

    @pytest.mark.asyncio
    async def test_fifo_multiple_questions_and_skip_non_ask_user(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)

        route_result = SkillMatchResult(
            skills_used=[],
            tool_scope=["ask_user", "add_numbers"],
            route_mode="llm_confirm",
            system_contexts=[],
        )
        engine._route_skills = AsyncMock(return_value=route_result)

        first_round = _make_tool_call_response(
            [
                (
                    "call_q1",
                    "ask_user",
                    json.dumps(
                        self._ask_question_payload(
                            header="语言",
                            text="选择开发语言",
                            multi_select=False,
                        ),
                        ensure_ascii=False,
                    ),
                ),
                ("call_skip", "add_numbers", json.dumps({"a": 10, "b": 20})),
                (
                    "call_q2",
                    "ask_user",
                    json.dumps(
                        self._ask_question_payload(
                            header="约束",
                            text="选择约束策略",
                            multi_select=True,
                        ),
                        ensure_ascii=False,
                    ),
                ),
            ]
        )
        final_response = _make_text_response("两个问题都确认完毕。")
        engine._client.chat.completions.create = AsyncMock(
            side_effect=[first_round, final_response]
        )

        asked = await engine.chat("开始执行")
        assert "选择开发语言" in asked.reply
        assert engine.has_pending_question() is True
        assert len(asked.tool_calls) == 3
        skipped = next(r for r in asked.tool_calls if r.tool_name == "add_numbers")
        assert skipped.success is True
        assert "已跳过" in skipped.result

        second_prompt = await engine.chat("1")
        assert "选择约束策略" in second_prompt.reply
        assert engine.has_pending_question() is True

        done = await engine.chat("1\n自定义策略")
        assert done.reply == "两个问题都确认完毕。"
        assert engine.has_pending_question() is False
        assert engine._route_skills.await_count == 1

        tool_msgs = [m for m in engine.memory.get_messages() if m.get("role") == "tool"]
        assert any(m.get("tool_call_id") == "call_q1" for m in tool_msgs)
        q2_msg = next(m for m in tool_msgs if m.get("tool_call_id") == "call_q2")
        q2_payload = json.loads(q2_msg["content"])
        assert q2_payload["multi_select"] is True
        assert any(item["label"] == "方案A" for item in q2_payload["selected_options"])
        assert q2_payload["other_text"] == "自定义策略"

    @pytest.mark.asyncio
    async def test_pending_question_blocks_slash_command(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)

        route_result = SkillMatchResult(
            skills_used=[],
            tool_scope=["ask_user"],
            route_mode="llm_confirm",
            system_contexts=[],
        )
        engine._route_skills = AsyncMock(return_value=route_result)

        ask_response = _make_tool_call_response(
            [
                (
                    "call_q1",
                    "ask_user",
                    json.dumps(self._ask_question_payload(), ensure_ascii=False),
                )
            ]
        )
        final_response = _make_text_response("已恢复执行。")
        engine._client.chat.completions.create = AsyncMock(
            side_effect=[ask_response, final_response]
        )

        first = await engine.chat("发起提问")
        assert engine.has_pending_question() is True
        assert "请先回答这个问题后再继续" in first.reply

        blocked = await engine.chat("/help")
        assert "请先回答后再使用命令" in blocked.reply
        assert engine.has_pending_question() is True
        # 待回答状态不触发重路由
        assert engine._route_skills.await_count == 1

        resumed = await engine.chat("1")
        assert resumed.reply == "已恢复执行。"
        assert engine.has_pending_question() is False

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
        engine._subagent_registry = MagicMock()
        engine._subagent_registry.build_catalog.return_value = (
            "可用子代理：\n- folder_summarizer：目录总结",
            ["folder_summarizer"],
        )

        meta_tools = engine._build_meta_tools()
        assert len(meta_tools) == 4
        by_name = {tool["function"]["name"]: tool for tool in meta_tools}
        assert "select_skill" in by_name
        assert "delegate_to_subagent" in by_name
        assert "list_subagents" in by_name
        assert "ask_user" in by_name

        select_tool = by_name["select_skill"]["function"]
        select_params = select_tool["parameters"]
        assert "Skill_Catalog" in select_tool["description"]
        assert select_params["required"] == ["skill_name"]
        assert select_params["properties"]["skill_name"]["enum"] == [
            "data_basic",
            "chart_basic",
        ]
        assert "reason" in select_params["properties"]

        delegate_tool = by_name["delegate_to_subagent"]["function"]
        delegate_params = delegate_tool["parameters"]
        assert delegate_params["required"] == ["task"]
        assert delegate_params["properties"]["file_paths"]["type"] == "array"
        assert "agent_name" in delegate_params["properties"]
        assert delegate_params["properties"]["agent_name"]["enum"] == ["folder_summarizer"]
        assert "Subagent_Catalog" in delegate_tool["description"]
        assert "folder_summarizer" in delegate_tool["description"]

        ask_user_tool = by_name["ask_user"]["function"]
        ask_user_params = ask_user_tool["parameters"]
        assert ask_user_params["required"] == ["question"]
        question_schema = ask_user_params["properties"]["question"]
        assert question_schema["required"] == ["text", "header", "options"]
        assert question_schema["properties"]["options"]["minItems"] == 2
        assert question_schema["properties"]["options"]["maxItems"] == 4

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


class TestFallbackScopeGuard:
    """fallback/slash_not_found 场景下工具权限收敛。"""

    def test_get_current_tool_scope_for_fallback_adds_only_meta_tools(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)

        route_result = SkillMatchResult(
            skills_used=[],
            tool_scope=["list_skills"],
            route_mode="fallback",
            system_contexts=[],
        )
        scope = engine._get_current_tool_scope(route_result=route_result)
        assert "list_skills" in scope
        assert "select_skill" in scope
        assert "delegate_to_subagent" in scope
        assert "list_subagents" in scope
        assert "ask_user" in scope
        assert "add_numbers" not in scope

    @pytest.mark.asyncio
    async def test_fallback_blocks_tool_until_select_skill_then_allows(self) -> None:
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
            tool_scope=["list_skills"],
            route_mode="fallback",
            system_contexts=[],
        )
        initial_scope = engine._get_current_tool_scope(route_result=route_result)
        forbidden_call = SimpleNamespace(
            id="call_1",
            function=SimpleNamespace(
                name="add_numbers",
                arguments=json.dumps({"a": 1, "b": 2}),
            ),
        )
        forbidden_result = await engine._execute_tool_call(
            tc=forbidden_call,
            tool_scope=initial_scope,
            on_event=None,
            iteration=1,
        )
        assert forbidden_result.success is False
        error_payload = json.loads(forbidden_result.result)
        assert error_payload["error_code"] == "TOOL_NOT_ALLOWED"
        assert error_payload["tool"] == "add_numbers"

        await engine._handle_select_skill("data_basic")
        upgraded_scope = engine._get_current_tool_scope(route_result=route_result)
        assert "add_numbers" in upgraded_scope

        allowed_call = SimpleNamespace(
            id="call_2",
            function=SimpleNamespace(
                name="add_numbers",
                arguments=json.dumps({"a": 1, "b": 2}),
            ),
        )
        allowed_result = await engine._execute_tool_call(
            tc=allowed_call,
            tool_scope=upgraded_scope,
            on_event=None,
            iteration=1,
        )
        assert allowed_result.success is True
        assert allowed_result.result == "3"

    @pytest.mark.asyncio
    async def test_chat_last_route_scope_matches_effective_scope(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)

        route_result = SkillMatchResult(
            skills_used=[],
            tool_scope=["list_skills"],
            route_mode="fallback",
            system_contexts=[],
        )
        engine._route_skills = AsyncMock(return_value=route_result)
        engine._client.chat.completions.create = AsyncMock(
            return_value=_make_text_response("ok")
        )

        result = await engine.chat("请分析这个文件")
        assert result == "ok"
        scope = engine.last_route_result.tool_scope
        assert "list_skills" in scope
        assert "select_skill" in scope
        assert "delegate_to_subagent" in scope
        assert "list_subagents" in scope
        assert "ask_user" in scope
        assert "add_numbers" not in scope


class TestMCPScopeSelector:
    """MCP 工具授权选择器展开。"""

    @staticmethod
    def _register_mcp_test_tool(
        registry: ToolRegistry,
        *,
        server: str = "context7",
        tool: str = "query_docs",
        result: str = "mcp-ok",
    ) -> str:
        mcp_tool = add_tool_prefix(server, tool)
        registry.register_tool(
            ToolDef(
                name=mcp_tool,
                description="mcp-test-tool",
                input_schema={"type": "object", "properties": {}},
                func=lambda: result,
            )
        )
        return mcp_tool

    @pytest.mark.parametrize(
        ("route_mode", "tool_scope"),
        [
            ("fallback", ["list_skills"]),
            ("slash_not_found", ["list_skills"]),
            ("no_skillpack", ["list_skills"]),
            ("slash_direct", ["add_numbers"]),
        ],
    )
    def test_get_current_tool_scope_includes_mcp_for_all_route_modes(
        self,
        route_mode: str,
        tool_scope: list[str],
    ) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        mcp_tool = self._register_mcp_test_tool(registry)
        engine = AgentEngine(config, registry)

        route_result = SkillMatchResult(
            skills_used=[],
            tool_scope=tool_scope,
            route_mode=route_mode,
            system_contexts=[],
        )
        scope = engine._get_current_tool_scope(route_result=route_result)
        assert mcp_tool in scope

    def test_get_current_tool_scope_includes_mcp_when_active_skill(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        mcp_tool = self._register_mcp_test_tool(registry)
        engine = AgentEngine(config, registry)
        engine._active_skill = Skillpack(
            name="data_basic",
            description="active skill",
            allowed_tools=["add_numbers"],
            triggers=[],
            instructions="test",
            source="project",
            root_dir="/tmp/active_skill",
        )

        scope = engine._get_current_tool_scope(
            route_result=SkillMatchResult(
                skills_used=["data_basic"],
                tool_scope=["add_numbers"],
                route_mode="fallback",
                system_contexts=[],
            )
        )
        assert "add_numbers" in scope
        assert mcp_tool in scope

    def test_scope_expands_mcp_all_selector(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        mcp_tool_a = add_tool_prefix("context7", "query_docs")
        mcp_tool_b = add_tool_prefix("filesystem", "read_file")
        registry.register_tools(
            [
                ToolDef(
                    name=mcp_tool_a,
                    description="mcp-a",
                    input_schema={"type": "object", "properties": {}},
                    func=lambda: "ok-a",
                ),
                ToolDef(
                    name=mcp_tool_b,
                    description="mcp-b",
                    input_schema={"type": "object", "properties": {}},
                    func=lambda: "ok-b",
                ),
            ]
        )
        engine = AgentEngine(config, registry)
        engine._full_access_enabled = True

        route_result = SkillMatchResult(
            skills_used=["mcp_skill"],
            tool_scope=["mcp:*"],
            route_mode="slash_direct",
            system_contexts=[],
        )
        scope = engine._get_current_tool_scope(route_result=route_result)
        assert mcp_tool_a in scope
        assert mcp_tool_b in scope
        assert "select_skill" in scope

    def test_scope_expands_server_level_mcp_selector(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        context_tool_a = add_tool_prefix("context7", "query_docs")
        context_tool_b = add_tool_prefix("context7", "resolve_library_id")
        fs_tool = add_tool_prefix("filesystem", "read_file")
        registry.register_tools(
            [
                ToolDef(
                    name=context_tool_a,
                    description="context-a",
                    input_schema={"type": "object", "properties": {}},
                    func=lambda: "ok-a",
                ),
                ToolDef(
                    name=context_tool_b,
                    description="context-b",
                    input_schema={"type": "object", "properties": {}},
                    func=lambda: "ok-b",
                ),
                ToolDef(
                    name=fs_tool,
                    description="fs",
                    input_schema={"type": "object", "properties": {}},
                    func=lambda: "ok-c",
                ),
            ]
        )
        engine = AgentEngine(config, registry)

        route_result = SkillMatchResult(
            skills_used=["mcp_skill"],
            tool_scope=["mcp:context7:*"],
            route_mode="slash_direct",
            system_contexts=[],
        )
        scope = engine._get_current_tool_scope(route_result=route_result)
        assert context_tool_a in scope
        assert context_tool_b in scope
        # 破坏性重构后，MCP 工具全场景全量注入 scope。
        assert fs_tool in scope

    @pytest.mark.asyncio
    async def test_execute_tool_call_accepts_expanded_mcp_selector(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        context_tool = add_tool_prefix("context7", "query_docs")
        registry.register_tool(
            ToolDef(
                name=context_tool,
                description="context-tool",
                input_schema={"type": "object", "properties": {}},
                func=lambda: "mcp-ok",
            )
        )
        engine = AgentEngine(config, registry)
        engine._full_access_enabled = True

        route_result = SkillMatchResult(
            skills_used=["mcp_skill"],
            tool_scope=["mcp:context7:query_docs"],
            route_mode="slash_direct",
            system_contexts=[],
        )
        scope = engine._get_current_tool_scope(route_result=route_result)

        call = SimpleNamespace(
            id="call_mcp",
            function=SimpleNamespace(name=context_tool, arguments=json.dumps({})),
        )
        result = await engine._execute_tool_call(
            tc=call,
            tool_scope=scope,
            on_event=None,
            iteration=1,
        )
        assert result.success is True
        assert result.result == "mcp-ok"

    @pytest.mark.asyncio
    async def test_non_whitelist_mcp_still_requires_pending_approval(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        mcp_tool = self._register_mcp_test_tool(registry)
        engine = AgentEngine(config, registry)

        scope = engine._get_current_tool_scope(
            route_result=SkillMatchResult(
                skills_used=[],
                tool_scope=["list_skills"],
                route_mode="fallback",
                system_contexts=[],
            )
        )
        call = SimpleNamespace(
            id="call_mcp_pending",
            function=SimpleNamespace(name=mcp_tool, arguments=json.dumps({})),
        )
        result = await engine._execute_tool_call(
            tc=call,
            tool_scope=scope,
            on_event=None,
            iteration=1,
        )

        assert result.success is True
        assert result.pending_approval is True
        assert engine._approval.pending is not None

    @pytest.mark.asyncio
    async def test_whitelist_mcp_executes_without_fullaccess(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        mcp_tool = self._register_mcp_test_tool(registry)
        engine = AgentEngine(config, registry)
        engine._approval.register_mcp_auto_approve([mcp_tool])

        scope = engine._get_current_tool_scope(
            route_result=SkillMatchResult(
                skills_used=[],
                tool_scope=["list_skills"],
                route_mode="fallback",
                system_contexts=[],
            )
        )
        call = SimpleNamespace(
            id="call_mcp_auto",
            function=SimpleNamespace(name=mcp_tool, arguments=json.dumps({})),
        )
        result = await engine._execute_tool_call(
            tc=call,
            tool_scope=scope,
            on_event=None,
            iteration=1,
        )

        assert result.success is True
        assert result.pending_approval is False
        assert result.result == "mcp-ok"
        assert engine._approval.pending is None


class TestSkillMCPRequirements:
    """Skill 的 MCP 依赖校验。"""

    @pytest.mark.asyncio
    async def test_select_skill_rejects_when_required_mcp_server_missing(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)

        skill = Skillpack(
            name="need_mcp",
            description="依赖外部 MCP",
            allowed_tools=["mcp:context7:*"],
            triggers=[],
            instructions="调用 context7",
            source="project",
            root_dir="/tmp/need_mcp",
            required_mcp_servers=["context7"],
        )
        mock_loader = MagicMock()
        mock_loader.get_skillpacks.return_value = {"need_mcp": skill}
        mock_router = MagicMock()
        mock_router._loader = mock_loader
        mock_router._find_skill_by_name = MagicMock(return_value=skill)
        engine._skill_router = mock_router

        result = await engine._handle_select_skill("need_mcp")

        assert "MCP 依赖未满足" in result
        assert engine._active_skill is None

    @pytest.mark.asyncio
    async def test_select_skill_accepts_when_required_mcp_server_and_tool_ready(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        mcp_tool = add_tool_prefix("context7", "query_docs")
        registry.register_tool(
            ToolDef(
                name=mcp_tool,
                description="文档查询",
                input_schema={"type": "object", "properties": {}},
                func=lambda: "ok",
            )
        )
        engine = AgentEngine(config, registry)
        engine._mcp_manager._clients["context7"] = MagicMock()

        skill = Skillpack(
            name="need_mcp",
            description="依赖外部 MCP",
            allowed_tools=["mcp:context7:*"],
            triggers=[],
            instructions="调用 context7",
            source="project",
            root_dir="/tmp/need_mcp",
            required_mcp_servers=["context7"],
            required_mcp_tools=["context7:query_docs"],
        )
        mock_loader = MagicMock()
        mock_loader.get_skillpacks.return_value = {"need_mcp": skill}
        mock_router = MagicMock()
        mock_router._loader = mock_loader
        mock_router._find_skill_by_name = MagicMock(return_value=skill)
        engine._skill_router = mock_router

        result = await engine._handle_select_skill("need_mcp")

        assert result.startswith("OK")
        assert engine._active_skill is not None
        assert engine._active_skill.name == "need_mcp"


class TestCommandDispatchAndHooks:
    @pytest.mark.asyncio
    async def test_command_dispatch_maps_plain_args(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        registry.register_tool(
            ToolDef(
                name="echo_tool",
                description="回显",
                input_schema={
                    "type": "object",
                    "properties": {"input": {"type": "string"}},
                    "required": ["input"],
                },
                func=lambda input: input,
            )
        )
        engine = AgentEngine(config, registry)
        engine._full_access_enabled = True
        skill = Skillpack(
            name="echo",
            description="命令分发",
            allowed_tools=["echo_tool"],
            triggers=[],
            instructions="回显输入",
            source="project",
            root_dir="/tmp/echo",
            command_dispatch="tool",
            command_tool="echo_tool",
        )
        route_result = SkillMatchResult(
            skills_used=["echo"],
            tool_scope=["echo_tool"],
            route_mode="slash_direct",
            system_contexts=[],
        )

        result = await engine._run_command_dispatch_skill(
            skill=skill,
            raw_args="hello-dispatch",
            route_result=route_result,
            on_event=None,
        )
        assert result.reply == "hello-dispatch"
        assert result.tool_calls
        assert result.tool_calls[0].success is True

    @pytest.mark.asyncio
    async def test_pre_tool_hook_deny_blocks_tool(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        engine._active_skill = Skillpack(
            name="hook/deny",
            description="deny hook",
            allowed_tools=["add_numbers"],
            triggers=[],
            instructions="",
            source="project",
            root_dir="/tmp/hook",
            hooks={
                "PreToolUse": [
                    {
                        "matcher": "add_numbers",
                        "hooks": [{"type": "prompt", "decision": "deny", "reason": "blocked"}],
                    }
                ]
            },
        )

        tc = SimpleNamespace(
            id="call_hook_deny",
            function=SimpleNamespace(name="add_numbers", arguments=json.dumps({"a": 1, "b": 2})),
        )
        result = await engine._execute_tool_call(
            tc=tc,
            tool_scope=["add_numbers"],
            on_event=None,
            iteration=1,
        )
        assert result.success is False
        assert "blocked" in result.result

    @pytest.mark.asyncio
    async def test_pre_tool_hook_ask_creates_pending_approval(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        engine._active_skill = Skillpack(
            name="hook/ask",
            description="ask hook",
            allowed_tools=["add_numbers"],
            triggers=[],
            instructions="",
            source="project",
            root_dir="/tmp/hook",
            hooks={
                "PreToolUse": [
                    {
                        "matcher": "add_numbers",
                        "hooks": [{"type": "prompt", "decision": "ask"}],
                    }
                ]
            },
        )

        tc = SimpleNamespace(
            id="call_hook_ask",
            function=SimpleNamespace(name="add_numbers", arguments=json.dumps({"a": 1, "b": 2})),
        )
        result = await engine._execute_tool_call(
            tc=tc,
            tool_scope=["add_numbers"],
            on_event=None,
            iteration=1,
        )
        assert result.success is True
        assert result.pending_approval is True
        assert isinstance(result.approval_id, str) and result.approval_id

    @pytest.mark.asyncio
    async def test_pre_tool_hook_updated_input_is_applied(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        engine._active_skill = Skillpack(
            name="hook/update",
            description="update input hook",
            allowed_tools=["add_numbers"],
            triggers=[],
            instructions="",
            source="project",
            root_dir="/tmp/hook",
            hooks={
                "PreToolUse": [
                    {
                        "matcher": "add_numbers",
                        "hooks": [
                            {
                                "type": "prompt",
                                "decision": "allow",
                                "updated_input": {"a": 7, "b": 4},
                            }
                        ],
                    }
                ]
            },
        )

        tc = SimpleNamespace(
            id="call_hook_update",
            function=SimpleNamespace(name="add_numbers", arguments=json.dumps({"a": 1, "b": 2})),
        )
        result = await engine._execute_tool_call(
            tc=tc,
            tool_scope=["add_numbers"],
            on_event=None,
            iteration=1,
        )
        assert result.success is True
        assert result.result == "11"

    @pytest.mark.asyncio
    async def test_pre_tool_hook_allow_skips_pending_approval_for_high_risk(
        self,
        tmp_path: Path,
    ) -> None:
        config = _make_config(workspace_root=str(tmp_path))
        registry = _make_registry_with_tools()

        def write_text_file(file_path: str, content: str) -> str:
            Path(file_path).write_text(content, encoding="utf-8")
            return "ok"

        registry.register_tool(
            ToolDef(
                name="write_text_file",
                description="写入文本",
                input_schema={
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["file_path", "content"],
                },
                func=write_text_file,
            )
        )
        engine = AgentEngine(config, registry)
        engine._active_skill = Skillpack(
            name="hook/allow",
            description="allow hook",
            allowed_tools=["write_text_file"],
            triggers=[],
            instructions="",
            source="project",
            root_dir="/tmp/hook",
            hooks={
                "PreToolUse": [
                    {
                        "matcher": "write_text_file",
                        "hooks": [{"type": "prompt", "decision": "allow"}],
                    }
                ]
            },
        )

        output = tmp_path / "hook_allow.txt"
        tc = SimpleNamespace(
            id="call_hook_allow",
            function=SimpleNamespace(
                name="write_text_file",
                arguments=json.dumps({"file_path": str(output), "content": "ok"}),
            ),
        )
        result = await engine._execute_tool_call(
            tc=tc,
            tool_scope=["write_text_file"],
            on_event=None,
            iteration=1,
        )
        assert result.success is True
        assert result.pending_approval is False
        assert output.exists()

    def test_non_pre_tool_ask_downgrades_to_continue(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        skill = Skillpack(
            name="hook/ask_scope",
            description="ask scope",
            allowed_tools=["add_numbers"],
            triggers=[],
            instructions="",
            source="project",
            root_dir="/tmp/hook",
            hooks={
                "UserPromptSubmit": {
                    "type": "prompt",
                    "decision": "ask",
                    "reason": "需要确认",
                }
            },
        )

        result = engine._run_skill_hook(
            skill=skill,
            event=HookEvent.USER_PROMPT_SUBMIT,
            payload={"user_message": "测试"},
        )
        assert result is not None
        assert result.decision == HookDecision.CONTINUE
        assert "不支持 ASK" in result.reason

    @pytest.mark.asyncio
    async def test_pre_tool_agent_hook_runs_subagent_and_injects_context(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        engine.run_subagent = AsyncMock(
            return_value=SubagentResult(
                success=True,
                summary="子代理摘要",
                subagent_name="explorer",
                permission_mode="default",
                conversation_id="sub_1",
            )
        )
        engine._active_skill = Skillpack(
            name="hook/agent",
            description="agent hook",
            allowed_tools=["add_numbers"],
            triggers=[],
            instructions="",
            source="project",
            root_dir="/tmp/hook",
            hooks={
                "PreToolUse": [
                    {
                        "matcher": "add_numbers",
                        "hooks": [
                            {
                                "type": "agent",
                                "agent_name": "explorer",
                                "task": "请检查调用参数",
                                "inject_summary_as_context": True,
                            }
                        ],
                    }
                ]
            },
        )

        tc = SimpleNamespace(
            id="call_hook_agent",
            function=SimpleNamespace(
                name="add_numbers",
                arguments=json.dumps({"a": 1, "b": 2}),
            ),
        )
        result = await engine._execute_tool_call(
            tc=tc,
            tool_scope=["add_numbers"],
            on_event=None,
            iteration=1,
        )
        assert result.success is True
        assert result.result == "3"
        engine.run_subagent.assert_awaited_once()
        assert any("子代理摘要" in item for item in engine._transient_hook_contexts)

    @pytest.mark.asyncio
    async def test_agent_hook_recursion_guard_respects_on_failure_deny(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        engine._hook_agent_action_depth = 1

        resolved = await engine._resolve_hook_result(
            event=HookEvent.PRE_TOOL_USE,
            hook_result=HookResult(
                decision=HookDecision.CONTINUE,
                agent_action=HookAgentAction(task="递归测试", on_failure="deny"),
            ),
            on_event=None,
        )
        assert resolved is not None
        assert resolved.decision == HookDecision.DENY
        assert "递归触发" in resolved.reason


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
        assert isinstance(result, ChatResult)
        assert result == "你好，这是回复。"
        assert result.reply == "你好，这是回复。"
        assert result.iterations == 1
        assert result.truncated is False
        assert result.tool_calls == []

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

    @pytest.mark.asyncio
    async def test_string_response_is_treated_as_text_reply(self) -> None:
        """兼容某些网关直接返回纯字符串。"""
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)

        engine._client.chat.completions.create = AsyncMock(return_value="你好，字符串响应。")

        result = await engine.chat("你好")
        assert isinstance(result, ChatResult)
        assert result.reply == "你好，字符串响应。"
        assert result.tool_calls == []
        assert result.iterations == 1
        assert result.truncated is False

    @pytest.mark.asyncio
    async def test_html_document_response_returns_endpoint_hint(self) -> None:
        """当上游返回 HTML 页面时，返回可操作的配置提示。"""
        config = _make_config(base_url="https://example.invalid/")
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)

        engine._client.chat.completions.create = AsyncMock(
            return_value="<!doctype html><html><head><meta charset='utf-8'></head><body>oops</body></html>"
        )

        result = await engine.chat("你是谁")
        assert "EXCELMANUS_BASE_URL" in result.reply
        assert "/v1" in result.reply
        assert "<!doctype html>" not in result.reply.lower()


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
        assert isinstance(result, ChatResult)
        assert result == "3 + 5 = 8"
        assert result.reply == "3 + 5 = 8"
        assert result.iterations == 2
        assert result.truncated is False
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].tool_name == "add_numbers"
        assert result.tool_calls[0].success is True

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
        assert result.truncated is True
        assert result.iterations == 3


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
            # 当前实现使用闭包封装 registry.call_tool，再提交给 to_thread。
            assert len(call_args.args) == 1
            assert callable(call_args.args[0])
            assert result == "结果是 15"


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
from hypothesis import given, assume
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
    engine._full_access_enabled = True

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
    engine._full_access_enabled = True

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

        # 不变量 2：每次调用都传入了可在线程池执行的可调用对象
        for call in mock_to_thread.call_args_list:
            assert len(call.args) == 1
            assert callable(call.args[0])

        # 不变量 3：流程可正常收敛到最终文本结果
        assert result == "完成"


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

    def _make_registry_with_audit_tool(self, workspace: Path) -> ToolRegistry:
        registry = ToolRegistry()

        def create_chart(output_path: str) -> str:
            target = workspace / output_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("chart", encoding="utf-8")
            return json.dumps({"status": "success", "output_path": output_path}, ensure_ascii=False)

        registry.register_tools([
            ToolDef(
                name="create_chart",
                description="生成图表文件",
                input_schema={
                    "type": "object",
                    "properties": {
                        "output_path": {"type": "string"},
                    },
                    "required": ["output_path"],
                },
                func=create_chart,
            ),
        ])
        return registry

    def _make_registry_with_failing_write_tool(self, workspace: Path) -> ToolRegistry:
        registry = ToolRegistry()

        def write_text_file(file_path: str, content: str) -> str:
            target = workspace / file_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            raise RuntimeError("intentional_write_failure")

        registry.register_tools([
            ToolDef(
                name="write_text_file",
                description="写文件后抛错",
                input_schema={
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["file_path", "content"],
                },
                func=write_text_file,
            ),
        ])
        return registry

    def _make_registry_with_custom_tool(self) -> ToolRegistry:
        registry = ToolRegistry()

        def custom_tool() -> str:
            return "custom-ok"

        registry.register_tools([
            ToolDef(
                name="custom_tool",
                description="自定义工具",
                input_schema={"type": "object", "properties": {}},
                func=custom_tool,
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
    async def test_failed_accept_still_writes_failed_manifest(self, tmp_path: Path) -> None:
        config = _make_config(workspace_root=str(tmp_path))
        registry = self._make_registry_with_failing_write_tool(tmp_path)
        engine = AgentEngine(config, registry)

        tool_response = _make_tool_call_response([
            ("call_1", "write_text_file", json.dumps({"file_path": "err.txt", "content": "x"}))
        ])
        engine._client.chat.completions.create = AsyncMock(side_effect=[tool_response])
        await engine.chat("写文件")
        assert engine._approval.pending is not None
        approval_id = engine._approval.pending.approval_id

        accept_reply = await engine.chat(f"/accept {approval_id}")
        assert "accept 执行失败" in accept_reply
        manifest_path = tmp_path / "outputs" / "approvals" / approval_id / "manifest.json"
        assert manifest_path.exists()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["execution"]["status"] == "failed"
        assert manifest["execution"]["error_type"] == "ToolExecutionError"

    @pytest.mark.asyncio
    async def test_undo_after_restart_loads_manifest(self, tmp_path: Path) -> None:
        config = _make_config(workspace_root=str(tmp_path))
        registry = self._make_registry_with_write_tool(tmp_path)
        engine1 = AgentEngine(config, registry)

        tool_response = _make_tool_call_response([
            ("call_1", "write_text_file", json.dumps({"file_path": "restart.txt", "content": "v"}))
        ])
        engine1._client.chat.completions.create = AsyncMock(side_effect=[tool_response])
        await engine1.chat("写文件")
        assert engine1._approval.pending is not None
        approval_id = engine1._approval.pending.approval_id
        await engine1.chat(f"/accept {approval_id}")
        assert (tmp_path / "restart.txt").exists()

        engine2 = AgentEngine(config, registry)
        undo_reply = await engine2.chat(f"/undo {approval_id}")
        assert "已回滚" in undo_reply
        assert not (tmp_path / "restart.txt").exists()

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

    @pytest.mark.asyncio
    async def test_default_mode_non_whitelist_tool_executes_directly(self, tmp_path: Path) -> None:
        config = _make_config(workspace_root=str(tmp_path))
        registry = self._make_registry_with_custom_tool()
        engine = AgentEngine(config, registry)
        engine._active_skill = None

        tool_response = _make_tool_call_response([("call_1", "custom_tool", "{}")])
        text_response = _make_text_response("完成")
        engine._client.chat.completions.create = AsyncMock(
            side_effect=[tool_response, text_response]
        )

        reply = await engine.chat("执行自定义工具")
        assert reply == "完成"
        assert engine._approval.pending is None

    @pytest.mark.asyncio
    async def test_fullaccess_executes_non_whitelist_tool(self, tmp_path: Path) -> None:
        config = _make_config(workspace_root=str(tmp_path))
        registry = self._make_registry_with_custom_tool()
        engine = AgentEngine(config, registry)
        engine._active_skill = Skillpack(
            name="test/custom",
            description="test",
            allowed_tools=["custom_tool"],
            triggers=[],
            instructions="",
            source="project",
            root_dir=str(tmp_path),
        )

        on_reply = await engine.chat("/fullAccess on")
        assert "已开启" in on_reply

        tool_response = _make_tool_call_response([("call_1", "custom_tool", "{}")])
        text_response = _make_text_response("完成")
        engine._client.chat.completions.create = AsyncMock(
            side_effect=[tool_response, text_response]
        )

        reply = await engine.chat("执行自定义工具")
        assert reply == "完成"
        assert engine._approval.pending is None

    @pytest.mark.asyncio
    async def test_default_mode_audit_only_tool_executes_without_accept(self, tmp_path: Path) -> None:
        config = _make_config(workspace_root=str(tmp_path))
        registry = self._make_registry_with_audit_tool(tmp_path)
        engine = AgentEngine(config, registry)
        engine._execute_tool_with_audit = AsyncMock(return_value=('{"status":"success"}', None))

        tool_response = _make_tool_call_response([
            ("call_1", "create_chart", json.dumps({"output_path": "charts/a.png"}))
        ])
        text_response = _make_text_response("完成")
        engine._client.chat.completions.create = AsyncMock(
            side_effect=[tool_response, text_response]
        )

        reply = await engine.chat("生成图表")
        assert reply == "完成"
        assert engine._approval.pending is None
        engine._execute_tool_with_audit.assert_awaited_once()
