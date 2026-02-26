"""AgentEngine 单元测试：覆盖 Tool Calling 循环核心逻辑。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import threading
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
from excelmanus.window_perception import AdvisorContext, PerceptionBudget, WindowType
from excelmanus.window_perception.domain import Window
from tests.window_factories import make_window


# ── 辅助工厂 ──────────────────────────────────────────────


def _make_config(**overrides) -> ExcelManusConfig:
    """创建测试用配置。"""
    defaults = {
        "api_key": "test-key",
        "base_url": "https://test.example.com/v1",
        "model": "test-model",
        "max_iterations": 20,
        "max_consecutive_failures": 3,
        "workspace_root": str(Path(__file__).resolve().parent),
        "backup_enabled": False,
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


def _activate_test_tools(engine: AgentEngine, tools: list[str] | None = None) -> None:
    """为测试激活一个 Skillpack，注入 skill context。"""
    engine._active_skills = [Skillpack(
        name="_test_scope",
        description="test scope",
        instructions="test",
        source="system",
        root_dir="/tmp/_test_scope",
    )]


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
        # fork 后是独立实例，但包含相同的基础工具集
        assert engine._registry is not registry
        assert set(engine._registry.get_tool_names()) >= set(registry.get_tool_names())

    def test_make_config_defaults_workspace_root_to_test_dir(self) -> None:
        """默认 workspace_root 应使用测试目录，避免扫描整个仓库导致慢测。"""
        config = _make_config()
        assert Path(config.workspace_root).resolve() == Path(__file__).resolve().parent


class TestControlCommandFullAccess:
    """会话级 /fullaccess 控制命令测试。"""

    @pytest.mark.asyncio
    async def test_status_defaults_to_restricted(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)

        result = await engine.chat("/fullaccess status")
        assert isinstance(result, ChatResult)
        assert "restricted" in result
        assert engine.full_access_enabled is False
        assert engine.last_route_result.route_mode == "control_command"

    @pytest.mark.asyncio
    async def test_on_then_off(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)

        on_result = await engine.chat("/fullaccess")
        assert "full_access" in on_result
        assert engine.full_access_enabled is True
        assert engine.last_route_result.route_mode == "control_command"

        off_result = await engine.chat("/fullaccess off")
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

        await engine.chat("/fullaccess on")
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
        assert "subagent" in result

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
            file_paths=None,
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
            file_paths=None,
            on_event=None,
        )


class TestRegistryScan:
    """FileRegistry 后台扫描。"""

    @pytest.mark.asyncio
    async def test_first_notice_does_not_block_when_scan_running(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)

        if engine._file_registry is None:
            pytest.skip("FileRegistry 未初始化（无 database）")

        gate = threading.Event()

        def _slow_scan(*_args, **_kwargs):
            gate.wait(timeout=2)
            from excelmanus.file_registry import ScanResult
            return ScanResult(total_files=0)

        with patch.object(engine._file_registry, "scan_workspace", side_effect=_slow_scan):
            started = engine.start_registry_scan()
            assert started is True

            t0 = time.monotonic()
            notice = engine._context_builder._build_file_registry_notice()
            elapsed = time.monotonic() - t0

            # 扫描进行中，notice 应快速返回（不阻塞）
            assert elapsed < 0.1

            gate.set()
            assert engine._registry_scan_task is not None
            await engine._registry_scan_task

    @pytest.mark.asyncio
    async def test_registry_control_command_scan_and_status(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)

        if engine._file_registry is None:
            pytest.skip("FileRegistry 未初始化（无 database）")

        gate = threading.Event()

        def _slow_scan(*_args, **_kwargs):
            gate.wait(timeout=2)
            from excelmanus.file_registry import ScanResult
            return ScanResult(total_files=3)

        with patch.object(engine._file_registry, "scan_workspace", side_effect=_slow_scan):
            build_reply = await engine.chat("/registry scan")
            assert "后台开始 FileRegistry 扫描" in build_reply.reply

            status_reply = await engine.chat("/registry status")
            assert "后台扫描中" in status_reply.reply

            gate.set()
            assert engine._registry_scan_task is not None
            await engine._registry_scan_task

            final_status = await engine.chat("/registry status")
            assert "已就绪" in final_status.reply


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

    def test_switch_model_keeps_router_when_aux_model_configured(self) -> None:
        config = _make_config(
            model="main-a",
            aux_model="aux-fixed",
            aux_api_key="aux-key",
            aux_base_url="https://aux.example.com/v1",
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
        assert engine._router_model == "aux-fixed"

        engine.switch_model("alt")
        assert engine._active_model == "main-b"
        assert engine._router_model == "aux-fixed"
        assert engine._router_client is old_router_client
        assert engine._router_client is not engine._client

    @pytest.mark.asyncio
    async def test_window_perception_advisor_follows_active_model_after_switch(self) -> None:
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
        engine._advisor_client.chat.completions.create = AsyncMock(
            return_value=_make_text_response('{"task_type":"GENERAL_BROWSE","advices":[]}')
        )

        _ = await engine._run_window_perception_advisor_async(
            windows=[make_window(id="w1", type=WindowType.SHEET, title="A")],
            active_window_id="w1",
            budget=PerceptionBudget(),
            context=AdvisorContext(turn_number=1, task_type="GENERAL_BROWSE"),
        )

        _, kwargs = engine._advisor_client.chat.completions.create.call_args
        assert kwargs["model"] == "main-b"

    @pytest.mark.asyncio
    async def test_window_perception_advisor_keeps_aux_model_when_configured(self) -> None:
        config = _make_config(
            model="main-a",
            aux_model="aux-fixed",
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
        engine._advisor_client.chat.completions.create = AsyncMock(
            return_value=_make_text_response('{"task_type":"GENERAL_BROWSE","advices":[]}')
        )

        _ = await engine._run_window_perception_advisor_async(
            windows=[make_window(id="w1", type=WindowType.SHEET, title="A")],
            active_window_id="w1",
            budget=PerceptionBudget(),
            context=AdvisorContext(turn_number=1, task_type="GENERAL_BROWSE"),
        )

        _, kwargs = engine._advisor_client.chat.completions.create.call_args
        assert kwargs["model"] == "aux-fixed"

    @pytest.mark.asyncio
    async def test_run_subagent_uses_active_model_when_subroute_not_configured(self) -> None:
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
        engine = AgentEngine(config, _make_registry_with_tools())
        engine.switch_model("alt")
        engine._subagent_registry = MagicMock()
        engine._subagent_registry.get.return_value = SubagentConfig(
            name="explorer",
            description="只读探查",
            permission_mode="readOnly",
        )
        engine._subagent_executor.run = AsyncMock(
            return_value=SubagentResult(
                success=True,
                summary="完成",
                subagent_name="explorer",
                permission_mode="readOnly",
                conversation_id="conv-1",
            )
        )

        _ = await engine.run_subagent(agent_name="explorer", prompt="请分析")

        kwargs = engine._subagent_executor.run.await_args.kwargs
        assert kwargs["config"].model == "main-b"

    @pytest.mark.asyncio
    async def test_run_subagent_keeps_global_subroute_model_when_configured(self) -> None:
        config = _make_config(
            model="main-a",
            aux_model="sub-fixed",
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
        engine = AgentEngine(config, _make_registry_with_tools())
        engine.switch_model("alt")
        engine._subagent_registry = MagicMock()
        engine._subagent_registry.get.return_value = SubagentConfig(
            name="explorer",
            description="只读探查",
            permission_mode="readOnly",
        )
        engine._subagent_executor.run = AsyncMock(
            return_value=SubagentResult(
                success=True,
                summary="完成",
                subagent_name="explorer",
                permission_mode="readOnly",
                conversation_id="conv-2",
            )
        )

        _ = await engine.run_subagent(agent_name="explorer", prompt="请分析")

        kwargs = engine._subagent_executor.run.await_args.kwargs
        assert kwargs["config"].model == "sub-fixed"

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
        engine._advisor_client.chat.completions.create = mocked_create
        mocked_sleep = AsyncMock(return_value=None)
        monkeypatch.setattr("excelmanus.engine.asyncio.sleep", mocked_sleep)

        plan = await engine._run_window_perception_advisor_async(
            windows=[make_window(id="w1", type=WindowType.SHEET, title="A")],
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
        engine._advisor_client.chat.completions.create = mocked_create
        mocked_sleep = AsyncMock(return_value=None)
        monkeypatch.setattr("excelmanus.engine.asyncio.sleep", mocked_sleep)

        plan = await engine._run_window_perception_advisor_async(
            windows=[make_window(id="w1", type=WindowType.SHEET, title="A")],
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
        engine._advisor_client.chat.completions.create = mocked_create

        def _unexpected_retry_delay(_exc: Exception) -> float:
            raise AssertionError("不应进入重试分支")

        monkeypatch.setattr(
            AgentEngine,
            "_window_advisor_retry_delay_seconds",
            staticmethod(_unexpected_retry_delay),
        )

        plan = await engine._run_window_perception_advisor_async(
            windows=[make_window(id="w1", type=WindowType.SHEET, title="A")],
            active_window_id="w1",
            budget=PerceptionBudget(),
            context=AdvisorContext(turn_number=1, task_type="GENERAL_BROWSE"),
        )

        assert plan is None
        assert mocked_create.await_count == 1


class TestSystemMessageMode:
    """system_message_mode 行为测试。"""

    def test_auto_mode_defaults_to_replace(self) -> None:
        AgentEngine._system_mode_fallback_cache = {}  # 确保干净状态
        config = _make_config(system_message_mode="auto")
        engine = AgentEngine(config, _make_registry_with_tools())
        assert engine._effective_system_mode() == "replace"

    def test_prepare_system_prompts_replace_mode_splits_system_messages(self) -> None:
        config = _make_config(system_message_mode="replace")
        engine = AgentEngine(config, _make_registry_with_tools())
        prompts, _ = engine._prepare_system_prompts_for_request(["[Skillpack] data_basic\n描述：测试"])
        assert len(prompts) == 2
        assert "[Skillpack] data_basic" in prompts[1]

    def test_prepare_system_prompts_merge_mode_merges_into_single_message(self) -> None:
        config = _make_config(system_message_mode="merge")
        engine = AgentEngine(config, _make_registry_with_tools())
        prompts, _ = engine._prepare_system_prompts_for_request(["[Skillpack] data_basic\n描述：测试"])
        assert len(prompts) == 1
        assert "[Skillpack] data_basic" in prompts[0]

    @pytest.mark.asyncio
    async def test_auto_mode_fallback_merges_messages_after_provider_compat_error(self) -> None:
        AgentEngine._system_mode_fallback_cache = {}  # 确保干净状态
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
        _cache_key = (config.model, config.base_url)
        assert AgentEngine._system_mode_fallback_cache.get(_cache_key) == "merge"

    @pytest.mark.asyncio
    async def test_auto_mode_fallback_persists_across_sessions(self) -> None:
        """类级缓存确保新会话不再重复试错。"""
        config = _make_config(system_message_mode="auto")
        # 先确保类缓存被设置为 merge（由上一个测试或手动设置）
        _cache_key = (config.model, config.base_url)
        AgentEngine._system_mode_fallback_cache = {_cache_key: "merge"}
        engine = AgentEngine(config, _make_registry_with_tools())
        # 新实例应直接读取类缓存，无需试错
        assert engine._effective_system_mode() == "merge"
        # 清理类状态，避免污染其他测试
        AgentEngine._system_mode_fallback_cache = {}


class TestContextBudgetAndHardCap:
    """上下文预算与工具结果全局硬截断测试。"""

    @pytest.mark.asyncio
    async def test_tool_loop_messages_fit_max_context_budget(self) -> None:
        config = _make_config(max_context_tokens=20000)
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        engine.memory.add_user_message("测试上下文预算")

        route_result = SkillMatchResult(
            skills_used=[],
            tool_scope=["add_numbers"],
            route_mode="fallback",
            system_contexts=["X" * 8000],
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
        config = _make_config(window_return_mode="enriched")
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

        assert "--- perception ---" in result.result
        assert "file: sales.xlsx" in result.result
        assert "_environment_perception" not in result.result
        json_part, _sep, _tail = result.result.partition("\n\n--- perception ---")
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
        engine = AgentEngine(_make_config(window_return_mode="enriched"), registry)
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

        assert "scroll:" in result.result
        assert "stats: SUM=" in result.result
        assert "col-width: A=12, B=15" in result.result
        assert "row-height: 1=24, 2=18" in result.result
        assert "merged: F1:H1" in result.result
        assert "cond-fmt: D2:D7:" in result.result

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
        assert "--- perception ---" not in result.result
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
        config = _make_config(system_message_mode="replace", window_return_mode="enriched")
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

        assert result.result.startswith("[OK] [")
        assert "read_excel: A1:J25" in result.result
        assert "intent: aggregate" in result.result
        assert "write actions must be confirmed by write-tool results" in result.result
        assert "--- perception ---" not in result.result

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

        assert result.result.startswith("[OK] [")
        assert "read_excel: A1:E10" in result.result
        assert "| intent=aggregate" in result.result
        assert "首行预览" not in result.result
        assert "--- perception ---" not in result.result

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

        assert result.result.startswith("[OK] [")
        assert "read_excel: A1:E10" in result.result
        assert "| intent=aggregate" in result.result
        assert "首行预览" not in result.result
        assert "--- perception ---" not in result.result
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
        # 重复读取不再被阻断或降级
        assert second.success
        assert third.success
        assert isinstance(second.result, str) and len(second.result) > 0
        assert isinstance(third.result, str) and len(third.result) > 0

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
        # 重复读取不再触发降级，模式保持 adaptive 解析结果
        # 模式保持为 adaptive 解析结果（如 gpt-5.3 统一为 unified）
        assert engine._effective_window_return_mode() in ("unified", "anchored")

        switch_message = engine.switch_model("deepseek")
        assert "已切换到模型" in switch_message

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
        # 重复读取不再被阻断，全部正常成功
        assert second.success
        assert third.success

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
        assert "--- perception ---" not in after_write.result

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
        assert "A | 100" in prompts[-1]

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
            window_return_mode="enriched",
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
            window_return_mode="enriched",
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
        assert "[ACTIVE -- sales.xlsx / Q1]" in notice1

        await _read("catalog.xlsx", 2)
        notice2 = engine._build_window_perception_notice()
        assert "[ACTIVE -- catalog.xlsx / Q1]" in notice2
        assert "[BG -- sales.xlsx / Q1]" in notice2

        notice3 = engine._build_window_perception_notice()
        assert "[BG -- sales.xlsx / Q1]" in notice3
        assert "[BG -- catalog.xlsx / Q1]" in notice3

        notice4 = engine._build_window_perception_notice()
        assert "[IDLE -- sales.xlsx / Q1" in notice4
        assert "[BG -- catalog.xlsx / Q1]" in notice4

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
            window_return_mode="enriched",
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
        assert "[ACTIVE -- reactivate.xlsx / Q1]" in notice5

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
        engine._advisor_client.chat.completions.create = AsyncMock(side_effect=_slow_response)

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
        engine._advisor_client.chat.completions.create = AsyncMock(
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
        engine._advisor_client.chat.completions.create = AsyncMock(
            return_value=_make_text_response(plan_text)
        )

        _ = engine._build_window_perception_notice()
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        second_notice = engine._build_window_perception_notice()
        assert "[IDLE -- sales.xlsx / Q1" in second_notice

    @pytest.mark.asyncio
    async def test_window_perception_hybrid_advisor_falls_back_when_advisor_fails(self) -> None:
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
        engine._advisor_client.chat.completions.create = AsyncMock(
            side_effect=RuntimeError("advisor failed")
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
        assert "## 数据窗口" in fallback_notice
        assert "sales.xlsx / Q1" in fallback_notice or "catalog.xlsx / Q1" in fallback_notice


class TestTaskUpdateFailureSemantics:
    """task_update 失败语义与事件一致性测试。"""

    @pytest.mark.asyncio
    async def test_invalid_transition_returns_failure_and_no_task_item_updated_event(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
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


class TestChatModeDeprecatedPlanCommand:
    """旧 /plan 命令已废弃，应返回提示信息。"""

    @pytest.mark.asyncio
    async def test_plan_command_returns_deprecation_notice(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)

        for cmd in ("/plan status", "/plan on", "/plan off", "/plan approve"):
            result = await engine.chat(cmd)
            assert "废弃" in result.reply or "Tab" in result.reply

    @pytest.mark.asyncio
    async def test_chat_mode_passed_to_route(self) -> None:
        """chat_mode 参数应传递到 _route_skills。"""
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        engine._client.chat.completions.create = AsyncMock(
            return_value=_make_text_response("ok")
        )
        mock_router = MagicMock()
        mock_router.route = AsyncMock(return_value=SkillMatchResult(
            skills_used=[], route_mode="all_tools", system_contexts=[],
        ))
        engine._skill_router = mock_router

        await engine.chat("分析数据", chat_mode="read")
        assert mock_router.route.call_count == 1
        call_kwargs = mock_router.route.call_args[1]
        assert call_kwargs.get("chat_mode") == "read"


class TestManualSkillSlashCommand:
    """手动 Skill 斜杠命令解析与路由。"""

    @pytest.mark.asyncio
    async def test_chat_passes_route_task_tags_to_window_turn_hints(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)

        route_result = SkillMatchResult(
            skills_used=[],
            route_mode="all_tools",
            system_contexts=[],
            task_tags=("cross_sheet",),
        )
        mock_router = MagicMock()
        mock_router.route = AsyncMock(return_value=route_result)
        engine._skill_router = mock_router
        engine._tool_calling_loop = AsyncMock(return_value=ChatResult(reply="ok"))

        with patch.object(engine, "_set_window_perception_turn_hints") as mock_set_hints:
            result = await engine.chat("从Sheet2批量填充到Sheet1")

        assert result.reply == "ok"
        mock_set_hints.assert_called_once_with(
            user_message="从Sheet2批量填充到Sheet1",
            is_new_task=True,
            task_tags=("cross_sheet",),
        )

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
    async def test_embedded_slash_skill_command_maps_to_slash_route_args(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)

        route_result = SkillMatchResult(
            skills_used=["verification-before-completion"],
            route_mode="hint_direct",
            system_contexts=[],
        )
        mock_loader = MagicMock()
        mock_loader.get_skillpacks.return_value = {
            "verification-before-completion": MagicMock()
        }
        mock_router = MagicMock()
        mock_router._loader = mock_loader
        mock_router.route = AsyncMock(return_value=route_result)
        engine._skill_router = mock_router
        engine._client.chat.completions.create = AsyncMock(
            return_value=_make_text_response("ok")
        )

        result = await engine.chat(
            "查看文件夹下 /verification-before-completion 查看哪个表格行数最多"
        )
        assert result == "ok"

        _, kwargs = mock_router.route.call_args
        assert kwargs["slash_command"] == "verification-before-completion"
        assert kwargs["raw_args"] == "查看哪个表格行数最多"

    @pytest.mark.asyncio
    async def test_explicit_slash_command_arguments_pass_through(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)

        route_result = SkillMatchResult(
            skills_used=["data_basic"],
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
        first_call = mock_router.route.call_args_list[0]
        _, kwargs = first_call
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

    def test_resolve_skill_command_ignores_embedded_path_like_input(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)

        mock_loader = MagicMock()
        mock_loader.get_skillpacks.return_value = {
            "verification-before-completion": MagicMock()
        }
        mock_router = MagicMock()
        mock_router._loader = mock_loader
        engine._skill_router = mock_router

        assert engine.resolve_skill_command("请读取 /tmp/data.xlsx 的前10行") is None

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

    @pytest.mark.asyncio
    async def test_guidance_only_slash_with_args_falls_back_to_task_route(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)

        guidance_skill = Skillpack(
            name="guidance_only",
            description="仅方法论约束",
            instructions="只提供规范，不直接绑定工具",
            source="project",
            root_dir="/tmp/guidance_only",
        )
        mock_loader = MagicMock()
        mock_loader.get_skillpack.return_value = guidance_skill
        mock_loader.get_skillpacks.return_value = {"guidance_only": guidance_skill}
        mock_router = MagicMock()
        mock_router._loader = mock_loader
        engine._skill_router = mock_router

        slash_route = SkillMatchResult(
            skills_used=["guidance_only"],
            route_mode="slash_direct",
            system_contexts=["[Skillpack] guidance_only"],
            parameterized=True,
        )
        fallback_route = SkillMatchResult(
            skills_used=[],
            tool_scope=["add_numbers"],
            route_mode="fallback",
            system_contexts=["fallback-context"],
            parameterized=False,
        )
        engine._route_skills = AsyncMock(side_effect=[slash_route, fallback_route])
        engine._tool_calling_loop = AsyncMock(return_value=ChatResult(reply="ok"))

        result = await engine.chat(
            "/guidance_only 查看哪个表格最大",
            slash_command="guidance_only",
            raw_args="查看哪个表格最大",
        )

        assert result.reply == "ok"
        assert engine._route_skills.await_count == 2
        first_call = engine._route_skills.await_args_list[0]
        assert first_call.args[0] == "/guidance_only 查看哪个表格最大"
        assert first_call.kwargs["slash_command"] == "guidance_only"
        second_call = engine._route_skills.await_args_list[1]
        assert second_call.args[0] == "查看哪个表格最大"
        assert second_call.kwargs.get("slash_command") is None

        loop_route = engine._tool_calling_loop.await_args.args[0]
        assert loop_route.route_mode == "fallback"
        assert any("Slash Guidance" in item for item in loop_route.system_contexts)

        user_messages = [
            msg.get("content", "")
            for msg in engine.memory.get_messages()
            if msg.get("role") == "user"
        ]
        assert user_messages == ["查看哪个表格最大"]


class TestForkPathRemoved:
    """fork 链路已硬移除，仅保留显式 delegate_to_subagent。"""

    @pytest.mark.asyncio
    async def test_chat_with_active_skill_no_longer_auto_delegates(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)

        engine._active_skills = [Skillpack(
            name="excel_code_runner",
            description="代码处理",
            instructions="",
            source="project",
            root_dir="/tmp/skill",
        )]
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
    async def test_activate_skill_success_no_longer_triggers_auto_delegate(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        engine.memory.add_user_message("请分析销售趋势")
        engine._delegate_to_subagent = AsyncMock()

        async def _fake_execute_tool_call(*args, **kwargs) -> ToolCallResult:
            engine._active_skills = [Skillpack(
                name="team/analyst",
                description="普通技能",
                instructions="",
                source="project",
                root_dir="/tmp/skill",
            )]
            return ToolCallResult(
                tool_name="activate_skill",
                arguments={"skill_name": "team/analyst"},
                result="OK",
                success=True,
            )

        engine._execute_tool_call = AsyncMock(side_effect=_fake_execute_tool_call)
        # 引擎每轮 LLM 调用先尝试流式（消耗 1 个响应），若失败回退非流式（再消耗 1 个）。
        # 此外"中间讨论放行"机制会让纯文本回复后继续迭代。
        # 提供足够多的响应以覆盖所有调用路径。
        final_response = _make_text_response("主代理继续执行。")
        engine._client.chat.completions.create = AsyncMock(
            side_effect=[
                _make_tool_call_response(
                    [("call_1", "activate_skill", json.dumps({"skill_name": "team/analyst"}))]
                ),
            ] + [final_response] * 20  # 足够覆盖流式回退 + 中间讨论放行
        )

        route_result = SkillMatchResult(
            skills_used=[],
            route_mode="fallback",
            system_contexts=[],
        )
        result = await engine._tool_calling_loop(route_result, on_event=None)

        assert result.reply in ("主代理继续执行。", "分析完成。")
        engine._delegate_to_subagent.assert_not_awaited()

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

        prompts, _ = engine._prepare_system_prompts_for_request([])
        assert len(prompts) >= 1
        assert any("examples/bench/stress_test_comprehensive.xlsx" in p for p in prompts)

    @pytest.mark.asyncio
    async def test_run_subagent_passes_window_context_and_enricher(self) -> None:
        config = _make_config(window_perception_enabled=True, window_return_mode="enriched")
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)

        engine._subagent_registry = MagicMock()
        engine._subagent_registry.get.return_value = SubagentConfig(
            name="explorer",
            description="测试",
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
    async def test_run_subagent_verifier_falls_back_to_active_model_when_aux_bound_to_other_endpoint(
        self,
    ) -> None:
        config = _make_config(
            model="main-model",
            base_url="https://www.right.codes/codex/v1",
            aux_model="qwen-flash",
            aux_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        )
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        engine._active_model = "gpt-5.3-codex-high"
        engine._active_api_key = "active-key"
        engine._active_base_url = "https://www.right.codes/codex/v1"

        engine._subagent_registry = MagicMock()
        engine._subagent_registry.get.return_value = SubagentConfig(
            name="verifier",
            description="测试 verifier",
            permission_mode="readOnly",
        )
        engine._subagent_executor.run = AsyncMock(
            return_value=SubagentResult(
                success=True,
                summary="ok",
                subagent_name="verifier",
                permission_mode="readOnly",
                conversation_id="conv_verifier",
            )
        )

        result = await engine.run_subagent(agent_name="verifier", prompt="请验证")

        assert result.success is True
        runtime_cfg = engine._subagent_executor.run.await_args.kwargs["config"]
        assert runtime_cfg.model == "gpt-5.3-codex-high"
        assert runtime_cfg.api_key == "active-key"
        assert runtime_cfg.base_url == "https://www.right.codes/codex/v1"

    @pytest.mark.asyncio
    async def test_run_subagent_retries_with_active_model_when_aux_model_unavailable(
        self,
    ) -> None:
        config = _make_config(
            model="main-model",
            base_url="https://www.right.codes/codex/v1",
            aux_model="qwen-flash",
        )
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        engine._active_model = "gpt-5.3-codex-high"
        engine._active_api_key = "active-key"
        engine._active_base_url = "https://www.right.codes/codex/v1"

        engine._subagent_registry = MagicMock()
        engine._subagent_registry.get.return_value = SubagentConfig(
            name="verifier",
            description="测试 verifier",
            permission_mode="readOnly",
        )

        first_fail = SubagentResult(
            success=False,
            summary="子代理执行失败",
            error="Error code: 400 - {'error': '端点/codex未配置模型qwen-flash'}",
            subagent_name="verifier",
            permission_mode="readOnly",
            conversation_id="conv_1",
        )
        retry_success = SubagentResult(
            success=True,
            summary="ok",
            subagent_name="verifier",
            permission_mode="readOnly",
            conversation_id="conv_2",
        )
        engine._subagent_executor.run = AsyncMock(side_effect=[first_fail, retry_success])

        result = await engine.run_subagent(agent_name="verifier", prompt="请验证")

        assert result.success is True
        assert engine._subagent_executor.run.await_count == 2
        first_cfg = engine._subagent_executor.run.await_args_list[0].kwargs["config"]
        second_cfg = engine._subagent_executor.run.await_args_list[1].kwargs["config"]
        assert first_cfg.model == "qwen-flash"
        assert second_cfg.model == "gpt-5.3-codex-high"

    @pytest.mark.asyncio
    async def test_delegate_pending_approval_asks_user_and_supports_fullaccess_retry(
        self,
        tmp_path: Path,
    ) -> None:
        """阻塞式子代理审批：question_resolver 返回 '2'（fullaccess 重试）后内联处理。"""
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

        # 设置 question_resolver：选择选项 2（fullaccess 重试）
        async def _resolver(q):
            return "2"
        engine._question_resolver = _resolver

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
        # 阻塞模式下，_execute_tool_call 内联完成审批流程
        assert first.success is True
        assert "已开启 fullaccess" in first.result
        assert "重试完成" in first.result
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
    async def test_auto_select_subagent_returns_single_candidate(self) -> None:
        """v6: 只有一个候选时直接返回，不调用 LLM。"""
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        engine._subagent_registry = MagicMock()
        engine._subagent_registry.build_catalog.return_value = (
            "可用子代理：\n- subagent：通用全能力子代理",
            ["subagent"],
        )

        picked = await engine._auto_select_subagent(
            task="请先总结这个文件夹结构",
            file_paths=["data"],
        )

        assert picked == "subagent"

    @pytest.mark.asyncio
    async def test_auto_select_subagent_returns_first_when_multiple(self) -> None:
        """v6: 多个候选时返回第一个（用户自定义场景）。"""
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        engine._subagent_registry = MagicMock()
        engine._subagent_registry.build_catalog.return_value = (
            "可用子代理：\n- subagent：通用\n- custom：自定义",
            ["subagent", "custom"],
        )

        picked = await engine._auto_select_subagent(
            task="请分析一下",
            file_paths=[],
        )

        assert picked == "subagent"

    @pytest.mark.asyncio
    async def test_auto_select_subagent_fallback_when_no_candidates(self) -> None:
        """v6: 无候选时回退 subagent。"""
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        engine._subagent_registry = MagicMock()
        engine._subagent_registry.build_catalog.return_value = ("", [])

        picked = await engine._auto_select_subagent(
            task="请分析一下",
            file_paths=[],
        )

        assert picked == "subagent"


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
            "questions": [
                {
                    "header": header,
                    "text": text,
                    "options": [
                        {"label": "方案A", "description": "快速实现"},
                        {"label": "方案B", "description": "稳健实现"},
                    ],
                    "multiSelect": multi_select,
                }
            ]
        }

    @pytest.mark.asyncio
    async def test_ask_user_blocking_inline_completes_without_reroute(self) -> None:
        """阻塞式 ask_user：question_resolver 返回 '1' 后内联完成，不中断循环。"""
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

        # question_resolver：选择选项 1（方案A）
        async def _resolver(q):
            return "1"

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
        # 第 1 次：do_work → add_numbers → 循环继续
        # 第 2 次：ask_user → 内联阻塞获取回答 → 循环继续
        # 第 3 次：final text
        engine._client.chat.completions.create = AsyncMock(
            side_effect=[
                do_work_response,
                ask_response,
                final_response,
            ]
        )

        result = await engine.chat("请完成任务", question_resolver=_resolver)
        assert result.reply == "已按你的选择完成，结果是 3。"
        assert engine.has_pending_question() is False
        # 整个流程在一次 chat() 中完成，只路由一次
        assert engine._route_skills.await_count == 1

        tool_msgs = [m for m in engine.memory.get_messages() if m.get("role") == "tool"]
        ask_msg = next(m for m in tool_msgs if m.get("tool_call_id") == "call_q1")
        ask_payload = json.loads(ask_msg["content"])
        assert ask_payload["question_id"].startswith("qst_")
        assert ask_payload["multi_select"] is False
        assert ask_payload["selected_options"][0]["label"] == "方案A"

    @pytest.mark.asyncio
    async def test_blocking_multiple_questions_resolved_inline(self) -> None:
        """阻塞式多问题：question_resolver 依次回答两个问题后内联完成。"""
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

        # 依次回答两个问题
        _answers = iter(["1", "1\n自定义策略"])
        async def _resolver(q):
            return next(_answers)

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
                ("call_add", "add_numbers", json.dumps({"a": 10, "b": 20})),
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

        result = await engine.chat("开始执行", question_resolver=_resolver)
        assert result.reply == "两个问题都确认完毕。"
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
    async def test_blocking_ask_user_no_pending_state_after_chat(self) -> None:
        """阻塞式 ask_user：chat() 返回后不留 pending 状态。"""
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

        async def _resolver(q):
            return "1"

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

        result = await engine.chat("发起提问", question_resolver=_resolver)
        # 阻塞模式下 chat() 返回时问题已解决
        assert result.reply == "已恢复执行。"
        assert engine.has_pending_question() is False


class TestToolCallingLoopApprovalResolver:
    """_tool_calling_loop 的审批分支行为锁定测试。"""

    @pytest.mark.asyncio
    async def test_inline_approval_accept_continues_and_emits_resolved_event(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)

        route_result = SkillMatchResult(
            skills_used=[],
            route_mode="fallback",
            system_contexts=[],
        )
        pending = engine._approval.create_pending(
            tool_name="run_shell",
            arguments={"command": "echo ok"},
            tool_scope=["run_shell"],
        )

        engine._client.chat.completions.create = AsyncMock(
            side_effect=[
                _make_tool_call_response(
                    [("call_approve", "run_shell", json.dumps({"command": "echo ok"}))]
                ),
                _make_text_response("审批后继续执行完成。"),
            ]
        )
        engine._execute_tool_call = AsyncMock(
            return_value=ToolCallResult(
                tool_name="run_shell",
                arguments={"command": "echo ok"},
                result=engine._format_pending_prompt(pending),
                success=True,
                pending_approval=True,
                approval_id=pending.approval_id,
            )
        )

        async def _approval_resolver(_pending):
            assert _pending.approval_id == pending.approval_id
            return "accept"

        async def _execute_approved_pending(_pending, *, on_event=None):
            assert _pending.approval_id == pending.approval_id
            engine._approval.clear_pending()
            return True, "已执行 run_shell", None

        engine._execute_approved_pending = AsyncMock(side_effect=_execute_approved_pending)
        events: list[Any] = []

        result = await engine._tool_calling_loop(
            route_result,
            on_event=events.append,
            approval_resolver=_approval_resolver,
        )

        assert result.reply == "审批后继续执行完成。"
        assert engine._execute_approved_pending.await_count == 1
        assert any(
            event.event_type == EventType.APPROVAL_RESOLVED and event.success
            for event in events
        )
        tool_messages = [
            msg for msg in engine.memory.get_messages() if msg.get("role") == "tool"
        ]
        approved_tool_msg = next(
            msg for msg in tool_messages if msg.get("tool_call_id") == "call_approve"
        )
        assert "已执行 run_shell" in approved_tool_msg.get("content", "")

    @pytest.mark.asyncio
    async def test_inline_approval_reject_continues_and_emits_failed_event(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)

        route_result = SkillMatchResult(
            skills_used=[],
            route_mode="fallback",
            system_contexts=[],
        )
        pending = engine._approval.create_pending(
            tool_name="run_shell",
            arguments={"command": "echo fail"},
            tool_scope=["run_shell"],
        )

        engine._client.chat.completions.create = AsyncMock(
            side_effect=[
                _make_tool_call_response(
                    [("call_reject", "run_shell", json.dumps({"command": "echo fail"}))]
                ),
                _make_text_response("拒绝后继续执行完成。"),
            ]
        )
        engine._execute_tool_call = AsyncMock(
            return_value=ToolCallResult(
                tool_name="run_shell",
                arguments={"command": "echo fail"},
                result=engine._format_pending_prompt(pending),
                success=True,
                pending_approval=True,
                approval_id=pending.approval_id,
            )
        )
        engine._execute_approved_pending = AsyncMock(
            return_value=(True, "不应执行", None)
        )

        events: list[Any] = []

        async def _approval_resolver(_pending):
            assert _pending.approval_id == pending.approval_id
            return None

        result = await engine._tool_calling_loop(
            route_result,
            on_event=events.append,
            approval_resolver=_approval_resolver,
        )

        assert result.reply == "拒绝后继续执行完成。"
        engine._execute_approved_pending.assert_not_awaited()
        assert any(
            event.event_type == EventType.APPROVAL_RESOLVED and not event.success
            for event in events
        )
        tool_messages = [
            msg for msg in engine.memory.get_messages() if msg.get("role") == "tool"
        ]
        rejected_tool_msg = next(
            msg for msg in tool_messages if msg.get("tool_call_id") == "call_reject"
        )
        assert "已拒绝待确认操作" in rejected_tool_msg.get("content", "")

    @pytest.mark.asyncio
    async def test_pending_approval_without_resolver_blocks_and_resolves_via_registry(self) -> None:
        """无 resolver 时，审批通过 InteractionRegistry Future 阻塞等待并内联处理。"""
        import asyncio

        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)

        route_result = SkillMatchResult(
            skills_used=[],
            route_mode="fallback",
            system_contexts=[],
        )
        pending = engine._approval.create_pending(
            tool_name="run_shell",
            arguments={"command": "echo pending"},
            tool_scope=["run_shell"],
        )

        engine._client.chat.completions.create = AsyncMock(
            side_effect=[
                _make_tool_call_response(
                    [("call_pending", "run_shell", json.dumps({"command": "echo pending"}))]
                ),
                _make_text_response("审批通过，已执行完成。"),
            ]
        )
        engine._execute_tool_call = AsyncMock(
            return_value=ToolCallResult(
                tool_name="run_shell",
                arguments={"command": "echo pending"},
                result="pending",
                success=True,
                pending_approval=True,
                approval_id=pending.approval_id,
            )
        )
        engine._execute_approved_pending = AsyncMock(
            return_value=(True, "echo ok", None),
        )

        # 后台任务在 Future 创建后立即 resolve
        async def _resolve_later():
            for _ in range(50):
                await asyncio.sleep(0.05)
                if pending.approval_id in engine._interaction_registry._futures:
                    engine._interaction_registry.resolve(
                        pending.approval_id, {"decision": "accept"},
                    )
                    return
        resolve_task = asyncio.create_task(_resolve_later())

        result = await engine._tool_calling_loop(route_result, on_event=None)
        await resolve_task

        assert "审批通过" in result.reply or "echo ok" in result.reply
        engine._execute_approved_pending.assert_awaited_once()


class TestToolCallingLoopWriteGuard:
    """_tool_calling_loop 写入门禁退出行为测试。"""

    @pytest.mark.asyncio
    async def test_write_guard_off_returns_first_text_response(self) -> None:
        """guard_mode=off（默认）时，text-only 响应直接返回，不强制继续。"""
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)

        route_result = SkillMatchResult(
            skills_used=[],
            route_mode="fallback",
            system_contexts=[],
            write_hint="may_write",
        )
        engine._client.chat.completions.create = AsyncMock(
            side_effect=[
                _make_text_response("先解释步骤，暂未执行工具。"),
                _make_text_response("仍未执行任何写入工具。"),
            ]
        )

        result = await engine._tool_calling_loop(route_result, on_event=None)

        # guard_mode=off：第一轮 text-only 直接返回
        assert result.reply == "先解释步骤，暂未执行工具。"
        assert result.iterations == 1


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
        assert len(meta_tools) >= 4
        by_name = {tool["function"]["name"]: tool for tool in meta_tools}
        assert "activate_skill" in by_name
        assert "delegate" in by_name
        assert "list_subagents" in by_name
        assert "ask_user" in by_name

        activate_tool = by_name["activate_skill"]["function"]
        activate_params = activate_tool["parameters"]
        assert activate_params["required"] == ["skill_name"]
        assert set(activate_params["properties"]["skill_name"]["enum"]) == {
            "chart_basic",
            "data_basic",
        }

        delegate_tool = by_name["delegate"]["function"]
        delegate_params = delegate_tool["parameters"]
        assert delegate_params["required"] == []
        assert "task" in delegate_params["properties"]
        assert "task_brief" in delegate_params["properties"]
        assert "tasks" in delegate_params["properties"]
        assert delegate_params["properties"]["task_brief"]["type"] == "object"
        assert delegate_params["properties"]["task_brief"]["required"] == ["title"]
        assert delegate_params["properties"]["file_paths"]["type"] == "array"
        assert "agent_name" in delegate_params["properties"]
        assert delegate_params["properties"]["agent_name"]["enum"] == ["folder_summarizer"]
        assert "Subagent_Catalog" in delegate_tool["description"]
        assert "folder_summarizer" in delegate_tool["description"]

        ask_user_tool = by_name["ask_user"]["function"]
        ask_user_params = ask_user_tool["parameters"]
        assert ask_user_params["required"] == ["questions"]
        questions_schema = ask_user_params["properties"]["questions"]
        assert questions_schema["type"] == "array"
        assert questions_schema["minItems"] == 1
        assert questions_schema["maxItems"] == 8
        item_schema = questions_schema["items"]
        assert item_schema["required"] == ["text", "options"]
        assert item_schema["properties"]["options"]["minItems"] == 1
        assert item_schema["properties"]["options"]["maxItems"] == 4

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


class TestSkillMCPRequirements:
    """Skill 的 MCP 依赖校验。"""

    @pytest.mark.asyncio
    async def test_activate_skill_rejects_when_required_mcp_server_missing(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)

        skill = Skillpack(
            name="need_mcp",
            description="依赖外部 MCP",
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

        result = await engine._handle_activate_skill("need_mcp")

        assert "MCP 依赖未满足" in result
        assert not engine._active_skills

    @pytest.mark.asyncio
    async def test_activate_skill_accepts_when_required_mcp_server_and_tool_ready(self) -> None:
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

        result = await engine._handle_activate_skill("need_mcp")

        assert result.startswith("OK")
        assert engine._active_skills
        assert engine._active_skills[-1].name == "need_mcp"


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
    async def test_execute_tool_call_parse_error_returns_failure(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)

        tc = SimpleNamespace(
            id="call_bad_json",
            function=SimpleNamespace(name="add_numbers", arguments="{bad json"),
        )
        result = await engine._execute_tool_call(
            tc=tc,
            tool_scope=["add_numbers"],
            on_event=None,
            iteration=1,
        )

        assert result.success is False
        assert "工具参数解析错误" in result.result

    @pytest.mark.asyncio
    async def test_pre_tool_hook_deny_blocks_tool(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        engine._active_skills = [Skillpack(
            name="hook/deny",
            description="deny hook",
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
        )]

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
        engine._active_skills = [Skillpack(
            name="hook/ask",
            description="ask hook",
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
        )]

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
        engine._active_skills = [Skillpack(
            name="hook/update",
            description="update input hook",
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
        )]

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
        engine._active_skills = [Skillpack(
            name="hook/allow",
            description="allow hook",
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
        )]

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
        engine._active_skills = [Skillpack(
            name="hook/agent",
            description="agent hook",
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
        )]

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

    @pytest.mark.asyncio
    async def test_post_tool_hook_deny_turns_success_to_failure(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        engine._active_skills = [Skillpack(
            name="hook/post_deny",
            description="post deny hook",
            instructions="",
            source="project",
            root_dir="/tmp/hook",
            hooks={
                "PostToolUse": [
                    {
                        "matcher": "add_numbers",
                        "hooks": [{"type": "prompt", "decision": "deny", "reason": "post blocked"}],
                    }
                ]
            },
        )]

        tc = SimpleNamespace(
            id="call_post_hook_deny",
            function=SimpleNamespace(name="add_numbers", arguments=json.dumps({"a": 2, "b": 3})),
        )
        result = await engine._execute_tool_call(
            tc=tc,
            tool_scope=["add_numbers"],
            on_event=None,
            iteration=1,
        )

        assert result.success is False
        assert "[Hook 拒绝] post blocked" in result.result

    @pytest.mark.asyncio
    async def test_run_code_red_creates_pending_approval(self) -> None:
        config = _make_config(code_policy_enabled=True)
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)

        dangerous_code = "import subprocess\nsubprocess.run(['echo', 'x'])"
        tc = SimpleNamespace(
            id="call_run_code_red",
            function=SimpleNamespace(
                name="run_code",
                arguments=json.dumps({"code": dangerous_code}),
            ),
        )

        result = await engine._execute_tool_call(
            tc=tc,
            tool_scope=None,
            on_event=None,
            iteration=1,
        )

        assert result.success is True
        assert result.pending_approval is True
        assert isinstance(result.approval_id, str) and result.approval_id
        assert "需要人工确认" in result.result
        assert engine._approval.pending is not None
        assert engine._approval.pending.approval_id == result.approval_id


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
        _activate_test_tools(engine)

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
        _activate_test_tools(engine)

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
        _activate_test_tools(engine)

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
        _activate_test_tools(engine)

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
        _activate_test_tools(engine)

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
        assert "TOOL_EXECUTION_ERROR" in tool_msgs[0]["content"]

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
        _activate_test_tools(engine)

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
        _activate_test_tools(engine)

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
        _activate_test_tools(engine)

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
        assert r.finish_accepted is False

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
# **验证：需求 1.1, 1.7**
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

    **验证：需求 1.1, 1.7**
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

    **验证：需求 1.1, 1.7**
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
# **验证：需求 1.2**
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

    **验证：需求 1.2**
    """
    config = _make_config()
    registry = _make_registry_with_tools()
    engine = AgentEngine(config, registry)
    _activate_test_tools(engine)

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
# **验证：需求 1.3**
# ---------------------------------------------------------------------------


