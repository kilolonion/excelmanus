"""测试上下文自动压缩（Compaction）功能。"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, replace
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from excelmanus.compaction import (
    COMPACTION_SYSTEM_PROMPT,
    CompactionManager,
    CompactionResult,
    CompactionStats,
    _format_messages_for_compaction,
)
from excelmanus.config import ExcelManusConfig
from excelmanus.memory import ConversationMemory


# ── Fixtures ──────────────────────────────────────────────


def _make_config(**overrides: Any) -> ExcelManusConfig:
    """创建测试用配置，必填字段用 dummy 值填充。"""
    defaults = {
        "api_key": "test-key",
        "base_url": "https://api.example.com/v1",
        "model": "test-model",
        "max_context_tokens": 1000,
        "compaction_enabled": True,
        "compaction_threshold_ratio": 0.85,
        "compaction_keep_recent_turns": 2,
        "compaction_max_summary_tokens": 500,
        "summarization_enabled": False,
    }
    defaults.update(overrides)
    return ExcelManusConfig(**defaults)


def _make_memory(config: ExcelManusConfig) -> ConversationMemory:
    """创建测试用 ConversationMemory。"""
    return ConversationMemory(config)


def _mock_client(summary_text: str = "测试摘要内容") -> AsyncMock:
    """创建返回固定摘要的 mock client。"""
    client = AsyncMock()
    mock_response = MagicMock()
    mock_response.choices = [
        MagicMock(message=MagicMock(content=summary_text))
    ]
    client.chat.completions.create = AsyncMock(return_value=mock_response)
    return client


# ── CompactionManager 基础测试 ────────────────────────────


class TestCompactionManagerBasic:
    """CompactionManager 基础功能测试。"""

    def test_init_inherits_config(self) -> None:
        config = _make_config(compaction_enabled=True)
        mgr = CompactionManager(config)
        assert mgr.enabled is True
        assert mgr.stats.compaction_count == 0

    def test_init_disabled(self) -> None:
        config = _make_config(compaction_enabled=False)
        mgr = CompactionManager(config)
        assert mgr.enabled is False

    def test_enabled_toggle(self) -> None:
        config = _make_config(compaction_enabled=True)
        mgr = CompactionManager(config)
        mgr.enabled = False
        assert mgr.enabled is False
        mgr.enabled = True
        assert mgr.enabled is True


# ── should_compact 测试 ──────────────────────────────────


class TestShouldCompact:
    """should_compact 阈值检测测试。"""

    def test_below_threshold(self) -> None:
        config = _make_config(max_context_tokens=500_000, compaction_threshold_ratio=0.85)
        mgr = CompactionManager(config)
        memory = _make_memory(config)
        # 空 memory，远低于阈值
        assert mgr.should_compact(memory, None) is False

    def test_above_threshold(self) -> None:
        config = _make_config(max_context_tokens=1000, compaction_threshold_ratio=0.85)
        mgr = CompactionManager(config)
        memory = _make_memory(config)
        # 填充大量消息使 token 超阈值
        for i in range(100):
            memory.add_user_message(f"这是一条很长的测试消息，编号 {i}，" * 20)
            memory.add_assistant_message(f"这是助手的回复 {i}，" * 20)
        assert mgr.should_compact(memory, None) is True

    def test_disabled_never_triggers(self) -> None:
        config = _make_config(max_context_tokens=100, compaction_threshold_ratio=0.1)
        mgr = CompactionManager(config)
        mgr.enabled = False
        memory = _make_memory(config)
        for i in range(50):
            memory.add_user_message(f"消息 {i}" * 50)
        assert mgr.should_compact(memory, None) is False


# ── auto_compact 测试 ────────────────────────────────────


class TestAutoCompact:
    """auto_compact 自动压缩测试。"""

    @pytest.mark.asyncio
    async def test_successful_compact(self) -> None:
        config = _make_config(
            max_context_tokens=10000,
            compaction_keep_recent_turns=2,
        )
        mgr = CompactionManager(config)
        memory = _make_memory(config)
        client = _mock_client("## 文件状态\n- /path/to/file.xlsx")

        # 添加足够多的消息
        for i in range(10):
            memory.add_user_message(f"用户消息 {i}")
            memory.add_assistant_message(f"助手回复 {i}")

        messages_before = len(memory._messages)

        result = await mgr.auto_compact(
            memory=memory,
            system_msgs=None,
            client=client,
            summary_model="test-model",
        )

        assert result.success is True
        assert result.messages_before == messages_before
        assert result.messages_after < messages_before
        assert "文件状态" in result.summary_text
        assert mgr.stats.compaction_count == 1
        assert mgr.stats.last_compaction_at is not None

        # 验证合成消息
        assert memory._messages[0]["role"] == "user"
        assert "[系统]" in memory._messages[0]["content"]
        assert memory._messages[1]["role"] == "assistant"
        assert "[对话摘要]" in memory._messages[1]["content"]

    @pytest.mark.asyncio
    async def test_too_few_messages(self) -> None:
        config = _make_config(
            max_context_tokens=10000,
            compaction_keep_recent_turns=5,
        )
        mgr = CompactionManager(config)
        memory = _make_memory(config)
        client = _mock_client()

        # 只有 3 轮 user 消息，不够 keep_recent_turns=5
        for i in range(3):
            memory.add_user_message(f"用户消息 {i}")
            memory.add_assistant_message(f"助手回复 {i}")

        result = await mgr.auto_compact(
            memory=memory,
            system_msgs=None,
            client=client,
            summary_model="test-model",
        )

        assert result.success is False
        assert "不足" in result.error
        # 不应调用 LLM
        client.chat.completions.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_messages(self) -> None:
        config = _make_config(max_context_tokens=10000)
        mgr = CompactionManager(config)
        memory = _make_memory(config)
        client = _mock_client()

        result = await mgr.auto_compact(
            memory=memory,
            system_msgs=None,
            client=client,
            summary_model="test-model",
        )

        assert result.success is False
        assert "没有可压缩" in result.error

    @pytest.mark.asyncio
    async def test_llm_call_failure_falls_back(self) -> None:
        config = _make_config(
            max_context_tokens=10000,
            compaction_keep_recent_turns=2,
        )
        mgr = CompactionManager(config)
        memory = _make_memory(config)

        # 创建会失败的 client
        client = AsyncMock()
        client.chat.completions.create = AsyncMock(
            side_effect=Exception("API 调用失败")
        )

        for i in range(10):
            memory.add_user_message(f"用户消息 {i}")
            memory.add_assistant_message(f"助手回复 {i}")

        result = await mgr.auto_compact(
            memory=memory,
            system_msgs=None,
            client=client,
            summary_model="test-model",
        )

        assert result.success is False
        assert "摘要失败" in result.error
        # 压缩次数不应增加
        assert mgr.stats.compaction_count == 0

    @pytest.mark.asyncio
    async def test_empty_summary_falls_back(self) -> None:
        config = _make_config(
            max_context_tokens=10000,
            compaction_keep_recent_turns=2,
        )
        mgr = CompactionManager(config)
        memory = _make_memory(config)
        client = _mock_client("")  # 空摘要

        for i in range(10):
            memory.add_user_message(f"用户消息 {i}")
            memory.add_assistant_message(f"助手回复 {i}")

        result = await mgr.auto_compact(
            memory=memory,
            system_msgs=None,
            client=client,
            summary_model="test-model",
        )

        assert result.success is False
        assert "摘要为空" in result.error


# ── manual_compact 测试 ──────────────────────────────────


class TestManualCompact:
    """manual_compact 手动压缩测试。"""

    @pytest.mark.asyncio
    async def test_manual_compact_with_custom_instruction(self) -> None:
        config = _make_config(
            max_context_tokens=10000,
            compaction_keep_recent_turns=2,
        )
        mgr = CompactionManager(config)
        memory = _make_memory(config)
        client = _mock_client("自定义摘要")

        for i in range(10):
            memory.add_user_message(f"用户消息 {i}")
            memory.add_assistant_message(f"助手回复 {i}")

        result = await mgr.manual_compact(
            memory=memory,
            system_msgs=None,
            client=client,
            summary_model="test-model",
            custom_instruction="只保留文件操作记录",
        )

        assert result.success is True
        # 验证自定义指令被传入
        call_args = client.chat.completions.create.call_args
        user_msg = call_args.kwargs["messages"][1]["content"]
        assert "只保留文件操作记录" in user_msg

    @pytest.mark.asyncio
    async def test_manual_compact_without_instruction(self) -> None:
        config = _make_config(
            max_context_tokens=10000,
            compaction_keep_recent_turns=2,
        )
        mgr = CompactionManager(config)
        memory = _make_memory(config)
        client = _mock_client("基础摘要")

        for i in range(10):
            memory.add_user_message(f"用户消息 {i}")
            memory.add_assistant_message(f"助手回复 {i}")

        result = await mgr.manual_compact(
            memory=memory,
            system_msgs=None,
            client=client,
            summary_model="test-model",
        )

        assert result.success is True
        assert mgr.stats.compaction_count == 1


# ── get_status 测试 ──────────────────────────────────────


class TestGetStatus:
    """get_status 状态查询测试。"""

    def test_initial_status(self) -> None:
        config = _make_config(max_context_tokens=1000)
        mgr = CompactionManager(config)
        memory = _make_memory(config)

        status = mgr.get_status(memory, None)

        assert status["enabled"] is True
        assert status["max_tokens"] == 1000
        assert status["compaction_count"] == 0
        assert status["last_compaction_at"] is None

    @pytest.mark.asyncio
    async def test_status_after_compact(self) -> None:
        config = _make_config(
            max_context_tokens=10000,
            compaction_keep_recent_turns=2,
        )
        mgr = CompactionManager(config)
        memory = _make_memory(config)
        client = _mock_client("摘要")

        for i in range(10):
            memory.add_user_message(f"用户消息 {i}")
            memory.add_assistant_message(f"助手回复 {i}")

        await mgr.auto_compact(
            memory=memory,
            system_msgs=None,
            client=client,
            summary_model="test-model",
        )

        status = mgr.get_status(memory, None)
        assert status["compaction_count"] == 1
        assert status["last_compaction_at"] is not None


# ── get_token_usage_ratio 测试 ───────────────────────────


class TestTokenUsageRatio:
    """get_token_usage_ratio 测试。"""

    def test_empty_memory(self) -> None:
        config = _make_config(max_context_tokens=500_000)
        mgr = CompactionManager(config)
        memory = _make_memory(config)
        ratio = mgr.get_token_usage_ratio(memory, None)
        # 即使空 memory 也有 system prompt 的 token，但在大窗口下远低于 1.0
        assert 0.0 < ratio < 1.0

    def test_zero_max_tokens(self) -> None:
        config = _make_config(max_context_tokens=1)
        mgr = CompactionManager(config)
        memory = _make_memory(config)
        ratio = mgr.get_token_usage_ratio(memory, None)
        assert ratio > 0


# ── _format_messages_for_compaction 测试 ─────────────────


class TestFormatMessages:
    """_format_messages_for_compaction 格式化测试。"""

    def test_basic_messages(self) -> None:
        messages = [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好！有什么可以帮助你的？"},
        ]
        result = _format_messages_for_compaction(messages)
        assert "[user] 你好" in result
        assert "[assistant] 你好！" in result

    def test_tool_call_messages(self) -> None:
        messages = [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "tc_1",
                        "function": {
                            "name": "read_excel",
                            "arguments": '{"file_path": "/data/test.xlsx"}',
                        },
                    }
                ],
            },
        ]
        result = _format_messages_for_compaction(messages)
        assert "read_excel" in result
        assert "工具调用" in result

    def test_tool_result_messages(self) -> None:
        messages = [
            {
                "role": "tool",
                "tool_call_id": "tc_1",
                "content": "工具执行结果：成功读取 100 行数据",
            },
        ]
        result = _format_messages_for_compaction(messages)
        assert "[tool result:tc_1]" in result
        assert "成功读取" in result

    def test_long_content_truncation(self) -> None:
        messages = [
            {"role": "user", "content": "x" * 2000},
        ]
        result = _format_messages_for_compaction(messages, max_content_chars=100)
        assert "...[截断]" in result
        assert len(result) < 2000

    def test_total_chars_limit(self) -> None:
        messages = [
            {"role": "user", "content": f"消息 {i}" * 10}
            for i in range(1000)
        ]
        result = _format_messages_for_compaction(messages, max_total_chars=500)
        assert len(result) <= 600  # 允许一点溢出（最后一条 + 省略标记）
        assert "省略" in result

    def test_empty_messages(self) -> None:
        result = _format_messages_for_compaction([])
        assert result == ""

    def test_multimodal_content(self) -> None:
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "请看这张图片"},
                    {"type": "image_url", "image_url": {"url": "data:..."}},
                ],
            },
        ]
        result = _format_messages_for_compaction(messages)
        assert "请看这张图片" in result
        assert "[图片]" in result


# ── 多次压缩统计测试 ────────────────────────────────────


class TestMultipleCompactions:
    """测试多次压缩的统计累积。"""

    @pytest.mark.asyncio
    async def test_stats_accumulate(self) -> None:
        config = _make_config(
            max_context_tokens=10000,
            compaction_keep_recent_turns=2,
        )
        mgr = CompactionManager(config)
        client = _mock_client("摘要内容")

        for round_num in range(3):
            memory = _make_memory(config)
            for i in range(10):
                memory.add_user_message(f"轮次{round_num} 用户消息 {i}")
                memory.add_assistant_message(f"轮次{round_num} 助手回复 {i}")

            result = await mgr.auto_compact(
                memory=memory,
                system_msgs=None,
                client=client,
                summary_model="test-model",
            )
            assert result.success is True

        assert mgr.stats.compaction_count == 3


# ── 提示词质量验证 ───────────────────────────────────────


class TestPromptQuality:
    """验证压缩提示词包含必要的 ExcelManus 场景关键词。"""

    def test_system_prompt_contains_key_categories(self) -> None:
        """确保系统提示词涵盖了 ExcelManus 关键信息类别。"""
        assert "文件" in COMPACTION_SYSTEM_PROMPT
        assert "工作表" in COMPACTION_SYSTEM_PROMPT
        assert "操作" in COMPACTION_SYSTEM_PROMPT
        assert "任务" in COMPACTION_SYSTEM_PROMPT
        assert "数据" in COMPACTION_SYSTEM_PROMPT
        assert "列名" in COMPACTION_SYSTEM_PROMPT
        assert "skill" in COMPACTION_SYSTEM_PROMPT.lower()
        assert "备份" in COMPACTION_SYSTEM_PROMPT
        assert "fullaccess" in COMPACTION_SYSTEM_PROMPT.lower()
        assert "窗口" in COMPACTION_SYSTEM_PROMPT

    def test_system_prompt_contains_rules(self) -> None:
        """确保包含防止幻觉的规则。"""
        assert "不要编造" in COMPACTION_SYSTEM_PROMPT
        assert "精确" in COMPACTION_SYSTEM_PROMPT
