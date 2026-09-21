"""probe_health 瞬时错误 vs 永久错误分类测试。

验证修复：probe_health 对瞬时错误（超时、限流、网络抖动）返回 (None, err)，
对永久性错误（认证失败、模型不存在、额度不足）返回 (False, err)，
run_full_probe 不持久化瞬时错误的 unhealthy 结果。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from excelmanus.model_probe import (
    ModelCapabilities,
    _is_permanent_health_failure,
    _try_thinking_stream,
    capabilities_cache_is_fresh,
    probe_health,
    run_full_probe,
)
from excelmanus.providers import OpenAIResponsesClient


# ── _is_permanent_health_failure 单元测试 ──────────────────


class TestIsPermanentHealthFailure:
    """永久性健康错误检测。"""

    @pytest.mark.parametrize("err", [
        "Error code: 401 - Unauthorized",
        "Error 403 Forbidden: access denied",
        "Invalid API key provided: sk-xxx...",
        "Authentication failed for model gpt-4",
        "Error code: 402 - Payment required",
        "Insufficient quota: your account balance is 0",
        "Quota exceeded for this billing period",
        "Error code: 404 - Model not found",
        "The model 'fake-model' does not exist",
        "No such model: nonexistent-model",
    ])
    def test_permanent_errors_detected(self, err: str):
        assert _is_permanent_health_failure(err) is True

    @pytest.mark.parametrize("err", [
        "Connection timed out after 15s",
        "Error code: 429 - Rate limit exceeded",
        "Rate limit reached for requests",
        "Error code: 500 - Internal server error",
        "Error code: 502 - Bad gateway",
        "Error code: 503 - Service temporarily unavailable",
        "Connection refused",
        "Network is unreachable",
        "asyncio.TimeoutError",
        "SSLError: certificate verify failed",
        "Connection reset by peer",
    ])
    def test_transient_errors_not_detected(self, err: str):
        assert _is_permanent_health_failure(err) is False

    def test_empty_string(self):
        assert _is_permanent_health_failure("") is False


# ── probe_health 返回值测试 ────────────────────────────────


class TestProbeHealthReturnValues:
    """probe_health 对不同异常类型的返回值。"""

    @pytest.mark.asyncio
    async def test_success_returns_true(self):
        client = MagicMock()
        client.chat.completions.create = AsyncMock(return_value=MagicMock())
        ok, err = await probe_health(client, "test-model", timeout=5.0)
        assert ok is True
        assert err == ""

    @pytest.mark.asyncio
    async def test_responses_health_probe_omits_output_limit(self):
        client = OpenAIResponsesClient.__new__(OpenAIResponsesClient)
        create = AsyncMock(return_value=MagicMock())
        client.chat = SimpleNamespace(
            completions=SimpleNamespace(create=create),
        )

        ok, err = await probe_health(client, "gpt-6-astra", timeout=5.0)

        assert ok is True
        assert err == ""
        assert "max_tokens" not in create.await_args.kwargs

    @pytest.mark.asyncio
    async def test_auth_error_returns_false(self):
        """认证错误 → (False, err)，永久性不健康。"""
        client = MagicMock()
        client.chat.completions.create = AsyncMock(
            side_effect=Exception("Error code: 401 - Unauthorized")
        )
        ok, err = await probe_health(client, "test-model", timeout=5.0)
        assert ok is False
        assert "401" in err

    @pytest.mark.asyncio
    async def test_model_not_found_returns_false(self):
        """模型不存在 → (False, err)，永久性不健康。"""
        client = MagicMock()
        client.chat.completions.create = AsyncMock(
            side_effect=Exception("Error code: 404 - Model not found")
        )
        ok, err = await probe_health(client, "test-model", timeout=5.0)
        assert ok is False
        assert "404" in err

    @pytest.mark.asyncio
    async def test_quota_error_returns_false(self):
        """额度不足 → (False, err)，永久性不健康。"""
        client = MagicMock()
        client.chat.completions.create = AsyncMock(
            side_effect=Exception("Insufficient quota for this request")
        )
        ok, err = await probe_health(client, "test-model", timeout=5.0)
        assert ok is False
        assert "quota" in err.lower()

    @pytest.mark.asyncio
    async def test_timeout_returns_none(self):
        """超时 → (None, err)，瞬时错误。"""
        client = MagicMock()

        async def _slow(*a, **kw):
            await asyncio.sleep(100)

        client.chat.completions.create = _slow
        ok, err = await probe_health(client, "test-model", timeout=0.01)
        assert ok is None
        # asyncio.TimeoutError 的 str() 可能为空，关键是 ok 为 None

    @pytest.mark.asyncio
    async def test_rate_limit_returns_none(self):
        """限流 → (None, err)，瞬时错误。"""
        client = MagicMock()
        client.chat.completions.create = AsyncMock(
            side_effect=Exception("Error code: 429 - Rate limit exceeded")
        )
        ok, err = await probe_health(client, "test-model", timeout=5.0)
        assert ok is None
        assert "429" in err

    @pytest.mark.asyncio
    async def test_server_error_returns_none(self):
        """5xx 服务器错误 → (None, err)，瞬时错误。"""
        client = MagicMock()
        client.chat.completions.create = AsyncMock(
            side_effect=Exception("Error code: 500 - Internal server error")
        )
        ok, err = await probe_health(client, "test-model", timeout=5.0)
        assert ok is None
        assert "500" in err

    @pytest.mark.asyncio
    async def test_connection_error_returns_none(self):
        """网络连接错误 → (None, err)，瞬时错误。"""
        client = MagicMock()
        client.chat.completions.create = AsyncMock(
            side_effect=ConnectionError("Connection refused")
        )
        ok, err = await probe_health(client, "test-model", timeout=5.0)
        assert ok is None
        assert "connection" in err.lower()


# ── run_full_probe 持久化行为测试 ──────────────────────────


class TestRunFullProbeTransientHealth:
    """run_full_probe 对瞬时健康检查失败的持久化行为。"""

    @pytest.mark.asyncio
    async def test_permanent_failure_saved_to_db(self):
        """永久性错误 → healthy=False 被持久化到 DB。"""
        client = MagicMock()
        client.chat.completions.create = AsyncMock(
            side_effect=Exception("Error code: 401 - Unauthorized")
        )
        db = MagicMock()
        db.conn = MagicMock()

        with patch("excelmanus.model_probe.save_capabilities") as mock_save:
            caps = await run_full_probe(
                client=client,
                model="test-model",
                base_url="https://api.example.com/v1",
                skip_if_cached=False,
                db=db,
            )

        assert caps.healthy is False
        assert caps.health_error
        mock_save.assert_called_once()
        saved_caps = mock_save.call_args[0][1]
        assert saved_caps.healthy is False

    @pytest.mark.asyncio
    async def test_transient_failure_not_saved_to_db(self):
        """瞬时错误 → healthy=None，不被持久化到 DB。"""
        client = MagicMock()
        client.chat.completions.create = AsyncMock(
            side_effect=Exception("Error code: 500 - Internal server error")
        )
        db = MagicMock()
        db.conn = MagicMock()

        with patch("excelmanus.model_probe.save_capabilities") as mock_save:
            caps = await run_full_probe(
                client=client,
                model="test-model",
                base_url="https://api.example.com/v1",
                skip_if_cached=False,
                db=db,
            )

        assert caps.healthy is None
        assert caps.health_error
        mock_save.assert_not_called()

    @pytest.mark.asyncio
    async def test_transient_failure_skips_capability_probes(self):
        """瞬时健康检查失败仍跳过能力探测（无法到达模型）。"""
        client = MagicMock()
        client.chat.completions.create = AsyncMock(
            side_effect=Exception("Connection timed out")
        )

        with patch("excelmanus.model_probe.probe_tool_calling") as mock_tc, \
             patch("excelmanus.model_probe.probe_vision") as mock_vis:
            caps = await run_full_probe(
                client=client,
                model="test-model",
                base_url="https://api.example.com/v1",
                skip_if_cached=False,
                db=None,
            )

        assert caps.healthy is None
        assert caps.supports_tool_calling is None
        assert caps.supports_vision is None
        mock_tc.assert_not_called()
        mock_vis.assert_not_called()

    @pytest.mark.asyncio
    async def test_transient_failure_allows_reprobe(self):
        """瞬时失败不缓存 → 下次 skip_if_cached=True 仍会重新探测。"""
        call_count = 0

        async def _mock_create(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("Error code: 502 - Bad gateway")
            return MagicMock()

        client = MagicMock()
        client.chat.completions.create = _mock_create
        db = MagicMock()
        db.conn = MagicMock()

        with patch("excelmanus.model_probe.save_capabilities") as mock_save, \
             patch("excelmanus.model_probe.load_capabilities", return_value=None):
            # 第一次：瞬时失败，不保存
            caps1 = await run_full_probe(
                client=client, model="m", base_url="http://x/v1",
                skip_if_cached=True, db=db,
            )
            assert caps1.healthy is None
            assert mock_save.call_count == 0

            # 第二次：因为没有缓存（load_capabilities=None），仍然会重新探测
            # 这次成功了
            caps2 = await run_full_probe(
                client=client, model="m", base_url="http://x/v1",
                skip_if_cached=True, db=db,
            )
            assert caps2.healthy is True
            assert mock_save.call_count == 1  # 成功结果被保存


# ── 探测缓存新鲜度 ─────────────────────────────────────────


class TestCapabilitiesCacheFreshness:
    """capabilities_cache_is_fresh：过期/不完整缓存不得当作永久有效。"""

    def _caps(self, **overrides) -> ModelCapabilities:
        base = ModelCapabilities(
            model="m",
            base_url="http://x/v1",
            healthy=True,
            supports_tool_calling=True,
            supports_vision=True,
            supports_thinking=False,
        )
        for key, value in overrides.items():
            setattr(base, key, value)
        return base

    def test_manual_override_always_fresh(self):
        assert capabilities_cache_is_fresh(self._caps(manual_override=True)) is True

    def test_unknown_health_not_fresh(self):
        assert capabilities_cache_is_fresh(self._caps(healthy=None)) is False

    def test_partial_capabilities_not_fresh(self):
        assert capabilities_cache_is_fresh(self._caps(supports_vision=None)) is False

    def test_fresh_until_in_future(self):
        from datetime import datetime, timedelta, timezone
        future = (datetime.now(tz=timezone.utc) + timedelta(hours=1)).isoformat()
        assert capabilities_cache_is_fresh(self._caps(fresh_until=future)) is True

    def test_fresh_until_expired(self):
        from datetime import datetime, timedelta, timezone
        past = (datetime.now(tz=timezone.utc) - timedelta(hours=2)).isoformat()
        assert capabilities_cache_is_fresh(self._caps(fresh_until=past)) is False

    def test_detected_at_fallback_window(self):
        """旧缓存没有 fresh_until：detected_at + 1h 内有效，过期后重探。"""
        from datetime import datetime, timedelta, timezone
        recent = (datetime.now(tz=timezone.utc) - timedelta(minutes=30)).isoformat()
        old = (datetime.now(tz=timezone.utc) - timedelta(hours=2)).isoformat()
        assert capabilities_cache_is_fresh(self._caps(detected_at=recent)) is True
        assert capabilities_cache_is_fresh(self._caps(detected_at=old)) is False

    @pytest.mark.asyncio
    async def test_stale_cache_triggers_reprobe(self):
        """run_full_probe 命中过期缓存时应重新探测而非直接返回。"""
        from datetime import datetime, timedelta, timezone
        expired = self._caps(
            fresh_until=(datetime.now(tz=timezone.utc) - timedelta(hours=2)).isoformat(),
        )
        with patch("excelmanus.model_probe.load_capabilities", return_value=expired), \
             patch("excelmanus.model_probe.probe_health", new=AsyncMock(return_value=(True, ""))), \
             patch("excelmanus.model_probe.probe_tool_calling", new=AsyncMock(return_value=(False, ""))), \
             patch("excelmanus.model_probe.probe_vision", new=AsyncMock(return_value=(False, ""))), \
             patch("excelmanus.model_probe.probe_thinking", new=AsyncMock(return_value=(False, "", ""))):
            caps = await run_full_probe(
                client=MagicMock(), model="m", base_url="http://x/v1",
                skip_if_cached=True, db=MagicMock(),
            )

        assert caps.source == "auto_probe"
        assert caps.supports_tool_calling is False


# ── thinking 流式探测超时 ─────────────────────────────────


class TestThinkingStreamTimeout:
    """_try_thinking_stream 的超时必须覆盖消费阶段，而非只覆盖建流。"""

    @pytest.mark.asyncio
    async def test_consume_phase_timeout(self):
        """建流成功但 chunk 迟迟不到 → 消费超时返回 (False, timeout err)。"""
        closed = False

        class _TrickleStream:
            def __aiter__(self):
                return self._gen()

            async def _gen(self):
                yield SimpleNamespace()  # 无 choices / delta → 继续等待
                await asyncio.sleep(60)

            async def aclose(self):
                nonlocal closed
                closed = True

        client = MagicMock()
        client.chat.completions.create = AsyncMock(return_value=_TrickleStream())

        ok, err = await _try_thinking_stream(
            client, "m", [{"role": "user", "content": "hi"}],
            timeout=0.05, extra_kwargs={},
        )

        assert ok is False
        assert "timeout" in err
        assert closed is True

    @pytest.mark.asyncio
    async def test_responses_probe_omits_max_tokens(self):
        """OpenAIResponsesClient 的 thinking 探测不得带 max_tokens。"""
        client = OpenAIResponsesClient.__new__(OpenAIResponsesClient)

        class _EmptyStream:
            def __aiter__(self):
                return self._gen()

            async def _gen(self):
                return
                yield  # pragma: no cover

        create = AsyncMock(return_value=_EmptyStream())
        client.chat = SimpleNamespace(completions=SimpleNamespace(create=create))

        ok, _err = await _try_thinking_stream(
            client, "gpt-6-astra", [{"role": "user", "content": "hi"}],
            timeout=1.0, extra_kwargs={},
        )

        assert ok is False
        assert "max_tokens" not in create.await_args.kwargs


# ── 前端兼容性测试 ─────────────────────────────────────────


class TestFrontendCompatibility:
    """确认 healthy=None 不会导致前端显示"不可用"。"""

    def test_healthy_none_not_equal_false(self):
        """前端 JS: capsMap[m]?.healthy === false → None 不匹配。"""
        caps = ModelCapabilities(model="test", base_url="http://x")
        caps.healthy = None
        d = caps.to_dict()
        # JSON 中 None → null，JS 中 null === false → false
        assert d["healthy"] is None
        assert d["healthy"] is not False

    def test_healthy_false_is_unhealthy(self):
        caps = ModelCapabilities(model="test", base_url="http://x")
        caps.healthy = False
        d = caps.to_dict()
        assert d["healthy"] is False

    def test_healthy_true_is_healthy(self):
        caps = ModelCapabilities(model="test", base_url="http://x")
        caps.healthy = True
        d = caps.to_dict()
        assert d["healthy"] is True
