"""LLM Provider 抽象层：根据 base_url 自动选择合适的客户端。

支持的 Provider：
  - OpenAI Chat Completions（默认，标准 openai.AsyncOpenAI）
  - Gemini 原生 API（自动检测 URL）
  - Claude / Anthropic 原生 API（自动检测 URL）
  - OpenAI Responses API（需通过环境变量 EXCELMANUS_USE_RESPONSES_API=1 启用）
"""

from __future__ import annotations

import os
import re

import openai

from excelmanus.providers.claude import ClaudeClient
from excelmanus.providers.gemini import GeminiClient
from excelmanus.providers.openai_responses import OpenAIResponsesClient

# ── URL 模式匹配 ─────────────────────────────────────────────

# Gemini 原生 API
_GEMINI_URL_PATTERNS = (
    re.compile(r"generativelanguage\.googleapis\.com", re.IGNORECASE),
    re.compile(r"/gemini/", re.IGNORECASE),
    re.compile(r":generateContent", re.IGNORECASE),
    re.compile(r":streamGenerateContent", re.IGNORECASE),
)

# Claude / Anthropic 原生 API
_CLAUDE_URL_PATTERNS = (
    re.compile(r"api\.anthropic\.com", re.IGNORECASE),
    re.compile(r"anthropic", re.IGNORECASE),
    re.compile(r"/claude/", re.IGNORECASE),
)


def is_gemini_provider(base_url: str) -> bool:
    """判断 base_url 是否指向 Gemini 原生 API（非 OpenAI 兼容层）。"""
    for pattern in _GEMINI_URL_PATTERNS:
        if pattern.search(base_url):
            return True
    return False


def is_claude_provider(base_url: str) -> bool:
    """判断 base_url 是否指向 Claude / Anthropic 原生 API。"""
    for pattern in _CLAUDE_URL_PATTERNS:
        if pattern.search(base_url):
            return True
    return False


def is_responses_api_enabled() -> bool:
    """判断是否启用 OpenAI Responses API 模式。

    通过环境变量 EXCELMANUS_USE_RESPONSES_API=1 启用。
    """
    return os.environ.get("EXCELMANUS_USE_RESPONSES_API", "").strip() in ("1", "true", "yes")


def create_client(
    api_key: str,
    base_url: str,
    protocol: str = "auto",
) -> openai.AsyncOpenAI | GeminiClient | ClaudeClient | OpenAIResponsesClient:
    """根据 protocol（或 base_url 自动检测）创建合适的 LLM 客户端。

    protocol 参数：
      - "auto"（默认）：按 URL 模式自动检测，与旧行为一致
      - "openai"：强制使用 OpenAI Chat Completions API
      - "openai_responses"：强制使用 OpenAI Responses API
      - "anthropic"：强制使用 Claude / Anthropic 原生 API
      - "gemini"：强制使用 Gemini 原生 API

    auto 检测优先级：
      1. Gemini 原生 API → GeminiClient
      2. Claude / Anthropic 原生 API → ClaudeClient
      3. EXCELMANUS_USE_RESPONSES_API=1 → OpenAIResponsesClient
      4. 其他 → 标准 openai.AsyncOpenAI（Chat Completions）
    """
    normalized = (protocol or "auto").strip().lower()

    if normalized == "gemini":
        return GeminiClient(api_key=api_key, base_url=base_url)
    if normalized == "anthropic":
        return ClaudeClient(api_key=api_key, base_url=base_url)
    if normalized == "openai_responses":
        return OpenAIResponsesClient(api_key=api_key, base_url=base_url)
    if normalized == "openai":
        return openai.AsyncOpenAI(api_key=api_key, base_url=base_url)

    # auto: 按 URL 模式自动检测（旧行为）
    if is_gemini_provider(base_url):
        return GeminiClient(api_key=api_key, base_url=base_url)
    if is_claude_provider(base_url):
        return ClaudeClient(api_key=api_key, base_url=base_url)
    if is_responses_api_enabled():
        return OpenAIResponsesClient(api_key=api_key, base_url=base_url)
    return openai.AsyncOpenAI(api_key=api_key, base_url=base_url)


__all__ = [
    "create_client",
    "is_gemini_provider",
    "is_claude_provider",
    "is_responses_api_enabled",
    "GeminiClient",
    "ClaudeClient",
    "OpenAIResponsesClient",
]
