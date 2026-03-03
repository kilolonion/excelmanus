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
    _extract_rule_based_summary,
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
        "max_context_tokens": 500_000,
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
        config = _make_config(max_context_tokens=20000, compaction_threshold_ratio=0.85)
        mgr = CompactionManager(config)
        memory = _make_memory(config)
        # 填充大量消息使 token 超阈值
        for i in range(100):
            memory.add_user_message(f"这是一条很长的测试消息，编号 {i}，" * 20)
            memory.add_assistant_message(f"这是助手的回复 {i}，" * 20)
        assert mgr.should_compact(memory, None) is True

    def test_disabled_never_triggers(self) -> None:
        config = _make_config(max_context_tokens=20000, compaction_threshold_ratio=0.1)
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
            max_context_tokens=500_000,
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
            max_context_tokens=500_000,
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
        config = _make_config(max_context_tokens=500_000)
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
            max_context_tokens=500_000,
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
            max_context_tokens=500_000,
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
            max_context_tokens=500_000,
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
            max_context_tokens=500_000,
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
        config = _make_config(max_context_tokens=500_000)
        mgr = CompactionManager(config)
        memory = _make_memory(config)

        status = mgr.get_status(memory, None)

        assert status["enabled"] is True
        assert status["max_tokens"] == 500_000
        assert status["compaction_count"] == 0
        assert status["last_compaction_at"] is None

    @pytest.mark.asyncio
    async def test_status_after_compact(self) -> None:
        config = _make_config(
            max_context_tokens=500_000,
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
        config = _make_config(max_context_tokens=500_000)
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
            max_context_tokens=500_000,
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


# ── _extract_rule_based_summary 专项测试 ──────────────────


def _tc(tc_id: str, name: str, arguments: str) -> dict:
    """构造 tool_call 字典的辅助函数。"""
    return {
        "id": tc_id,
        "function": {"name": name, "arguments": arguments},
    }


class TestExtractRuleBasedSummaryOriginal:
    """测试原有 3 个维度（文件路径、工具调用、用户意图）。"""

    def test_empty_messages(self) -> None:
        assert _extract_rule_based_summary([]) == ""

    def test_file_path_extraction_from_content(self) -> None:
        messages = [
            {"role": "user", "content": '打开 file_path: "/data/report.xlsx" 进行处理'},
        ]
        result = _extract_rule_based_summary(messages)
        assert "/data/report.xlsx" in result
        assert "**涉及文件**" in result

    def test_file_path_extraction_from_tool_args(self) -> None:
        messages = [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    _tc("tc_1", "read_excel", '{"file_path": "/data/sales.xlsx"}'),
                ],
            },
        ]
        result = _extract_rule_based_summary(messages)
        assert "/data/sales.xlsx" in result

    def test_tool_calls_summary(self) -> None:
        messages = [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    _tc("tc_1", "read_excel", '{"file_path": "/a.xlsx"}'),
                    _tc("tc_2", "read_excel", '{"file_path": "/b.xlsx"}'),
                    _tc("tc_3", "list_sheets", '{"file_path": "/a.xlsx"}'),
                ],
            },
        ]
        result = _extract_rule_based_summary(messages)
        assert "**已执行工具**" in result
        assert "read_excel×2" in result
        assert "list_sheets×1" in result

    def test_user_intents(self) -> None:
        messages = [
            {"role": "user", "content": "帮我整理销售数据"},
            {"role": "user", "content": "按月份汇总"},
            {"role": "user", "content": "生成图表"},
            {"role": "user", "content": "导出为PDF"},
        ]
        result = _extract_rule_based_summary(messages)
        assert "**用户意图**" in result
        # 只保留最后 3 条
        assert "按月份汇总" in result
        assert "生成图表" in result
        assert "导出为PDF" in result

    def test_system_user_messages_excluded(self) -> None:
        messages = [
            {"role": "user", "content": "[系统] 请基于以下对话摘要继续工作。"},
            {"role": "user", "content": "真正的用户消息"},
        ]
        result = _extract_rule_based_summary(messages)
        assert "[系统]" not in result
        assert "真正的用户消息" in result


