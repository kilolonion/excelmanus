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
