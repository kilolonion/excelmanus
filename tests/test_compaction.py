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

        # 只有 3 轮 user 消息，不够 keep_recent_turns=5：
        # 保留下限收缩为当前用户轮起点，此前轮次仍可压缩。
        for i in range(3):
            memory.add_user_message(f"用户消息 {i}")
            memory.add_assistant_message(f"助手回复 {i}")

        result = await mgr.auto_compact(
            memory=memory,
            system_msgs=None,
            client=client,
            summary_model="test-model",
        )

        assert result.success is True
        # 当前用户轮（第 3 轮）原文必须保留，此前轮次被摘要替换
        roles = [m.get("role") for m in memory.messages]
        assert memory.messages[-2].get("content") == "用户消息 2"
        assert memory.messages[-1].get("content") == "助手回复 2"
        assert roles[0] == "user"

    @pytest.mark.asyncio
    async def test_no_user_turns_refuses(self) -> None:
        config = _make_config(max_context_tokens=500_000)
        mgr = CompactionManager(config)
        memory = _make_memory(config)
        client = _mock_client()

        # 没有可见用户轮次（纯注入/工具历史）时仍拒绝压缩
        memory._messages.append({"role": "tool", "tool_call_id": "t1", "content": "x"})

        result = await mgr.auto_compact(
            memory=memory,
            system_msgs=None,
            client=client,
            summary_model="test-model",
        )

        assert result.success is False
        assert "不足" in result.error
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
        assert "未改写历史" in result.error
        assert len(memory._messages) == 20
        assert getattr(memory, "_compaction_generation", 0) in (0, None)
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
        assert "未改写历史" in result.error
        assert len(memory._messages) == 20
        assert getattr(memory, "_compaction_generation", 0) in (0, None)

    @pytest.mark.asyncio
    async def test_auto_compact_does_not_prune_durable_before_summary(self) -> None:
        config = _make_config(
            max_context_tokens=8000,
            compaction_threshold_ratio=0.15,
            compaction_keep_recent_turns=2,
        )
        mgr = CompactionManager(config)
        memory = _make_memory(config)
        memory.system_prompt = "sys"
        client = _mock_client("摘要")
        memory.add_user_message("用户 0")
        memory.add_assistant_message("助手 0")
        long_tool = "X" * 20000
        memory.add_tool_result("call-1", long_tool)
        for i in range(1, 4):
            memory.add_user_message(f"用户 {i}")
            memory.add_assistant_message(f"助手 {i}")

        result = await mgr.auto_compact(
            memory=memory,
            system_msgs=[{"role": "system", "content": "sys"}],
            client=client,
            summary_model="test-model",
        )

        client.chat.completions.create.assert_called_once()
        sent = client.chat.completions.create.call_args.kwargs["messages"]
        assert any(long_tool in str(m.get("content", "")) for m in sent)
        assert result.pruned_tool_results == 0


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
        call_args = client.chat.completions.create.call_args
        compact_msgs = call_args.kwargs["messages"]
        assert compact_msgs[0]["role"] == "system"
        assert compact_msgs[-1]["role"] == "user"
        assert "只保留文件操作记录" in compact_msgs[-1]["content"]
        assert "只输出文本摘要" in compact_msgs[-1]["content"]

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

    @pytest.mark.asyncio
    async def test_compaction_reuses_session_system_prefix(self) -> None:
        config = _make_config(
            max_context_tokens=500_000,
            compaction_keep_recent_turns=2,
        )
        mgr = CompactionManager(config)
        memory = _make_memory(config)
        client = _mock_client("前缀摘要")
        for i in range(8):
            memory.add_user_message(f"用户消息 {i}")
            memory.add_assistant_message(f"助手回复 {i}")

        system_msgs = [{"role": "system", "content": "你是会话助手"}]
        result = await mgr.manual_compact(
            memory=memory,
            system_msgs=system_msgs,
            client=client,
            summary_model="test-model",
        )
        assert result.success is True
        compact_msgs = client.chat.completions.create.call_args.kwargs["messages"]
        assert compact_msgs[0] == system_msgs[0]
        assert compact_msgs[-1]["role"] == "user"
        assert compact_msgs[-1]["content"].startswith("你是 ExcelManus 对话压缩助手")
        assert not any(
            part.get("type") in {"image", "image_url"}
            for msg in compact_msgs
            if isinstance(msg.get("content"), list)
            for part in msg["content"]
            if isinstance(part, dict)
        )

    @pytest.mark.asyncio
    async def test_compaction_forwards_tools(self) -> None:
        config = _make_config(
            max_context_tokens=500_000,
            compaction_keep_recent_turns=2,
        )
        mgr = CompactionManager(config)
        memory = _make_memory(config)
        client = _mock_client("工具前缀")
        for i in range(8):
            memory.add_user_message(f"用户消息 {i}")
            memory.add_assistant_message(f"助手回复 {i}")
        tools = [{"type": "function", "function": {"name": "read_excel"}}]
        result = await mgr.manual_compact(
            memory=memory,
            system_msgs=[{"role": "system", "content": "会话助手"}],
            client=client,
            summary_model="test-model",
            tools=tools,
        )
        assert result.success is True
        assert client.chat.completions.create.call_args.kwargs["tools"] == tools

    @pytest.mark.asyncio
    async def test_compaction_rejects_image_summary(self) -> None:
        config = _make_config(
            max_context_tokens=500_000,
            compaction_keep_recent_turns=2,
        )
        mgr = CompactionManager(config)
        memory = _make_memory(config)
        for i in range(8):
            memory.add_user_message(f"用户消息 {i}")
            memory.add_assistant_message(f"助手回复 {i}")

        client = AsyncMock()
        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(message=MagicMock(content=[
                {"type": "text", "text": "带图摘要"},
                {"type": "image", "attachment": {"attachmentId": "sha256:deadbeef"}},
            ])),
        ]
        client.chat.completions.create = AsyncMock(return_value=mock_response)
        result = await mgr.auto_compact(
            memory=memory,
            system_msgs=None,
            client=client,
            summary_model="test-model",
        )
        assert result.success is False
        assert "图片" in (result.error or "") or "image" in (result.error or "").lower()
        assert len(memory.messages) == 16
        assert getattr(memory, "_compaction_generation", 0) in (0, None)
        assert all(
            not (isinstance(m.get("content"), list) and any(
                isinstance(p, dict) and p.get("type") == "image"
                for p in m["content"]
            ))
            for m in memory.messages
        )


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


