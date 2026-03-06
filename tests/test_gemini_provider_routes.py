from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_request(*, body: dict | None = None, credential_store: MagicMock | None = None):
    store = credential_store or MagicMock()
    app = SimpleNamespace(state=SimpleNamespace(credential_store=store))
    return SimpleNamespace(
        app=app,
        json=AsyncMock(return_value=body or {}),
    )


class TestGoogleGeminiProviderRoutes:
    def test_gemini_provider_prefers_config_store_over_env(self, monkeypatch: pytest.MonkeyPatch):
        from excelmanus.auth.providers.google_gemini import GoogleGeminiProvider

        monkeypatch.setenv("EXCELMANUS_GEMINI_OAUTH_CLIENT_ID", "env-client-id")
        monkeypatch.setenv("EXCELMANUS_GEMINI_OAUTH_CLIENT_SECRET", "env-client-secret")

        config_store = MagicMock()
        config_store.get.side_effect = lambda key, default="": {
            "gemini_oauth_client_id": "config-client-id",
            "gemini_oauth_client_secret": "config-client-secret",
        }.get(key, default)

        provider = GoogleGeminiProvider(config_store=config_store)
        authorize_url = provider.build_authorize_url(
            redirect_uri="http://localhost:1455/auth/gemini/callback",
            state="state-config",
            code_challenge="challenge-config",
        )

        assert "config-client-id" in authorize_url

    def test_gemini_provider_prefers_env_oauth_client_credentials(self, monkeypatch: pytest.MonkeyPatch):
        from excelmanus.auth.providers.google_gemini import GoogleGeminiProvider

        monkeypatch.setenv("EXCELMANUS_GEMINI_OAUTH_CLIENT_ID", "env-client-id")
        monkeypatch.setenv("EXCELMANUS_GEMINI_OAUTH_CLIENT_SECRET", "env-client-secret")

        provider = GoogleGeminiProvider()
        authorize_url = provider.build_authorize_url(
            redirect_uri="http://localhost:1455/auth/gemini/callback",
            state="state-1",
            code_challenge="challenge-1",
        )

        assert "env-client-id" in authorize_url

    def test_gemini_provider_falls_back_to_bundled_client_credentials(self, monkeypatch: pytest.MonkeyPatch):
        from excelmanus.auth.providers.google_gemini import GoogleGeminiProvider

        monkeypatch.delenv("EXCELMANUS_GEMINI_OAUTH_CLIENT_ID", raising=False)
        monkeypatch.delenv("EXCELMANUS_GEMINI_OAUTH_CLIENT_SECRET", raising=False)

        provider = GoogleGeminiProvider()
        authorize_url = provider.build_authorize_url(
            redirect_uri="http://localhost:1455/auth/gemini/callback",
            state="state-2",
            code_challenge="challenge-2",
        )

        assert GoogleGeminiProvider.CLIENT_ID in authorize_url

    def test_registry_lists_google_gemini_descriptor(self):
        from excelmanus.auth.providers.registry import list_descriptors

        descriptors = list_descriptors()
        gemini = next((item for item in descriptors if item.id == "google-gemini"), None)

        assert gemini is not None
        assert gemini.protocol == "gemini"
        assert "pkce" in gemini.supported_flows
        assert "token_paste" in gemini.supported_flows
        assert any(model.public_id == "google-gemini/gemini-2.5-pro" for model in gemini.models)

    @pytest.mark.asyncio
    async def test_generic_oauth_start_uses_gemini_callback_path(self):
        from excelmanus.auth.router import generic_provider_oauth_start

        cred_store = MagicMock()
        request = _make_request(
            body={"redirect_uri": "http://localhost:1455/auth/gemini/callback"},
            credential_store=cred_store,
        )
        user = SimpleNamespace(id="user-1")

        result = await generic_provider_oauth_start("google-gemini", request, user)

        assert result["mode"] == "popup"
        assert result["redirect_uri"] == "http://localhost:1455/auth/gemini/callback"
        assert "accounts.google.com" in result["authorize_url"]
        cred_store.save_oauth_state.assert_called_once()
        saved_state, saved_payload = cred_store.save_oauth_state.call_args.args[:2]
        assert isinstance(saved_state, str) and saved_state
        assert saved_payload["provider"] == "google-gemini"
        assert saved_payload["user_id"] == "user-1"
        assert saved_payload["redirect_uri"] == "http://localhost:1455/auth/gemini/callback"

    @pytest.mark.asyncio
    async def test_generic_oauth_start_uses_request_config_store_for_client_credentials(self):
        from excelmanus.auth.router import generic_provider_oauth_start

        cred_store = MagicMock()
        config_store = MagicMock()
        config_store.get.side_effect = lambda key, default="": {
            "gemini_oauth_client_id": "config-client-id",
            "gemini_oauth_client_secret": "config-client-secret",
        }.get(key, default)
        app = SimpleNamespace(state=SimpleNamespace(credential_store=cred_store, config_store=config_store))
        request = SimpleNamespace(
            app=app,
            json=AsyncMock(return_value={"redirect_uri": "http://localhost:1455/auth/gemini/callback"}),
        )
        user = SimpleNamespace(id="user-1")

        result = await generic_provider_oauth_start("google-gemini", request, user)

        assert "config-client-id" in result["authorize_url"]

    @pytest.mark.asyncio
    async def test_generic_provider_status_reports_refresh_capability(self):
        from excelmanus.auth.router import generic_provider_status

        future = (datetime.now(tz=timezone.utc) + timedelta(hours=1)).isoformat()
        profile = SimpleNamespace(
            account_id="user@example.com",
            plan_type="",
            expires_at=future,
            is_active=True,
            refresh_token="refresh-token",
        )
        cred_store = MagicMock()
        cred_store.get_active_profile.return_value = profile
        request = _make_request(credential_store=cred_store)
        user = SimpleNamespace(id="user-1")

        result = await generic_provider_status("google-gemini", request, user)

        assert result["status"] == "connected"
        assert result["provider"] == "google-gemini"
        assert result["account_id"] == "user@example.com"
        assert result["has_refresh_token"] is True


