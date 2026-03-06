"""probe_health 瞬时错误 vs 永久错误分类测试。

验证修复：probe_health 对瞬时错误（超时、限流、网络抖动）返回 (None, err)，
对永久性错误（认证失败、模型不存在、额度不足）返回 (False, err)，
run_full_probe 不持久化瞬时错误的 unhealthy 结果。
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from excelmanus.model_probe import (
    ModelCapabilities,
    _is_permanent_health_failure,
    probe_health,
    run_full_probe,
)


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
