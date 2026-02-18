"""ConversationMemory 单元测试与属性测试。"""

from __future__ import annotations

import pytest
from hypothesis import given, assume
from hypothesis import strategies as st

from excelmanus.config import ExcelManusConfig
from excelmanus.memory import ConversationMemory, TokenCounter, _DEFAULT_SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def config() -> ExcelManusConfig:
    """创建测试用配置。"""
    return ExcelManusConfig(api_key="test-key", base_url="https://test.example.com/v1", model="test-model")


@pytest.fixture()
def memory(config: ExcelManusConfig) -> ConversationMemory:
    """创建测试用 ConversationMemory 实例。"""
    return ConversationMemory(config)


# ---------------------------------------------------------------------------
# TokenCounter 单元测试
# ---------------------------------------------------------------------------

class TestTokenCounter:
    """TokenCounter 估算逻辑测试。"""

    def test_empty_string_returns_zero(self) -> None:
        assert TokenCounter.count("") == 0

    def test_short_string_returns_at_least_one(self) -> None:
        assert TokenCounter.count("hi") >= 1

    def test_longer_string_scales(self) -> None:
        short = TokenCounter.count("hello")
        long = TokenCounter.count("hello world, this is a longer sentence")
        assert long > short

    def test_count_message_includes_overhead(self) -> None:
        msg = {"role": "user", "content": "你好"}
        tokens = TokenCounter.count_message(msg)
        # 至少包含固定开销 4 + content 的 token
        assert tokens >= 4

    def test_count_message_skips_none_values(self) -> None:
        msg = {"role": "assistant", "content": None}
        tokens = TokenCounter.count_message(msg)
        # 只有固定开销 + role 字符串
        assert tokens >= 4


# ---------------------------------------------------------------------------
# ConversationMemory 单元测试
# ---------------------------------------------------------------------------

class TestConversationMemory:
    """ConversationMemory 基本功能测试。"""

    def test_default_system_prompt_blocks_write_completion_claims_without_tool_result(self) -> None:
        assert "写入完成声明门禁" in _DEFAULT_SYSTEM_PROMPT
        assert "未收到写入类工具成功返回前" in _DEFAULT_SYSTEM_PROMPT

    def test_initial_get_messages_has_system_only(self, memory: ConversationMemory) -> None:
        """初始状态只有 system 消息。"""
        msgs = memory.get_messages()
        assert len(msgs) == 1
        assert msgs[0]["role"] == "system"
        assert msgs[0]["content"] == _DEFAULT_SYSTEM_PROMPT

    def test_add_user_message(self, memory: ConversationMemory) -> None:
        memory.add_user_message("你好")
        msgs = memory.get_messages()
        assert len(msgs) == 2
        assert msgs[1] == {"role": "user", "content": "你好"}

    def test_add_assistant_message(self, memory: ConversationMemory) -> None:
        memory.add_assistant_message("你好，有什么可以帮你？")
        msgs = memory.get_messages()
        assert len(msgs) == 2
        assert msgs[1]["role"] == "assistant"
        assert msgs[1]["content"] == "你好，有什么可以帮你？"

    def test_add_tool_call(self, memory: ConversationMemory) -> None:
        memory.add_tool_call("call_1", "read_excel", '{"path": "test.xlsx"}')
        msgs = memory.get_messages()
        assert len(msgs) == 2
        tool_msg = msgs[1]
        assert tool_msg["role"] == "assistant"
        assert tool_msg["content"] is None
        assert len(tool_msg["tool_calls"]) == 1
        assert tool_msg["tool_calls"][0]["id"] == "call_1"
        assert tool_msg["tool_calls"][0]["function"]["name"] == "read_excel"

    def test_add_tool_result(self, memory: ConversationMemory) -> None:
        memory.add_tool_result("call_1", "操作成功")
        msgs = memory.get_messages()
        assert len(msgs) == 2
        assert msgs[1]["role"] == "tool"
        assert msgs[1]["tool_call_id"] == "call_1"
        assert msgs[1]["content"] == "操作成功"

    def test_add_assistant_tool_message_keeps_extra_fields(
        self, memory: ConversationMemory
    ) -> None:
        """完整 tool 消息应保留扩展字段（如 reasoning_content）。"""
        memory.add_assistant_tool_message(
            {
                "role": "assistant",
                "content": None,
                "reasoning_content": "思考内容",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "read_excel", "arguments": "{}"},
                    }
                ],
            }
        )
        msg = memory.get_messages()[1]
        assert msg["reasoning_content"] == "思考内容"

    def test_message_ordering_preserved(self, memory: ConversationMemory) -> None:
        """消息顺序：system -> user -> assistant -> tool_call -> tool_result。"""
        memory.add_user_message("读取文件")
        memory.add_tool_call("c1", "read_excel", '{"path": "a.xlsx"}')
        memory.add_tool_result("c1", "数据内容")
        memory.add_assistant_message("已读取完成")

        msgs = memory.get_messages()
        assert [m["role"] for m in msgs] == [
            "system", "user", "assistant", "tool", "assistant"
        ]

    def test_clear_removes_history(self, memory: ConversationMemory) -> None:
        memory.add_user_message("你好")
        memory.add_assistant_message("你好")
        memory.clear()
        msgs = memory.get_messages()
        assert len(msgs) == 1
        assert msgs[0]["role"] == "system"

    def test_system_prompt_getter_setter(self, memory: ConversationMemory) -> None:
        assert memory.system_prompt == _DEFAULT_SYSTEM_PROMPT
        memory.system_prompt = "自定义提示词"
        assert memory.system_prompt == "自定义提示词"
        msgs = memory.get_messages()
        assert msgs[0]["content"] == "自定义提示词"

    def test_get_messages_returns_copy(self, memory: ConversationMemory) -> None:
        """get_messages 返回的列表修改不影响内部状态。"""
        memory.add_user_message("测试")
        msgs = memory.get_messages()
        msgs.pop()
        assert len(memory.get_messages()) == 2


