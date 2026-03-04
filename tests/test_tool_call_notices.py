"""工具调用通知增强测试（P1-P6）。

P1: safe_mode 渠道旁路 — 工具事件不再被 safe_mode 过滤
P2: 子代理事件处理 — subagent_start/end/tool_start/tool_end
P3: LLM 重试事件处理 — llm_retry
P4: 参数摘要增强 — args_summary 传递到策略
P5: 批量进度事件 — batch_progress
P6: 失败引导增强 — failure_guidance retryable hint
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from excelmanus.channels.base import ChannelAdapter, ChannelCapabilities
from excelmanus.channels.output_manager import (
    BatchSendStrategy,
    CardStreamStrategy,
    ChunkedOutputManager,
    EditStreamStrategy,
    _brief_tool_args,
)
from excelmanus.api_sse import sse_event_to_sse
from excelmanus.events import EventType, ToolCallEvent


# ── 辅助：Mock Adapter ──


def _make_mock_adapter(
    *,
    supports_edit: bool = False,
    supports_card_update: bool = False,
    supports_typing: bool = False,
    max_message_length: int = 4000,
    max_edits_per_minute: int = 0,
    preferred_format: str = "markdown",
    passive_reply_window: int = 0,
) -> MagicMock:
    adapter = MagicMock(spec=ChannelAdapter)
    adapter.capabilities = ChannelCapabilities(
        supports_edit=supports_edit,
        supports_card_update=supports_card_update,
        supports_typing=supports_typing,
        max_message_length=max_message_length,
        max_edits_per_minute=max_edits_per_minute,
        preferred_format=preferred_format,
        passive_reply_window=passive_reply_window,
    )
    adapter.name = "mock"
    adapter.send_text = AsyncMock()
    adapter.send_markdown = AsyncMock()
    adapter.send_markdown_return_id = AsyncMock(return_value="msg1")
    adapter.send_text_return_id = AsyncMock(return_value="msg2")
    adapter.edit_text = AsyncMock(return_value=True)
    adapter.edit_markdown = AsyncMock(return_value=True)
    adapter.show_typing = AsyncMock()
    adapter.send_card = AsyncMock(return_value="card1")
    adapter.update_card = AsyncMock(return_value=True)
    adapter.send_file = AsyncMock()
    adapter.supports_markdown_tables = False
    return adapter


def _tg_adapter() -> MagicMock:
    return _make_mock_adapter(
        supports_edit=True, supports_typing=True, max_edits_per_minute=20,
    )


def _qq_adapter() -> MagicMock:
    return _make_mock_adapter(
        preferred_format="plain", max_message_length=2000, passive_reply_window=300,
    )


def _feishu_adapter() -> MagicMock:
    return _make_mock_adapter(
        supports_card_update=True, max_message_length=4000,
    )


# ══════════════════════════════════════════════════════════════
# P1: safe_mode 渠道旁路
# ══════════════════════════════════════════════════════════════


class TestSafeModeChannelBypass:
    """P1: is_channel=True 时工具追踪事件应通过 safe_mode 过滤。"""

    def test_safe_mode_blocks_tool_call_start_for_web(self):
        """safe_mode=True, is_channel=False → tool_call_start 被过滤。"""
        event = ToolCallEvent(
            event_type=EventType.TOOL_CALL_START,
            tool_call_id="tc1",
            tool_name="read_excel",
            arguments={"path": "test.xlsx"},
            iteration=1,
        )
        result = sse_event_to_sse(event, safe_mode=True, is_channel=False)
        assert result is None

    def test_safe_mode_allows_tool_call_start_for_channel(self):
        """safe_mode=True, is_channel=True → tool_call_start 应通过。"""
        event = ToolCallEvent(
            event_type=EventType.TOOL_CALL_START,
            tool_call_id="tc1",
            tool_name="read_excel",
            arguments={"path": "test.xlsx"},
            iteration=1,
        )
        result = sse_event_to_sse(event, safe_mode=True, is_channel=True)
        assert result is not None
        assert "tool_call_start" in result
        assert "read_excel" in result

    def test_safe_mode_allows_tool_call_end_for_channel(self):
        """safe_mode=True, is_channel=True → tool_call_end 应通过。"""
        event = ToolCallEvent(
            event_type=EventType.TOOL_CALL_END,
            tool_call_id="tc1",
            tool_name="read_excel",
            success=True,
            result="OK",
            iteration=1,
        )
        result = sse_event_to_sse(event, safe_mode=True, is_channel=True)
        assert result is not None
        assert "tool_call_end" in result

    def test_safe_mode_allows_subagent_start_for_channel(self):
        """safe_mode=True, is_channel=True → subagent_start 应通过。"""
        event = ToolCallEvent(
            event_type=EventType.SUBAGENT_START,
            subagent_name="data_analysis",
            subagent_reason="分析数据",
        )
        result = sse_event_to_sse(event, safe_mode=True, is_channel=True)
        assert result is not None
        assert "subagent_start" in result

    def test_safe_mode_allows_subagent_end_for_channel(self):
        """safe_mode=True, is_channel=True → subagent_end 应通过。"""
        event = ToolCallEvent(
            event_type=EventType.SUBAGENT_END,
            subagent_name="data_analysis",
            subagent_success=True,
        )
        result = sse_event_to_sse(event, safe_mode=True, is_channel=True)
        assert result is not None
        assert "subagent_end" in result

    def test_safe_mode_allows_subagent_tool_events_for_channel(self):
        """safe_mode=True, is_channel=True → subagent_tool_start/end 应通过。"""
        for et in (EventType.SUBAGENT_TOOL_START, EventType.SUBAGENT_TOOL_END):
            event = ToolCallEvent(
                event_type=et,
                tool_name="write_excel",
                subagent_conversation_id="conv1",
            )
            result = sse_event_to_sse(event, safe_mode=True, is_channel=True)
            assert result is not None

    def test_safe_mode_allows_approval_events_for_channel(self):
        """safe_mode=True, is_channel=True → pending_approval/approval_resolved 应通过。"""
        for et in (EventType.PENDING_APPROVAL, EventType.APPROVAL_RESOLVED):
            event = ToolCallEvent(
                event_type=et,
                approval_id="ap1",
                approval_tool_name="delete_file",
                tool_call_id="tc1",
            )
            result = sse_event_to_sse(event, safe_mode=True, is_channel=True)
            assert result is not None

    def test_safe_mode_still_blocks_thinking_for_channel(self):
        """safe_mode=True, is_channel=True → thinking 仍被过滤（隐私保护）。"""
        event = ToolCallEvent(
            event_type=EventType.THINKING,
            thinking="internal thought",
            iteration=1,
        )
        result = sse_event_to_sse(event, safe_mode=True, is_channel=True)
        assert result is None

    def test_safe_mode_still_blocks_thinking_delta_for_channel(self):
        """safe_mode=True, is_channel=True → thinking_delta 仍被过滤。"""
        event = ToolCallEvent(
            event_type=EventType.THINKING_DELTA,
            thinking_delta="partial thought",
            iteration=1,
        )
        result = sse_event_to_sse(event, safe_mode=True, is_channel=True)
        assert result is None

    def test_safe_mode_still_blocks_iteration_start_for_channel(self):
        """safe_mode=True, is_channel=True → iteration_start 仍被过滤。"""
        event = ToolCallEvent(
            event_type=EventType.ITERATION_START,
            iteration=1,
        )
        result = sse_event_to_sse(event, safe_mode=True, is_channel=True)
        assert result is None

    def test_no_safe_mode_always_passes(self):
        """safe_mode=False → 所有事件通过。"""
        event = ToolCallEvent(
            event_type=EventType.TOOL_CALL_START,
            tool_call_id="tc1",
            tool_name="read_excel",
            arguments={"path": "test.xlsx"},
            iteration=1,
        )
        result = sse_event_to_sse(event, safe_mode=False, is_channel=False)
        assert result is not None


# ══════════════════════════════════════════════════════════════
# P2: 子代理事件处理
# ══════════════════════════════════════════════════════════════


class TestSubagentEventHandling:
    """P2: ChunkedOutputManager 应正确处理子代理事件。"""

    @pytest.mark.asyncio
    async def test_subagent_start_tracked(self):
        """subagent_start → 添加到 _tool_calls 并触发 on_tool_start。"""
        adapter = _tg_adapter()
        mgr = ChunkedOutputManager(adapter, "chat1")
        await mgr.feed("subagent_start", {
            "name": "data_analysis",
            "reason": "需要分析销售数据",
        })
        assert len(mgr._tool_calls) == 1
        assert mgr._tool_calls[0]["name"] == "↳ data_analysis"
        assert mgr._tool_calls[0]["status"] == "running"

    @pytest.mark.asyncio
    async def test_subagent_tool_start_tracked(self):
        """subagent_tool_start → 嵌套工具添加到 _tool_calls。"""
        adapter = _tg_adapter()
        mgr = ChunkedOutputManager(adapter, "chat1")
        await mgr.feed("subagent_tool_start", {
            "tool_name": "read_excel",
            "arguments": {"path": "sales.xlsx"},
        })
        assert len(mgr._tool_calls) == 1
        assert "↳ read_excel" in mgr._tool_calls[0]["name"]
        assert mgr._tool_calls[0]["status"] == "running"

    @pytest.mark.asyncio
    async def test_subagent_tool_end_updates_status(self):
        """subagent_tool_end → 更新嵌套工具状态。"""
        adapter = _tg_adapter()
        mgr = ChunkedOutputManager(adapter, "chat1")
        await mgr.feed("subagent_tool_start", {"tool_name": "read_excel"})
        await mgr.feed("subagent_tool_end", {
            "tool_name": "read_excel",
            "success": True,
        })
        assert mgr._tool_calls[0]["status"] == "done"

    @pytest.mark.asyncio
    async def test_subagent_tool_end_failure(self):
        """subagent_tool_end success=False → 状态为 error。"""
        adapter = _tg_adapter()
        mgr = ChunkedOutputManager(adapter, "chat1")
        await mgr.feed("subagent_tool_start", {"tool_name": "write_excel"})
        await mgr.feed("subagent_tool_end", {
            "tool_name": "write_excel",
            "success": False,
        })
        assert mgr._tool_calls[0]["status"] == "error"

    @pytest.mark.asyncio
    async def test_subagent_end_updates_status(self):
        """subagent_end → 更新子代理任务状态。label 不含 reason，能正确匹配。"""
        adapter = _tg_adapter()
        mgr = ChunkedOutputManager(adapter, "chat1")
        await mgr.feed("subagent_start", {"name": "analyzer", "reason": "test"})
        # label 应为 "↳ analyzer"，reason 仅作为 args_summary 显示
        assert mgr._tool_calls[0]["name"] == "↳ analyzer"
        await mgr.feed("subagent_end", {"name": "analyzer", "success": True})
        assert mgr._tool_calls[0]["status"] == "done"

    @pytest.mark.asyncio
    async def test_subagent_full_lifecycle(self):
        """完整子代理生命周期：start → tool_start → tool_end → end。"""
        adapter = _feishu_adapter()
        mgr = ChunkedOutputManager(adapter, "chat1")
        await mgr.feed("subagent_start", {"name": "analyzer", "reason": "分析"})
        await mgr.feed("subagent_tool_start", {"tool_name": "read_excel"})
        await mgr.feed("subagent_tool_end", {"tool_name": "read_excel", "success": True})
        await mgr.feed("subagent_end", {"name": "analyzer", "success": True})
        assert len(mgr._tool_calls) == 2  # subagent + nested tool
        assert mgr._tool_calls[0]["status"] == "done"  # subagent
        assert mgr._tool_calls[1]["status"] == "done"  # nested tool


# ══════════════════════════════════════════════════════════════
# P3: LLM 重试事件处理
# ══════════════════════════════════════════════════════════════


class TestLLMRetryHandling:
    """P3: llm_retry 事件应通过 on_progress 反馈到策略。"""

    @pytest.mark.asyncio
    async def test_llm_retry_retrying(self):
        """llm_retry status=retrying → 显示重试进度。"""
        adapter = _feishu_adapter()
        mgr = ChunkedOutputManager(adapter, "chat1")
        await mgr.feed("llm_retry", {
            "retry_status": "retrying",
            "retry_attempt": 1,
            "retry_max_attempts": 3,
            "retry_delay_seconds": 5,
        })
        # CardStreamStrategy stores progress text
        strategy = mgr._strategy
        assert isinstance(strategy, CardStreamStrategy)
        assert "重试" in strategy._progress_text

    @pytest.mark.asyncio
    async def test_llm_retry_exhausted(self):
        """llm_retry status=exhausted → 显示耗尽消息。"""
        adapter = _feishu_adapter()
        mgr = ChunkedOutputManager(adapter, "chat1")
        await mgr.feed("llm_retry", {
            "retry_status": "exhausted",
            "retry_attempt": 3,
            "retry_max_attempts": 3,
        })
        strategy = mgr._strategy
        assert isinstance(strategy, CardStreamStrategy)
        assert "耗尽" in strategy._progress_text

    @pytest.mark.asyncio
    async def test_llm_retry_unknown_status_ignored(self):
        """llm_retry 未知 status → 不触发进度更新。"""
        adapter = _feishu_adapter()
        mgr = ChunkedOutputManager(adapter, "chat1")
        await mgr.feed("llm_retry", {
            "retry_status": "unknown",
        })
        strategy = mgr._strategy
        assert isinstance(strategy, CardStreamStrategy)
        assert strategy._progress_text == ""


# ══════════════════════════════════════════════════════════════
# P4: 参数摘要增强
# ══════════════════════════════════════════════════════════════


class TestArgsSummary:
    """P4: tool_call_start 应传递 args_summary 到策略。"""

    def test_brief_tool_args_basic(self):
        """_brief_tool_args 基本格式。"""
        result = _brief_tool_args("read_excel", {"path": "test.xlsx", "sheet": "Sheet1"})
        assert 'read_excel(path="test.xlsx"' in result
        assert 'sheet="Sheet1"' in result

    def test_brief_tool_args_empty(self):
        """_brief_tool_args 无参数。"""
        assert _brief_tool_args("list_files", {}) == "list_files()"
        assert _brief_tool_args("list_files", None) == "list_files()"

    def test_brief_tool_args_code_summary(self):
        """_brief_tool_args code 参数显示行数。"""
        code = "line1\nline2\nline3"
        result = _brief_tool_args("run_python", {"code": code})
        assert "code=<3行>" in result

    def test_brief_tool_args_long_value_truncated(self):
        """_brief_tool_args 长值截断。"""
        result = _brief_tool_args("write_file", {"content": "A" * 100})
        assert "..." in result
        assert len(result) <= 160

    def test_brief_tool_args_skip_none(self):
        """_brief_tool_args 跳过 None 和空字符串。"""
        result = _brief_tool_args("tool", {"a": "val", "b": None, "c": ""})
        assert 'a="val"' in result
        assert "b=" not in result
        assert "c=" not in result

    @pytest.mark.asyncio
    async def test_tool_call_start_passes_args_summary_to_strategy(self):
        """tool_call_start → EditStreamStrategy 收到 args_summary。"""
        adapter = _tg_adapter()
        mgr = ChunkedOutputManager(adapter, "chat1")
        await mgr.feed("tool_call_start", {
            "tool_name": "read_excel",
            "arguments": {"path": "sales.xlsx"},
        })
        strategy = mgr._strategy
        assert isinstance(strategy, EditStreamStrategy)
        assert len(strategy._tool_states) == 1
        assert strategy._tool_states[0]["summary"] != ""
        assert "sales.xlsx" in strategy._tool_states[0]["summary"]

    @pytest.mark.asyncio
    async def test_tool_status_text_shows_summary_for_running(self):
        """EditStreamStrategy _build_tool_status_text 运行中工具显示摘要。"""
        adapter = _tg_adapter()
        strategy = EditStreamStrategy(adapter, "chat1")
        await strategy.on_tool_start(
            "read_excel", args_summary='read_excel(path="data.xlsx")',
        )
        text = strategy._build_tool_status_text()
        assert "data.xlsx" in text
        assert "🔄" in text

    @pytest.mark.asyncio
    async def test_tool_status_text_shows_error_for_failed(self):
        """EditStreamStrategy _build_tool_status_text 失败工具显示错误。"""
        adapter = _tg_adapter()
        strategy = EditStreamStrategy(adapter, "chat1")
        await strategy.on_tool_start("write_excel")
        await strategy.on_tool_end("write_excel", False, error="文件被锁定")
        text = strategy._build_tool_status_text()
        assert "文件被锁定" in text
        assert "❌" in text

    @pytest.mark.asyncio
    async def test_tool_status_text_hides_summary_when_done(self):
        """完成的工具仅显示名称，不显示摘要。"""
        adapter = _tg_adapter()
        strategy = EditStreamStrategy(adapter, "chat1")
        await strategy.on_tool_start(
            "read_excel", args_summary='read_excel(path="data.xlsx")',
        )
        await strategy.on_tool_end("read_excel", True)
        text = strategy._build_tool_status_text()
        assert "data.xlsx" not in text
        assert "✅" in text
        assert "read_excel" in text


# ══════════════════════════════════════════════════════════════
# P5: 批量进度事件
# ══════════════════════════════════════════════════════════════


class TestBatchProgressHandling:
    """P5: batch_progress 事件应通过 on_progress 反馈到策略。"""

    @pytest.mark.asyncio
    async def test_batch_progress_with_message(self):
        """batch_progress 有 message → 显示自定义消息。"""
        adapter = _feishu_adapter()
        mgr = ChunkedOutputManager(adapter, "chat1")
        await mgr.feed("batch_progress", {
            "batch_index": 3,
            "batch_total": 10,
            "message": "处理文件 report.xlsx",
        })
        strategy = mgr._strategy
        assert isinstance(strategy, CardStreamStrategy)
        assert "[3/10]" in strategy._progress_text
        assert "report.xlsx" in strategy._progress_text

    @pytest.mark.asyncio
    async def test_batch_progress_with_item_name(self):
        """batch_progress 有 item_name → 显示项目名。"""
        adapter = _feishu_adapter()
        mgr = ChunkedOutputManager(adapter, "chat1")
        await mgr.feed("batch_progress", {
            "batch_index": 1,
            "batch_total": 5,
            "batch_item_name": "Sheet1",
        })
        strategy = mgr._strategy
        assert isinstance(strategy, CardStreamStrategy)
        assert "Sheet1" in strategy._progress_text

    @pytest.mark.asyncio
    async def test_batch_progress_fallback(self):
        """batch_progress 无 message 和 item_name → 显示默认。"""
        adapter = _feishu_adapter()
        mgr = ChunkedOutputManager(adapter, "chat1")
        await mgr.feed("batch_progress", {
            "batch_index": 2,
            "batch_total": 8,
        })
        strategy = mgr._strategy
        assert isinstance(strategy, CardStreamStrategy)
        assert "2/8" in strategy._progress_text


# ══════════════════════════════════════════════════════════════
# P6: 失败引导增强
# ══════════════════════════════════════════════════════════════


class TestFailureGuidanceEnhanced:
    """P6: failure_guidance 应提取 retryable 信息。"""

    @pytest.mark.asyncio
    async def test_failure_guidance_retryable(self):
        """failure_guidance retryable=True → 错误附加重试提示。"""
        adapter = _tg_adapter()
        mgr = ChunkedOutputManager(adapter, "chat1")
        await mgr.feed("failure_guidance", {
            "title": "模型超时",
            "message": "请求超时，请稍后重试",
            "retryable": True,
        })
        assert mgr._error is not None
        assert "重试" in mgr._error
        assert "模型超时" in mgr._error

    @pytest.mark.asyncio
    async def test_failure_guidance_not_retryable(self):
        """failure_guidance retryable=False → 不附加重试提示。"""
        adapter = _tg_adapter()
        mgr = ChunkedOutputManager(adapter, "chat1")
        await mgr.feed("failure_guidance", {
            "title": "配额超限",
            "message": "API 配额已用完",
            "retryable": False,
        })
        assert mgr._error is not None
        assert "配额超限" in mgr._error
        assert "暂时的" not in mgr._error

    @pytest.mark.asyncio
    async def test_failure_guidance_no_title(self):
        """failure_guidance 无 title → 仅显示 message。"""
        adapter = _tg_adapter()
        mgr = ChunkedOutputManager(adapter, "chat1")
        await mgr.feed("failure_guidance", {
            "message": "未知错误",
        })
        assert mgr._error == "未知错误"


# ══════════════════════════════════════════════════════════════
# 工具错误详情传递
# ══════════════════════════════════════════════════════════════


class TestToolEndErrorDetail:
    """tool_call_end error 字段应传递到策略并影响显示。"""

    @pytest.mark.asyncio
    async def test_qq_tool_end_shows_error(self):
        """BatchSendStrategy tool_call_end 失败时显示错误详情。"""
        adapter = _qq_adapter()
        strategy = BatchSendStrategy(adapter, "chat1")
        await strategy.on_tool_start("write_excel")
        await strategy.on_tool_end("write_excel", False, error="Permission denied")
        # 检查发送的消息包含错误
        calls = adapter.send_text.call_args_list
        last_text = calls[-1][0][1]
        assert "Permission denied" in last_text
        assert "❌" in last_text

    @pytest.mark.asyncio
    async def test_qq_tool_end_success_no_error(self):
        """BatchSendStrategy tool_call_end 成功时静默（不单独发送通知）。"""
        adapter = _qq_adapter()
        strategy = BatchSendStrategy(adapter, "chat1")
        await strategy.on_tool_start("read_excel")
        call_count_before = adapter.send_text.call_count
        await strategy.on_tool_end("read_excel", True, error="")
        # 成功工具不发送独立消息
        assert adapter.send_text.call_count == call_count_before

    @pytest.mark.asyncio
    async def test_card_tool_state_stores_error(self):
        """CardStreamStrategy on_tool_end 存储错误到 tool_states。"""
        adapter = _feishu_adapter()
        strategy = CardStreamStrategy(adapter, "chat1")
        await strategy.on_tool_start("write_excel")
        await strategy.on_tool_end("write_excel", False, error="文件不存在")
        assert strategy._tool_states[0]["status"] == "error"
        assert strategy._tool_states[0].get("error") == "文件不存在"


# ══════════════════════════════════════════════════════════════
# 飞书卡片工具状态增强
# ══════════════════════════════════════════════════════════════


class TestCardToolStateDisplay:
    """CardStreamStrategy _build_card 应展示参数摘要和错误。"""

    @pytest.mark.asyncio
    async def test_card_shows_args_summary_for_running(self):
        """运行中工具在卡片中显示参数摘要。"""
        adapter = _feishu_adapter()
        strategy = CardStreamStrategy(adapter, "chat1")
        await strategy.on_tool_start(
            "read_excel", args_summary='read_excel(path="data.xlsx")',
        )
        card = strategy._build_card(final=False)
        tool_div = card["elements"][0]
        assert "data.xlsx" in tool_div["text"]["content"]

    @pytest.mark.asyncio
    async def test_card_shows_error_for_failed(self):
        """失败工具在卡片中显示错误。"""
        adapter = _feishu_adapter()
        strategy = CardStreamStrategy(adapter, "chat1")
        await strategy.on_tool_start("write_excel")
        await strategy.on_tool_end("write_excel", False, error="磁盘已满")
        card = strategy._build_card(final=False)
        tool_div = card["elements"][0]
        assert "磁盘已满" in tool_div["text"]["content"]

    @pytest.mark.asyncio
    async def test_card_hides_summary_after_done(self):
        """完成的工具卡片中仅显示名称。"""
        adapter = _feishu_adapter()
        strategy = CardStreamStrategy(adapter, "chat1")
        await strategy.on_tool_start(
            "read_excel", args_summary='read_excel(path="data.xlsx")',
        )
        await strategy.on_tool_end("read_excel", True)
        card = strategy._build_card(final=False)
        tool_div = card["elements"][0]
        # 完成后不应显示参数摘要，只显示名称
        assert "data.xlsx" not in tool_div["text"]["content"]
        assert "read_excel" in tool_div["text"]["content"]


# ══════════════════════════════════════════════════════════════
# _prepend_tool_summary 增强
# ══════════════════════════════════════════════════════════════


class TestPrependToolSummaryEnhanced:
    """_prepend_tool_summary 应展示参数摘要和错误。"""

    @pytest.mark.asyncio
    async def test_edit_prepend_shows_running_summary(self):
        """EditStreamStrategy _prepend_tool_summary 运行中工具显示摘要。"""
        adapter = _tg_adapter()
        strategy = EditStreamStrategy(adapter, "chat1")
        await strategy.on_tool_start(
            "read_excel", args_summary='read_excel(path="file.xlsx")',
        )
        text = strategy._prepend_tool_summary("回复内容")
        assert "file.xlsx" in text
        assert "回复内容" in text

    @pytest.mark.asyncio
    async def test_batch_prepend_shows_error(self):
        """BatchSendStrategy _prepend_tool_summary 失败工具显示错误。"""
        adapter = _qq_adapter()
        strategy = BatchSendStrategy(adapter, "chat1")
        await strategy.on_tool_start("write_excel")
        await strategy.on_tool_end("write_excel", False, error="权限不足")
        text = strategy._prepend_tool_summary("结果")
        assert "权限不足" in text
        assert "❌" in text


# ══════════════════════════════════════════════════════════════
# 集成测试：完整事件流
# ══════════════════════════════════════════════════════════════


class TestFullEventFlow:
    """端到端：完整事件流经 ChunkedOutputManager。"""

    @pytest.mark.asyncio
    async def test_full_tool_lifecycle_telegram(self):
        """Telegram: tool_call_start → tool_call_end → text → finalize。"""
        adapter = _tg_adapter()
        mgr = ChunkedOutputManager(adapter, "chat1")
        await mgr.feed("tool_call_start", {
            "tool_name": "read_excel",
            "arguments": {"path": "data.xlsx"},
        })
        await mgr.feed("tool_call_end", {
            "tool_name": "read_excel",
            "success": True,
        })
        await mgr.feed("text_delta", {"content": "文件已读取，共100行数据。"})
        result = await mgr.finalize()
        assert result["reply"]
        assert len(result["tool_calls"]) == 1
        assert result["tool_calls"][0]["status"] == "done"

    @pytest.mark.asyncio
    async def test_full_flow_with_subagent_and_retry(self):
        """综合流：subagent + llm_retry + batch_progress + tool_call。"""
        adapter = _feishu_adapter()
        mgr = ChunkedOutputManager(adapter, "chat1")
        # tool call
        await mgr.feed("tool_call_start", {
            "tool_name": "read_excel",
            "arguments": {"path": "input.xlsx"},
        })
        await mgr.feed("tool_call_end", {
            "tool_name": "read_excel", "success": True,
        })
        # subagent
        await mgr.feed("subagent_start", {"name": "analyzer", "reason": "分析数据"})
        await mgr.feed("subagent_tool_start", {"tool_name": "run_python"})
        await mgr.feed("subagent_tool_end", {"tool_name": "run_python", "success": True})
        await mgr.feed("subagent_end", {"name": "analyzer", "success": True})
        # LLM retry
        await mgr.feed("llm_retry", {
            "retry_status": "retrying",
            "retry_attempt": 1,
            "retry_max_attempts": 3,
            "retry_delay_seconds": 10,
        })
        # batch progress
        await mgr.feed("batch_progress", {
            "batch_index": 1, "batch_total": 3, "message": "Sheet1",
        })
        # text
        await mgr.feed("text_delta", {"content": "分析完成。"})
        result = await mgr.finalize()
        assert result["reply"] == "分析完成。"
        # tool_calls: read_excel + subagent + subagent_tool = 3
        assert len(result["tool_calls"]) == 3

    @pytest.mark.asyncio
    async def test_error_flow_with_failure_guidance(self):
        """错误流：failure_guidance retryable → 错误消息包含重试提示。"""
        adapter = _qq_adapter()
        mgr = ChunkedOutputManager(adapter, "chat1")
        await mgr.feed("failure_guidance", {
            "title": "模型错误",
            "message": "rate limit exceeded",
            "retryable": True,
        })
        result = await mgr.finalize()
        assert result["error"]
        assert "重试" in result["error"]
        # 错误应作为文本输出
        assert adapter.send_markdown.called or adapter.send_text.called
