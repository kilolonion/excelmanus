"""模型标识归一化与匹配。

为上下文窗口、弃用替代、视觉关键词等内置表提供统一匹配：
同一模型 ID 的点号/横线/下划线写法、provider 前缀（``openai/``）
与 Bedrock 命名空间（``us.anthropic.``）在此归一后等价。

本模块只依赖标准库，不得反向依赖其它 excelmanus 模块（避免循环导入）。
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from functools import lru_cache
from typing import TypeVar

V = TypeVar("V")

# 空格/下划线/点号/冒号/@ 统一视为 token 分隔符
_SEPARATOR_RE = re.compile(r"[\s_.:@]+")
# 字母 → 数字 边界（仅此方向，"gpt-4o" 的 4o 不拆分）
_LETTER_TO_DIGIT_RE = re.compile(r"(?<=[a-z])(?=\d)")
_DASH_RUN_RE = re.compile(r"-+")
# Bedrock 风格点分命名空间（点号后必须是字母，避免误剥版本号如 gpt-4.1）
_NAMESPACE_RE = re.compile(r"^[a-z0-9-]+\.(?=[a-z])")


def normalize_model_tokens(model: str) -> str:
    """归一化模型标识：分隔符统一为 ``-``，字母→数字边界补 ``-``。"""
    text = (model or "").lower()
    text = _SEPARATOR_RE.sub("-", text)
    text = _LETTER_TO_DIGIT_RE.sub("-", text)
    text = _DASH_RUN_RE.sub("-", text)
    return text.strip("-")


def strip_namespace(model: str) -> str:
    """循环剥掉 Bedrock 风格点分命名空间（如 ``us.anthropic.``、``xai.``）。"""
    text = (model or "").strip().lower()
    while True:
        match = _NAMESPACE_RE.match(text)
        if not match:
            return text
        text = text[match.end():]


@lru_cache(maxsize=1024)
def model_match_candidates(model: str) -> tuple[str, ...]:
    """按顺序生成去重后的归一化候选：raw、`/` 尾段、去命名空间后的两者。"""
    raw = (model or "").strip().lower()
    tail = raw.rsplit("/", 1)[-1] if "/" in raw else raw
    forms = (raw, tail, strip_namespace(raw), strip_namespace(tail))
    seen: set[str] = set()
    candidates: list[str] = []
    for form in forms:
        normalized = normalize_model_tokens(form)
        if normalized and normalized not in seen:
            seen.add(normalized)
            candidates.append(normalized)
    return tuple(candidates)


def normalize_lookup_table(table: Mapping[str, V]) -> dict[str, tuple[str, V]]:
    """把 ``{原始键: 值}`` 转为 ``{归一化键: (原始键, 值)}``。

    两个原始键归一化后相同但值不同属于数据错误，在 import 时直接抛错。
    """
    lookup: dict[str, tuple[str, V]] = {}
    for raw_key, value in table.items():
        norm_key = normalize_model_tokens(raw_key)
        existing = lookup.get(norm_key)
        if existing is not None:
            if existing[1] != value:
                raise ValueError(
                    f"模型表键归一化冲突: {existing[0]!r} 与 {raw_key!r} "
                    f"归一化为 {norm_key!r} 但值不同"
                )
            continue
        lookup[norm_key] = (raw_key, value)
    return lookup


# 紧跟的 1~2 位纯数字段视为版本号（如 kimi-k2.6 的 6）
_VERSION_SEGMENT_RE = re.compile(r"\d{1,2}(?:-|$)")


def longest_prefix_match(
    model: str,
    table: dict[str, tuple[str, V]],
    *,
    version_guard: bool = False,
) -> tuple[str, V] | None:
    """归一化最长前缀匹配；命中条件为 ``candidate == key`` 或 ``key + "-"`` 前缀。

    ``version_guard=True`` 时，前缀命中后紧跟 1~2 位纯数字段（版本号）不算命中；
    更长数字段（8 位日期等）仍算命中。同长度键取先出现的候选。
    """
    best: tuple[str, V] | None = None
    best_len = 0
    for candidate in model_match_candidates(model):
        for norm_key, entry in table.items():
            if len(norm_key) <= best_len:
                continue
            if candidate == norm_key:
                hit = True
            elif candidate.startswith(norm_key + "-"):
                if version_guard:
                    remainder = candidate[len(norm_key) + 1:]
                    hit = _VERSION_SEGMENT_RE.match(remainder) is None
                else:
                    hit = True
            else:
                hit = False
            if hit:
                best = entry
                best_len = len(norm_key)
    return best


def token_sequence_pattern(keywords: Iterable[str]) -> re.Pattern[str]:
    """把关键词编译为 token 序列正则：``(?:^|-)(?:kw1|kw2|...)(?:-|$)``。"""
    normalized = {
        kw for kw in (normalize_model_tokens(k) for k in keywords) if kw
    }
    alternatives = "|".join(
        re.escape(kw) for kw in sorted(normalized, key=len, reverse=True)
    )
    return re.compile(rf"(?:^|-)(?:{alternatives})(?:-|$)")


def matches_token_sequence(model: str, pattern: re.Pattern[str]) -> bool:
    """任一归一化候选命中 token 序列正则即返回 True。"""
    return any(
        pattern.search(candidate) for candidate in model_match_candidates(model)
    )


def has_token_prefix(model: str, prefixes: str | Iterable[str]) -> bool:
    """任一归一化候选以任一归一化前缀开头（含 ``-`` 边界）即返回 True。"""
    if isinstance(prefixes, str):
        prefixes = (prefixes,)
    normalized = [
        p for p in (normalize_model_tokens(p) for p in prefixes) if p
    ]
    for candidate in model_match_candidates(model):
        for prefix in normalized:
            if candidate == prefix or candidate.startswith(prefix + "-"):
                return True
    return False
