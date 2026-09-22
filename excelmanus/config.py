"""配置管理模块：从主库设置与默认值构建运行时配置。"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from excelmanus.data_home import get_data_home
from excelmanus.model_identity import (
    longest_prefix_match,
    model_match_candidates,
    normalize_lookup_table,
    normalize_model_tokens,
)
class ConfigError(Exception):
    """配置缺失或校验失败时抛出的异常。"""


@dataclass(frozen=True)
class ModelProfile:
    """单个模型配置档案。"""

    name: str  # 用户可见的短名称，如 "gpt5", "qwen", "kimi"
    model: str  # 实际模型标识符
    api_key: str
    base_url: str
    description: str = ""  # 可选描述
    protocol: str = "auto"  # 协议类型：auto|openai|openai_responses|anthropic|gemini
    thinking_mode: str = "auto"  # thinking 参数格式覆盖
    model_family: str = ""  # 实际模型族：claude|gpt|gemini|deepseek|qwen|glm|grok
    custom_extra_body: str = ""  # 自定义 extra_body JSON
    custom_extra_headers: str = ""  # 自定义 extra_headers JSON
    canonical_model: str = ""  # 智能匹配绑定的已知规范模型名（仅用于本地配置，不改写上游 Model ID）


# 基础 URL 合法性正则：仅接受 http:// 或 https:// 开头的 URL
_URL_PATTERN = re.compile(r"^https?://[^\s/$.?#].[^\s]*$", re.IGNORECASE)
_ALLOWED_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
THINKING_EFFORT_ORDER = ("none", "minimal", "low", "medium", "high", "xhigh", "max")
_ALLOWED_THINKING_EFFORTS = set(THINKING_EFFORT_ORDER)
_ALLOWED_PROTOCOLS = {"auto", "openai", "openai_responses", "anthropic", "gemini"}
logger = logging.getLogger(__name__)

# ── 模型 → 上下文窗口大小映射（token 数） ──────────────────────────
# 键为模型标识符的前缀或完整名称；匹配时先归一化（点/横线/下划线等价、
# 字母→数字边界分段），再按 "-" 边界做最长前缀匹配，支持 provider/
# 前缀与 Bedrock 命名空间（us.anthropic. 等）。未匹配回退默认值。
_DEFAULT_CONTEXT_TOKENS = 256_000

_MODEL_CONTEXT_WINDOW: dict[str, int] = {
    # OpenAI 提供商
    "gpt-6-astra": 1_050_000,
    "gpt-6": 1_050_000,
    "gpt-5.6-sol": 1_050_000,
    "gpt-5.6-terra": 1_050_000,
    "gpt-5.6-luna": 1_050_000,
    "gpt-5.6": 1_050_000,
    "gpt-5.6-cyber": 400_000,
    "gpt-5.5": 1_050_000,
    "gpt-5.4": 1_050_000,
    "gpt-5": 400_000,
    "gpt-5-pro": 400_000,
    "gpt-5-mini": 400_000,
    "gpt-5-nano": 400_000,
    "gpt-5.2": 400_000,
    "gpt-5.3": 400_000,
    "gpt-5-codex": 400_000,
    "gpt-5-codex-mini": 400_000,
    "gpt-5.2-codex": 400_000,
    "gpt-5.3-codex": 400_000,
    "gpt-5.1-codex-mini": 400_000,
    "gpt-5.1-codex-max": 400_000,
    "gpt-5.3-codex-spark": 128_000,
    "gpt-5.1": 400_000,
    "gpt-5.1-codex": 400_000,
    "gpt-5-chat-latest": 128_000,
    "gpt-5.1-chat-latest": 128_000,
    "gpt-5.3-chat-latest": 128_000,
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    "gpt-4.1": 1_047_576,
    "gpt-4.1-mini": 1_047_576,
    "gpt-4.1-nano": 1_047_576,
    "o1": 200_000,
    "o1-pro": 200_000,
    "o3": 200_000,
    "o3-mini": 200_000,
    "o3-pro": 200_000,
    "o3-deep-research": 200_000,
    "o4": 200_000,
    "o4-mini": 200_000,
    "o4-mini-deep-research": 200_000,
    # Anthropic（Claude）提供商
    "claude-4-sonnet": 200_000,
    "claude-4-opus": 200_000,
    "claude-sonnet-4": 200_000,
    "claude-opus-4": 200_000,
    "claude-sonnet-4.5": 200_000,
    "claude-opus-4.5": 200_000,
    "claude-opus-4.1": 200_000,
    "claude-opus-4.6": 200_000,
    "claude-opus-4.7": 1_000_000,
    "claude-opus-4.8": 1_000_000,
    "claude-sonnet-4.6": 200_000,
    "claude-fable-5": 1_000_000,
    "claude-mythos-5": 1_000_000,
    "claude-opus-5": 1_000_000,
    "claude-sonnet-5": 1_000_000,
    "claude-haiku-4.5": 200_000,
    # Google Gemini 提供商
    "gemini-2.5-pro": 1_048_576,
    "gemini-2.5-flash": 1_048_576,
    "gemini-2.5-flash-lite": 1_048_576,
    "gemini-live-2.5-flash-preview": 1_048_576,
    "gemini-2.5-flash-live-preview": 1_048_576,
    "gemini-2.5-flash-native-audio-preview": 1_048_576,
    "gemini-3.8-flash": 1_048_576,
    "gemini-3.7-flash": 1_048_576,
    "gemini-3.6-flash": 1_048_576,
    "gemini-3.5-flash-lite": 1_048_576,
    "gemini-3.5-flash": 1_048_576,
    "gemini-3.1-pro": 1_048_576,
    "gemini-3.1-flash-lite": 1_048_576,
    "gemini-3.1-flash": 1_048_576,
    "gemini-3.1-flash-image": 128_000,
    "gemini-3-pro-image": 65_536,
    "gemini-3-flash": 1_048_576,
    "gemini-3.0-pro-preview-02-2026": 1_048_576,
    "gemini-3.0-flash-preview-02-2026": 1_048_576,
    "gemini-3.0-flash-lite-preview-02-2026": 1_048_576,
    "gemini-3.0-flash-thinking-preview-02-2026": 262_144,
    # 通义千问（Qwen）提供商
    "qwen-max": 262_144,
    "qwen-max-latest": 262_144,
    "qwen3-max": 262_144,
    "qwen3-vl-plus": 262_144,
    "qwen3-vl-flash": 262_144,
    "qwen-vl-max": 131_072,
    "qwen-vl-plus": 131_072,
    "qwq-32b": 131_072,
    "qwen-plus": 1_000_000,
    "qwen-plus-us": 1_000_000,
    "qwen-plus-latest": 1_000_000,
    "qwen3.8-max": 1_000_000,
    "qwen3.8-flash": 1_000_000,
    "qwen3.7-plus": 1_000_000,
    "qwen3.7-flash": 1_000_000,
    "qwen3.7-max": 1_000_000,
    "qwen3.6-plus": 1_000_000,
    "qwen3.6-flash": 1_000_000,
    "qwen3.5-plus": 1_000_000,
    "qwq-plus": 131_072,
    "qwq-plus-latest": 131_072,
    "qwen-flash": 1_000_000,
    "qwen-flash-latest": 1_000_000,
    "qwen-turbo": 1_000_000,
    "qwen-long": 1_000_000,
    "qwen-long-latest": 10_000_000,
    "qwen-coder": 1_000_000,
    "qwen-coder-plus": 131_072,
    "qwen-coder-plus-latest": 131_072,
    "qwen-coder-turbo": 131_072,
    "qwen3-coder-plus": 1_000_000,
    "qwen3-235b": 131_072,
    "qwen3-30b": 131_072,
    "qwen3-32b": 131_072,
    "qvq-72b-preview": 32_768,
    "qwen-vl-ocr": 38_192,
    "qwen-vl-ocr-2025-08-28": 34_096,
    "qwen2.5-omni-7b": 32_768,
    "qwen2.5-72b": 131_072,
    "qwen2.5-32b": 131_072,
    # DeepSeek 提供商
    "deepseek-flash": 1_000_000,
    "deepseek-v4-pro": 1_000_000,
    "deepseek-v4-flash": 1_000_000,
    "deepseek-v4.1": 1_000_000,
    "deepseek-chat": 128_000,
    "deepseek-reasoner": 128_000,
    "deepseek-v3": 128_000,
    "deepseek-r1": 128_000,
    "deepseek-v3.2": 131_072,
    "deepseek-v3.2-exp": 131_072,
    # Mistral 提供商
    "mistral-large-2512": 256_000,
    "mistral-large-latest": 256_000,
    "mistral-medium-2508": 128_000,
    "mistral-medium-latest": 128_000,
    "mistral-small-2506": 128_000,
    "mistral-small-latest": 128_000,
    "devstral-2512": 256_000,
    "labs-devstral-small-2512": 256_000,
    "labs-devstral-small-latest": 256_000,
    "devstral-small-2505": 128_000,
    "codestral-2508": 128_000,
    "codestral-latest": 128_000,
    "magistral-small-2509": 128_000,
    "magistral-medium-2509": 128_000,
    "magistral-small-2507": 40_000,
    "magistral-small-2506": 40_000,
    "pixtral-large-2411": 128_000,
    "pixtral-large-latest": 128_000,
    "voxtral-mini-2507": 128_000,
    "voxtral-mini-latest": 128_000,
    "voxtral-small-2507": 32_000,
    "voxtral-small-latest": 32_000,
    "labs-mistral-small-creative": 32_000,
    "mistral-small-2503": 128_000,
    "ministral-14b-2512": 256_000,
    "ministral-8b-2512": 256_000,
    "ministral-3b-2512": 256_000,
    # AI21 Jamba 提供商
    "jamba-large": 256_000,
    "jamba-mini": 256_000,
    "jamba-3b": 256_000,
    # Amazon Nova（含 Bedrock 区域前缀，靠命名空间剥离匹配）
    "nova-premier": 1_000_000,
    "nova-pro": 300_000,
    "nova-lite": 300_000,
    "nova-micro": 128_000,
    "nova-sonic": 300_000,
    "nova-2-lite": 1_000_000,
    "nova-2-sonic": 1_000_000,
    # MiniMax 提供商
    "minimax-m3": 1_000_000,
    "minimax-m2.7": 204_800,
    "minimax-m2.5": 204_800,
    "minimax-m2.5-highspeed": 204_800,
    "minimax-m2.1": 204_800,
    "minimax-m2.1-highspeed": 204_800,
    "minimax-m2.1-lightning": 204_800,
    "minimax-m2": 204_800,
    "m2-her": 64_000,
    # Moonshot（Kimi）提供商
    "kimi-k3": 1_000_000,
    "kimi-k2.7-code": 256_000,
    "kimi-k2.7-code-highspeed": 256_000,
    "kimi-k2.7": 256_000,
    "kimi-k2.6": 256_000,
    "kimi-k2": 262_144,
    "kimi-k2-thinking": 262_144,
    "kimi-k2.5": 262_144,
    "kimi-k2.5-thinking": 262_144,
    "moonshot-kimi-k2.5": 262_144,
    "moonshot-kimi-k2.5-thinking": 262_144,
    "moonshot-kimi-k2-instruct": 131_072,
    "moonshot-v1-128k": 128_000,
    "moonshot-v1-32k": 32_000,
    "moonshot-v1-8k": 8_000,
    # Cohere 提供商
    "command-a": 256_000,
    "command-a-03-2025": 256_000,
    "command-a-reasoning": 256_000,
    "command-r-plus": 128_000,
    "command-r-plus-08-2024": 128_000,
    "command-r7b": 128_000,
    "command-r7b-12-2024": 128_000,
    "c4ai-command-r7b-12-2024": 128_000,
    "command-r": 128_000,
    "command-r-08-2024": 128_000,
    # 智谱 GLM 提供商
    "glm-5.3": 1_000_000,
    "glm-5.3-flash": 1_000_000,
    "glm-5.2": 1_000_000,
    "glm-5.1": 200_000,
    "glm-5-turbo": 200_000,
    "glm-5v-turbo": 200_000,
    "glm-5": 200_000,
    "glm-4.7": 200_000,
    "glm-4.6": 200_000,
    "glm-4.5": 128_000,
    "glm-4.5-air": 128_000,
    "glm-4-plus": 128_000,
    "glm-4-long": 1_000_000,
    "glm-4": 128_000,
    # 字节豆包（火山方舟）
    "doubao-seed-evolving": 256_000,
    "doubao-seed-2.1-pro": 256_000,
    "doubao-seed-2.1-turbo": 256_000,
    "doubao-seed-2.0-pro": 256_000,
    "doubao-seed-2.0-code": 256_000,
    "doubao-seed-2.0-lite": 256_000,
    "doubao-seed-2.0-mini": 256_000,
    "doubao-seed-2.0": 256_000,
    "doubao-seed-1.6": 256_000,
    # xAI Grok 提供商
    "grok-4.6": 500_000,
    "grok-4.5": 500_000,
    "grok-4.3": 1_000_000,
    "grok-4-fast-reasoning": 2_000_000,
    "grok-4-fast-non-reasoning": 2_000_000,
    "grok-4.1-fast-reasoning": 2_000_000,
    "grok-4.1-fast-non-reasoning": 2_000_000,
    "grok-code-fast-1": 256_000,
    "grok-4": 256_000,
    # Meta Llama 提供商
    "llama-4-scout": 10_000_000,
    "llama-4-maverick": 1_000_000,
    "llama-3.3": 131_072,
    "llama-3.2": 131_072,
    "llama-3.1": 131_072,
}


_CONTEXT_WINDOW_LOOKUP = normalize_lookup_table(_MODEL_CONTEXT_WINDOW)


_DEPRECATED_MODEL_REPLACEMENTS: dict[str, str] = {
    # OpenAI
    "codex-mini-latest": "gpt-5.6-luna",
    "gpt-4-turbo": "gpt-6-astra",
    "gpt-4-turbo-preview": "gpt-6-astra",
    "gpt-4-0125-preview": "gpt-6-astra",
    "gpt-4-1106-preview": "gpt-6-astra",
    "o1-mini": "o4-mini",
    # Anthropic Claude 3.x
    "claude-3-opus": "claude-opus-5",
    "claude-3-sonnet": "claude-sonnet-5",
    "claude-3-haiku": "claude-haiku-4-5",
    "claude-3-5-sonnet": "claude-sonnet-5",
    "claude-3-5-haiku": "claude-haiku-4-5",
    "claude-3-7-sonnet": "claude-sonnet-5",
    # Gemini 1.5 / 2.0 generations（2.0 已关停）
    "gemini-1.5-pro": "gemini-3.8-flash",
    "gemini-1.5-flash": "gemini-3.8-flash",
    "gemini-2.0-flash": "gemini-3.8-flash",
    "gemini-2.0-flash-001": "gemini-3.8-flash",
    "gemini-2.0-flash-live": "gemini-live-2.5-flash-preview",
    "gemini-2.0-flash-thinking-exp": "gemini-3.8-flash",
    "gemini-2.0-flash-lite": "gemini-3.5-flash-lite",
    # Gemini 3 预览别名（官方已关停/更名）
    "gemini-3-pro-preview": "gemini-3.1-pro-preview",
    "gemini-3.1-flash-lite-preview": "gemini-3.1-flash-lite",
    # 2026 年 9 月前的模型别名，统一迁移到当前旗舰默认值
    "qwen3.7-plus": "qwen3.8-max",
    "qwen3.7-flash": "qwen3.8-flash",
    "qwen3.7-max": "qwen3.8-max",
    "qwen3.6-plus": "qwen3.8-max",
    "qwen3.6-flash": "qwen3.8-flash",
    "grok-4.5": "grok-4.6",
    "grok-4.3": "grok-4.6",
    # DeepSeek 旧别名（2026-07-24 下线）
    "deepseek-chat": "deepseek-flash",
    "deepseek-reasoner": "deepseek-flash",
    # 已下线，暂时路由到 V4.1-Flash
    "deepseek-v4-flash": "deepseek-flash",
    # Moonshot 已下线系列
    "moonshot-v1": "kimi-k3",
    "kimi-k2.5": "kimi-k3",
    "kimi-k2": "kimi-k3",
    # 智谱旧旗舰
    "glm-4-plus": "glm-5.3",
    "glm-4-long": "glm-5.3",
    "glm-z1": "glm-5.3",
}


_DEPRECATED_LOOKUP = normalize_lookup_table(_DEPRECATED_MODEL_REPLACEMENTS)


def get_deprecated_model_replacement(model: str) -> tuple[str, str] | None:
    """返回弃用模型及推荐替代模型；未命中时返回 None。

    归一化后最长前缀匹配；前缀命中后紧跟 1~2 位纯数字段（版本号）
    不算命中（如 ``kimi-k2.6`` 不命中 ``kimi-k2``）。
    """
    return longest_prefix_match(model, _DEPRECATED_LOOKUP, version_guard=True)


def format_deprecated_model_message(model: str) -> str | None:
    """生成统一的弃用模型迁移提示文案。"""
    matched = get_deprecated_model_replacement(model)
    if not matched:
        return None
    deprecated, replacement = matched
    return (
        f"模型 {model!r} 已弃用（匹配 {deprecated!r}），"
        f"请改用 {replacement!r}。"
    )


def _log_deprecated_model_warning(scope: str, model: str) -> None:
    """记录模型弃用告警，不中断启动。"""
    msg = format_deprecated_model_message(model)
    if msg:
        logger.warning("[%s] %s", scope, msg)


@lru_cache(maxsize=1024)
def _infer_context_tokens_for_model(model: str) -> int:
    """根据模型名推断上下文窗口大小，归一化最长前缀匹配优先。"""
    matched = longest_prefix_match(model, _CONTEXT_WINDOW_LOOKUP)
    if matched is not None:
        best_key, best_val = matched
        logger.debug(
            "模型 %r 匹配上下文窗口映射 %r → %d tokens",
            model, best_key, best_val,
        )
        return best_val
    logger.debug(
        "模型 %r 未匹配到已知映射，使用默认 %d tokens",
        model, _DEFAULT_CONTEXT_TOKENS,
    )
    return _DEFAULT_CONTEXT_TOKENS


def is_context_window_user_pinned(max_context_tokens: int, model: str) -> bool:
    """用户是否显式锁定了上下文窗口。

    设置页保存会写入 ``EXCELMANUS_MAX_CONTEXT_TOKENS``；只要该设置存在
    即视为锁定（即使数值恰好等于模型推断值）。否则回退到
    「配置值 ≠ 当前模型推断值」——兼容测试和编程方式传入的 Config。
    """
    from excelmanus.settings_runtime import get_setting

    if get_setting("EXCELMANUS_MAX_CONTEXT_TOKENS"):
        return True
    return max_context_tokens != _infer_context_tokens_for_model(model)


# ── 已知模型规范名匹配（Jev 智能匹配，可开关）─────────────────────
# 把用户填写的 Model ID 与内置已知模型表比对，置信度足够时把档案绑定到
# 规范模型名，从而继承其上下文窗口、模型族与已探测能力配置。
# 只绑定本地配置，不改写发给上游 API 的 Model ID。
CANONICAL_MATCH_THRESHOLD = 0.85


@dataclass(frozen=True)
class CanonicalModelMatch:
    """已知模型名匹配结果。"""

    canonical: str  # 命中的内置规范模型名（_MODEL_CONTEXT_WINDOW 的原始键）
    confidence: float  # 0~1；>= CANONICAL_MATCH_THRESHOLD 才会自动绑定
    reason: str  # 命中规则标签（诊断用）


# 供应商/命名空间前缀 token（去掉后再比对，如 openai-gpt-5.6-sol、azure-xxx）
_VENDOR_PREFIX_TOKENS = frozenset({
    "openai", "anthropic", "google", "meta", "xai", "amazon", "aws",
    "azure", "bedrock", "deepseek", "moonshot", "zhipu", "mistral",
    "cohere", "alibaba", "aliyun", "bytedance", "volcengine", "nvidia",
    "ai21", "huggingface", "hf", "openrouter", "openai-codex",
    "workbuddy", "antigravity",
})
# 尾部修饰性后缀（剥掉后不影响模型身份，如 -preview、-20260301、-v2）
_NOISE_SUFFIX_TOKENS = frozenset({
    "preview", "latest", "exp", "experimental", "beta", "alpha",
    "stable", "free", "fast", "thinking", "instruct", "chat", "base",
    "it", "fp8", "bf16", "awq", "gptq", "int4", "int8",
})
_VERSION_TOKEN_RE = re.compile(r"^v?\d+$")
_DIGIT_GLUE_RE = re.compile(r"^(\d+)([a-z][a-z0-9]*)$")


def _drop_vendor_tokens(tokens: tuple[str, ...]) -> tuple[str, ...]:
    """剥掉开头最多 2 个供应商前缀 token，至少保留 2 个模型 token。"""
    rest = list(tokens)
    dropped = 0
    while len(rest) > 2 and dropped < 2 and rest[0] in _VENDOR_PREFIX_TOKENS:
        rest.pop(0)
        dropped += 1
    return tuple(rest)


def _loose_tokens(tokens: tuple[str, ...]) -> tuple[str, ...]:
    """宽松形态：数字+字母粘连拆分（4o→4,o），相邻纯数字段合并（5,6→56）。"""
    out: list[str] = []
    for tok in tokens:
        m = _DIGIT_GLUE_RE.match(tok)
        if m:
            out.extend((m.group(1), m.group(2)))
        else:
            out.append(tok)
    merged: list[str] = []
    for tok in out:
        if merged and merged[-1].isdigit() and tok.isdigit():
            merged[-1] += tok
        else:
            merged.append(tok)
    return tuple(merged)


def _token_variants(tokens: tuple[str, ...]) -> list[tuple[tuple[str, ...], float]]:
    """生成 (tokens, 权重) 变体：raw / 去供应商前缀 / 宽松 / 宽松+去前缀。"""
    variants: list[tuple[tuple[str, ...], float]] = [(tokens, 1.0)]
    loose = _loose_tokens(tokens)
    if loose != tokens:
        variants.append((loose, 0.95))
    stripped = _drop_vendor_tokens(tokens)
    if stripped != tokens:
        variants.append((stripped, 0.97))
        loose_stripped = _loose_tokens(stripped)
        if loose_stripped not in (stripped, loose):
            variants.append((loose_stripped, 0.92))
    return variants


@lru_cache(maxsize=1)
def _canonical_key_variants() -> tuple[tuple[tuple[str, ...], float, str], ...]:
    """已知模型表键的匹配变体：[(tokens, 权重, 原始键)]，权重按序取先。"""
    entries: list[tuple[tuple[str, ...], float, str]] = []
    seen: set[tuple[str, ...]] = set()
    for raw_key in _MODEL_CONTEXT_WINDOW:
        norm = normalize_model_tokens(raw_key)
        toks = tuple(t for t in norm.split("-") if t)
        if not toks:
            continue
        for variant, weight in _token_variants(toks):
            if variant in seen:
                continue
            seen.add(variant)
            entries.append((variant, weight, raw_key))
    return tuple(entries)


def _is_cosmetic_suffix(tok: str) -> bool:
    return (
        tok in _NOISE_SUFFIX_TOKENS
        or tok.isdigit()
        or _VERSION_TOKEN_RE.match(tok) is not None
    )


def _levenshtein_leq1(a: str, b: str) -> bool:
    """两个字符串编辑距离是否 ≤ 1。"""
    if a == b:
        return True
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if la > lb:
        a, b, la, lb = b, a, lb, la
    i = j = diffs = 0
    while i < la and j < lb:
        if a[i] == b[j]:
            i += 1
            j += 1
            continue
        diffs += 1
        if diffs > 1:
            return False
        if la == lb:
            i += 1
            j += 1
        else:
            j += 1
    return diffs + (lb - j) + (la - i) <= 1


def match_canonical_model(model: str) -> CanonicalModelMatch | None:
    """把输入 Model ID 匹配到内置已知模型表的规范名。

    评分规则（候选 tokens × 表键变体）：
    - 归一化 token 完全一致：1.0 - 权重损耗（raw 命中=1.0，去前缀=0.97，
      宽松形态=0.95，宽松+去前缀≈0.92）；
    - 候选 = 键 + 修饰性/版本/日期后缀：0.9 - 权重损耗；
    - 候选 = 键 + 其他后缀：0.78 - 权重损耗（低于阈值，仅作建议）；
    - 归一化串编辑距离 ≤1 且首 token 相同：0.75（仅作建议）。
    返回得分最高的命中；无任何命中返回 None。
    """
    cand_variants: list[tuple[tuple[str, ...], float]] = []
    seen: set[tuple[str, ...]] = set()
    for cand in model_match_candidates(model):
        toks = tuple(t for t in cand.split("-") if t)
        if not toks:
            continue
        for variant, weight in _token_variants(toks):
            if variant not in seen:
                seen.add(variant)
                cand_variants.append((variant, weight))

    if not cand_variants:
        return None

    best: CanonicalModelMatch | None = None
    tied_keys: set[str] = set()
    for cand_toks, cand_w in cand_variants:
        for key_toks, key_w, raw_key in _canonical_key_variants():
            if cand_toks == key_toks:
                score = cand_w + key_w - 1.0
                reason = "exact" if cand_w == key_w == 1.0 else "variant"
            elif len(cand_toks) > len(key_toks) and cand_toks[: len(key_toks)] == key_toks:
                extras = cand_toks[len(key_toks):]
                base = 0.9 if all(_is_cosmetic_suffix(t) for t in extras) else 0.78
                score = base - (1.0 - cand_w) - (1.0 - key_w)
                reason = "suffix"
            else:
                continue
            if best is None or score > best.confidence:
                best = CanonicalModelMatch(raw_key, round(score, 4), reason)
                tied_keys = {raw_key}
            elif score == best.confidence:
                tied_keys.add(raw_key)

    # 同分命中多个不同规范名 → 歧义，不绑定
    if best is not None and len(tied_keys) <= 1:
        return best
    if best is not None:
        return None

    # 兜底：归一化串编辑距离 ≤1（同首 token），仅作为低置信度建议
    cand_norms = {cand for cand in model_match_candidates(model) if cand}
    for key_toks, key_w, raw_key in _canonical_key_variants():
        if key_w != 1.0:
            continue
        key_norm = "-".join(key_toks)
        for cand_norm in cand_norms:
            if cand_norm.split("-", 1)[0] != key_toks[0]:
                continue
            if _levenshtein_leq1(cand_norm, key_norm):
                return CanonicalModelMatch(raw_key, 0.75, "edit_distance")
    return None


# 规范模型名前缀 → 模型族；只覆盖已知表里的厂商，未命中返回 ""。
_MODEL_FAMILY_PREFIXES: tuple[tuple[str, str], ...] = (
    ("claude", "claude"),
    # 注意：前缀按归一化后的 token 形态书写（字母→数字边界已分段，o4 → o-4）
    ("gpt", "gpt"), ("o-1", "gpt"), ("o-3", "gpt"), ("o-4", "gpt"),
    ("codex", "gpt"), ("chatgpt", "gpt"),
    ("gemini", "gemini"),
    ("deepseek", "deepseek"),
    ("qwen", "qwen"), ("qwq", "qwen"), ("qvq", "qwen"),
    ("glm", "glm"),
    ("grok", "grok"),
    ("kimi", "moonshot"), ("moonshot", "moonshot"),
    ("minimax", "minimax"), ("m-2-her", "minimax"),
    ("doubao", "doubao"),
    ("mistral", "mistral"), ("ministral", "mistral"), ("devstral", "mistral"),
    ("codestral", "mistral"), ("magistral", "mistral"), ("pixtral", "mistral"),
    ("voxtral", "mistral"),
    ("llama", "meta"),
    ("nova", "amazon"),
    ("jamba", "ai21"),
    ("command", "cohere"), ("c4ai", "cohere"),
    ("hunyuan", "hunyuan"),
    ("step", "stepfun"),
)


def infer_model_family(canonical: str) -> str:
    """由规范模型名推断 model_family（用于前端提供商分组/图标）。"""
    norm = normalize_model_tokens(canonical)
    for prefix, family in _MODEL_FAMILY_PREFIXES:
        if norm == prefix or norm.startswith(prefix + "-"):
            return family
    return ""


def canonical_match_enabled() -> bool:
    """Jev 智能匹配开关（默认开启）；读运行时设置，未配置时按默认开启。"""
    from excelmanus.settings_runtime import get_setting

    raw = (get_setting("EXCELMANUS_MODEL_CANONICAL_MATCH") or "").strip().lower()
    if not raw:
        return True
    return raw in ("1", "true", "yes", "on")


def profile_canonical(row: Mapping[str, object]) -> str:
    """读取档案行的 canonical_model；开关关闭时视为未绑定。"""
    if not canonical_match_enabled():
        return ""
    return str(row.get("canonical_model") or "").strip()


@dataclass(frozen=True)
class ExcelManusConfig:
    """不可变的全局配置对象。"""

    api_key: str
    base_url: str
    model: str
    protocol: str = "auto"  # 激活模型协议类型：auto|openai|openai_responses|anthropic|gemini
    responses_continuation_enabled: bool = False  # Responses 原生 previous_response_id 续接
    responses_background_enabled: bool = False  # Responses 后台响应并轮询终态
    max_iterations: int = 120  # 本轮 LLM 回合与工具调用上限（并行工具各计 1 次）
    turn_timeout_seconds: int = 0  # 单个 turn 的 wall-clock 上限；0 表示不限制
    turn_token_budget: int = 0  # 单个 turn 的输入+输出 token 上限；0 表示不限制
    turn_cost_budget_usd: float = 0.0  # 单个 turn 的美元成本上限；0 表示不限制
    input_cost_per_1k_usd: float = 0.0  # provider 未返回 cost 时的估算单价
    output_cost_per_1k_usd: float = 0.0
    max_consecutive_failures: int = 6
    session_ttl_seconds: int = 1800
    max_sessions: int = 1000
    workspace_root: str = field(default_factory=lambda: str(get_data_home()))
    data_root: str = ""  # 集中数据目录（默认 ~/.excelmanus/data）
    deploy_mode: str = "standalone"  # standalone|server — 部署模式
    log_level: str = "INFO"
    skills_system_dir: str = "excelmanus/skillpacks/system"
    skills_user_dir: str = "~/.excelmanus/skillpacks"
    skills_project_dir: str = ".excelmanus/skillpacks"
    skills_context_char_budget: int = 12000  # 技能正文字符预算，0 表示不限制
    skills_discovery_enabled: bool = True
    agent_self_management_enabled: bool = False  # 用户显式启用自身能力查询与会话配置技能/工具
    skills_discovery_scan_workspace_ancestors: bool = True
    skills_discovery_include_agents: bool = True
    skills_discovery_scan_external_tool_dirs: bool = True
    skills_discovery_extra_dirs: tuple[str, ...] = ()
    tool_result_hard_cap_chars: int = 12000
    cors_allow_origins: tuple[str, ...] = (
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    )
    mcp_shared_manager: bool = False
    pool_enabled: bool = False  # 号池功能开关（默认关闭，灰度上线）
    pool_auto_enabled: bool = False  # 号池自动轮换开关（需 pool_enabled=True）
    pool_auto_interval_seconds: int = 60  # 自动轮换定时扫描间隔（秒）
    pool_auto_default_cooldown_seconds: int = 300  # 自动轮换默认冷却时间（秒）
    pool_auto_hysteresis_delta: float = 0.12  # 迟滞防抖阈值（分数差 < delta 不切换）
    pool_auto_min_dwell_seconds: int = 180  # 最短驻留时间（秒，软触发受限）
    pool_auto_breaker_open_seconds: int = 120  # 熔断器打开持续时间（秒）
    pool_auto_breaker_threshold: int = 5  # 连续失败多少次触发熔断
    exa_search_enabled: bool = True  # 内置搜索引擎总开关（False 禁用全部内置搜索）
    search_default_provider: str = "exa"  # 默认搜索引擎：exa | tavily | brave
    exa_api_key: str | None = None  # Exa API 密钥（可选，提升搜索质量和速率限制）
    tavily_api_key: str | None = None  # Tavily API 密钥（配置后额外启用 Tavily 搜索）
    brave_api_key: str | None = None  # Brave API 密钥（配置后额外启用 Brave 搜索）
    # ── 能力探测任务配置 ──
    cap_probe_job_concurrency: int = 2  # 全局最大并发探测数
    cap_probe_provider_concurrency: int = 1  # 同一 provider 最大并发（降低 429）
    cap_probe_health_timeout: float = 8.0  # 健康检查超时（秒）
    cap_probe_tool_timeout: float = 20.0  # tool calling 探测超时（秒）
    cap_probe_vision_timeout: float = 20.0  # vision 探测超时（秒）
    cap_probe_thinking_total_timeout: float = 30.0  # thinking 总预算（秒）
    cap_probe_thinking_strategy_timeout: float = 8.0  # thinking 单策略上限（秒）
    # subagent 执行配置
    subagent_enabled: bool = True
    subagent_max_iterations: int = 120  # 子循环 LLM 回合与工具调用上限
    subagent_max_consecutive_failures: int = 6
    subagent_timeout_seconds: int = 600  # 单个子代理执行超时（秒）
    parallel_subagent_max: int = 3  # 并行子代理最大并发数
    # 同一轮次中相邻只读工具并发执行（asyncio.gather）
    parallel_readonly_tools: bool = True
    parallel_tool_max: int = 4  # 同一只读工具批最多同时执行的调用数（1–32）
    subagent_user_dir: str = "~/.excelmanus/agents"
    subagent_project_dir: str = ".excelmanus/agents"
    # 跨会话持久记忆配置
    memory_enabled: bool = True
    memory_dir: str = "~/.excelmanus/memory"
    memory_auto_load_lines: int = 200
    memory_expire_days: int = 90  # 记忆过期天数（0 = 不过期）
    # 记忆维护代理配置
    memory_maintenance_enabled: bool = False  # LLM 记忆维护（默认关，显式开启）
    memory_maintenance_min_entries: int = 10  # 至少多少条才值得维护
    memory_maintenance_new_threshold: int = 5  # 新增多少条后触发维护
    memory_maintenance_interval_hours: float = 4.0  # 两次维护最小间隔（小时）
    memory_maintenance_model: str | None = None  # 维护用模型，默认 None → 使用激活模型
    # LLM 调用重试配置：遇到 5xx / 429 / 网络错误时自动重试
    llm_retry_max_attempts: int = 3          # 最大尝试次数（含首次）
    llm_retry_base_delay_seconds: float = 2.0  # 指数退避基准延迟（秒）
    llm_retry_max_delay_seconds: float = 30.0  # 单次重试最大延迟上限（秒）
    # 对话记忆上下文窗口大小（token 数），用于截断策略
    max_context_tokens: int = _DEFAULT_CONTEXT_TOKENS
    # 提示词缓存优化：向 OpenAI API 发送 prompt_cache_key 提升缓存命中率
    prompt_cache_key_enabled: bool = True
    # 提示词缓存保留策略：default|extended；仅一方 Anthropic/OpenAI 端点生效，
    # 兼容网关与自部署端点不会收到扩展 TTL/retention 字段
    prompt_cache_retention: str = "default"
    # 上下文自动压缩（Compaction）：增强版对话摘要，后台静默执行
    compaction_enabled: bool = True
    compaction_threshold_ratio: float = 0.85
    compaction_keep_recent_turns: int = 5
    # thinking 模型会把推理计入 completion 预算，1500 易被烧光导致空摘要
    compaction_max_summary_tokens: int = 4096
    # 连续空摘要重试上限；达到后跳过 LLM 摘要回落硬截断（0 = 禁用回落）
    compaction_empty_summary_max_retries: int = 3
    # 压缩时按 token 保留的尾段预算；0 = 自动（max_context 的 25%）
    compaction_retain_tokens: int = 0
    # L1 无模型修剪：压缩边界内先于 LLM 摘要瘦身超大 tool 结果
    compaction_pruner_enabled: bool = True
    # hooks 配置
    hooks_command_enabled: bool = False
    hooks_command_allowlist: tuple[str, ...] = ()
    hooks_command_timeout_seconds: int = 10
    hooks_output_max_chars: int = 32000
    # 视觉：图片只交给激活模型；请求投影预算（不改写历史）
    image_pixel_budget: int | str = 640_000
    image_max_bytes: int = 1_048_576
    image_files_api: str = "auto"
    main_model_vision: str = "auto"  # 激活模型视觉能力：auto/true/false
    # 代码策略引擎配置
    code_policy_enabled: bool = True
    code_policy_green_auto_approve: bool = True
    code_policy_yellow_auto_approve: bool = False
    code_policy_extra_safe_modules: tuple[str, ...] = ()
    code_policy_extra_blocked_modules: tuple[str, ...] = ()
    # 工具参数 schema 校验（off/shadow/enforce）
    tool_schema_validation_mode: str = "shadow"
    tool_schema_validation_canary_percent: int = 100
    tool_schema_strict_path: bool = False
    # 会话结束摘要落库（不注入新会话；检索/注入未接入）
    session_summary_enabled: bool = False  # 仅控制会话结束摘要落库，默认关；不注入新会话
    session_summary_min_turns: int = 3  # 最少轮次才生成摘要；仅约束落库，不注入新会话
    # 统一数据库路径（聊天记录、记忆、审批、用户设置均存于此）
    db_path: str = "~/.excelmanus/excelmanus.db"
    # 聊天记录持久化
    chat_history_enabled: bool = True
    chat_history_db_path: str = ""  # 废弃别名，始终与 db_path 相同
    # 会话事件日志（session_events append-only 事实源 + surface fold）
    session_log_enabled: bool = True
    # Thinking（推理深度）配置
    thinking_effort: str = "medium"  # none|minimal|low|medium|high|xhigh|max
    thinking_budget: int = 0  # 精确 token 预算（>0 时覆盖 effort 换算值）
    thinking_effort_options: tuple[str, ...] = THINKING_EFFORT_ORDER
    # 友好错误消息：将内部错误映射为更友好的用户可见消息
    friendly_error_messages: bool = True
    # 多模型配置档案（可选，通过 /model 命令切换）
    models: tuple[ModelProfile, ...] = ()
    # Jev / TypeSafe System One（可选 extra；走运行时设置 / config_kv，不进 model_profiles）
    # JEV is binary: off disables a gate, enforce enables it fully.  The
    # loader migrates the removed legacy ``shadow`` value to ``enforce``.
    jev_enabled: str = "enforce"
    jev_exposure: str = "enforce"
    jev_mode_hint: bool = True
    jev_observation: str = "enforce"
    jev_verification: str = "enforce"
    jev_recovery: str = "enforce"
    jev_ui_hint: bool = True
    jev_model: str = "jev-1.13.0"
    typesafe_api_key: str | None = None
    ai_gateway_api_key: str | None = None
    jev_active_provider: str = ""
    jev_providers: tuple = ()  # EXCELMANUS_JEV_PROVIDERS 解析出的 JevProviderRecord
    jev_timeout_seconds: float = 1.5
    # Legacy compatibility flag; runtime application no longer depends on it.
    jev_calibrated: bool = False
    # 当前激活模型绑定到的已知规范模型名（仅用于本地配置推断）
    canonical_model: str = ""
    # Jev 智能匹配：加入/保存模型档案时按置信度绑定已知规范模型名
    model_canonical_match_enabled: bool = True

    @property
    def is_standalone(self) -> bool:
        """单机桌面部署模式。"""
        return self.deploy_mode == "standalone"

    @property
    def is_server(self) -> bool:
        """前后端分离的服务器部署。"""
        return self.deploy_mode == "server"


@dataclass(frozen=True)
class _ContextOptimizationConfig:
    """上下文预算与压缩策略配置（单一接线入口）。"""

    max_context_tokens: int
    prompt_cache_key_enabled: bool
    prompt_cache_retention: str
    compaction_enabled: bool
    compaction_threshold_ratio: float
    compaction_keep_recent_turns: int
    compaction_max_summary_tokens: int


def load_runtime_env() -> None:
    """启动探测。"""
    from excelmanus.data_home import load_runtime_env as _load_runtime_env

    _load_runtime_env()


def _s(name: str, default: str | None = None) -> str | None:
    """读取产品设置；未设置时返回 default。"""
    from excelmanus.settings_runtime import get_setting

    raw = get_setting(name)
    if raw is None:
        return default
    return raw


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


def _parse_image_pixel_budget(value: str | None) -> int | str:
    if value is None or not str(value).strip():
        return 640_000
    raw = str(value).strip().lower()
    if raw == "low":
        return "low"
    return _parse_int(raw, "EXCELMANUS_IMAGE_PIXEL_BUDGET", 640_000)


def _parse_int_allow_zero(value: str | None, name: str, default: int) -> int:
    """将字符串解析为非负整数，0 表示不限制。"""
    if value is None:
        return default
    try:
        result = int(value)
    except (ValueError, TypeError):
        raise ConfigError(f"配置项 {name} 必须为整数，当前值: {value!r}")
    if result < 0:
        raise ConfigError(f"配置项 {name} 必须为非负整数，当前值: {result}")
    return result


def _parse_positive_float(value: str | None, name: str, default: float) -> float:
    """将字符串解析为正浮点数。空字符串视为未设置。"""
    if value is None or not str(value).strip():
        return default
    try:
        result = float(value)
    except (ValueError, TypeError):
        raise ConfigError(f"配置项 {name} 必须为浮点数，当前值: {value!r}")
    if result <= 0:
        raise ConfigError(f"配置项 {name} 必须为正数，当前值: {result}")
    return result


def _parse_nonnegative_float(value: str | None, name: str, default: float) -> float:
    """Parse an optional non-negative float; zero disables the budget."""
    if value is None or not str(value).strip():
        return default
    try:
        result = float(str(value).strip())
    except ValueError as exc:
        raise ConfigError(f"{name} 必须是非负数字") from exc
    if result < 0:
        raise ConfigError(f"{name} 必须大于等于 0")
    return result


def _parse_float_between_zero_and_one(value: str | None, name: str, default: float) -> float:
    """将字符串解析为 (0, 1) 区间内浮点数。"""
    if value is None:
        return default
    try:
        result = float(value)
    except (ValueError, TypeError):
        raise ConfigError(f"配置项 {name} 必须为浮点数，当前值: {value!r}")
    if not 0 < result < 1:
        raise ConfigError(f"配置项 {name} 必须在 (0, 1) 区间内，当前值: {result}")
    return result


def _validate_base_url(url: str) -> None:
    """校验 Base URL 为合法的 HTTP/HTTPS URL。"""
    if not _URL_PATTERN.match(url):
        raise ConfigError(
            f"EXCELMANUS_BASE_URL 必须为合法的 HTTP/HTTPS URL，当前值: {url!r}"
        )


# OpenAI 兼容 API 的常见路径后缀（用于自动修正）
_OPENAI_COMPAT_V1_SUFFIX = "/v1"
# Gemini / Anthropic 原生 API 的 URL 特征（这些不需要 /v1）
_NATIVE_API_INDICATORS = (
    "generativelanguage.googleapis.com",
    ":generateContent",
    ":streamGenerateContent",
    "api.anthropic.com",
)

# 模型名称前缀 → 协议类型映射（用于 auto 模式下的多信号推断）
_MODEL_PREFIX_TO_PROTOCOL: tuple[tuple[str, str], ...] = (
    ("claude-", "anthropic"),
    ("claude3", "anthropic"),
    ("claude4", "anthropic"),
    ("claude_", "anthropic"),
    ("gemini-", "gemini"),
    ("gemini2", "gemini"),
    ("gemini1", "gemini"),
    ("gemini_", "gemini"),
)


def _infer_protocol_from_model(model: str) -> str | None:
    """根据模型名称前缀推断协议类型。

    仅用于 protocol=auto 时的辅助推断，不覆盖用户显式设置。
    返回 None 表示无法从模型名推断。
    """
    if not model:
        return None
    lower = model.strip().lower()
    for prefix, proto in _MODEL_PREFIX_TO_PROTOCOL:
        if lower.startswith(prefix):
            return proto
    return None


def _infer_protocol_from_api_key(api_key: str) -> str | None:
    """根据 API Key 前缀推断协议类型。

    已知前缀：
      - sk-ant-  → Anthropic

    返回 None 表示无法从 API Key 推断。
    """
    if not api_key:
        return None
    if api_key.startswith("sk-ant-"):
        return "anthropic"
    return None


def _normalize_base_url(
    url: str,
    *,
    protocol: str = "auto",
    env_name: str = "EXCELMANUS_BASE_URL",
    model: str = "",
    api_key: str = "",
) -> str:
    """规范化 Base URL：去尾斜杠、检测并自动修正缺失 /v1。

    对于 OpenAI 兼容协议（openai / openai_responses / auto 且非 Gemini/Anthropic 原生），
    如果 URL 路径不以 /v1 结尾且看起来像是第三方代理，
    自动补全 /v1 并记录警告日志。

    多信号推断（仅 protocol=auto 时生效，按优先级）：
      1. URL 特征匹配（_NATIVE_API_INDICATORS）
      2. 模型名称前缀推断（claude-* → anthropic, gemini-* → gemini）
      3. API Key 前缀推断（sk-ant-* → anthropic）

    Returns:
        规范化后的 URL。
    """
    # 去除尾部斜杠
    normalized = url.rstrip("/")

    # 判断是否为原生 API（不需要 /v1）
    is_native = any(indicator in normalized.lower() for indicator in _NATIVE_API_INDICATORS)
    p = (protocol or "auto").strip().lower()
    is_openai_compat = p in ("openai", "openai_responses", "auto") and not is_native
    if p in ("gemini", "anthropic"):
        is_openai_compat = False

    # auto 模式下，URL 未匹配原生特征时，尝试从模型名/API Key 推断
    if is_openai_compat and p == "auto":
        inferred = _infer_protocol_from_model(model) or _infer_protocol_from_api_key(api_key)
        if inferred in ("gemini", "anthropic"):
            logger.info(
                "%s: protocol=auto 但从模型名/API Key 推断出 %s 协议，跳过 /v1 自动补全。"
                "如需强制 OpenAI 兼容模式，请显式设置 protocol=openai。",
                env_name, inferred,
            )
            is_openai_compat = False

    if not is_openai_compat:
        return normalized

    # 对 OpenAI 兼容协议，检查路径是否以版本段结尾
    from urllib.parse import urlparse
    parsed = urlparse(normalized)
    path = parsed.path.rstrip("/")

    # 已经以版本段结尾（/v1、/v2、/api/v3、/v1beta 等）— 正常。
    # 例如 WorkBuddy 上游使用 /v2，不应再补 /v1。
    if re.search(r"/v[\w.]+$", path):
        return normalized

    # 已带其它版本前缀（Anthropic 兼容 /anthropic）— 不要再补 /v1
    if path.endswith("/anthropic"):
        return normalized

    # 版本段后还有子路径（如 /v1/chat）→ 过度指定，警告
    _ver_in_path = re.search(r"/v[\w.]+/", path)
    if _ver_in_path:
        logger.warning(
            "%s 的路径 %r 在版本段后包含额外子路径，"
            "OpenAI SDK 会自动拼接 /chat/completions，请确认路径是否正确。"
            "建议将路径截断到版本段，例如: %s",
            env_name, path,
            normalized[:normalized.index(_ver_in_path.group()) + len(_ver_in_path.group()) - 1],
        )
        return normalized

    # 路径不包含版本段 — 很可能缺失，自动补全 /v1
    corrected = normalized + _OPENAI_COMPAT_V1_SUFFIX
    logger.warning(
        "%s=%r 未以 /v1 结尾。OpenAI 兼容 API 通常需要 /v1 路径前缀，"
        "已自动修正为 %r。如果这不正确，请显式设置完整的 Base URL。",
        env_name, url, corrected,
    )
    return corrected


def _parse_bool(value: str | None, name: str, default: bool) -> bool:
    """将字符串解析为布尔值。"""
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"配置项 {name} 必须为布尔值，当前值: {value!r}")


_ALLOWED_JEV_GATES = frozenset({"off", "enforce"})


def _parse_jev_gate(value: str | None, name: str, default: str = "off") -> str:
    if value is None or not str(value).strip():
        return default
    normalized = value.strip().lower()
    # Migrate settings written by the removed observation-only mode.  This is
    # deliberately done at the boundary so runtime code only handles two
    # states and an upgrade cannot silently turn JEV off.
    if normalized == "shadow":
        logger.warning("%s=shadow 已废弃，按 enforce 读取", name)
        return "enforce"
    if normalized in _ALLOWED_JEV_GATES:
        return normalized
    raise ConfigError(
        f"配置项 {name} 必须是 ['off', 'enforce'] 之一，当前值: {value!r}"
    )


def _parse_log_level(value: str | None) -> str:
    """解析日志级别。"""
    if value is None:
        return "INFO"
    normalized = value.strip().upper()
    if normalized not in _ALLOWED_LOG_LEVELS:
        raise ConfigError(
            "配置项 EXCELMANUS_LOG_LEVEL 必须是 "
            f"{sorted(_ALLOWED_LOG_LEVELS)} 之一，当前值: {value!r}"
        )
    return normalized


def _parse_tool_schema_validation_mode(value: str | None) -> str:
    """解析工具参数 schema 校验模式。"""
    if value is None:
        return "shadow"
    normalized = value.strip().lower()
    if normalized in {"off", "shadow", "enforce"}:
        return normalized
    raise ConfigError(
        "配置项 EXCELMANUS_TOOL_SCHEMA_VALIDATION_MODE 必须是 "
        "['off', 'shadow', 'enforce'] 之一，"
        f"当前值: {value!r}"
    )


def _parse_protocol(value: str | None, name: str = "protocol") -> str:
    """解析协议类型，非法值自动回退 auto。"""
    if value is None or not value.strip():
        return "auto"
    normalized = value.strip().lower()
    if normalized in _ALLOWED_PROTOCOLS:
        return normalized
    logger.warning(
        "配置项 %s 非法(%r)，已回退为 auto",
        name, value,
    )
    return "auto"


def _detect_deploy_mode() -> str:
    """自动推断部署模式。默认 standalone；server 只能通过进程定位符显式指定。"""
    return "standalone"


DEFAULT_FRONTEND_PORT = "3000"
# 浏览器把 localhost / 127.0.0.1 / ::1 视为不同源，缺一则直连 :8000 的 health/SSE 会被拦。
LOOPBACK_CORS_HOSTS = ("localhost", "127.0.0.1", "[::1]")
_LOOPBACK_HOST_ALIASES = frozenset({"localhost", "127.0.0.1", "::1", "[::1]"})


def parse_frontend_ports(raw: str | None = None) -> tuple[str, ...]:
    """解析 EXCELMANUS_FRONTEND_PORT（逗号分隔）。空值回退 3000。"""
    text = DEFAULT_FRONTEND_PORT if raw is None else raw.strip()
    ports = tuple(item.strip() for item in text.split(",") if item.strip())
    return ports or (DEFAULT_FRONTEND_PORT,)


def _http_origin(host: str, port: str) -> str:
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"http://{host}:{port}"


def expand_cors_origins(
    configured: Iterable[str],
    *,
    frontend_ports: Iterable[str] | None = None,
    extra_hosts: Iterable[str] = (),
) -> list[str]:
    """合并显式 CORS 来源与本机 loopback / 局域网前端源。

    只写 localhost 时，用 127.0.0.1 打开前端会被浏览器判为跨域。
    启动时固定补上 loopback，再按需叠加 LAN IP。
    """
    origins = {item.strip() for item in configured if item and item.strip()}
    ports = tuple(str(port).strip() for port in (frontend_ports or ()) if str(port).strip())
    ports = ports or (DEFAULT_FRONTEND_PORT,)
    hosts: list[str] = list(LOOPBACK_CORS_HOSTS)
    for host in extra_hosts:
        cleaned = (host or "").strip()
        if not cleaned or cleaned.startswith("127.") or cleaned.lower() in _LOOPBACK_HOST_ALIASES:
            continue
        hosts.append(cleaned)
    for host in hosts:
        for port in ports:
            origins.add(_http_origin(host, port))
    return sorted(origins)


def _parse_cors_allow_origins() -> tuple[str, ...]:
    """解析 CORS 允许来源列表。"""
    cors_raw = _s("EXCELMANUS_CORS_ALLOW_ORIGINS")
    if cors_raw is not None:
        return tuple(o.strip() for o in cors_raw.split(",") if o.strip())
    return ("http://localhost:3000", "http://127.0.0.1:3000")


def load_cors_allow_origins() -> tuple[str, ...]:
    """解析 CORS 允许来源列表（逗号分隔，空字符串将被忽略）。"""
    load_runtime_env()
    return _parse_cors_allow_origins()


def _parse_csv_tuple(value: str | None) -> tuple[str, ...]:
    if value is None:
        return ()
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _load_context_optimization_config(
    model: str = "", canonical: str = "",
) -> _ContextOptimizationConfig:
    """加载上下文优化相关配置，避免字段声明/解析/回填三处漂移。

    优先级：EXCELMANUS_MAX_CONTEXT_TOKENS 设置 > 模型自动推断 > 默认 256k。
    智能匹配开启时优先用绑定的规范模型名推断。
    """
    env_max_ctx = _s("EXCELMANUS_MAX_CONTEXT_TOKENS")
    if env_max_ctx:
        max_context_tokens = _parse_int(env_max_ctx, "EXCELMANUS_MAX_CONTEXT_TOKENS", _DEFAULT_CONTEXT_TOKENS)
    elif model or canonical:
        max_context_tokens = _infer_context_tokens_for_model(canonical or model)
    else:
        max_context_tokens = _DEFAULT_CONTEXT_TOKENS
    retention_raw = (_s("EXCELMANUS_PROMPT_CACHE_RETENTION") or "").strip().lower()
    if retention_raw in ("", "default", "extended"):
        prompt_cache_retention = retention_raw or "default"
    else:
        logger.warning(
            "EXCELMANUS_PROMPT_CACHE_RETENTION=%r 无效，回退到 default",
            retention_raw,
        )
        prompt_cache_retention = "default"
    return _ContextOptimizationConfig(
        max_context_tokens=max_context_tokens,
        prompt_cache_key_enabled=_parse_bool(
            _s("EXCELMANUS_PROMPT_CACHE_KEY_ENABLED"),
            "EXCELMANUS_PROMPT_CACHE_KEY_ENABLED",
            True,
        ),
        prompt_cache_retention=prompt_cache_retention,
        compaction_enabled=_parse_bool(
            _s("EXCELMANUS_COMPACTION_ENABLED"),
            "EXCELMANUS_COMPACTION_ENABLED",
            True,
        ),
        compaction_threshold_ratio=_parse_float_between_zero_and_one(
            _s("EXCELMANUS_COMPACTION_THRESHOLD_RATIO"),
            "EXCELMANUS_COMPACTION_THRESHOLD_RATIO",
            0.85,
        ),
        compaction_keep_recent_turns=_parse_int(
            _s("EXCELMANUS_COMPACTION_KEEP_RECENT_TURNS"),
            "EXCELMANUS_COMPACTION_KEEP_RECENT_TURNS",
            5,
        ),
        compaction_max_summary_tokens=_parse_int(
            _s("EXCELMANUS_COMPACTION_MAX_SUMMARY_TOKENS"),
            "EXCELMANUS_COMPACTION_MAX_SUMMARY_TOKENS",
            4096,
        ),
    )


def load_config(values: Mapping[str, str] | None = None, *, allow_incomplete: bool = False) -> ExcelManusConfig:
    """加载配置。用户设置以主库 / 覆盖层为准；缺省用默认值。

    模型凭证以数据库档案为准；缺失时抛出 ConfigError。
    allow_incomplete 仅用于 API 首次配置时恢复非模型设置，不补造模型凭证。
    ``values`` 仅供单元测试传入一份临时映射。
    """
    from excelmanus.settings_runtime import credentials_from_store, models_from_store, using_values

    if values is not None:
        with using_values(values):
            return load_config(None, allow_incomplete=allow_incomplete)

    load_runtime_env()

    api_key = _s("EXCELMANUS_API_KEY") or ""
    base_url = _s("EXCELMANUS_BASE_URL") or ""
    model = _s("EXCELMANUS_MODEL") or ""
    protocol = _parse_protocol(_s("EXCELMANUS_PROTOCOL"), "EXCELMANUS_PROTOCOL")

    creds: dict[str, str] = {}
    if not api_key or not base_url or not model:
        creds = credentials_from_store()
        api_key = api_key or creds.get("api_key", "")
        base_url = base_url or creds.get("base_url", "")
        model = model or creds.get("model", "")
        if not _s("EXCELMANUS_PROTOCOL") and creds.get("protocol"):
            protocol = _parse_protocol(creds.get("protocol"), "EXCELMANUS_PROTOCOL")
    canonical_model = (creds.get("canonical_model") or "").strip()

    if not api_key and not allow_incomplete:
        raise ConfigError(
            "缺少必填配置项 EXCELMANUS_API_KEY。"
            "请在设置页面添加模型档案。"
        )
    if not base_url and not allow_incomplete:
        raise ConfigError(
            "缺少必填配置项 EXCELMANUS_BASE_URL。"
            "请在设置页面添加模型档案。"
        )
    if base_url:
        _validate_base_url(base_url)
        base_url = _normalize_base_url(base_url, protocol=protocol, env_name="EXCELMANUS_BASE_URL", model=model, api_key=api_key)

    if not model:
        from excelmanus.providers.gemini import _extract_model_from_url
        extracted = _extract_model_from_url(base_url)
        if extracted:
            model = extracted
    if not model and not allow_incomplete:
        raise ConfigError(
            "缺少必填配置项 EXCELMANUS_MODEL。"
            "请在设置页面添加模型档案。"
            "（Gemini 用户也可在 BASE_URL 中包含模型名，如 .../models/gemini-3.8-flash:generateContent）"
        )
    _log_deprecated_model_warning("EXCELMANUS_MODEL", model)

    max_iterations = _parse_int(
        _s("EXCELMANUS_MAX_ITERATIONS"), "EXCELMANUS_MAX_ITERATIONS", 120
    )
    turn_timeout_seconds = _parse_int_allow_zero(
        _s("EXCELMANUS_TURN_TIMEOUT_SECONDS"),
        "EXCELMANUS_TURN_TIMEOUT_SECONDS",
        0,
    )
    responses_continuation_enabled = _parse_bool(
        _s("EXCELMANUS_RESPONSES_CONTINUATION_ENABLED"),
        "EXCELMANUS_RESPONSES_CONTINUATION_ENABLED",
        False,
    )
    responses_background_enabled = _parse_bool(
        _s("EXCELMANUS_RESPONSES_BACKGROUND_ENABLED"),
        "EXCELMANUS_RESPONSES_BACKGROUND_ENABLED",
        False,
    )
    turn_token_budget = _parse_int_allow_zero(
        _s("EXCELMANUS_TURN_TOKEN_BUDGET"),
        "EXCELMANUS_TURN_TOKEN_BUDGET",
        0,
    )
    turn_cost_budget_usd = _parse_nonnegative_float(
        _s("EXCELMANUS_TURN_COST_BUDGET_USD"),
        "EXCELMANUS_TURN_COST_BUDGET_USD",
        0.0,
    )
    input_cost_per_1k_usd = _parse_nonnegative_float(
        _s("EXCELMANUS_INPUT_COST_PER_1K_USD"),
        "EXCELMANUS_INPUT_COST_PER_1K_USD",
        0.0,
    )
    output_cost_per_1k_usd = _parse_nonnegative_float(
        _s("EXCELMANUS_OUTPUT_COST_PER_1K_USD"),
        "EXCELMANUS_OUTPUT_COST_PER_1K_USD",
        0.0,
    )
    max_consecutive_failures = _parse_int(
        _s("EXCELMANUS_MAX_CONSECUTIVE_FAILURES"),
        "EXCELMANUS_MAX_CONSECUTIVE_FAILURES",
        6,
    )
    session_ttl_seconds = _parse_int(
        _s("EXCELMANUS_SESSION_TTL_SECONDS"),
        "EXCELMANUS_SESSION_TTL_SECONDS",
        1800,
    )
    max_sessions = _parse_int(
        _s("EXCELMANUS_MAX_SESSIONS"), "EXCELMANUS_MAX_SESSIONS", 1000
    )

    workspace_root = _s("EXCELMANUS_WORKSPACE_ROOT") or str(get_data_home())
    data_root = os.environ.get("EXCELMANUS_DATA_ROOT", "").strip()

    # 部署模式推断
    deploy_mode_raw = os.environ.get("EXCELMANUS_DEPLOY_MODE", "auto").strip().lower()
    if deploy_mode_raw in ("standalone", "server"):
        deploy_mode = deploy_mode_raw
    else:
        # auto / 未知值（含已废弃的 docker）都走 standalone
        deploy_mode = _detect_deploy_mode()
    log_level = _parse_log_level(_s("EXCELMANUS_LOG_LEVEL"))
    default_system_skill_dir = (
        Path(__file__).resolve().parent / "skillpacks" / "system"
    )
    default_project_skill_dir = Path(workspace_root) / ".excelmanus" / "skillpacks"
    skills_system_dir = _s(
        "EXCELMANUS_SKILLS_SYSTEM_DIR", str(default_system_skill_dir)
    )
    skills_user_dir = _s(
        "EXCELMANUS_SKILLS_USER_DIR", "~/.excelmanus/skillpacks"
    )
    skills_project_dir = _s(
        "EXCELMANUS_SKILLS_PROJECT_DIR", str(default_project_skill_dir)
    )
    skills_context_char_budget = _parse_int_allow_zero(
        _s("EXCELMANUS_SKILLS_CONTEXT_CHAR_BUDGET"),
        "EXCELMANUS_SKILLS_CONTEXT_CHAR_BUDGET",
        12000,
    )
    skills_discovery_enabled = _parse_bool(
        _s("EXCELMANUS_SKILLS_DISCOVERY_ENABLED"),
        "EXCELMANUS_SKILLS_DISCOVERY_ENABLED",
        True,
    )
    skills_discovery_scan_workspace_ancestors = _parse_bool(
        _s("EXCELMANUS_SKILLS_DISCOVERY_SCAN_WORKSPACE_ANCESTORS"),
        "EXCELMANUS_SKILLS_DISCOVERY_SCAN_WORKSPACE_ANCESTORS",
        True,
    )
    skills_discovery_include_agents = _parse_bool(
        _s("EXCELMANUS_SKILLS_DISCOVERY_INCLUDE_AGENTS"),
        "EXCELMANUS_SKILLS_DISCOVERY_INCLUDE_AGENTS",
        True,
    )
    skills_discovery_scan_external_tool_dirs = _parse_bool(
        _s("EXCELMANUS_SKILLS_DISCOVERY_SCAN_EXTERNAL_TOOL_DIRS"),
        "EXCELMANUS_SKILLS_DISCOVERY_SCAN_EXTERNAL_TOOL_DIRS",
        True,
    )
    skills_discovery_extra_dirs = _parse_csv_tuple(
        _s("EXCELMANUS_SKILLS_DISCOVERY_EXTRA_DIRS")
    )
    tool_result_hard_cap_chars = _parse_int_allow_zero(
        _s("EXCELMANUS_TOOL_RESULT_HARD_CAP_CHARS"),
        "EXCELMANUS_TOOL_RESULT_HARD_CAP_CHARS",
        12000,
    )
    cors_allow_origins = _parse_cors_allow_origins()
    mcp_shared_manager = _parse_bool(
        _s("EXCELMANUS_MCP_SHARED_MANAGER"),
        "EXCELMANUS_MCP_SHARED_MANAGER",
        False,
    )
    pool_enabled = _parse_bool(
        _s("EXCELMANUS_POOL_ENABLED"),
        "EXCELMANUS_POOL_ENABLED",
        False,
    )
    pool_auto_enabled = _parse_bool(
        _s("EXCELMANUS_POOL_AUTO_ENABLED"),
        "EXCELMANUS_POOL_AUTO_ENABLED",
        False,
    )
    pool_auto_interval_seconds = int(
        _s("EXCELMANUS_POOL_AUTO_INTERVAL", "60"),
    )
    pool_auto_default_cooldown_seconds = int(
        _s("EXCELMANUS_POOL_AUTO_COOLDOWN", "300"),
    )
    pool_auto_hysteresis_delta = float(
        _s("EXCELMANUS_POOL_AUTO_HYSTERESIS_DELTA", "0.12"),
    )
    pool_auto_min_dwell_seconds = int(
        _s("EXCELMANUS_POOL_AUTO_MIN_DWELL", "180"),
    )
    pool_auto_breaker_open_seconds = int(
        _s("EXCELMANUS_POOL_AUTO_BREAKER_OPEN", "120"),
    )
    pool_auto_breaker_threshold = int(
        _s("EXCELMANUS_POOL_AUTO_BREAKER_THRESHOLD", "5"),
    )
    exa_search_enabled = _parse_bool(
        _s("EXCELMANUS_EXA_SEARCH"),
        "EXCELMANUS_EXA_SEARCH",
        True,
    )
    _allowed_search_providers = {"exa", "tavily", "brave"}
    search_default_provider = (
        _s("EXCELMANUS_SEARCH_DEFAULT", "exa").strip().lower()
    )
    if search_default_provider not in _allowed_search_providers:
        logger.warning(
            "配置项 EXCELMANUS_SEARCH_DEFAULT 值无效(%r)，回退默认值 exa",
            search_default_provider,
        )
        search_default_provider = "exa"
    exa_api_key = _s("EXCELMANUS_EXA_API_KEY") or None
    tavily_api_key = _s("EXCELMANUS_TAVILY_API_KEY") or None
    brave_api_key = _s("EXCELMANUS_BRAVE_API_KEY") or None

    # 能力探测任务配置
    cap_probe_job_concurrency = int(_s("CAP_PROBE_JOB_CONCURRENCY", "2"))
    cap_probe_provider_concurrency = int(_s("CAP_PROBE_PROVIDER_CONCURRENCY", "1"))
    cap_probe_health_timeout = float(_s("CAP_PROBE_HEALTH_TIMEOUT", "8"))
    cap_probe_tool_timeout = float(_s("CAP_PROBE_TOOL_TIMEOUT", "20"))
    cap_probe_vision_timeout = float(_s("CAP_PROBE_VISION_TIMEOUT", "20"))
    cap_probe_thinking_total_timeout = float(_s("CAP_PROBE_THINKING_TOTAL_TIMEOUT", "30"))
    cap_probe_thinking_strategy_timeout = float(_s("CAP_PROBE_THINKING_STRATEGY_TIMEOUT", "8"))

    # subagent 执行配置
    subagent_enabled = _parse_bool(
        _s("EXCELMANUS_SUBAGENT_ENABLED"),
        "EXCELMANUS_SUBAGENT_ENABLED",
        True,
    )
    parallel_readonly_tools = _parse_bool(
        _s("EXCELMANUS_PARALLEL_READONLY_TOOLS"),
        "EXCELMANUS_PARALLEL_READONLY_TOOLS",
        True,
    )
    parallel_tool_max = _parse_int(_s("EXCELMANUS_PARALLEL_TOOL_MAX"), "EXCELMANUS_PARALLEL_TOOL_MAX", 4)
    if parallel_tool_max > 32:
        raise ConfigError("EXCELMANUS_PARALLEL_TOOL_MAX 不能超过 32")
    subagent_max_iterations = _parse_int(
        _s("EXCELMANUS_SUBAGENT_MAX_ITERATIONS"),
        "EXCELMANUS_SUBAGENT_MAX_ITERATIONS",
        120,
    )
    subagent_max_consecutive_failures = _parse_int(
        _s("EXCELMANUS_SUBAGENT_MAX_CONSECUTIVE_FAILURES"),
        "EXCELMANUS_SUBAGENT_MAX_CONSECUTIVE_FAILURES",
        6,
    )
    subagent_timeout_seconds = _parse_int(
        _s("EXCELMANUS_SUBAGENT_TIMEOUT_SECONDS"),
        "EXCELMANUS_SUBAGENT_TIMEOUT_SECONDS",
        600,
    )
    parallel_subagent_max = _parse_int(
        _s("EXCELMANUS_PARALLEL_SUBAGENT_MAX"),
        "EXCELMANUS_PARALLEL_SUBAGENT_MAX",
        3,
    )
    subagent_user_dir = _s(
        "EXCELMANUS_SUBAGENT_USER_DIR",
        "~/.excelmanus/agents",
    )
    subagent_project_dir = _s(
        "EXCELMANUS_SUBAGENT_PROJECT_DIR",
        str(Path(workspace_root) / ".excelmanus" / "agents"),
    )

    # 跨会话持久记忆配置
    memory_enabled = _parse_bool(
        _s("EXCELMANUS_MEMORY_ENABLED"),
        "EXCELMANUS_MEMORY_ENABLED",
        True,
    )
    memory_dir = _s("EXCELMANUS_MEMORY_DIR", "~/.excelmanus/memory")
    memory_auto_load_lines = _parse_int(
        _s("EXCELMANUS_MEMORY_AUTO_LOAD_LINES"),
        "EXCELMANUS_MEMORY_AUTO_LOAD_LINES",
        200,
    )
    memory_expire_days = _parse_int_allow_zero(
        _s("EXCELMANUS_MEMORY_EXPIRE_DAYS"),
        "EXCELMANUS_MEMORY_EXPIRE_DAYS",
        90,
    )
    memory_maintenance_enabled = _parse_bool(
        _s("EXCELMANUS_MEMORY_MAINTENANCE_ENABLED"),
        "EXCELMANUS_MEMORY_MAINTENANCE_ENABLED",
        False,
    )
    memory_maintenance_min_entries = _parse_int(
        _s("EXCELMANUS_MEMORY_MAINTENANCE_MIN_ENTRIES"),
        "EXCELMANUS_MEMORY_MAINTENANCE_MIN_ENTRIES",
        10,
    )
    memory_maintenance_new_threshold = _parse_int(
        _s("EXCELMANUS_MEMORY_MAINTENANCE_NEW_THRESHOLD"),
        "EXCELMANUS_MEMORY_MAINTENANCE_NEW_THRESHOLD",
        5,
    )
    memory_maintenance_interval_hours = _parse_positive_float(
        _s("EXCELMANUS_MEMORY_MAINTENANCE_INTERVAL_HOURS"),
        "EXCELMANUS_MEMORY_MAINTENANCE_INTERVAL_HOURS",
        4.0,
    )
    _memory_maintenance_model = (_s("EXCELMANUS_MEMORY_MAINTENANCE_MODEL") or "").strip()
    memory_maintenance_model = _memory_maintenance_model or None
    llm_retry_max_attempts = _parse_int(
        _s("EXCELMANUS_LLM_RETRY_MAX_ATTEMPTS"),
        "EXCELMANUS_LLM_RETRY_MAX_ATTEMPTS",
        3,
    )
    llm_retry_base_delay_seconds = _parse_positive_float(
        _s("EXCELMANUS_LLM_RETRY_BASE_DELAY_SECONDS"),
        "EXCELMANUS_LLM_RETRY_BASE_DELAY_SECONDS",
        2.0,
    )
    llm_retry_max_delay_seconds = _parse_positive_float(
        _s("EXCELMANUS_LLM_RETRY_MAX_DELAY_SECONDS"),
        "EXCELMANUS_LLM_RETRY_MAX_DELAY_SECONDS",
        30.0,
    )
    image_pixel_budget = _parse_image_pixel_budget(
        _s("EXCELMANUS_IMAGE_PIXEL_BUDGET"),
    )
    image_max_bytes = _parse_int(
        _s("EXCELMANUS_IMAGE_MAX_BYTES"),
        "EXCELMANUS_IMAGE_MAX_BYTES",
        1_048_576,
    )
    image_files_api = (
        _s("EXCELMANUS_IMAGE_FILES_API", "auto").strip().lower() or "auto"
    )
    if image_files_api not in ("auto", "true", "false"):
        logger.warning("EXCELMANUS_IMAGE_FILES_API=%r 无效，回退到 auto", image_files_api)
        image_files_api = "auto"
    friendly_error_messages = _parse_bool(
        _s("EXCELMANUS_FRIENDLY_ERROR_MESSAGES"),
        "EXCELMANUS_FRIENDLY_ERROR_MESSAGES",
        True,
    )
    context_optimization = _load_context_optimization_config(
        model=model, canonical=canonical_model,
    )
    hooks_command_enabled = _parse_bool(
        _s("EXCELMANUS_HOOKS_COMMAND_ENABLED"),
        "EXCELMANUS_HOOKS_COMMAND_ENABLED",
        False,
    )
    hooks_command_allowlist = _parse_csv_tuple(
        _s("EXCELMANUS_HOOKS_COMMAND_ALLOWLIST")
    )
    hooks_command_timeout_seconds = _parse_int(
        _s("EXCELMANUS_HOOKS_COMMAND_TIMEOUT_SECONDS"),
        "EXCELMANUS_HOOKS_COMMAND_TIMEOUT_SECONDS",
        10,
    )
    hooks_output_max_chars = _parse_int(
        _s("EXCELMANUS_HOOKS_OUTPUT_MAX_CHARS"),
        "EXCELMANUS_HOOKS_OUTPUT_MAX_CHARS",
        32000,
    )

    main_model_vision = (
        _s("EXCELMANUS_MAIN_MODEL_VISION", "auto").strip().lower()
    )
    if main_model_vision not in ("auto", "true", "false"):
        logger.warning(
            "EXCELMANUS_MAIN_MODEL_VISION=%r 无效，回退到 'auto'",
            main_model_vision,
        )
        main_model_vision = "auto"

    # 代码策略引擎配置
    code_policy_enabled = _parse_bool(
        _s("EXCELMANUS_CODE_POLICY_ENABLED"),
        "EXCELMANUS_CODE_POLICY_ENABLED",
        True,
    )
    code_policy_green_auto_approve = _parse_bool(
        _s("EXCELMANUS_CODE_POLICY_GREEN_AUTO"),
        "EXCELMANUS_CODE_POLICY_GREEN_AUTO",
        True,
    )
    code_policy_yellow_auto_approve = _parse_bool(
        _s("EXCELMANUS_CODE_POLICY_YELLOW_AUTO"),
        "EXCELMANUS_CODE_POLICY_YELLOW_AUTO",
        False,
    )
    code_policy_extra_safe_modules = _parse_csv_tuple(
        _s("EXCELMANUS_CODE_POLICY_EXTRA_SAFE")
    )
    code_policy_extra_blocked_modules = _parse_csv_tuple(
        _s("EXCELMANUS_CODE_POLICY_EXTRA_BLOCKED")
    )
    tool_schema_validation_mode = _parse_tool_schema_validation_mode(
        _s("EXCELMANUS_TOOL_SCHEMA_VALIDATION_MODE")
    )
    tool_schema_validation_canary_percent = _parse_int_allow_zero(
        _s("EXCELMANUS_TOOL_SCHEMA_VALIDATION_CANARY_PERCENT"),
        "EXCELMANUS_TOOL_SCHEMA_VALIDATION_CANARY_PERCENT",
        100,
    )
    if tool_schema_validation_canary_percent > 100:
        raise ConfigError(
            "配置项 EXCELMANUS_TOOL_SCHEMA_VALIDATION_CANARY_PERCENT 必须在 0..100，"
            f"当前值: {tool_schema_validation_canary_percent}"
        )
    tool_schema_strict_path = _parse_bool(
        _s("EXCELMANUS_TOOL_SCHEMA_STRICT_PATH"),
        "EXCELMANUS_TOOL_SCHEMA_STRICT_PATH",
        False,
    )

    # 历史会话感知（Session Summary）配置
    session_summary_enabled = _parse_bool(
        _s("EXCELMANUS_SESSION_SUMMARY_ENABLED"),
        "EXCELMANUS_SESSION_SUMMARY_ENABLED",
        False,
    )
    session_summary_min_turns = _parse_int(
        _s("EXCELMANUS_SESSION_SUMMARY_MIN_TURNS"),
        "EXCELMANUS_SESSION_SUMMARY_MIN_TURNS",
        3,
    )
    # 聊天记录持久化
    chat_history_enabled = _parse_bool(
        _s("EXCELMANUS_CHAT_HISTORY_ENABLED"),
        "EXCELMANUS_CHAT_HISTORY_ENABLED",
        True,
    )
    from excelmanus.data_home import resolve_db_path

    leftover_pg_url = os.environ.get("EXCELMANUS_DATABASE_URL", "").strip()
    if leftover_pg_url:
        logger.warning(
            "EXCELMANUS_DATABASE_URL 已废弃并被忽略；ExcelManus 仅使用 SQLite（EXCELMANUS_DB_PATH）"
        )
    db_path = resolve_db_path()
    chat_history_db_path = db_path

    # Thinking（推理深度）配置
    thinking_effort_raw = (_s("EXCELMANUS_THINKING_EFFORT") or "medium").strip().lower()
    if thinking_effort_raw not in _ALLOWED_THINKING_EFFORTS:
        logger.warning(
            "EXCELMANUS_THINKING_EFFORT=%r 无效，回退到 'medium'",
            thinking_effort_raw,
        )
        thinking_effort_raw = "medium"
    thinking_budget = _parse_int_allow_zero(
        _s("EXCELMANUS_THINKING_BUDGET"),
        "EXCELMANUS_THINKING_BUDGET",
        0,
    )
    configured_effort_options = set(
        _parse_csv_tuple(_s("EXCELMANUS_THINKING_EFFORT_OPTIONS"))
    )
    thinking_effort_options = tuple(
        effort for effort in THINKING_EFFORT_ORDER
        if effort in configured_effort_options
    ) or THINKING_EFFORT_ORDER

    models = models_from_store()

    jev_enabled = _parse_jev_gate(
        _s("EXCELMANUS_JEV_ENABLED"), "EXCELMANUS_JEV_ENABLED", "enforce"
    )
    jev_exposure = _parse_jev_gate(
        _s("EXCELMANUS_JEV_EXPOSURE"), "EXCELMANUS_JEV_EXPOSURE", "enforce"
    )
    jev_mode_hint = _parse_bool(
        _s("EXCELMANUS_JEV_MODE_HINT"), "EXCELMANUS_JEV_MODE_HINT", True
    )
    jev_observation = _parse_jev_gate(
        _s("EXCELMANUS_JEV_OBSERVATION"), "EXCELMANUS_JEV_OBSERVATION", "enforce"
    )
    jev_verification = _parse_jev_gate(
        _s("EXCELMANUS_JEV_VERIFICATION"), "EXCELMANUS_JEV_VERIFICATION", "enforce"
    )
    jev_recovery = _parse_jev_gate(
        _s("EXCELMANUS_JEV_RECOVERY"), "EXCELMANUS_JEV_RECOVERY", "enforce"
    )
    jev_ui_hint = _parse_bool(
        _s("EXCELMANUS_JEV_UI_HINT"), "EXCELMANUS_JEV_UI_HINT", True
    )
    jev_model = (_s("EXCELMANUS_JEV_MODEL") or "jev-1.13.0").strip() or "jev-1.13.0"
    typesafe_api_key = (_s("EXCELMANUS_TYPESAFE_API_KEY") or "").strip() or None
    ai_gateway_api_key = (_s("EXCELMANUS_AI_GATEWAY_API_KEY") or "").strip() or None
    jev_active_provider = (_s("EXCELMANUS_JEV_ACTIVE_PROVIDER") or "").strip()
    from excelmanus.system_one.providers import parse_jev_providers_json

    jev_providers = tuple(
        parse_jev_providers_json(_s("EXCELMANUS_JEV_PROVIDERS"))
    )
    jev_timeout_seconds = min(
        10.0,
        _parse_positive_float(
            _s("EXCELMANUS_JEV_TIMEOUT_SECONDS"),
            "EXCELMANUS_JEV_TIMEOUT_SECONDS",
            1.5,
        ),
    )
    jev_calibrated = _parse_bool(
        _s("EXCELMANUS_JEV_CALIBRATED"),
        "EXCELMANUS_JEV_CALIBRATED",
        False,
    )

    return ExcelManusConfig(
        api_key=api_key,
        base_url=base_url,
        model=model,
        protocol=protocol,
        responses_continuation_enabled=responses_continuation_enabled,
        responses_background_enabled=responses_background_enabled,
        max_iterations=max_iterations,
        turn_timeout_seconds=turn_timeout_seconds,
        turn_token_budget=turn_token_budget,
        turn_cost_budget_usd=turn_cost_budget_usd,
        input_cost_per_1k_usd=input_cost_per_1k_usd,
        output_cost_per_1k_usd=output_cost_per_1k_usd,
        max_consecutive_failures=max_consecutive_failures,
        session_ttl_seconds=session_ttl_seconds,
        max_sessions=max_sessions,
        workspace_root=workspace_root,
        data_root=data_root,
        deploy_mode=deploy_mode,
        log_level=log_level,
        skills_system_dir=skills_system_dir,
        skills_user_dir=skills_user_dir,
        skills_project_dir=skills_project_dir,
        skills_context_char_budget=skills_context_char_budget,
        skills_discovery_enabled=skills_discovery_enabled,
        skills_discovery_scan_workspace_ancestors=skills_discovery_scan_workspace_ancestors,
        skills_discovery_include_agents=skills_discovery_include_agents,
        skills_discovery_scan_external_tool_dirs=skills_discovery_scan_external_tool_dirs,
        skills_discovery_extra_dirs=skills_discovery_extra_dirs,
        tool_result_hard_cap_chars=tool_result_hard_cap_chars,
        cors_allow_origins=cors_allow_origins,
        mcp_shared_manager=mcp_shared_manager,
        pool_enabled=pool_enabled,
        pool_auto_enabled=pool_auto_enabled,
        pool_auto_interval_seconds=pool_auto_interval_seconds,
        pool_auto_default_cooldown_seconds=pool_auto_default_cooldown_seconds,
        pool_auto_hysteresis_delta=pool_auto_hysteresis_delta,
        pool_auto_min_dwell_seconds=pool_auto_min_dwell_seconds,
        pool_auto_breaker_open_seconds=pool_auto_breaker_open_seconds,
        pool_auto_breaker_threshold=pool_auto_breaker_threshold,
        exa_search_enabled=exa_search_enabled,
        search_default_provider=search_default_provider,
        exa_api_key=exa_api_key,
        tavily_api_key=tavily_api_key,
        brave_api_key=brave_api_key,
        cap_probe_job_concurrency=cap_probe_job_concurrency,
        cap_probe_provider_concurrency=cap_probe_provider_concurrency,
        cap_probe_health_timeout=cap_probe_health_timeout,
        cap_probe_tool_timeout=cap_probe_tool_timeout,
        cap_probe_vision_timeout=cap_probe_vision_timeout,
        cap_probe_thinking_total_timeout=cap_probe_thinking_total_timeout,
        cap_probe_thinking_strategy_timeout=cap_probe_thinking_strategy_timeout,
        subagent_enabled=subagent_enabled,
        agent_self_management_enabled=_parse_bool(
            _s("EXCELMANUS_AGENT_SELF_MANAGEMENT_ENABLED"),
            "EXCELMANUS_AGENT_SELF_MANAGEMENT_ENABLED", False,
        ),
        parallel_readonly_tools=parallel_readonly_tools,
        parallel_tool_max=parallel_tool_max,
        subagent_max_iterations=subagent_max_iterations,
        subagent_max_consecutive_failures=subagent_max_consecutive_failures,
        subagent_timeout_seconds=subagent_timeout_seconds,
        parallel_subagent_max=parallel_subagent_max,
        subagent_user_dir=subagent_user_dir,
        subagent_project_dir=subagent_project_dir,
        memory_enabled=memory_enabled,
        memory_dir=memory_dir,
        memory_auto_load_lines=memory_auto_load_lines,
        memory_expire_days=memory_expire_days,
        memory_maintenance_enabled=memory_maintenance_enabled,
        memory_maintenance_min_entries=memory_maintenance_min_entries,
        memory_maintenance_new_threshold=memory_maintenance_new_threshold,
        memory_maintenance_interval_hours=memory_maintenance_interval_hours,
        memory_maintenance_model=memory_maintenance_model,
        llm_retry_max_attempts=llm_retry_max_attempts,
        llm_retry_base_delay_seconds=llm_retry_base_delay_seconds,
        llm_retry_max_delay_seconds=llm_retry_max_delay_seconds,
        image_pixel_budget=image_pixel_budget,
        image_max_bytes=image_max_bytes,
        image_files_api=image_files_api,
        friendly_error_messages=friendly_error_messages,
        max_context_tokens=context_optimization.max_context_tokens,
        prompt_cache_key_enabled=context_optimization.prompt_cache_key_enabled,
        prompt_cache_retention=context_optimization.prompt_cache_retention,
        compaction_enabled=context_optimization.compaction_enabled,
        compaction_threshold_ratio=context_optimization.compaction_threshold_ratio,
        compaction_keep_recent_turns=context_optimization.compaction_keep_recent_turns,
        compaction_max_summary_tokens=context_optimization.compaction_max_summary_tokens,
        hooks_command_enabled=hooks_command_enabled,
        hooks_command_allowlist=hooks_command_allowlist,
        hooks_command_timeout_seconds=hooks_command_timeout_seconds,
        hooks_output_max_chars=hooks_output_max_chars,
        main_model_vision=main_model_vision,
        code_policy_enabled=code_policy_enabled,
        code_policy_green_auto_approve=code_policy_green_auto_approve,
        code_policy_yellow_auto_approve=code_policy_yellow_auto_approve,
        code_policy_extra_safe_modules=code_policy_extra_safe_modules,
        code_policy_extra_blocked_modules=code_policy_extra_blocked_modules,
        tool_schema_validation_mode=tool_schema_validation_mode,
        tool_schema_validation_canary_percent=tool_schema_validation_canary_percent,
        tool_schema_strict_path=tool_schema_strict_path,
        session_summary_enabled=session_summary_enabled,
        session_summary_min_turns=session_summary_min_turns,
        db_path=db_path,
        chat_history_enabled=chat_history_enabled,
        chat_history_db_path=chat_history_db_path,
        thinking_effort=thinking_effort_raw,
        thinking_budget=thinking_budget,
        thinking_effort_options=thinking_effort_options,
        models=models,
        jev_enabled=jev_enabled,
        jev_exposure=jev_exposure,
        jev_mode_hint=jev_mode_hint,
        jev_observation=jev_observation,
        jev_verification=jev_verification,
        jev_recovery=jev_recovery,
        jev_ui_hint=jev_ui_hint,
        jev_model=jev_model,
        typesafe_api_key=typesafe_api_key,
        ai_gateway_api_key=ai_gateway_api_key,
        jev_active_provider=jev_active_provider,
        jev_providers=jev_providers,
        jev_timeout_seconds=jev_timeout_seconds,
        jev_calibrated=jev_calibrated,
        canonical_model=canonical_model,
        model_canonical_match_enabled=_parse_bool(
            _s("EXCELMANUS_MODEL_CANONICAL_MATCH"),
            "EXCELMANUS_MODEL_CANONICAL_MATCH",
            True,
        ),
    )
