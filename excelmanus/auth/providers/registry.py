"""订阅提供商注册表 —— 统一管理所有 AuthProvider 实例。"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from excelmanus.auth.providers.base import AuthProvider, ProviderDescriptor

_PROVIDERS: dict[str, "AuthProvider"] = {}


def register(provider: "AuthProvider") -> None:
    """注册一个 provider 实例。"""
    _PROVIDERS[provider.provider_name] = provider


def get_provider(name: str) -> "AuthProvider | None":
    """按名称查找已注册的 provider。"""
    return _PROVIDERS.get(name)


def list_all() -> dict[str, "AuthProvider"]:
    """返回所有已注册 provider 的副本。"""
    return dict(_PROVIDERS)


def list_descriptors() -> list["ProviderDescriptor"]:
    """返回所有已注册 provider 的描述符列表。"""
    return [p.get_descriptor() for p in _PROVIDERS.values()]


def match_provider(model: str) -> str | None:
    """从规范化模型标识中提取 provider，并检查是否有对应的注册提供商。

    优先从 ``provider/raw_model`` 格式提取；回退到 regex 匹配。
    """
    from excelmanus.config import parse_canonical_model

    provider, _ = parse_canonical_model(model)
    if provider != "unknown" and provider in _PROVIDERS:
        return provider
    for name, prov in _PROVIDERS.items():
        if prov.matches_model(model):
            return name
    return None


# ── 自动注册所有内置 provider ──────────────────────────────────

def _register_builtins() -> None:
    from excelmanus.auth.providers.openai_codex import OpenAICodexProvider
    from excelmanus.auth.providers.google_gemini import GoogleGeminiProvider
    register(OpenAICodexProvider())
    register(GoogleGeminiProvider())


_register_builtins()