# ── 空摘要 streak / fallback 截断 / no-think 参数 ──────────


class TestEmptySummaryFallback:
    """连续空摘要 → 跳过 LLM 直接硬截断；thinking 禁用参数出网。"""

    @pytest.mark.asyncio
    async def test_no_think_extra_body_and_directive(self) -> None:
        config = _make_config(
            max_context_tokens=500_000,
            compaction_keep_recent_turns=2,
        )
        mgr = CompactionManager(config)
        memory = _make_memory(config)
        client = _mock_client("摘要")

        for i in range(5):
            memory.add_user_message(f"用户消息 {i}")
            memory.add_assistant_message(f"助手回复 {i}")

        await mgr.auto_compact(
            memory=memory, system_msgs=None,
            client=client, summary_model="test-model",
        )

        kwargs = client.chat.completions.create.call_args.kwargs
        extra = kwargs["extra_body"]
        assert extra["chat_template_kwargs"]["enable_thinking"] is False
        instruction = kwargs["messages"][-1]["content"]
        assert "/no_think" in instruction

    @pytest.mark.asyncio
    async def test_empty_streak_then_fallback_truncate(self) -> None:
        """streak 达标后 pre_step 跳过 LLM 摘要，直接硬截断到阈值内。"""
        from excelmanus.compaction import compact_for_pre_step
        from excelmanus.session_log import SessionEventLog

        config = _make_config(
            max_context_tokens=3000,
            compaction_threshold_ratio=0.5,
            compaction_keep_recent_turns=2,
            compaction_empty_summary_max_retries=2,
        )
        mgr = CompactionManager(config)
        memory = _make_memory(config)
        memory.attach_event_log(SessionEventLog("s1"))
        # 空摘要 client
        client = AsyncMock()
        resp = MagicMock()
        resp.choices = [MagicMock(message=MagicMock(content=""))]
        client.chat.completions.create = AsyncMock(return_value=resp)

        # 堆积超阈值历史（每条约百 token 级 content）
        memory.add_user_message("任务开始")
        for i in range(8):
            memory._messages.append({
                "role": "assistant", "content": None,
                "tool_calls": [{"id": f"tc{i}", "type": "function",
                                "function": {"name": "t", "arguments": "{}"}}],
            })
            memory._messages.append({
                "role": "tool", "tool_call_id": f"tc{i}",
                "content": "x" * 2000,
            })
        memory.add_user_message("继续任务")
        assert mgr.should_compact(memory, None)

        engine = SimpleNamespace(
            _compaction_manager=mgr, _memory=memory, _config=config,
            _client=client, _is_vision_capable=False,
        )

        # 第 1 次：LLM 摘要返回空 → streak=1，历史不变
        out = await compact_for_pre_step(engine)
        assert out == "enter"
        assert mgr._empty_streak == 1

        # 第 2 次：空摘要使 streak=2 达标 → 同一边界内立即硬截断
        before_tokens = memory._total_tokens_with_system_messages(None)
        out = await compact_for_pre_step(engine)
        assert out == "enter"
        assert client.chat.completions.create.call_count == 2  # 共 2 次摘要调用
        assert mgr._empty_streak == 0  # 截断后复位
        after_tokens = memory._total_tokens_with_system_messages(None)
        assert after_tokens < before_tokens
        assert len(memory.messages) < 18  # 头部旧消息被截掉

        kinds = [e.kind for e in memory._event_log.events]
        assert "compaction/fallback-truncate" in kinds
        assert "compaction/truncate" in kinds

    @pytest.mark.asyncio
    async def test_streak_gate_skips_llm_call(self) -> None:
        """streak 已达标时 pre_step 完全跳过 LLM 摘要调用。"""
        from excelmanus.compaction import compact_for_pre_step

        config = _make_config(
            max_context_tokens=3000,
            compaction_threshold_ratio=0.5,
            compaction_keep_recent_turns=2,
            compaction_empty_summary_max_retries=2,
        )
        mgr = CompactionManager(config)
        mgr._empty_streak = 2  # 预置达标 streak
        memory = _make_memory(config)

        memory.add_user_message("任务")
        for i in range(6):
            memory._messages.append({
                "role": "tool", "tool_call_id": f"tc{i}",
                "content": "x" * 2000,
            })
        memory.add_user_message("继续")
        assert mgr.should_compact(memory, None)

        client = AsyncMock()
        engine = SimpleNamespace(
            _compaction_manager=mgr, _memory=memory, _config=config,
            _client=client, _is_vision_capable=False,
        )
        before = len(memory.messages)
        out = await compact_for_pre_step(engine)
        assert out == "enter"
        client.chat.completions.create.assert_not_called()  # 零 LLM 调用
        assert mgr._empty_streak == 0
        assert len(memory.messages) < before

    @pytest.mark.asyncio
    async def test_empty_streak_resets_on_success(self) -> None:
        config = _make_config(
            max_context_tokens=500_000, compaction_keep_recent_turns=2,
        )
        mgr = CompactionManager(config)
        memory = _make_memory(config)

        empty_client = AsyncMock()
        resp = MagicMock()
        resp.choices = [MagicMock(message=MagicMock(content=""))]
        empty_client.chat.completions.create = AsyncMock(return_value=resp)

        for i in range(5):
            memory.add_user_message(f"用户消息 {i}")
            memory.add_assistant_message(f"助手回复 {i}")

        r1 = await mgr.auto_compact(
            memory=memory, system_msgs=None,
            client=empty_client, summary_model="m",
        )
        assert r1.success is False
        assert mgr._empty_streak == 1

        r2 = await mgr.auto_compact(
            memory=memory, system_msgs=None,
            client=_mock_client("摘要"), summary_model="m",
        )
        assert r2.success is True
        assert mgr._empty_streak == 0
