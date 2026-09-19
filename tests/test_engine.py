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
from excelmanus.auth.providers.base import ResolvedCredential
from excelmanus.config import ExcelManusConfig, ModelProfile
from excelmanus.agent.loop import run_tool_loop
from excelmanus.engine import AgentEngine, ChatResult, ToolCallResult
from excelmanus.events import EventType
from excelmanus.hooks import HookAgentAction, HookDecision, HookEvent, HookResult
from excelmanus.mcp.manager import add_tool_prefix
from excelmanus.memory import TokenCounter
from excelmanus.plan_mode import PendingPlanState, PlanDraft
from excelmanus.security.policy import is_plan_active
from excelmanus.skillpacks import SkillMatchResult, Skillpack
from excelmanus.subagent import SubagentResult
from excelmanus.task_list import TaskStatus
from excelmanus.tools import ToolRegistry, task_tools
from excelmanus.tools.registry import ToolDef

def _make_config(**overrides) -> ExcelManusConfig:
    """创建测试用配置。"""
    defaults = {'api_key': 'test-key', 'base_url': 'https://test.example.com/v1', 'model': 'test-model', 'max_iterations': 20, 'max_consecutive_failures': 3, 'workspace_root': str(Path(__file__).resolve().parent)}
    defaults.update(overrides)
    return ExcelManusConfig(**defaults)

def _make_registry_with_tools() -> ToolRegistry:
    """创建包含简单测试工具的 ToolRegistry。"""
    registry = ToolRegistry()

    def add_numbers(a: int, b: int) -> int:
        return a + b

    def fail_tool() -> str:
        raise RuntimeError('工具执行失败')
    tools = [ToolDef(name='add_numbers', description='两数相加', input_schema={'type': 'object', 'properties': {'a': {'type': 'integer'}, 'b': {'type': 'integer'}}, 'required': ['a', 'b']}, func=add_numbers), ToolDef(name='fail_tool', description='总是失败的工具', input_schema={'type': 'object', 'properties': {}}, func=fail_tool)]
    registry.register_tools(tools)
    return registry

def _activate_test_tools(engine: AgentEngine, tools: list[str] | None=None) -> None:
    """为测试激活一个 Skillpack，注入 skill context。"""
    engine._active_skills = [Skillpack(name='_test_scope', description='test scope', instructions='test', source='system', root_dir='/tmp/_test_scope')]

def _make_text_response(content: str) -> MagicMock:
    """构造一个纯文本 LLM 响应（无 tool_calls）。"""
    message = SimpleNamespace(content=content, tool_calls=None)
    choice = SimpleNamespace(message=message)
    response = SimpleNamespace(choices=[choice])
    return response

def _make_tool_call_response(tool_calls: list[tuple[str, str, str]], content: str | None=None) -> MagicMock:
    """构造一个包含 tool_calls 的 LLM 响应。

    Args:
        tool_calls: [(tool_call_id, tool_name, arguments_json), ...]
        content: 可选的文本内容
    """
    tc_objects = []
    for call_id, name, args in tool_calls:
        tc = SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=args))
        tc_objects.append(tc)
    message = SimpleNamespace(content=content, tool_calls=tc_objects)
    choice = SimpleNamespace(message=message)
    response = SimpleNamespace(choices=[choice])
    return response

