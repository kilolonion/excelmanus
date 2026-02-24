"""认证中间件，保护 API 端点。

白名单路径（无需认证）：
- /api/v1/auth/*    — 登录、注册、OAuth 流程
- /api/v1/health    — 健康检查
- /docs, /openapi.json, /redoc — API 文档
- 静态文件路径
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
    """将 Bearer token 中的 ``request.state.user_id`` 注入请求。

    公开端点无需认证直接放行。
    所有其他 ``/api/`` 端点需要有效的访问令牌。
    """

    async def dispatch(
        self, request: Request, call_next: Callable[..., Response],
    ) -> Response:
        # 放行 CORS 预检请求，避免 OPTIONS 被认证拦截
        if request.method == "OPTIONS":
            return await call_next(request)

        path = request.url.path

        # 放行公开端点
        if any(path.startswith(prefix) for prefix in _PUBLIC_PREFIXES):
            return await call_next(request)

        # 非 API 路径（前端静态资源）直接放行
        if not path.startswith("/api/"):
            return await call_next(request)

        # 检查 auth_enabled 标志（允许禁用认证以保持向后兼容）
        auth_enabled = getattr(request.app.state, "auth_enabled", False)
        if not auth_enabled:
            return await call_next(request)

        # 提取 Bearer token
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

        # 将 user_id 注入 request state，供下游处理器使用
        request.state.user_id = user_id
        request.state.user_role = payload.get("role", "user")

        return await call_next(request)
