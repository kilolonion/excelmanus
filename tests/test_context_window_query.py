"""query_model_context_window 三层查询链测试。

Layer 1: Provider 原生 API（Gemini / Claude / OpenAI / OpenAI compat）
Layer 2: LiteLLM 在线模型注册表
Layer 3: 静态前缀映射（config._infer_context_tokens_for_model）
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from excelmanus.model_probe import (
    _extract_ctx_from_dict,
    _lookup_litellm_registry,
    query_model_context_window,
)


# ── 辅助 mock 类 ─────────────────────────────────────────────────


class _FakeGeminiModels:
    def __init__(self, response: dict[str, Any] | Exception):
        self._response = response

    async def retrieve(self, model: str) -> dict[str, Any]:
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


class _FakeGeminiClient:
    """模拟 GeminiClient，仅提供 models.retrieve()。"""

    def __init__(self, response: dict[str, Any] | Exception):
        self.models = _FakeGeminiModels(response)

    # 让 isinstance 检查通过
    __class_name__ = "GeminiClient"


class _FakeClaudeModels:
    def __init__(self, response: dict[str, Any] | Exception):
        self._response = response

    async def retrieve(self, model: str) -> dict[str, Any]:
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


class _FakeClaudeClient:
    def __init__(self, response: dict[str, Any] | Exception):
        self.models = _FakeClaudeModels(response)


class _FakeResponsesModels:
    def __init__(self, response: dict[str, Any] | Exception):
        self._response = response

    async def retrieve(self, model: str) -> dict[str, Any]:
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


class _FakeOpenAIResponsesClient:
    def __init__(self, response: dict[str, Any] | Exception):
        self.models = _FakeResponsesModels(response)


# ── _extract_ctx_from_dict 测试 ──────────────────────────────────


class TestExtractCtxFromDict:
    def test_openai_context_window(self):
        assert _extract_ctx_from_dict({"context_window": 128_000}) == 128_000

    def test_mistral_max_context_length(self):
        assert _extract_ctx_from_dict({"max_context_length": 256_000}) == 256_000

    def test_gemini_input_token_limit(self):
        assert _extract_ctx_from_dict({"inputTokenLimit": 1_048_576}) == 1_048_576

    def test_litellm_max_input_tokens(self):
        assert _extract_ctx_from_dict({"max_input_tokens": 200_000}) == 200_000

    def test_empty_dict(self):
        assert _extract_ctx_from_dict({}) is None

    def test_zero_value_ignored(self):
        assert _extract_ctx_from_dict({"context_window": 0}) is None

    def test_negative_value_ignored(self):
        assert _extract_ctx_from_dict({"context_window": -1}) is None

    def test_priority_first_match_wins(self):
        data = {"context_window": 100_000, "inputTokenLimit": 200_000}
        assert _extract_ctx_from_dict(data) == 100_000


# ── query_model_context_window Layer 1 测试 ──────────────────────


class TestQueryGeminiContext:
    @pytest.mark.asyncio
    async def test_gemini_returns_input_token_limit(self):
        """Gemini models.get 返回 inputTokenLimit 时应正确提取。"""
        client = _FakeGeminiClient({"inputTokenLimit": 1_048_576})
        with patch(
            "excelmanus.model_probe.isinstance",
            side_effect=lambda obj, cls: type(obj).__name__ == "GeminiClient"
            if "GeminiClient" in str(cls)
            else isinstance(obj, cls),
        ):
            pass
        # 直接测试内部函数
        from excelmanus.model_probe import _query_gemini_context

        result = await _query_gemini_context(client, "gemini-2.5-flash", timeout=5.0)
        assert result == 1_048_576

    @pytest.mark.asyncio
    async def test_gemini_api_failure_returns_none(self):
        """Gemini API 失败时应返回 None。"""
        client = _FakeGeminiClient(RuntimeError("Network error"))
        from excelmanus.model_probe import _query_gemini_context

        result = await _query_gemini_context(client, "gemini-2.5-flash", timeout=5.0)
        assert result is None


class TestQueryClaudeContext:
    @pytest.mark.asyncio
    async def test_claude_no_context_window_returns_none(self):
        """当前 Anthropic API 不返回 context_window，应返回 None。"""
        client = _FakeClaudeClient({
            "type": "model",
            "id": "claude-sonnet-4-6",
            "display_name": "Claude Sonnet 4.6",
        })
        from excelmanus.model_probe import _query_claude_context

        result = await _query_claude_context(client, "claude-sonnet-4-6", timeout=5.0)
        assert result is None

    @pytest.mark.asyncio
    async def test_claude_future_context_window_field(self):
        """若 Anthropic 未来添加 context_window 字段，应自动生效。"""
        client = _FakeClaudeClient({
            "type": "model",
            "id": "claude-sonnet-4-6",
            "context_window": 200_000,
        })
        from excelmanus.model_probe import _query_claude_context

        result = await _query_claude_context(client, "claude-sonnet-4-6", timeout=5.0)
        assert result == 200_000


class TestQueryOpenAIResponsesContext:
    @pytest.mark.asyncio
    async def test_openai_responses_returns_context_window(self):
        """OpenAI /v1/models 返回 context_window 时应正确提取。"""
        client = _FakeOpenAIResponsesClient({"context_window": 400_000})
        from excelmanus.model_probe import _query_openai_responses_context

        result = await _query_openai_responses_context(
            client, "gpt-5", timeout=5.0,
        )
        assert result == 400_000


# ── query_model_context_window Layer 2 (LiteLLM) 测试 ────────────


class TestLiteLLMRegistryLookup:
    @pytest.mark.asyncio
    async def test_exact_match(self):
        """精确匹配 LiteLLM registry key。"""
        fake_registry = {
            "gpt-5": {"max_input_tokens": 400_000, "max_tokens": 16384},
        }
        with patch("excelmanus.model_probe._fetch_litellm_registry", return_value=fake_registry):
            result = await _lookup_litellm_registry("gpt-5")
        assert result == 400_000

    @pytest.mark.asyncio
    async def test_strip_provider_prefix(self):
        """model 带 provider 前缀时，应去除后匹配。"""
        fake_registry = {
            "deepseek-chat": {"max_input_tokens": 128_000},
        }
        with patch("excelmanus.model_probe._fetch_litellm_registry", return_value=fake_registry):
            result = await _lookup_litellm_registry("deepseek/deepseek-chat")
        assert result == 128_000

    @pytest.mark.asyncio
    async def test_add_provider_prefix(self):
        """model 无前缀但 registry 使用 {provider}/{model} 格式时，应补前缀匹配。"""
        fake_registry = {
            "deepseek/deepseek-chat": {"max_input_tokens": 128_000},
        }
        with patch("excelmanus.model_probe._fetch_litellm_registry", return_value=fake_registry):
            result = await _lookup_litellm_registry("deepseek-chat")
        assert result == 128_000

    @pytest.mark.asyncio
    async def test_registry_unavailable_returns_none(self):
        """注册表不可用时应返回 None。"""
        with patch("excelmanus.model_probe._fetch_litellm_registry", return_value=None):
            result = await _lookup_litellm_registry("unknown-model")
        assert result is None

    @pytest.mark.asyncio
    async def test_model_not_in_registry(self):
        """模型不在注册表中时应返回 None。"""
        fake_registry = {
            "gpt-5": {"max_input_tokens": 400_000},
        }
        with patch("excelmanus.model_probe._fetch_litellm_registry", return_value=fake_registry):
            result = await _lookup_litellm_registry("totally-unknown-model-xyz")
        assert result is None


# ── query_model_context_window 集成测试 ──────────────────────────


class TestQueryModelContextWindowIntegration:
    @pytest.mark.asyncio
    async def test_layer1_success_skips_layer2(self):
        """Layer 1 成功时不应触发 Layer 2 查询。"""
        client = _FakeGeminiClient({"inputTokenLimit": 1_048_576})

        with (
            patch("excelmanus.model_probe.isinstance", wraps=isinstance) as mock_isinstance,
            patch("excelmanus.model_probe._query_gemini_context", return_value=1_048_576) as mock_gemini,
            patch("excelmanus.model_probe._lookup_litellm_registry") as mock_litellm,
        ):
            # 需要让 isinstance 对 GeminiClient 返回 True
            from excelmanus.providers.gemini import GeminiClient as _GC
            real_gc = _GC.__new__(_GC)
            real_gc._api_key = "test"
            real_gc._base_url = "https://generativelanguage.googleapis.com/v1beta"
            real_gc._http = MagicMock()
            real_gc._default_model = ""
            real_gc.chat = MagicMock()
            real_gc.models = _FakeGeminiModels({"inputTokenLimit": 1_048_576})

            result = await query_model_context_window(real_gc, "gemini-2.5-flash")
            assert result == 1_048_576
            mock_litellm.assert_not_called()

    @pytest.mark.asyncio
    async def test_layer1_failure_falls_through_to_layer2(self):
        """Layer 1 失败时应回退到 Layer 2。"""
        from excelmanus.providers.gemini import GeminiClient as _GC
        real_gc = _GC.__new__(_GC)
        real_gc._api_key = "test"
        real_gc._base_url = "https://generativelanguage.googleapis.com/v1beta"
        real_gc._http = MagicMock()
        real_gc._default_model = ""
        real_gc.chat = MagicMock()
        real_gc.models = _FakeGeminiModels(RuntimeError("API down"))

        fake_registry = {
            "gemini/gemini-2.5-flash": {"max_input_tokens": 1_048_576},
        }
        with patch("excelmanus.model_probe._fetch_litellm_registry", return_value=fake_registry):
            result = await query_model_context_window(real_gc, "gemini-2.5-flash")
        assert result == 1_048_576

    @pytest.mark.asyncio
    async def test_both_layers_fail_returns_none(self):
        """两层均失败时应返回 None（由调用方回退到静态映射）。"""
        from excelmanus.providers.gemini import GeminiClient as _GC
        real_gc = _GC.__new__(_GC)
        real_gc._api_key = "test"
        real_gc._base_url = "https://generativelanguage.googleapis.com/v1beta"
        real_gc._http = MagicMock()
        real_gc._default_model = ""
        real_gc.chat = MagicMock()
        real_gc.models = _FakeGeminiModels(RuntimeError("API down"))

        with patch("excelmanus.model_probe._fetch_litellm_registry", return_value=None):
            result = await query_model_context_window(real_gc, "gemini-2.5-flash")
        assert result is None

    @pytest.mark.asyncio
    async def test_openai_compat_provider_whitelist_expanded(self):
        """扩展后的 provider 白名单：xai / cohere / deepseek 等不再被跳过。"""
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.context_window = 256_000
        mock_client.models.retrieve = AsyncMock(return_value=mock_resp)

        result = await query_model_context_window(
            mock_client, "grok-4", base_url="https://api.x.ai/v1",
        )
        assert result == 256_000
