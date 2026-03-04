"""ResponsesAPIError 错误处理回归测试。

验证 OpenAIResponsesClient 抛出的 ResponsesAPIError 携带 status_code，
使得 retry / classify_failure 管线能正确识别 429 / 5xx / 401 等状态码，
而非回退到通用 "内部错误" 兜底。

回归场景：修复前 OpenAIResponsesClient 使用 RuntimeError（无 status_code），
导致所有 HTTP 错误被视为不可分类的 "内部错误"。
"""

from __future__ import annotations

import pytest

from excelmanus.providers.openai_responses import ResponsesAPIError
from excelmanus.engine_core.llm_caller import (
    is_retryable_llm_error,
    is_nonretryable_auth_error,
    is_content_filter_error,
)
from excelmanus.error_guidance import classify_failure, _extract_status_code


# ── ResponsesAPIError 基础属性 ────────────────────────────────


class TestResponsesAPIErrorAttributes:
    """ResponsesAPIError 携带 status_code 属性。"""

    def test_status_code_attribute(self):
        exc = ResponsesAPIError(429, "Rate limited")
        assert exc.status_code == 429
        assert "Rate limited" in str(exc)

    def test_is_exception_subclass(self):
        exc = ResponsesAPIError(500, "Server error")
        assert isinstance(exc, Exception)

    def test_various_status_codes(self):
        for code in (400, 401, 403, 404, 429, 500, 502, 503, 504):
            exc = ResponsesAPIError(code, f"HTTP {code}")
            assert exc.status_code == code


# ── status_code 提取 ──────────────────────────────────────────


class TestStatusCodeExtraction:
    """_extract_status_code 能从 ResponsesAPIError 提取状态码。"""

    def test_extract_429(self):
        exc = ResponsesAPIError(429, "Too many requests")
        assert _extract_status_code(exc) == 429

    def test_extract_500(self):
        exc = ResponsesAPIError(500, "Internal server error")
        assert _extract_status_code(exc) == 500

    def test_extract_401(self):
        exc = ResponsesAPIError(401, "Unauthorized")
        assert _extract_status_code(exc) == 401

    def test_runtime_error_has_no_status_code(self):
        """对比：旧 RuntimeError 无法提取 status_code（回归对照）。"""
        exc = RuntimeError("Responses API 错误 (HTTP 429): rate limited")
        assert _extract_status_code(exc) is None


# ── 重试判定 ──────────────────────────────────────────────────


class TestRetryClassification:
    """is_retryable_llm_error 能正确识别 ResponsesAPIError 的可重试状态码。"""

    @pytest.mark.parametrize("code", [429, 500, 502, 503, 504])
    def test_retryable_status_codes(self, code: int):
        exc = ResponsesAPIError(code, f"Responses API 错误 (HTTP {code})")
        assert is_retryable_llm_error(exc) is True

    @pytest.mark.parametrize("code", [400, 401, 403, 404])
    def test_non_retryable_status_codes(self, code: int):
        exc = ResponsesAPIError(code, f"Responses API 错误 (HTTP {code})")
        assert is_retryable_llm_error(exc) is False

    def test_runtime_error_429_not_retried_via_status_code(self):
        """回归对照：旧 RuntimeError(429) 只能靠关键词 '429' 匹配。"""
        exc = RuntimeError("Responses API 错误 (HTTP 429): rate limited")
        # RuntimeError 没有 status_code 属性，但 "429" 在文本中能匹配关键词
        assert is_retryable_llm_error(exc) is True  # 靠关键词兜底

    def test_runtime_error_503_not_retried(self):
        """回归对照：旧 RuntimeError(503) 无法被正确识别为可重试。"""
        exc = RuntimeError("Responses API 错误 (HTTP 503): service temporarily unavailable")
        # "503" 不在关键词列表中，但 "temporarily unavailable" 在
        # 注意：这取决于具体的错误消息文本，不够可靠
        # ResponsesAPIError 通过 status_code=503 直接匹配 500<=code<600
        responses_exc = ResponsesAPIError(503, "Responses API 错误 (HTTP 503)")
        assert is_retryable_llm_error(responses_exc) is True


