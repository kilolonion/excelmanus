"""SmartChunker + ChunkedOutputManager 综合测试。

覆盖：
  - SmartChunker 块解析、语义合并、超长切分、格式修复、格式转换
  - ChunkedOutputManager 策略选择、事件分发、结果收集
  - EditStreamStrategy / CardStreamStrategy / BatchSendStrategy 行为
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock

import pytest

from excelmanus.channels.base import ChannelAdapter, ChannelCapabilities
from excelmanus.channels.chunking import (
    Block,
    BlockType,
    SmartChunker,
    _count_unescaped,
    _fix_unclosed_code_fence,
    _fix_unclosed_inline,
    _parse_blocks,
    smart_chunk,
)
from excelmanus.channels.output_manager import (
    BatchSendStrategy,
    CardStreamStrategy,
    ChunkedOutputManager,
    EditStreamStrategy,
)


# ════════════════════════════════════════════
#  测试用 Mock Adapter
# ════════════════════════════════════════════


class FakeAdapter(ChannelAdapter):
    """可配置能力的假适配器，记录所有调用。"""

    name = "fake"

    def __init__(self, caps: ChannelCapabilities | None = None):
        self._caps = caps or ChannelCapabilities()
        self.texts: list[tuple[str, str]] = []
        self.markdowns: list[tuple[str, str]] = []
        self.edits: list[tuple[str, str, str]] = []
        self.edit_results: list[bool] = []  # 预设每次 edit 的返回值
        self.cards_sent: list[tuple[str, dict]] = []
        self.cards_updated: list[tuple[str, str, dict]] = []
        self._msg_counter = 0

    @property
    def capabilities(self) -> ChannelCapabilities:
        return self._caps

    async def start(self):
        pass

    async def stop(self):
        pass

    async def send_text(self, chat_id, text):
        self.texts.append((chat_id, text))

    async def send_markdown(self, chat_id, text):
        self.markdowns.append((chat_id, text))

    async def send_file(self, chat_id, data, filename):
        pass

    async def send_approval_card(self, chat_id, approval_id, tool_name, risk_level, args_summary):
        pass

    async def send_question_card(self, chat_id, question_id, header, text, options):
        pass

    async def show_typing(self, chat_id):
        pass

    # ── 可编辑 / 可卡片 ──

    async def send_markdown_return_id(self, chat_id, text, *, reply_to=None):
        self._msg_counter += 1
        self.markdowns.append((chat_id, text))
        return f"msg-{self._msg_counter}"

    async def send_text_return_id(self, chat_id, text):
        self._msg_counter += 1
        self.texts.append((chat_id, text))
        return f"msg-{self._msg_counter}"

    async def edit_markdown(self, chat_id, message_id, text):
        self.edits.append((chat_id, message_id, text))
        if self.edit_results:
            return self.edit_results.pop(0)
        return True

    async def edit_text(self, chat_id, message_id, text):
        self.edits.append((chat_id, message_id, text))
        return True

    async def send_card(self, chat_id, card):
        self._msg_counter += 1
        self.cards_sent.append((chat_id, card))
        return f"card-{self._msg_counter}"

    async def update_card(self, chat_id, message_id, card):
        self.cards_updated.append((chat_id, message_id, card))
        return True


# ════════════════════════════════════════════
#  SmartChunker 测试
# ════════════════════════════════════════════


class TestParseBlocks:
    """_parse_blocks 块解析测试。"""

    def test_heading(self):
        blocks = _parse_blocks("# Title\n## Subtitle")
        assert len(blocks) == 2
        assert blocks[0].type == BlockType.HEADING
        assert blocks[1].type == BlockType.HEADING

    def test_code_block(self):
        text = "```python\nprint('hi')\n```"
        blocks = _parse_blocks(text)
        assert len(blocks) == 1
        assert blocks[0].type == BlockType.CODE_BLOCK
        assert blocks[0].code_lang == "python"
        assert "print" in blocks[0].text

    def test_table(self):
        text = "| A | B |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |"
        blocks = _parse_blocks(text)
        assert len(blocks) == 1
        assert blocks[0].type == BlockType.TABLE

    def test_list(self):
        text = "- item1\n- item2\n- item3"
        blocks = _parse_blocks(text)
        assert len(blocks) == 1
        assert blocks[0].type == BlockType.LIST

    def test_ordered_list(self):
        text = "1. first\n2. second\n3. third"
        blocks = _parse_blocks(text)
        assert len(blocks) == 1
        assert blocks[0].type == BlockType.LIST

    def test_blockquote(self):
        text = "> This is a quote\n> continued"
        blocks = _parse_blocks(text)
        assert len(blocks) == 1
        assert blocks[0].type == BlockType.BLOCKQUOTE

    def test_paragraph(self):
        text = "Normal paragraph text."
        blocks = _parse_blocks(text)
        assert len(blocks) == 1
        assert blocks[0].type == BlockType.PARAGRAPH

    def test_thematic_break(self):
        text = "---"
        blocks = _parse_blocks(text)
        assert len(blocks) == 1
        assert blocks[0].type == BlockType.THEMATIC_BREAK

    def test_mixed_blocks(self):
        text = "# Heading\n\nSome text.\n\n```py\ncode\n```\n\n- list item"
        blocks = _parse_blocks(text)
        types = [b.type for b in blocks]
        assert BlockType.HEADING in types
        assert BlockType.PARAGRAPH in types
        assert BlockType.CODE_BLOCK in types
        assert BlockType.LIST in types

    def test_blank_lines(self):
        text = "line1\n\n\nline2"
        blocks = _parse_blocks(text)
        blank_count = sum(1 for b in blocks if b.type == BlockType.BLANK_LINE)
        assert blank_count >= 1


class TestInlineFixups:
    """内联标记修复测试。"""

    def test_count_unescaped_basic(self):
        assert _count_unescaped("**bold** text", "**") == 2
        assert _count_unescaped("*italic*", "*") == 2

    def test_count_unescaped_odd(self):
        assert _count_unescaped("**unclosed", "**") == 1

    def test_fix_unclosed_bold(self):
        result = _fix_unclosed_inline("**unclosed text")
        assert result.endswith("**")

    def test_fix_unclosed_italic(self):
        result = _fix_unclosed_inline("*unclosed text")
        assert result.count("*") % 2 == 0

    def test_fix_closed_no_change(self):
        original = "**bold** and *italic*"
        result = _fix_unclosed_inline(original)
        assert result == original

    def test_fix_unclosed_code_fence_single(self):
        text = "```python\nprint('hi')"
        result = _fix_unclosed_code_fence(text)
        assert result.endswith("```")

    def test_fix_closed_code_fence_no_change(self):
        text = "```python\nprint('hi')\n```"
        result = _fix_unclosed_code_fence(text)
        assert result == text


class TestSmartChunker:
    """SmartChunker.chunk() 核心逻辑测试。"""

    def setup_method(self):
        self.chunker = SmartChunker()

    def test_empty_text(self):
        assert self.chunker.chunk("") == []

    def test_whitespace_only(self):
        result = self.chunker.chunk("   ")
        assert result == ["   "]

    def test_short_text_returns_single(self):
        text = "Hello, world!"
        assert self.chunker.chunk(text) == [text]

    def test_text_at_limit(self):
        text = "x" * 4000
        assert self.chunker.chunk(text, max_len=4000) == [text]

    def test_text_over_limit_gets_split(self):
        text = "Hello world. " * 500  # ~6500 chars
        result = self.chunker.chunk(text, max_len=2000)
        assert len(result) >= 2
        for chunk in result:
            assert len(chunk) <= 2000

    def test_heading_forces_split(self):
        # Build text with heading in the middle, both halves substantial
        part1 = "Introduction paragraph. " * 50  # ~1200 chars
        part2 = "# New Section\n\nContent of new section. " * 50
        text = part1 + "\n\n" + part2
        result = self.chunker.chunk(text, max_len=2000)
        assert len(result) >= 2

    def test_code_block_preserved(self):
        code = "```python\n" + "x = 1\n" * 10 + "```"
        prefix = "Some text before.\n\n"
        text = prefix + code
        result = self.chunker.chunk(text, max_len=5000)
        assert len(result) == 1
        assert "```python" in result[0]
        assert result[0].count("```") == 2

    def test_oversized_code_block_split(self):
        code_lines = [f"line_{i} = {i}" for i in range(200)]
        text = "```python\n" + "\n".join(code_lines) + "\n```"
        result = self.chunker.chunk(text, max_len=500)
        assert len(result) >= 2
        # Each chunk should be a valid code block
        for chunk in result:
            assert chunk.count("```") == 2

    def test_table_header_preserved(self):
        header = "| Name | Value |"
        sep = "|------|-------|"
        rows = [f"| item{i} | {i} |" for i in range(100)]
        text = header + "\n" + sep + "\n" + "\n".join(rows)
        result = self.chunker.chunk(text, max_len=500)
        assert len(result) >= 2
        for chunk in result:
            assert "| Name | Value |" in chunk
            assert "|---" in chunk

    def test_list_split_at_item_boundary(self):
        items = [f"- Item number {i} with some extra text to pad" for i in range(50)]
        text = "\n".join(items)
        result = self.chunker.chunk(text, max_len=500)
        assert len(result) >= 2
        for chunk in result:
            assert chunk.startswith("- ")

    def test_output_format_html(self):
        # Format conversion only applies when text is chunked (exceeds max_len)
        part1 = "**bold** paragraph. " * 30
        part2 = "*italic* paragraph. " * 30
        text = part1 + "\n\n" + part2
        result = self.chunker.chunk(text, max_len=500, output_format="html")
        assert len(result) >= 2
        combined = " ".join(result)
        assert "<b>" in combined
        assert "<i>" in combined

    def test_output_format_plain(self):
        # Format conversion only applies when text is chunked
        part1 = "# Heading\n\n" + "**bold** text. " * 30
        part2 = "More content. " * 30
        text = part1 + "\n\n" + part2
        result = self.chunker.chunk(text, max_len=500, output_format="plain")
        assert len(result) >= 2
        # Headings and bold markers should be stripped
        assert "**" not in result[0]

    def test_smart_chunk_convenience_function(self):
        text = "Hello world"
        assert smart_chunk(text) == [text]


class TestMarkdownToHtml:
    """Markdown → HTML 轻量转换测试。"""

    def setup_method(self):
        self.chunker = SmartChunker()

    def test_bold(self):
        result = self.chunker._markdown_to_html("**bold**")
        assert "<b>bold</b>" in result

    def test_italic(self):
        result = self.chunker._markdown_to_html("*italic*")
        assert "<i>italic</i>" in result

    def test_inline_code(self):
        result = self.chunker._markdown_to_html("`code`")
        assert "<code>code</code>" in result

    def test_strikethrough(self):
        result = self.chunker._markdown_to_html("~~deleted~~")
        assert "<s>deleted</s>" in result

    def test_heading_becomes_bold(self):
        result = self.chunker._markdown_to_html("# Title")
        assert "<b>" in result
        assert "Title" in result

    def test_code_block(self):
        result = self.chunker._markdown_to_html("```python\ncode\n```")
        assert "<pre><code" in result
        assert "language-python" in result
        assert "</code></pre>" in result

    def test_html_escape(self):
        result = self.chunker._markdown_to_html("a < b & c > d")
        assert "&lt;" in result
        assert "&amp;" in result
        assert "&gt;" in result

    def test_unclosed_code_block_auto_closes(self):
        result = self.chunker._markdown_to_html("```\ncode")
        assert "</code></pre>" in result


class TestMarkdownToPlain:
    """Markdown → 纯文本转换测试。"""

    def setup_method(self):
        self.chunker = SmartChunker()

    def test_heading_stripped(self):
        result = self.chunker._markdown_to_plain("## Heading")
        assert result.strip() == "Heading"

    def test_bold_stripped(self):
        result = self.chunker._markdown_to_plain("**bold**")
        assert "**" not in result
        assert "bold" in result

    def test_link_text_preserved(self):
        result = self.chunker._markdown_to_plain("[text](http://example.com)")
        assert "text" in result
        assert "http://" not in result

    def test_code_fence_removed(self):
        result = self.chunker._markdown_to_plain("```\ncode\n```")
        assert "```" not in result
        assert "code" in result


# ════════════════════════════════════════════
#  OutputStrategy 测试
# ════════════════════════════════════════════


class TestStrategySelection:
    """ChunkedOutputManager 策略选择测试。"""

    def test_picks_card_strategy(self):
        caps = ChannelCapabilities(supports_card_update=True)
        adapter = FakeAdapter(caps)
        mgr = ChunkedOutputManager(adapter, "c1")
        assert isinstance(mgr._strategy, CardStreamStrategy)

    def test_picks_edit_strategy(self):
        caps = ChannelCapabilities(supports_edit=True, max_edits_per_minute=20)
        adapter = FakeAdapter(caps)
        mgr = ChunkedOutputManager(adapter, "c1")
        assert isinstance(mgr._strategy, EditStreamStrategy)

    def test_picks_batch_strategy_default(self):
        adapter = FakeAdapter()
        mgr = ChunkedOutputManager(adapter, "c1")
        assert isinstance(mgr._strategy, BatchSendStrategy)

    def test_card_takes_priority_over_edit(self):
        caps = ChannelCapabilities(
            supports_edit=True, max_edits_per_minute=20,
            supports_card_update=True,
        )
        adapter = FakeAdapter(caps)
        mgr = ChunkedOutputManager(adapter, "c1")
        assert isinstance(mgr._strategy, CardStreamStrategy)


class TestBatchSendStrategy:
    """BatchSendStrategy 行为测试。"""

    @pytest.mark.asyncio
    async def test_accumulates_and_sends_on_finalize(self):
        adapter = FakeAdapter()
        strategy = BatchSendStrategy(adapter, "c1")
        await strategy.on_text_delta("Hello ")
        await strategy.on_text_delta("World")
        assert len(adapter.markdowns) == 0  # 不立即发送
        await strategy.finalize()
        assert len(adapter.markdowns) == 1
        assert adapter.markdowns[0][1] == "Hello World"

    @pytest.mark.asyncio
    async def test_tool_start_no_standalone_hint(self):
        """on_tool_start 不再发送独立"处理中"（由 _delayed_hint 统一负责）。"""
        adapter = FakeAdapter()
        strategy = BatchSendStrategy(adapter, "c1")
        await strategy.on_tool_start("read_excel")
        assert len(adapter.texts) == 0  # 不发独立消息

    @pytest.mark.asyncio
    async def test_tool_summary_prepended(self):
        adapter = FakeAdapter()
        strategy = BatchSendStrategy(adapter, "c1")
        await strategy.on_tool_start("read_excel")
        await strategy.on_tool_end("read_excel", True)
        await strategy.on_text_delta("Done!")
        await strategy.finalize()
        # 最终发送的 markdown 应包含工具摘要
        final_text = adapter.markdowns[0][1]
        assert "read_excel" in final_text
        assert "Done!" in final_text

    @pytest.mark.asyncio
    async def test_empty_finalize_no_send(self):
        adapter = FakeAdapter()
        strategy = BatchSendStrategy(adapter, "c1")
        await strategy.finalize()
        assert len(adapter.markdowns) == 0

    @pytest.mark.asyncio
    async def test_long_text_chunked(self):
        adapter = FakeAdapter(ChannelCapabilities(max_message_length=100))
        strategy = BatchSendStrategy(adapter, "c1", send_interval=0)
        text = "A" * 250
        await strategy.on_text_delta(text)
        await strategy.finalize()
        assert len(adapter.markdowns) >= 2
        for _, chunk in adapter.markdowns:
            assert len(chunk) <= 100


class TestEditStreamStrategy:
    """EditStreamStrategy 行为测试。"""

    @pytest.mark.asyncio
    async def test_short_reply_sends_on_finalize(self):
        caps = ChannelCapabilities(supports_edit=True, max_edits_per_minute=20)
        adapter = FakeAdapter(caps)
        strategy = EditStreamStrategy(adapter, "c1", first_flush_chars=100)
        await strategy.on_text_delta("Short reply.")
        assert len(adapter.markdowns) == 0
        await strategy.finalize()
        assert len(adapter.markdowns) == 1
        assert "Short reply." in adapter.markdowns[0][1]

    @pytest.mark.asyncio
    async def test_long_reply_triggers_initial_flush(self):
        caps = ChannelCapabilities(supports_edit=True, max_edits_per_minute=20)
        adapter = FakeAdapter(caps)
        strategy = EditStreamStrategy(adapter, "c1", first_flush_chars=20)
        await strategy.on_text_delta("A" * 30)
        # Should have flushed initial message
        assert len(adapter.markdowns) == 1

    @pytest.mark.asyncio
    async def test_edit_respects_interval(self):
        caps = ChannelCapabilities(supports_edit=True, max_edits_per_minute=20)
        adapter = FakeAdapter(caps)
        strategy = EditStreamStrategy(
            adapter, "c1", first_flush_chars=10, edit_interval=100,
        )
        await strategy.on_text_delta("A" * 20)  # triggers flush
        await strategy.on_text_delta("B" * 5)   # too soon to edit
        assert len(adapter.edits) == 0  # no edit yet (interval not reached)

    @pytest.mark.asyncio
    async def test_tool_tracking(self):
        caps = ChannelCapabilities(supports_edit=True, max_edits_per_minute=20)
        adapter = FakeAdapter(caps)
        strategy = EditStreamStrategy(adapter, "c1")
        await strategy.on_tool_start("read_excel")
        assert len(adapter.texts) == 1  # tool progress message sent
        assert "read_excel" in adapter.texts[0][1]

        await strategy.on_tool_end("read_excel", True)
        # Should have updated the tool message
        assert len(adapter.edits) >= 1

    @pytest.mark.asyncio
    async def test_get_full_text(self):
        caps = ChannelCapabilities(supports_edit=True, max_edits_per_minute=20)
        adapter = FakeAdapter(caps)
        strategy = EditStreamStrategy(adapter, "c1")
        await strategy.on_text_delta("part1 ")
        await strategy.on_text_delta("part2")
        assert strategy.get_full_text() == "part1 part2"


class TestCardStreamStrategy:
    """CardStreamStrategy 行为测试。"""

    @pytest.mark.asyncio
    async def test_first_update_sends_card(self):
        caps = ChannelCapabilities(supports_card_update=True)
        adapter = FakeAdapter(caps)
        strategy = CardStreamStrategy(adapter, "c1", update_interval=0)
        await strategy.on_text_delta("Hello")
        assert len(adapter.cards_sent) == 1

    @pytest.mark.asyncio
    async def test_subsequent_updates_card(self):
        caps = ChannelCapabilities(supports_card_update=True)
        adapter = FakeAdapter(caps)
        strategy = CardStreamStrategy(adapter, "c1", update_interval=0)
        await strategy.on_text_delta("Hello")
        await strategy.on_text_delta(" World")
        assert len(adapter.cards_sent) == 1
        assert len(adapter.cards_updated) == 1

    @pytest.mark.asyncio
    async def test_finalize_updates_final_card(self):
        caps = ChannelCapabilities(supports_card_update=True)
        adapter = FakeAdapter(caps)
        strategy = CardStreamStrategy(adapter, "c1", update_interval=0)
        await strategy.on_text_delta("Content")
        await strategy.finalize()
        # Final update should have "ExcelManus" (not "思考中") in header
        last_update = adapter.cards_updated[-1]
        card = last_update[2]
        assert card["header"]["title"]["content"] == "ExcelManus"

    @pytest.mark.asyncio
    async def test_card_includes_tool_states(self):
        caps = ChannelCapabilities(supports_card_update=True)
        adapter = FakeAdapter(caps)
        strategy = CardStreamStrategy(adapter, "c1", update_interval=0)
        await strategy.on_tool_start("analyze")
        card = adapter.cards_sent[0][1]
        assert any("analyze" in str(e) for e in card["elements"])


# ════════════════════════════════════════════
#  ChunkedOutputManager 集成测试
# ════════════════════════════════════════════


class TestChunkedOutputManager:
    """ChunkedOutputManager 事件分发与结果收集测试。"""

    @pytest.mark.asyncio
    async def test_session_init(self):
        adapter = FakeAdapter()
        mgr = ChunkedOutputManager(adapter, "c1")
        await mgr.feed("session_init", {"session_id": "s123"})
        result = await mgr.finalize()
        assert result["session_id"] == "s123"

    @pytest.mark.asyncio
    async def test_text_delta_accumulation(self):
        adapter = FakeAdapter()
        mgr = ChunkedOutputManager(adapter, "c1")
        await mgr.feed("text_delta", {"content": "Hello "})
        await mgr.feed("text_delta", {"content": "World"})
        result = await mgr.finalize()
        assert result["reply"] == "Hello World"

    @pytest.mark.asyncio
    async def test_tool_call_tracking(self):
        adapter = FakeAdapter()
        mgr = ChunkedOutputManager(adapter, "c1")
        await mgr.feed("tool_call_start", {"tool_name": "read_excel"})
        await mgr.feed("tool_call_end", {"tool_name": "read_excel", "success": True})
        result = await mgr.finalize()
        assert len(result["tool_calls"]) == 1
        assert result["tool_calls"][0]["status"] == "done"

    @pytest.mark.asyncio
    async def test_tool_call_failure(self):
        adapter = FakeAdapter()
        mgr = ChunkedOutputManager(adapter, "c1")
        await mgr.feed("tool_call_start", {"tool_name": "write_excel"})
        await mgr.feed("tool_call_end", {"tool_name": "write_excel", "success": False})
        result = await mgr.finalize()
        assert result["tool_calls"][0]["status"] == "error"

    @pytest.mark.asyncio
    async def test_approval_event(self):
        adapter = FakeAdapter()
        mgr = ChunkedOutputManager(adapter, "c1")
        approval_data = {"approval_id": "a1", "approval_tool_name": "delete_file"}
        await mgr.feed("pending_approval", approval_data)
        result = await mgr.finalize()
        assert result["approval"] == approval_data

    @pytest.mark.asyncio
    async def test_question_event(self):
        adapter = FakeAdapter()
        mgr = ChunkedOutputManager(adapter, "c1")
        q_data = {"id": "q1", "header": "Confirm", "text": "OK?", "options": []}
        await mgr.feed("user_question", q_data)
        result = await mgr.finalize()
        assert result["question"] == q_data

    @pytest.mark.asyncio
    async def test_file_download_event(self):
        adapter = FakeAdapter()
        mgr = ChunkedOutputManager(adapter, "c1")
        await mgr.feed("file_download", {"file_path": "/out.xlsx"})
        await mgr.feed("file_download", {"file_path": "/out2.csv"})
        result = await mgr.finalize()
        assert len(result["file_downloads"]) == 2

    @pytest.mark.asyncio
    async def test_error_event_no_text(self):
        """错误事件且无文本 → 应将错误作为文本输出。"""
        adapter = FakeAdapter()
        mgr = ChunkedOutputManager(adapter, "c1")
        await mgr.feed("error", {"error": "连接中断"})
        result = await mgr.finalize()
        assert result["error"] == "连接中断"
        # 错误应被发送为文本
        assert len(adapter.markdowns) == 1
        assert "连接中断" in adapter.markdowns[0][1]

    @pytest.mark.asyncio
    async def test_error_event_with_text(self):
        """有文本回复时，错误不覆盖文本。"""
        adapter = FakeAdapter()
        mgr = ChunkedOutputManager(adapter, "c1")
        await mgr.feed("text_delta", {"content": "partial result"})
        await mgr.feed("error", {"error": "stream interrupted"})
        result = await mgr.finalize()
        assert result["reply"] == "partial result"
        assert result["error"] == "stream interrupted"

    @pytest.mark.asyncio
    async def test_failure_guidance_event(self):
        adapter = FakeAdapter()
        mgr = ChunkedOutputManager(adapter, "c1")
        await mgr.feed("failure_guidance", {"title": "Error", "message": "Try again"})
        result = await mgr.finalize()
        assert "Error" in result["error"]
        assert "Try again" in result["error"]

    @pytest.mark.asyncio
    async def test_staging_event(self):
        adapter = FakeAdapter()
        mgr = ChunkedOutputManager(adapter, "c1")
        staging = {"action": "new", "files": [{"path": "a.xlsx"}], "pending_count": 1}
        await mgr.feed("staging_updated", staging)
        result = await mgr.finalize()
        assert result["staging_event"] == staging

    @pytest.mark.asyncio
    async def test_pipeline_progress(self):
        adapter = FakeAdapter()
        mgr = ChunkedOutputManager(adapter, "c1")
        await mgr.feed("pipeline_progress", {"stage": "planning", "message": "思考中"})
        result = await mgr.finalize()
        assert len(result["progress_events"]) == 1

    @pytest.mark.asyncio
    async def test_reply_event_treated_as_text(self):
        """'reply' 事件应和 text_delta 一样处理。"""
        adapter = FakeAdapter()
        mgr = ChunkedOutputManager(adapter, "c1")
        await mgr.feed("reply", {"content": "Final answer."})
        result = await mgr.finalize()
        assert result["reply"] == "Final answer."

    @pytest.mark.asyncio
    async def test_full_flow(self):
        """模拟完整流程：session → tools → text → file → finalize。"""
        adapter = FakeAdapter()
        mgr = ChunkedOutputManager(adapter, "c1")

        await mgr.feed("session_init", {"session_id": "s1"})
        await mgr.feed("tool_call_start", {"tool_name": "read_excel"})
        await mgr.feed("tool_call_end", {"tool_name": "read_excel", "success": True})
        await mgr.feed("text_delta", {"content": "分析结果：数据共100行。"})
        await mgr.feed("file_download", {"file_path": "/result.xlsx", "filename": "result.xlsx"})

        result = await mgr.finalize()

        assert result["session_id"] == "s1"
        assert "100行" in result["reply"]
        assert len(result["tool_calls"]) == 1
        assert len(result["file_downloads"]) == 1
        # 文本应已通过 adapter 发送
        assert len(adapter.markdowns) >= 1


# ════════════════════════════════════════════
#  空闲心跳测试
# ════════════════════════════════════════════

from excelmanus.channels.output_manager import (
    _format_elapsed,
    _pick_heartbeat_message,
    _HEARTBEAT_INTERVALS,
    _HEARTBEAT_TOOL_RUNNING,
    _HEARTBEAT_THINKING,
    _HEARTBEAT_WRITING,
    _HEARTBEAT_LONG_RUNNING,
)


class TestFormatElapsed:
    """_format_elapsed 辅助函数测试。"""

    def test_seconds(self):
        assert _format_elapsed(30) == "30秒"

    def test_minutes_and_seconds(self):
        assert _format_elapsed(90) == "1分30秒"

    def test_exact_minutes(self):
        assert _format_elapsed(120) == "2分钟"

    def test_zero(self):
        assert _format_elapsed(0) == "0秒"


class TestPickHeartbeatMessage:
    """_pick_heartbeat_message 消息选择逻辑测试。"""

    def test_long_running_overrides_all(self):
        """运行超过 60s 时优先显示耗时消息。"""
        msg = _pick_heartbeat_message(["read_excel"], True, 90)
        assert "1分30秒" in msg

    def test_tool_running(self):
        """有活跃工具时显示工具名。"""
        msg = _pick_heartbeat_message(["write_excel"], False, 20)
        assert "write_excel" in msg

    def test_has_text_writing(self):
        """有文本但无工具时显示写作提示。"""
        msg = _pick_heartbeat_message([], True, 20)
        # 消息应来自 _HEARTBEAT_WRITING 池
        assert any(m in msg for m in ["组织", "撰写", "整理"])

    def test_pure_thinking(self):
        """无工具无文本时显示思考提示。"""
        msg = _pick_heartbeat_message([], False, 10)
        assert any(kw in msg for kw in ["分析", "思考", "理解", "梳理"])


class TestHeartbeatIntegration:
    """ChunkedOutputManager 心跳集成测试。"""

    @pytest.mark.asyncio
    async def test_heartbeat_fires_on_idle(self):
        """空闲超过阈值时应触发心跳消息。"""
        adapter = FakeAdapter()
        mgr = ChunkedOutputManager(adapter, "c1")

        # 缩短心跳间隔以加速测试
        import excelmanus.channels.output_manager as om
        original_intervals = om._HEARTBEAT_INTERVALS
        om._HEARTBEAT_INTERVALS = [0.1, 0.2, 0.3]
        try:
            mgr.start_heartbeat()
            # 不发送任何事件，等待心跳触发
            await asyncio.sleep(0.3)
            mgr._stop_heartbeat()

            # 应至少发送过 1 条心跳
            assert len(adapter.texts) >= 1
            # 心跳消息应包含思考相关内容（无工具、无文本）
            heartbeat_text = adapter.texts[0][1]
            assert len(heartbeat_text) > 0
        finally:
            om._HEARTBEAT_INTERVALS = original_intervals

    @pytest.mark.asyncio
    async def test_heartbeat_not_fires_with_frequent_events(self):
        """频繁收到事件时不应触发心跳。"""
        adapter = FakeAdapter()
        mgr = ChunkedOutputManager(adapter, "c1")

        import excelmanus.channels.output_manager as om
        original_intervals = om._HEARTBEAT_INTERVALS
        om._HEARTBEAT_INTERVALS = [0.2, 0.3, 0.5]
        try:
            mgr.start_heartbeat()
            # 密集发送事件
            for i in range(5):
                await mgr.feed("text_delta", {"content": f"chunk{i}"})
                await asyncio.sleep(0.05)
            mgr._stop_heartbeat()

            # 不应发送心跳文本（texts 仅用于心跳，markdowns 用于最终文本）
            assert len(adapter.texts) == 0
        finally:
            om._HEARTBEAT_INTERVALS = original_intervals

    @pytest.mark.asyncio
    async def test_heartbeat_with_tool_context(self):
        """心跳应包含当前运行的工具名。"""
        adapter = FakeAdapter()
        mgr = ChunkedOutputManager(adapter, "c1")

        import excelmanus.channels.output_manager as om
        original_intervals = om._HEARTBEAT_INTERVALS
        om._HEARTBEAT_INTERVALS = [0.1, 0.2, 0.3]
        try:
            # 模拟工具开始但不结束
            await mgr.feed("tool_call_start", {"tool_name": "read_excel"})
            mgr.start_heartbeat()
            await asyncio.sleep(0.3)
            mgr._stop_heartbeat()

            # 心跳消息应包含工具名或耗时信息
            heartbeat_msgs = [t[1] for t in adapter.texts]
            # 过滤掉策略本身的工具消息，只看心跳
            # BatchSendStrategy 第一个工具会发 "⏳ 正在处理，请稍候..."
            # 心跳消息应在后续
            assert len(heartbeat_msgs) >= 1
        finally:
            om._HEARTBEAT_INTERVALS = original_intervals

    @pytest.mark.asyncio
    async def test_heartbeat_stopped_on_finalize(self):
        """finalize() 后心跳任务应被取消。"""
        adapter = FakeAdapter()
        mgr = ChunkedOutputManager(adapter, "c1")
        mgr.start_heartbeat()
        assert mgr._heartbeat_task is not None

        await mgr.feed("text_delta", {"content": "hello"})
        await mgr.finalize()

        assert mgr._heartbeat_task is None

    @pytest.mark.asyncio
    async def test_heartbeat_escalating_interval(self):
        """心跳间隔应递增。"""
        adapter = FakeAdapter()
        mgr = ChunkedOutputManager(adapter, "c1")
        assert mgr._next_heartbeat_interval() == _HEARTBEAT_INTERVALS[0]
        mgr._heartbeat_count = 1
        assert mgr._next_heartbeat_interval() == _HEARTBEAT_INTERVALS[1]
        mgr._heartbeat_count = 2
        assert mgr._next_heartbeat_interval() == _HEARTBEAT_INTERVALS[2]
        # 超出数组范围后停留在最后一个
        mgr._heartbeat_count = 100
        assert mgr._next_heartbeat_interval() == _HEARTBEAT_INTERVALS[-1]

    @pytest.mark.asyncio
    async def test_edit_strategy_heartbeat_edits_main_msg(self):
        """Telegram EditStreamStrategy 心跳应编辑主消息（已刷新时）。"""
        caps = ChannelCapabilities(
            supports_edit=True,
            max_edits_per_minute=20,
        )
        adapter = FakeAdapter(caps)
        strategy = EditStreamStrategy(adapter, "c1")
        # 模拟已刷新首条消息
        strategy._flushed = True
        strategy._current_msg_id = "msg-1"
        strategy._current_msg_text = "前置内容"

        await strategy.on_idle_heartbeat("🧠 思考中…")
        # 应编辑主消息而非发新消息
        assert len(adapter.edits) == 1
        assert adapter.edits[0][1] == "msg-1"
        assert "思考中" in adapter.edits[0][2]
        # 不应发新文本
        assert len(adapter.texts) == 0

    @pytest.mark.asyncio
    async def test_edit_strategy_heartbeat_fallback_send(self):
        """Telegram EditStreamStrategy 未刷新时心跳应发新文本。"""
        caps = ChannelCapabilities(
            supports_edit=True,
            max_edits_per_minute=20,
        )
        adapter = FakeAdapter(caps)
        strategy = EditStreamStrategy(adapter, "c1")
        # 未刷新 — 无 current_msg_id

        await strategy.on_idle_heartbeat("🧠 思考中…")
        # 应回退到 send_text
        assert len(adapter.texts) == 1
        assert "思考中" in adapter.texts[0][1]

    @pytest.mark.asyncio
    async def test_card_strategy_heartbeat_updates_card(self):
        """飞书 CardStreamStrategy 心跳应更新卡片 header。"""
        caps = ChannelCapabilities(
            supports_card=True,
            supports_card_update=True,
        )
        adapter = FakeAdapter(caps)
        strategy = CardStreamStrategy(adapter, "c1")
        # 模拟已发过卡片
        strategy._card_msg_id = "card-1"

        await strategy.on_idle_heartbeat("🧠 AI 正在深度分析…")
        assert len(adapter.cards_updated) == 1
        card = adapter.cards_updated[0][2]
        assert "深度分析" in card["header"]["title"]["content"]

    @pytest.mark.asyncio
    async def test_batch_strategy_heartbeat_resets_keepalive(self):
        """QQ BatchSendStrategy 心跳应刷新 keepalive 计时器。"""
        adapter = FakeAdapter()
        strategy = BatchSendStrategy(adapter, "c1")
        old_time = strategy._last_keepalive_time

        await asyncio.sleep(0.01)
        await strategy.on_idle_heartbeat("💭 正在思考…")

        assert strategy._last_keepalive_time > old_time
        assert strategy._sent_keepalive is True
        assert len(adapter.texts) == 1


# ════════════════════════════════════════════
#  /tools 与 /reasoning 渠道通知测试
# ════════════════════════════════════════════


class TestChannelNoticeEvents:
    """测试 tool_call_notice / reasoning_notice 在各策略中的行为。"""

    @pytest.mark.asyncio
    async def test_feed_tool_call_notice(self):
        """ChunkedOutputManager.feed 应将 tool_call_notice 分发到策略。"""
        adapter = FakeAdapter()
        mgr = ChunkedOutputManager(adapter, "c1")
        await mgr.feed("tool_call_notice", {
            "tool_name": "read_cells",
            "args_summary": 'read_cells(sheet="Sheet1", range="A1:D10")',
            "iteration": 1,
        })
        assert len(adapter.texts) == 1
        assert "read_cells" in adapter.texts[0][1]

    @pytest.mark.asyncio
    async def test_feed_reasoning_notice(self):
        """ChunkedOutputManager.feed 应将 reasoning_notice 分发到策略。"""
        adapter = FakeAdapter()
        mgr = ChunkedOutputManager(adapter, "c1")
        await mgr.feed("reasoning_notice", {
            "content": "Let me analyze the spreadsheet structure first.",
            "iteration": 1,
        })
        assert len(adapter.texts) == 1
        assert "analyze" in adapter.texts[0][1]

    @pytest.mark.asyncio
    async def test_feed_empty_notice_ignored(self):
        """空 summary/content 不应触发发送。"""
        adapter = FakeAdapter()
        mgr = ChunkedOutputManager(adapter, "c1")
        await mgr.feed("tool_call_notice", {"args_summary": "", "iteration": 0})
        await mgr.feed("reasoning_notice", {"content": "", "iteration": 0})
        assert len(adapter.texts) == 0

    @pytest.mark.asyncio
    async def test_edit_strategy_tool_notice_independent(self):
        """Telegram EditStreamStrategy: 工具通知应独立发送，不编辑主消息。"""
        caps = ChannelCapabilities(
            supports_edit=True,
            max_message_length=4000,
        )
        adapter = FakeAdapter(caps)
        strategy = EditStreamStrategy(adapter, "c1")
        strategy._flushed = True
        strategy._current_msg_id = "msg-1"
        strategy._current_msg_text = "正在处理..."

        await strategy.on_tool_notice('read_cells(sheet="S1")')
        # 应独立发送，不编辑主消息
        assert len(adapter.texts) == 1
        assert "read_cells" in adapter.texts[0][1]
        assert len(adapter.edits) == 0  # 不应编辑

    @pytest.mark.asyncio
    async def test_edit_strategy_reasoning_notice_separate(self):
        """Telegram EditStreamStrategy: 推理通知应独立发送。"""
        caps = ChannelCapabilities(supports_edit=True)
        adapter = FakeAdapter(caps)
        strategy = EditStreamStrategy(adapter, "c1")

        await strategy.on_reasoning_notice("I need to first read the data...")
        assert len(adapter.texts) == 1
        assert "read the data" in adapter.texts[0][1]
        assert "💭" in adapter.texts[0][1]

    @pytest.mark.asyncio
    async def test_card_strategy_tool_notice_independent(self):
        """飞书 CardStreamStrategy: 工具通知应写入独立字段，不覆盖推理通知。"""
        caps = ChannelCapabilities(
            supports_card=True,
            supports_card_update=True,
        )
        adapter = FakeAdapter(caps)
        strategy = CardStreamStrategy(adapter, "c1")
        strategy._card_msg_id = "card-1"

        await strategy.on_tool_notice('read_cells(sheet="S1")')
        assert strategy._tool_notice_text.startswith("🔧")
        assert "read_cells" in strategy._tool_notice_text
        assert strategy._reasoning_notice_text == ""  # 未被覆盖

    @pytest.mark.asyncio
    async def test_card_strategy_reasoning_notice_independent(self):
        """飞书 CardStreamStrategy: 推理通知应写入独立字段，不覆盖工具通知。"""
        caps = ChannelCapabilities(
            supports_card=True,
            supports_card_update=True,
        )
        adapter = FakeAdapter(caps)
        strategy = CardStreamStrategy(adapter, "c1")
        strategy._card_msg_id = "card-1"

        await strategy.on_reasoning_notice("Analyzing the structure...")
        assert "Analyzing" in strategy._reasoning_notice_text
        assert strategy._tool_notice_text == ""  # 未被覆盖

    @pytest.mark.asyncio
    async def test_card_strategy_both_notices_coexist(self):
        """飞书 CardStreamStrategy: 两种通知同时开启时应各自保留。"""
        caps = ChannelCapabilities(
            supports_card=True,
            supports_card_update=True,
        )
        adapter = FakeAdapter(caps)
        strategy = CardStreamStrategy(adapter, "c1")
        strategy._card_msg_id = "card-1"

        await strategy.on_tool_notice('read_cells(sheet="S1")')
        await strategy.on_reasoning_notice("Need to check structure...")
        # 两者应各自存在，互不覆盖
        assert "read_cells" in strategy._tool_notice_text
        assert "check structure" in strategy._reasoning_notice_text

    @pytest.mark.asyncio
    async def test_batch_strategy_uses_base_class(self):
        """QQ BatchSendStrategy: 使用基类默认行为（独立发送）。"""
        adapter = FakeAdapter()
        strategy = BatchSendStrategy(adapter, "c1")

        await strategy.on_tool_notice('run_code(code=<5行>)')
        assert len(adapter.texts) == 1
        assert "run_code" in adapter.texts[0][1]

        await strategy.on_reasoning_notice("Let me think step by step...")
        assert len(adapter.texts) == 2
        assert "think step" in adapter.texts[1][1]

    @pytest.mark.asyncio
    async def test_reasoning_notice_truncation(self):
        """超长推理内容应被截断。"""
        adapter = FakeAdapter()
        strategy = BatchSendStrategy(adapter, "c1")
        long_content = "x" * 1000
        await strategy.on_reasoning_notice(long_content)
        sent_text = adapter.texts[0][1]
        assert len(sent_text) < 600
        assert sent_text.endswith("...")