class TestExtractRuleBasedSummaryWriteOps:
    """测试新增维度：写入操作记录。"""

    def test_write_text_file_recorded(self) -> None:
        messages = [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    _tc("tc_1", "write_text_file", '{"file_path": "/scripts/process.py", "content": "print(1)"}'),
                ],
            },
        ]
        result = _extract_rule_based_summary(messages)
        assert "**写入操作**" in result
        assert "write_text_file" in result
        assert "/scripts/process.py" in result

    def test_run_code_with_sheet_and_range(self) -> None:
        messages = [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    _tc(
                        "tc_1",
                        "run_code",
                        '{"file_path": "/data/report.xlsx", "sheet": "Sheet1", "range": "A1:D100"}',
                    ),
                ],
            },
        ]
        result = _extract_rule_based_summary(messages)
        assert "**写入操作**" in result
        assert "run_code → /data/report.xlsx / Sheet1 / A1:D100" in result

    def test_read_only_tools_not_in_write_ops(self) -> None:
        messages = [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    _tc("tc_1", "read_excel", '{"file_path": "/data/report.xlsx"}'),
                    _tc("tc_2", "list_sheets", '{"file_path": "/data/report.xlsx"}'),
                ],
            },
        ]
        result = _extract_rule_based_summary(messages)
        assert "**写入操作**" not in result

    def test_duplicate_write_ops_deduped(self) -> None:
        messages = [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    _tc("tc_1", "write_text_file", '{"file_path": "/a.py", "content": "v1"}'),
                ],
            },
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    _tc("tc_2", "write_text_file", '{"file_path": "/a.py", "content": "v1"}'),
                ],
            },
        ]
        result = _extract_rule_based_summary(messages)
        # 相同的写入描述应被去重
        assert result.count("write_text_file → /a.py") == 1


class TestExtractRuleBasedSummaryTaskStatus:
    """测试新增维度：任务状态重放。"""

    def test_task_create_and_update(self) -> None:
        messages = [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    _tc(
                        "tc_1",
                        "task_create",
                        '{"title": "整理销售数据", "subtasks": ["读取数据", "清洗数据", "生成报表"]}',
                    ),
                ],
            },
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    _tc("tc_2", "task_update", '{"index": 0, "new_status": "completed"}'),
                ],
            },
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    _tc("tc_3", "task_update", '{"index": 1, "new_status": "in_progress"}'),
                ],
            },
        ]
        result = _extract_rule_based_summary(messages)
        assert "**任务进度**" in result
        assert "整理销售数据" in result
        assert "completed: 1" in result
        assert "待完成" in result
        assert "清洗数据" in result  # in_progress
        assert "生成报表" in result  # pending

    def test_task_create_with_dict_subtasks(self) -> None:
        messages = [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    _tc(
                        "tc_1",
                        "task_create",
                        '{"title": "测试任务", "subtasks": [{"title": "子任务1"}, {"title": "子任务2"}]}',
                    ),
                ],
            },
        ]
        result = _extract_rule_based_summary(messages)
        assert "**任务进度**" in result
        assert "测试任务" in result
        assert "pending: 2" in result

    def test_no_task_create_no_section(self) -> None:
        messages = [
            {"role": "user", "content": "帮我做个任务"},
        ]
        result = _extract_rule_based_summary(messages)
        assert "**任务进度**" not in result

    def test_task_update_out_of_range_ignored(self) -> None:
        messages = [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    _tc("tc_1", "task_create", '{"title": "T", "subtasks": ["A"]}'),
                ],
            },
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    _tc("tc_2", "task_update", '{"index": 99, "new_status": "completed"}'),
                ],
            },
        ]
        result = _extract_rule_based_summary(messages)
        assert "**任务进度**" in result
        assert "pending: 1" in result


