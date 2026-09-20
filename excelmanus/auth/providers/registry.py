"""订阅提供商注册表 —— 统一管理所有 AuthProvider 实例。"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from excelmanus.auth.providers.base import AuthProvider

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


def match_provider(model: str) -> str | None:
    """识别模型/档案名归属的订阅 provider。

    优先按 ``MODEL_NAME_PREFIX`` 前缀匹配；回退到 provider 自定义匹配。
    """
    prov = managed_provider_for(model)
    if prov is not None:
        return prov.provider_name
    for name, p in _PROVIDERS.items():
        if p.matches_model(model):
            return name
    return None


def managed_provider_for(value: str) -> "AuthProvider | None":
    """value 以某 provider 的 ``MODEL_NAME_PREFIX`` 开头时返回该 provider。"""
    for p in _PROVIDERS.values():
        if p.MODEL_NAME_PREFIX and value.startswith(p.MODEL_NAME_PREFIX):
            return p
    return None


def strip_managed_prefix(value: str) -> str:
    """剥离订阅档案前缀得到真实模型 ID；非订阅档案原样返回。"""
    p = managed_provider_for(value)
    if p is None:
        return value
    return p.model_from_profile_name(value) or value[len(p.MODEL_NAME_PREFIX):]


# ── 自动注册所有内置 provider ──────────────────────────────────

def _register_builtins() -> None:
    from excelmanus.auth.providers.antigravity import AntigravityProvider
    from excelmanus.auth.providers.openai_codex import OpenAICodexProvider
    from excelmanus.auth.providers.workbuddy import WorkBuddyProvider
    register(OpenAICodexProvider())
    register(AntigravityProvider())
    for realm in WorkBuddyProvider.REALMS:
        register(WorkBuddyProvider(realm))


_register_builtins()
