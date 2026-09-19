"""Normalized cache usage. Unknown is not 0."""

from __future__ import annotations

from typing import Any

from excelmanus.engine_utils import _extract_anthropic_cache_tokens, _usage_token
from excelmanus.request.types import CacheUsage


def _optional_positive(usage: Any, *keys: str) -> int | None:
    if usage is None:
        return None
    seen = False
    best = 0
    for key in keys:
        if isinstance(usage, dict):
            present = key in usage
            value = usage.get(key) if present else None
        else:
            present = hasattr(usage, key)
            value = getattr(usage, key, None) if present else None
        if not present or value is None:
            continue
        seen = True
        try:
            best = max(best, int(value or 0))
        except (TypeError, ValueError):
            continue
    return best if seen else None


def extract_cache_usage(usage: Any) -> CacheUsage:
    prompt = _usage_token(usage, "prompt_tokens") if usage is not None else 0
    details = None
    if usage is not None:
        details = (
            usage.get("prompt_tokens_details")
            if isinstance(usage, dict)
            else getattr(usage, "prompt_tokens_details", None)
        )
    details_hit = _optional_positive(details, "cached_tokens")
    deepseek_hit = _optional_positive(usage, "prompt_cache_hit_tokens")
    creation, read = _extract_anthropic_cache_tokens(usage)
    anthropic_present = False
    if usage is not None:
        if isinstance(usage, dict):
            anthropic_present = (
                "cache_creation_input_tokens" in usage
                or "cache_read_input_tokens" in usage
            )
        else:
            anthropic_present = (
                hasattr(usage, "cache_creation_input_tokens")
                or hasattr(usage, "cache_read_input_tokens")
            )
    hits = [value for value in (details_hit, deepseek_hit) if value is not None]
    if anthropic_present:
        hits.append(read)
    hit = max(hits) if hits else None
    write = creation if anthropic_present else None
    if hit is None:
        reason = "unknown"
    elif hit <= 0:
        reason = "none" if prompt <= 0 else "first_turn"
    else:
        reason = "none"
    return CacheUsage(
        hit=hit,
        write=write,
        prompt_tokens=prompt,
        miss_reason=reason,
    )
