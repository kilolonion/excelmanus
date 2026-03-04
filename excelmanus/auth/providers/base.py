"""认证提供商基类与数据类型定义。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ValidatedCredential:
    """经过验证的凭证数据（从用户粘贴的 token 解析而来）。"""

    access_token: str
    refresh_token: str | None
    expires_at: str  # ISO 8601
    account_id: str
    plan_type: str
    credential_type: str = "oauth"
    extra_data: dict[str, Any] | None = None


@dataclass(frozen=True)
class RefreshedCredential:
    """刷新后的凭证数据。"""

    access_token: str
    refresh_token: str | None  # 可能被 provider 轮换
    expires_at: str  # ISO 8601


@dataclass(frozen=True)
class ResolvedCredential:
    """运行时解析出的 LLM 调用凭证。"""

    api_key: str
    base_url: str
    source: str  # 'oauth' | 'user_key' | 'system'
    provider: str | None = None
    protocol: str = "openai"


@dataclass(frozen=True)
class AuthProfileSummary:
    """auth_profiles 表的摘要视图（不含明文 token）。"""

    id: str
    user_id: str
    provider: str
    profile_name: str
    credential_type: str
    expires_at: str | None
    account_id: str | None
    plan_type: str | None
    is_active: bool
    created_at: str
    updated_at: str


@dataclass
class AuthProfileRecord:
    """auth_profiles 表完整记录（含解密后的 token）。"""

    id: str
    user_id: str
    provider: str
    profile_name: str
    credential_type: str
    access_token: str | None
    refresh_token: str | None
    expires_at: str | None
    account_id: str | None
    plan_type: str | None
    extra_data: str | None
    is_active: bool
    created_at: str
    updated_at: str


class AuthProvider(ABC):
    """认证提供商抽象基类。"""

    provider_name: str = ""

    @abstractmethod
    def validate_token_data(self, raw_data: dict[str, Any]) -> ValidatedCredential:
        """验证用户粘贴的令牌数据，返回标准化凭证。

        Raises:
            ValueError: 令牌格式无效或缺少必要字段。
        """

    @abstractmethod
    async def refresh_token(self, refresh_token: str) -> RefreshedCredential:
        """刷新过期的 access token。

        Raises:
            RuntimeError: 刷新失败（网络错误、refresh token 过期等）。
        """

    @abstractmethod
    def get_api_credential(self, access_token: str) -> tuple[str, str]:
        """从 access token 获取 (api_key, base_url) 用于 LLM 调用。"""

    def matches_model(self, model: str) -> bool:
        """检查模型是否属于本 provider 管辖。默认返回 False。"""
        return False
