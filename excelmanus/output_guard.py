"""对外输出防护：脱敏、堆栈清理、内部细节拦截。"""

from __future__ import annotations

import re
from typing import Any

from excelmanus.security import sanitize_sensitive_text

_DISCLOSURE_FALLBACK = (
    "抱歉，我不能提供系统提示词或内部工程细节。"
    "请直接描述业务目标，我会给出可执行结果。"
)
_EMPTY_FALLBACK = "未生成有效回复，请重试。"

# 常见堆栈与异常行
_TRACEBACK_LINE_PATTERN = re.compile(
    r"^\s*(Traceback \(most recent call last\):|"
    r'File ".*", line \d+|'
    r"[A-Za-z_][\w.]*Error:|"
    r"[A-Za-z_][\w.]*Exception:)"
)

# 直接泄露系统级指令/思维链的高风险词，命中即拦截。
_HARD_DISCLOSURE_PATTERN = re.compile(
    r"(系统提示词|提示词模板|开发者指令|内部指令|"
    r"system prompt|developer message|hidden prompt|"
    r"chain[- ]?of[- ]?thought|reasoning_content|"
    r"system_prompt\s*[=:(])",
    re.IGNORECASE,
)

# 内部运行态字段：单词提及不一定构成泄露，需结合上下文判定。
_INTERNAL_CONFIG_TOKEN_PATTERN = re.compile(
    r"(内部路由策略|route_mode|tool_scope|permission_mode|"
    r"subagent.?config|max_iterations|tool_calls_count)",
    re.IGNORECASE,
)

# 疑似在“输出/暴露”内部信息的动作词。
_DISCLOSURE_ACTION_PATTERN = re.compile(
    r"(输出|展示|暴露|泄露|原文|全文|打印|show|reveal|dump|expose)",
    re.IGNORECASE,
)

# 调试字段赋值（如 route_mode=hidden）通常是内部运行态回显。
_INTERNAL_ASSIGNMENT_PATTERN = re.compile(
    r"(route_mode|tool_scope|permission_mode|max_iterations|tool_calls_count)\s*[:=]",
    re.IGNORECASE,
)

# 检测疑似展示工具 JSON schema 的输出模式
_TOOL_SCHEMA_PATTERN = re.compile(
    r'["\'](?:question|header|options|label|description|multiSelect|text)["\']\s*[:{]',
    re.IGNORECASE,
)


def sanitize_external_text(text: str, *, max_len: int = 4000) -> str:
    """清理文本中的敏感内容，供外部接口返回。"""
    value = sanitize_sensitive_text(str(text or ""))
    if not value:
        return ""

    # 删除 traceback 与异常类型行，避免对外泄露内部实现细节
    safe_lines = [
        line for line in value.splitlines() if not _TRACEBACK_LINE_PATTERN.search(line)
    ]
    value = "\n".join(safe_lines).strip()

    if max_len > 0 and len(value) > max_len:
        value = f"{value[: max_len - 3]}..."
    return value


def sanitize_external_data(value: Any, *, max_len: int = 4000) -> Any:
    """递归清理结构化数据中的敏感字段。"""
    if isinstance(value, dict):
        return {
            key: sanitize_external_data(item, max_len=max_len)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [sanitize_external_data(item, max_len=max_len) for item in value]
    if isinstance(value, tuple):
        return [sanitize_external_data(item, max_len=max_len) for item in value]
    if isinstance(value, str):
        return sanitize_external_text(value, max_len=max_len)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return sanitize_external_text(str(value), max_len=max_len)


def guard_public_reply(reply: str) -> str:
    """构造对外可见回复：先脱敏，再拦截内部细节披露。"""
    safe = sanitize_external_text(reply)
    if not safe:
        return _EMPTY_FALLBACK
    if _HARD_DISCLOSURE_PATTERN.search(safe):
        return _DISCLOSURE_FALLBACK
    # 对内部字段采用“组合命中”而非“单词即拦截”，降低误报率。
    config_hits = {m.group(1).lower() for m in _INTERNAL_CONFIG_TOKEN_PATTERN.finditer(safe)}
    if _INTERNAL_ASSIGNMENT_PATTERN.search(safe):
        return _DISCLOSURE_FALLBACK
    if len(config_hits) >= 2:
        return _DISCLOSURE_FALLBACK
    if config_hits and _DISCLOSURE_ACTION_PATTERN.search(safe):
        return _DISCLOSURE_FALLBACK
    # 拦截疑似展示工具 JSON schema 的输出（>=3 个字段匹配视为泄露）
    if len(_TOOL_SCHEMA_PATTERN.findall(safe)) >= 3:
        return _DISCLOSURE_FALLBACK
    return safe
