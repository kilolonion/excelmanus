"""统一敏感信息脱敏工具。"""

from __future__ import annotations

import re

# API Key / Token / Header / Cookie
_API_KEY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(EXCELMANUS_API_KEY\s*[=:]\s*)\S+", re.IGNORECASE),
    re.compile(r"(api[_-]?key\s*[=:]\s*)\S+", re.IGNORECASE),
    re.compile(r"\bsk-[A-Za-z0-9]{8,}\b"),
)
_BEARER_PATTERN = re.compile(r"(Bearer\s+)\S+", re.IGNORECASE)
_AUTH_HEADER_PATTERN = re.compile(r"(Authorization\s*[=:]\s*).*$", re.IGNORECASE | re.MULTILINE)
_COOKIE_PATTERN = re.compile(r"(?im)^(Cookie\s*[=:]\s*).*$")

# 绝对路径（Unix / Windows）
_ABS_PATH_PATTERN = re.compile(
    r"(?<![:/\w])/(?!/)(?:[\w.\-]+/)+[\w.\-]+|"
    r"(?<!\w)[A-Za-z]:\\(?:[\w.\-]+\\)+[\w.\-]+"
)


def _mask_path(match: re.Match[str]) -> str:
    path = match.group(0)
    sep = "\\" if "\\" in path else "/"
    parts = path.split(sep)
    filename = parts[-1] if parts else path
    return f"<path>/{filename}"


def sanitize_sensitive_text(text: str) -> str:
    """对文本中的敏感信息做统一脱敏。"""
    value = str(text or "")
    if not value:
        return ""

    for pattern in _API_KEY_PATTERNS:
        if pattern.groups:
            value = pattern.sub(r"\1***", value)
        else:
            value = pattern.sub("***", value)

    # 先脱敏 Bearer，再脱敏 Authorization 头整行，避免产生重复标记
    value = _BEARER_PATTERN.sub(r"\1***", value)
    value = _AUTH_HEADER_PATTERN.sub(r"\1***", value)
    value = _COOKIE_PATTERN.sub(r"\1***", value)
    value = _ABS_PATH_PATTERN.sub(_mask_path, value)
    return value
