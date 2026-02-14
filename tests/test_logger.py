"""日志模块测试：脱敏行为与日志配置。

覆盖需求：7.8（敏感信息脱敏）
"""

from __future__ import annotations

import logging
import logging.handlers
from io import StringIO

import pytest

from excelmanus.logger import (
    _sanitize,
    SanitizingFormatter,
    setup_logging,
    get_logger,
    log_tool_call,
    LOGGER_NAME,
)


# ══════════════════════════════════════════════════════════
# 脱敏行为单元测试（需求 7.8）
# ══════════════════════════════════════════════════════════


class TestSanitizeApiKey:
    """API Key 脱敏测试。"""

    def test_env_var_assignment(self) -> None:
        """EXCELMANUS_API_KEY=xxx 形式应被脱敏。"""
        text = "EXCELMANUS_API_KEY=sk-abc123def456"
        result = _sanitize(text)
        assert "sk-abc123def456" not in result
        assert "***" in result

    def test_api_key_param(self) -> None:
        """api_key=xxx 形式应被脱敏。"""
        text = "api_key=my-secret-key-12345"
        result = _sanitize(text)
        assert "my-secret-key-12345" not in result
        assert "***" in result

    def test_sk_prefix_token(self) -> None:
        """sk- 开头的 OpenAI 风格密钥应被脱敏。"""
        text = "使用密钥 sk-abcdefghijklmnop 调用 API"
        result = _sanitize(text)
        assert "sk-abcdefghijklmnop" not in result
        assert "***" in result


class TestSanitizeBearerToken:
    """Bearer Token 脱敏测试。"""

    def test_bearer_token(self) -> None:
        """Bearer Token 应被脱敏。"""
        text = "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig"
        result = _sanitize(text)
        assert "eyJhbGciOiJIUzI1NiJ9" not in result
        assert "***" in result


class TestSanitizeAuthHeader:
    """Authorization 头脱敏测试。"""

    def test_auth_header(self) -> None:
        """Authorization 头应被脱敏。"""
        text = "Authorization=Bearer-token-xyz123"
        result = _sanitize(text)
        assert "Bearer-token-xyz123" not in result
        assert "***" in result


class TestSanitizeCookie:
    """Cookie 脱敏测试。"""

    def test_cookie_value(self) -> None:
        """Cookie 值应被脱敏。"""
        text = "Cookie: session_id=abc123xyz789"
        result = _sanitize(text)
        assert "abc123xyz789" not in result
        assert "***" in result

    def test_cookie_line_with_spaces_is_fully_masked(self) -> None:
        """Cookie 行中包含空格时应整行脱敏。"""
        text = "Cookie: session=abc; csrftoken=def"
        result = _sanitize(text)
        assert result == "Cookie: ***"


class TestSanitizeAbsolutePath:
    """绝对路径脱敏测试。"""

    def test_unix_absolute_path(self) -> None:
        """Unix 绝对路径应被脱敏，仅保留文件名。"""
        text = "读取文件 /home/user/documents/secret.xlsx"
        result = _sanitize(text)
        assert "/home/user/documents/" not in result
        assert "secret.xlsx" in result

    def test_windows_absolute_path(self) -> None:
        """Windows 绝对路径应被脱敏，仅保留文件名。"""
        text = r"读取文件 C:\Users\admin\Desktop\data.xlsx"
        result = _sanitize(text)
        assert "Users" not in result
        assert "data.xlsx" in result

    def test_windows_absolute_path_with_lower_drive(self) -> None:
        """Windows 小写盘符路径也应被脱敏。"""
        text = r"读取文件 c:\Users\admin\Desktop\data.xlsx"
        result = _sanitize(text)
        assert r"c:\Users\admin\Desktop\\" not in result
        assert "data.xlsx" in result

    def test_relative_path_not_sanitized(self) -> None:
        """相对路径不应被脱敏。"""
        text = "读取文件 data/sales.xlsx"
        result = _sanitize(text)
        assert "data/sales.xlsx" in result

    def test_url_should_not_be_sanitized_as_path(self) -> None:
        """URL 不应被绝对路径规则误脱敏。"""
        text = "BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1"
        result = _sanitize(text)
        assert result == text


