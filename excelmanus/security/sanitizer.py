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

# ── PII 个人身份信息脱敏 ──────────────────────────────────

# 中国大陆手机号（11位，1[3-9]开头）
_PHONE_PATTERN = re.compile(r"(?<![\d])1[3-9]\d{9}(?![\d])")

# 身份证号（18位，末位可为 X/x）
_ID_CARD_PATTERN = re.compile(r"(?<![\d])\d{17}[\dXx](?![\d])")

# 银行卡号（16-19位纯数字，排除已匹配的身份证）
_BANK_CARD_PATTERN = re.compile(r"(?<![\d])\d{16,19}(?![\d])")

# 邮箱地址
_EMAIL_PATTERN = re.compile(
    r"(?<![\w.@])" r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}" r"(?![\w@])"
)


def _mask_phone(match: re.Match[str]) -> str:
    """138****1234"""
    num = match.group(0)
    return num[:3] + "****" + num[7:]


def _mask_id_card(match: re.Match[str]) -> str:
    """320106********1234"""
    num = match.group(0)
    return num[:6] + "********" + num[14:]


def _mask_bank_card(match: re.Match[str]) -> str:
    """6222 **** **** 1234"""
    num = match.group(0)
    return num[:4] + " **** **** " + num[-4:]


def _mask_email(match: re.Match[str]) -> str:
    """u***@example.com"""
    email = match.group(0)
    local, domain = email.rsplit("@", 1)
    if len(local) <= 1:
        masked_local = local + "***"
    else:
        masked_local = local[0] + "***"
    return f"{masked_local}@{domain}"


def _sanitize_pii(text: str) -> str:
    """对文本中的 PII 个人信息做统一脱敏。"""
    # 顺序很重要：先身份证（18位），再银行卡（16-19位），再手机（11位）
    value = _ID_CARD_PATTERN.sub(_mask_id_card, text)
    value = _BANK_CARD_PATTERN.sub(_mask_bank_card, value)
    value = _PHONE_PATTERN.sub(_mask_phone, value)
    value = _EMAIL_PATTERN.sub(_mask_email, value)
    return value


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


def sanitize_sensitive_text(text: str, *, mask_pii: bool = True) -> str:
    """对文本中的敏感信息做统一脱敏。

    Args:
        text: 待脱敏文本。
        mask_pii: 是否同时脱敏 PII 个人信息（手机号/身份证/银行卡/邮箱），默认 True。
    """
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

    if mask_pii:
        value = _sanitize_pii(value)

    return value
