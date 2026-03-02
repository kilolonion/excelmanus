"""错误归因器单元测试 — 验证 classify_failure 的分类逻辑。"""

from __future__ import annotations

import uuid

import pytest

from excelmanus.error_guidance import (
    FailureGuidance,
    classify_failure,
    classify_workspace_full,
)


# ── 辅助：构造带 status_code 的异常 ─────────────────────────


class _FakeHTTPError(Exception):
    def __init__(self, status_code: int, message: str = ""):
        self.status_code = status_code
        super().__init__(message or f"HTTP {status_code}")


class _FakeAuthenticationError(Exception):
    """模拟 openai.AuthenticationError。"""
    pass


class _FakeRateLimitError(Exception):
    """模拟 openai.RateLimitError。"""
    pass


class _FakeNotFoundError(Exception):
    """模拟 openai.NotFoundError。"""
    pass


class _FakeAPITimeoutError(Exception):
    """模拟 openai.APITimeoutError。"""
    pass


class _FakeAPIConnectionError(Exception):
    """模拟 openai.APIConnectionError。"""
    pass


# ── HTTP 状态码分类 ──────────────────────────────────────────


class TestClassifyByStatusCode:
    def test_401_auth_failed(self):
        g = classify_failure(_FakeHTTPError(401, "Unauthorized"))
        assert g.category == "model"
        assert g.code == "model_auth_failed"
        assert g.retryable is False

    def test_403_auth_failed(self):
        g = classify_failure(_FakeHTTPError(403, "Forbidden"))
        assert g.category == "model"
        assert g.code == "model_auth_failed"

    def test_402_quota_exceeded(self):
        g = classify_failure(_FakeHTTPError(402, "Payment Required"))
        assert g.category == "quota"
        assert g.code == "quota_exceeded"
        assert g.retryable is False

    def test_404_model_not_found(self):
        g = classify_failure(_FakeHTTPError(404, "Not Found"))
        assert g.category == "model"
        assert g.code == "model_not_found"
        assert g.retryable is False

    def test_429_rate_limited(self):
        g = classify_failure(_FakeHTTPError(429, "Too Many Requests"))
        assert g.category == "quota"
        assert g.code == "rate_limited"
        assert g.retryable is True

    def test_500_provider_internal(self):
        g = classify_failure(_FakeHTTPError(500, "Internal Server Error"))
        assert g.category == "model"
        assert g.code == "provider_internal_error"
        assert g.retryable is True
        assert "500" in g.message

    def test_502_provider_internal(self):
        g = classify_failure(_FakeHTTPError(502, "Bad Gateway"))
        assert g.category == "model"
        assert g.code == "provider_internal_error"
        assert g.retryable is True

    def test_503_provider_internal(self):
        g = classify_failure(_FakeHTTPError(503, "Service Unavailable"))
        assert g.category == "model"
        assert g.code == "provider_internal_error"
        assert g.retryable is True


# ── 异常类名分类 ─────────────────────────────────────────────


class TestClassifyByExceptionClassName:
    def test_authentication_error(self):
        exc = _FakeAuthenticationError("Invalid API Key")
        g = classify_failure(exc)
        assert g.category == "model"
        assert g.code == "model_auth_failed"

    def test_rate_limit_error(self):
        exc = _FakeRateLimitError("Rate limit exceeded")
        g = classify_failure(exc)
        assert g.category == "quota"
        assert g.code == "rate_limited"
        assert g.retryable is True

    def test_not_found_error(self):
        exc = _FakeNotFoundError("Model not found")
        g = classify_failure(exc)
        assert g.category == "model"
        assert g.code == "model_not_found"

    def test_timeout_error(self):
        exc = _FakeAPITimeoutError("Request timed out")
        g = classify_failure(exc)
        assert g.category == "transport"
        assert g.code == "connect_timeout"
        assert g.retryable is True

    def test_connection_error(self):
        exc = _FakeAPIConnectionError("Connection refused")
        g = classify_failure(exc)
        assert g.category == "transport"
        assert g.code == "network_error"
        assert g.retryable is True


