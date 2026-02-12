"""AgentEngine 单元测试：覆盖 Tool Calling 循环核心逻辑。"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from excelmanus.config import ExcelManusConfig
from excelmanus.engine import AgentEngine, ChatResult, ToolCallResult
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
