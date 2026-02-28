"""Tests for session title generation."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from excelmanus.session_title import generate_session_title


@pytest.mark.asyncio
async def test_generate_title_returns_short_title():
    """AUX 模型正常返回时，应得到 stripped 标题。"""
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
    """AUX 模型返回空内容时，应返回 None。"""
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
async def test_generate_title_truncates_long_title():
    """LLM 返回超长标题时，应截断到 max_length。"""
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
    assert len(result) <= 15


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

    def test_sync_title_updates_when_no_title_source(self, persistence):
        """title_source 未设置（默认）时 _sync_title 应正常更新。"""
        cp, ch = persistence
        ch.create_session("s1", "")

        messages = [{"role": "user", "content": "帮我分析Q3营收报表"}]
        cp._sync_title("s1", messages)

        sessions = ch.list_sessions()
        assert sessions[0]["title"] == "帮我分析Q3营收报表"

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
