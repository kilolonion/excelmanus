"""LLM Provider 抽象层：根据 base_url 自动选择 OpenAI 或 Gemini 客户端。"""

from __future__ import annotations

import re

import openai

from excelmanus.providers.gemini import GeminiClient

# 匹配 Gemini 原生 API 的 base_url 模式
_GEMINI_URL_PATTERNS = (
    re.compile(r"generativelanguage\.googleapis\.com", re.IGNORECASE),
    re.compile(r"/gemini/", re.IGNORECASE),
    re.compile(r":generateContent", re.IGNORECASE),
    re.compile(r":streamGenerateContent", re.IGNORECASE),
)


def is_gemini_provider(base_url: str) -> bool:
    """判断 base_url 是否指向 Gemini 原生 API（非 OpenAI 兼容层）。"""
    for pattern in _GEMINI_URL_PATTERNS:
        if pattern.search(base_url):
            return True
    return False


def create_client(
    api_key: str,
    base_url: str,
) -> openai.AsyncOpenAI | GeminiClient:
    """根据 base_url 创建合适的 LLM 客户端。

    - Gemini 原生 API → GeminiClient（鸭子类型兼容 openai.AsyncOpenAI）
    - 其他 → 标准 openai.AsyncOpenAI
    """
    if is_gemini_provider(base_url):
        return GeminiClient(api_key=api_key, base_url=base_url)
    return openai.AsyncOpenAI(api_key=api_key, base_url=base_url)


__all__ = ["create_client", "is_gemini_provider", "GeminiClient"]
