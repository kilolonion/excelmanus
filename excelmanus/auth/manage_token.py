"""进程级管理令牌：非 loopback 监听时强制，不恢复登录/多租户。"""

from __future__ import annotations

import hashlib
import hmac
import os
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

MANAGE_TOKEN_ENV = "EXCELMANUS_MANAGE_TOKEN"
MANAGE_TOKEN_MIN_LEN = 16
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "0:0:0:0:0:0:0:1"})

_HEALTH_PATH = "/api/v1/health"
_DOWNLOAD_PREFIX = "/api/v1/files/dl/"


def get_manage_token() -> str:
    return (os.environ.get(MANAGE_TOKEN_ENV) or "").strip()


def manage_token_configured() -> bool:
    return len(get_manage_token()) >= MANAGE_TOKEN_MIN_LEN


def is_loopback_bind_host(host: str) -> bool:
    h = (host or "").strip().lower()
    if h in LOOPBACK_HOSTS:
        return True
    if h.startswith("127."):
        return True
    return False


def require_manage_token_for_bind(host: str) -> None:
    """Refuse to start on a non-loopback bind without a long enough token."""
    if is_loopback_bind_host(host):
        return
    token = get_manage_token()
    if len(token) < MANAGE_TOKEN_MIN_LEN:
        raise SystemExit(
            f"监听 {host} 时必须设置 {MANAGE_TOKEN_ENV}（至少 {MANAGE_TOKEN_MIN_LEN} 字符）。"
            "默认请绑定 127.0.0.1，由反向代理对外暴露。"
        )


def _provided_token(request: Request) -> str:
    auth = request.headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    header = (request.headers.get("x-excelmanus-token") or "").strip()
    if header:
        return header
    return (request.query_params.get("manage_token") or "").strip()


def token_matches(provided: str, expected: str) -> bool:
    if not provided or not expected:
        return False
    left = hashlib.sha256(provided.encode("utf-8")).digest()
    right = hashlib.sha256(expected.encode("utf-8")).digest()
    return hmac.compare_digest(left, right)


class ManageTokenMiddleware:
    """When EXCELMANUS_MANAGE_TOKEN is set, require it on /api except health and download tokens."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        expected = get_manage_token()
        if len(expected) < MANAGE_TOKEN_MIN_LEN:
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive)
        if request.method == "OPTIONS":
            await self.app(scope, receive, send)
            return
        path = request.url.path
        if path == _HEALTH_PATH or path.startswith(_DOWNLOAD_PREFIX):
            await self.app(scope, receive, send)
            return
        if not path.startswith("/api/"):
            await self.app(scope, receive, send)
            return
        if token_matches(_provided_token(request), expected):
            await self.app(scope, receive, send)
            return

        response = JSONResponse(
            {"error": "需要管理令牌", "error_id": "manage_token_required"},
            status_code=401,
        )
        await response(scope, receive, send)
