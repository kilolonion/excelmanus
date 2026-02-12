"""日志配置模块：分级日志与敏感信息脱敏。"""

from __future__ import annotations

import logging
import re

# 日志器名称常量
LOGGER_NAME = "excelmanus"

# ── 脱敏正则 ──────────────────────────────────────────────

# API Key 模式：EXCELMANUS_API_KEY=xxx 或 api_key=xxx 或 sk-xxx 等
_API_KEY_PATTERNS: list[re.Pattern[str]] = [
    # 环境变量赋值形式
    re.compile(
        r"(EXCELMANUS_API_KEY\s*[=:]\s*)\S+",
        re.IGNORECASE,
    ),
    # 通用 api_key / api-key 参数
    re.compile(
        r"(api[_-]?key\s*[=:]\s*)\S+",
        re.IGNORECASE,
    ),
    # sk- 开头的 OpenAI 风格密钥
    re.compile(r"\bsk-[A-Za-z0-9]{8,}\b"),
]

# Bearer Token
_BEARER_PATTERN = re.compile(
    r"(Bearer\s+)\S+",
    re.IGNORECASE,
)

# Authorization 头
_AUTH_HEADER_PATTERN = re.compile(
    r"(Authorization\s*[=:]\s*)\S+",
    re.IGNORECASE,
)

# Cookie 值
_COOKIE_PATTERN = re.compile(
    r"(Cookie\s*[=:]\s*)\S+",
    re.IGNORECASE,
)

# 绝对路径（Unix / Windows）
_ABS_PATH_PATTERN = re.compile(
    r"(?<![:/\w])/(?!/)(?:[\w.\-]+/)+[\w.\-]+|(?<!\w)[A-Z]:\\(?:[\w.\-]+\\)+[\w.\-]+",
)


def _sanitize(text: str) -> str:
    """对文本中的敏感信息进行脱敏处理。"""
    # API Key 脱敏
    for pattern in _API_KEY_PATTERNS:
        if pattern.groups:
            text = pattern.sub(r"\1***", text)
        else:
            text = pattern.sub("***", text)

    # Bearer Token 脱敏
    text = _BEARER_PATTERN.sub(r"\1***", text)

    # Authorization 头脱敏
    text = _AUTH_HEADER_PATTERN.sub(r"\1***", text)

    # Cookie 脱敏
    text = _COOKIE_PATTERN.sub(r"\1***", text)

    # 绝对路径脱敏：保留文件名，隐藏目录结构
    def _mask_path(match: re.Match[str]) -> str:
        path = match.group(0)
        sep = "\\" if "\\" in path else "/"
        parts = path.split(sep)
        filename = parts[-1] if parts else path
        return f"<path>/{filename}"

    text = _ABS_PATH_PATTERN.sub(_mask_path, text)

    return text


class SanitizingFormatter(logging.Formatter):
    """自动脱敏的日志格式化器。"""

    def format(self, record: logging.LogRecord) -> str:
        original = super().format(record)
        return _sanitize(original)


# ── 工具调用日志辅助 ──────────────────────────────────────

_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s - %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logging(level: str = "INFO") -> logging.Logger:
    """配置并返回 excelmanus 根日志器。

    Args:
        level: 日志级别字符串，支持 DEBUG/INFO/WARNING/ERROR。

    Returns:
        配置好的 Logger 实例。
    """
    level_upper = level.upper()
    numeric_level = getattr(logging, level_upper, logging.INFO)

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(numeric_level)

    # 避免重复添加 handler
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setLevel(numeric_level)
        formatter = SanitizingFormatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    else:
        # 更新已有 handler 的级别
        for h in logger.handlers:
            h.setLevel(numeric_level)

    # 阻止日志向上传播到 root logger
    logger.propagate = False

    return logger


def get_logger(name: str | None = None) -> logging.Logger:
    """获取 excelmanus 命名空间下的子日志器。

    Args:
        name: 子模块名称，如 "engine"、"skills.data"。
              为 None 时返回根日志器。
    """
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
    # DEBUG：完整详情
    logger.debug(
        "工具调用 [%s] 参数: %s | 结果: %s",
        tool_name,
        arguments,
        error if error else (result or "无"),
    )
