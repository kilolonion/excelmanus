"""OAuth2 helpers for GitHub and Google login."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)


def _get_oauth_proxy() -> str | None:
    """Read optional SOCKS5/HTTP proxy for OAuth requests (e.g. Google from China)."""
    return os.environ.get("EXCELMANUS_OAUTH_PROXY") or None


@dataclass(frozen=True)
class OAuthUserInfo:
    provider: str
    oauth_id: str
    email: str
    display_name: str
    avatar_url: str | None


# ── GitHub OAuth ──────────────────────────────────────────

GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_USER_URL = "https://api.github.com/user"
GITHUB_EMAILS_URL = "https://api.github.com/user/emails"


def get_github_config() -> tuple[str, str, str]:
    client_id = os.environ.get("EXCELMANUS_GITHUB_CLIENT_ID", "")
    client_secret = os.environ.get("EXCELMANUS_GITHUB_CLIENT_SECRET", "")
    redirect_uri = os.environ.get("EXCELMANUS_GITHUB_REDIRECT_URI", "")
    return client_id, client_secret, redirect_uri


def github_authorize_url(state: str | None = None) -> str:
    client_id, _, redirect_uri = get_github_config()
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": "read:user user:email",
    }
    if state:
        params["state"] = state
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{GITHUB_AUTHORIZE_URL}?{qs}"


async def github_exchange_code(code: str) -> OAuthUserInfo | None:
    client_id, client_secret, redirect_uri = get_github_config()
    if not client_id or not client_secret:
        logger.warning("GitHub OAuth not configured")
        return None

    async with httpx.AsyncClient(timeout=15) as client:
        # Exchange code for access token
        resp = await client.post(
            GITHUB_TOKEN_URL,
            json={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
            },
            headers={"Accept": "application/json"},
        )
        if resp.status_code != 200:
            logger.warning("GitHub token exchange failed: %s", resp.text)
            return None

        token_data = resp.json()
        access_token = token_data.get("access_token")
        if not access_token:
            logger.warning("GitHub token missing from response")
            return None

        headers = {"Authorization": f"Bearer {access_token}"}

        # Fetch user profile
        user_resp = await client.get(GITHUB_USER_URL, headers=headers)
        if user_resp.status_code != 200:
            return None
        user_data = user_resp.json()

        # Fetch primary email
        email = user_data.get("email")
        if not email:
            emails_resp = await client.get(GITHUB_EMAILS_URL, headers=headers)
            if emails_resp.status_code == 200:
                for e in emails_resp.json():
                    if e.get("primary") and e.get("verified"):
                        email = e["email"]
                        break

        if not email:
            logger.warning("GitHub user has no verified email")
            return None

        return OAuthUserInfo(
            provider="github",
            oauth_id=str(user_data["id"]),
            email=email,
            display_name=user_data.get("name") or user_data.get("login") or "",
            avatar_url=user_data.get("avatar_url"),
        )


# ── Google OAuth ──────────────────────────────────────────

GOOGLE_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"


def get_google_config() -> tuple[str, str, str]:
    client_id = os.environ.get("EXCELMANUS_GOOGLE_CLIENT_ID", "")
    client_secret = os.environ.get("EXCELMANUS_GOOGLE_CLIENT_SECRET", "")
    redirect_uri = os.environ.get("EXCELMANUS_GOOGLE_REDIRECT_URI", "")
    return client_id, client_secret, redirect_uri


def google_authorize_url(state: str | None = None) -> str:
    client_id, _, redirect_uri = get_google_config()
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "offline",
    }
    if state:
        params["state"] = state
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{GOOGLE_AUTHORIZE_URL}?{qs}"


async def google_exchange_code(code: str) -> OAuthUserInfo | None:
    client_id, client_secret, redirect_uri = get_google_config()
    if not client_id or not client_secret:
        logger.warning("Google OAuth not configured")
        return None

    proxy = _get_oauth_proxy()
    async with httpx.AsyncClient(timeout=15, proxy=proxy) as client:
        resp = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        if resp.status_code != 200:
            logger.warning("Google token exchange failed: %s", resp.text)
            return None

        token_data = resp.json()
        access_token = token_data.get("access_token")
        if not access_token:
            return None

        user_resp = await client.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if user_resp.status_code != 200:
            return None

        info = user_resp.json()
        email = info.get("email")
        if not email:
            return None

        return OAuthUserInfo(
            provider="google",
            oauth_id=info.get("sub", ""),
            email=email,
            display_name=info.get("name", ""),
            avatar_url=info.get("picture"),
        )
