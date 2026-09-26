"""ConversationMemory 单元测试与属性测试。"""

from __future__ import annotations

import pytest
from hypothesis import HealthCheck, given, assume, settings
from hypothesis import strategies as st

from excelmanus.config import ExcelManusConfig
from excelmanus.memory import ConversationMemory, TokenCounter, _DEFAULT_SYSTEM_PROMPT, IMAGE_TOKEN_ESTIMATE


# ---------------------------------------------------------------------------
# 测试固件
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

    def test_default_system_prompt_contains_persona(self) -> None:
        assert "你是 ExcelManus" in _DEFAULT_SYSTEM_PROMPT
        assert "VERSION_CONFLICT" in _DEFAULT_SYSTEM_PROMPT
        assert "uploads/" in _DEFAULT_SYSTEM_PROMPT
        assert "问一个具体问题" in _DEFAULT_SYSTEM_PROMPT
        assert "不擅自换文件" in _DEFAULT_SYSTEM_PROMPT
        assert "宿主有轮次上限" not in _DEFAULT_SYSTEM_PROMPT

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
        assert msgs[1]["role"] == "user"
        assert msgs[1]["content"] == "你好"
        assert "_ui_hidden" not in msgs[1]

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
        threshold = 100

        for i in range(50):
            mem.add_user_message(f"这是第 {i} 条很长的消息，" * 10)
        mem._truncate_history_to_threshold(threshold, None)

        msgs = mem.get_messages()
        assert msgs[0]["role"] == "system"
        assert msgs[0]["content"] == _DEFAULT_SYSTEM_PROMPT

    def test_truncation_removes_oldest_first(self, config: ExcelManusConfig) -> None:
        """截断时移除最早的消息，保留最近的。"""
        mem = ConversationMemory(config)
        # 阈值需大于 system token 数，以便保留最后一条时仍能保留部分内容
        system_tokens = TokenCounter.count_message(
            {"role": "system", "content": mem.system_prompt}
        )
        threshold = system_tokens + 500

        mem.add_user_message("第一条消息")
        mem.add_assistant_message("第一条回复")
        mem.add_user_message("第二条消息")
        mem.add_assistant_message("第二条回复")
        mem.add_user_message("这是一条非常长的消息，" * 100)
        mem._truncate_history_to_threshold(threshold, None)

        msgs = mem.get_messages()
        # 最后一条（最长的）应保留，且要么保留原文/截断后缀，要么因 system 过大被缩为空
        assert msgs[-1]["role"] == "user"
        content = msgs[-1].get("content") or ""
        assert "非常长" in content or content == "" or content.startswith("[截断]")

    def test_truncation_removes_tool_call_and_result_together(
        self, config: ExcelManusConfig
    ) -> None:
        """截断 tool_call 消息时，对应的 tool_result 也一并移除。"""
        mem = ConversationMemory(config)
        threshold = 200

        # 添加一组 tool_call + tool_result
        mem.add_tool_call("old_call", "read_excel", '{"path": "old.xlsx"}')
        mem.add_tool_result("old_call", "旧数据内容")
        mem.add_user_message("新的请求，" * 100)
        mem._truncate_history_to_threshold(threshold, None)

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
        threshold = system_tokens + 200
        mem.add_user_message("x" * 8000)
        mem._truncate_history_to_threshold(threshold, None)
        assert mem._total_tokens_with_system_messages(None) <= threshold

    def test_project_for_request_does_not_drop_durable_history(self, config: ExcelManusConfig) -> None:
        """发送投影不得从头部删 durable；预算压力交给 compaction。"""
        mem = ConversationMemory(config)
        for i in range(30):
            mem.add_user_message(f"用户消息 {i} " * 20)
            mem.add_assistant_message(f"助手回复 {i} " * 20)
        before = len(mem.messages)
        result = mem.project_for_request(
            system_prompts=["系统提示 A"],
        )
        assert len(mem.messages) == before
        assert result[0]["role"] == "system"
        assert any(m.get("content") == mem.messages[0]["content"] for m in result if m.get("role") == "user")

    def test_project_for_request_puts_context_prompts_at_tail(
        self, config: ExcelManusConfig
    ) -> None:
        mem = ConversationMemory(config)
        mem.add_user_message("你好")
        mem.add_user_message("## Hook 上下文\nnotice", hidden=True, prompt_kind="hook")
        msgs = mem.project_for_request(
            system_prompts=["系统提示"],
        )
        assert msgs[0]["role"] == "system"
        assert msgs[1]["role"] == "user"
        assert msgs[1]["content"] == "你好"
        assert msgs[2]["role"] == "user"
        assert msgs[2]["content"] == "## Hook 上下文\nnotice"

    def test_project_for_request_keeps_tool_call_and_result_consistency(
        self, config: ExcelManusConfig
    ) -> None:
        """project_for_request 截断后不应出现孤立 tool result。"""
        mem = ConversationMemory(config)
        mem.add_tool_call("call_1", "read_excel", '{"file_path":"a.xlsx"}')
        mem.add_tool_result("call_1", "读取结果")
        mem.add_user_message("后续问题 " * 50)

        msgs = mem.project_for_request(
            system_prompts=["系统提示"],
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
# 多模态支持测试
# ---------------------------------------------------------------------------

class TestMultimodalMemory:
    """多模态 content parts 支持测试。"""

    def test_add_user_message_str_backward_compat(self, config: ExcelManusConfig) -> None:
        """纯文本消息向后兼容。"""
        mem = ConversationMemory(config)
        mem.add_user_message("hello")
        msgs = mem.get_messages()
        assert msgs[-1]["role"] == "user"
        assert msgs[-1]["content"] == "hello"
        assert "_image_id" not in msgs[-1]

    def test_add_user_message_list_content(self, config: ExcelManusConfig, tmp_path, monkeypatch) -> None:
        """多模态 content parts 写入 durable image ref。"""
        monkeypatch.setenv("EXCELMANUS_HOME", str(tmp_path))
        from excelmanus.attachments.store import reset_attachment_store
        reset_attachment_store()
        mem = ConversationMemory(config)
        png = (
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
        )
        parts = [
            {"type": "text", "text": "看这张图"},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{png}", "detail": "auto"}},
        ]
        mem.add_user_message(parts)
        msgs = mem.get_messages()
        assert msgs[-1]["role"] == "user"
        content = msgs[-1]["content"]
        assert isinstance(content, list)
        assert content[0]["type"] == "text"
        assert content[1]["type"] == "image"
        assert content[1]["attachment"]["attachmentId"].startswith("sha256:")
        assert "_image_id" not in msgs[-1]

    def test_add_user_image_message(self, config: ExcelManusConfig, tmp_path, monkeypatch) -> None:
        """便捷图片注入写入 ref，不把 base64 留在历史上。"""
        monkeypatch.setenv("EXCELMANUS_HOME", str(tmp_path))
        from excelmanus.attachments.store import reset_attachment_store
        reset_attachment_store()
        mem = ConversationMemory(config)
        png = (
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
        )
        from excelmanus.attachments.admit import admit_image_bytes, decode_image_payload

        ref = admit_image_bytes(decode_image_payload(png), media_type="image/png")
        mem.add_user_message([{"type": "image", "attachment": ref.to_dict()}])
        msgs = mem.get_messages()
        last = msgs[-1]
        assert last["role"] == "user"
        assert isinstance(last["content"], list)
        assert last["content"][0]["type"] == "image"

    def test_count_message_with_image(self, config: ExcelManusConfig, tmp_path, monkeypatch) -> None:
        """图片消息 token 估算。"""
        monkeypatch.setenv("EXCELMANUS_HOME", str(tmp_path))
        from excelmanus.attachments.store import reset_attachment_store
        reset_attachment_store()
        mem = ConversationMemory(config)
        png = (
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
        )
        from excelmanus.attachments.admit import admit_image_bytes, decode_image_payload

        ref = admit_image_bytes(decode_image_payload(png), media_type="image/png")
        mem.add_user_message([{"type": "image", "attachment": ref.to_dict()}])
        msgs = mem.get_messages()
        count = TokenCounter.count_message(msgs[-1])
        assert count >= IMAGE_TOKEN_ESTIMATE


# ---------------------------------------------------------------------------
# Property 7：对话截断属性测试
# **验证：需求 1.8**
# ---------------------------------------------------------------------------

# 生成随机消息内容的策略
message_content = st.text(min_size=1, max_size=500)


# 属性测试使用短 system prompt，使 threshold 策略 [1500, 5000] 能通过 assume
_PROPERTY_TEST_SYSTEM_PROMPT = "Short system prompt for property test."


@settings(suppress_health_check=[HealthCheck.filter_too_much])
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

    **验证：需求 1.8**
    """
    system_tokens = TokenCounter.count_message(
        {"role": "system", "content": _PROPERTY_TEST_SYSTEM_PROMPT}
    )
    min_last_msg_tokens = TokenCounter.count_message(
        {"role": "user", "content": "x"}
    )
    # 阈值若低于 system 消息自身，无法同时满足“保留最近消息”。
    assume(threshold > system_tokens + min_last_msg_tokens)

    config = ExcelManusConfig(api_key="test-key", base_url="https://test.example.com/v1", model="test-model")
    mem = ConversationMemory(config)
    mem.system_prompt = _PROPERTY_TEST_SYSTEM_PROMPT
    for role, content in messages:
        if role == "user":
            mem.add_user_message(content)
        else:
            mem.add_assistant_message(content)

    mem._truncate_history_to_threshold(threshold, None)
    result = mem.get_messages()

    # 不变量 1：system 消息始终在首位
    assert len(result) >= 1
    assert result[0]["role"] == "system"
    assert result[0]["content"] == _PROPERTY_TEST_SYSTEM_PROMPT

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

    **验证：需求 1.8**
    """
    config = ExcelManusConfig(api_key="test-key", base_url="https://test.example.com/v1", model="test-model")
    mem = ConversationMemory(config)
    threshold = 150  # 较低阈值以触发截断

    # 模拟多轮 tool calling 对话
    for i in range(n_rounds):
        mem.add_user_message("x" * content_size)
        call_id = f"call_{i}"
        mem.add_tool_call(call_id, "test_tool", '{"arg": "val"}')
        mem.add_tool_result(call_id, "y" * content_size)
        mem.add_assistant_message("z" * content_size)

    mem._truncate_history_to_threshold(threshold, None)
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


# ---------------------------------------------------------------------------
# repair_dangling_tool_calls 回归测试
# ---------------------------------------------------------------------------

class TestRepairDanglingToolCalls:
    """中断后悬空 tool_call 修复测试。"""

    def test_no_messages_returns_zero(self, memory: ConversationMemory) -> None:
        """空 memory 不做任何修复。"""
        assert memory.repair_dangling_tool_calls() == 0

    def test_no_dangling_returns_zero(self, memory: ConversationMemory) -> None:
        """所有 tool_call 都有对应 result 时不做修复。"""
        memory.add_assistant_tool_message({
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "tc_1", "type": "function", "function": {"name": "read_excel", "arguments": "{}"}},
            ],
        })
        memory.add_tool_result("tc_1", "ok")
        assert memory.repair_dangling_tool_calls() == 0

    def test_all_missing_results_repaired(self, memory: ConversationMemory) -> None:
        """assistant 有 2 个 tool_calls 但 0 个 result → 补 2 个。"""
        memory.add_assistant_tool_message({
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "tc_a", "type": "function", "function": {"name": "read_excel", "arguments": "{}"}},
                {"id": "tc_b", "type": "function", "function": {"name": "write_cells", "arguments": "{}"}},
            ],
        })
        repaired = memory.repair_dangling_tool_calls()
        assert repaired == 2
        # 验证补的 result 内容
        tool_results = [m for m in memory.messages if m.get("role") == "tool"]
        assert len(tool_results) == 2
        assert {m["tool_call_id"] for m in tool_results} == {"tc_a", "tc_b"}
        # 未登记效果的工具（read_excel / write_cells）按"结果未确认"给：不能假定已经生效。
        for m in tool_results:
            assert "不能假定已经生效" in m["content"]

    def test_partial_missing_results_repaired(self, memory: ConversationMemory) -> None:
        """assistant 有 3 个 tool_calls，只有 1 个 result → 补 2 个。"""
        memory.add_assistant_tool_message({
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "tc_x", "type": "function", "function": {"name": "t1", "arguments": "{}"}},
                {"id": "tc_y", "type": "function", "function": {"name": "t2", "arguments": "{}"}},
                {"id": "tc_z", "type": "function", "function": {"name": "t3", "arguments": "{}"}},
            ],
        })
        memory.add_tool_result("tc_x", "done")
        repaired = memory.repair_dangling_tool_calls()
        assert repaired == 2
        tool_ids = [m["tool_call_id"] for m in memory.messages if m.get("role") == "tool"]
        assert set(tool_ids) == {"tc_x", "tc_y", "tc_z"}

    def test_idempotent(self, memory: ConversationMemory) -> None:
        """连续调用两次不会重复补充。"""
        memory.add_assistant_tool_message({
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "tc_1", "type": "function", "function": {"name": "t", "arguments": "{}"}},
            ],
        })
        assert memory.repair_dangling_tool_calls() == 1
        assert memory.repair_dangling_tool_calls() == 0

    def test_only_repairs_latest_group(self, memory: ConversationMemory) -> None:
        """只修复最近一组 tool_call，不影响更早的完整对话。"""
        # 第一轮：完整的 tool_call + result
        memory.add_user_message("第一轮")
        memory.add_assistant_tool_message({
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "tc_old", "type": "function", "function": {"name": "t", "arguments": "{}"}},
            ],
        })
        memory.add_tool_result("tc_old", "ok")
        memory.add_assistant_message("第一轮完成")
        # 第二轮：用户消息 + 中断的 tool_call
        memory.add_user_message("第二轮")
        memory.add_assistant_tool_message({
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "tc_new", "type": "function", "function": {"name": "t", "arguments": "{}"}},
            ],
        })
        repaired = memory.repair_dangling_tool_calls()
        assert repaired == 1
        # 总共有 2 个 tool results（1 个原有 + 1 个补充）
        tool_results = [m for m in memory.messages if m.get("role") == "tool"]
        assert len(tool_results) == 2

    def test_text_reply_tail_no_repair(self, memory: ConversationMemory) -> None:
        """尾部是纯文本 assistant 消息（非 tool_call）时不做修复。"""
        memory.add_user_message("hello")
        memory.add_assistant_message("hi there")
        assert memory.repair_dangling_tool_calls() == 0