class TestAgentEngineInit:
    """AgentEngine 初始化测试。"""

    def test_creates_async_client(self) -> None:
        """验证初始化时创建 AsyncOpenAI 客户端。"""
        config = _make_config()
        registry = ToolRegistry()
        engine = AgentEngine(config, registry)
        assert engine._client is not None
        assert engine._config is config
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
        result = await engine.followup('/fullaccess status')
        assert isinstance(result, ChatResult)
        assert 'restricted' in result.reply
        assert engine.full_access_enabled is False
        assert engine.last_route_result.route_mode == 'control_command'

    @pytest.mark.asyncio
    async def test_on_then_off(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        on_result = await engine.followup('/fullaccess')
        assert 'full_access' in on_result.reply
        assert engine.full_access_enabled is True
        assert engine.last_route_result.route_mode == 'control_command'
        off_result = await engine.followup('/fullaccess off')
        assert 'restricted' in off_result.reply
        assert engine.full_access_enabled is False
        assert engine.last_route_result.route_mode == 'control_command'

    @pytest.mark.asyncio
    async def test_command_does_not_invoke_llm_or_write_memory(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        mocked_create = AsyncMock(return_value=_make_text_response('不应被调用'))
        engine._client.chat.completions.create = mocked_create
        before_count = len(engine.memory.get_messages())
        result = await engine.followup('/full_access status')
        assert 'restricted' in result.reply
        mocked_create.assert_not_called()
        after_count = len(engine.memory.get_messages())
        assert before_count == after_count == 1

    @pytest.mark.asyncio
    async def test_route_blocked_skillpacks_switch_with_full_access(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        route_result = SkillMatchResult(skills_used=[], route_mode='llm_confirm', system_contexts=[])
        mock_router = MagicMock()
        mock_router.parse_slash_skill = AsyncMock(return_value=route_result)
        engine._skill_router = mock_router
        engine._client.chat.completions.create = AsyncMock(return_value=_make_text_response('ok'))
        await engine.followup('普通请求')
        _, kwargs_default = mock_router.parse_slash_skill.call_args
        assert kwargs_default['blocked_skillpacks'] == {'excel_code_runner'}
        await engine.followup('/fullaccess on')
        mock_router.parse_slash_skill.reset_mock()
        engine._client.chat.completions.create = AsyncMock(return_value=_make_text_response('ok2'))
        await engine.followup('普通请求2')
        _, kwargs_unlocked = mock_router.parse_slash_skill.call_args
        assert kwargs_unlocked['blocked_skillpacks'] is None


class TestControlCommandCode:
    """会话级 /code 控制命令测试。"""

    @pytest.mark.asyncio
    async def test_status_defaults_to_native(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        result = await engine.followup('/code status')
        assert isinstance(result, ChatResult)
        assert '关闭' in result.reply
        assert engine._present_as == 'native'
        assert engine.last_route_result.route_mode == 'control_command'

    @pytest.mark.asyncio
    async def test_on_then_off(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        on_result = await engine.followup('/code')
        assert '已开启代码模式' in on_result.reply
        assert engine._present_as == 'code'
        assert engine.last_route_result.route_mode == 'control_command'
        off_result = await engine.followup('/code off')
        assert '已关闭代码模式' in off_result.reply
        assert engine._present_as == 'native'

    @pytest.mark.asyncio
    async def test_code_preference_survives_plan_mode(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        await engine.followup('/code on')
        from excelmanus.plan_mode import set_plan_active
        set_plan_active(engine, True)
        status = await engine.followup('/code status')
        assert engine._present_as == 'code'
        assert '观察/计划' in status.reply

    @pytest.mark.asyncio
    async def test_command_does_not_invoke_llm(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        mocked_create = AsyncMock(return_value=_make_text_response('不应被调用'))
        engine._client.chat.completions.create = mocked_create
        result = await engine.followup('/code_mode status')
        assert '关闭' in result.reply
        mocked_create.assert_not_called()


class TestControlCommandSubagent:
    """会话级 /subagent 控制命令测试。"""

    @pytest.mark.asyncio
    async def test_status_defaults_to_enabled(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        result = await engine.followup('/subagent status')
        assert 'enabled' in result.reply
        assert engine.subagent_enabled is True
        assert engine.last_route_result.route_mode == 'control_command'

    @pytest.mark.asyncio
    async def test_off_then_on(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        off_result = await engine.followup('/subagent off')
        assert '已关闭' in off_result.reply
        assert engine.subagent_enabled is False
        assert engine.last_route_result.route_mode == 'control_command'
        on_result = await engine.followup('/subagent on')
        assert '已开启' in on_result.reply
        assert engine.subagent_enabled is True
        assert engine.last_route_result.route_mode == 'control_command'

    @pytest.mark.asyncio
    async def test_no_args_defaults_to_status(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        result = await engine.followup('/subagent')
        assert '当前 subagent 状态' in result.reply
        assert engine.subagent_enabled is True

    @pytest.mark.asyncio
    async def test_alias_sub_agent_supported(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        result = await engine.followup('/sub_agent off')
        assert '已关闭' in result.reply
        assert engine.subagent_enabled is False

    @pytest.mark.asyncio
    async def test_command_does_not_invoke_llm_or_write_memory(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        mocked_create = AsyncMock(return_value=_make_text_response('不应被调用'))
        engine._client.chat.completions.create = mocked_create
        before_count = len(engine.memory.get_messages())
        result = await engine.followup('/subagent off')
        assert '已关闭' in result.reply
        mocked_create.assert_not_called()
        after_count = len(engine.memory.get_messages())
        assert before_count == after_count == 1

    @pytest.mark.asyncio
    async def test_list_command_returns_catalog(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        result = await engine.followup('/subagent list')
        assert 'subagent' in result.reply

    @pytest.mark.asyncio
    async def test_run_command_with_agent_routes_to_delegate(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        engine._delegate_to_subagent = AsyncMock(return_value=SubagentResult(stop_reason='completed', output='执行完成', subagent_name='explorer', permission_mode='readOnly', conversation_id='c1'))
        result = await engine.followup('/subagent run explorer -- 分析这个文件')
        assert result.reply == '执行完成'
        engine._delegate_to_subagent.assert_awaited_once_with(task='分析这个文件', agent_name='explorer', file_paths=None, on_event=None)

    @pytest.mark.asyncio
    async def test_run_command_without_agent_routes_to_delegate(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        engine._delegate_to_subagent = AsyncMock(return_value=SubagentResult(stop_reason='completed', output='执行完成', subagent_name='subagent', permission_mode='default', conversation_id='c1'))
        result = await engine.followup('/subagent run -- 分析这个文件')
        assert result.reply == '执行完成'
        engine._delegate_to_subagent.assert_awaited_once_with(task='分析这个文件', agent_name=None, file_paths=None, on_event=None)

class TestRegistryScan:
    """FileRegistry 后台扫描。"""

    @pytest.mark.asyncio
    async def test_first_notice_does_not_block_when_scan_running(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        if engine._file_registry is None:
            pytest.skip('FileRegistry 未初始化（无 database）')
        gate = threading.Event()

        def _slow_scan(*_args, **_kwargs):
            gate.wait(timeout=2)
            from excelmanus.file_registry import ScanResult
            return ScanResult(total_files=0)
        with patch.object(engine._file_registry, 'scan_workspace', side_effect=_slow_scan):
            t0 = time.monotonic()
            started = engine.start_registry_scan()
            elapsed = time.monotonic() - t0
            assert started is True
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
            pytest.skip('FileRegistry 未初始化（无 database）')
        gate = threading.Event()

        def _slow_scan(*_args, **_kwargs):
            gate.wait(timeout=2)
            from excelmanus.file_registry import ScanResult
            return ScanResult(total_files=3)
        with patch.object(engine._file_registry, 'scan_workspace', side_effect=_slow_scan):
            build_reply = await engine.followup('/registry scan')
            assert '后台开始 FileRegistry 扫描' in build_reply.reply
            status_reply = await engine.followup('/registry status')
            assert '后台扫描中' in status_reply.reply
            gate.set()
            assert engine._registry_scan_task is not None
            await engine._registry_scan_task
            final_status = await engine.followup('/registry status')
            assert '已就绪' in final_status.reply

class TestModelSwitchConsistency:
    """模型切换与激活客户端一致性测试。"""

    def test_switch_model_updates_active_client(self) -> None:
        config = _make_config(model='main-a', models=(ModelProfile(name='alt', model='main-b', api_key='alt-key', base_url='https://alt.example.com/v1', description='备选模型'),))
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        assert engine._active_model == 'main-a'
        msg = engine.switch_model('alt')
        assert '已切换到模型' in msg
        assert engine._active_model == 'main-b'
        assert engine._active_model_name == 'alt'
        assert engine._client is engine._llm_clients.client

    def test_switch_model_rejects_unknown_name(self) -> None:
        config = _make_config(model='main-a', models=(ModelProfile(name='alt', model='main-b', api_key='alt-key', base_url='https://alt.example.com/v1', description='备选'),))
        engine = AgentEngine(config, _make_registry_with_tools())
        msg = engine.switch_model('missing')
        assert '未找到模型' in msg
        assert engine._active_model == 'main-a'

    def test_switch_model_rejects_default_alias(self) -> None:
        config = _make_config(model='main-a', models=(ModelProfile(name='alt', model='main-b', api_key='alt-key', base_url='https://alt.example.com/v1'),))
        engine = AgentEngine(config, _make_registry_with_tools())
        msg = engine.switch_model('default')
        assert '未找到模型' in msg
        assert engine._active_model == 'main-a'

    def test_switch_model_rejects_deprecated_model_id(self) -> None:
        config = _make_config(model='main-a', models=(ModelProfile(name='legacy', model='claude-3.5-sonnet', api_key='alt-key', base_url='https://alt.example.com/v1', description='legacy'),))
        engine = AgentEngine(config, _make_registry_with_tools())
        msg = engine.switch_model('legacy')
        assert '已弃用' in msg
        assert 'claude-sonnet-5' in msg
        assert engine._active_model == 'main-a'

    def test_extract_retry_after_seconds_from_nested_exception(self) -> None:

        class _RateLimitError(Exception):

            def __init__(self) -> None:
                super().__init__('rate limited')
                self.response = SimpleNamespace(headers={'Retry-After': '0.6'})
        wrapped = RuntimeError('Gemini API 请求失败: ')
        wrapped.__cause__ = _RateLimitError()
        from excelmanus.engine_core.llm_caller import extract_retry_after_seconds
        retry_after = extract_retry_after_seconds(wrapped)
        assert retry_after == pytest.approx(0.6)

class TestStreamFallbackBehavior:
    """流式失败回退策略测试。"""

    @pytest.mark.asyncio
    async def test_auth_error_from_stream_path_skips_non_stream_fallback(self) -> None:
        """401/403 鉴权错误应直接抛出，不再做非流式重复请求。"""

        class _FakeAuthError(Exception):

            def __init__(self, message: str) -> None:
                self.status_code = 401
                super().__init__(message)
        config = _make_config()
        engine = AgentEngine(config, _make_registry_with_tools())
        engine.memory.add_user_message('测试鉴权失败')
        route_result = SkillMatchResult(skills_used=[], route_mode='fallback', system_contexts=[])
        auth_exc = _FakeAuthError('Missing scopes: model.request')
        mocked_call = AsyncMock(side_effect=auth_exc)
        engine._llm_caller.create_chat_completion_with_retry = mocked_call
        with pytest.raises(_FakeAuthError):
            await run_tool_loop(engine, route_result, on_event=None)
        assert mocked_call.await_count == 1

class TestContextBudgetAndHardCap:
    """上下文预算与工具结果全局硬截断测试。"""

    @pytest.mark.asyncio
    async def test_tool_loop_messages_fit_max_context_budget(self) -> None:
        config = _make_config(max_context_tokens=40000)
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        engine.memory.add_user_message('测试上下文预算')
        route_result = SkillMatchResult(skills_used=[], tool_scope=['add_numbers'], route_mode='fallback', system_contexts=['X' * 8000])
        mocked_create = AsyncMock(return_value=_make_text_response('ok'))
        engine._client.chat.completions.create = mocked_create
        result = await run_tool_loop(engine, route_result, on_event=None)
        assert result.reply == 'ok'
        assert mocked_create.call_count == 1
        _, kwargs = mocked_create.call_args
        sent_messages = kwargs['messages']
        total_tokens = sum((TokenCounter.count_message(m) for m in sent_messages))
        assert total_tokens <= int(config.max_context_tokens * 0.9)

    @pytest.mark.asyncio
    async def test_tool_loop_returns_actionable_error_when_system_prompt_itself_over_budget(self) -> None:
        config = _make_config(max_context_tokens=20)
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        engine.memory.add_user_message('测试极小上下文')
        mocked_create = AsyncMock(return_value=_make_text_response('不应调用'))
        engine._client.chat.completions.create = mocked_create
        route_result = SkillMatchResult(skills_used=[], route_mode='fallback', system_contexts=[])
        result = await run_tool_loop(engine, route_result, on_event=None)
        assert '系统上下文过长' in result.reply
        mocked_create.assert_not_called()

    @pytest.mark.asyncio
    async def test_execute_tool_call_applies_global_hard_cap(self) -> None:

        def long_tool() -> str:
            return 'A' * 500
        registry = ToolRegistry()
        registry.register_tools([ToolDef(name='long_tool', description='长文本工具', input_schema={'type': 'object', 'properties': {}}, func=long_tool, max_result_chars=0)])
        config = _make_config(tool_result_hard_cap_chars=80)
        engine = AgentEngine(config, registry)
        tc = SimpleNamespace(id='call_long', function=SimpleNamespace(name='long_tool', arguments='{}'))
        result = await engine._execute_tool_call(tc, tool_scope=['long_tool'], on_event=None, iteration=1, route_result=None)
        assert result.success is True
        assert '结果已截断' in result.result
        assert '上限: 80 字符' in result.result

class TestTaskUpdateFailureSemantics:
    """task_update 失败语义与事件一致性测试。"""

    @pytest.mark.asyncio
    async def test_invalid_transition_returns_failure_and_no_task_item_updated_event(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        engine._task_store.create('测试任务', ['子任务A'])
        tc = SimpleNamespace(id='call_task_update_1', function=SimpleNamespace(name='task_update', arguments=json.dumps({'task_index': 0, 'status': 'completed'})))
        events: list = []
        result = await engine._execute_tool_call(tc=tc, tool_scope=['task_update'], on_event=events.append, iteration=1, route_result=None)
        assert result.success is False
        assert '非法状态转换' in result.result
        assert all((event.event_type != EventType.TASK_ITEM_UPDATED for event in events))
        assert engine._task_store.current is not None
        assert engine._task_store.current.items[0].status == TaskStatus.PENDING

class TestPlanCommand:
    """``/plan`` 是控制面命令，不进模型历史。"""

    @pytest.mark.asyncio
    async def test_plan_command_toggles_session_flag(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        result = await engine.followup('/plan on')
        assert '计划模式' in result.reply
        assert is_plan_active(engine)
        assert engine._current_chat_mode == 'plan'
        status = await engine.followup('/plan status')
        assert '开启' in status.reply
        off = await engine.followup('/plan off')
        assert '关闭' in off.reply
        assert not is_plan_active(engine)

    @pytest.mark.asyncio
    async def test_chat_mode_not_passed_to_slash_parser(self) -> None:
        """plan/read 不再改目录；chat_mode 不传给斜杠解析。"""
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        engine._client.chat.completions.create = AsyncMock(return_value=_make_text_response('ok'))
        mock_router = MagicMock()
        mock_router.parse_slash_skill = AsyncMock(return_value=SkillMatchResult(skills_used=[], route_mode='all_tools', system_contexts=[]))
        engine._skill_router = mock_router
        await engine.followup('分析数据', chat_mode='read')
        assert mock_router.parse_slash_skill.call_count == 1
        assert 'chat_mode' not in mock_router.parse_slash_skill.call_args.kwargs

class TestManualSkillSlashCommand:
    """手动 Skill 斜杠命令解析与路由。"""

    @pytest.mark.asyncio
    async def test_route_mode_is_all_tools_when_skill_router_missing(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        engine._skill_router = None
        engine._client.chat.completions.create = AsyncMock(return_value=_make_text_response('ok'))
        result = await engine.followup('请读取数据')
        assert result.reply == 'ok'
        assert engine.last_route_result.route_mode == 'all_tools'

    @pytest.mark.asyncio
    async def test_slash_skill_command_maps_to_slash_route_args(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        route_result = SkillMatchResult(skills_used=['data_basic'], route_mode='hint_direct', system_contexts=[])
        mock_loader = MagicMock()
        mock_loader.get_skillpacks.return_value = {'data_basic': MagicMock()}
        mock_router = MagicMock()
        mock_router._loader = mock_loader
        mock_router.parse_slash_skill = AsyncMock(return_value=route_result)
        engine._skill_router = mock_router
        engine._client.chat.completions.create = AsyncMock(return_value=_make_text_response('ok'))
        result = await engine.followup('/data_basic 请分析这个文件')
        assert result.reply == 'ok'
        args, kwargs = mock_router.parse_slash_skill.call_args
        assert args[0] == 'data_basic'
        assert kwargs['raw_args'] == '请分析这个文件'

    @pytest.mark.asyncio
    async def test_embedded_slash_skill_command_maps_to_slash_route_args(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        route_result = SkillMatchResult(skills_used=['verification-before-completion'], route_mode='hint_direct', system_contexts=[])
        mock_loader = MagicMock()
        mock_loader.get_skillpacks.return_value = {'verification-before-completion': MagicMock()}
        mock_router = MagicMock()
        mock_router._loader = mock_loader
        mock_router.parse_slash_skill = AsyncMock(return_value=route_result)
        engine._skill_router = mock_router
        engine._client.chat.completions.create = AsyncMock(return_value=_make_text_response('ok'))
        result = await engine.followup('查看文件夹下 /verification-before-completion 查看哪个表格行数最多')
        assert result.reply == 'ok'
        args, kwargs = mock_router.parse_slash_skill.call_args
        assert args[0] == 'verification-before-completion'
        assert kwargs['raw_args'] == '查看哪个表格行数最多'

    @pytest.mark.asyncio
    async def test_explicit_slash_command_arguments_pass_through(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        route_result = SkillMatchResult(skills_used=['data_basic'], route_mode='slash_direct', system_contexts=[], parameterized=True)
        mock_loader = MagicMock()
        mock_loader.get_skillpacks.return_value = {'data_basic': MagicMock()}
        mock_router = MagicMock()
        mock_router._loader = mock_loader
        mock_router.parse_slash_skill = AsyncMock(return_value=route_result)
        engine._skill_router = mock_router
        engine._client.chat.completions.create = AsyncMock(return_value=_make_text_response('ok'))
        await engine.followup('执行技能', slash_command='data_basic', raw_args='"sales data.xlsx" bar')
        first_call = mock_router.parse_slash_skill.call_args_list[0]
        args, kwargs = first_call
        assert args[0] == 'data_basic'
        assert kwargs['raw_args'] == '"sales data.xlsx" bar'

    def test_resolve_skill_command_normalizes_dash_and_underscore(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        mock_loader = MagicMock()
        mock_loader.get_skillpacks.return_value = {'data_basic': MagicMock()}
        mock_router = MagicMock()
        mock_router._loader = mock_loader
        engine._skill_router = mock_router
        assert engine.resolve_skill_command('/data_basic') == 'data_basic'
        assert engine.resolve_skill_command('/data-basic 参数') == 'data_basic'
        assert engine.resolve_skill_command('/DATA_BASIC') == 'data_basic'

    def test_resolve_skill_command_ignores_path_like_input(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        mock_loader = MagicMock()
        mock_loader.get_skillpacks.return_value = {'data_basic': MagicMock()}
        mock_router = MagicMock()
        mock_router._loader = mock_loader
        engine._skill_router = mock_router
        assert engine.resolve_skill_command('/Users/test/file.xlsx') is None
        assert engine.resolve_skill_command('/tmp/data.xlsx') is None

    def test_resolve_skill_command_ignores_embedded_path_like_input(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        mock_loader = MagicMock()
        mock_loader.get_skillpacks.return_value = {'verification-before-completion': MagicMock()}
        mock_router = MagicMock()
        mock_router._loader = mock_loader
        engine._skill_router = mock_router
        assert engine.resolve_skill_command('请读取 /tmp/data.xlsx 的前10行') is None

    def test_resolve_skill_command_supports_namespace(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        mock_loader = MagicMock()
        mock_loader.get_skillpacks.return_value = {'team/data-cleaner': MagicMock()}
        mock_router = MagicMock()
        mock_router._loader = mock_loader
        engine._skill_router = mock_router
        assert engine.resolve_skill_command('/team/data-cleaner --mode fast') == 'team/data-cleaner'
        assert engine.resolve_skill_command('/team/data-cleaner.xlsx') is None

    def test_resolve_skill_command_respects_user_invocable(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        mock_loader = MagicMock()
        mock_loader.get_skillpacks.return_value = {'private_skill': Skillpack(name='private_skill', description='private', instructions='', source='project', root_dir='/tmp/private', user_invocable=False)}
        mock_router = MagicMock()
        mock_router._loader = mock_loader
        engine._skill_router = mock_router
        assert engine.resolve_skill_command('/private_skill run') is None

    @pytest.mark.asyncio
    async def test_chat_rejects_slash_for_not_user_invocable_skill(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        engine._route_skills = AsyncMock(return_value=SkillMatchResult(skills_used=[], route_mode='slash_not_user_invocable', system_contexts=[]))
        result = await engine.followup('/private_skill do', slash_command='private_skill', raw_args='do')
        assert isinstance(result, ChatResult)
        assert '不允许手动调用' in result.reply

    @pytest.mark.asyncio
    async def test_guidance_only_slash_with_args_falls_back_to_task_route(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        guidance_skill = Skillpack(name='guidance_only', description='仅方法论约束', instructions='只提供规范，不直接绑定工具', source='project', root_dir='/tmp/guidance_only')
        mock_loader = MagicMock()
        mock_loader.get_skillpack.return_value = guidance_skill
        mock_loader.get_skillpacks.return_value = {'guidance_only': guidance_skill}
        mock_router = MagicMock()
        mock_router._loader = mock_loader
        engine._skill_router = mock_router
        slash_route = SkillMatchResult(skills_used=['guidance_only'], route_mode='slash_direct', system_contexts=['[Skillpack] guidance_only'], parameterized=True)
        engine._route_skills = AsyncMock(return_value=slash_route)
        with patch('excelmanus.agent.loop.run_tool_loop', new_callable=AsyncMock, return_value=ChatResult(reply='ok')) as loop_mock:
            result = await engine.followup('/guidance_only 查看哪个表格最大', slash_command='guidance_only', raw_args='查看哪个表格最大')
        assert result.reply == 'ok'
        assert engine._route_skills.await_count == 1
        first_call = engine._route_skills.await_args_list[0]
        assert first_call.args[0] == '/guidance_only 查看哪个表格最大'
        assert first_call.kwargs['slash_command'] == 'guidance_only'
        loop_route = loop_mock.await_args.args[1]
        assert loop_route.route_mode == 'all_tools'
        assert loop_route.skills_used == ['guidance_only']
        assert loop_route.tool_scope == []
        assert loop_route.system_contexts == []
        user_messages = [msg.get('content', '') for msg in engine.memory.get_messages() if msg.get('role') == 'user']
        assert '查看哪个表格最大' in user_messages
        assert any('<skill-invocation name="guidance_only">' in str(item) for item in user_messages)

class TestForkPathRemoved:
    """fork 链路已硬移除，仅保留显式 delegate_to_subagent。"""

    @pytest.mark.asyncio
    async def test_chat_with_active_skill_no_longer_auto_delegates(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        engine._active_skills = [Skillpack(name='excel_code_runner', description='代码处理', instructions='', source='project', root_dir='/tmp/skill')]
        route_result = SkillMatchResult(skills_used=['excel_code_runner'], tool_scope=['add_numbers'], route_mode='fallback', system_contexts=['[Skillpack] excel_code_runner'])
        engine._route_skills = AsyncMock(return_value=route_result)
        engine._delegate_to_subagent = AsyncMock(return_value=SubagentResult(stop_reason='completed', output='不应被调用', subagent_name='subagent', permission_mode='default', conversation_id='c1'))
        engine._client.chat.completions.create = AsyncMock(return_value=_make_text_response('主代理执行完成。'))
        result = await engine.followup('请处理这个大文件')
        assert result.reply == '主代理执行完成。'
        engine._delegate_to_subagent.assert_not_awaited()
        engine._client.chat.completions.create.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_activate_skill_success_no_longer_triggers_auto_delegate(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        engine.memory.add_user_message('请分析销售趋势')
        engine._delegate_to_subagent = AsyncMock()

        async def _fake_execute_tool_call(*args, **kwargs) -> ToolCallResult:
            engine._active_skills = [Skillpack(name='team/analyst', description='普通技能', instructions='', source='project', root_dir='/tmp/skill')]
            return ToolCallResult(tool_name='activate_skill', arguments={'skill_name': 'team/analyst'}, result='OK', success=True)
        engine._execute_tool_call = AsyncMock(side_effect=_fake_execute_tool_call)
        final_response = _make_text_response('主代理继续执行。')
        engine._client.chat.completions.create = AsyncMock(side_effect=[_make_tool_call_response([('call_1', 'activate_skill', json.dumps({'skill_name': 'team/analyst'}))])] + [final_response] * 20)
        route_result = SkillMatchResult(skills_used=[], route_mode='fallback', system_contexts=[])
        result = await run_tool_loop(engine, route_result, on_event=None)
        assert result.reply in ('主代理继续执行。', '分析完成。')
        engine._delegate_to_subagent.assert_not_awaited()

    def test_engine_has_no_run_fork_skill_entrypoint(self) -> None:
        engine = AgentEngine(_make_config(), _make_registry_with_tools())
        assert not hasattr(engine, '_run_fork_skill')

class TestDelegateSubagent:
    """delegate_to_subagent 元工具测试。"""

    @pytest.mark.asyncio
    async def test_delegate_tool_call_runs_subagent_and_returns_summary(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        engine._delegate_to_subagent = AsyncMock(return_value=SubagentResult(stop_reason='completed', output='子代理摘要', subagent_name='explorer', permission_mode='readOnly', conversation_id='c1'))
        tc = SimpleNamespace(id='call_1', function=SimpleNamespace(name='delegate', arguments=json.dumps({'task': '探查销量异常', 'file_paths': ['sales.xlsx']})))
        result = await engine._execute_tool_call(tc=tc, tool_scope=['delegate'], on_event=None, iteration=1)
        assert result.success is True
        assert result.result == '子代理摘要'

    @pytest.mark.asyncio
    async def test_delegate_refusal_does_not_bubble_approval(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        engine._delegate_to_subagent = AsyncMock(
            return_value=SubagentResult(
                stop_reason='refusal',
                output='只读子代理拒绝写入：run_code',
                diagnostic='只读子代理拒绝写入：run_code',
                subagent_name='explorer',
                permission_mode='readOnly',
                conversation_id='conv_1',
            )
        )
        tc = SimpleNamespace(id='call_1', function=SimpleNamespace(name='delegate', arguments=json.dumps({'task': '统计城市销售额', 'agent_name': 'explorer'})))
        first = await engine._execute_tool_call(tc=tc, tool_scope=['delegate'], on_event=None, iteration=1)
        assert first.success is False
        assert '拒绝' in (first.result or '')
        assert engine._question_flow.has_pending() is False

    @pytest.mark.asyncio
    async def test_delegate_rejects_invalid_file_paths_type(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        tc = SimpleNamespace(id='call_1', function=SimpleNamespace(name='delegate_to_subagent', arguments=json.dumps({'task': '探查销量异常', 'file_paths': 'sales.xlsx'})))
        result = await engine._execute_tool_call(tc=tc, tool_scope=['delegate_to_subagent'], on_event=None, iteration=1)
        assert result.success is False
        assert 'file_paths 必须为字符串数组' in result.result

    @pytest.mark.asyncio
    async def test_delegate_rejects_invalid_agent_name_type(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        tc = SimpleNamespace(id='call_1', function=SimpleNamespace(name='delegate_to_subagent', arguments=json.dumps({'task': '探查销量异常', 'agent_name': 123})))
        result = await engine._execute_tool_call(tc=tc, tool_scope=['delegate_to_subagent'], on_event=None, iteration=1)
        assert result.success is False
        assert 'agent_name 必须为字符串' in result.result

    @pytest.mark.asyncio
    async def test_legacy_subagent_approval_question_fails_loud(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        pending = engine._question_flow.enqueue(
            {
                'header': '高风险确认',
                'text': '历史审批问题',
                'options': [{'label': '拒绝本次操作', 'description': 'x'}],
                'multiSelect': False,
            },
            'legacy_approval',
        )
        engine._system_question_actions[pending.question_id] = {
            'type': 'subagent_high_risk_approval',
            'approval_id': 'a1',
        }
        result = await engine._interaction_handler.handle_pending_question_answer(
            user_message='1',
            on_event=None,
        )
        assert result is not None
        assert '审批冒泡已移除' in result.reply
        assert engine._question_flow.has_pending() is False

    @pytest.mark.asyncio
    async def test_tool_loop_logs_latency_without_llm_call_store(self) -> None:
        config = _make_config()
        engine = AgentEngine(config, _make_registry_with_tools())
        engine._llm_call_store = None
        engine.memory.add_user_message('测延迟日志')
        response = _make_text_response('ok')
        response.usage = SimpleNamespace(prompt_tokens=10, completion_tokens=4, _ttft_ms=12.0)
        engine._client.chat.completions.create = AsyncMock(return_value=response)
        route_result = SkillMatchResult(skills_used=[], route_mode='fallback', system_contexts=[])
        result = await run_tool_loop(engine, route_result, on_event=None)
        assert result.reply == 'ok'

class TestAskUserFlow:
    """ask_user 挂起恢复与队列行为测试。"""

    @staticmethod
    def _ask_question_payload(*, header: str='实现方案', text: str='请选择实现方案', multi_select: bool=False) -> dict:
        return {'questions': [{'header': header, 'text': text, 'options': [{'label': '方案A', 'description': '快速实现'}, {'label': '方案B', 'description': '稳健实现'}], 'multiSelect': multi_select}]}

    @pytest.mark.asyncio
    async def test_ask_user_blocking_inline_completes_without_reroute(self) -> None:
        """阻塞式 ask_user：question_resolver 返回 '1' 后内联完成，不中断循环。"""
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        route_result = SkillMatchResult(skills_used=[], tool_scope=['ask_user', 'add_numbers'], route_mode='llm_confirm', system_contexts=[])
        engine._route_skills = AsyncMock(return_value=route_result)

        async def _resolver(q):
            return '1'
        ask_response = _make_tool_call_response([('call_q1', 'ask_user', json.dumps(self._ask_question_payload(), ensure_ascii=False))])
        do_work_response = _make_tool_call_response([('call_add', 'add_numbers', json.dumps({'a': 1, 'b': 2}))])
        final_response = _make_text_response('已按你的选择完成，结果是 3。')
        engine._client.chat.completions.create = AsyncMock(side_effect=[do_work_response, ask_response, final_response])
        result = await engine.followup('请完成任务', question_resolver=_resolver)
        assert result.reply == '已按你的选择完成，结果是 3。'
        assert engine.has_pending_question() is False
        assert engine._route_skills.await_count == 1
        tool_msgs = [m for m in engine.memory.get_messages() if m.get('role') == 'tool']
        ask_msg = next((m for m in tool_msgs if m.get('tool_call_id') == 'call_q1'))
        ask_payload = json.loads(ask_msg['content'])
        assert ask_payload['question_id'].startswith('qst_')
        assert ask_payload['multi_select'] is False
        assert ask_payload['selected_options'][0]['label'] == '方案A'

    @pytest.mark.asyncio
    async def test_blocking_multiple_questions_resolved_inline(self) -> None:
        """阻塞式多问题：question_resolver 依次回答两个问题后内联完成。"""
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        route_result = SkillMatchResult(skills_used=[], tool_scope=['ask_user', 'add_numbers'], route_mode='llm_confirm', system_contexts=[])
        engine._route_skills = AsyncMock(return_value=route_result)
        _answers = iter(['1', '1\n自定义策略'])

        async def _resolver(q):
            return next(_answers)
        first_round = _make_tool_call_response([('call_q1', 'ask_user', json.dumps(self._ask_question_payload(header='语言', text='选择开发语言', multi_select=False), ensure_ascii=False)), ('call_add', 'add_numbers', json.dumps({'a': 10, 'b': 20})), ('call_q2', 'ask_user', json.dumps(self._ask_question_payload(header='约束', text='选择约束策略', multi_select=True), ensure_ascii=False))])
        final_response = _make_text_response('两个问题都确认完毕。')
        engine._client.chat.completions.create = AsyncMock(side_effect=[first_round, final_response])
        result = await engine.followup('开始执行', question_resolver=_resolver)
        assert result.reply == '两个问题都确认完毕。'
        assert engine.has_pending_question() is False
        assert engine._route_skills.await_count == 1
        tool_msgs = [m for m in engine.memory.get_messages() if m.get('role') == 'tool']
        assert any((m.get('tool_call_id') == 'call_q1' for m in tool_msgs))
        q2_msg = next((m for m in tool_msgs if m.get('tool_call_id') == 'call_q2'))
        q2_payload = json.loads(q2_msg['content'])
        assert q2_payload['multi_select'] is True
        assert any((item['label'] == '方案A' for item in q2_payload['selected_options']))
        assert q2_payload['other_text'] == '自定义策略'

    @pytest.mark.asyncio
    async def test_blocking_ask_user_no_pending_state_after_chat(self) -> None:
        """阻塞式 ask_user：chat() 返回后不留 pending 状态。"""
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        route_result = SkillMatchResult(skills_used=[], tool_scope=['ask_user'], route_mode='llm_confirm', system_contexts=[])
        engine._route_skills = AsyncMock(return_value=route_result)

        async def _resolver(q):
            return '1'
        ask_response = _make_tool_call_response([('call_q1', 'ask_user', json.dumps(self._ask_question_payload(), ensure_ascii=False))])
        final_response = _make_text_response('已恢复执行。')
        engine._client.chat.completions.create = AsyncMock(side_effect=[ask_response, final_response])
        result = await engine.followup('发起提问', question_resolver=_resolver)
        assert result.reply == '已恢复执行。'
        assert engine.has_pending_question() is False

class TestToolCallingLoopApprovalResolver:
    """_tool_calling_loop 的审批分支行为锁定测试。"""

    @pytest.mark.asyncio
    async def test_inline_approval_accept_continues_and_emits_resolved_event(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        route_result = SkillMatchResult(skills_used=[], route_mode='fallback', system_contexts=[])
        pending = engine._approval.create_pending(tool_name='run_shell', arguments={'command': 'echo ok'}, tool_scope=['run_shell'])
        engine.memory.add_user_message('请执行')
        engine._client.chat.completions.create = AsyncMock(side_effect=[_make_tool_call_response([('call_approve', 'run_shell', json.dumps({'command': 'echo ok'}))]), _make_text_response('审批后继续执行完成。')])
        engine._execute_tool_call = AsyncMock(return_value=ToolCallResult(tool_name='run_shell', arguments={'command': 'echo ok'}, result=engine._format_pending_prompt(pending), success=True, pending_approval=True, approval_id=pending.approval_id))

        async def _approval_resolver(_pending):
            assert _pending.approval_id == pending.approval_id
            return 'accept'

        async def _execute_approved_pending(_pending, *, on_event=None, tool_call_id=None):
            assert _pending.approval_id == pending.approval_id
            engine._approval.clear_pending()
            return (True, '已执行 run_shell', None)
        engine._execute_approved_pending = AsyncMock(side_effect=_execute_approved_pending)
        events: list[Any] = []
        result = await run_tool_loop(engine, route_result, on_event=events.append, approval_resolver=_approval_resolver)
        assert result.reply == '审批后继续执行完成。'
        assert engine._execute_approved_pending.await_count == 1
        assert any((event.event_type == EventType.APPROVAL_RESOLVED and event.success for event in events))
        tool_messages = [msg for msg in engine.memory.get_messages() if msg.get('role') == 'tool']
        approved_tool_msg = next((msg for msg in tool_messages if msg.get('tool_call_id') == 'call_approve'))
        assert '已执行 run_shell' in approved_tool_msg.get('content', '')

    @pytest.mark.asyncio
    async def test_inline_approval_reject_continues_and_emits_failed_event(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        route_result = SkillMatchResult(skills_used=[], route_mode='fallback', system_contexts=[])
        pending = engine._approval.create_pending(tool_name='run_shell', arguments={'command': 'echo fail'}, tool_scope=['run_shell'])
        engine.memory.add_user_message('请执行')
        engine._client.chat.completions.create = AsyncMock(side_effect=[_make_tool_call_response([('call_reject', 'run_shell', json.dumps({'command': 'echo fail'}))]), _make_text_response('拒绝后继续执行完成。')])
        engine._execute_tool_call = AsyncMock(return_value=ToolCallResult(tool_name='run_shell', arguments={'command': 'echo fail'}, result=engine._format_pending_prompt(pending), success=True, pending_approval=True, approval_id=pending.approval_id))
        engine._execute_approved_pending = AsyncMock(return_value=(True, '不应执行', None))
        events: list[Any] = []

        async def _approval_resolver(_pending):
            assert _pending.approval_id == pending.approval_id
            return None
        result = await run_tool_loop(engine, route_result, on_event=events.append, approval_resolver=_approval_resolver)
        assert result.reply == '拒绝后继续执行完成。'
        engine._execute_approved_pending.assert_not_awaited()
        assert any((event.event_type == EventType.APPROVAL_RESOLVED and (not event.success) for event in events))
        tool_messages = [msg for msg in engine.memory.get_messages() if msg.get('role') == 'tool']
        rejected_tool_msg = next((msg for msg in tool_messages if msg.get('tool_call_id') == 'call_reject'))
        assert '已拒绝待确认操作' in rejected_tool_msg.get('content', '')

    @pytest.mark.asyncio
    async def test_pending_approval_without_resolver_blocks_and_resolves_via_registry(self) -> None:
        """无 resolver 时，审批通过 InteractionRegistry Future 阻塞等待并内联处理。"""
        import asyncio
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        route_result = SkillMatchResult(skills_used=[], route_mode='fallback', system_contexts=[])
        pending = engine._approval.create_pending(tool_name='run_shell', arguments={'command': 'echo pending'}, tool_scope=['run_shell'])
        engine._client.chat.completions.create = AsyncMock(side_effect=[_make_tool_call_response([('call_pending', 'run_shell', json.dumps({'command': 'echo pending'}))]), _make_text_response('审批通过，已执行完成。')])
        engine._execute_tool_call = AsyncMock(return_value=ToolCallResult(tool_name='run_shell', arguments={'command': 'echo pending'}, result='pending', success=True, pending_approval=True, approval_id=pending.approval_id))
        engine._execute_approved_pending = AsyncMock(return_value=(True, 'echo ok', None))

        async def _resolve_later():
            for _ in range(50):
                await asyncio.sleep(0.05)
                if pending.approval_id in engine._interaction_registry._futures:
                    engine._interaction_registry.resolve(pending.approval_id, {'decision': 'accept'})
                    return
        resolve_task = asyncio.create_task(_resolve_later())
        result = await run_tool_loop(engine, route_result, on_event=None)
        await resolve_task
        assert '审批通过' in result.reply or 'echo ok' in result.reply
        engine._execute_approved_pending.assert_awaited_once()

class TestToolCallingLoopWriteGuard:
    """_tool_calling_loop 写入门禁退出行为测试。"""

    @pytest.mark.asyncio
    async def test_write_guard_off_returns_first_text_response(self) -> None:
        """纯文本响应第一轮直接返回，不强制继续。"""
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        route_result = SkillMatchResult(skills_used=[], route_mode='fallback', system_contexts=[])
        engine._client.chat.completions.create = AsyncMock(side_effect=[_make_text_response('先解释步骤，暂未执行工具。'), _make_text_response('仍未执行任何写入工具。')])
        result = await run_tool_loop(engine, route_result, on_event=None)
        assert result.reply == '先解释步骤，暂未执行工具。'
        assert result.iterations == 1

class TestMetaToolDefinitions:
    """元工具定义结构与动态更新测试（task6.4）。"""

    def test_build_meta_tools_schema_structure(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        mock_router = MagicMock()
        mock_router.list_skill_names.return_value = ['data_basic', 'chart_basic']
        engine._skill_router = mock_router
        engine._subagent_registry = MagicMock()
        engine._subagent_registry.build_catalog.return_value = ('可用子代理：\n- folder_summarizer：目录总结', ['folder_summarizer'])
        meta_tools = engine._meta_tool_builder.build_meta_tools()
        assert len(meta_tools) >= 4
        by_name = {tool['function']['name']: tool for tool in meta_tools}
        assert 'skill' in by_name
        assert 'delegate' in by_name
        assert 'list_subagents' in by_name
        assert 'ask_user' in by_name
        activate_tool = by_name['skill']['function']
        activate_params = activate_tool['parameters']
        assert activate_params['required'] == ['name']
        assert set(activate_params['properties']['name']['enum']) == {'chart_basic', 'data_basic'}
        delegate_tool = by_name['delegate']['function']
        delegate_params = delegate_tool['parameters']
        assert delegate_params['required'] == []
        assert 'task' in delegate_params['properties']
        assert 'task_brief' in delegate_params['properties']
        assert 'tasks' in delegate_params['properties']
        assert delegate_params['properties']['task_brief']['type'] == 'object'
        assert delegate_params['properties']['task_brief']['required'] == ['title']
        assert delegate_params['properties']['file_paths']['type'] == 'array'
        assert 'agent_name' in delegate_params['properties']
        assert delegate_params['properties']['agent_name']['enum'] == ['folder_summarizer']
        assert delegate_tool['description'] == (
            "把一项自包含任务交给具名子代理。它看不到本段对话，只回终态结果不回中间步骤。"
            "省略 agent_name 用通用 subagent。下一动作依赖结果时用单任务；独立探查可走 tasks。"
        )
        assert 'folder_summarizer' not in delegate_tool['description']
        ask_user_tool = by_name['ask_user']['function']
        ask_user_params = ask_user_tool['parameters']
        assert ask_user_params['required'] == ['questions']
        questions_schema = ask_user_params['properties']['questions']
        assert questions_schema['type'] == 'array'
        assert questions_schema['minItems'] == 1
        assert questions_schema['maxItems'] == 8
        item_schema = questions_schema['items']
        assert item_schema['required'] == ['text', 'options']
        assert item_schema['properties']['options']['minItems'] == 1
        assert item_schema['properties']['options']['maxItems'] == 4

    def test_build_meta_tools_reflects_updated_catalog(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        mock_router = MagicMock()
        mock_router.list_skill_names.side_effect = [['data_basic'], ['data_basic', 'chart_basic']]
        engine._skill_router = mock_router
        first = engine._meta_tool_builder.build_meta_tools()
        second = engine._meta_tool_builder.build_meta_tools()
        first_enum = first[0]['function']['parameters']['properties']['name']['enum']
        second_enum = second[0]['function']['parameters']['properties']['name']['enum']
        assert first_enum == ['data_basic']
        assert second_enum == ['data_basic', 'chart_basic']

class TestSkillMCPRequirements:
    """Skill 的 MCP 依赖校验。"""

    @pytest.mark.asyncio
    async def test_activate_skill_rejects_when_required_mcp_server_missing(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        skill = Skillpack(name='need_mcp', description='依赖外部 MCP', instructions='调用 context7', source='project', root_dir='/tmp/need_mcp', required_mcp_servers=['context7'])
        mock_loader = MagicMock()
        mock_loader.get_skillpacks.return_value = {'need_mcp': skill}
        mock_router = MagicMock()
        mock_router._loader = mock_loader
        mock_router._find_skill_by_name = MagicMock(return_value=skill)
        engine._skill_router = mock_router
        result = await engine._handle_activate_skill('need_mcp')
        assert 'MCP 依赖未满足' in result
        assert not engine._active_skills

    @pytest.mark.asyncio
    async def test_activate_skill_accepts_when_required_mcp_server_and_tool_ready(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        mcp_tool = add_tool_prefix('context7', 'query_docs')
        registry.register_tool(ToolDef(name=mcp_tool, description='文档查询', input_schema={'type': 'object', 'properties': {}}, func=lambda: 'ok'))
        engine = AgentEngine(config, registry)
        engine._mcp_manager._clients['context7'] = MagicMock()
        skill = Skillpack(name='need_mcp', description='依赖外部 MCP', instructions='调用 context7', source='project', root_dir='/tmp/need_mcp', required_mcp_servers=['context7'], required_mcp_tools=['context7:query_docs'])
        mock_loader = MagicMock()
        mock_loader.get_skillpacks.return_value = {'need_mcp': skill}
        mock_router = MagicMock()
        mock_router._loader = mock_loader
        mock_router._find_skill_by_name = MagicMock(return_value=skill)
        engine._skill_router = mock_router
        result = await engine._handle_activate_skill('need_mcp')
        assert result.startswith('OK')
        assert engine._active_skills
        assert engine._active_skills[-1].name == 'need_mcp'

class TestCommandDispatchAndHooks:

    @pytest.mark.asyncio
    async def test_execute_tool_call_parse_error_returns_failure(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        tc = SimpleNamespace(id='call_bad_json', function=SimpleNamespace(name='add_numbers', arguments='{bad json'))
        result = await engine._execute_tool_call(tc=tc, tool_scope=['add_numbers'], on_event=None, iteration=1)
        assert result.success is False
        assert '工具参数解析错误' in result.result

    @pytest.mark.asyncio
    async def test_pre_tool_hook_deny_blocks_tool(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        engine._active_skills = [Skillpack(name='hook/deny', description='deny hook', instructions='', source='project', root_dir='/tmp/hook', hooks={'PreToolUse': [{'matcher': 'add_numbers', 'hooks': [{'type': 'prompt', 'decision': 'deny', 'reason': 'blocked'}]}]})]
        tc = SimpleNamespace(id='call_hook_deny', function=SimpleNamespace(name='add_numbers', arguments=json.dumps({'a': 1, 'b': 2})))
        result = await engine._execute_tool_call(tc=tc, tool_scope=['add_numbers'], on_event=None, iteration=1)
        assert result.success is False
        assert 'blocked' in result.result

    @pytest.mark.asyncio
    async def test_pre_tool_hook_ask_creates_pending_approval(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        engine._active_skills = [Skillpack(name='hook/ask', description='ask hook', instructions='', source='project', root_dir='/tmp/hook', hooks={'PreToolUse': [{'matcher': 'add_numbers', 'hooks': [{'type': 'prompt', 'decision': 'ask'}]}]})]
        tc = SimpleNamespace(id='call_hook_ask', function=SimpleNamespace(name='add_numbers', arguments=json.dumps({'a': 1, 'b': 2})))
        result = await engine._execute_tool_call(tc=tc, tool_scope=['add_numbers'], on_event=None, iteration=1)
        assert result.success is True
        assert result.pending_approval is True
        assert isinstance(result.approval_id, str) and result.approval_id

    @pytest.mark.asyncio
    async def test_pre_tool_hook_updated_input_is_applied(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        engine._active_skills = [Skillpack(name='hook/update', description='update input hook', instructions='', source='project', root_dir='/tmp/hook', hooks={'PreToolUse': [{'matcher': 'add_numbers', 'hooks': [{'type': 'prompt', 'decision': 'allow', 'updated_input': {'a': 7, 'b': 4}}]}]})]
        tc = SimpleNamespace(id='call_hook_update', function=SimpleNamespace(name='add_numbers', arguments=json.dumps({'a': 1, 'b': 2})))
        result = await engine._execute_tool_call(tc=tc, tool_scope=['add_numbers'], on_event=None, iteration=1)
        assert result.success is True
        assert result.result == '11'

    @pytest.mark.asyncio
    async def test_pre_tool_hook_allow_skips_pending_approval_for_high_risk(self, tmp_path: Path) -> None:
        config = _make_config(workspace_root=str(tmp_path))
        registry = _make_registry_with_tools()

        def write_text_file(file_path: str, content: str) -> str:
            Path(file_path).write_text(content, encoding='utf-8')
            # 返回形状须满足 write_text_file 输出合同（成功对象为必有键）。
            return json.dumps({
                "status": "success", "file_path": file_path,
                "content_version": "v1",
            }, ensure_ascii=False)
        registry.register_tool(ToolDef(name='write_text_file', description='写入文本', input_schema={'type': 'object', 'properties': {'file_path': {'type': 'string'}, 'content': {'type': 'string'}}, 'required': ['file_path', 'content']}, func=write_text_file))
        engine = AgentEngine(config, registry)
        engine._active_skills = [Skillpack(name='hook/allow', description='allow hook', instructions='', source='project', root_dir='/tmp/hook', hooks={'PreToolUse': [{'matcher': 'write_text_file', 'hooks': [{'type': 'prompt', 'decision': 'allow'}]}]})]
        output = tmp_path / 'hook_allow.txt'
        tc = SimpleNamespace(id='call_hook_allow', function=SimpleNamespace(name='write_text_file', arguments=json.dumps({'file_path': str(output), 'content': 'ok'})))
        result = await engine._execute_tool_call(tc=tc, tool_scope=['write_text_file'], on_event=None, iteration=1)
        assert result.success is True
        assert result.pending_approval is False
        assert output.exists()

    def test_non_pre_tool_ask_downgrades_to_continue(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        skill = Skillpack(name='hook/ask_scope', description='ask scope', instructions='', source='project', root_dir='/tmp/hook', hooks={'UserPromptSubmit': {'type': 'prompt', 'decision': 'ask', 'reason': '需要确认'}})
        result = engine._skill_resolver.run_skill_hook(skill=skill, event=HookEvent.USER_PROMPT_SUBMIT, payload={'user_message': '测试'})
        assert result is not None
        assert result.decision == HookDecision.CONTINUE
        assert '不支持 ASK' in result.reason

    @pytest.mark.asyncio
    async def test_pre_tool_agent_hook_runs_subagent_and_injects_context(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        engine.run_subagent = AsyncMock(return_value=SubagentResult(stop_reason='completed', output='子代理摘要', subagent_name='explorer', permission_mode='default', conversation_id='sub_1'))
        engine._active_skills = [Skillpack(name='hook/agent', description='agent hook', instructions='', source='project', root_dir='/tmp/hook', hooks={'PreToolUse': [{'matcher': 'add_numbers', 'hooks': [{'type': 'agent', 'agent_name': 'explorer', 'task': '请检查调用参数', 'inject_summary_as_context': True}]}]})]
        tc = SimpleNamespace(id='call_hook_agent', function=SimpleNamespace(name='add_numbers', arguments=json.dumps({'a': 1, 'b': 2})))
        result = await engine._execute_tool_call(tc=tc, tool_scope=['add_numbers'], on_event=None, iteration=1)
        assert result.success is True
        assert result.result == '3'
        engine.run_subagent.assert_awaited_once()
        assert any(('子代理摘要' in item for item in engine._transient_hook_contexts))

    @pytest.mark.asyncio
    async def test_agent_hook_recursion_guard_respects_on_failure_deny(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        engine._hook_agent_action_depth = 1
        resolved = await engine._skill_resolver.resolve_hook_result(event=HookEvent.PRE_TOOL_USE, hook_result=HookResult(decision=HookDecision.CONTINUE, agent_action=HookAgentAction(task='递归测试', on_failure='deny')), on_event=None)
        assert resolved is not None
        assert resolved.decision == HookDecision.DENY
        assert '递归触发' in resolved.reason

    @pytest.mark.asyncio
    async def test_post_tool_hook_deny_turns_success_to_failure(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        engine._active_skills = [Skillpack(name='hook/post_deny', description='post deny hook', instructions='', source='project', root_dir='/tmp/hook', hooks={'PostToolUse': [{'matcher': 'add_numbers', 'hooks': [{'type': 'prompt', 'decision': 'deny', 'reason': 'post blocked'}]}]})]
        tc = SimpleNamespace(id='call_post_hook_deny', function=SimpleNamespace(name='add_numbers', arguments=json.dumps({'a': 2, 'b': 3})))
        result = await engine._execute_tool_call(tc=tc, tool_scope=['add_numbers'], on_event=None, iteration=1)
        assert result.success is False
        assert '[Hook 拒绝] post blocked' in result.result

    @pytest.mark.asyncio
    async def test_run_code_red_creates_pending_approval(self) -> None:
        config = _make_config(code_policy_enabled=True)
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        dangerous_code = "import subprocess\nsubprocess.run(['echo', 'x'])"
        tc = SimpleNamespace(id='call_run_code_red', function=SimpleNamespace(name='run_code', arguments=json.dumps({'code': dangerous_code})))
        result = await engine._execute_tool_call(tc=tc, tool_scope=None, on_event=None, iteration=1)
        assert result.success is True
        assert result.pending_approval is True
        assert isinstance(result.approval_id, str) and result.approval_id
        assert '需要人工确认' in result.result
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
        mock_response = _make_text_response('你好，这是回复。')
        engine._client.chat.completions.create = AsyncMock(return_value=mock_response)
        result = await engine.followup('你好')
        assert isinstance(result, ChatResult)
        assert result.reply == '你好，这是回复。'
        assert result.reply == '你好，这是回复。'
        assert result.iterations == 1
        assert result.truncated is False
        assert result.tool_calls == []

    @pytest.mark.asyncio
    async def test_empty_content_returns_empty_string(self) -> None:
        """LLM 返回 content=None 时，返回空字符串。"""
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        mock_response = _make_text_response('')
        mock_response.choices[0].message.content = None
        engine._client.chat.completions.create = AsyncMock(return_value=mock_response)
        result = await engine.followup('测试')
        assert result.reply == ''

    @pytest.mark.asyncio
    async def test_string_response_is_treated_as_text_reply(self) -> None:
        """兼容某些网关直接返回纯字符串。"""
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        engine._client.chat.completions.create = AsyncMock(return_value='你好，字符串响应。')
        result = await engine.followup('你好')
        assert isinstance(result, ChatResult)
        assert result.reply == '你好，字符串响应。'
        assert result.tool_calls == []
        assert result.iterations == 1
        assert result.truncated is False

    @pytest.mark.asyncio
    async def test_html_document_response_returns_endpoint_hint(self) -> None:
        """当上游返回 HTML 页面时，返回可操作的配置提示。"""
        config = _make_config(base_url='https://example.invalid/')
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        engine._client.chat.completions.create = AsyncMock(return_value="<!doctype html><html><head><meta charset='utf-8'></head><body>oops</body></html>")
        result = await engine.followup('你是谁')
        assert 'EXCELMANUS_BASE_URL' in result.reply
        assert '/v1' in result.reply
        assert '<!doctype html>' not in result.reply.lower()

class TestChatToolCalling:
    """Tool Calling 循环场景（Requirements 1.1, 1.2, 1.9）。"""

    @pytest.mark.asyncio
    async def test_single_tool_call_then_text(self) -> None:
        """单个 tool_call 执行后，LLM 返回文本结束循环。"""
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        _activate_test_tools(engine)
        tool_response = _make_tool_call_response([('call_1', 'add_numbers', json.dumps({'a': 3, 'b': 5}))])
        text_response = _make_text_response('3 + 5 = 8')
        engine._client.chat.completions.create = AsyncMock(side_effect=[tool_response, text_response])
        result = await engine.followup('计算 3 + 5')
        assert isinstance(result, ChatResult)
        assert result.reply == '3 + 5 = 8'
        assert result.reply == '3 + 5 = 8'
        assert result.iterations == 2
        assert result.truncated is False
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].tool_name == 'add_numbers'
        assert result.tool_calls[0].success is True

    @pytest.mark.asyncio
    async def test_multiple_tool_calls_in_single_response(self) -> None:
        """单轮响应包含多个 tool_calls（Requirement 1.9）。"""
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        _activate_test_tools(engine)
        tool_response = _make_tool_call_response([('call_1', 'add_numbers', json.dumps({'a': 1, 'b': 2})), ('call_2', 'add_numbers', json.dumps({'a': 3, 'b': 4}))])
        text_response = _make_text_response('结果分别是 3 和 7')
        engine._client.chat.completions.create = AsyncMock(side_effect=[tool_response, text_response])
        result = await engine.followup('分别计算 1+2 和 3+4')
        assert result.reply == '结果分别是 3 和 7'

    @pytest.mark.asyncio
    async def test_tool_result_fed_back_to_memory(self) -> None:
        """工具执行结果被正确回填到对话记忆。"""
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        _activate_test_tools(engine)
        tool_response = _make_tool_call_response([('call_1', 'add_numbers', json.dumps({'a': 10, 'b': 20}))])
        text_response = _make_text_response('结果是 30')
        engine._client.chat.completions.create = AsyncMock(side_effect=[tool_response, text_response])
        await engine.followup('计算 10 + 20')
        messages = engine.memory.get_messages()
        tool_msgs = [m for m in messages if m.get('role') == 'tool']
        assert len(tool_msgs) == 1
        assert '30' in tool_msgs[0]['content']

    @pytest.mark.asyncio
    async def test_preserves_assistant_extra_fields_for_tool_message(self) -> None:
        """assistant tool 消息应保留扩展字段（供应商兼容）。"""
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        _activate_test_tools(engine)
        message = SimpleNamespace(content=None, reasoning_content='internal-thought', tool_calls=[SimpleNamespace(id='call_1', function=SimpleNamespace(name='add_numbers', arguments=json.dumps({'a': 1, 'b': 2})))])
        tool_response = SimpleNamespace(choices=[SimpleNamespace(message=message)])
        text_response = _make_text_response('done')
        engine._client.chat.completions.create = AsyncMock(side_effect=[tool_response, text_response])
        result = await engine.followup('计算')
        assert result.reply == 'done'
        msgs = engine.memory.get_messages()
        assistant_with_tool = [m for m in msgs if m.get('tool_calls')]
        assert len(assistant_with_tool) == 1
        assert assistant_with_tool[0].get('reasoning_content') == 'internal-thought'

class TestChatToolError:
    """工具异常处理场景（Requirement 1.5）。"""

    @pytest.mark.asyncio
    async def test_tool_error_fed_back_as_tool_message(self) -> None:
        """工具执行异常被捕获并作为 tool message 反馈给 LLM。"""
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        _activate_test_tools(engine)
        tool_response = _make_tool_call_response([('call_1', 'fail_tool', '{}')])
        text_response = _make_text_response('工具执行出错了，请检查。')
        engine._client.chat.completions.create = AsyncMock(side_effect=[tool_response, text_response])
        result = await engine.followup('执行失败工具')
        assert '工具执行出错' in result.reply or '检查' in result.reply
        messages = engine.memory.get_messages()
        tool_msgs = [m for m in messages if m.get('role') == 'tool']
        assert len(tool_msgs) == 1
        tool_content = tool_msgs[0]['content']
        assert (
            'TOOL_EXECUTION_ERROR' in tool_content
            or '工具执行失败' in tool_content
            or 'error_kind' in tool_content
        )

    @pytest.mark.asyncio
    async def test_malformed_arguments_should_not_execute_tool(self) -> None:
        """参数 JSON 非法时不应执行工具函数。"""
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        bad_args_response = _make_tool_call_response([('call_1', 'add_numbers', '{"a": 1')])
        text_response = _make_text_response('已处理')
        engine._client.chat.completions.create = AsyncMock(side_effect=[bad_args_response, text_response])
        with patch('excelmanus.engine_core.tool_dispatcher.asyncio.to_thread', new_callable=AsyncMock) as mock_to_thread:
            result = await engine.followup('坏参数测试')
            assert result.reply == '已处理'
            mock_to_thread.assert_not_called()
        msgs = engine.memory.get_messages()
        tool_msgs = [m for m in msgs if m.get('role') == 'tool']
        assert len(tool_msgs) == 1
        assert '参数解析错误' in tool_msgs[0]['content']

class TestConsecutiveFailureCircuitBreaker:
    """连续失败熔断场景（Requirement 1.6）。"""

    @pytest.mark.asyncio
    async def test_circuit_breaker_after_consecutive_failures(self) -> None:
        """连续 3 次工具失败后，熔断终止并返回错误摘要。"""
        config = _make_config(max_consecutive_failures=3)
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        fail_responses = [_make_tool_call_response([(f'call_{i}', 'fail_tool', '{}')]) for i in range(1, 4)]
        engine._client.chat.completions.create = AsyncMock(side_effect=fail_responses)
        result = await engine.followup('连续失败测试')
        assert '连续' in result.reply
        assert '失败' in result.reply

    @pytest.mark.asyncio
    async def test_success_resets_failure_counter(self) -> None:
        """成功的工具调用重置连续失败计数。"""
        config = _make_config(max_consecutive_failures=3)
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        _activate_test_tools(engine)
        fail_resp_1 = _make_tool_call_response([('c1', 'fail_tool', '{}')])
        success_resp = _make_tool_call_response([('c2', 'add_numbers', json.dumps({'a': 1, 'b': 1}))])
        fail_resp_2 = _make_tool_call_response([('c3', 'fail_tool', '{}')])
        text_resp = _make_text_response('完成')
        engine._client.chat.completions.create = AsyncMock(side_effect=[fail_resp_1, success_resp, fail_resp_2, text_resp])
        result = await engine.followup('混合成功失败')
        assert result.reply == '完成'

    @pytest.mark.asyncio
    async def test_circuit_breaker_keeps_tool_call_result_pairs(self) -> None:
        """单轮多 tool_calls 熔断后，也应为每个 tool_call 回填结果。"""
        config = _make_config(max_consecutive_failures=1)
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        tool_response = _make_tool_call_response([('call_1', 'fail_tool', '{}'), ('call_2', 'fail_tool', '{}')])
        engine._client.chat.completions.create = AsyncMock(side_effect=[tool_response])
        result = await engine.followup('触发熔断')
        assert '终止执行' in result.reply
        msgs = engine.memory.get_messages()
        tool_results = [m for m in msgs if m.get('role') == 'tool']
        assert {m['tool_call_id'] for m in tool_results} == {'call_1', 'call_2'}

class TestIterationLimit:
    """config.max_iterations 截断主循环（LLM 回合与工具调用共用）。"""

    @pytest.mark.asyncio
    async def test_truncates_at_max_iterations(self) -> None:
        """工具步超过配置值时停止，不再继续要模型收束。"""
        config = _make_config(max_iterations=3)
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        _activate_test_tools(engine)
        tool_responses = [
            _make_tool_call_response([(f'call_{i}', 'add_numbers', json.dumps({'a': i, 'b': i}))])
            for i in range(1, 6)
        ]
        engine._client.chat.completions.create = AsyncMock(
            side_effect=[*tool_responses, _make_text_response('完成')],
        )
        result = await engine.followup('继续做完')
        assert result.truncated is True
        assert '最大迭代次数' in result.reply or '调用上限' in result.reply
        assert result.iterations <= 3
        assert result.reply != '完成'

class TestAsyncToolExecution:
    """异步工具执行场景（Requirement 1.10）。"""

    @pytest.mark.asyncio
    async def test_blocking_tool_runs_in_thread(self) -> None:
        """阻塞型工具通过 asyncio.to_thread 隔离执行。"""
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        _activate_test_tools(engine)
        tool_response = _make_tool_call_response([('call_1', 'add_numbers', json.dumps({'a': 5, 'b': 10}))])
        text_response = _make_text_response('结果是 15')
        engine._client.chat.completions.create = AsyncMock(side_effect=[tool_response, text_response])
        with patch('excelmanus.engine_core.tool_dispatcher.asyncio.to_thread', new_callable=AsyncMock) as mock_to_thread:
            mock_to_thread.return_value = 15
            result = await engine.followup('计算 5 + 10')
            mock_to_thread.assert_called_once()
            call_args = mock_to_thread.call_args
            assert len(call_args.args) == 1
            assert callable(call_args.args[0])
            assert result.reply == '结果是 15'

class TestClearMemory:
    """清除记忆测试。"""

    def test_clear_memory(self) -> None:
        """clear_memory 清除对话历史。"""
        config = _make_config()
        registry = ToolRegistry()
        engine = AgentEngine(config, registry)
        engine.memory.add_user_message('测试消息')
        assert len(engine.memory.get_messages()) > 1
        engine.clear_memory()
        assert len(engine.memory.get_messages()) == 1
        assert engine.memory.get_messages()[0]['role'] == 'system'

class TestDataModels:
    """数据模型测试。"""

    def test_tool_call_result_defaults(self) -> None:
        """ToolCallResult 默认值正确。"""
        r = ToolCallResult(tool_name='test', arguments={}, result='ok', success=True)
        assert r.error is None
        assert r.success is True
        assert r.finish_accepted is False

    def test_chat_result_defaults(self) -> None:
        """ChatResult 默认值正确。"""
        r = ChatResult(reply='hello')
        assert r.tool_calls == []
        assert r.iterations == 0
        assert r.truncated is False
import string
from hypothesis import given, assume
from hypothesis import strategies as st
tool_name_st = st.from_regex('[a-z][a-z0-9_]{2,20}', fullmatch=True)
nonempty_text_st = st.text(alphabet=st.characters(whitelist_categories=('L', 'N', 'P', 'Z')), min_size=1, max_size=200)
tool_call_id_st = st.from_regex('call_[a-z0-9]{4,10}', fullmatch=True)

@given(history=st.lists(st.tuples(st.sampled_from(['user', 'assistant']), nonempty_text_st), min_size=0, max_size=10), new_input=nonempty_text_st)
def test_property_1_message_construction_completeness(history: list[tuple[str, str]], new_input: str) -> None:
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
    for role, content in history:
        if role == 'user':
            mem.add_user_message(content)
        else:
            mem.add_assistant_message(content)
    mem.add_user_message(new_input)
    messages = mem.get_messages()
    assert messages[0]['role'] == 'system'
    assert messages[0]['content'] == _DEFAULT_SYSTEM_PROMPT
    assert messages[-1]['role'] == 'user'
    assert messages[-1]['content'] == new_input
    assert len(messages) >= 2
    valid_roles = {'system', 'user', 'assistant', 'tool'}
    for m in messages:
        assert m['role'] in valid_roles

@given(n_tools=st.integers(min_value=1, max_value=5))
def test_property_1_tools_schema_attached(n_tools: int) -> None:
    """Property 1 补充：Engine 构建请求时附全量 tools schema。

    **验证：需求 1.1, 1.7**
    """
    registry = ToolRegistry()
    tools = []
    for i in range(n_tools):
        tools.append(ToolDef(name=f'tool_{i}', description=f'测试工具 {i}', input_schema={'type': 'object', 'properties': {}}, func=lambda: 'ok'))
    registry.register_tools(tools)
    schemas = registry.get_openai_schemas()
    assert len(schemas) == n_tools
    for s in schemas:
        assert s['type'] == 'function'
        if 'function' in s:
            assert 'name' in s['function']
            assert 'description' in s['function']
            assert 'parameters' in s['function']
        else:
            assert 'name' in s
            assert 'description' in s
            assert 'parameters' in s

@given(n_calls=st.integers(min_value=1, max_value=4), a_values=st.lists(st.integers(min_value=0, max_value=100), min_size=4, max_size=4), b_values=st.lists(st.integers(min_value=0, max_value=100), min_size=4, max_size=4))
@pytest.mark.asyncio
async def test_property_2_tool_call_parsing_and_invocation(n_calls: int, a_values: list[int], b_values: list[int]) -> None:
    """Property 2：Tool Call 解析与调用。

    对于任意包含 tool_calls 的响应，Engine 必须正确解析并逐个调用工具，
    且 tool_call_id 对应一致。

    **验证：需求 1.2**
    """
    config = _make_config()
    registry = _make_registry_with_tools()
    engine = AgentEngine(config, registry)
    _activate_test_tools(engine)
    tc_list = []
    expected_results = []
    for i in range(n_calls):
        call_id = f'call_{i}'
        a, b = (a_values[i], b_values[i])
        tc_list.append((call_id, 'add_numbers', json.dumps({'a': a, 'b': b})))
        expected_results.append((call_id, str(a + b)))
    tool_response = _make_tool_call_response(tc_list)
    text_response = _make_text_response('完成')
    engine._client.chat.completions.create = AsyncMock(side_effect=[tool_response, text_response])
    result = await engine.followup('测试多工具调用')
    assert result.reply == '完成'
    messages = engine.memory.get_messages()
    tool_msgs = [m for m in messages if m.get('role') == 'tool']
    assert len(tool_msgs) == n_calls
    for call_id, expected_val in expected_results:
        matching = [m for m in tool_msgs if m.get('tool_call_id') == call_id]
        assert len(matching) == 1, f'tool_call_id {call_id} 应有且仅有一个对应结果'
        assert expected_val in matching[0]['content']

@given(reply_text=nonempty_text_st)
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
    result = await engine.followup('任意输入')
    assert result.reply == reply_text
    assert engine._client.chat.completions.create.call_count == 1
    messages = engine.memory.get_messages()
    roles = [m['role'] for m in messages]
    assert roles[0] == 'system'
    assert 'user' in roles
    assert 'assistant' in roles
    assert 'tool' not in roles

@given(max_iter=st.integers(min_value=1, max_value=4))
@pytest.mark.asyncio
async def test_property_4_iteration_budget(max_iter: int) -> None:
    """Property 4：config.max_iterations 截断循环。

    工具步超过配置值时停止，不再等到纯文本收束。
    """
    config = _make_config(max_iterations=max_iter)
    registry = _make_registry_with_tools()
    engine = AgentEngine(config, registry)
    _activate_test_tools(engine)
    n_tools = max_iter + 2
    tool_responses = [
        _make_tool_call_response([(f'call_{i}', 'add_numbers', json.dumps({'a': i, 'b': i}))])
        for i in range(n_tools)
    ]
    engine._client.chat.completions.create = AsyncMock(
        side_effect=[*tool_responses, _make_text_response('完成')],
    )
    result = await engine.followup('继续做完')
    assert result.truncated is True
    assert '最大迭代次数' in result.reply or '调用上限' in result.reply
    assert result.iterations <= max_iter
    assert result.reply != '完成'

@given(error_msg=st.text(alphabet=st.characters(whitelist_categories=('L', 'N')), min_size=1, max_size=100))
@pytest.mark.asyncio
async def test_property_5_tool_exception_feedback(error_msg: str) -> None:
    """Property 5：工具异常反馈。

    任意工具异常必须被捕获并作为 tool message 反馈给 LLM，不直接向调用方抛出。

    **验证：需求 1.5**
    """

    def failing_tool() -> str:
        raise RuntimeError(error_msg)
    registry = ToolRegistry()
    registry.register_tools([ToolDef(name='custom_fail', description='自定义失败工具', input_schema={'type': 'object', 'properties': {}}, func=failing_tool)])
    config = _make_config(max_consecutive_failures=10)
    engine = AgentEngine(config, registry)
    engine._full_access_enabled = True
    tool_response = _make_tool_call_response([('call_err', 'custom_fail', '{}')])
    text_response = _make_text_response('已处理错误')
    engine._client.chat.completions.create = AsyncMock(side_effect=[tool_response, text_response])
    result = await engine.followup('测试异常反馈')
    assert result.reply == '已处理错误'
    messages = engine.memory.get_messages()
    tool_msgs = [m for m in messages if m.get('role') == 'tool']
    assert len(tool_msgs) >= 1
    assert any(('错误' in m['content'] or 'error' in m['content'].lower() for m in tool_msgs))

@given(max_failures=st.integers(min_value=1, max_value=5))
@pytest.mark.asyncio
async def test_property_6_consecutive_failure_circuit_breaker(max_failures: int) -> None:
    """Property 6：连续失败熔断。

    连续 M 次工具失败后，Engine 必须终止并返回错误摘要。

    **验证：需求 1.6**
    """
    registry = ToolRegistry()
    registry.register_tools([ToolDef(name='always_fail', description='总是失败', input_schema={'type': 'object', 'properties': {}}, func=lambda: (_ for _ in ()).throw(RuntimeError('boom')))])
    config = _make_config(max_consecutive_failures=max_failures, max_iterations=max_failures + 5)
    engine = AgentEngine(config, registry)
    engine._full_access_enabled = True
    fail_responses = [_make_tool_call_response([(f'call_{i}', 'always_fail', '{}')]) for i in range(max_failures + 3)]
    engine._client.chat.completions.create = AsyncMock(side_effect=fail_responses)
    result = await engine.followup('熔断测试')
    assert '失败' in result.reply or '终止' in result.reply or '错误' in result.reply
    assert engine._client.chat.completions.create.call_count <= max_failures

@given(n_calls=st.integers(min_value=1, max_value=3))
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
    tc_list = [(f'call_{i}', 'add_numbers', json.dumps({'a': i, 'b': i})) for i in range(n_calls)]
    tool_response = _make_tool_call_response(tc_list)
    text_response = _make_text_response('完成')
    engine._client.chat.completions.create = AsyncMock(side_effect=[tool_response, text_response])
    with patch('excelmanus.engine_core.tool_dispatcher.asyncio.to_thread', new_callable=AsyncMock) as mock_to_thread:
        mock_to_thread.side_effect = [i + i for i in range(n_calls)]
        result = await engine.followup('异步测试')
        assert mock_to_thread.call_count == n_calls
        for call in mock_to_thread.call_args_list:
            assert len(call.args) == 1
            assert callable(call.args[0])
        assert result.reply == '完成'

class TestApprovalFlow:
    """Accept 门禁主流程测试。"""

    def _make_registry_with_write_tool(self, workspace: Path) -> ToolRegistry:
        registry = ToolRegistry()

        def write_text_file(file_path: str, content: str, overwrite: bool=True, encoding: str='utf-8') -> str:
            target = workspace / file_path
            if target.exists() and (not overwrite):
                return json.dumps({'status': 'error', 'error': 'exists'}, ensure_ascii=False)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding=encoding)
            return json.dumps({'status': 'success', 'file': file_path}, ensure_ascii=False)
        registry.register_tools([ToolDef(name='write_text_file', description='写文件', input_schema={'type': 'object', 'properties': {'file_path': {'type': 'string'}, 'content': {'type': 'string'}, 'overwrite': {'type': 'boolean'}, 'encoding': {'type': 'string'}}, 'required': ['file_path', 'content']}, func=write_text_file)])
        return registry

    def _make_registry_with_audit_tool(self, workspace: Path) -> ToolRegistry:
        registry = ToolRegistry()

        def copy_file(source: str, destination: str) -> str:
            target = workspace / destination
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('copied', encoding='utf-8')
            return json.dumps({'status': 'success', 'destination': destination}, ensure_ascii=False)
        registry.register_tools([ToolDef(name='copy_file', description='复制文件', input_schema={'type': 'object', 'properties': {'source': {'type': 'string'}, 'destination': {'type': 'string'}}, 'required': ['source', 'destination']}, func=copy_file)])
        return registry

    def _make_registry_with_failing_write_tool(self, workspace: Path) -> ToolRegistry:
        registry = ToolRegistry()

        def write_text_file(file_path: str, content: str) -> str:
            target = workspace / file_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding='utf-8')
            raise RuntimeError('intentional_write_failure')
        registry.register_tools([ToolDef(name='write_text_file', description='写文件后抛错', input_schema={'type': 'object', 'properties': {'file_path': {'type': 'string'}, 'content': {'type': 'string'}}, 'required': ['file_path', 'content']}, func=write_text_file)])
        return registry

    def _make_registry_with_failing_audit_tool(self, workspace: Path) -> ToolRegistry:
        registry = ToolRegistry()

        def copy_file(source: str, destination: str) -> str:
            target = workspace / destination
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('copied', encoding='utf-8')
            raise RuntimeError('intentional_audit_failure')
        registry.register_tools([ToolDef(name='copy_file', description='复制文件后抛错', input_schema={'type': 'object', 'properties': {'source': {'type': 'string'}, 'destination': {'type': 'string'}}, 'required': ['source', 'destination']}, func=copy_file)])
        return registry

    def _make_registry_with_custom_tool(self) -> ToolRegistry:
        registry = ToolRegistry()

        def custom_tool() -> str:
            return 'custom-ok'
        registry.register_tools([ToolDef(name='custom_tool', description='自定义工具', input_schema={'type': 'object', 'properties': {}}, func=custom_tool)])
        return registry

    @staticmethod
    def _promote_write_text_file_to_confirm(engine: AgentEngine) -> None:
        """将 write_text_file 从 Tier B (audit-only) 提升为 Tier A (confirm-required)，以测试审批流程。"""
        engine._approval._confirm_tools.add('write_text_file')
        engine._approval._audit_only_tools.discard('write_text_file')

    @pytest.mark.asyncio
    async def test_high_risk_tool_requires_accept(self, tmp_path: Path) -> None:
        """阻塞式审批：approval_resolver 返回 accept 后内联执行高风险工具。"""
        config = _make_config(workspace_root=str(tmp_path))
        registry = self._make_registry_with_write_tool(tmp_path)
        engine = AgentEngine(config, registry)
        self._promote_write_text_file_to_confirm(engine)
        captured_id = None

        async def _accept(p):
            nonlocal captured_id
            captured_id = p.approval_id
            return 'accept'
        tool_response = _make_tool_call_response([('call_1', 'write_text_file', json.dumps({'file_path': 'a.txt', 'content': 'hello'}))])
        text_response = _make_text_response('文件已写入完成。')
        engine._client.chat.completions.create = AsyncMock(side_effect=[tool_response, text_response])
        reply = await engine.followup('写入文件', approval_resolver=_accept)
        assert (tmp_path / 'a.txt').read_text(encoding='utf-8') == 'hello'
        assert captured_id is not None
        assert (tmp_path / 'outputs' / 'approvals' / captured_id / 'manifest.json').exists()

    @pytest.mark.asyncio
    async def test_accept_resumes_task_list_execution_after_high_risk_gate(self, tmp_path: Path) -> None:
        """阻塞式审批：accept 后继续执行后续工具调用。"""
        config = _make_config(workspace_root=str(tmp_path))
        registry = self._make_registry_with_write_tool(tmp_path)

        def add_numbers(a: int, b: int) -> int:
            return a + b
        registry.register_tools([ToolDef(name='add_numbers', description='两数相加', input_schema={'type': 'object', 'properties': {'a': {'type': 'integer'}, 'b': {'type': 'integer'}}, 'required': ['a', 'b']}, func=add_numbers)])
        engine = AgentEngine(config, registry)
        engine._task_store.create('测试任务', ['写入文件', '继续计算'])
        route_result = SkillMatchResult(skills_used=[], tool_scope=['write_text_file', 'add_numbers', 'task_update'], route_mode='fallback', system_contexts=[])
        engine._route_skills = AsyncMock(return_value=route_result)

        async def _accept(p):
            return 'accept'
        first_round = _make_tool_call_response([('call_write', 'write_text_file', json.dumps({'file_path': 'resume.txt', 'content': 'ok'}, ensure_ascii=False))])
        resume_round = _make_tool_call_response([('call_add', 'add_numbers', json.dumps({'a': 1, 'b': 2}, ensure_ascii=False))])
        done_round = _make_text_response('后续子任务已完成')
        engine._client.chat.completions.create = AsyncMock(side_effect=[first_round, resume_round, done_round])
        reply = await engine.followup('开始执行', approval_resolver=_accept)
        assert '后续子任务已完成' in reply.reply
        assert (tmp_path / 'resume.txt').read_text(encoding='utf-8') == 'ok'

    @pytest.mark.asyncio
    async def test_reject_pending(self, tmp_path: Path) -> None:
        """阻塞式审批：approval_resolver 返回 reject 后文件不写入。"""
        config = _make_config(workspace_root=str(tmp_path))
        registry = self._make_registry_with_write_tool(tmp_path)
        engine = AgentEngine(config, registry)
        self._promote_write_text_file_to_confirm(engine)

        async def _reject(p):
            return 'reject'
        tool_response = _make_tool_call_response([('call_1', 'write_text_file', json.dumps({'file_path': 'b.txt', 'content': 'world'}))])
        text_response = _make_text_response('已拒绝操作。')
        engine._client.chat.completions.create = AsyncMock(side_effect=[tool_response, text_response])
        reply = await engine.followup('写文件', approval_resolver=_reject)
        assert engine._approval.pending is None
        assert not (tmp_path / 'b.txt').exists()

    @pytest.mark.asyncio
    async def test_undo_after_accept(self, tmp_path: Path) -> None:
        """阻塞式审批：accept 后 /undo 回滚文件。"""
        config = _make_config(workspace_root=str(tmp_path))
        registry = self._make_registry_with_write_tool(tmp_path)
        engine = AgentEngine(config, registry)
        self._promote_write_text_file_to_confirm(engine)
        captured_id = None

        async def _accept(p):
            nonlocal captured_id
            captured_id = p.approval_id
            return 'accept'
        tool_response = _make_tool_call_response([('call_1', 'write_text_file', json.dumps({'file_path': 'c.txt', 'content': 'undo'}))])
        text_response = _make_text_response('文件已写入。')
        engine._client.chat.completions.create = AsyncMock(side_effect=[tool_response, text_response])
        await engine.followup('写文件', approval_resolver=_accept)
        assert (tmp_path / 'c.txt').exists()
        assert captured_id is not None
        undo_reply = await engine.followup(f'/undo {captured_id}')
        assert '已回滚' in undo_reply.reply
        assert (tmp_path / 'c.txt').exists()

    @pytest.mark.asyncio
    async def test_failed_accept_still_writes_failed_manifest(self, tmp_path: Path) -> None:
        """阻塞式审批：accept 后工具执行失败，manifest 记录失败状态。"""
        config = _make_config(workspace_root=str(tmp_path))
        registry = self._make_registry_with_failing_write_tool(tmp_path)
        engine = AgentEngine(config, registry)
        self._promote_write_text_file_to_confirm(engine)
        captured_id = None

        async def _accept(p):
            nonlocal captured_id
            captured_id = p.approval_id
            return 'accept'
        tool_response = _make_tool_call_response([('call_1', 'write_text_file', json.dumps({'file_path': 'err.txt', 'content': 'x'}))])
        text_response = _make_text_response('执行出错。')
        engine._client.chat.completions.create = AsyncMock(side_effect=[tool_response, text_response])
        reply = await engine.followup('写文件', approval_resolver=_accept)
        assert captured_id is not None
        manifest_path = tmp_path / 'outputs' / 'approvals' / captured_id / 'manifest.json'
        assert manifest_path.exists()
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        assert manifest['execution']['status'] == 'failed'
        assert manifest['execution']['error_type'] == 'ToolExecutionError'

    @pytest.mark.asyncio
    async def test_audit_tool_failure_still_returns_failed_audit_record(self, tmp_path: Path) -> None:
        config = _make_config(workspace_root=str(tmp_path))
        registry = self._make_registry_with_failing_audit_tool(tmp_path)
        engine = AgentEngine(config, registry)
        tc = SimpleNamespace(id='call_audit_fail', function=SimpleNamespace(name='copy_file', arguments=json.dumps({'source': 'a.txt', 'destination': 'failed_copy.txt'}, ensure_ascii=False)))
        result = await engine._execute_tool_call(tc=tc, tool_scope=['copy_file'], on_event=None, iteration=1)
        assert result.success is False
        assert 'intentional_audit_failure' in result.result
        assert result.audit_record is not None
        assert result.audit_record.execution_status == 'failed'
        assert result.audit_record.error_type == 'ToolExecutionError'

    @pytest.mark.asyncio
    async def test_undo_after_restart_loads_manifest(self, tmp_path: Path) -> None:
        """阻塞式审批：跨 engine 实例 /undo 回滚。"""
        config = _make_config(workspace_root=str(tmp_path))
        registry = self._make_registry_with_write_tool(tmp_path)
        engine1 = AgentEngine(config, registry)
        _activate_test_tools(engine1, ['write_text_file'])
        self._promote_write_text_file_to_confirm(engine1)
        captured_id = None

        async def _accept(p):
            nonlocal captured_id
            captured_id = p.approval_id
            return 'accept'
        tool_response = _make_tool_call_response([('call_1', 'write_text_file', json.dumps({'file_path': 'restart.txt', 'content': 'v'}))])
        text_response = _make_text_response('写入完成。')
        engine1._client.chat.completions.create = AsyncMock(side_effect=[tool_response, text_response])
        await engine1.followup('写文件', approval_resolver=_accept)
        assert (tmp_path / 'restart.txt').exists()
        assert captured_id is not None
        engine2 = AgentEngine(config, registry)
        undo_reply = await engine2.followup(f'/undo {captured_id}')
        assert '已回滚' in undo_reply.reply
        assert (tmp_path / 'restart.txt').exists()

    @pytest.mark.asyncio
    async def test_fullaccess_bypass_accept(self, tmp_path: Path) -> None:
        config = _make_config(workspace_root=str(tmp_path))
        registry = self._make_registry_with_write_tool(tmp_path)
        engine = AgentEngine(config, registry)
        on_reply = await engine.followup('/fullaccess on')
        assert '已开启' in on_reply.reply
        tool_response = _make_tool_call_response([('call_1', 'write_text_file', json.dumps({'file_path': 'd.txt', 'content': 'full'}))])
        text_response = _make_text_response('完成')
        engine._client.chat.completions.create = AsyncMock(side_effect=[tool_response, text_response])
        reply = await engine.followup('直接写')
        assert reply.reply == '完成'
        assert engine._approval.pending is None
        assert (tmp_path / 'd.txt').read_text(encoding='utf-8') == 'full'

    @pytest.mark.asyncio
    async def test_fullaccess_resolver_accepts_and_enables_fullaccess(self, tmp_path: Path) -> None:
        """阻塞式审批：approval_resolver 返回 fullaccess 后自动开启并执行。"""
        config = _make_config(workspace_root=str(tmp_path))
        registry = self._make_registry_with_write_tool(tmp_path)
        engine = AgentEngine(config, registry)
        self._promote_write_text_file_to_confirm(engine)

        async def _fullaccess(p):
            return 'fullaccess'
        tool_response = _make_tool_call_response([('call_1', 'write_text_file', json.dumps({'file_path': 'auto.txt', 'content': 'hello'}))])
        text_response = _make_text_response('已完成。')
        engine._client.chat.completions.create = AsyncMock(side_effect=[tool_response, text_response])
        reply = await engine.followup('写文件', approval_resolver=_fullaccess)
        assert engine.full_access_enabled is True
        assert engine._approval.pending is None
        assert (tmp_path / 'auto.txt').read_text(encoding='utf-8') == 'hello'

    @pytest.mark.asyncio
    async def test_default_mode_non_whitelist_tool_executes_directly(self, tmp_path: Path) -> None:
        config = _make_config(workspace_root=str(tmp_path))
        registry = self._make_registry_with_custom_tool()
        engine = AgentEngine(config, registry)
        tool_response = _make_tool_call_response([('call_1', 'custom_tool', '{}')])
        text_response = _make_text_response('完成')
        engine._client.chat.completions.create = AsyncMock(side_effect=[tool_response, text_response])
        reply = await engine.followup('执行自定义工具')
        assert reply.reply == '完成'
        assert engine._approval.pending is None

    @pytest.mark.asyncio
    async def test_fullaccess_executes_non_whitelist_tool(self, tmp_path: Path) -> None:
        config = _make_config(workspace_root=str(tmp_path))
        registry = self._make_registry_with_custom_tool()
        engine = AgentEngine(config, registry)
        engine._active_skills = [Skillpack(name='test/custom', description='test', instructions='', source='project', root_dir=str(tmp_path))]
        on_reply = await engine.followup('/fullaccess on')
        assert '已开启' in on_reply.reply
        tool_response = _make_tool_call_response([('call_1', 'custom_tool', '{}')])
        text_response = _make_text_response('完成')
        engine._client.chat.completions.create = AsyncMock(side_effect=[tool_response, text_response])
        reply = await engine.followup('执行自定义工具')
        assert reply.reply == '完成'
        assert engine._approval.pending is None

    @pytest.mark.asyncio
    async def test_default_mode_audit_only_tool_executes_without_accept(self, tmp_path: Path) -> None:
        config = _make_config(workspace_root=str(tmp_path))
        registry = self._make_registry_with_audit_tool(tmp_path)
        engine = AgentEngine(config, registry)
        engine._execute_tool_with_audit = AsyncMock(return_value=('{"status":"success"}', None))
        tool_response = _make_tool_call_response([('call_1', 'copy_file', json.dumps({'source': 'a.xlsx', 'destination': 'b.xlsx'}))])
        text_response = _make_text_response('完成')
        engine._client.chat.completions.create = AsyncMock(side_effect=[tool_response, text_response])
        reply = await engine.followup('复制文件')
        assert reply.reply == '完成'
        assert engine._approval.pending is None
        engine._execute_tool_with_audit.assert_awaited_once()

class TestToolIndexNotice:
    """工具分组索引不再注入 system。"""

    def test_tool_index_not_injected(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        prompts, error = engine._prepare_system_prompts_for_request(skill_contexts=[])
        assert error is None
        blob = "\n".join(prompts)
        assert "工具分类" not in blob
        assert "工具索引" not in blob

class TestToolInjectionOptimizationE2E:
    """Task 7: 工具注入优化端到端集成测试。"""

    @staticmethod
    def _make_registry_with_categorized_tools() -> ToolRegistry:
        registry = ToolRegistry()

        def _noop(**_: object) -> str:
            return 'ok'
        for tool_name in ('read_excel', 'create_chart', 'format_cells', 'write_excel', 'list_sheets'):
            registry.register_tool(ToolDef(name=tool_name, description=f'{tool_name} tool', input_schema={'type': 'object', 'properties': {}}, func=_noop))
        return registry

    def test_tool_index_not_in_system_prompt_when_no_skill(self) -> None:
        """无 skill 激活时 system prompt 也不再注入工具索引。"""
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        assert not engine._active_skills
        prompts, error = engine._prepare_system_prompts_for_request(skill_contexts=[])
        assert error is None
        full_prompt = '\n'.join(prompts)
        assert '工具分类' not in full_prompt
        assert '工具索引' not in full_prompt

def _make_skill_router(config: ExcelManusConfig | None=None) -> 'SkillRouter':
    """创建包含模拟 skillpacks 的 SkillRouter，用于自动补充测试。"""
    from excelmanus.skillpacks.loader import SkillpackLoader
    from excelmanus.skillpacks.router import SkillRouter
    cfg = config or _make_config()
    registry = _make_registry_with_tools()
    loader = SkillpackLoader(cfg, registry)
    loader._skillpacks = {'format_basic': Skillpack(name='format_basic', description='基础格式化', instructions='格式化操作指引', source='system', root_dir='/tmp/format_basic'), 'data_basic': Skillpack(name='data_basic', description='数据分析', instructions='数据操作指引', source='system', root_dir='/tmp/data_basic'), 'chart_basic': Skillpack(name='chart_basic', description='图表', instructions='图表操作指引', source='system', root_dir='/tmp/chart_basic'), 'data_basic': Skillpack(name='data_basic', description='通用 Excel', instructions='通用 Excel 操作', source='system', root_dir='/tmp/data_basic'), 'excel_code_runner': Skillpack(name='excel_code_runner', description='代码执行', instructions='代码执行指引', source='system', root_dir='/tmp/excel_code_runner'), 'sheet_ops': Skillpack(name='sheet_ops', description='工作表操作', instructions='工作表操作指引', source='system', root_dir='/tmp/sheet_ops'), 'file_ops': Skillpack(name='file_ops', description='文件操作', instructions='文件操作指引', source='system', root_dir='/tmp/file_ops')}
    return SkillRouter(cfg, loader)

@pytest.mark.asyncio
async def test_refresh_credential_updates_protocol_even_if_token_unchanged() -> None:
    """OAuth token 不变时，也应同步 protocol/base_url 更新。"""
    cfg = _make_config(api_key='oauth-token', base_url='https://chatgpt.com/backend-api/codex', model='gpt-5.3-codex', protocol='openai')
    engine = AgentEngine(cfg, _make_registry_with_tools())
    resolver = AsyncMock()
    resolver.resolve.return_value = ResolvedCredential(api_key='oauth-token', base_url='https://chatgpt.com/backend-api/codex', source='oauth', provider='openai-codex', protocol='openai_responses')
    engine._credential_resolver = resolver
    await engine._refresh_credential_if_needed()
    assert engine._active_protocol == 'openai_responses'