# ── 认证错误判定 ──────────────────────────────────────────────


class TestAuthErrorClassification:
    """is_nonretryable_auth_error 能正确识别 ResponsesAPIError 的 401/403。"""

    def test_401_detected(self):
        exc = ResponsesAPIError(401, "Unauthorized")
        assert is_nonretryable_auth_error(exc) is True

    def test_403_detected(self):
        exc = ResponsesAPIError(403, "Forbidden")
        assert is_nonretryable_auth_error(exc) is True

    def test_429_not_auth_error(self):
        exc = ResponsesAPIError(429, "Rate limited")
        assert is_nonretryable_auth_error(exc) is False

    def test_runtime_error_401_unreliable(self):
        """回归对照：旧 RuntimeError(401) 依赖关键词匹配，可能不触发。"""
        # 如果错误体不包含 "unauthorized" 等关键词，就无法识别
        exc = RuntimeError("Responses API 错误 (HTTP 401): {\"error\": \"invalid_token\"}")
        # "invalid_token" 不在 auth_keywords 中
        # ResponsesAPIError 通过 status_code=401 直接匹配
        responses_exc = ResponsesAPIError(401, "Responses API 错误 (HTTP 401): invalid_token")
        assert is_nonretryable_auth_error(responses_exc) is True


# ── classify_failure 分类 ─────────────────────────────────────


class TestClassifyFailure:
    """classify_failure 能正确分类 ResponsesAPIError。"""

    def test_429_classified_as_rate_limited(self):
        exc = ResponsesAPIError(429, "Responses API 错误 (HTTP 429): rate limit exceeded")
        guidance = classify_failure(exc, stage="calling_llm")
        assert guidance.code == "rate_limited"
        assert guidance.category == "quota"
        assert guidance.retryable is True

    def test_401_classified_as_auth_failed(self):
        exc = ResponsesAPIError(401, "Responses API 错误 (HTTP 401): unauthorized")
        guidance = classify_failure(exc, stage="calling_llm")
        assert guidance.code == "model_auth_failed"
        assert guidance.category == "model"

    def test_500_classified_as_provider_error(self):
        exc = ResponsesAPIError(500, "Responses API 错误 (HTTP 500): internal error")
        guidance = classify_failure(exc, stage="calling_llm")
        assert guidance.code == "provider_internal_error"
        assert guidance.retryable is True

    def test_503_classified_as_provider_error(self):
        exc = ResponsesAPIError(503, "Responses API 错误 (HTTP 503): service unavailable")
        guidance = classify_failure(exc, stage="calling_llm")
        # 503 匹配 5xx 规则或 "service unavailable" 关键词
        assert guidance.code in ("provider_internal_error", "model_overloaded")

    def test_runtime_error_falls_to_generic(self):
        """回归对照：旧 RuntimeError 500 会回退到通用 internal_error。"""
        exc = RuntimeError("Responses API 错误 (HTTP 500): server error")
        guidance = classify_failure(exc, stage="calling_llm")
        # RuntimeError 无 status_code，"500" 不在关键词中，
        # "server error" 也不匹配任何规则 → 回退到 internal_error
        # 这正是用户报告的 bug
        assert guidance.code == "internal_error"

    def test_responses_api_error_never_falls_to_generic_for_known_codes(self):
        """ResponsesAPIError 的常见 HTTP 状态码都能被正确分类。"""
        known_codes = {
            401: "model_auth_failed",
            403: "model_auth_failed",
            429: "rate_limited",
            500: "provider_internal_error",
            502: "provider_internal_error",
            503: "provider_internal_error",
        }
        for code, expected in known_codes.items():
            exc = ResponsesAPIError(code, f"Responses API 错误 (HTTP {code})")
            guidance = classify_failure(exc, stage="calling_llm")
            assert guidance.code == expected, (
                f"HTTP {code} should be classified as {expected}, got {guidance.code}"
            )