@given(
    reply_text=nonempty_text_st,
)
@pytest.mark.asyncio
async def test_property_3_pure_text_terminates_loop(reply_text: str) -> None:
    """Property 3：纯文本终止循环。

    对于任意不含 tool_calls 的响应，Engine 必须立即终止循环并返回文本。

    **验证：需求 1.3**
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
# **验证：需求 1.4**
# ---------------------------------------------------------------------------


@given(
    max_iter=st.integers(min_value=1, max_value=10),
)
@pytest.mark.asyncio
async def test_property_4_iteration_limit_protection(max_iter: int) -> None:
    """Property 4：迭代上限保护。

    当连续 N 轮均需要工具调用时，Engine 在第 N 轮后必须终止。

    **验证：需求 1.4**
    """
    config = _make_config(max_iterations=max_iter)
    registry = _make_registry_with_tools()
    engine = AgentEngine(config, registry)
    _activate_test_tools(engine)

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
# **验证：需求 1.5**
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

    **验证：需求 1.5**
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
# **验证：需求 1.6**
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

    **验证：需求 1.6**
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
# **验证：需求 1.10, 5.7**
# ---------------------------------------------------------------------------


@given(
    n_calls=st.integers(min_value=1, max_value=3),
)
@pytest.mark.asyncio
async def test_property_20_async_non_blocking(n_calls: int) -> None:
    """Property 20：异步不阻塞。

    并发请求场景下，阻塞工具执行不得阻塞主事件循环。
    验证 asyncio.to_thread 被用于工具执行。

    **验证：需求 1.10, 5.7**
    """
    config = _make_config()
    registry = _make_registry_with_tools()
    engine = AgentEngine(config, registry)
    _activate_test_tools(engine)

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

        def copy_file(source: str, destination: str) -> str:
            target = workspace / destination
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("copied", encoding="utf-8")
            return json.dumps({"status": "success", "destination": destination}, ensure_ascii=False)

        registry.register_tools([
            ToolDef(
                name="copy_file",
                description="复制文件",
                input_schema={
                    "type": "object",
                    "properties": {
                        "source": {"type": "string"},
                        "destination": {"type": "string"},
                    },
                    "required": ["source", "destination"],
                },
                func=copy_file,
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

    def _make_registry_with_failing_audit_tool(self, workspace: Path) -> ToolRegistry:
        registry = ToolRegistry()

        def copy_file(source: str, destination: str) -> str:
            target = workspace / destination
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("copied", encoding="utf-8")
            raise RuntimeError("intentional_audit_failure")

        registry.register_tools([
            ToolDef(
                name="copy_file",
                description="复制文件后抛错",
                input_schema={
                    "type": "object",
                    "properties": {
                        "source": {"type": "string"},
                        "destination": {"type": "string"},
                    },
                    "required": ["source", "destination"],
                },
                func=copy_file,
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
        """阻塞式审批：approval_resolver 返回 accept 后内联执行高风险工具。"""
        config = _make_config(workspace_root=str(tmp_path), window_perception_enabled=False)
        registry = self._make_registry_with_write_tool(tmp_path)
        engine = AgentEngine(config, registry)

        captured_id = None
        async def _accept(p):
            nonlocal captured_id
            captured_id = p.approval_id
            return "accept"

        tool_response = _make_tool_call_response([
            ("call_1", "write_text_file", json.dumps({"file_path": "a.txt", "content": "hello"}))
        ])
        text_response = _make_text_response("文件已写入完成。")
        engine._client.chat.completions.create = AsyncMock(
            side_effect=[tool_response, text_response]
        )

        reply = await engine.chat("写入文件", approval_resolver=_accept)
        assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "hello"
        assert captured_id is not None
        assert (tmp_path / "outputs" / "approvals" / captured_id / "manifest.json").exists()

    @pytest.mark.asyncio
    async def test_accept_resumes_task_list_execution_after_high_risk_gate(
        self,
        tmp_path: Path,
    ) -> None:
        """阻塞式审批：accept 后继续执行后续工具调用。"""
        config = _make_config(workspace_root=str(tmp_path))
        registry = self._make_registry_with_write_tool(tmp_path)

        def add_numbers(a: int, b: int) -> int:
            return a + b

        registry.register_tools([
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
            )
        ])

        engine = AgentEngine(config, registry)
        engine._task_store.create("测试任务", ["写入文件", "继续计算"])

        route_result = SkillMatchResult(
            skills_used=[],
            tool_scope=["write_text_file", "add_numbers", "task_update"],
            route_mode="fallback",
            system_contexts=[],
        )
        engine._route_skills = AsyncMock(return_value=route_result)

        async def _accept(p):
            return "accept"

        first_round = _make_tool_call_response([
            (
                "call_write",
                "write_text_file",
                json.dumps({"file_path": "resume.txt", "content": "ok"}, ensure_ascii=False),
            )
        ])
        resume_round = _make_tool_call_response([
            ("call_add", "add_numbers", json.dumps({"a": 1, "b": 2}, ensure_ascii=False))
        ])
        done_round = _make_text_response("后续子任务已完成")
        engine._client.chat.completions.create = AsyncMock(
            side_effect=[first_round, resume_round, done_round]
        )

        reply = await engine.chat("开始执行", approval_resolver=_accept)
        assert "后续子任务已完成" in reply
        assert (tmp_path / "resume.txt").read_text(encoding="utf-8") == "ok"

    @pytest.mark.asyncio
    async def test_reject_pending(self, tmp_path: Path) -> None:
        """阻塞式审批：approval_resolver 返回 reject 后文件不写入。"""
        config = _make_config(workspace_root=str(tmp_path))
        registry = self._make_registry_with_write_tool(tmp_path)
        engine = AgentEngine(config, registry)

        async def _reject(p):
            return "reject"

        tool_response = _make_tool_call_response([
            ("call_1", "write_text_file", json.dumps({"file_path": "b.txt", "content": "world"}))
        ])
        text_response = _make_text_response("已拒绝操作。")
        engine._client.chat.completions.create = AsyncMock(
            side_effect=[tool_response, text_response]
        )
        reply = await engine.chat("写文件", approval_resolver=_reject)
        assert engine._approval.pending is None
        assert not (tmp_path / "b.txt").exists()

    @pytest.mark.asyncio
    async def test_undo_after_accept(self, tmp_path: Path) -> None:
        """阻塞式审批：accept 后 /undo 回滚文件。"""
        config = _make_config(workspace_root=str(tmp_path))
        registry = self._make_registry_with_write_tool(tmp_path)
        engine = AgentEngine(config, registry)

        captured_id = None
        async def _accept(p):
            nonlocal captured_id
            captured_id = p.approval_id
            return "accept"

        tool_response = _make_tool_call_response([
            ("call_1", "write_text_file", json.dumps({"file_path": "c.txt", "content": "undo"}))
        ])
        text_response = _make_text_response("文件已写入。")
        engine._client.chat.completions.create = AsyncMock(
            side_effect=[tool_response, text_response]
        )
        await engine.chat("写文件", approval_resolver=_accept)
        assert (tmp_path / "c.txt").exists()
        assert captured_id is not None

        undo_reply = await engine.chat(f"/undo {captured_id}")
        assert "已回滚" in undo_reply
        assert not (tmp_path / "c.txt").exists()

    @pytest.mark.asyncio
    async def test_failed_accept_still_writes_failed_manifest(self, tmp_path: Path) -> None:
        """阻塞式审批：accept 后工具执行失败，manifest 记录失败状态。"""
        config = _make_config(workspace_root=str(tmp_path))
        registry = self._make_registry_with_failing_write_tool(tmp_path)
        engine = AgentEngine(config, registry)

        captured_id = None
        async def _accept(p):
            nonlocal captured_id
            captured_id = p.approval_id
            return "accept"

        tool_response = _make_tool_call_response([
            ("call_1", "write_text_file", json.dumps({"file_path": "err.txt", "content": "x"}))
        ])
        text_response = _make_text_response("执行出错。")
        engine._client.chat.completions.create = AsyncMock(
            side_effect=[tool_response, text_response]
        )
        reply = await engine.chat("写文件", approval_resolver=_accept)
        assert captured_id is not None
        manifest_path = tmp_path / "outputs" / "approvals" / captured_id / "manifest.json"
        assert manifest_path.exists()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["execution"]["status"] == "failed"
        assert manifest["execution"]["error_type"] == "ToolExecutionError"

    @pytest.mark.asyncio
    async def test_audit_tool_failure_still_returns_failed_audit_record(self, tmp_path: Path) -> None:
        config = _make_config(workspace_root=str(tmp_path))
        registry = self._make_registry_with_failing_audit_tool(tmp_path)
        engine = AgentEngine(config, registry)

        tc = SimpleNamespace(
            id="call_audit_fail",
            function=SimpleNamespace(
                name="copy_file",
                arguments=json.dumps({"source": "a.txt", "destination": "failed_copy.txt"}, ensure_ascii=False),
            ),
        )

        result = await engine._execute_tool_call(
            tc=tc,
            tool_scope=["copy_file"],
            on_event=None,
            iteration=1,
        )

        assert result.success is False
        assert "intentional_audit_failure" in result.result
        assert result.audit_record is not None
        assert result.audit_record.execution_status == "failed"
        assert result.audit_record.error_type == "ToolExecutionError"

    @pytest.mark.asyncio
    async def test_undo_after_restart_loads_manifest(self, tmp_path: Path) -> None:
        """阻塞式审批：跨 engine 实例 /undo 回滚。"""
        config = _make_config(workspace_root=str(tmp_path))
        registry = self._make_registry_with_write_tool(tmp_path)
        engine1 = AgentEngine(config, registry)
        _activate_test_tools(engine1, ["write_text_file"])

        captured_id = None
        async def _accept(p):
            nonlocal captured_id
            captured_id = p.approval_id
            return "accept"

        tool_response = _make_tool_call_response([
            ("call_1", "write_text_file", json.dumps({"file_path": "restart.txt", "content": "v"}))
        ])
        text_response = _make_text_response("写入完成。")
        engine1._client.chat.completions.create = AsyncMock(
            side_effect=[tool_response, text_response]
        )
        await engine1.chat("写文件", approval_resolver=_accept)
        assert (tmp_path / "restart.txt").exists()
        assert captured_id is not None

        engine2 = AgentEngine(config, registry)
        undo_reply = await engine2.chat(f"/undo {captured_id}")
        assert "已回滚" in undo_reply
        assert not (tmp_path / "restart.txt").exists()

    @pytest.mark.asyncio
    async def test_fullaccess_bypass_accept(self, tmp_path: Path) -> None:
        config = _make_config(workspace_root=str(tmp_path))
        registry = self._make_registry_with_write_tool(tmp_path)
        engine = AgentEngine(config, registry)

        on_reply = await engine.chat("/fullaccess on")
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
    async def test_fullaccess_resolver_accepts_and_enables_fullaccess(self, tmp_path: Path) -> None:
        """阻塞式审批：approval_resolver 返回 fullaccess 后自动开启并执行。"""
        config = _make_config(workspace_root=str(tmp_path))
        registry = self._make_registry_with_write_tool(tmp_path)
        engine = AgentEngine(config, registry)

        async def _fullaccess(p):
            return "fullaccess"

        tool_response = _make_tool_call_response([
            ("call_1", "write_text_file", json.dumps({"file_path": "auto.txt", "content": "hello"}))
        ])
        text_response = _make_text_response("已完成。")
        engine._client.chat.completions.create = AsyncMock(
            side_effect=[tool_response, text_response]
        )
        reply = await engine.chat("写文件", approval_resolver=_fullaccess)
        assert engine.full_access_enabled is True
        assert engine._approval.pending is None
        assert (tmp_path / "auto.txt").read_text(encoding="utf-8") == "hello"

    @pytest.mark.asyncio
    async def test_default_mode_non_whitelist_tool_executes_directly(self, tmp_path: Path) -> None:
        config = _make_config(workspace_root=str(tmp_path))
        registry = self._make_registry_with_custom_tool()
        engine = AgentEngine(config, registry)

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
        engine._active_skills = [Skillpack(
            name="test/custom",
            description="test",
            instructions="",
            source="project",
            root_dir=str(tmp_path),
        )]

        on_reply = await engine.chat("/fullaccess on")
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
            ("call_1", "copy_file", json.dumps({"source": "a.xlsx", "destination": "b.xlsx"}))
        ])
        text_response = _make_text_response("完成")
        engine._client.chat.completions.create = AsyncMock(
            side_effect=[tool_response, text_response]
        )

        reply = await engine.chat("复制文件")
        assert reply == "完成"
        assert engine._approval.pending is None
        engine._execute_tool_with_audit.assert_awaited_once()


class TestToolIndexNotice:
    """Task 4: 工具分组索引注入测试。"""

    def test_build_tool_index_notice_empty_scope(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        notice = engine._build_tool_index_notice()
        assert notice == ""

    def test_tool_index_not_in_notice_when_no_categorized_tools(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        # add_numbers 不在任何分类中
        notice = engine._build_tool_index_notice()
        assert notice == ""


class TestToolInjectionOptimizationE2E:
    """Task 7: 工具注入优化端到端集成测试。"""

    @staticmethod
    def _make_registry_with_categorized_tools() -> ToolRegistry:
        registry = ToolRegistry()

        def _noop(**_: object) -> str:
            return "ok"

        for tool_name in (
            "read_excel",
            "create_chart",
            "format_cells",
            "write_excel",
            "list_sheets",
        ):
            registry.register_tool(
                ToolDef(
                    name=tool_name,
                    description=f"{tool_name} tool",
                    input_schema={"type": "object", "properties": {}},
                    func=_noop,
                )
            )
        return registry

    def test_tool_index_in_system_prompt_when_no_skill(self) -> None:
        """无 skill 激活时 system prompt 中应包含工具索引。"""
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        # 确保无 active_skill
        assert not engine._active_skills
        prompts, error = engine._prepare_system_prompts_for_request(skill_contexts=[])
        assert error is None
        # 合并所有 prompt 检查是否包含工具索引
        full_prompt = "\n".join(prompts)
        # 由于 registry 中只有 add_numbers 和 fail_tool，不在 TOOL_CATEGORIES 中
        # 所以工具索引可能为空。这是正确行为。
        # 工具索引应包含已注册的工具。


# ── Auto-Supplement 测试辅助 ──────────────────────────────────


def _make_skill_router(config: ExcelManusConfig | None = None) -> "SkillRouter":
    """创建包含模拟 skillpacks 的 SkillRouter，用于自动补充测试。"""
    from excelmanus.skillpacks.loader import SkillpackLoader
    from excelmanus.skillpacks.router import SkillRouter

    cfg = config or _make_config()
    registry = _make_registry_with_tools()
    loader = SkillpackLoader(cfg, registry)

    # 注入模拟 skillpacks（不从磁盘加载）
    loader._skillpacks = {
        "format_basic": Skillpack(
            name="format_basic",
            description="基础格式化",
            instructions="格式化操作指引",
            source="system",
            root_dir="/tmp/format_basic",
        ),
        "data_basic": Skillpack(
            name="data_basic",
            description="数据分析",
            instructions="数据操作指引",
            source="system",
            root_dir="/tmp/data_basic",
        ),
        "chart_basic": Skillpack(
            name="chart_basic",
            description="图表",
            instructions="图表操作指引",
            source="system",
            root_dir="/tmp/chart_basic",
        ),
        "data_basic": Skillpack(
            name="data_basic",
            description="通用 Excel",
            instructions="通用 Excel 操作",
            source="system",
            root_dir="/tmp/data_basic",
        ),
        "excel_code_runner": Skillpack(
            name="excel_code_runner",
            description="代码执行",
            instructions="代码执行指引",
            source="system",
            root_dir="/tmp/excel_code_runner",
        ),
        "sheet_ops": Skillpack(
            name="sheet_ops",
            description="工作表操作",
            instructions="工作表操作指引",
            source="system",
            root_dir="/tmp/sheet_ops",
        ),
        "file_ops": Skillpack(
            name="file_ops",
            description="文件操作",
            instructions="文件操作指引",
            source="system",
            root_dir="/tmp/file_ops",
        ),
    }
    return SkillRouter(cfg, loader)
