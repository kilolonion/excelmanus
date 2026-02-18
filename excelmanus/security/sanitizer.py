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

# URL 整体匹配，用于保护 URL 内路径不被脱敏
_URL_PATTERN = re.compile(r"https?://\S+", re.IGNORECASE)


def _build_url_spans(text: str) -> list[tuple[int, int]]:
    """返回文本中所有 URL 的 (start, end) 区间列表。"""
    return [m.span() for m in _URL_PATTERN.finditer(text)]


def _mask_path_keep_basename(path_text: str) -> str:
    """将绝对路径脱敏为 <path>/basename。"""
    raw = path_text.rstrip("/\\")
    if not raw:
        return "<path>"
    parts = re.split(r"[\\/]+", raw)
    basename = parts[-1] if parts else ""
    if not basename:
        return "<path>"
    return f"<path>/{basename}"


def _make_path_replacer(url_spans: list[tuple[int, int]]):
    """生成路径脱敏回调，跳过 URL 内部的路径匹配。"""
    def _replacer(match: re.Match[str]) -> str:
        start = match.start()
        for span_start, span_end in url_spans:
            if span_start <= start < span_end:
                return match.group(0)  # 在 URL 内，保留原文
        return _mask_path_keep_basename(match.group(0))
    return _replacer


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
    url_spans = _build_url_spans(value)
    value = _ABS_PATH_PATTERN.sub(_make_path_replacer(url_spans), value)
    return value
