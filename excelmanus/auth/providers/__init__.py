"""认证提供商模块 —— 支持用户通过订阅令牌（OAuth）接入 AI 模型。

支持：OpenAI Codex（ChatGPT Plus/Pro 订阅）、WorkBuddy/CodeBuddy、
Google Antigravity（Cloud Code Assist 订阅）
"""

from excelmanus.auth.providers.base import (
    AuthProvider,
    BrowserPollCapable,
    LoopbackOAuthCapable,
    ResolvedCredential,
    ValidatedCredential,
    RefreshedCredential,
    AuthProfileSummary,
)
from excelmanus.auth.providers.antigravity import AntigravityProvider
from excelmanus.auth.providers.credential_store import CredentialStore
from excelmanus.auth.providers.openai_codex import OpenAICodexProvider
from excelmanus.auth.providers.resolver import CredentialResolver
from excelmanus.auth.providers.workbuddy import WorkBuddyProvider

__all__ = [
    "AuthProvider",
    "BrowserPollCapable",
    "LoopbackOAuthCapable",
    "ResolvedCredential",
    "ValidatedCredential",
    "RefreshedCredential",
    "AuthProfileSummary",
    "CredentialStore",
    "OpenAICodexProvider",
    "AntigravityProvider",
    "WorkBuddyProvider",
    "CredentialResolver",
]
