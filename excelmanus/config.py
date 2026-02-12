"""配置管理模块：加载环境变量、.env 文件和默认值。"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


class ConfigError(Exception):
    """配置缺失或校验失败时抛出的异常。"""


# Base URL 合法性正则：仅接受 http:// 或 https:// 开头的 URL
_URL_PATTERN = re.compile(r"^https?://[^\s/$.?#].[^\s]*$", re.IGNORECASE)


@dataclass(frozen=True)
class ExcelManusConfig:
    """不可变的全局配置对象。"""

    api_key: str
    base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    model: str = "qwen-max-latest"
    max_iterations: int = 20
    max_consecutive_failures: int = 3
    session_ttl_seconds: int = 1800
    max_sessions: int = 1000
    workspace_root: str = "."
    log_level: str = "INFO"


def _parse_int(value: str | None, name: str, default: int) -> int:
    """将字符串解析为正整数，无效时抛出 ConfigError。"""
    if value is None:
        return default
    try:
        result = int(value)
    except (ValueError, TypeError):
        raise ConfigError(f"配置项 {name} 必须为整数，当前值: {value!r}")
    if result <= 0:
        raise ConfigError(f"配置项 {name} 必须为正整数，当前值: {result}")
    return result


def _validate_base_url(url: str) -> None:
    """校验 Base URL 为合法的 HTTP/HTTPS URL。"""
    if not _URL_PATTERN.match(url):
        raise ConfigError(
            f"EXCELMANUS_BASE_URL 必须为合法的 HTTP/HTTPS URL，当前值: {url!r}"
        )


def load_config() -> ExcelManusConfig:
    """加载配置。优先级：环境变量 > .env 文件 > 默认值。

    API Key 为必填项，缺失时抛出 ConfigError。
    """
    # 先加载 .env 文件（不覆盖已有环境变量）
    dotenv_path = Path.cwd() / ".env"
    load_dotenv(dotenv_path=dotenv_path, override=False)

    # 读取各配置项
    api_key = os.environ.get("EXCELMANUS_API_KEY")
    if not api_key:
        raise ConfigError(
            "缺少必填配置项 EXCELMANUS_API_KEY。"
            "请通过环境变量或 .env 文件设置该值。"
        )

    base_url = os.environ.get(
        "EXCELMANUS_BASE_URL",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
    )
    _validate_base_url(base_url)

    model = os.environ.get("EXCELMANUS_MODEL", "qwen-max-latest")

    max_iterations = _parse_int(
        os.environ.get("EXCELMANUS_MAX_ITERATIONS"), "EXCELMANUS_MAX_ITERATIONS", 20
    )
    max_consecutive_failures = _parse_int(
        os.environ.get("EXCELMANUS_MAX_CONSECUTIVE_FAILURES"),
        "EXCELMANUS_MAX_CONSECUTIVE_FAILURES",
        3,
    )
    session_ttl_seconds = _parse_int(
        os.environ.get("EXCELMANUS_SESSION_TTL_SECONDS"),
        "EXCELMANUS_SESSION_TTL_SECONDS",
        1800,
    )
    max_sessions = _parse_int(
        os.environ.get("EXCELMANUS_MAX_SESSIONS"), "EXCELMANUS_MAX_SESSIONS", 1000
    )

    workspace_root = os.environ.get("EXCELMANUS_WORKSPACE_ROOT", ".")
    log_level = os.environ.get("EXCELMANUS_LOG_LEVEL", "INFO").upper()

    return ExcelManusConfig(
        api_key=api_key,
        base_url=base_url,
        model=model,
        max_iterations=max_iterations,
        max_consecutive_failures=max_consecutive_failures,
        session_ttl_seconds=session_ttl_seconds,
        max_sessions=max_sessions,
        workspace_root=workspace_root,
        log_level=log_level,
    )