# ── 关键词分类 ───────────────────────────────────────────────


class TestClassifyByKeyword:
    def test_insufficient_quota(self):
        g = classify_failure(Exception("insufficient quota for this request"))
        assert g.category == "quota"
        assert g.code == "quota_exceeded"

    def test_billing(self):
        g = classify_failure(Exception("billing issue on your account"))
        assert g.category == "quota"
        assert g.code == "quota_exceeded"

    def test_invalid_api_key(self):
        g = classify_failure(Exception("invalid api key provided"))
        assert g.category == "model"
        assert g.code == "model_auth_failed"

    def test_model_not_found_keyword(self):
        g = classify_failure(Exception("The model `gpt-5` does not exist"))
        assert g.category == "model"
        assert g.code == "model_not_found"

    def test_connection_refused(self):
        g = classify_failure(Exception("connection refused by host"))
        assert g.category == "transport"
        assert g.code == "network_error"

    def test_timeout_keyword(self):
        g = classify_failure(Exception("Request timed out after 30s"))
        assert g.category == "transport"
        assert g.code == "connect_timeout"

    def test_overloaded(self):
        g = classify_failure(Exception("The server is overloaded"))
        assert g.category == "model"
        assert g.code == "model_overloaded"
        assert g.retryable is True


# ── 兜底 ─────────────────────────────────────────────────────


class TestClassifyFallback:
    def test_unknown_error(self):
        g = classify_failure(ValueError("some random error"))
        assert g.category == "unknown"
        assert g.code == "internal_error"
        assert g.retryable is False


# ── 输出验证 ──────────────────────────────────────────────────


class TestOutputShape:
    def test_actions_retryable(self):
        g = classify_failure(_FakeHTTPError(429))
        assert g.retryable is True
        action_types = [a["type"] for a in g.actions]
        assert "retry" in action_types

    def test_actions_not_retryable(self):
        g = classify_failure(_FakeHTTPError(401))
        assert g.retryable is False
        action_types = [a["type"] for a in g.actions]
        assert "retry" not in action_types
        assert "open_settings" in action_types

    def test_diagnostic_id_is_uuid(self):
        g = classify_failure(Exception("test"))
        # 应能解析为合法 UUID
        parsed = uuid.UUID(g.diagnostic_id)
        assert str(parsed) == g.diagnostic_id

    def test_message_no_sensitive_info(self):
        """message 不应包含文件路径或堆栈信息。"""
        exc = Exception("/usr/local/lib/python3.12/site-packages/openai/main.py line 42")
        g = classify_failure(exc)
        assert "/usr/local/" not in g.message
        assert "site-packages" not in g.message

    def test_stage_and_provider_passthrough(self):
        g = classify_failure(
            _FakeHTTPError(401),
            stage="calling_llm",
            provider="openai",
            model="gpt-4o-mini",
        )
        assert g.stage == "calling_llm"
        assert g.provider == "openai"
        assert g.model == "gpt-4o-mini"

    def test_to_dict(self):
        g = classify_failure(_FakeHTTPError(429))
        d = g.to_dict()
        assert isinstance(d, dict)
        assert d["category"] == "quota"
        assert d["code"] == "rate_limited"
        assert isinstance(d["actions"], list)


# ── classify_workspace_full ──────────────────────────────────


class TestClassifyWorkspaceFull:
    def test_basic(self):
        g = classify_workspace_full(stage="initializing", detail="文件数 50/50")
        assert g.category == "quota"
        assert g.code == "workspace_full"
        assert g.retryable is False
        assert "50/50" in g.message

    def test_no_detail(self):
        g = classify_workspace_full()
        assert g.code == "workspace_full"
        assert g.diagnostic_id  # 非空
