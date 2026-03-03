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

    def test_408_request_timeout(self):
        g = classify_failure(_FakeHTTPError(408, "Request Timeout"))
        assert g.category == "transport"
        assert g.code == "request_timeout"
        assert g.retryable is True

    def test_413_payload_too_large(self):
        g = classify_failure(_FakeHTTPError(413, "Payload Too Large"))
        assert g.category == "model"
        assert g.code == "payload_too_large"
        assert g.retryable is False

    def test_422_unprocessable(self):
        g = classify_failure(_FakeHTTPError(422, "Unprocessable Entity"))
        assert g.category == "model"
        assert g.code == "invalid_request"
        assert g.retryable is False


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


class _FakeSSLError(Exception):
    """模拟 ssl.SSLError。"""
    pass


class _FakeProxyError(Exception):
    """模拟 urllib3.exceptions.ProxyError。"""
    pass


class _FakeJSONDecodeError(Exception):
    """模拟 json.JSONDecodeError。"""
    pass


class TestClassifyByExceptionClassNameExtended:
    def test_ssl_error(self):
        exc = _FakeSSLError("certificate verify failed")
        g = classify_failure(exc)
        assert g.category == "transport"
        assert g.code == "ssl_error"
        assert g.retryable is False

    def test_proxy_error(self):
        exc = _FakeProxyError("proxy connection refused")
        g = classify_failure(exc)
        assert g.category == "transport"
        assert g.code == "proxy_error"
        assert g.retryable is True

    def test_json_decode_error(self):
        exc = _FakeJSONDecodeError("Expecting value: line 1")
        g = classify_failure(exc)
        assert g.category == "transport"
        assert g.code == "response_parse_error"
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


# ── 新增关键词分类（SSL/代理/内容过滤/流中断/编码/磁盘/权限） ──


class TestClassifyByKeywordExtended:
    @pytest.mark.parametrize("msg,expected_code", [
        ("SSL: certificate verify failed", "ssl_error"),
        ("ssl handshake failure", "ssl_error"),
        ("SSLCertVerificationError occurred", "ssl_error"),
        ("[SSL] connection failed", "ssl_error"),
        ("ssl error during connect", "ssl_error"),
        ("ssl_cert verification failed", "ssl_error"),
    ])
    def test_ssl_keywords(self, msg, expected_code):
        g = classify_failure(Exception(msg))
        assert g.category == "transport"
        assert g.code == expected_code

    @pytest.mark.parametrize("msg", [
        "proxy error: connection refused",
        "tunnel connection failed",
        "proxy connection timeout",
    ])
    def test_proxy_keywords(self, msg):
        g = classify_failure(Exception(msg))
        assert g.category == "transport"
        assert g.code == "proxy_error"
        assert g.retryable is True

    @pytest.mark.parametrize("msg", [
        "content_filter triggered",
        "content policy violation detected",
        "blocked by safety system",
        "responsible_ai_policy violation",
        "The response was flagged",
    ])
    def test_content_filter_keywords(self, msg):
        g = classify_failure(Exception(msg))
        assert g.category == "model"
        assert g.code == "content_filtered"
        assert g.retryable is False

    @pytest.mark.parametrize("msg", [
        "json decode error at position 0",
        "Expecting value: line 1 column 1",
        "invalid json response from server",
    ])
    def test_json_decode_keywords(self, msg):
        g = classify_failure(Exception(msg))
        assert g.category == "transport"
        assert g.code == "response_parse_error"
        assert g.retryable is True

    @pytest.mark.parametrize("msg", [
        "incomplete chunked encoding",
        "IncompleteRead: 0 bytes read",
        "stream ended unexpectedly",
        "RemoteDisconnected: Remote end closed connection",
        "premature end of response",
    ])
    def test_stream_interrupted_keywords(self, msg):
        g = classify_failure(Exception(msg))
        assert g.category == "transport"
        assert g.code == "stream_interrupted"
        assert g.retryable is True

    @pytest.mark.parametrize("msg", [
        "UnicodeDecodeError: 'utf-8' codec can't decode",
        "invalid start byte at position 42",
        "charmap codec can't encode character",
    ])
    def test_encoding_keywords(self, msg):
        g = classify_failure(Exception(msg))
        assert g.category == "config"
        assert g.code == "encoding_error"
        assert g.retryable is False

    @pytest.mark.parametrize("msg", [
        "OSError: [Errno 28] No space left on device",
        "disk full",
        "disk quota used up",
        "磁盘空间不足",
    ])
    def test_disk_full_keywords(self, msg):
        g = classify_failure(Exception(msg))
        assert g.category == "config"
        assert g.code == "disk_full"
        assert g.retryable is False

    @pytest.mark.parametrize("msg", [
        "PermissionError: [Errno 13] Permission denied",
        "operation not permitted",
        "[Errno 13] permission denied: '/workspace/data.xlsx'",
    ])
    def test_permission_denied_keywords(self, msg):
        g = classify_failure(Exception(msg))
        assert g.category == "config"
        assert g.code == "permission_denied"
        assert g.retryable is False


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


# ── 会话相关错误分类 ─────────────────────────────────────────


class _FakeSessionBusyError(Exception):
    """模拟 excelmanus.session.SessionBusyError。"""
    pass


class _FakeSessionLimitExceededError(Exception):
    """模拟 excelmanus.session.SessionLimitExceededError。"""
    pass


class _FakeSessionNotFoundError(Exception):
    """模拟 excelmanus.session.SessionNotFoundError。"""
    pass


class TestClassifySessionErrors:
    def test_session_busy(self):
        exc = _FakeSessionBusyError("会话正在处理中")
        g = classify_failure(exc)
        assert g.category == "transport"
        assert g.code == "session_busy"
        assert g.retryable is True
        assert "retry" in [a["type"] for a in g.actions]

    def test_session_limit_exceeded(self):
        exc = _FakeSessionLimitExceededError("会话数量已达上限")
        g = classify_failure(exc)
        assert g.category == "quota"
        assert g.code == "session_limit"
        assert g.retryable is True

    def test_session_not_found(self):
        exc = _FakeSessionNotFoundError("会话不存在")
        g = classify_failure(exc)
        assert g.category == "config"
        assert g.code == "session_not_found"
        assert g.retryable is False

    def test_real_session_busy_error(self):
        """使用真实的 SessionBusyError 类。"""
        from excelmanus.session import SessionBusyError
        exc = SessionBusyError("会话 'abc' 正在处理中")
        g = classify_failure(exc, stage="initializing")
        assert g.code == "session_busy"
        assert g.stage == "initializing"

    def test_real_session_limit_exceeded_error(self):
        """使用真实的 SessionLimitExceededError 类。"""
        from excelmanus.session import SessionLimitExceededError
        exc = SessionLimitExceededError("会话数量已达上限（10）")
        g = classify_failure(exc)
        assert g.code == "session_limit"

    def test_real_session_not_found_error(self):
        """使用真实的 SessionNotFoundError 类。"""
        from excelmanus.session import SessionNotFoundError
        exc = SessionNotFoundError("会话 'xyz' 不存在")
        g = classify_failure(exc)
        assert g.code == "session_not_found"
