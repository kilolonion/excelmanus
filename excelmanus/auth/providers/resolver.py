"""运行时凭证解析器 —— LLM 调用前确定使用哪个凭证。"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from excelmanus.auth.providers.base import AuthProfileRecord, ResolvedCredential
from excelmanus.auth.providers.openai_codex import OpenAICodexProvider
from excelmanus.auth.providers.google_gemini import GoogleGeminiProvider

if TYPE_CHECKING:
    from excelmanus.auth.providers.credential_store import CredentialStore
    from excelmanus.config import ExcelManusConfig

logger = logging.getLogger(__name__)

_PROVIDERS = {
    "openai-codex": OpenAICodexProvider(),
    "google-gemini": GoogleGeminiProvider(),
}


def _is_expiring_soon(expires_at: str | None, margin_seconds: int = 300) -> bool:
    """检查 token 是否即将过期（默认 5 分钟内）。"""
    if not expires_at:
        return True
    try:
        exp = datetime.fromisoformat(expires_at)
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        now = datetime.now(tz=timezone.utc)
        return (exp - now).total_seconds() < margin_seconds
    except (ValueError, TypeError):
        return True


class CredentialResolver:
    """运行时凭证解析器。

    解析优先级：
    1. auth_profiles 中匹配 provider 的 OAuth token（自动刷新）
    2. UserRecord.llm_api_key（用户自定义 API Key）
    3. ExcelManusConfig 全局默认
    """

    def __init__(
        self,
        credential_store: "CredentialStore | None" = None,
        user_store: Any = None,
        config: "ExcelManusConfig | None" = None,
    ) -> None:
        self._store = credential_store
        self._user_store = user_store
        self._config = config
        self._refresh_locks: dict[str, asyncio.Lock] = {}
        self._pool_service: Any = None  # PoolService，由 lifespan 注入
        self._pool_enabled: bool = False  # 灰度开关，由 lifespan 注入

    def _get_refresh_lock(self, user_id: str, provider_name: str) -> asyncio.Lock:
        """获取 (user_id, provider) 对应的 asyncio.Lock（懒创建）。"""
        key = f"{user_id}:{provider_name}"
        lock = self._refresh_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._refresh_locks[key] = lock
        return lock

    async def resolve(
        self, user_id: str | None, model: str
    ) -> ResolvedCredential | None:
        """解析凭证。返回 None 表示使用调用方的默认凭证。"""
        if not user_id:
            return None

        # 0. 号池优先：检查人工激活映射
        provider_name = self._match_provider(model)
        if provider_name:
            pool_resolved = self._try_pool_account(provider_name, model)
            if pool_resolved:
                return pool_resolved

        # 1. 匹配 provider → 用户 OAuth
        if provider_name and self._store:
            resolved = await self._try_oauth_profile(user_id, provider_name)
            if resolved:
                return resolved

        # 2. 用户自定义 API Key
        if self._user_store:
            user = self._user_store.get_by_id(user_id)
            if user and getattr(user, "llm_api_key", None):
                return ResolvedCredential(
                    api_key=user.llm_api_key,
                    base_url=getattr(user, "llm_base_url", "") or "",
                    source="user_key",
                    protocol="openai",
                )

        # 3. 回退到系统默认（返回 None，让调用方使用自己的默认配置）
        return None

    def resolve_sync(
        self, user_id: str | None, model: str
    ) -> ResolvedCredential | None:
        """同步版本 resolve —— 用于引擎创建时（不触发自动刷新，仅读已有凭证）。"""
        if not user_id or not self._store:
            return None
        provider_name = self._match_provider(model)
        if not provider_name:
            return None

        # 号池优先
        pool_resolved = self._try_pool_account(provider_name, model)
        if pool_resolved:
            return pool_resolved

        profile = self._store.get_active_profile(user_id, provider_name)
        if not profile or not profile.access_token:
            return None
        provider = _PROVIDERS.get(provider_name)
        if not provider:
            return None
        api_key, base_url = provider.get_api_credential(profile.access_token)
        _protocol = getattr(provider, "PROTOCOL", "openai")
        return ResolvedCredential(
            api_key=api_key,
            base_url=base_url,
            source="oauth",
            provider=provider_name,
            protocol=_protocol,
        )

    async def _try_oauth_profile(
        self, user_id: str, provider_name: str
    ) -> ResolvedCredential | None:
        """尝试使用 OAuth profile，必要时自动刷新（带锁 + double-check）。"""
        if not self._store:
            return None

        profile = self._store.get_active_profile(user_id, provider_name)
        if not profile or not profile.access_token:
            return None

        provider = _PROVIDERS.get(provider_name)
        if not provider:
            return None

        _protocol = getattr(provider, "PROTOCOL", "openai")

        if not _is_expiring_soon(profile.expires_at):
            api_key, base_url = provider.get_api_credential(profile.access_token)
            return ResolvedCredential(
                api_key=api_key, base_url=base_url, source="oauth",
                provider=provider_name, protocol=_protocol,
            )

        # Token 即将过期 —— 获取锁后 double-check 再刷新
        lock = self._get_refresh_lock(user_id, provider_name)
        async with lock:
            # Double-check: 重新从 DB 读取，可能已被其他协程刷新
            profile = self._store.get_active_profile(user_id, provider_name)
            if not profile or not profile.access_token:
                return None
            if not _is_expiring_soon(profile.expires_at):
                api_key, base_url = provider.get_api_credential(profile.access_token)
                return ResolvedCredential(
                    api_key=api_key, base_url=base_url, source="oauth",
                    provider=provider_name, protocol=_protocol,
                )
            # 确实需要刷新
            refreshed = await self._refresh_profile(profile, provider)
            if not refreshed:
                return None
            profile = refreshed

        api_key, base_url = provider.get_api_credential(profile.access_token)
        return ResolvedCredential(
            api_key=api_key, base_url=base_url, source="oauth",
            provider=provider_name, protocol=_protocol,
        )

    async def _refresh_profile(
        self,
        profile: AuthProfileRecord,
        provider: Any,
    ) -> AuthProfileRecord | None:
        """刷新 OAuth token 并更新数据库（调用方需持有锁）。"""
        if not profile.refresh_token:
            logger.warning(
                "Provider %s 的 profile %s 无 refresh token，标记为不活跃",
                profile.provider, profile.id,
            )
            if self._store:
                self._store.deactivate_profile(profile.id)
            return None

        try:
            refreshed = await provider.refresh_token(profile.refresh_token)
        except RuntimeError as e:
            logger.warning(
                "Provider %s token 刷新失败: %s", profile.provider, e,
            )
            if self._store:
                self._store.deactivate_profile(profile.id)
            return None

        if self._store:
            self._store.update_tokens(
                profile.id,
                refreshed.access_token,
                refreshed.refresh_token,
                refreshed.expires_at,
            )

        return AuthProfileRecord(
            id=profile.id,
            user_id=profile.user_id,
            provider=profile.provider,
            profile_name=profile.profile_name,
            credential_type=profile.credential_type,
            access_token=refreshed.access_token,
            refresh_token=refreshed.refresh_token or profile.refresh_token,
            expires_at=refreshed.expires_at,
            account_id=profile.account_id,
            plan_type=profile.plan_type,
            extra_data=profile.extra_data,
            is_active=True,
            created_at=profile.created_at,
            updated_at=profile.updated_at,
        )

    def _try_pool_account(
        self, provider_name: str, model: str,
    ) -> ResolvedCredential | None:
        """尝试从号池获取凭证（人工激活映射）。"""
        if not self._pool_enabled:
            return None
        pool_svc = self._pool_service
        if pool_svc is None or self._store is None:
            return None
        try:
            account = pool_svc.resolve_active_account(provider_name, model)
            if account is None:
                return None
            from excelmanus.pool.service import POOL_USER_ID
            profile_name = pool_svc.get_pool_profile_name(account.id)
            # 按 profile_name 精确获取池账号凭证（多池账号安全）
            _get_by_name = getattr(self._store, "get_profile_by_name", None)
            if _get_by_name is not None:
                profile = _get_by_name(POOL_USER_ID, provider_name, profile_name)
            else:
                profile = self._store.get_active_profile(POOL_USER_ID, provider_name)
                if profile is not None and profile.profile_name != profile_name:
                    profile = None
            if profile is None or not profile.access_token:
                return None
            provider = _PROVIDERS.get(provider_name)
            if not provider:
                return None
            api_key, base_url = provider.get_api_credential(profile.access_token)
            _protocol = getattr(provider, "PROTOCOL", "openai")
            return ResolvedCredential(
                api_key=api_key,
                base_url=base_url,
                source="pool_oauth",
                provider=provider_name,
                protocol=_protocol,
                pool_account_id=account.id,
                pool_profile_name=profile_name,
            )
        except Exception:
            logger.debug("池账号凭证解析失败", exc_info=True)
            return None

    @staticmethod
    def _match_provider(model: str) -> str | None:
        """根据模型名推断 provider。"""
        for name, provider in _PROVIDERS.items():
            if provider.matches_model(model):
                return name
        return None
