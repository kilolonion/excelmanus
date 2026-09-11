"""query_model_context_window 当前实现测试。

旧的 _extract_ctx_from_dict / _query_gemini_context / _lookup_litellm_registry
等辅助函数已删除。现实现：Gemini/Claude/OpenAIResponses 客户端直接返回 None；
标准 OpenAI 兼容客户端从 models.retrieve 提取 context_window / max_context_length。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from excelmanus.model_probe import query_model_context_window
from excelmanus.providers.claude import ClaudeClient
from excelmanus.providers.gemini import GeminiClient
from excelmanus.providers.openai_responses import OpenAIResponsesClient


class TestQueryModelContextWindowCurrent:
    @pytest.mark.asyncio
    async def test_gemini_client_returns_none(self) -> None:
        """专用 Gemini 客户端不再走独立查询层，直接返回 None。"""
        client = GeminiClient.__new__(GeminiClient)
        result = await query_model_context_window(client, "gemini-2.5-flash")
        assert result is None

    @pytest.mark.asyncio
    async def test_claude_client_returns_none(self) -> None:
        """专用 Claude 客户端不再走独立查询层，直接返回 None。"""
        client = ClaudeClient.__new__(ClaudeClient)
        result = await query_model_context_window(client, "claude-sonnet-4-6")
        assert result is None

    @pytest.mark.asyncio
    async def test_openai_responses_client_returns_none(self) -> None:
        """OpenAI Responses 客户端不再走独立查询层，直接返回 None。"""
        client = OpenAIResponsesClient.__new__(OpenAIResponsesClient)
        result = await query_model_context_window(client, "gpt-5")
        assert result is None

    @pytest.mark.asyncio
    async def test_openai_compat_context_window_attr(self) -> None:
        """标准 OpenAI 兼容客户端从 context_window 属性提取。"""
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.context_window = 128_000
        mock_resp.max_context_length = None
        mock_client.models.retrieve = AsyncMock(return_value=mock_resp)

        result = await query_model_context_window(
            mock_client, "gpt-4o", base_url="https://api.openai.com/v1",
        )
        assert result == 128_000

    @pytest.mark.asyncio
    async def test_mistral_max_context_length_attr(self) -> None:
        """Mistral 兼容客户端从 max_context_length 属性提取。"""
        mock_client = MagicMock()
        mock_resp = MagicMock(spec=["max_context_length"])
        mock_resp.max_context_length = 256_000
        mock_client.models.retrieve = AsyncMock(return_value=mock_resp)

        result = await query_model_context_window(
            mock_client, "mistral-large", base_url="https://api.mistral.ai/v1",
        )
        assert result == 256_000

    @pytest.mark.asyncio
    async def test_dict_fallback_context_window(self) -> None:
        """属性缺失时从 model_dump 字典兜底提取。"""
        mock_client = MagicMock()
        mock_resp = MagicMock(spec=["model_dump"])
        mock_resp.model_dump.return_value = {"context_window": 200_000}
        mock_client.models.retrieve = AsyncMock(return_value=mock_resp)

        result = await query_model_context_window(
            mock_client, "custom-model", base_url="https://api.together.xyz/v1",
        )
        assert result == 200_000

    @pytest.mark.asyncio
    async def test_retrieve_failure_returns_none(self) -> None:
        """models.retrieve 失败时返回 None，不抛异常。"""
        mock_client = MagicMock()
        mock_client.models.retrieve = AsyncMock(side_effect=RuntimeError("network"))

        result = await query_model_context_window(
            mock_client, "gpt-4o", base_url="https://api.openai.com/v1",
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_zero_context_window_ignored(self) -> None:
        """非正数窗口值应被忽略并返回 None。"""
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.context_window = 0
        mock_resp.max_context_length = None
        mock_resp.model_dump.return_value = {}
        mock_client.models.retrieve = AsyncMock(return_value=mock_resp)

        result = await query_model_context_window(
            mock_client, "gpt-4o", base_url="https://api.openai.com/v1",
        )
        assert result is None
