"""日志配置模块：分级日志与敏感信息脱敏。"""

from __future__ import annotations

import logging

from excelmanus.security import sanitize_sensitive_text

# 日志器名称常量
LOGGER_NAME = "excelmanus"


class SanitizingFilter(logging.Filter):
    """在 record 级别做脱敏，避免向上游传播明文。"""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            message = str(record.msg)
        record.msg = sanitize_sensitive_text(message)
        record.args = ()
        return True


# 兼容旧测试与调用方

def _sanitize(text: str) -> str:
    """对文本中的敏感信息进行脱敏处理。"""
    return sanitize_sensitive_text(text)


class SanitizingFormatter(logging.Formatter):
    """自动脱敏的日志格式化器。"""

    def format(self, record: logging.LogRecord) -> str:
        original = super().format(record)
        return sanitize_sensitive_text(original)


# ── 工具调用日志辅助 ──────────────────────────────────────

_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s - %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def _ensure_logger_filter(logger: logging.Logger) -> None:
    if not any(isinstance(f, SanitizingFilter) for f in logger.filters):
        logger.addFilter(SanitizingFilter())


def _ensure_handler_filter(handler: logging.Handler) -> None:
    if not any(isinstance(f, SanitizingFilter) for f in handler.filters):
        handler.addFilter(SanitizingFilter())


def setup_logging(level: str = "INFO") -> logging.Logger:
    """配置并返回 excelmanus 根日志器。"""
    level_upper = level.upper()
    numeric_level = getattr(logging, level_upper, logging.INFO)

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(numeric_level)
    _ensure_logger_filter(logger)

    # 避免重复添加 handler
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setLevel(numeric_level)
        _ensure_handler_filter(handler)
        formatter = SanitizingFormatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    else:
        # 更新已有 handler 的级别
        for h in logger.handlers:
            h.setLevel(numeric_level)
            _ensure_handler_filter(h)

    # 允许传播到 root（便于 caplog/宿主统一采集），
    # 由 handler 级过滤器确保传播前已完成脱敏。
    logger.propagate = True

    return logger


def get_logger(name: str | None = None) -> logging.Logger:
    """获取 excelmanus 命名空间下的子日志器。"""
    if name:
        return logging.getLogger(f"{LOGGER_NAME}.{name}")
    return logging.getLogger(LOGGER_NAME)


def log_tool_call(
    logger: logging.Logger,
    tool_name: str,
    arguments: dict | str,
    result: str | None = None,
    error: str | None = None,
) -> None:
    """记录工具调用信息。

    DEBUG 级别输出完整详情（工具名、参数、返回值）。
    INFO 级别不记录工具级细节，避免噪音。
    """
    logger.debug(
        "工具调用 [%s] 参数: %s | 结果: %s",
        tool_name,
        arguments,
        error if error else (result or "无"),
    )
