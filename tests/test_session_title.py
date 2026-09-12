"""Tests for session title generation."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from excelmanus.session_title import (
    clip_title,
    generate_session_title,
    instant_session_title,
)


@pytest.mark.asyncio
async def test_generate_title_returns_short_title():
    """激活模型正常返回时，应得到 stripped 标题。"""
    mock_client = AsyncMock()
    mock_choice = MagicMock()
    mock_choice.message.content = "  表格排序汇总  "
    mock_client.chat.completions.create.return_value = MagicMock(choices=[mock_choice])

    result = await generate_session_title(
        user_message="帮我把这个表格的第三列数据按照日期排序",
        assistant_reply="好的，我来帮你排序第三列数据。",
        client=mock_client,
        model="gpt-4o-mini",
    )
    assert result == "表格排序汇总"


@pytest.mark.asyncio
async def test_generate_title_returns_none_on_empty():
    """模型返回空内容时，应返回 None。"""
    mock_client = AsyncMock()
    mock_choice = MagicMock()
    mock_choice.message.content = "   "
    mock_client.chat.completions.create.return_value = MagicMock(choices=[mock_choice])

    result = await generate_session_title(
        user_message="你好",
        assistant_reply="你好！",
        client=mock_client,
        model="gpt-4o-mini",
    )
    assert result is None


@pytest.mark.asyncio
async def test_generate_title_returns_none_on_exception():
    """LLM 调用异常时，应返回 None 而非抛出异常。"""
    mock_client = AsyncMock()
    mock_client.chat.completions.create.side_effect = Exception("API error")

    result = await generate_session_title(
        user_message="帮我分析数据",
        assistant_reply="好的",
        client=mock_client,
        model="gpt-4o-mini",
    )
    assert result is None


@pytest.mark.asyncio
async def test_generate_title_keeps_full_title_by_default():
    """未指定 max_length 时，保留模型返回的完整标题。"""
    mock_client = AsyncMock()
    mock_choice = MagicMock()
    long_title = "这是一个非常非常长的标题超过了我们设定的最大长度限制"
    mock_choice.message.content = long_title
    mock_client.chat.completions.create.return_value = MagicMock(choices=[mock_choice])

    result = await generate_session_title(
        user_message="帮我做很多事情",
        assistant_reply="好的",
        client=mock_client,
        model="gpt-4o-mini",
    )
    assert result == long_title


@pytest.mark.asyncio
async def test_generate_title_truncates_long_title_with_ellipsis():
    """显式传入 max_length 时，截断并在末尾加省略号。"""
    mock_client = AsyncMock()
    mock_choice = MagicMock()
    mock_choice.message.content = "这是一个非常非常长的标题超过了我们设定的最大长度限制"
    mock_client.chat.completions.create.return_value = MagicMock(choices=[mock_choice])

    result = await generate_session_title(
        user_message="帮我做很多事情",
        assistant_reply="好的",
        client=mock_client,
        model="gpt-4o-mini",
        max_length=15,
    )
    assert result is not None
    assert result.endswith("…")
    assert len(result) == 15


@pytest.mark.asyncio
async def test_generate_title_strips_quotes():
    """LLM 返回带引号的标题时，应去除引号。"""
    mock_client = AsyncMock()
    mock_choice = MagicMock()
    mock_choice.message.content = '"销售数据分析"'
    mock_client.chat.completions.create.return_value = MagicMock(choices=[mock_choice])

    result = await generate_session_title(
        user_message="帮我分析销售数据",
        assistant_reply="好的，我来分析。",
        client=mock_client,
        model="gpt-4o-mini",
    )
    assert result == "销售数据分析"


@pytest.mark.asyncio
async def test_generate_title_returns_none_on_none_content():
    """LLM 返回 None content 时，应返回 None。"""
    mock_client = AsyncMock()
    mock_choice = MagicMock()
    mock_choice.message.content = None
    mock_client.chat.completions.create.return_value = MagicMock(choices=[mock_choice])

    result = await generate_session_title(
        user_message="你好",
        assistant_reply="你好！",
        client=mock_client,
        model="gpt-4o-mini",
    )
    assert result is None


def test_clip_title_adds_ellipsis():
    assert clip_title("识别截图中的表格，还原数据", None) == "识别截图中的表格，还原数据"
    assert clip_title("识别截图中的表格，还原数据", 12) == "识别截图中的表格，还原…"
    assert clip_title("短", 12) == "短"


def test_instant_session_title_keeps_full_first_line():
    title = instant_session_title("帮我把这份很长的销售报表按月份汇总并写回原表")
    assert title == "帮我把这份很长的销售报表按月份汇总并写回原表"


def test_instant_session_title_strips_upload_prefix():
    title = instant_session_title(
        "[已上传文件: ./uploads/sales.xlsx]\n\n识别截图中的表格，还原数据"
    )
    assert title == "识别截图中的表格，还原数据"


def test_instant_session_title_keeps_upload_notice_when_no_caption():
    title = instant_session_title("[已上传图片: ./uploads/receipt.png]")
    assert title == "[已上传图片: ./uploads/receipt.png]"


def test_title_from_messages_skips_injected_skill_catalog():
    from excelmanus.session_title import title_from_messages

    messages = [
        {"role": "user", "content": "<available_skills>\n- `chart_basic`: 图表\n</available_skills>"},
        {"role": "user", "content": "识别截图中的表格，还原数据"},
    ]
    assert title_from_messages(messages) == "识别截图中的表格，还原数据"


# ── _sync_title 保护逻辑回归测试 ──────────────────────────────

class TestSyncTitleProtection:
    """验证 _sync_title 不会覆盖 LLM 或用户设置的标题。"""

    @pytest.fixture
    def persistence(self, tmp_path):
        from excelmanus.conversation_persistence import ConversationPersistence
        from excelmanus.database import Database
        from excelmanus.chat_history import ChatHistoryStore

        db = Database(str(tmp_path / "test.db"))
        ch = ChatHistoryStore(db)
        return ConversationPersistence(ch), ch

    def test_sync_title_skips_when_auto_title_set(self, persistence):
        """title_source='auto' 时 _sync_title 不应覆盖标题。"""
        cp, ch = persistence
        ch.create_session("s1", "LLM生成的标题")
        ch.update_session("s1", title_source="auto")

        messages = [{"role": "user", "content": "帮我分析Q3营收报表"}]
        cp._sync_title("s1", messages)

        sessions = ch.list_sessions()
        assert sessions[0]["title"] == "LLM生成的标题"

    def test_sync_title_skips_when_user_title_set(self, persistence):
        """title_source='user' 时 _sync_title 不应覆盖标题。"""
        cp, ch = persistence
        ch.create_session("s1", "用户自定义标题")
        ch.update_session("s1", title_source="user")

        messages = [{"role": "user", "content": "帮我分析Q3营收报表"}]
        cp._sync_title("s1", messages)

        sessions = ch.list_sessions()
        assert sessions[0]["title"] == "用户自定义标题"

    def test_sync_title_skips_when_truncated_title_set(self, persistence):
        """title_source='truncated' 时 _sync_title 不应拉长即时截取标题。"""
        cp, ch = persistence
        ch.create_session("s1", "即时截取标题")
        ch.update_session("s1", title_source="truncated")

        messages = [{"role": "user", "content": "帮我把这份很长的销售报表按月份汇总并写回原表"}]
        cp._sync_title("s1", messages)

        sessions = ch.list_sessions()
        assert sessions[0]["title"] == "即时截取标题"

    def test_sync_title_updates_when_no_title_source(self, persistence):
        """title_source 未设置（默认）时 _sync_title 应正常更新。"""
        cp, ch = persistence
        ch.create_session("s1", "")

        messages = [{"role": "user", "content": "帮我分析Q3营收报表"}]
        cp._sync_title("s1", messages)

        sessions = ch.list_sessions()
        assert sessions[0]["title"] == "帮我分析Q3营收报表"

    def test_sync_title_keeps_full_fallback_title(self, persistence):
        """无 title_source 时，派生标题保留用户首行全文。"""
        cp, ch = persistence
        ch.create_session("s1", "")

        messages = [{"role": "user", "content": "帮我把这份很长的销售报表按月份汇总并写回原表"}]
        cp._sync_title("s1", messages)

        sessions = ch.list_sessions()
        assert sessions[0]["title"] == "帮我把这份很长的销售报表按月份汇总并写回原表"

    def test_full_flow_title_preserved_after_second_turn(self, persistence):
        """模拟完整流程：首轮 LLM 标题 → 第二轮 _sync_title 不覆盖。"""
        cp, ch = persistence

        # 首轮：创建会话，_sync_title 先写入原始消息
        ch.create_session("s1", "")
        msgs_turn1 = [
            {"role": "user", "content": "帮我分析Q3营收报表"},
            {"role": "assistant", "content": "好的，我来分析。"},
        ]
        cp._sync_title("s1", msgs_turn1)
        assert ch.list_sessions()[0]["title"] == "帮我分析Q3营收报表"

        # LLM 标题生成后覆盖
        ch.update_session("s1", title="Q3营收分析", title_source="auto")
        assert ch.list_sessions()[0]["title"] == "Q3营收分析"

        # 第二轮：_sync_title 不应覆盖 LLM 标题
        msgs_turn2 = msgs_turn1 + [
            {"role": "user", "content": "再帮我做个图表"},
            {"role": "assistant", "content": "好的。"},
        ]
        cp._sync_title("s1", msgs_turn2)
        assert ch.list_sessions()[0]["title"] == "Q3营收分析"


def test_truncate_user_message_as_title_keeps_full_line():
    from excelmanus.api_routes_chat import _truncate_user_message_as_title

    title = _truncate_user_message_as_title(
        "帮我把这份很长的销售报表按月份汇总并写回原表"
    )
    assert title == "帮我把这份很长的销售报表按月份汇总并写回原表"


@pytest.mark.asyncio
async def test_background_title_skips_when_session_in_flight(monkeypatch):
    from excelmanus import api_routes_chat as chat

    class _BusyManager:
        async def is_session_in_flight(self, session_id: str) -> bool:
            return True

    monkeypatch.setattr(chat, "get_session_manager", lambda: _BusyManager())
    called = {"n": 0}

    async def _should_not_run(**kwargs):
        called["n"] += 1
        return "should-not-run"

    monkeypatch.setattr(chat, "_generate_session_title_with_timeout", _should_not_run)
    await chat._generate_session_title_background(
        session_id="s1",
        user_message="hi",
        assistant_reply="ok",
    )
    assert called["n"] == 0