# ---------------------------------------------------------------------------
# 截断策略测试
# ---------------------------------------------------------------------------

class TestTruncation:
    """token 截断策略测试。"""

    def test_truncation_keeps_system_prompt(self, config: ExcelManusConfig) -> None:
        """截断后 system prompt 始终保留。"""
        mem = ConversationMemory(config)
        # 降低阈值以便触发截断
        mem._truncation_threshold = 100

        # 添加大量消息直到触发截断
        for i in range(50):
            mem.add_user_message(f"这是第 {i} 条很长的消息，" * 10)

        msgs = mem.get_messages()
        assert msgs[0]["role"] == "system"
        assert msgs[0]["content"] == _DEFAULT_SYSTEM_PROMPT

    def test_truncation_removes_oldest_first(self, config: ExcelManusConfig) -> None:
        """截断时移除最早的消息，保留最近的。"""
        mem = ConversationMemory(config)
        mem._truncation_threshold = 2500

        mem.add_user_message("第一条消息")
        mem.add_assistant_message("第一条回复")
        mem.add_user_message("第二条消息")
        mem.add_assistant_message("第二条回复")
        # 添加一条很长的消息触发截断
        mem.add_user_message("这是一条非常长的消息，" * 100)

        msgs = mem.get_messages()
        # 最后一条（最长的）应该保留
        assert msgs[-1]["role"] == "user"
        assert "非常长" in msgs[-1]["content"]

    def test_truncation_removes_tool_call_and_result_together(
        self, config: ExcelManusConfig
    ) -> None:
        """截断 tool_call 消息时，对应的 tool_result 也一并移除。"""
        mem = ConversationMemory(config)
        mem._truncation_threshold = 200

        # 添加一组 tool_call + tool_result
        mem.add_tool_call("old_call", "read_excel", '{"path": "old.xlsx"}')
        mem.add_tool_result("old_call", "旧数据内容")
        # 添加新消息触发截断
        mem.add_user_message("新的请求，" * 100)

        msgs = mem.get_messages()
        # 不应有孤立的 tool result
        tool_msgs = [m for m in msgs if m.get("role") == "tool"]
        for tm in tool_msgs:
            # 每个 tool result 都应有对应的 tool_call
            call_id = tm["tool_call_id"]
            has_call = any(
                m.get("tool_calls") and any(tc["id"] == call_id for tc in m["tool_calls"])
                for m in msgs
            )
            assert has_call, f"孤立的 tool result: {call_id}"

    def test_no_truncation_under_threshold(self, config: ExcelManusConfig) -> None:
        """token 未超阈值时不截断。"""
        mem = ConversationMemory(config)
        # 默认阈值很高，少量消息不会触发
        mem.add_user_message("你好")
        mem.add_assistant_message("你好")
        msgs = mem.get_messages()
        assert len(msgs) == 3  # system + user + assistant

    def test_single_huge_message_is_shrunk_to_threshold(
        self, config: ExcelManusConfig
    ) -> None:
        """仅一条超长消息时，也应收缩到阈值内。"""
        mem = ConversationMemory(config)
        system_tokens = TokenCounter.count_message(
            {"role": "system", "content": mem.system_prompt}
        )
        mem._truncation_threshold = system_tokens + 200
        mem.add_user_message("x" * 8000)
        assert mem._total_tokens() <= mem._truncation_threshold

    def test_trim_for_request_enforces_final_message_budget(self, config: ExcelManusConfig) -> None:
        """trim_for_request 应按最终请求消息预算裁剪历史。"""
        mem = ConversationMemory(config)
        for i in range(30):
            mem.add_user_message(f"用户消息 {i} " * 20)
            mem.add_assistant_message(f"助手回复 {i} " * 20)

        system_prompts = ["系统提示 A", "系统提示 B"]
        result = mem.trim_for_request(
            system_prompts=system_prompts,
            max_context_tokens=2000,
            reserve_ratio=0.1,
        )
        total_tokens = sum(TokenCounter.count_message(m) for m in result)
        assert total_tokens <= int(2000 * 0.9)

    def test_trim_for_request_keeps_tool_call_and_result_consistency(
        self, config: ExcelManusConfig
    ) -> None:
        """trim_for_request 截断后不应出现孤立 tool result。"""
        mem = ConversationMemory(config)
        mem.add_tool_call("call_1", "read_excel", '{"file_path":"a.xlsx"}')
        mem.add_tool_result("call_1", "读取结果")
        mem.add_user_message("后续问题 " * 50)

        msgs = mem.trim_for_request(
            system_prompts=["系统提示"],
            max_context_tokens=1200,
            reserve_ratio=0.1,
        )
        call_ids: set[str] = set()
        for msg in msgs:
            if msg.get("tool_calls"):
                for tc in msg["tool_calls"]:
                    call_ids.add(tc["id"])
        for msg in msgs:
            if msg.get("role") == "tool":
                assert msg.get("tool_call_id") in call_ids


