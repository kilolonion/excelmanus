"""回退对话功能测试。"""

from __future__ import annotations

import pytest

from excelmanus.config import ExcelManusConfig
from excelmanus.memory import ConversationMemory


def _make_config(**overrides) -> ExcelManusConfig:
    defaults = dict(
        api_key="test-key",
        base_url="https://test.invalid/v1",
        model="test-model",
        workspace_root="/tmp/test-ws",
        max_context_tokens=100_000,
    )
    defaults.update(overrides)
    return ExcelManusConfig(**defaults)


class TestConversationMemoryRollback:
    """ConversationMemory.rollback_to_user_turn 测试。"""

    def _build_memory(self) -> ConversationMemory:
        mem = ConversationMemory(_make_config())
        # Turn 0: user → assistant → tool_call → tool_result
        mem.add_user_message("你好")
        mem.add_assistant_message("你好！有什么需要帮忙的？")
        # Turn 1: user → assistant
        mem.add_user_message("帮我读取文件")
        mem.add_tool_call("tc_1", "read_excel", '{"file": "a.xlsx"}')
        mem.add_tool_result("tc_1", "成功读取")
        mem.add_assistant_message("已读取文件 a.xlsx")
        # Turn 2: user → assistant
        mem.add_user_message("帮我写入数据")
        mem.add_assistant_message("已完成写入")
        return mem

    def test_rollback_to_turn_0(self):
        mem = self._build_memory()
        initial_count = mem.message_count
        removed = mem.rollback_to_user_turn(0)
        # 保留第一条 user 消息，其余全部移除
        assert mem.message_count == 1
        assert removed == initial_count - 1
        # 验证保留的消息是第一条 user 消息
        msgs = mem.get_messages()
        user_msgs = [m for m in msgs if m.get("role") == "user"]
        assert len(user_msgs) == 1
        assert user_msgs[0]["content"] == "你好"

    def test_rollback_to_turn_1(self):
        mem = self._build_memory()
        removed = mem.rollback_to_user_turn(1)
        assert removed > 0
        msgs = mem.get_messages()
        user_msgs = [m for m in msgs if m.get("role") == "user"]
        assert len(user_msgs) == 2
        assert user_msgs[0]["content"] == "你好"
        assert user_msgs[1]["content"] == "帮我读取文件"

    def test_rollback_to_last_turn(self):
        mem = self._build_memory()
        # 回退到最后一个 user turn（turn 2），只移除该 turn 之后的消息
        removed = mem.rollback_to_user_turn(2)
        assert removed == 1  # 只移除 "已完成写入" 这条 assistant 消息
        msgs = mem.get_messages()
        user_msgs = [m for m in msgs if m.get("role") == "user"]
        assert len(user_msgs) == 3

    def test_rollback_out_of_range(self):
        mem = self._build_memory()
        with pytest.raises(IndexError, match="超出范围"):
            mem.rollback_to_user_turn(10)

    def test_rollback_negative_index(self):
        mem = self._build_memory()
        with pytest.raises(IndexError, match="超出范围"):
            mem.rollback_to_user_turn(-1)

    def test_rollback_empty_memory(self):
        mem = ConversationMemory(_make_config())
        with pytest.raises(IndexError, match="超出范围"):
            mem.rollback_to_user_turn(0)


class TestListUserTurns:
    """ConversationMemory.list_user_turns 测试。"""

    def test_list_turns(self):
        mem = ConversationMemory(_make_config())
        mem.add_user_message("消息一")
        mem.add_assistant_message("回复一")
        mem.add_user_message("消息二比较长" * 20)
        mem.add_assistant_message("回复二")

        turns = mem.list_user_turns()
        assert len(turns) == 2
        assert turns[0]["index"] == 0
        assert turns[0]["content_preview"] == "消息一"
        assert turns[1]["index"] == 1
        # 长消息应截断到 80 字符
        assert len(turns[1]["content_preview"]) <= 83  # 80 + "..."

    def test_list_turns_empty(self):
        mem = ConversationMemory(_make_config())
        assert mem.list_user_turns() == []

    def test_list_turns_multimodal(self):
        mem = ConversationMemory(_make_config())
        mem.add_user_message([{"type": "text", "text": "看图"}])
        turns = mem.list_user_turns()
        assert turns[0]["content_preview"] == "[多模态消息]"


class TestControlCommandRollback:
    """验证 /rollback 命令注册。"""

    def test_rollback_in_control_commands(self):
        from excelmanus.control_commands import (
            CONTROL_COMMAND_SPECS,
            NORMALIZED_ALIAS_TO_CANONICAL_CONTROL_COMMAND,
            normalize_control_command,
        )
        commands = {spec.command for spec in CONTROL_COMMAND_SPECS}
        assert "/rollback" in commands

        normalized = normalize_control_command("/rollback")
        canonical = NORMALIZED_ALIAS_TO_CANONICAL_CONTROL_COMMAND.get(normalized)
        assert canonical == "/rollback"