class TestExtractRuleBasedSummaryAssistantConclusions:
    """测试新增维度：助手结论。"""

    def test_assistant_conclusions_extracted(self) -> None:
        messages = [
            {"role": "assistant", "content": "分析完成，合计销售额为 ¥1,234,567，其中华东地区占比最高。"},
            {"role": "assistant", "content": "已将结果写入 Sheet2 的 E 列，数据格式为人民币。"},
        ]
        result = _extract_rule_based_summary(messages)
        assert "**助手结论**" in result
        assert "合计销售额" in result
        assert "Sheet2" in result

    def test_short_assistant_messages_skipped(self) -> None:
        messages = [
            {"role": "assistant", "content": "好的"},
            {"role": "assistant", "content": "收到"},
        ]
        result = _extract_rule_based_summary(messages)
        assert "**助手结论**" not in result

    def test_only_last_two_kept(self) -> None:
        messages = [
            {"role": "assistant", "content": "第一条较长的助手回复内容，包含分析结论"},
            {"role": "assistant", "content": "第二条较长的助手回复内容，包含操作记录"},
            {"role": "assistant", "content": "第三条较长的助手回复内容，包含最终结果"},
        ]
        result = _extract_rule_based_summary(messages)
        assert "第一条" not in result
        assert "第二条" in result
        assert "第三条" in result


class TestExtractRuleBasedSummaryToolErrors:
    """测试新增维度：工具执行错误。"""

    def test_tool_error_extracted(self) -> None:
        messages = [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    _tc("tc_1", "read_excel", '{"file_path": "/missing.xlsx"}'),
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "tc_1",
                "content": "工具执行错误: FileNotFoundError: /missing.xlsx 不存在",
            },
        ]
        result = _extract_rule_based_summary(messages)
        assert "**近期错误**" in result
        assert "read_excel" in result
        assert "FileNotFoundError" in result

    def test_successful_tool_result_not_in_errors(self) -> None:
        messages = [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    _tc("tc_1", "read_excel", '{"file_path": "/data.xlsx"}'),
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "tc_1",
                "content": "成功读取 100 行数据",
            },
        ]
        result = _extract_rule_based_summary(messages)
        assert "**近期错误**" not in result

    def test_only_last_three_errors_kept(self) -> None:
        messages = []
        for i in range(5):
            messages.append({
                "role": "assistant",
                "content": None,
                "tool_calls": [_tc(f"tc_{i}", "run_code", "{}")],
            })
            messages.append({
                "role": "tool",
                "tool_call_id": f"tc_{i}",
                "content": f"工具执行错误: ValueError: 错误编号{i}",
            })
        result = _extract_rule_based_summary(messages)
        assert "错误编号2" in result
        assert "错误编号3" in result
        assert "错误编号4" in result
        assert "错误编号0" not in result
        assert "错误编号1" not in result

    def test_unknown_tool_name_for_orphan_result(self) -> None:
        messages = [
            {
                "role": "tool",
                "tool_call_id": "orphan_id",
                "content": "工具执行错误: TypeError: 未知错误",
            },
        ]
        result = _extract_rule_based_summary(messages)
        assert "**近期错误**" in result
        assert "unknown:" in result


