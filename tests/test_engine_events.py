"""AgentEngine 事件发出属性测试：验证工具调用循环中事件回调的正确性。

使用 hypothesis 框架对 Property 2/3/4 进行属性测试，
确保 AgentEngine 在 Tool Calling 循环中发出的事件数据与实际操作一致。
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st

from excelmanus.config import ExcelManusConfig
from excelmanus.engine import AgentEngine
from excelmanus.events import EventType, ToolCallEvent
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
        "max_consecutive_failures": 10,
        "workspace_root": ".",
    }
    defaults.update(overrides)
    return ExcelManusConfig(**defaults)


def _make_registry_with_echo_tool() -> ToolRegistry:
    """创建包含 echo 工具的 ToolRegistry，工具返回参数的 JSON 字符串。"""
    registry = ToolRegistry()

    def echo_tool(**kwargs) -> str:
        return json.dumps(kwargs, ensure_ascii=False)

    tools = [
        ToolDef(
            name="echo_tool",
            description="回显参数",
            input_schema={
                "type": "object",
                "properties": {
                    "message": {"type": "string"},
                },
            },
            func=echo_tool,
        ),
    ]
    registry.register_tools(tools)
    return registry


def _make_registry_with_named_tool(tool_name: str, func) -> ToolRegistry:
    """创建包含指定名称工具的 ToolRegistry。"""
    registry = ToolRegistry()
    tools = [
        ToolDef(
            name=tool_name,
            description=f"工具 {tool_name}",
            input_schema={"type": "object", "properties": {}},
            func=func,
        ),
    ]
    registry.register_tools(tools)
    return registry


def _make_text_response(content: str):
    """构造纯文本 LLM 响应（无 tool_calls）。"""
    message = SimpleNamespace(content=content, tool_calls=None)
    choice = SimpleNamespace(message=message)
    return SimpleNamespace(choices=[choice])


def _make_tool_call_response(tool_calls: list[tuple[str, str, str]], content=None):
    """构造包含 tool_calls 的 LLM 响应。

    Args:
        tool_calls: [(tool_call_id, tool_name, arguments_json), ...]
    """
    tc_objects = [
        SimpleNamespace(
            id=call_id,
            function=SimpleNamespace(name=name, arguments=args),
        )
        for call_id, name, args in tool_calls
    ]
    message = SimpleNamespace(content=content, tool_calls=tc_objects)
    choice = SimpleNamespace(message=message)
    return SimpleNamespace(choices=[choice])


# ── hypothesis 策略 ──────────────────────────────────────


# 合法工具名称：小写字母开头，包含字母数字下划线
tool_name_st = st.from_regex(r"[a-z][a-z0-9_]{2,15}", fullmatch=True)

# 简单参数值：字符串或整数
simple_value_st = st.one_of(
    st.text(
        alphabet=st.characters(whitelist_categories=("L", "N")),
        min_size=0,
        max_size=50,
    ),
    st.integers(min_value=-1000, max_value=1000),
)

# 参数字典：键为合法标识符，值为简单类型
arguments_st = st.dictionaries(
    keys=st.from_regex(r"[a-z][a-z0-9_]{0,10}", fullmatch=True),
    values=simple_value_st,
    min_size=0,
    max_size=5,
)


# ── 事件收集器 ────────────────────────────────────────────


class EventCollector:
    """收集 AgentEngine 发出的所有事件。"""

    def __init__(self) -> None:
        self.events: list[ToolCallEvent] = []

    def __call__(self, event: ToolCallEvent) -> None:
        self.events.append(event)

    def by_type(self, event_type: EventType) -> list[ToolCallEvent]:
        """按事件类型过滤。"""
        return [e for e in self.events if e.event_type == event_type]


# ══════════════════════════════════════════════════════════
# Property 2: 工具调用开始事件包含正确数据
# **Feature: cli-beautify, Property 2: 工具调用开始事件包含正确数据**
# **Validates: Requirements 1.1**
# ══════════════════════════════════════════════════════════


@given(
    arguments=arguments_st,
)
@settings(max_examples=100, deadline=None)
@pytest.mark.asyncio
async def test_property_2_tool_call_start_event_data(
    arguments: dict,
) -> None:
    """Property 2: 工具调用开始事件包含正确数据。

    对于任意工具名称和参数字典，当 AgentEngine 发出 tool_call_start 事件时，
    事件的 tool_name 应等于实际工具名称，arguments 应等于实际参数字典，
    event_type 应为 TOOL_CALL_START。

    **Feature: cli-beautify, Property 2: 工具调用开始事件包含正确数据**
    **Validates: Requirements 1.1**
    """
    # 使用固定工具名，因为 ToolRegistry 需要注册真实工具
    fixed_tool_name = "echo_tool"
    registry = _make_registry_with_echo_tool()
    config = _make_config()
    engine = AgentEngine(config, registry)

    collector = EventCollector()

    # 构造 LLM 响应：一轮 tool_call + 一轮纯文本
    args_json = json.dumps(arguments, ensure_ascii=False)
    tool_response = _make_tool_call_response(
        [("call_1", fixed_tool_name, args_json)]
    )
    text_response = _make_text_response("完成")

    engine._client.chat.completions.create = AsyncMock(
        side_effect=[tool_response, text_response]
    )

    await engine.chat("测试", on_event=collector)

    # 验证：至少有一个 TOOL_CALL_START 事件
    start_events = collector.by_type(EventType.TOOL_CALL_START)
    assert len(start_events) == 1

    event = start_events[0]

    # 不变量 1：event_type 为 TOOL_CALL_START
    assert event.event_type == EventType.TOOL_CALL_START

    # 不变量 2：tool_name 等于实际工具名称
    assert event.tool_name == fixed_tool_name

    # 不变量 3：arguments 等于实际参数字典
    assert event.arguments == arguments


# ══════════════════════════════════════════════════════════
# Property 3: 工具调用结束事件包含正确状态
# **Feature: cli-beautify, Property 3: 工具调用结束事件包含正确状态**
# **Validates: Requirements 1.2**
# ══════════════════════════════════════════════════════════


@given(
    should_succeed=st.booleans(),
    arguments=arguments_st,
)
@settings(max_examples=100, deadline=None)
@pytest.mark.asyncio
async def test_property_3_tool_call_end_event_status(
    should_succeed: bool,
    arguments: dict,
) -> None:
    """Property 3: 工具调用结束事件包含正确状态。

    对于任意工具调用结果（成功或失败），当 AgentEngine 发出 tool_call_end 事件时，
    事件的 success 字段应与实际执行结果一致，且失败时 error 字段非空。

    **Feature: cli-beautify, Property 3: 工具调用结束事件包含正确状态**
    **Validates: Requirements 1.2**
    """
    error_msg = "模拟工具执行失败"

    if should_succeed:
        def tool_func(**kwargs) -> str:
            return "成功结果"
        tool_name = "success_tool"
    else:
        def tool_func(**kwargs) -> str:
            raise RuntimeError(error_msg)
        tool_name = "fail_tool"

    registry = _make_registry_with_named_tool(tool_name, tool_func)
    config = _make_config(max_consecutive_failures=10)
    engine = AgentEngine(config, registry)

    collector = EventCollector()

    args_json = json.dumps(arguments, ensure_ascii=False)
    tool_response = _make_tool_call_response(
        [("call_1", tool_name, args_json)]
    )
    text_response = _make_text_response("完成")

    engine._client.chat.completions.create = AsyncMock(
        side_effect=[tool_response, text_response]
    )

    await engine.chat("测试", on_event=collector)

    # 验证：至少有一个 TOOL_CALL_END 事件
    end_events = collector.by_type(EventType.TOOL_CALL_END)
    assert len(end_events) == 1

    event = end_events[0]

    # 不变量 1：event_type 为 TOOL_CALL_END
    assert event.event_type == EventType.TOOL_CALL_END

    # 不变量 2：success 字段与实际执行结果一致
    assert event.success == should_succeed

    # 不变量 3：失败时 error 字段非空
    if not should_succeed:
        assert event.error is not None
        assert len(event.error) > 0

    # 不变量 4：成功时 result 非空
    if should_succeed:
        assert len(event.result) > 0

    # 不变量 5：tool_name 正确
    assert event.tool_name == tool_name


# ══════════════════════════════════════════════════════════
# Property 4: 迭代事件轮次编号递增
# **Feature: cli-beautify, Property 4: 迭代事件轮次编号递增**
# **Validates: Requirements 1.5**
# ══════════════════════════════════════════════════════════


@given(
    n_iterations=st.integers(min_value=1, max_value=8),
)
@settings(max_examples=100, deadline=None)
@pytest.mark.asyncio
async def test_property_4_iteration_numbers_strictly_increasing(
    n_iterations: int,
) -> None:
    """Property 4: 迭代事件轮次编号递增。

    对于任意多轮 Tool Calling 循环，AgentEngine 发出的 iteration_start 事件序列中，
    轮次编号应从 1 开始严格递增。

    **Feature: cli-beautify, Property 4: 迭代事件轮次编号递增**
    **Validates: Requirements 1.5**
    """
    registry = _make_registry_with_echo_tool()
    config = _make_config(max_iterations=n_iterations + 5)
    engine = AgentEngine(config, registry)

    collector = EventCollector()

    # 构造 n_iterations 轮 tool_call 响应，最后一轮返回纯文本
    responses = []
    for i in range(n_iterations):
        responses.append(
            _make_tool_call_response(
                [(f"call_{i}", "echo_tool", '{"message": "test"}')]
            )
        )
    responses.append(_make_text_response("完成"))

    engine._client.chat.completions.create = AsyncMock(side_effect=responses)

    await engine.chat("测试", on_event=collector)

    # 获取所有 ITERATION_START 事件
    iter_events = collector.by_type(EventType.ITERATION_START)

    # 不变量 1：迭代事件数量等于实际迭代轮数 + 1（最后纯文本轮也有 iteration_start）
    # 注意：engine 在每轮循环开始时都发出 iteration_start，包括最终返回纯文本的那轮
    assert len(iter_events) == n_iterations + 1

    # 不变量 2：轮次编号从 1 开始
    assert iter_events[0].iteration == 1

    # 不变量 3：轮次编号严格递增
    for i in range(1, len(iter_events)):
        assert iter_events[i].iteration == iter_events[i - 1].iteration + 1

    # 不变量 4：最后一个轮次编号等于总迭代数 + 1
    assert iter_events[-1].iteration == n_iterations + 1


# ══════════════════════════════════════════════════════════
# 单元测试：AgentEngine 回调机制
# 任务 2.3 - 验证 on_event=None 无副作用、回调异常不影响主流程
# 需求: 1.4
# ══════════════════════════════════════════════════════════


class TestOnEventNone:
    """测试 on_event=None 时 AgentEngine 行为正常，无副作用。"""

    @pytest.mark.asyncio
    async def test_chat_without_callback_returns_reply(self) -> None:
        """on_event=None 时，chat() 正常返回最终文本回复。"""
        registry = _make_registry_with_echo_tool()
        config = _make_config()
        engine = AgentEngine(config, registry)

        text_response = _make_text_response("你好，世界")
        engine._client.chat.completions.create = AsyncMock(
            return_value=text_response,
        )

        # 不传 on_event（默认 None）
        reply = await engine.chat("测试")

        assert reply == "你好，世界"

    @pytest.mark.asyncio
    async def test_chat_without_callback_with_tool_calls(self) -> None:
        """on_event=None 时，包含工具调用的循环也能正常完成。"""
        registry = _make_registry_with_echo_tool()
        config = _make_config()
        engine = AgentEngine(config, registry)

        tool_response = _make_tool_call_response(
            [("call_1", "echo_tool", '{"message": "hi"}')]
        )
        text_response = _make_text_response("完成")

        engine._client.chat.completions.create = AsyncMock(
            side_effect=[tool_response, text_response],
        )

        reply = await engine.chat("测试")

        assert reply == "完成"

    @pytest.mark.asyncio
    async def test_emit_with_none_callback_is_noop(self) -> None:
        """_emit() 在 on_event=None 时不抛出异常，直接返回。"""
        registry = _make_registry_with_echo_tool()
        config = _make_config()
        engine = AgentEngine(config, registry)

        event = ToolCallEvent(event_type=EventType.TOOL_CALL_START, tool_name="test")
        # 不应抛出任何异常
        engine._emit(None, event)


class TestCallbackExceptionIsolation:
    """测试回调异常不影响 AgentEngine 主流程。"""

    @pytest.mark.asyncio
    async def test_callback_exception_does_not_break_chat(self) -> None:
        """回调函数抛出异常时，chat() 仍正常完成并返回预期回复。"""
        registry = _make_registry_with_echo_tool()
        config = _make_config()
        engine = AgentEngine(config, registry)

        def bad_callback(event: ToolCallEvent) -> None:
            raise ValueError("回调内部错误")

        tool_response = _make_tool_call_response(
            [("call_1", "echo_tool", '{"message": "test"}')]
        )
        text_response = _make_text_response("最终回复")

        engine._client.chat.completions.create = AsyncMock(
            side_effect=[tool_response, text_response],
        )

        # 即使回调每次都抛异常，chat() 也应正常返回
        reply = await engine.chat("测试", on_event=bad_callback)

        assert reply == "最终回复"

    @pytest.mark.asyncio
    async def test_callback_exception_does_not_prevent_subsequent_events(self) -> None:
        """回调异常不阻止后续事件的发出——所有事件节点都应被触发。"""
        registry = _make_registry_with_echo_tool()
        config = _make_config()
        engine = AgentEngine(config, registry)

        received_types: list[EventType] = []

        def flaky_callback(event: ToolCallEvent) -> None:
            # 先记录事件类型，再抛异常
            received_types.append(event.event_type)
            raise RuntimeError("模拟回调崩溃")

        tool_response = _make_tool_call_response(
            [("call_1", "echo_tool", '{"message": "x"}')]
        )
        text_response = _make_text_response("完成")

        engine._client.chat.completions.create = AsyncMock(
            side_effect=[tool_response, text_response],
        )

        reply = await engine.chat("测试", on_event=flaky_callback)

        assert reply == "完成"

        # 验证所有关键事件类型都被触发过
        assert EventType.ITERATION_START in received_types
        assert EventType.TOOL_CALL_START in received_types
        assert EventType.TOOL_CALL_END in received_types

    @pytest.mark.asyncio
    async def test_callback_exception_logged_as_warning(self) -> None:
        """回调异常应被记录为 warning 级别日志。"""
        registry = _make_registry_with_echo_tool()
        config = _make_config()
        engine = AgentEngine(config, registry)

        def bad_callback(event: ToolCallEvent) -> None:
            raise TypeError("类型错误")

        event = ToolCallEvent(event_type=EventType.TOOL_CALL_START, tool_name="test")

        with patch("excelmanus.engine.logger") as mock_logger:
            engine._emit(bad_callback, event)
            mock_logger.warning.assert_called_once()
