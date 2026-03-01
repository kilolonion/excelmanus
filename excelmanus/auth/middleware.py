"""认证中间件，保护 API 端点。

白名单路径（无需认证）：
- /api/v1/auth/*    — 登录、注册、OAuth 流程
- /api/v1/health    — 健康检查
- /docs, /openapi.json, /redoc — API 文档
- 静态文件路径

实现为纯 ASGI 中间件（非 BaseHTTPMiddleware），避免 Starlette
BaseHTTPMiddleware 对 StreamingResponse / SSE 的已知缓冲问题：
BaseHTTPMiddleware 通过 anyio.MemoryObjectStream 管道传递响应体，
会导致 SSE 长连接的 chunk 投递延迟甚至堆积。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

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


def _make_json_401(detail: str) -> tuple[Message, Message]:
    """构造 401 JSON 响应的 ASGI 消息对。"""
    body = json.dumps({"error": detail, "error_id": ""}).encode()
    start: Message = {
        "type": "http.response.start",
        "status": 401,
        "headers": [
            (b"content-type", b"application/json"),
            (b"www-authenticate", b"Bearer"),
        ],
    }
    body_msg: Message = {
        "type": "http.response.body",
        "body": body,
    }
    return start, body_msg


class AuthMiddleware:
    """纯 ASGI 认证中间件 — 将 Bearer token 中的 user_id 注入 scope.state。

    公开端点无需认证直接放行。
    所有其他 ``/api/`` 端点需要有效的访问令牌。

    使用纯 ASGI 协议实现，不继承 BaseHTTPMiddleware，
    确保 StreamingResponse / SSE 的 chunk 被即时透传给客户端。
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path: str = scope.get("path", "")
        method: str = scope.get("method", "")

        # 放行 CORS 预检请求
        if method == "OPTIONS":
            await self.app(scope, receive, send)
            return

        # 放行公开端点
        if any(path.startswith(prefix) for prefix in _PUBLIC_PREFIXES):
            await self.app(scope, receive, send)
            return

        # 非 API 路径（前端静态资源）直接放行
        if not path.startswith("/api/"):
            await self.app(scope, receive, send)
            return

        # 检查 auth_enabled 标志
        app_state: Any = scope.get("app")
        auth_enabled = False
        if app_state is not None:
            state_obj = getattr(app_state, "state", None)
            if state_obj is not None:
                auth_enabled = getattr(state_obj, "auth_enabled", False)
        if not auth_enabled:
            await self.app(scope, receive, send)
            return

        # 提取 Bearer token
        headers = dict(scope.get("headers", []))
        auth_header = (headers.get(b"authorization") or b"").decode("latin-1")
        if not auth_header.startswith("Bearer "):
            start, body = _make_json_401("缺少认证凭据")
            await send(start)
            await send(body)
            return

        token = auth_header[7:]
        payload = decode_token(token)
        if payload is None or payload.get("type") != "access":
            start, body = _make_json_401("无效或过期的 token")
            await send(start)
            await send(body)
            return

        user_id = payload.get("sub")
        if not user_id:
            start, body = _make_json_401("无效的 token 载荷")
            await send(start)
            await send(body)
            return

        # 将 user_id / user_role 注入 scope.state，供下游处理器使用
        state = scope.setdefault("state", {})
        state["user_id"] = user_id
        state["user_role"] = payload.get("role", "user")

        await self.app(scope, receive, send)
