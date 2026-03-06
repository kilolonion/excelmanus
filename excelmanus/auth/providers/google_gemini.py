"""Google Gemini (Google AI Pro/Ultra 订阅) 认证提供商。

复用 Gemini CLI 的 OAuth Desktop App 凭证，通过 Google OAuth2 PKCE 流程
或粘贴 ~/.gemini/oauth_creds.json 完成认证。认证后的 access_token 以
Bearer 方式调用 generativelanguage.googleapis.com API。
"""

from __future__ import annotations

import base64
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from excelmanus.auth.providers.base import (
    AuthProvider,
    PKCECapable,
    ProviderDescriptor,
    ProviderModelEntry,
    RefreshedCredential,
    ValidatedCredential,
)

logger = logging.getLogger(__name__)

_GEMINI_MODEL_PATTERN = re.compile(r"gemini-", re.IGNORECASE)


class GoogleGeminiProvider(AuthProvider, PKCECapable):
    """Google Gemini OAuth 提供商（基于 Gemini CLI Desktop App 凭证）。"""

    provider_name = "google-gemini"

    AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
    TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
    USERINFO_ENDPOINT = "https://www.googleapis.com/oauth2/v2/userinfo"

    # Gemini CLI 内嵌的 Desktop OAuth App 凭证（公开分发，非机密）
    CLIENT_ID = (
        "681255809395-oo8ft2oprdrnp9e3aqf6av3hmdib135j"
        ".apps.googleusercontent.com"
    )
    CLIENT_SECRET = "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"

    SCOPE = (
        "https://www.googleapis.com/auth/cloud-platform "
        "https://www.googleapis.com/auth/userinfo.email "
        "https://www.googleapis.com/auth/userinfo.profile"
    )

    BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
    PROTOCOL = "gemini"
    REFRESH_MARGIN_SECONDS = 300
    MODEL_NAME_PREFIX = "google-gemini/"

    _SUPPORTED_MODELS: tuple[tuple[str, str, bool], ...] = (
        ("gemini-2.5-pro", "Gemini 2.5 Pro", False),
        ("gemini-2.5-flash", "Gemini 2.5 Flash", False),
        ("gemini-2.5-flash-lite", "Gemini 2.5 Flash Lite", False),
        ("gemini-3-pro-preview", "Gemini 3 Pro Preview", False),
        ("gemini-3-flash-preview", "Gemini 3 Flash Preview", False),
        ("gemini-3.1-pro-preview", "Gemini 3.1 Pro Preview", True),
    )

    DEFAULT_MODEL = "google-gemini/gemini-2.5-pro"
    DEFAULT_PROFILE_NAME = "google-gemini/gemini-2.5-pro"

    def __init__(self, config_store: Any | None = None) -> None:
        self._config_store = config_store

    # ── ProviderDescriptor / on_connect_success ───────────────

    def get_descriptor(self) -> ProviderDescriptor:
        model_entries = tuple(
            ProviderModelEntry(
                model_id=mid,
                display_name=dname,
                public_id=f"{self.MODEL_NAME_PREFIX}{mid}",
                profile_name=f"{self.MODEL_NAME_PREFIX}{mid}",
                pro_only=pro,
            )
            for mid, dname, pro in self._SUPPORTED_MODELS
        )
        return ProviderDescriptor(
            id=self.provider_name,
            label="Google Gemini",
            protocol=self.PROTOCOL,
            base_url=self.BASE_URL,
            supported_flows=("token_paste", "pkce"),
            models=model_entries,
            default_model=self.DEFAULT_MODEL,
            thinking_mode="auto",
            model_family="gemini",
        )

    def _resolve_oauth_client_credentials(self) -> tuple[str, str]:
        def _from_config(key: str) -> str:
            if self._config_store is None:
                return ""
            getter = getattr(self._config_store, "get", None)
            if not callable(getter):
                return ""
            try:
                return (getter(key, "") or "").strip()
            except TypeError:
                return (getter(key) or "").strip()
            except Exception:
                logger.debug("读取 Gemini OAuth 配置失败: %s", key, exc_info=True)
                return ""

        client_id = (
            _from_config("gemini_oauth_client_id")
            or os.environ.get("EXCELMANUS_GEMINI_OAUTH_CLIENT_ID", "").strip()
            or self.CLIENT_ID
        )
        client_secret = (
            _from_config("gemini_oauth_client_secret")
            or os.environ.get("EXCELMANUS_GEMINI_OAUTH_CLIENT_SECRET", "").strip()
            or self.CLIENT_SECRET
        )
        return client_id, client_secret

    async def on_connect_success(
        self,
        request: Any,
        user_id: str,
        credential: ValidatedCredential,
    ) -> None:
        """连接成功后自动添加默认模型条目（如果尚未存在）。"""
        config_store = getattr(request.app.state, "config_store", None)
        if config_store is None:
            return
        try:
            existing = config_store.list_profiles()
            for p in existing:
                if p.get("name", "") == self.DEFAULT_PROFILE_NAME:
                    return
                if p.get("model", "") == self.DEFAULT_MODEL:
                    return
            config_store.add_profile(
                name=self.DEFAULT_PROFILE_NAME,
                model=self.DEFAULT_MODEL,
                api_key="",
                base_url=self.BASE_URL,
                description="Gemini 2.5 Pro - OAuth 登录（无需 API Key）",
                protocol="gemini",
                thinking_mode="auto",
                model_family="gemini",
            )
            from excelmanus.api import _sync_config_profiles_from_db
            try:
                _sync_config_profiles_from_db()
            except Exception:
                pass
            logger.info(
                "已自动添加 %s 默认模型: %s (%s)",
                self.provider_name, self.DEFAULT_PROFILE_NAME, self.DEFAULT_MODEL,
            )
        except Exception:
            logger.debug("自动添加 %s 默认模型失败", self.provider_name, exc_info=True)

    # ── PKCECapable ───────────────────────────────────────────

    def generate_pkce(self) -> tuple[str, str]:
        import hashlib
        import os
        verifier_bytes = os.urandom(32)
        code_verifier = base64.urlsafe_b64encode(verifier_bytes).rstrip(b"=").decode()
        digest = hashlib.sha256(code_verifier.encode()).digest()
        code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
        return code_verifier, code_challenge

    def build_authorize_url(
        self, redirect_uri: str, state: str, code_challenge: str,
    ) -> str:
        from urllib.parse import urlencode
        client_id, _ = self._resolve_oauth_client_credentials()
        params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": self.SCOPE,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "state": state,
            "access_type": "offline",
            "prompt": "consent",
        }
        return f"{self.AUTH_ENDPOINT}?{urlencode(params)}"

    async def exchange_code(
        self, code: str, redirect_uri: str, code_verifier: str,
    ) -> ValidatedCredential:
        client_id, client_secret = self._resolve_oauth_client_credentials()
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                resp = await client.post(
                    self.TOKEN_ENDPOINT,
                    data={
                        "grant_type": "authorization_code",
                        "code": code,
                        "redirect_uri": redirect_uri,
                        "client_id": client_id,
                        "client_secret": client_secret,
                        "code_verifier": code_verifier,
                    },
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
            except httpx.HTTPError as e:
                raise RuntimeError(f"Token 交换网络错误: {e}") from e

            if resp.status_code != 200:
                body = resp.text[:500]
                logger.warning("Gemini token 交换失败: %d %s", resp.status_code, body)
                raise RuntimeError(f"Token 交换失败 (HTTP {resp.status_code})")

            data = resp.json()

        access_token = data.get("access_token", "")
        refresh_token = data.get("refresh_token")

        logger.info(
            "Gemini token exchange: token_type=%r, has_refresh=%s, scope=%r",
            data.get("token_type", ""), bool(refresh_token), data.get("scope", ""),
        )

        if not access_token:
            raise RuntimeError("Token 交换响应中缺少 access_token")

        expires_at = self._parse_google_token_expiry(data)
        account_id = await self._fetch_account_id(access_token)

        return ValidatedCredential(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
            account_id=account_id,
            plan_type="",
            credential_type="oauth",
        )

    # ── Token paste ───────────────────────────────────────────

    def validate_token_data(self, raw_data: dict[str, Any]) -> ValidatedCredential:
        """验证粘贴的 token 数据。

        支持三种格式:
        1. Gemini CLI oauth_creds.json: {access_token, refresh_token, expiry_date, token_type}
        2. 简化格式: {access_token, refresh_token}
        3. google-auth-library authorized_user: {client_id, client_secret, refresh_token, type}
        """
        access_token = raw_data.get("access_token") or ""
        refresh_token = raw_data.get("refresh_token") or ""

        # google-auth-library 的 authorized_user 格式只含 refresh_token
        if (
            not access_token
            and refresh_token
            and raw_data.get("type") == "authorized_user"
        ):
            pass
        elif not access_token:
            raise ValueError(
                "缺少 access_token。请粘贴 ~/.gemini/oauth_creds.json "
                "的完整内容，或包含 access_token 字段的 JSON。"
            )

        if not refresh_token:
            raise ValueError(
                "缺少 refresh_token。请确保粘贴的是完整的 oauth_creds.json 内容。"
            )

        if access_token:
            expires_at = self._parse_expires(raw_data)
        else:
            expires_at = (
                datetime.now(tz=timezone.utc) - timedelta(seconds=1)
            ).isoformat()

        return ValidatedCredential(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
            account_id="",
            plan_type="",
            credential_type="oauth",
        )

    # ── Refresh ───────────────────────────────────────────────

    async def refresh_token(self, refresh_token: str) -> RefreshedCredential:
        if not refresh_token:
            raise RuntimeError(
                "无 refresh token，无法刷新。"
                "请重新运行 gemini CLI 登录或重新粘贴凭证。"
            )

        client_id, client_secret = self._resolve_oauth_client_credentials()
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                resp = await client.post(
                    self.TOKEN_ENDPOINT,
                    data={
                        "grant_type": "refresh_token",
                        "refresh_token": refresh_token,
                        "client_id": client_id,
                        "client_secret": client_secret,
                    },
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
            except httpx.HTTPError as e:
                raise RuntimeError(f"刷新请求网络错误: {e}") from e

            if resp.status_code != 200:
                body = resp.text[:500]
                logger.warning("Gemini token 刷新失败: %d %s", resp.status_code, body)
                raise RuntimeError(
                    f"Token 刷新失败 (HTTP {resp.status_code})。"
                    "请重新运行 gemini CLI 登录获取新令牌。"
                )

            data = resp.json()

        new_access = data.get("access_token", "")
        new_refresh = data.get("refresh_token") or refresh_token
        if not new_access:
            raise RuntimeError("刷新响应中缺少 access_token")

        expires_at = self._parse_google_token_expiry(data)

        return RefreshedCredential(
            access_token=new_access,
            refresh_token=new_refresh,
            expires_at=expires_at,
        )

    # ── Credential mapping ────────────────────────────────────

    def get_api_credential(self, access_token: str) -> tuple[str, str]:
        """返回 (api_key, base_url)。

        GeminiClient 会检测 ya29. 前缀并自动使用 Bearer auth。
        """
        return (access_token, self.BASE_URL)

    def matches_model(self, model: str) -> bool:
        return bool(_GEMINI_MODEL_PATTERN.search(model))

    # ── Helpers ───────────────────────────────────────────────

    @staticmethod
    async def _fetch_account_id(access_token: str) -> str:
        """通过 Google UserInfo 端点获取用户邮箱作为 account_id。"""
        if not access_token:
            return ""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    "https://www.googleapis.com/oauth2/v2/userinfo",
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("email", data.get("id", ""))
        except Exception:
            logger.debug("获取 Google 用户信息失败", exc_info=True)
        return ""

    @staticmethod
    def _parse_google_token_expiry(data: dict[str, Any]) -> str:
        """从 Google token 响应解析过期时间。"""
        expires_in = data.get("expires_in")
        if isinstance(expires_in, (int, float)) and expires_in > 0:
            return (
                datetime.now(tz=timezone.utc) + timedelta(seconds=int(expires_in))
            ).isoformat()
        return (datetime.now(tz=timezone.utc) + timedelta(hours=1)).isoformat()

    @staticmethod
    def _parse_expires(raw_data: dict[str, Any]) -> str:
        """从粘贴数据解析过期时间。"""
        # Gemini CLI 格式: expiry_date (ISO string like "2025-01-01T00:00:00.000Z")
        if ed := raw_data.get("expiry_date"):
            if isinstance(ed, str):
                return ed
            if isinstance(ed, (int, float)):
                ts = ed / 1000 if ed > 1e12 else ed
                return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()

        # expires_at (ISO string)
        if ea := raw_data.get("expires_at"):
            if isinstance(ea, str):
                return ea
            if isinstance(ea, (int, float)):
                return datetime.fromtimestamp(ea, tz=timezone.utc).isoformat()

        # expires_in (seconds from now)
        if ei := raw_data.get("expires_in"):
            if isinstance(ei, (int, float)) and ei > 0:
                return (
                    datetime.now(tz=timezone.utc) + timedelta(seconds=int(ei))
                ).isoformat()

        return (datetime.now(tz=timezone.utc) + timedelta(hours=1)).isoformat()

    @classmethod
    def list_supported_models(cls) -> tuple[str, ...]:
        return tuple(mid for mid, _, _ in cls._SUPPORTED_MODELS)

    @classmethod
    def profile_name_for_model(cls, model_id: str) -> str:
        return f"{cls.MODEL_NAME_PREFIX}{model_id}"

    @classmethod
    def is_gemini_profile_name(cls, profile_name: str) -> bool:
        return profile_name.startswith(cls.MODEL_NAME_PREFIX)
