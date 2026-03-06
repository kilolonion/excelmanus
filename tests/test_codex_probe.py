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
    """验证 probe_all_model_capabilities 正确处理 Codex 配置文件。"""

    def _make_request(self, resolver=None, user_id=None):
        """构造 mock Request 对象。"""
        app_state = MagicMock()
        app_state.credential_resolver = resolver
        app = MagicMock()
        app.state = app_state
        request = MagicMock()
        request.app = app
        return request

    @pytest.mark.asyncio
    async def test_codex_profile_with_credential_included_in_targets(self):
        """有 OAuth 凭证的 Codex 配置 → 应被加入 targets 而非跳过。"""
        import ast
        import inspect

        # 使用 AST 验证 probe-all 中不再无条件 continue Codex profiles
        from excelmanus import api as api_module
        source = inspect.getsource(api_module.probe_all_model_capabilities)
        tree = ast.parse(source)

        # 查找 "openai-codex/" 字符串引用
        found_codex_check = False
        found_resolver = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if "openai-codex/" in node.value:
                    found_codex_check = True
            if isinstance(node, ast.Attribute) and node.attr == "resolve_sync":
                found_resolver = True
        assert found_codex_check, "probe-all 应检查 openai-codex/ 前缀"
        assert found_resolver, "probe-all 应调用 resolve_sync 解析凭证"

    @pytest.mark.asyncio
    async def test_codex_profile_no_credential_skipped_with_note(self):
        """无 OAuth 凭证时 → 结果中应有 skipped 标注。"""
        # 模拟无凭证的 resolver
        resolver = MagicMock()
        resolver.resolve_sync = MagicMock(return_value=None)

        # 模拟 _config_store, _config, _session_manager 等全局变量
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

        request = self._make_request(resolver=resolver, user_id="user-1")

        with patch("excelmanus.api._require_admin_if_auth_enabled", new_callable=AsyncMock, return_value=None), \
             patch("excelmanus.api._config", mock_config), \
             patch("excelmanus.api._config_store", mock_config_store), \
             patch("excelmanus.api._session_manager", mock_session_manager), \
             patch("excelmanus.api._get_isolation_user_id", return_value="user-1"), \
             patch("excelmanus.model_probe.run_full_probe", new_callable=AsyncMock) as mock_probe:

            # 让 main 模型探测返回一个 mock 结果
            mock_caps = MagicMock()
            mock_caps.to_dict.return_value = {"supports_tool_calling": True}
            mock_probe.return_value = mock_caps

            from excelmanus.api import probe_all_model_capabilities
            response = await probe_all_model_capabilities(request)

            import json
            body = json.loads(response.body)
            results = body["results"]

            # 找到 Codex 的结果
            codex_results = [r for r in results if "openai-codex/" in r.get("model", "")]
            assert len(codex_results) == 1
            assert codex_results[0].get("skipped") == "no_oauth_credential"

    @pytest.mark.asyncio
    async def test_codex_profile_with_credential_probed(self):
        """有 OAuth 凭证时 → 应执行真实探测。"""
        from excelmanus.auth.providers.base import ResolvedCredential

        resolved = ResolvedCredential(
            api_key="oauth-token-xxx",
            base_url="https://chatgpt.com/backend-api/codex",
            source="oauth",
            provider="openai-codex",
            protocol="openai_responses",
        )
        resolver = MagicMock()
        resolver.resolve_sync = MagicMock(return_value=resolved)

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

        request = self._make_request(resolver=resolver, user_id="user-1")

        with patch("excelmanus.api._require_admin_if_auth_enabled", new_callable=AsyncMock, return_value=None), \
             patch("excelmanus.api._config", mock_config), \
             patch("excelmanus.api._config_store", mock_config_store), \
             patch("excelmanus.api._session_manager", mock_session_manager), \
             patch("excelmanus.api._get_isolation_user_id", return_value="user-1"), \
             patch("excelmanus.model_probe.run_full_probe", new_callable=AsyncMock) as mock_probe, \
             patch("excelmanus.providers.create_client") as mock_create:

            mock_caps = MagicMock()
            mock_caps.to_dict.return_value = {"supports_thinking": True}
            mock_probe.return_value = mock_caps

            from excelmanus.api import probe_all_model_capabilities
            response = await probe_all_model_capabilities(request)

            import json
            body = json.loads(response.body)
            results = body["results"]

            # Codex 结果应包含 capabilities（非 skipped）
            codex_results = [r for r in results if "openai-codex/" in r.get("model", "")]
            assert len(codex_results) == 1
            assert "capabilities" in codex_results[0]
            assert codex_results[0]["capabilities"]["supports_thinking"] is True

            # 验证 create_client 使用了 OAuth 凭证和 openai_responses 协议
            _codex_call = None
            for call in mock_create.call_args_list:
                if call.kwargs.get("api_key") == "oauth-token-xxx":
                    _codex_call = call
            assert _codex_call is not None, "应使用 OAuth token 创建客户端"
            assert _codex_call.kwargs.get("protocol") == "openai_responses"

            # 验证 run_full_probe 使用了真实 model ID（剥离前缀）
            for call in mock_probe.call_args_list:
                if call.kwargs.get("model") == "gpt-5.1-codex":
                    break
            else:
                pytest.fail("run_full_probe 应使用剥离前缀后的真实 model ID 'gpt-5.1-codex'")


