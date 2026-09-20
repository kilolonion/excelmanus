"""Single administrator API guard, with legacy management token compatibility."""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import os
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

MANAGE_TOKEN_ENV = "EXCELMANUS_MANAGE_TOKEN"
MANAGE_TOKEN_MIN_LEN = 16
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "0:0:0:0:0:0:0:1"})

def get_manage_token() -> str:
    return (os.environ.get(MANAGE_TOKEN_ENV) or "").strip()


def manage_token_configured() -> bool:
    return len(get_manage_token()) >= MANAGE_TOKEN_MIN_LEN


def is_loopback_bind_host(host: str) -> bool:
    h = (host or "").strip().lower()
    if h in LOOPBACK_HOSTS:
        return True
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False


def require_manage_token_for_bind(host: str) -> None:
    """Refuse remote binding without administrator credentials."""
    from excelmanus.auth.access import access_enabled, access_explicitly_disabled, validate_access_config
    try:
        validate_access_config()
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if is_loopback_bind_host(host) or access_explicitly_disabled():
        return
    if not access_enabled():
        raise SystemExit(
            f"监听 {host} 时必须设置 EXCELMANUS_LOGIN_PASSWORD（至少 12 字符）"
            f"或 {MANAGE_TOKEN_ENV}（至少 {MANAGE_TOKEN_MIN_LEN} 字符）。"
            "默认请绑定 127.0.0.1，由反向代理对外暴露。"
        )


def _provided_token(request: Request) -> str:
    auth = request.headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    header = (request.headers.get("x-excelmanus-token") or "").strip()
    if header:
        return header
    # URL credentials leak through logs, history and referrers.
    return ""


def token_matches(provided: str, expected: str) -> bool:
    if not provided or not expected:
        return False
    left = hashlib.sha256(provided.encode("utf-8")).digest()
    right = hashlib.sha256(expected.encode("utf-8")).digest()
    return hmac.compare_digest(left, right)


class ManageTokenMiddleware:
    """Protect every API route using a session cookie or management header."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        from excelmanus.auth.access import (
            PUBLIC_PATHS, access_enabled, authenticated, require_browser_header,
            validate_access_config,
        )
        from starlette.concurrency import run_in_threadpool
        from fastapi import HTTPException

        if not await run_in_threadpool(access_enabled):
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive)
        if request.method == "OPTIONS":
            await self.app(scope, receive, send)
            return
        path = request.url.path
        if path.rstrip("/") in PUBLIC_PATHS:
            await self.app(scope, receive, send)
            return
        if not path.startswith("/api/") and path not in {"/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"}:
            await self.app(scope, receive, send)
            return
        try:
            await run_in_threadpool(validate_access_config)
        except ValueError:
            await JSONResponse({"error": "登录配置无效，请联系管理员", "error_id": "access_config_invalid"}, status_code=503)(scope, receive, send)
            return
        if await run_in_threadpool(authenticated, request):
            # Header tokens are not ambient browser credentials. Cookie writes
            # require a non-simple header so untrusted origins cannot submit.
            if request.method not in {"GET", "HEAD", "OPTIONS"} and not token_matches(_provided_token(request), get_manage_token()):
                try:
                    require_browser_header(request)
                except HTTPException as exc:
                    await JSONResponse({"error": exc.detail}, status_code=exc.status_code)(scope, receive, send)
                    return
            await self.app(scope, receive, send)
            return

        response = JSONResponse(
            {"error": "请先登录，或重新登录后继续", "error_id": "authentication_required"},
            status_code=401,
            headers={"Cache-Control": "no-store", "X-ExcelManus-Auth": "required"},
        )
        await response(scope, receive, send)