# ---------------------------------------------------------------------------
# project_for_request 消息清洗回归测试
# ---------------------------------------------------------------------------


class TestSanitizeMessagesForApi:
    """验证 project_for_request 在发送到 API 前剥离非标准字段。

    回归场景：LLM 返回的 assistant 消息包含 provider 特有字段
    （thinking / reasoning / reasoning_content），这些字段被存入 memory 后
    在后续轮次随 project_for_request 一并发送给 API，导致部分 provider
    返回 400 Bad Request。
    """

    def test_strips_thinking_fields_from_assistant(self, memory: ConversationMemory) -> None:
        """assistant 回放字段必须保留，不得再剥掉后补空串。"""
        memory.add_user_message("hello")
        memory.add_assistant_tool_message({
            "role": "assistant",
            "content": "Let me check",
            "tool_calls": [
                {"id": "tc_1", "type": "function", "function": {"name": "read_excel", "arguments": "{}"}},
            ],
            "thinking": "I should read the file first",
            "reasoning": "I should read the file first",
            "reasoning_content": "I should read the file first",
            "replay_state": {"thinking_blocks": [{"type": "thinking", "thinking": "I should read the file first", "signature": "sig"}]},
        })
        memory.add_tool_result("tc_1", "ok")

        msgs = memory.project_for_request(
            system_prompts=["You are a helpful assistant."],
        )
        assistant_msgs = [m for m in msgs if m.get("role") == "assistant"]
        assert len(assistant_msgs) == 1
        a = assistant_msgs[0]
        assert a["reasoning_content"] == "I should read the file first"
        assert "thinking" not in a
        assert "reasoning" not in a
        assert a["replay_state"]["thinking_blocks"][0]["signature"] == "sig"
        assert a["role"] == "assistant"
        assert a["content"] == "Let me check"
        assert len(a["tool_calls"]) == 1

    def test_strips_null_tool_calls(self, memory: ConversationMemory) -> None:
        """tool_calls 为 None 时应从输出中移除该键。"""
        memory.add_user_message("hello")
        memory.add_assistant_tool_message({
            "role": "assistant",
            "content": "Sure thing",
            "tool_calls": None,
            "reasoning_content": None,
        })

        msgs = memory.project_for_request(
            system_prompts=["system"],
        )
        assistant_msgs = [m for m in msgs if m.get("role") == "assistant"]
        assert len(assistant_msgs) == 1
        a = assistant_msgs[0]
        assert "tool_calls" not in a
        assert "reasoning_content" not in a or a.get("reasoning_content") in (None, "")

    def test_preserves_standard_tool_message_fields(self, memory: ConversationMemory) -> None:
        """tool 消息应仅保留 role/content/tool_call_id/name。"""
        memory.add_user_message("go")
        memory.add_assistant_tool_message({
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "tc_x", "type": "function", "function": {"name": "t", "arguments": "{}"}},
            ],
        })
        memory.add_tool_result("tc_x", "done")

        msgs = memory.project_for_request(
            system_prompts=["sys"],
        )
        tool_msgs = [m for m in msgs if m.get("role") == "tool"]
        assert len(tool_msgs) == 1
        t = tool_msgs[0]
        assert set(t.keys()) <= {"role", "content", "tool_call_id", "name"}

    def test_project_for_request_does_not_rewrite_durable_prefix(self, memory: ConversationMemory) -> None:
        """发送投影不得为了 user-first 删掉 durable 前缀。"""
        memory.add_user_message("first")
        memory.add_assistant_tool_message({
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "tc_a", "type": "function", "function": {"name": "t", "arguments": "{}"}},
            ],
        })
        memory.add_tool_result("tc_a", "ok")
        memory.add_assistant_message("middle text")
        memory.add_user_message("second")
        memory.add_assistant_message("final")
        memory._messages.pop(0)
        roles_before = [m.get("role") for m in memory.messages]

        msgs = memory.project_for_request(
            system_prompts=["sys"],
        )
        assert [m.get("role") for m in memory.messages] == roles_before
        non_system = [m for m in msgs if m.get("role") != "system"]
        assert non_system[0]["role"] == "assistant"

    def test_in_history_system_downgraded_to_user_on_wire(
        self, memory: ConversationMemory,
    ) -> None:
        """R06 复现：in-history system_update 在严格 OpenAI 网关触发
        'System message must be at the beginning' 400；wire 上须降级为 user。"""
        memory.add_user_message("first")
        memory.add_system_message("新系统提示内容", prompt_kind="system_update")
        memory.add_user_message("second")

        msgs = memory.project_for_request(system_prompts=["sys"])
        # 首位 system 保留；历史中不得再出现 system 角色
        assert msgs[0]["role"] == "system"
        mid = [m for m in msgs[1:] if m.get("role") == "system"]
        assert mid == []
        demoted = [
            m for m in msgs
            if m.get("role") == "user" and "新系统提示内容" in str(m.get("content"))
        ]
        assert len(demoted) == 1
        assert "系统提示" in demoted[0]["content"]
        # durable 原样保留 system 角色（回放语义不变）
        assert any(m.get("role") == "system" for m in memory.messages)

    def test_user_multimodal_content_preserved(
        self, memory: ConversationMemory, tmp_path, monkeypatch,
    ) -> None:
        """durable 历史存 ref；请求投影再派生 image_url。"""
        monkeypatch.setenv("EXCELMANUS_HOME", str(tmp_path))
        from excelmanus.attachments.store import reset_attachment_store
        reset_attachment_store()
        png = (
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
        )
        parts = [
            {"type": "text", "text": "describe this"},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{png}", "detail": "auto"}},
        ]
        memory.add_user_message(parts)
        durable = memory.messages[-1]
        assert durable["content"][1]["type"] == "image"
        assert "_image_id" not in durable

        msgs = memory.project_for_request(
            system_prompts=["sys"],
        )
        user_msgs = [m for m in msgs if m.get("role") == "user"]
        assert len(user_msgs) == 1
        u = user_msgs[0]
        assert isinstance(u["content"], list)
        assert any(p.get("type") == "image_url" for p in u["content"])
        assert "_image_id" not in u
        assert durable["content"][1]["type"] == "image"


def test_legacy_image_lifecycle_and_truncation_entry_removed(config: ExcelManusConfig) -> None:
    """旧图片生命周期空实现与旧原地截断入口必须清零。"""
    mem = ConversationMemory(config)
    for legacy in (
        "mark_images_sent",
        "manage_image_lifecycle",
        "reset_image_tracking",
        "_truncate_if_needed",
        "_ensure_starts_with_user",
    ):
        assert not hasattr(mem, legacy), f"旧入口仍存在: {legacy}"
    assert not hasattr(mem, "_truncation_threshold")
