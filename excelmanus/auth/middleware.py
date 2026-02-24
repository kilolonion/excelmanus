"""Authentication middleware that protects API endpoints.

Whitelisted paths (no auth required):
- /api/v1/auth/*    — login, register, OAuth flows
- /api/v1/health    — health check
- /docs, /openapi.json, /redoc — API docs
- Static file paths
"""

from __future__ import annotations

import logging
from typing import Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from excelmanus.auth.security import decode_token

logger = logging.getLogger(__name__)

_PUBLIC_PREFIXES = (
    "/api/v1/auth/",
    "/api/v1/health",
    "/docs",
    "/openapi.json",
    "/redoc",
    "/_next/",
    "/favicon",
    "/logo",
)


class AuthMiddleware(BaseHTTPMiddleware):
    """Inject ``request.state.user_id`` from Bearer token.

    Public endpoints are allowed through without auth.
    All other ``/api/`` endpoints require a valid access token.
    """

    async def dispatch(
        self, request: Request, call_next: Callable[..., Response],
    ) -> Response:
        path = request.url.path

        # Let public endpoints through
        if any(path.startswith(prefix) for prefix in _PUBLIC_PREFIXES):
            return await call_next(request)

        # Non-API paths (frontend assets) pass through
        if not path.startswith("/api/"):
            return await call_next(request)

        # Check for auth_enabled flag (allows disabling auth for backward compat)
        auth_enabled = getattr(request.app.state, "auth_enabled", False)
        if not auth_enabled:
            return await call_next(request)

        # Extract Bearer token
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return JSONResponse(
                status_code=401,
                content={"error": "缺少认证凭据", "error_id": ""},
                headers={"WWW-Authenticate": "Bearer"},
            )

        token = auth_header[7:]
        payload = decode_token(token)
        if payload is None or payload.get("type") != "access":
            return JSONResponse(
                status_code=401,
                content={"error": "无效或过期的 token", "error_id": ""},
                headers={"WWW-Authenticate": "Bearer"},
            )

        user_id = payload.get("sub")
        if not user_id:
            return JSONResponse(
                status_code=401,
                content={"error": "无效的 token 载荷", "error_id": ""},
            )

        # Inject user_id into request state for downstream handlers
        request.state.user_id = user_id
        request.state.user_role = payload.get("role", "user")

        return await call_next(request)