class TestGoogleGeminiProviderLifecycle:
    @pytest.mark.asyncio
    async def test_exchange_code_uses_resolved_oauth_client_credentials(self, monkeypatch: pytest.MonkeyPatch):
        import httpx
        from excelmanus.auth.providers.google_gemini import GoogleGeminiProvider

        monkeypatch.setenv("EXCELMANUS_GEMINI_OAUTH_CLIENT_ID", "env-client-id")
        monkeypatch.setenv("EXCELMANUS_GEMINI_OAUTH_CLIENT_SECRET", "env-client-secret")

        token_response = httpx.Response(
            200,
            json={"access_token": "access-token", "refresh_token": "refresh-token", "expires_in": 3600},
            request=httpx.Request("POST", GoogleGeminiProvider.TOKEN_ENDPOINT),
        )

        post_mock = AsyncMock(return_value=token_response)
        with patch("httpx.AsyncClient.post", post_mock), patch.object(
            GoogleGeminiProvider,
            "_fetch_account_id",
            AsyncMock(return_value="user@example.com"),
        ):
            provider = GoogleGeminiProvider()
            credential = await provider.exchange_code(
                code="auth-code",
                redirect_uri="http://localhost:1455/auth/gemini/callback",
                code_verifier="verifier-1",
            )

        assert credential.account_id == "user@example.com"
        assert credential.refresh_token == "refresh-token"
        assert post_mock.await_args.kwargs["data"]["client_id"] == "env-client-id"
        assert post_mock.await_args.kwargs["data"]["client_secret"] == "env-client-secret"

    @pytest.mark.asyncio
    async def test_refresh_token_uses_resolved_oauth_client_credentials(self, monkeypatch: pytest.MonkeyPatch):
        import httpx
        from excelmanus.auth.providers.google_gemini import GoogleGeminiProvider

        monkeypatch.setenv("EXCELMANUS_GEMINI_OAUTH_CLIENT_ID", "env-client-id")
        monkeypatch.setenv("EXCELMANUS_GEMINI_OAUTH_CLIENT_SECRET", "env-client-secret")

        refresh_response = httpx.Response(
            200,
            json={"access_token": "new-access-token", "expires_in": 1800},
            request=httpx.Request("POST", GoogleGeminiProvider.TOKEN_ENDPOINT),
        )

        post_mock = AsyncMock(return_value=refresh_response)
        with patch("httpx.AsyncClient.post", post_mock):
            provider = GoogleGeminiProvider()
            refreshed = await provider.refresh_token("refresh-token")

        assert refreshed.access_token == "new-access-token"
        assert post_mock.await_args.kwargs["data"]["client_id"] == "env-client-id"
        assert post_mock.await_args.kwargs["data"]["client_secret"] == "env-client-secret"

    @pytest.mark.asyncio
    async def test_on_connect_success_adds_default_gemini_profile(self):
        from excelmanus.auth.providers.base import ValidatedCredential
        from excelmanus.auth.providers.google_gemini import GoogleGeminiProvider

        config_store = MagicMock()
        config_store.list_profiles.return_value = []
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(config_store=config_store)))
        credential = ValidatedCredential(
            access_token="access-token",
            refresh_token="refresh-token",
            expires_at=(datetime.now(tz=timezone.utc) + timedelta(hours=1)).isoformat(),
            account_id="user@example.com",
            plan_type="",
        )

        provider = GoogleGeminiProvider()
        with patch("excelmanus.api._sync_config_profiles_from_db", MagicMock()):
            await provider.on_connect_success(request, "user-1", credential)

        config_store.add_profile.assert_called_once()
        assert config_store.add_profile.call_args.kwargs["name"] == provider.DEFAULT_PROFILE_NAME
        assert config_store.add_profile.call_args.kwargs["model"] == provider.DEFAULT_MODEL

    @pytest.mark.asyncio
    async def test_on_connect_success_skips_existing_default_profile(self):
        from excelmanus.auth.providers.base import ValidatedCredential
        from excelmanus.auth.providers.google_gemini import GoogleGeminiProvider

        config_store = MagicMock()
        config_store.list_profiles.return_value = [{"name": GoogleGeminiProvider.DEFAULT_PROFILE_NAME}]
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(config_store=config_store)))
        credential = ValidatedCredential(
            access_token="access-token",
            refresh_token="refresh-token",
            expires_at=(datetime.now(tz=timezone.utc) + timedelta(hours=1)).isoformat(),
            account_id="user@example.com",
            plan_type="",
        )

        provider = GoogleGeminiProvider()
        await provider.on_connect_success(request, "user-1", credential)

        config_store.add_profile.assert_not_called()


