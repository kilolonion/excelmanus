"""Google Antigravity（Cloud Code Assist 订阅）认证提供商。


- 标准 Google OAuth2 授权码 + 本机回环回调（http://localhost:<port>/oauth-callback），
  Google 对 loopback redirect 不校验端口。
- 对话走 Cloud Code 内部 REST 网关（JSON，非 gRPC）：
  ``POST {base}/v1internal:streamGenerateContent?alt=sse``，请求体为
  ``{project, model, requestType:"agent", userAgent:"antigravity", requestId, request:{...}}``
  信封包裹 Gemini generateContent 载荷（由 providers/antigravity.py 负责）。
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import httpx

from excelmanus.auth.providers.base import (
    AuthProvider,
    LoopbackOAuthCapable,
    RefreshedCredential,
    ValidatedCredential,
)

logger = logging.getLogger(__name__)

# ── Google OAuth2 客户端（Antigravity IDE 公开客户端，与 CPA 一致） ──
_CLIENT_ID = (
    ""
    ".apps.googleusercontent.com"
)
_CLIENT_SECRET = ""
_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
_USERINFO_ENDPOINT = "https://www.googleapis.com/oauth2/v2/userinfo?alt=json"
_SCOPES = (
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/cclog",
    "https://www.googleapis.com/auth/experimentsandconfigs",
)

# ── Cloud Code 内部 REST 网关 ──
# 对话请求走 daily 端点（与原生客户端一致）；loadCodeAssist 走 prod；onboardUser 走 daily。
_API_BASE_DAILY = "https://daily-cloudcode-pa.googleapis.com"
_API_BASE_PROD = "https://cloudcode-pa.googleapis.com"
_API_VERSION = "v1internal"

# 与原生客户端一致的 UA 指纹（版本需 ≥2.9.0，否则上游拒绝新模型）。
# 常量定义在客户端模块，此处单源引用。
from excelmanus.providers.antigravity import (  # noqa: E402
    PROJECT_ID_HEADER,
    _REQUEST_UA as _REQUEST_USER_AGENT,
)
_ONBOARD_USER_AGENT = _REQUEST_USER_AGENT + " google-api-nodejs-client/10.3.0"
_GOOG_API_CLIENT_UA = "gl-node/22.21.1"

_ONBOARD_MAX_ATTEMPTS = 5
_ONBOARD_POLL_SECONDS = 2.0


def _http_client() -> httpx.AsyncClient:
    import os

    proxy = ""
    for key in (
        "EXCELMANUS_OAUTH_PROXY", "HTTPS_PROXY", "https_proxy",
        "ALL_PROXY", "all_proxy",
    ):
        value = os.environ.get(key, "").strip()
        if value:
            proxy = value
            break
    return httpx.AsyncClient(timeout=30.0, proxy=proxy or None)


def _extract_project_id(data: dict[str, Any] | None) -> str:
    """从 loadCodeAssist/onboardUser 响应提取 GCP project id。"""
    if not isinstance(data, dict):
        return ""
    for key in ("cloudaicompanionProject", "projectId", "project"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            inner = value.get("id")
            if isinstance(inner, str) and inner.strip():
                return inner.strip()
    return ""


def _default_tier_id(load_resp: dict[str, Any]) -> str:
    tiers = load_resp.get("allowedTiers")
    if isinstance(tiers, list):
        for tier in tiers:
            if not isinstance(tier, dict):
                continue
            if tier.get("isDefault") is True:
                tid = str(tier.get("id") or "").strip()
                if tid:
                    return tid
    current = load_resp.get("currentTier")
    if isinstance(current, dict):
        tid = str(current.get("id") or "").strip()
        if tid:
            return tid
    return "free-tier"


def _expires_at_from(expires_in: Any, *, margin_seconds: int = 300) -> str:
    try:
        seconds = int(expires_in)
    except (TypeError, ValueError):
        seconds = 3600
    seconds = max(seconds - margin_seconds, 60)
    return (
        datetime.now(tz=timezone.utc) + timedelta(seconds=seconds)
    ).isoformat()


def _parse_expires_at(raw: Any) -> str:
    """解析粘贴凭证里的过期时间（``expired`` RFC3339 或 expires_in）。"""
    if isinstance(raw, str) and raw.strip():
        text = raw.strip()
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.isoformat()
        except ValueError:
            pass
    return _expires_at_from(raw)


class AntigravityProvider(AuthProvider, LoopbackOAuthCapable):
    """Google Antigravity 订阅（Gemini/Claude/GPT-OSS 统一网关）。"""

    provider_name = "antigravity"
    PROTOCOL = "antigravity"
    MODEL_NAME_PREFIX = "antigravity/"
    BASE_URL = _API_BASE_DAILY
    REFRESH_MARGIN_SECONDS = 300

    callback_path = "/oauth-callback"
    callback_port = 51121
    oauth_ttl_seconds = 900

    # 静态模型目录（采用内置静态 registry；上游无稳定公开目录接口）。
    _SUPPORTED_MODELS: tuple[tuple[str, str], ...] = (
        ("claude-opus-4-6-thinking", "Claude Opus 4.6 (Thinking)"),
        ("claude-sonnet-4-6", "Claude Sonnet 4.6 (Thinking)"),
        ("gemini-3.1-pro-low", "Gemini 3.1 Pro (Low)"),
        ("gemini-pro-agent", "Gemini 3.1 Pro (High)"),
        ("gemini-3-flash", "Gemini 3 Flash"),
        ("gemini-3.6-flash-high", "Gemini 3.6 Flash (High)"),
        ("gemini-3.7-flash-high", "Gemini 3.7 Flash (High)"),
        ("gemini-3.8-flash-high", "Gemini 3.8 Flash (High)"),
        ("gemini-3.1-flash-lite", "Gemini 3.1 Flash Lite"),
        ("gemini-3.5-flash-lite", "Gemini 3.5 Flash Lite"),
        ("gpt-oss-120b-medium", "GPT-OSS 120B (Medium)"),
        ("gemini-3.1-flash-image", "Gemini 3.1 Flash Image"),
    )

    # ── LoopbackOAuthCapable ──────────────────────────────────

    def build_authorize_url(self, state: str, redirect_uri: str) -> str:
        params = {
            "access_type": "offline",
            "client_id": _CLIENT_ID,
            "prompt": "consent",
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(_SCOPES),
            "state": state,
        }
        return f"{_AUTH_ENDPOINT}?{urlencode(params)}"

    async def exchange_code(
        self, code: str, redirect_uri: str,
    ) -> ValidatedCredential:
        """授权码 → token → userinfo(email) → loadCodeAssist(project_id)。"""
        async with _http_client() as client:
            resp = await client.post(
                _TOKEN_ENDPOINT,
                data={
                    "code": code,
                    "client_id": _CLIENT_ID,
                    "client_secret": _CLIENT_SECRET,
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            if resp.status_code != 200:
                body = resp.text[:500]
                logger.warning(
                    "Antigravity token 交换失败: %d %s", resp.status_code, body,
                )
                raise RuntimeError(
                    f"授权码交换失败 (HTTP {resp.status_code})，请重新发起登录"
                )
            data = resp.json()

            access_token = str(data.get("access_token") or "").strip()
            refresh_token = str(data.get("refresh_token") or "").strip()
            if not access_token:
                raise RuntimeError("Token 交换响应中缺少 access_token")

            email = await self._fetch_email(client, access_token)
            project_id = await self._fetch_project_id(client, access_token)

        return ValidatedCredential(
            access_token=access_token,
            refresh_token=refresh_token or None,
            expires_at=_expires_at_from(data.get("expires_in")),
            account_id=email or project_id,
            plan_type="",
            credential_type="oauth",
            extra_data={
                key: value
                for key, value in {
                    "email": email,
                    "project_id": project_id,
                }.items()
                if value
            } or None,
        )

    async def _fetch_email(
        self, client: httpx.AsyncClient, access_token: str,
    ) -> str:
        resp = await client.get(
            _USERINFO_ENDPOINT,
            headers={
                "Authorization": f"Bearer {access_token}",
                "User-Agent": _REQUEST_USER_AGENT,
            },
        )
        if resp.status_code != 200:
            raise RuntimeError(
                f"获取 Google 账号信息失败 (HTTP {resp.status_code})"
            )
        email = str(resp.json().get("email") or "").strip()
        if not email:
            raise RuntimeError("Google 账号信息未返回 email")
        return email

    async def _fetch_project_id(
        self, client: httpx.AsyncClient, access_token: str,
    ) -> str:
        """loadCodeAssist 发现 project；无项目时走 onboardUser 轮询。"""
        resp = await client.post(
            f"{_API_BASE_PROD}/{_API_VERSION}:loadCodeAssist",
            json={"metadata": {"ideType": "ANTIGRAVITY"}},
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "*/*",
                "Content-Type": "application/json",
                "User-Agent": _REQUEST_USER_AGENT,
            },
        )
        if resp.status_code != 200:
            raise RuntimeError(
                f"loadCodeAssist 失败 (HTTP {resp.status_code}): "
                f"{resp.text[:300]}"
            )
        load_resp = resp.json()
        project_id = _extract_project_id(load_resp)
        if project_id:
            return project_id
        return await self._onboard_user(
            client, access_token, _default_tier_id(load_resp),
        )

    async def _onboard_user(
        self,
        client: httpx.AsyncClient,
        access_token: str,
        tier_id: str,
    ) -> str:
        body = {
            "tier_id": tier_id,
            "metadata": {
                "ide_type": "ANTIGRAVITY",
                "ide_version": "2.9.1",
                "ide_name": "antigravity",
            },
        }
        for _attempt in range(_ONBOARD_MAX_ATTEMPTS):
            resp = await client.post(
                f"{_API_BASE_DAILY}/{_API_VERSION}:onboardUser",
                json=body,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "*/*",
                    "Content-Type": "application/json",
                    "User-Agent": _ONBOARD_USER_AGENT,
                    "X-Goog-Api-Client": _GOOG_API_CLIENT_UA,
                },
            )
            if resp.status_code != 200:
                raise RuntimeError(
                    f"onboardUser 失败 (HTTP {resp.status_code}): "
                    f"{resp.text[:300]}"
                )
            data = resp.json()
            if data.get("done") is True:
                project_id = _extract_project_id(
                    data.get("response") if isinstance(data.get("response"), dict)
                    else None
                )
                if project_id:
                    return project_id
                raise RuntimeError("onboardUser 完成但未返回 project_id")
            await asyncio.sleep(_ONBOARD_POLL_SECONDS)
        raise RuntimeError(
            f"onboardUser 未在 {_ONBOARD_MAX_ATTEMPTS} 次轮询内完成"
        )

    # ── AuthProvider ──────────────────────────────────────────

    def validate_token_data(self, raw_data: dict[str, Any]) -> ValidatedCredential:
        """验证粘贴的凭证 JSON（兼容 antigravity-*.json 凭证格式）。"""
        access_token = str(
            raw_data.get("access_token") or raw_data.get("token") or ""
        ).strip()
        refresh_token = str(raw_data.get("refresh_token") or "").strip() or None
        if not access_token:
            raise ValueError(
                "缺少 access_token。请粘贴完整的 Antigravity 凭证 JSON "
                "（需包含 access_token / refresh_token / project_id）。"
            )
        project_id = str(
            raw_data.get("project_id") or raw_data.get("projectId") or ""
        ).strip()
        email = str(raw_data.get("email") or "").strip()
        expires_at = _parse_expires_at(
            raw_data.get("expired") or raw_data.get("expires_at")
            or raw_data.get("expires_in")
        )
        return ValidatedCredential(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
            account_id=email or project_id,
            plan_type="",
            credential_type="oauth",
            extra_data={
                key: value
                for key, value in {
                    "email": email,
                    "project_id": project_id,
                }.items()
                if value
            } or None,
        )

    async def refresh_token(self, refresh_token: str) -> RefreshedCredential:
        if not refresh_token:
            raise RuntimeError("无 refresh token，无法刷新。请重新登录 Antigravity。")
        async with _http_client() as client:
            resp = await client.post(
                _TOKEN_ENDPOINT,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": _CLIENT_ID,
                    "client_secret": _CLIENT_SECRET,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        if resp.status_code != 200:
            body = resp.text[:500]
            logger.warning(
                "Antigravity token 刷新失败: %d %s", resp.status_code, body,
            )
            raise RuntimeError(
                f"Token 刷新失败 (HTTP {resp.status_code})，请重新登录"
            )
        data = resp.json()
        new_access = str(data.get("access_token") or "").strip()
        if not new_access:
            raise RuntimeError("刷新响应中缺少 access_token")
        return RefreshedCredential(
            access_token=new_access,
            refresh_token=str(data.get("refresh_token") or "").strip()
            or refresh_token,
            expires_at=_expires_at_from(data.get("expires_in")),
        )

    def get_api_credential(self, access_token: str) -> tuple[str, str]:
        return (access_token, self.BASE_URL)

    def get_request_headers(self, profile: Any) -> dict[str, str]:
        """携带 Antigravity UA 指纹与 GCP project id（信封由客户端消费）。"""
        headers = {"User-Agent": _REQUEST_USER_AGENT}
        raw = getattr(profile, "extra_data", None)
        data: Any = None
        if isinstance(raw, str) and raw:
            try:
                data = json.loads(raw)
            except Exception:
                data = None
        elif isinstance(raw, dict):
            data = raw
        project_id = ""
        if isinstance(data, dict):
            project_id = str(data.get("project_id") or "").strip()
        if project_id:
            headers[PROJECT_ID_HEADER] = project_id
        return headers

    def matches_model(self, model: str) -> bool:
        return False  # 仅通过 antigravity/ 前缀匹配，不误伤同名裸模型

    # ── 订阅档案钩子 ──────────────────────────────────────────

    @classmethod
    def profile_name_for_model(cls, model_id: str) -> str:
        return f"{cls.MODEL_NAME_PREFIX}{model_id}"

    @classmethod
    def list_supported_model_entries(cls) -> list[dict[str, str]]:
        return [
            {
                "model": model_id,
                "display_name": display,
                "profile_name": cls.profile_name_for_model(model_id),
                "public_model_id": cls.profile_name_for_model(model_id),
            }
            for model_id, display in cls._SUPPORTED_MODELS
        ]

    _DEFAULT_PROFILE_NAME = "antigravity/claude-sonnet-4-6"
    _DEFAULT_MODEL_ID = "claude-sonnet-4-6"

    async def subscription_profiles_on_connect(
        self,
        record: Any,
        existing_profiles: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if any(
            p.get("name") == self._DEFAULT_PROFILE_NAME
            or p.get("model") == self._DEFAULT_PROFILE_NAME
            for p in existing_profiles
        ):
            return []
        return [{
            "name": self._DEFAULT_PROFILE_NAME,
            "model": self._DEFAULT_PROFILE_NAME,
            "api_key": "",
            "base_url": self.BASE_URL,
            "description": "Claude Sonnet 4.6 - Antigravity 订阅（Google OAuth）",
            "protocol": self.PROTOCOL,
            "thinking_mode": "gemini_level",
            "model_family": "claude",
        }]

    def profile_display_info(self, profile: Any) -> dict[str, Any]:
        raw = getattr(profile, "extra_data", None)
        data: Any = None
        if isinstance(raw, str) and raw:
            try:
                data = json.loads(raw)
            except Exception:
                data = None
        elif isinstance(raw, dict):
            data = raw
        info: dict[str, Any] = {}
        if isinstance(data, dict):
            email = data.get("email")
            if isinstance(email, str) and email.strip():
                info["email"] = email.strip()
        return info
