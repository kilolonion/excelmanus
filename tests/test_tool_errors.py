"""Tests for excelmanus.engine_core.tool_errors — 错误分类、压缩与重试策略。"""

from __future__ import annotations

import json

import pytest

from excelmanus.engine_core.tool_errors import (
    DEFAULT_RETRY_POLICY,
    RetryPolicy,
    ToolError,
    ToolErrorKind,
    classify_tool_error,
    compact_error,
)


# ── ToolErrorKind 枚举 ──────────────────────────────────────


class TestToolErrorKind:
    def test_values(self):
        assert ToolErrorKind.RETRYABLE.value == "retryable"
        assert ToolErrorKind.PERMANENT.value == "permanent"
        assert ToolErrorKind.NEEDS_HUMAN.value == "needs_human"
        assert ToolErrorKind.CONTEXT_OVERFLOW.value == "overflow"


# ── ToolError 数据类 ──────────────────────────────────────


class TestToolError:
    def test_retryable_flag(self):
        err = ToolError(
            kind=ToolErrorKind.RETRYABLE,
            summary="timeout",
        )
        assert err.retryable is True

    def test_non_retryable_flag(self):
        err = ToolError(
            kind=ToolErrorKind.PERMANENT,
            summary="file not found",
        )
        assert err.retryable is False

    def test_to_compact_str(self):
        err = ToolError(
            kind=ToolErrorKind.PERMANENT,
            summary="文件不存在: test.xlsx",
            suggestion="请检查文件路径",
        )
        result = json.loads(err.to_compact_str())
        assert result["error_kind"] == "permanent"
        assert result["summary"] == "文件不存在: test.xlsx"
        assert result["suggestion"] == "请检查文件路径"

    def test_to_compact_str_no_suggestion(self):
        err = ToolError(
            kind=ToolErrorKind.RETRYABLE,
            summary="timeout",
        )
        result = json.loads(err.to_compact_str())
        assert "suggestion" not in result


# ── classify_tool_error ──────────────────────────────────────


class TestClassifyToolError:
    """测试错误分类器。"""

    # --- RETRYABLE ---

    @pytest.mark.parametrize("error_str", [
        "Connection timeout after 30s",
        "Rate limit exceeded, please retry",
        "HTTP 429 Too Many Requests",
        "HTTP 503 Service Unavailable",
        "Connection reset by peer",
        "ECONNREFUSED",
        "ETIMEDOUT",
        "temporary failure in name resolution",
        "internal server error",
    ])
    def test_retryable_by_string(self, error_str: str):
        result = classify_tool_error(error_str)
        assert result.kind == ToolErrorKind.RETRYABLE, (
            f"Expected RETRYABLE for: {error_str!r}, got {result.kind}"
        )
        assert result.retryable is True

    def test_retryable_by_exception_type(self):
        result = classify_tool_error(TimeoutError("connection timed out"))
        assert result.kind == ToolErrorKind.RETRYABLE

    def test_retryable_by_connection_error(self):
        result = classify_tool_error(ConnectionError("refused"))
        assert result.kind == ToolErrorKind.RETRYABLE

    # --- CONTEXT_OVERFLOW ---

    @pytest.mark.parametrize("error_str", [
        "Result too large to display",
        "结果过大，已截断",
        "Output truncated at 10000 chars",
        "Data exceeds limit of 50000 rows",
        "超出行数限制",
        "MemoryError: unable to allocate",
    ])
    def test_overflow_by_string(self, error_str: str):
        result = classify_tool_error(error_str)
        assert result.kind == ToolErrorKind.CONTEXT_OVERFLOW, (
            f"Expected CONTEXT_OVERFLOW for: {error_str!r}, got {result.kind}"
        )

    # --- NEEDS_HUMAN ---

    @pytest.mark.parametrize("error_str", [
        "Ambiguous column name: 'date'",
        "存在歧义：多个工作表匹配",
        "无法确定目标区域",
        "请用户确认操作范围",
        "Multiple matches found for pattern",
    ])
    def test_needs_human_by_string(self, error_str: str):
        result = classify_tool_error(error_str)
        assert result.kind == ToolErrorKind.NEEDS_HUMAN, (
            f"Expected NEEDS_HUMAN for: {error_str!r}, got {result.kind}"
        )

    # --- PERMANENT (兜底) ---

    @pytest.mark.parametrize("error_str", [
        "FileNotFoundError: test.xlsx",
        "ValueError: invalid range 'Z999:A1'",
        "KeyError: 'sheet1'",
        "PermissionError: access denied",
        "工具执行错误: 参数无效",
    ])
    def test_permanent_by_string(self, error_str: str):
        result = classify_tool_error(error_str)
        assert result.kind == ToolErrorKind.PERMANENT, (
            f"Expected PERMANENT for: {error_str!r}, got {result.kind}"
        )

    # --- 优先级：OVERFLOW > RETRYABLE ---

    def test_overflow_takes_priority_over_retryable(self):
        # 同时匹配 overflow 和 retryable 关键词时，overflow 优先
        result = classify_tool_error("timeout: result too large to process")
        assert result.kind == ToolErrorKind.CONTEXT_OVERFLOW

    # --- summary 截断 ---

    def test_long_error_truncated_in_summary(self):
        long_error = "x" * 500
        result = classify_tool_error(long_error)
        assert len(result.summary) <= 203  # 200 + "..."


# ── compact_error ──────────────────────────────────────────


class TestCompactError:
    def test_with_tool_error(self):
        te = ToolError(
            kind=ToolErrorKind.PERMANENT,
            summary="file not found",
            suggestion="check path",
        )
        result = compact_error("raw error", tool_error=te)
        parsed = json.loads(result)
        assert parsed["error_kind"] == "permanent"

    def test_without_tool_error(self):
        result = compact_error("some short error")
        assert result == "some short error"

    def test_empty_error(self):
        assert compact_error(None) == ""
        assert compact_error("") == ""

    def test_traceback_cleaned(self):
        tb = (
            "Traceback (most recent call last):\n"
            '  File "test.py", line 10, in func\n'
            "    raise ValueError('bad')\n"
            "ValueError: bad"
        )
        result = compact_error(tb)
        assert "Traceback" not in result
        assert "ValueError: bad" in result


# ── RetryPolicy ──────────────────────────────────────────────


class TestRetryPolicy:
    def test_default_policy(self):
        assert DEFAULT_RETRY_POLICY.max_retries == 2
        assert DEFAULT_RETRY_POLICY.base_delay_seconds == 0.5

    def test_delay_exponential_backoff(self):
        policy = RetryPolicy(max_retries=3, base_delay_seconds=1.0, max_delay_seconds=10.0)
        assert policy.delay_for_attempt(0) == 1.0   # 1 * 2^0
        assert policy.delay_for_attempt(1) == 2.0   # 1 * 2^1
        assert policy.delay_for_attempt(2) == 4.0   # 1 * 2^2
        assert policy.delay_for_attempt(3) == 8.0   # 1 * 2^3
        assert policy.delay_for_attempt(4) == 10.0  # capped at max

    def test_delay_capped(self):
        policy = RetryPolicy(base_delay_seconds=2.0, max_delay_seconds=3.0)
        assert policy.delay_for_attempt(0) == 2.0
        assert policy.delay_for_attempt(1) == 3.0  # 4.0 → capped to 3.0
        assert policy.delay_for_attempt(2) == 3.0  # 8.0 → capped to 3.0
