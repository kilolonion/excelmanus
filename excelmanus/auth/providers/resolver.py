"""运行时凭证解析器 —— LLM 调用前确定使用哪个凭证。"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from excelmanus.auth.providers.base import AuthProfileRecord, ResolvedCredential
from excelmanus.auth.providers.credential_store import PROCESS_USER_ID
from excelmanus.auth.providers.openai_codex import OpenAICodexProvider

if TYPE_CHECKING:
    from excelmanus.auth.providers.credential_store import CredentialStore
    from excelmanus.config import ExcelManusConfig

logger = logging.getLogger(__name__)

_PROVIDERS = {
    "openai-codex": OpenAICodexProvider(),
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
    1. 号池人工激活映射
    2. auth_profiles 中匹配 provider 的进程级 OAuth token（自动刷新）
    3. 调用方默认凭证（返回 None）
    """

    def __init__(
        self,
        credential_store: "CredentialStore | None" = None,
        config: "ExcelManusConfig | None" = None,
    ) -> None:
        self._store = credential_store
        self._config = config
        self._refresh_locks: dict[str, asyncio.Lock] = {}
        self._pool_service: Any = None
        self._pool_enabled: bool = False

    def _get_refresh_lock(self, provider_name: str) -> asyncio.Lock:
        lock = self._refresh_locks.get(provider_name)
        if lock is None:
            lock = asyncio.Lock()
            self._refresh_locks[provider_name] = lock
        return lock

    async def resolve(self, model: str) -> ResolvedCredential | None:
        """解析进程级凭证。返回 None 表示使用调用方的默认凭证。"""
        provider_name = self._match_provider(model)
        if provider_name:
            pool_resolved = self._try_pool_account(provider_name, model)
            if pool_resolved:
                return pool_resolved
        if provider_name and self._store:
            resolved = await self._try_oauth_profile(provider_name)
            if resolved:
                return resolved
        return None

    def resolve_sync(self, model: str) -> ResolvedCredential | None:
        """同步版本 resolve —— 用于引擎创建时（不触发自动刷新）。"""
        if not self._store:
            return None
        provider_name = self._match_provider(model)
        if not provider_name:
            return None

        pool_resolved = self._try_pool_account(provider_name, model)
        if pool_resolved:
            return pool_resolved

        profile = self._store.get_active_profile(provider_name)
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

    async def _try_oauth_profile(self, provider_name: str) -> ResolvedCredential | None:
        if not self._store:
            return None

        profile = self._store.get_active_profile(provider_name)
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

        lock = self._get_refresh_lock(provider_name)
        async with lock:
            profile = self._store.get_active_profile(provider_name)
            if not profile or not profile.access_token:
                return None
            if not _is_expiring_soon(profile.expires_at):
                api_key, base_url = provider.get_api_credential(profile.access_token)
                return ResolvedCredential(
                    api_key=api_key, base_url=base_url, source="oauth",
                    provider=provider_name, protocol=_protocol,
                )
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
            logger.warning("Provider %s token 刷新失败: %s", profile.provider, e)
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
            _get_by_name = getattr(self._store, "get_profile_by_name", None)
            if _get_by_name is not None:
                profile = _get_by_name(POOL_USER_ID, provider_name, profile_name)
            else:
                profile = self._store.get_active_profile(provider_name, user_id=POOL_USER_ID)
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
        for name, provider in _PROVIDERS.items():
            if provider.matches_model(model):
                return name
        return None