# ══════════════════════════════════════════════════════════════
# Fix 3: test-connection 真实测试 Codex
# ══════════════════════════════════════════════════════════════


class TestConnectionCodexOAuth:
    """验证 test_model_connection 正确处理 Codex OAuth。"""

    def _make_request(self, body: dict, resolver=None, user_id=None):
        """构造 mock Request 对象。"""
        app_state = MagicMock()
        app_state.credential_resolver = resolver
        app = MagicMock()
        app.state = app_state
        request = AsyncMock()
        request.app = app
        request.json = AsyncMock(return_value=body)
        return request

    @pytest.mark.asyncio
    async def test_codex_no_credential_returns_error(self):
        """无 OAuth 凭证 → 返回 ok=False 并提示登录。"""
        resolver = MagicMock()
        resolver.resolve_sync = MagicMock(return_value=None)

        request = self._make_request(
            {"model": "openai-codex/gpt-5.1-codex"},
            resolver=resolver, user_id="user-1",
        )

        mock_config = MagicMock()
        with patch("excelmanus.api._require_admin_if_auth_enabled", new_callable=AsyncMock, return_value=None), \
             patch("excelmanus.api._config", mock_config), \
             patch("excelmanus.api._config_store", None), \
             patch("excelmanus.api._get_isolation_user_id", return_value="user-1"):

            from excelmanus.api import test_model_connection
            response = await test_model_connection(request)

            import json
            body = json.loads(response.body)
            assert body["ok"] is False
            assert "Codex OAuth" in body["error"] or "登录" in body["error"]

    @pytest.mark.asyncio
    async def test_codex_with_credential_real_test(self):
        """有 OAuth 凭证 → 执行真实 probe_health 测试。"""
        from excelmanus.auth.providers.base import ResolvedCredential

        resolved = ResolvedCredential(
            api_key="oauth-token-xxx",
            base_url="https://chatgpt.com/backend-api/codex",
            source="oauth",
            provider="openai-codex",
            protocol="openai_responses",
        )
        resolver = MagicMock()
        resolver.resolve_sync = MagicMock(return_value=resolved)

        request = self._make_request(
            {"model": "openai-codex/gpt-5.1-codex"},
            resolver=resolver, user_id="user-1",
        )

        mock_config = MagicMock()
        with patch("excelmanus.api._require_admin_if_auth_enabled", new_callable=AsyncMock, return_value=None), \
             patch("excelmanus.api._config", mock_config), \
             patch("excelmanus.api._config_store", None), \
             patch("excelmanus.api._get_isolation_user_id", return_value="user-1"), \
             patch("excelmanus.model_probe.probe_health", new_callable=AsyncMock, return_value=(True, "")) as mock_health, \
             patch("excelmanus.providers.create_client") as mock_create:

            from excelmanus.api import test_model_connection
            response = await test_model_connection(request)

            import json
            body = json.loads(response.body)
            assert body["ok"] is True

            # 验证 probe_health 被调用，且使用了真实 model ID
            mock_health.assert_called_once()
            call_args = mock_health.call_args
            assert call_args.args[1] == "gpt-5.1-codex"  # 剥离前缀后的 model

            # 验证 create_client 使用了 OAuth 凭证
            mock_create.assert_called_once()
            assert mock_create.call_args.kwargs.get("api_key") == "oauth-token-xxx"
            assert mock_create.call_args.kwargs.get("protocol") == "openai_responses"

    @pytest.mark.asyncio
    async def test_codex_health_failure_reported(self):
        """OAuth 凭证过期 → probe_health 返回认证错误 → ok=False。"""
        from excelmanus.auth.providers.base import ResolvedCredential

        resolved = ResolvedCredential(
            api_key="expired-token",
            base_url="https://chatgpt.com/backend-api/codex",
            source="oauth",
            provider="openai-codex",
            protocol="openai_responses",
        )
        resolver = MagicMock()
        resolver.resolve_sync = MagicMock(return_value=resolved)

        request = self._make_request(
            {"model": "openai-codex/gpt-5.1-codex"},
            resolver=resolver, user_id="user-1",
        )

        mock_config = MagicMock()
        with patch("excelmanus.api._require_admin_if_auth_enabled", new_callable=AsyncMock, return_value=None), \
             patch("excelmanus.api._config", mock_config), \
             patch("excelmanus.api._config_store", None), \
             patch("excelmanus.api._get_isolation_user_id", return_value="user-1"), \
             patch("excelmanus.model_probe.probe_health", new_callable=AsyncMock, return_value=(False, "401 Unauthorized")), \
             patch("excelmanus.providers.create_client"):

            from excelmanus.api import test_model_connection
            response = await test_model_connection(request)

            import json
            body = json.loads(response.body)
            assert body["ok"] is False
            assert "401" in body.get("error", "") or "Unauthorized" in body.get("error", "")