# ---------------------------------------------------------------------------
# Property 7：对话截断属性测试
# **Validates: Requirements 1.8**
# ---------------------------------------------------------------------------

# 生成随机消息内容的策略
message_content = st.text(min_size=1, max_size=500)


@given(
    messages=st.lists(
        st.tuples(
            st.sampled_from(["user", "assistant"]),
            message_content,
        ),
        min_size=1,
        max_size=30,
    ),
    threshold=st.integers(min_value=1500, max_value=5000),
)
def test_property_truncation_preserves_system_and_recent(
    messages: list[tuple[str, str]],
    threshold: int,
) -> None:
    """Property 7：截断后 system prompt 始终在首位，且最近消息被保留。

    **Validates: Requirements 1.8**
    """
    system_tokens = TokenCounter.count_message(
        {"role": "system", "content": _DEFAULT_SYSTEM_PROMPT}
    )
    min_last_msg_tokens = TokenCounter.count_message(
        {"role": "user", "content": "x"}
    )
    # 阈值若低于 system 消息自身，无法同时满足“保留最近消息”。
    assume(threshold > system_tokens + min_last_msg_tokens)

    config = ExcelManusConfig(api_key="test-key", base_url="https://test.example.com/v1", model="test-model")
    mem = ConversationMemory(config)
    mem._truncation_threshold = threshold

    for role, content in messages:
        if role == "user":
            mem.add_user_message(content)
        else:
            mem.add_assistant_message(content)

    result = mem.get_messages()

    # 不变量 1：system 消息始终在首位
    assert len(result) >= 1
    assert result[0]["role"] == "system"
    assert result[0]["content"] == _DEFAULT_SYSTEM_PROMPT

    # 不变量 2：如果有非 system 消息，最后一条应对应最后添加的角色
    if len(result) > 1:
        last_role, last_content = messages[-1]
        assert result[-1]["role"] == last_role
        # 在仅剩一条历史消息且超阈值时，最后一条内容允许被收缩
        if len(result) > 2:
            assert result[-1]["content"] == last_content
        else:
            content = result[-1]["content"]
            assert isinstance(content, str)
            assert (
                content == last_content
                or content.startswith("[截断]")
                or content == ""
                or (content != "" and last_content.endswith(content))
            )

    # 不变量 3：消息角色顺序保持原始相对顺序（不含 system）
    history = result[1:]
    result_roles = [m["role"] for m in history]
    expected_suffix_roles = [role for role, _ in messages[-len(result_roles):]]
    assert result_roles == expected_suffix_roles

    # 不变量 4：总 token 数不超过上下文窗口限制
    total = sum(TokenCounter.count_message(m) for m in result)
    # 截断阈值是软限制，但总量不应远超上下文窗口
    assert total <= mem._max_context_tokens


@given(
    n_rounds=st.integers(min_value=1, max_value=10),
    content_size=st.integers(min_value=10, max_value=200),
)
def test_property_truncation_no_orphan_tool_results(
    n_rounds: int,
    content_size: int,
) -> None:
    """Property 7 补充：截断后不存在孤立的 tool result 消息。

    **Validates: Requirements 1.8**
    """
    config = ExcelManusConfig(api_key="test-key", base_url="https://test.example.com/v1", model="test-model")
    mem = ConversationMemory(config)
    mem._truncation_threshold = 150  # 较低阈值以触发截断

    # 模拟多轮 tool calling 对话
    for i in range(n_rounds):
        mem.add_user_message("x" * content_size)
        call_id = f"call_{i}"
        mem.add_tool_call(call_id, "test_tool", '{"arg": "val"}')
        mem.add_tool_result(call_id, "y" * content_size)
        mem.add_assistant_message("z" * content_size)

    result = mem.get_messages()

    # 收集所有 tool_call id
    all_call_ids: set[str] = set()
    for m in result:
        if m.get("tool_calls"):
            for tc in m["tool_calls"]:
                all_call_ids.add(tc["id"])

    # 每个 tool result 的 call_id 必须在 tool_calls 中存在
    for m in result:
        if m.get("role") == "tool":
            assert m["tool_call_id"] in all_call_ids, (
                f"孤立的 tool result: {m['tool_call_id']}"
            )
