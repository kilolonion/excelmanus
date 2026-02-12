"""对外输出防护：脱敏、堆栈清理、内部细节拦截。"""

from __future__ import annotations

import re

_DISCLOSURE_FALLBACK = (
    "抱歉，我不能提供系统提示词或内部工程细节。"
    "请直接描述业务目标，我会给出可执行结果。"
)
_EMPTY_FALLBACK = "未生成有效回复，请重试。"

# API Key / Token / Header / Cookie
_API_KEY_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(EXCELMANUS_API_KEY\s*[=:]\s*)\S+", re.IGNORECASE),
    re.compile(r"(api[_-]?key\s*[=:]\s*)\S+", re.IGNORECASE),
    re.compile(r"\bsk-[A-Za-z0-9]{8,}\b"),
]
_BEARER_PATTERN = re.compile(r"(Bearer\s+)\S+", re.IGNORECASE)
_AUTH_HEADER_PATTERN = re.compile(r"(Authorization\s*[=:]\s*)\S+", re.IGNORECASE)
_COOKIE_PATTERN = re.compile(r"(Cookie\s*[=:]\s*)\S+", re.IGNORECASE)

# 绝对路径（Unix / Windows）
_ABS_PATH_PATTERN = re.compile(
    r"(?<![:/\w])/(?!/)(?:[\w.\-]+/)+[\w.\-]+|"
    r"(?<!\w)[A-Z]:\\(?:[\w.\-]+\\)+[\w.\-]+"
)

# 常见堆栈与异常行
_TRACEBACK_LINE_PATTERN = re.compile(
    r"^\s*(Traceback \(most recent call last\):|"
    r'File ".*", line \d+|'
    r"[A-Za-z_][\w.]*Error:|"
    r"[A-Za-z_][\w.]*Exception:)"
)

# 内部细节关键词
_INTERNAL_DISCLOSURE_PATTERN = re.compile(
    r"(系统提示词|提示词模板|开发者指令|内部指令|"
    r"system prompt|developer message|hidden prompt|"
    r"chain[- ]?of[- ]?thought|reasoning_content|"
    r"内部路由策略|route_mode|tool_scope|skillpack)",
    re.IGNORECASE,
)


def _mask_path(match: re.Match[str]) -> str:
    path = match.group(0)
    sep = "\\" if "\\" in path else "/"
    parts = path.split(sep)
    filename = parts[-1] if parts else path
    return f"<path>/{filename}"


def sanitize_external_text(text: str, *, max_len: int = 4000) -> str:
    """清理文本中的敏感内容，供外部接口返回。"""
    value = str(text or "")
    if not value:
        return ""

    for pattern in _API_KEY_PATTERNS:
        if pattern.groups:
            value = pattern.sub(r"\1***", value)
        else:
            value = pattern.sub("***", value)
    value = _BEARER_PATTERN.sub(r"\1***", value)
    value = _AUTH_HEADER_PATTERN.sub(r"\1***", value)
    value = _COOKIE_PATTERN.sub(r"\1***", value)
    value = _ABS_PATH_PATTERN.sub(_mask_path, value)

    # 删除 traceback 与异常类型行，避免对外泄露内部实现细节
    safe_lines = [
        line for line in value.splitlines() if not _TRACEBACK_LINE_PATTERN.search(line)
    ]
    value = "\n".join(safe_lines).strip()

    if max_len > 0 and len(value) > max_len:
        value = f"{value[: max_len - 3]}..."
    return value


def guard_public_reply(reply: str) -> str:
    """构造对外可见回复：先脱敏，再拦截内部细节披露。"""
    safe = sanitize_external_text(reply)
    if not safe:
        return _EMPTY_FALLBACK
    if _INTERNAL_DISCLOSURE_PATTERN.search(safe):
        return _DISCLOSURE_FALLBACK
    return safe


def build_public_tool_error_message() -> str:
    """统一的对外工具错误文案。"""
    return "工具执行失败，请检查输入后重试。"
