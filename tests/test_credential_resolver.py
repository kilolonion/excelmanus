"""CredentialResolver 单元测试 —— 并发刷新保护、同步解析、集成验证。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from excelmanus.auth.providers.base import AuthProfileRecord, RefreshedCredential, ResolvedCredential
from excelmanus.auth.providers.resolver import CredentialResolver, _is_expiring_soon


# ── 测试辅助 ──────────────────────────────────────────────


def _make_profile(
    *,
    expired: bool = True,
    access: str = "old_token",
    refresh: str = "rt_xxx",
    provider: str = "openai-codex",
    user_id: str = "u1",
) -> AuthProfileRecord:
    if expired:
        exp = (datetime.now(tz=timezone.utc) - timedelta(hours=1)).isoformat()
    else:
        exp = (datetime.now(tz=timezone.utc) + timedelta(hours=1)).isoformat()
    return AuthProfileRecord(
        id="prof-1",
        user_id=user_id,
        provider=provider,
        profile_name="default",
        credential_type="oauth",
        access_token=access,
        refresh_token=refresh,
        expires_at=exp,
        account_id="acc-123",
        plan_type="plus",
        extra_data=None,
        is_active=True,
        created_at="2025-01-01T00:00:00+00:00",
        updated_at="2025-01-01T00:00:00+00:00",
    )


def _make_provider_mock(*, new_access: str = "new_token") -> MagicMock:
    """创建 mock provider，模拟 refresh + get_api_credential。"""
    fresh_exp = (datetime.now(tz=timezone.utc) + timedelta(hours=1)).isoformat()
    provider = MagicMock()
    provider.MODEL_NAME_PREFIX = ""
    provider.refresh_profile = AsyncMock(
        return_value=RefreshedCredential(
            access_token=new_access,
            refresh_token="rt_new",
            expires_at=fresh_exp,
        )
    )
    provider.get_api_credential = MagicMock(
        return_value=(new_access, "https://api.openai.com/v1")
    )
    provider.get_request_headers = MagicMock(return_value={})
    provider.matches_model = MagicMock(return_value=True)
    return provider


# ── _is_expiring_soon ─────────────────────────────────────


def test_is_expiring_soon_none():
    assert _is_expiring_soon(None) is True


def test_is_expiring_soon_expired():
    past = (datetime.now(tz=timezone.utc) - timedelta(hours=1)).isoformat()
    assert _is_expiring_soon(past) is True


def test_is_expiring_soon_fresh():
    future = (datetime.now(tz=timezone.utc) + timedelta(hours=2)).isoformat()
    assert _is_expiring_soon(future) is False


def test_is_expiring_soon_within_margin():
    near = (datetime.now(tz=timezone.utc) + timedelta(seconds=60)).isoformat()
    assert _is_expiring_soon(near, margin_seconds=300) is True


# ── resolve 基本逻辑 ─────────────────────────────────────


@pytest.mark.asyncio
async def test_resolve_returns_none_for_no_user():
    resolver = CredentialResolver()
    result = await resolver.resolve("gpt-5.3-codex")
    assert result is None


@pytest.mark.asyncio
async def test_resolve_returns_none_for_unknown_model():
    store = MagicMock()
    resolver = CredentialResolver(credential_store=store)
    result = await resolver.resolve("unknown-model-xyz")
    assert result is None
    store.get_active_profile.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_returns_credential_for_fresh_token():
    """未过期 token 直接返回，不触发 refresh。"""
    store = MagicMock()
    fresh_profile = _make_profile(expired=False, access="fresh_token")
    store.get_active_profile = MagicMock(return_value=fresh_profile)

    provider_mock = _make_provider_mock()
    # 覆盖 get_api_credential 以返回 profile 中的实际 token
    provider_mock.get_api_credential = MagicMock(
        return_value=("fresh_token", "https://api.openai.com/v1")
    )

    with patch("excelmanus.auth.providers.resolver._PROVIDERS", {"openai-codex": provider_mock}):
        resolver = CredentialResolver(credential_store=store)
        result = await resolver.resolve("gpt-5.3-codex")

    assert result is not None
    assert result.api_key == "fresh_token"
    assert result.source == "oauth"
    provider_mock.refresh_profile.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_refreshes_expired_token():
    """过期 token 触发自动刷新。"""
    store = MagicMock()
    expired_profile = _make_profile(expired=True, access="old_token")
    store.get_active_profile = MagicMock(return_value=expired_profile)
    store.update_tokens = MagicMock()

    provider_mock = _make_provider_mock(new_access="refreshed_token")

    with patch("excelmanus.auth.providers.resolver._PROVIDERS", {"openai-codex": provider_mock}):
        resolver = CredentialResolver(credential_store=store)
        result = await resolver.resolve("gpt-5.3-codex")

    assert result is not None
    assert result.api_key == "refreshed_token"
    provider_mock.refresh_profile.assert_called_once_with(expired_profile)
    store.update_tokens.assert_called_once()


@pytest.mark.asyncio
async def test_resolve_deactivates_on_refresh_failure():
    """刷新失败时 deactivate profile。"""
    store = MagicMock()
    expired_profile = _make_profile(expired=True)
    store.get_active_profile = MagicMock(return_value=expired_profile)
    store.deactivate_profile = MagicMock()

    provider_mock = MagicMock()
    provider_mock.MODEL_NAME_PREFIX = ""
    provider_mock.matches_model = MagicMock(return_value=True)
    provider_mock.refresh_profile = AsyncMock(side_effect=RuntimeError("refresh failed"))

    with patch("excelmanus.auth.providers.resolver._PROVIDERS", {"openai-codex": provider_mock}):
        resolver = CredentialResolver(credential_store=store)
        result = await resolver.resolve("gpt-5.3-codex")

    assert result is None
    store.deactivate_profile.assert_called_once_with("prof-1")


# ── 并发刷新保护 ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_concurrent_refresh_serialized_by_lock():
    """并发 resolve 调用被锁序列化，refresh 最多调用一次（double-check）。"""
    store = MagicMock()
    expired_profile = _make_profile(expired=True, access="old_token")
    fresh_profile = _make_profile(expired=False, access="new_token")
    call_count = 0

    def get_active_side_effect(provider, user_id=None):
        nonlocal call_count
        call_count += 1
        # 第一次返回过期（触发 refresh），之后返回新鲜（double-check 跳过）
        if call_count <= 1:
            return expired_profile
        return fresh_profile

    store.get_active_profile = MagicMock(side_effect=get_active_side_effect)
    store.update_tokens = MagicMock()

    provider_mock = _make_provider_mock(new_access="new_token")

    with patch("excelmanus.auth.providers.resolver._PROVIDERS", {"openai-codex": provider_mock}):
        resolver = CredentialResolver(credential_store=store)
        results = await asyncio.gather(
            resolver.resolve("gpt-5.3-codex"),
            resolver.resolve("gpt-5.3-codex"),
        )

    # 两个结果都应该成功
    assert all(r is not None for r in results)
    assert all(r.api_key == "new_token" for r in results)
    # refresh 最多被调用一次
    assert provider_mock.refresh_profile.call_count <= 1


@pytest.mark.asyncio
async def test_process_level_refresh_is_shared():
    """进程级凭证共享一把锁，并发 refresh 不会按用户分叉。"""
    store = MagicMock()
    expired = _make_profile(expired=True)
    fresh = _make_profile(expired=False, access="new_token")
    call_count = 0

    def get_active_side_effect(provider, user_id=None):
        nonlocal call_count
        call_count += 1
        if call_count <= 1:
            return expired
        return fresh

    store.get_active_profile = MagicMock(side_effect=get_active_side_effect)
    store.update_tokens = MagicMock()

    provider_mock = _make_provider_mock()

    with patch("excelmanus.auth.providers.resolver._PROVIDERS", {"openai-codex": provider_mock}):
        resolver = CredentialResolver(credential_store=store)
        results = await asyncio.gather(
            resolver.resolve("gpt-5.3-codex"),
            resolver.resolve("gpt-5.3-codex"),
        )

    assert all(r is not None for r in results)
    assert provider_mock.refresh_profile.call_count <= 1
    assert list(resolver._refresh_locks.keys()) == ["openai-codex"]


# ── resolve_sync ─────────────────────────────────────────


def test_resolve_sync_returns_credential_for_fresh_token():
    """同步版本直接返回凭证，不触发刷新。"""
    store = MagicMock()
    fresh_profile = _make_profile(expired=False, access="sync_token")
    store.get_active_profile = MagicMock(return_value=fresh_profile)

    provider_mock = _make_provider_mock()
    provider_mock.get_api_credential = MagicMock(
        return_value=("sync_token", "https://api.openai.com/v1")
    )

    with patch("excelmanus.auth.providers.resolver._PROVIDERS", {"openai-codex": provider_mock}):
        resolver = CredentialResolver(credential_store=store)
        result = resolver.resolve_sync("gpt-5.3-codex")

    assert result is not None
    assert result.api_key == "sync_token"
    assert result.source == "oauth"
    provider_mock.refresh_token.assert_not_called()


def test_resolve_sync_returns_none_for_no_store():
    resolver = CredentialResolver()
    result = resolver.resolve_sync("gpt-5.3-codex")
    assert result is None


def test_resolve_sync_returns_none_for_no_profile():
    store = MagicMock()
    store.get_active_profile = MagicMock(return_value=None)
    provider_mock = _make_provider_mock()
    with patch("excelmanus.auth.providers.resolver._PROVIDERS", {"openai-codex": provider_mock}):
        resolver = CredentialResolver(credential_store=store)
        result = resolver.resolve_sync("gpt-5.3-codex")
    assert result is None
