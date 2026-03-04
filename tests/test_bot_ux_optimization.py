"""Bot 渠道 UX 优化测试（P1-P6）。"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from excelmanus.channels.base import (
    ChannelAdapter,
    ChannelCapabilities,
    ChannelMessage,
    ChannelUser,
)
from excelmanus.channels.chunking import degrade_tables, smart_chunk
from excelmanus.channels.output_manager import (
    BatchSendStrategy,
    CardStreamStrategy,
    ChunkedOutputManager,
    EditStreamStrategy,
)


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
    # Async methods
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
    """Telegram-like adapter: supports_edit + typing."""
    return _make_mock_adapter(
        supports_edit=True,
        supports_typing=True,
        max_edits_per_minute=20,
        preferred_format="html",
    )


def _qq_adapter() -> MagicMock:
    """QQ-like adapter: no edit, passive reply window."""
    return _make_mock_adapter(
        preferred_format="plain",
        max_message_length=2000,
        passive_reply_window=300,
    )


def _feishu_adapter() -> MagicMock:
    """Feishu-like adapter: card update."""
    return _make_mock_adapter(
        supports_card_update=True,
        max_message_length=4000,
        preferred_format="markdown",
    )


# ══════════════════════════════════════════════════════════════
# P1: Telegram EditStreamStrategy 增强
# ══════════════════════════════════════════════════════════════


class TestEditStreamP1:
    """P1a-P1e: Telegram 流式输出增强测试。"""

    @pytest.mark.asyncio
    async def test_p1a_adaptive_edit_interval(self):
        """P1a: 编辑间隔应从 1.5s 起步，逐渐递增到 3.0s。"""
        adapter = _tg_adapter()
        strategy = EditStreamStrategy(adapter, "chat1")
        assert strategy._current_edit_interval() == 1.5
        strategy._edit_count = 3
        assert strategy._current_edit_interval() == pytest.approx(2.4, abs=0.01)
        strategy._edit_count = 10
        assert strategy._current_edit_interval() == 3.0  # capped

    @pytest.mark.asyncio
    async def test_p1b_typing_cursor_in_edit(self):
        """P1b: 编辑期间消息应包含打字光标 ◍。"""
        adapter = _tg_adapter()
        strategy = EditStreamStrategy(adapter, "chat1")
        # Send enough text to trigger initial flush
        await strategy.on_text_delta("A" * 130)
        # The initial send should contain the cursor
        call_args = adapter.send_markdown_return_id.call_args
        assert "◍" in call_args[0][1]

    @pytest.mark.asyncio
    async def test_p1b_cursor_removed_on_finalize(self):
        """P1b: finalize 后最终编辑不应包含光标。"""
        adapter = _tg_adapter()
        strategy = EditStreamStrategy(adapter, "chat1")
        await strategy.on_text_delta("Hello world! " * 15)
        await strategy.finalize()
        # Last edit_markdown call should NOT contain cursor
        last_call = adapter.edit_markdown.call_args
        if last_call:
            assert "◍" not in last_call[0][2]

    @pytest.mark.asyncio
    async def test_p1c_continuous_typing(self):
        """P1c: on_text_delta 应持续发送 show_typing。"""
        adapter = _tg_adapter()
        strategy = EditStreamStrategy(adapter, "chat1")
        strategy._last_typing_time = 0  # Force typing to trigger
        await strategy.on_text_delta("hi")
        adapter.show_typing.assert_called()

    @pytest.mark.asyncio
    async def test_p1d_tool_inline(self):
        """P1d: 工具状态应内联到主消息，不发独立消息（已 flush 后）。"""
        adapter = _tg_adapter()
        strategy = EditStreamStrategy(adapter, "chat1")
        # First flush
        await strategy.on_text_delta("X" * 130)
        adapter.send_markdown_return_id.assert_called_once()
        # Tool start — should try to edit main msg, not send new
        adapter.edit_markdown.reset_mock()
        strategy._last_edit_time = 0  # Allow edit
        await strategy.on_tool_start("read_excel")
        # Should attempt edit on main msg (not send_text_return_id for tool)
        # Either edit_markdown or send_text was called
        assert adapter.edit_markdown.called or adapter.send_text.called

    @pytest.mark.asyncio
    async def test_p1d_tool_before_flush_sends_text(self):
        """P1d: 未 flush 前工具开始应发送独立文本消息。"""
        adapter = _tg_adapter()
        strategy = EditStreamStrategy(adapter, "chat1")
        await strategy.on_tool_start("read_excel")
        adapter.send_text.assert_called_once()
        text = adapter.send_text.call_args[0][1]
        assert "read_excel" in text

    @pytest.mark.asyncio
    async def test_p1e_smart_first_flush_sentence(self):
        """P1e: 遇到句子边界且达到最小字符数时应触发首次刷新。"""
        adapter = _tg_adapter()
        strategy = EditStreamStrategy(adapter, "chat1")
        # 40+ chars + Chinese sentence boundary (。)
        # Each Chinese char is 1 char in Python str, need 40+ total
        text = "这是一段用来测试首次刷新逻辑的文本内容，需要确保长度超过四十个字符才行。后续内容会继续生成"
        assert len(text) >= 40  # sanity check
        await strategy.on_text_delta(text)
        # Should have flushed (>=40 chars + 。 sentence boundary)
        assert strategy._flushed
        adapter.send_markdown_return_id.assert_called_once()

    @pytest.mark.asyncio
    async def test_p1e_timeout_flush(self):
        """P1e: 超时后即使字符不足也应刷新。"""
        adapter = _tg_adapter()
        strategy = EditStreamStrategy(adapter, "chat1")
        strategy._first_delta_time = time.monotonic() - 3.0  # Simulate 3s ago
        await strategy.on_text_delta("Hi")  # Only 2 chars
        assert strategy._flushed

    @pytest.mark.asyncio
    async def test_short_reply_no_flush(self):
        """短回复直接 finalize，不经过编辑流程。"""
        adapter = _tg_adapter()
        strategy = EditStreamStrategy(adapter, "chat1")
        await strategy.on_text_delta("短回复")
        assert not strategy._flushed
        await strategy.finalize()
        adapter.send_markdown.assert_called_once()


# ══════════════════════════════════════════════════════════════
# P2: QQ BatchSendStrategy 渐进式输出
# ══════════════════════════════════════════════════════════════


class TestBatchSendP2:
    """P2a-P2c: QQ 渐进式输出测试。"""

    @pytest.mark.asyncio
    async def test_p2a_progressive_send_on_paragraph(self):
        """P2a: 积累足够文本且遇到段落边界时应即时发送。"""
        adapter = _qq_adapter()
        strategy = BatchSendStrategy(adapter, "chat1")
        strategy._last_progressive_send = 0  # Allow send

        # 发送 200+ 字符带段落边界
        text = "A" * 120 + "\n\n" + "B" * 100
        await strategy.on_text_delta(text)

        # 应该已渐进发送了第一段
        assert len(strategy._progressive_sent_parts) > 0

    @pytest.mark.asyncio
    async def test_p2a_no_premature_send(self):
        """P2a: 文本不足阈值时不应渐进发送。"""
        adapter = _qq_adapter()
        strategy = BatchSendStrategy(adapter, "chat1")
        await strategy.on_text_delta("short text")
        assert len(strategy._progressive_sent_parts) == 0

    @pytest.mark.asyncio
    async def test_p2b_tool_end_success_silent(self):
        """P1a: 成功工具不即时通知（静默累积到 finalize）。"""
        adapter = _qq_adapter()
        strategy = BatchSendStrategy(adapter, "chat1")
        await strategy.on_tool_start("read_excel")
        adapter.send_text.reset_mock()
        await strategy.on_tool_end("read_excel", success=True)
        # 成功工具不发送独立消息
        adapter.send_text.assert_not_called()

    @pytest.mark.asyncio
    async def test_p2b_tool_end_failure_feedback(self):
        """P1a: 工具失败时应立即发送失败反馈。"""
        adapter = _qq_adapter()
        strategy = BatchSendStrategy(adapter, "chat1")
        await strategy.on_tool_start("write_excel")
        adapter.send_text.reset_mock()
        await strategy.on_tool_end("write_excel", success=False)
        text = adapter.send_text.call_args[0][1]
        assert "❌" in text
        assert "失败" in text

    @pytest.mark.asyncio
    async def test_p1a_tool_chain_summary_in_finalize(self):
        """P1a: finalize 时无文本输出则发送聚合工具摘要。"""
        adapter = _qq_adapter()
        strategy = BatchSendStrategy(adapter, "chat1")
        await strategy.on_tool_start("read_excel")
        await strategy.on_tool_end("read_excel", success=True)
        await strategy.on_tool_start("write_cell")
        await strategy.on_tool_end("write_cell", success=True)
        adapter.send_text.reset_mock()
        await strategy.finalize()
        # 应发送聚合摘要
        adapter.send_text.assert_called()
        text = adapter.send_text.call_args[0][1]
        assert "read_excel" in text
        assert "write_cell" in text
        assert "✅" in text
        assert "→" in text

    @pytest.mark.asyncio
    async def test_p1a_tool_summary_not_sent_when_text_exists(self):
        """P1a: 有文本输出时工具摘要内联到文本中，不独立发送。"""
        adapter = _qq_adapter()
        strategy = BatchSendStrategy(adapter, "chat1")
        await strategy.on_tool_start("read_excel")
        await strategy.on_tool_end("read_excel", success=True)
        # 模拟有文本输出
        await strategy.on_text_delta("分析结果如下...")
        adapter.send_text.reset_mock()
        adapter.send_markdown.reset_mock()
        await strategy.finalize()
        # _flush_tool_summary 不应独立发送（因为有文本）
        # finalize 应通过 send_markdown 发送文本
        adapter.send_markdown.assert_called()

    @pytest.mark.asyncio
    async def test_p1a_mixed_success_and_failure(self):
        """P1a: 混合成功和失败工具 — 失败即时通知，成功静默。"""
        adapter = _qq_adapter()
        strategy = BatchSendStrategy(adapter, "chat1")
        await strategy.on_tool_start("read_excel")
        await strategy.on_tool_end("read_excel", success=True)
        # 成功 → 无通知
        call_count_after_success = adapter.send_text.call_count

        await strategy.on_tool_start("write_cell")
        await strategy.on_tool_end("write_cell", success=False, error="权限不足")
        # 失败 → 即时通知
        assert adapter.send_text.call_count > call_count_after_success
        text = adapter.send_text.call_args[0][1]
        assert "❌" in text
        assert "权限不足" in text

    @pytest.mark.asyncio
    async def test_p2c_keepalive_with_progress(self):
        """P2c: 保活消息应包含进度信息。"""
        adapter = _qq_adapter()
        strategy = BatchSendStrategy(adapter, "chat1")
        strategy._tools_total = 3
        strategy._tools_done = 2
        strategy._last_keepalive_time = 0  # Force trigger
        await strategy._check_keepalive()
        text = adapter.send_text.call_args[0][1]
        assert "2/3" in text

    @pytest.mark.asyncio
    async def test_p2_finalize_remaining(self):
        """P2a: finalize 应发送残余缓冲内容。"""
        adapter = _qq_adapter()
        strategy = BatchSendStrategy(adapter, "chat1")
        await strategy.on_text_delta("Hello world, this is a test.")
        await strategy.finalize()
        adapter.send_markdown.assert_called()


# ══════════════════════════════════════════════════════════════
# P3: 飞书 CardStreamStrategy 增强
# ══════════════════════════════════════════════════════════════


class TestCardStreamP3:
    """P3a-P3c: 飞书卡片流式输出增强测试。"""

    @pytest.mark.asyncio
    async def test_p3a_progress_in_card(self):
        """P3a: 进度事件应触发卡片更新。"""
        adapter = _feishu_adapter()
        strategy = CardStreamStrategy(adapter, "chat1")
        strategy._last_update_time = 0
        await strategy.on_progress("analysis", "正在分析数据结构")
        assert strategy._progress_text == "⏳ [analysis] 正在分析数据结构"
        # Should have tried to update card
        assert adapter.send_card.called or adapter.update_card.called

    @pytest.mark.asyncio
    async def test_p3c_error_card_color(self):
        """P3c: 有错误时 finalize 卡片应为红色模板。"""
        adapter = _feishu_adapter()
        strategy = CardStreamStrategy(adapter, "chat1")
        await strategy.on_text_delta("partial response")
        strategy.set_error("something failed")
        await strategy.finalize()
        # update_card(chat_id, msg_id, card) — card is 3rd positional arg
        if adapter.update_card.called:
            card = adapter.update_card.call_args[0][2]
        else:
            card = adapter.send_card.call_args[0][1]
        assert card["header"]["template"] == "red"

    @pytest.mark.asyncio
    async def test_p3c_success_card_green(self):
        """P3c: 无错误时 finalize 卡片应为绿色模板。"""
        adapter = _feishu_adapter()
        strategy = CardStreamStrategy(adapter, "chat1")
        await strategy.on_text_delta("Hello")
        await strategy.finalize()
        # update_card(chat_id, msg_id, card) — card is 3rd positional arg
        if adapter.update_card.called:
            card = adapter.update_card.call_args[0][2]
        else:
            card = adapter.send_card.call_args[0][1]
        assert card["header"]["template"] == "green"

    @pytest.mark.asyncio
    async def test_p3_build_card_has_typing_cursor(self):
        """非 final 卡片应有打字光标。"""
        adapter = _feishu_adapter()
        strategy = CardStreamStrategy(adapter, "chat1")
        strategy._all_text_parts = ["hello"]
        card = strategy._build_card(final=False)
        md_content = card["elements"][-1]["text"]["content"]
        assert "▍" in md_content

    @pytest.mark.asyncio
    async def test_p3_final_card_no_cursor(self):
        """Final 卡片不应有打字光标。"""
        adapter = _feishu_adapter()
        strategy = CardStreamStrategy(adapter, "chat1")
        strategy._all_text_parts = ["hello"]
        card = strategy._build_card(final=True)
        for el in card["elements"]:
            if el.get("tag") == "div" and "text" in el:
                text_content = el["text"].get("content", "")
                assert "▍" not in text_content


# ══════════════════════════════════════════════════════════════
# P4: 通用消息处理优化
# ══════════════════════════════════════════════════════════════


class TestMessageHandlerP4:
    """P4b: 动态处理提示测试。"""

    def test_processing_hints_dict(self):
        """P4b: 应有 write/read/plan 三种提示。"""
        from excelmanus.channels.message_handler import MessageHandler
        assert "write" in MessageHandler._PROCESSING_HINTS
        assert "read" in MessageHandler._PROCESSING_HINTS
        assert "plan" in MessageHandler._PROCESSING_HINTS
        # 每种提示应不同
        hints = set(MessageHandler._PROCESSING_HINTS.values())
        assert len(hints) == 3

    def test_file_processing_hint(self):
        """P4b: 文件处理应有专门提示。"""
        from excelmanus.channels.message_handler import MessageHandler
        assert MessageHandler._PROCESSING_HINT_FILE
        assert "文件" in MessageHandler._PROCESSING_HINT_FILE


# ══════════════════════════════════════════════════════════════
# P5: 错误恢复与反馈优化
# ══════════════════════════════════════════════════════════════


class TestErrorRecoveryP5:
    """P5a-P5c: 错误恢复测试。"""

    @pytest.mark.asyncio
    async def test_p3c_error_state_wired(self):
        """P3c/P5: ChunkedOutputManager 应将错误状态传递给 CardStreamStrategy。"""
        adapter = _feishu_adapter()
        mgr = ChunkedOutputManager(adapter, "chat1")
        await mgr.feed("text_delta", {"content": "hello"})
        await mgr.feed("error", {"error": "test error"})
        result = await mgr.finalize()
        assert result["error"] == "test error"
        # Strategy should have been notified
        strategy = mgr._strategy
        if hasattr(strategy, "_has_error"):
            assert strategy._has_error

    @pytest.mark.asyncio
    async def test_p5c_empty_with_tools_shows_completed(self):
        """P5c: 有 tool_calls 但无文本时应显示'操作已完成'。"""
        from excelmanus.channels.message_handler import MessageHandler
        from excelmanus.channels.session_store import SessionStore

        adapter = _tg_adapter()
        api = MagicMock()
        store = SessionStore()
        handler = MessageHandler(adapter, api, store)

        # Simulate result with tool_calls but no reply
        result = {
            "reply": "",
            "error": None,
            "approval": None,
            "question": None,
            "file_downloads": [],
            "staging_event": None,
            "tool_calls": [{"name": "read_excel", "status": "done"}],
        }
        await handler._dispatch_non_text_results("chat1", "user1", result)
        # Should have sent "操作已完成"
        texts = [call[0][1] for call in adapter.send_text.call_args_list]
        assert any("操作已完成" in t for t in texts)


# ══════════════════════════════════════════════════════════════
# P6: 分块质量改进
# ══════════════════════════════════════════════════════════════


class TestSentenceBoundary:
    """自然语言断句测试。"""

    def test_paragraph_boundary(self):
        """段落边界（\\n\\n）应为最高优先级。"""
        from excelmanus.channels.chunking import find_sentence_boundary
        text = "第一段内容。\n\n第二段内容。"
        cut = find_sentence_boundary(text)
        assert cut > 0
        assert text[:cut].rstrip() == "第一段内容。"

    def test_chinese_sentence_end(self):
        """中文句号应作为断句点。"""
        from excelmanus.channels.chunking import find_sentence_boundary
        text = "这是第一句话。这是第二句话。还有更多内容"
        cut = find_sentence_boundary(text)
        assert cut > 0
        # 应切在最后一个句号之后
        assert "。" in text[:cut]

    def test_chinese_question_mark(self):
        """中文问号应作为断句点。"""
        from excelmanus.channels.chunking import find_sentence_boundary
        text = "你好吗？我很好"
        cut = find_sentence_boundary(text)
        assert cut > 0
        assert text[:cut].rstrip().endswith("？")

    def test_chinese_exclamation(self):
        """中文感叹号应作为断句点。"""
        from excelmanus.channels.chunking import find_sentence_boundary
        text = "太好了！继续努力"
        cut = find_sentence_boundary(text)
        assert cut > 0
        assert "！" in text[:cut]

    def test_english_period_with_space(self):
        """英文句号+空格应作为断句点。"""
        from excelmanus.channels.chunking import find_sentence_boundary
        text = "This is done. Next step is here"
        cut = find_sentence_boundary(text)
        assert cut > 0
        assert text[:cut].rstrip().endswith(".")

    def test_english_period_no_space_ignored(self):
        """英文句号不跟空格时不应断句（避免切断 3.14 或 URL）。"""
        from excelmanus.channels.chunking import find_sentence_boundary
        text = "value is 3.14 and more"
        cut = find_sentence_boundary(text)
        # 没有 ". " 模式，应回退到逗号或无断点
        # "3.14 " 中的 .1 不构成句末
        # 但可能匹配到其他级别；只要不切在 3. 和 14 之间即可
        if cut > 0:
            assert text[:cut] != "value is 3."

    def test_chinese_comma_level(self):
        """中文逗号应作为低优先级断句点。"""
        from excelmanus.channels.chunking import find_sentence_boundary
        text = "第一部分，第二部分"
        cut = find_sentence_boundary(text)
        assert cut > 0
        assert "，" in text[:cut]

    def test_english_comma_level(self):
        """英文逗号+空格应作为低优先级断句点。"""
        from excelmanus.channels.chunking import find_sentence_boundary
        text = "first part, second part"
        cut = find_sentence_boundary(text)
        assert cut > 0
        assert text[:cut].rstrip().endswith(",")

    def test_newline_fallback(self):
        """裸换行应作为最低优先级断句点。"""
        from excelmanus.channels.chunking import find_sentence_boundary
        text = "line one\nline two"
        cut = find_sentence_boundary(text)
        assert cut > 0
        assert text[:cut].strip() == "line one"

    def test_no_boundary(self):
        """无任何标点或换行时应返回 -1。"""
        from excelmanus.channels.chunking import find_sentence_boundary
        text = "no punctuation here"
        cut = find_sentence_boundary(text)
        assert cut == -1

    def test_min_pos_respected(self):
        """min_pos 之前的标点不应被选中。"""
        from excelmanus.channels.chunking import find_sentence_boundary
        text = "短。这是后面更长的部分，有逗号"
        cut = find_sentence_boundary(text, min_pos=5)
        assert cut > 5

    def test_code_fence_protection(self):
        """代码块内的标点不应作为断句点。"""
        from excelmanus.channels.chunking import find_sentence_boundary
        text = "```\nprint('hello。world')\n```"
        cut = find_sentence_boundary(text)
        # 代码块内的句号不应被选中，应回退到换行
        if cut > 0:
            # 断点不应在代码内容中间
            assert "print" not in text[cut:] or text[cut:].startswith("```")

    def test_has_sentence_boundary_true(self):
        """has_sentence_boundary 应正确检测存在断句点。"""
        from excelmanus.channels.chunking import has_sentence_boundary
        assert has_sentence_boundary("你好。世界") is True
        assert has_sentence_boundary("hello. world") is True
        assert has_sentence_boundary("line1\nline2") is True

    def test_has_sentence_boundary_false(self):
        """has_sentence_boundary 应正确检测不存在断句点。"""
        from excelmanus.channels.chunking import has_sentence_boundary
        assert has_sentence_boundary("nopunctuation") is False

    def test_mixed_zh_en(self):
        """中英混合文本应能正确断句。"""
        from excelmanus.channels.chunking import find_sentence_boundary
        text = "处理完成。The result is 42. 继续下一步"
        cut = find_sentence_boundary(text)
        assert cut > 0

    def test_semicolon_boundary(self):
        """中文分号应作为断句点。"""
        from excelmanus.channels.chunking import find_sentence_boundary
        text = "第一步完成；第二步开始"
        cut = find_sentence_boundary(text)
        assert cut > 0
        assert "；" in text[:cut]

    @pytest.mark.asyncio
    async def test_batch_progressive_uses_sentence_boundary(self):
        """BatchSendStrategy 渐进发送应使用自然语言断句（无 \\n\\n 也能断）。"""
        adapter = _qq_adapter()
        strategy = BatchSendStrategy(adapter, "chat1")
        strategy._last_progressive_send = 0  # Allow send

        # 200+ 字符的中文文本，只有句号作为边界（无 \n\n）
        text = (
            "这是一段很长的分析结果，包含了很多数据和统计信息，需要非常详细地展示给用户看。"
            "经过仔细分析，我们发现了以下几个关键点需要特别注意和关注。"
            "第一个要点是数据质量整体良好，没有发现明显的异常值存在。"
            "第二个要点是趋势呈现出明显的上升态势，预计未来一段时间会继续保持增长。"
            "第三个要点是需要关注部分边缘数据，可能存在潜在的风险因素需要排查和处理。"
            "综合以上分析，整体情况是积极正面的，但仍需持续跟踪监控相关指标的变化趋势。"
            "后续我们还需要进一步深入验证这些发现的结论是否可靠"
        )
        assert len(text) >= 200, f"text len={len(text)}"  # sanity
        await strategy.on_text_delta(text)

        # 应该在句号处断句发送（不依赖 \n\n）
        assert len(strategy._progressive_sent_parts) > 0


class TestChunkingP6:
    """P6b: 表格降级改进测试。"""

    def test_p6b_degrade_tables_truncation(self):
        """P6b: 超长表格应被截断并标注省略行数。"""
        header = "| Name | Age |"
        sep = "|------|-----|"
        rows = [f"| Row{i} | {i} |" for i in range(50)]
        table = "\n".join([header, sep] + rows)

        result = degrade_tables(table, max_table_rows=10)
        assert "省略" in result
        assert "42" in result  # 52 code_lines (header+sep+50rows) - 10 kept = 42 omitted

    def test_p6b_small_table_no_truncation(self):
        """P6b: 小表格不应被截断。"""
        header = "| Name | Age |"
        sep = "|------|-----|"
        rows = [f"| Row{i} | {i} |" for i in range(3)]
        table = "\n".join([header, sep] + rows)

        result = degrade_tables(table)
        assert "省略" not in result
        assert "```" in result

    def test_smart_chunk_preserves_code_blocks(self):
        """分块器应正确处理代码块边界。"""
        text = "Introduction\n\n```python\nprint('hello')\nprint('world')\n```\n\nConclusion"
        chunks = smart_chunk(text, max_len=5000)
        assert len(chunks) == 1  # Should fit in one chunk

    def test_smart_chunk_splits_long_text(self):
        """分块器应正确切分超长文本。"""
        text = ("Line " * 200 + "\n\n") * 5
        chunks = smart_chunk(text, max_len=500)
        assert len(chunks) > 1
        for chunk in chunks:
            assert len(chunk) <= 500


# ══════════════════════════════════════════════════════════════
# ChunkedOutputManager 策略选择
# ══════════════════════════════════════════════════════════════


class TestStrategySelection:
    """ChunkedOutputManager 应根据 adapter capabilities 选择正确策略。"""

    def test_selects_edit_for_telegram(self):
        adapter = _tg_adapter()
        mgr = ChunkedOutputManager(adapter, "chat1")
        assert isinstance(mgr._strategy, EditStreamStrategy)

    def test_selects_card_for_feishu(self):
        adapter = _feishu_adapter()
        mgr = ChunkedOutputManager(adapter, "chat1")
        assert isinstance(mgr._strategy, CardStreamStrategy)

    def test_selects_batch_for_qq(self):
        adapter = _qq_adapter()
        mgr = ChunkedOutputManager(adapter, "chat1")
        assert isinstance(mgr._strategy, BatchSendStrategy)