class TestSanitizingFormatter:
    """SanitizingFormatter 集成测试。"""

    def test_formatter_sanitizes_log_record(self) -> None:
        """格式化器应自动对日志记录进行脱敏。"""
        formatter = SanitizingFormatter("%(message)s")
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="api_key=super-secret-key-123",
            args=(),
            exc_info=None,
        )
        output = formatter.format(record)
        assert "super-secret-key-123" not in output
        assert "***" in output


class TestSetupLogging:
    """日志配置测试。"""

    def test_returns_logger(self) -> None:
        """setup_logging 应返回 Logger 实例。"""
        logger = setup_logging("INFO")
        assert isinstance(logger, logging.Logger)
        assert logger.name == LOGGER_NAME

    def test_debug_level(self) -> None:
        """DEBUG 级别应正确设置。"""
        logger = setup_logging("DEBUG")
        assert logger.level == logging.DEBUG

    def test_info_level(self) -> None:
        """INFO 级别应正确设置。"""
        logger = setup_logging("INFO")
        assert logger.level == logging.INFO

    def test_no_duplicate_handlers(self) -> None:
        """多次调用不应重复添加 handler。"""
        logger = setup_logging("INFO")
        handler_count = len(logger.handlers)
        setup_logging("DEBUG")
        assert len(logger.handlers) == handler_count

    def test_record_filter_sanitizes_message_before_root_handler(self) -> None:
        """即使启用 propagate，也不能向 root 泄漏明文。"""
        root = logging.getLogger()
        old_handlers = list(root.handlers)
        old_level = root.level
        stream = StringIO()
        root_handler = logging.StreamHandler(stream)
        root_handler.setFormatter(logging.Formatter("%(message)s"))
        root.handlers = [root_handler]
        root.setLevel(logging.INFO)

        logger = setup_logging("INFO")
        logger.propagate = True
        try:
            logger.info("Authorization: Bearer abc.def api_key=secret")
        finally:
            logger.propagate = False
            root.handlers = old_handlers
            root.setLevel(old_level)

        output = stream.getvalue()
        assert "abc.def" not in output
        assert "secret" not in output
        assert "***" in output


class TestGetLogger:
    """子日志器获取测试。"""

    def test_root_logger(self) -> None:
        """无参数时返回根日志器。"""
        logger = get_logger()
        assert logger.name == LOGGER_NAME

    def test_sub_logger(self) -> None:
        """传入子模块名时返回子日志器。"""
        logger = get_logger("engine")
        assert logger.name == f"{LOGGER_NAME}.engine"


class TestLogToolCall:
    """工具调用日志记录测试。"""

    def test_tool_log_not_emitted_at_info_level(self) -> None:
        """INFO 级别不应输出工具级细节日志。"""
        logger = setup_logging("INFO")
        handler = logging.handlers.MemoryHandler(capacity=100)
        handler.setLevel(logging.DEBUG)
        logger.addHandler(handler)
        try:
            log_tool_call(
                logger, "read_excel", {"path": "data.xlsx"}, result="成功"
            )
            handler.flush()
            assert handler.buffer == []
        finally:
            logger.removeHandler(handler)

    def test_tool_log_emitted_at_debug_level(self) -> None:
        """DEBUG 级别应输出工具调用详情。"""
        logger = setup_logging("DEBUG")
        handler = logging.handlers.MemoryHandler(capacity=100)
        handler.setLevel(logging.DEBUG)
        logger.addHandler(handler)
        try:
            log_tool_call(logger, "write_excel", {}, error="文件不存在")
            handler.flush()
            messages = [r.getMessage() for r in handler.buffer]
            assert any("write_excel" in m for m in messages)
            assert any("文件不存在" in m for m in messages)
        finally:
            logger.removeHandler(handler)