class TestGeminiOAuthAdminConfig:
    def test_get_login_config_includes_gemini_oauth_fields(self, monkeypatch: pytest.MonkeyPatch):
        from excelmanus.auth.router import get_login_config

        monkeypatch.setenv("EXCELMANUS_GEMINI_OAUTH_CLIENT_ID", "env-gemini-client-id")
        monkeypatch.setenv("EXCELMANUS_GEMINI_OAUTH_CLIENT_SECRET", "env-gemini-client-secret")
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(config_store=None)))

        config = get_login_config(request)

        assert config["gemini_oauth_client_id"] == "env-gemini-client-id"
        assert config["gemini_oauth_client_secret"].endswith("cret")

    @pytest.mark.asyncio
    async def test_admin_update_login_config_persists_gemini_oauth_fields(self):
        from excelmanus.auth.router import admin_update_login_config

        values: dict[str, str] = {}
        config_store = MagicMock()
        config_store.get.side_effect = lambda key, default="": values.get(key, default)
        config_store.set.side_effect = lambda key, value: values.__setitem__(key, value)
        request = SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(config_store=config_store)),
            json=AsyncMock(return_value={
                "gemini_oauth_client_id": "config-gemini-client-id",
                "gemini_oauth_client_secret": "config-gemini-client-secret",
            }),
        )

        result = await admin_update_login_config(request, SimpleNamespace(id="admin-1", email="admin@example.com"))

        config_store.set.assert_any_call("gemini_oauth_client_id", "config-gemini-client-id")
        config_store.set.assert_any_call("gemini_oauth_client_secret", "config-gemini-client-secret")
        assert result["gemini_oauth_client_id"] == "config-gemini-client-id"
        assert result["gemini_oauth_client_secret"].endswith("cret")
