"""Codex OAuth 模型能力探测修复测试。

验证：
1. _try_thinking_stream 兼容 StreamDelta 格式（Fix 1）
2. probe-all 支持 Codex OAuth 配置文件（Fix 2）
3. test-connection 真实测试 Codex（Fix 3）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from excelmanus.model_probe import _try_thinking_stream
from excelmanus.providers.stream_types import StreamDelta


# ── 辅助工具 ───────────────────────────────────────────────────


class _FakeStreamFromDeltas:
    """模拟异步迭代器，逐个 yield StreamDelta 对象。"""

    def __init__(self, deltas: list[StreamDelta]) -> None:
        self._deltas = deltas
        self._idx = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._idx >= len(self._deltas):
            raise StopAsyncIteration
        d = self._deltas[self._idx]
        self._idx += 1
        return d


@dataclass
class _FakeDelta:
    """模拟标准 OpenAI SDK delta 对象。"""
    content: str | None = None
    reasoning_content: str | None = None
    reasoning: str | None = None
    thinking: str | None = None


@dataclass
class _FakeChoice:
    delta: _FakeDelta = field(default_factory=_FakeDelta)


@dataclass
class _FakeChunk:
    """模拟标准 OpenAI SDK stream chunk。"""
    choices: list[_FakeChoice] = field(default_factory=list)


class _FakeStreamFromChunks:
    """模拟异步迭代器，逐个 yield 标准 OpenAI SDK chunk。"""

    def __init__(self, chunks: list[_FakeChunk]) -> None:
        self._chunks = chunks
        self._idx = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._idx >= len(self._chunks):
            raise StopAsyncIteration
        c = self._chunks[self._idx]
        self._idx += 1
        return c


def _make_client_returning_stream(stream):
    """构造一个 mock client，client.chat.completions.create 返回给定的 stream。"""
    client = MagicMock()
    client.chat = MagicMock()
    client.chat.completions = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=stream)
    return client


# ══════════════════════════════════════════════════════════════
# Fix 1: _try_thinking_stream 兼容 StreamDelta
# ══════════════════════════════════════════════════════════════


class TestTryThinkingStreamDelta:
    """验证 _try_thinking_stream 正确处理 StreamDelta 对象。"""

    @pytest.mark.asyncio
    async def test_stream_delta_thinking_detected(self):
        """StreamDelta 含 thinking_delta → 应返回 (True, "")。"""
        deltas = [
            StreamDelta(thinking_delta="Let me think..."),
            StreamDelta(content_delta="The answer is 391"),
        ]
        stream = _FakeStreamFromDeltas(deltas)
        client = _make_client_returning_stream(stream)

        found, err = await _try_thinking_stream(
            client, "gpt-5.1-codex", [{"role": "user", "content": "Hi"}],
            timeout=5.0, extra_kwargs={},
        )
        assert found is True
        assert err == ""

    @pytest.mark.asyncio
    async def test_stream_delta_content_only_no_thinking(self):
        """StreamDelta 仅含 content_delta（无 thinking_delta）→ 应返回 (False, "")。"""
        deltas = [
            StreamDelta(content_delta="The answer is 391"),
        ]
        stream = _FakeStreamFromDeltas(deltas)
        client = _make_client_returning_stream(stream)

        found, err = await _try_thinking_stream(
            client, "gpt-5.1-codex", [{"role": "user", "content": "Hi"}],
            timeout=5.0, extra_kwargs={},
        )
        assert found is False
        assert err == ""

    @pytest.mark.asyncio
    async def test_stream_delta_empty_stream(self):
        """空 StreamDelta 流 → 应返回 (False, "")。"""
        stream = _FakeStreamFromDeltas([])
        client = _make_client_returning_stream(stream)

        found, err = await _try_thinking_stream(
            client, "gpt-5.1-codex", [{"role": "user", "content": "Hi"}],
            timeout=5.0, extra_kwargs={},
        )
        assert found is False
        assert err == ""

    @pytest.mark.asyncio
    async def test_stream_delta_finish_reason_only(self):
        """StreamDelta 仅含 finish_reason（无 thinking/content）→ 空转后返回 (False, "")。"""
        deltas = [
            StreamDelta(finish_reason="stop"),
        ]
        stream = _FakeStreamFromDeltas(deltas)
        client = _make_client_returning_stream(stream)

        found, err = await _try_thinking_stream(
            client, "gpt-5.1-codex", [{"role": "user", "content": "Hi"}],
            timeout=5.0, extra_kwargs={},
        )
        assert found is False
        assert err == ""


# ══════════════════════════════════════════════════════════════
# Fix 1 回归：标准 OpenAI SDK chunk 路径未被破坏
# ══════════════════════════════════════════════════════════════


class TestTryThinkingStandardChunk:
    """验证标准 OpenAI SDK chunk 格式仍然正常工作。"""

    @pytest.mark.asyncio
    async def test_standard_chunk_reasoning_detected(self):
        """标准 chunk 含 reasoning_content → 应返回 (True, "")。"""
        chunks = [
            _FakeChunk(choices=[_FakeChoice(delta=_FakeDelta(reasoning_content="Step 1..."))]),
        ]
        stream = _FakeStreamFromChunks(chunks)
        client = _make_client_returning_stream(stream)

        found, err = await _try_thinking_stream(
            client, "o3-mini", [{"role": "user", "content": "Hi"}],
            timeout=5.0, extra_kwargs={},
        )
        assert found is True
        assert err == ""

    @pytest.mark.asyncio
    async def test_standard_chunk_content_only(self):
        """标准 chunk 仅含 content → 应返回 (False, "")。"""
        chunks = [
            _FakeChunk(choices=[_FakeChoice(delta=_FakeDelta(content="Hello"))]),
        ]
        stream = _FakeStreamFromChunks(chunks)
        client = _make_client_returning_stream(stream)

        found, err = await _try_thinking_stream(
            client, "gpt-4o", [{"role": "user", "content": "Hi"}],
            timeout=5.0, extra_kwargs={},
        )
        assert found is False
        assert err == ""


# ══════════════════════════════════════════════════════════════
# Fix 2: probe-all 支持 Codex OAuth 配置文件
# ══════════════════════════════════════════════════════════════


class TestProbeAllCodexOAuth:
    """probe-all 跳过 Codex 档案；连通测试返回订阅说明，不走通用 API Key。"""

    def _make_request(self, body: dict | None = None):
        request = AsyncMock()
        request.app = MagicMock()
        request.json = AsyncMock(return_value=body or {})
        return request

    @pytest.mark.asyncio
    async def test_probe_all_skips_codex_profiles(self):
        mock_config = MagicMock()
        mock_config.model = "gpt-4o"
        mock_config.base_url = "https://api.openai.com/v1"
        mock_config.api_key = "sk-test"
        mock_config.protocol = "openai"

        mock_config_store = MagicMock()
        mock_config_store.list_profiles.return_value = [
            {"name": "codex", "model": "openai-codex/gpt-5.1-codex", "thinking_mode": "auto"},
        ]
        mock_session_manager = MagicMock()
        mock_session_manager.database = None
        mock_session_manager.broadcast_model_capabilities = AsyncMock()

        with patch("excelmanus.api_app_state._config", mock_config), \
             patch("excelmanus.api_app_state._config_store", mock_config_store), \
             patch("excelmanus.api_app_state._session_manager", mock_session_manager), \
             patch("excelmanus.model_probe.run_full_probe", new_callable=AsyncMock) as mock_probe:
            mock_caps = MagicMock()
            mock_caps.to_dict.return_value = {"supports_tool_calling": True}
            mock_probe.return_value = mock_caps

            from excelmanus.api import probe_all_model_capabilities
            response = await probe_all_model_capabilities(self._make_request())
            import json
            results = json.loads(response.body)["results"]
            assert all("openai-codex/" not in r.get("model", "") for r in results)


class TestConnectionCodexOAuth:
    """test_model_connection 对 Codex 返回订阅说明，不探测通用 API Key。"""

    def _make_request(self, body: dict):
        request = AsyncMock()
        request.app = MagicMock()
        request.json = AsyncMock(return_value=body)
        return request

    @pytest.mark.asyncio
    async def test_codex_connection_returns_oauth_note(self):
        request = self._make_request({"model": "openai-codex/gpt-5.1-codex"})
        mock_config = MagicMock()
        with patch("excelmanus.api_app_state._config", mock_config), \
             patch("excelmanus.api_app_state._config_store", None):
            from excelmanus.api import test_model_connection
            response = await test_model_connection(request)
            import json
            body = json.loads(response.body)
            assert body["ok"] is True
            assert "Codex OAuth" in (body.get("note") or "")
