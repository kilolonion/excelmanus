"""认证提供商模块 —— 支持用户通过订阅令牌（OAuth）接入 AI 模型。

支持：OpenAI Codex（ChatGPT Plus/Pro 订阅）
"""

from excelmanus.auth.providers.base import (
    AuthProvider,
    ResolvedCredential,
    ValidatedCredential,
    RefreshedCredential,
    AuthProfileSummary,
)
from excelmanus.auth.providers.credential_store import CredentialStore
from excelmanus.auth.providers.openai_codex import OpenAICodexProvider
from excelmanus.auth.providers.resolver import CredentialResolver

__all__ = [
    "AuthProvider",
    "ResolvedCredential",
    "ValidatedCredential",
    "RefreshedCredential",
    "AuthProfileSummary",
    "CredentialStore",
    "OpenAICodexProvider",
    "CredentialResolver",
]