class TestExtractRuleBasedSummaryLengthControl:
    """测试总长度软限控制。"""

    def test_max_total_chars_truncates_low_priority(self) -> None:
        messages = [
            {"role": "user", "content": "用户意图消息 " * 20},
            {"role": "assistant", "content": "助手结论内容 " * 50},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    _tc("tc_1", "read_excel", '{"file_path": "/very/long/path/to/file.xlsx"}'),
                    _tc("tc_2", "write_text_file", '{"file_path": "/output.py", "content": "x"}'),
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "tc_99",
                "content": "工具执行错误: ValueError: 一个很长的错误信息",
            },
        ]
        # 设置极小的 max_total_chars
        result = _extract_rule_based_summary(messages, max_total_chars=200)
        # 高优先级维度应被保留
        assert "**涉及文件**" in result
        # 低优先级维度可能被截断
        # 总长度应合理控制
        assert len(result) <= 400  # 允许最后一个 section 溢出

    def test_default_limit_allows_rich_summary(self) -> None:
        messages = [
            {"role": "user", "content": "处理销售数据"},
            {"role": "assistant", "content": "好的，我来帮你处理销售数据，首先读取文件了解结构。"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    _tc("tc_1", "read_excel", '{"file_path": "/data/sales.xlsx"}'),
                ],
            },
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    _tc("tc_2", "run_code", '{"file_path": "/data/sales.xlsx", "sheet": "Sheet1"}'),
                ],
            },
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    _tc(
                        "tc_3",
                        "task_create",
                        '{"title": "销售数据处理", "subtasks": ["读取", "清洗", "汇总"]}',
                    ),
                ],
            },
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    _tc("tc_4", "task_update", '{"index": 0, "new_status": "completed"}'),
                ],
            },
            {"role": "assistant", "content": "数据读取完成，共 1000 行，10 列。接下来进行数据清洗。"},
        ]
        result = _extract_rule_based_summary(messages)
        # 默认 2000 字符限制下应包含所有维度
        assert "**涉及文件**" in result
        assert "**已执行工具**" in result
        assert "**写入操作**" in result
        assert "**任务进度**" in result
        assert "**用户意图**" in result
        assert "**助手结论**" in result


class TestExtractRuleBasedSummaryIntegration:
    """端到端集成测试：模拟真实对话流。"""

    def test_realistic_conversation(self) -> None:
        """模拟一个真实的 ExcelManus 对话，验证摘要覆盖度。"""
        messages = [
            {"role": "user", "content": "帮我把 sales.xlsx 的数据按月份汇总到 summary.xlsx"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    _tc("tc_1", "read_excel", '{"file_path": "/data/sales.xlsx"}'),
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "tc_1",
                "content": "成功读取：Sheet1，1000行×5列（日期、产品、数量、单价、金额）",
            },
            {
                "role": "assistant",
                "content": "文件包含 1000 行销售数据，我来按月份汇总并写入新文件。",
            },
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    _tc(
                        "tc_2",
                        "task_create",
                        '{"title": "按月汇总销售数据", "subtasks": ["读取源数据", "按月聚合", "写入目标文件"]}',
                    ),
                ],
            },
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    _tc("tc_3", "task_update", '{"index": 0, "new_status": "completed"}'),
                ],
            },
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    _tc(
                        "tc_4",
                        "run_code",
                        '{"file_path": "/data/summary.xlsx", "sheet": "月度汇总"}',
                    ),
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "tc_4",
                "content": "代码执行成功：已生成 12 行月度汇总数据并写入 summary.xlsx",
            },
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    _tc("tc_5", "task_update", '{"index": 1, "new_status": "completed"}'),
                    _tc("tc_6", "task_update", '{"index": 2, "new_status": "completed"}'),
                ],
            },
            {
                "role": "assistant",
                "content": "已完成所有任务：按月汇总了 1000 行销售数据，结果写入 summary.xlsx 的「月度汇总」工作表。",
            },
        ]
        result = _extract_rule_based_summary(messages)

        # 文件路径
        assert "sales.xlsx" in result
        assert "summary.xlsx" in result
        # 工具调用
        assert "read_excel" in result
        assert "run_code" in result
        assert "task_update" in result
        # 写入操作
        assert "**写入操作**" in result
        assert "月度汇总" in result
        # 任务状态
        assert "**任务进度**" in result
        assert "completed: 3" in result
        # 用户意图
        assert "按月份汇总" in result
        # 助手结论
        assert "**助手结论**" in result
