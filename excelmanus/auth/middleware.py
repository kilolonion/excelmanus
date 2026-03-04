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

import ipaddress
import json
import logging
import os
import time
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from excelmanus.auth.security import decode_token

logger = logging.getLogger(__name__)

_PUBLIC_PREFIXES = (
    "/api/v1/auth/",
    "/api/v1/health",
    "/api/v1/files/dl/",
    "/docs",
    "/openapi.json",
    "/redoc",
    "/_next/",
    "/favicon",
    "/logo",
)


# ── C1: Service Token IP 白名单 ──────────────────────────────────

_DEFAULT_ALLOWED_IPS = "127.0.0.1,::1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"


def _parse_ip_whitelist(raw: str) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    """解析逗号分隔的 IP/CIDR 白名单字符串。"""
    networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            networks.append(ipaddress.ip_network(part, strict=False))
        except ValueError:
            logger.warning("忽略无效的 IP/CIDR 白名单条目: %s", part)
    return networks


def _check_ip_allowed(client_host: str, allowed_raw: str) -> bool:
    """检查客户端 IP 是否在白名单内。

    空白名单字符串 → 跳过检查（全部放行）。
    支持 IPv4/IPv6 + CIDR 段。
    """
    if not allowed_raw.strip():
        return True
    if not client_host:
        return False
    try:
        addr = ipaddress.ip_address(client_host)
    except ValueError:
        return False
    networks = _parse_ip_whitelist(allowed_raw)
    return any(addr in net for net in networks)


# ── C3: X-On-Behalf-Of 用户验证缓存 ─────────────────────────────

_OBO_CACHE_TTL = 60.0  # 秒


class _OboCache:
    """进程级 TTL 缓存：on_behalf_of user_id → (is_valid, timestamp)。

    避免每次请求都查 DB。缓存大小不超过 1024 条。
    """

    def __init__(self, ttl: float = _OBO_CACHE_TTL, max_size: int = 1024) -> None:
        self._ttl = ttl
        self._max_size = max_size
        self._cache: dict[str, tuple[bool, float]] = {}

    def get(self, user_id: str) -> bool | None:
        """返回 True/False 或 None（缓存未命中/过期）。"""
        entry = self._cache.get(user_id)
        if entry is None:
            return None
        is_valid, ts = entry
        if time.monotonic() - ts > self._ttl:
            self._cache.pop(user_id, None)
            return None
        return is_valid

    def put(self, user_id: str, is_valid: bool) -> None:
        if len(self._cache) >= self._max_size:
            # 简易淘汰：删最早条目
            oldest_key = next(iter(self._cache))
            self._cache.pop(oldest_key, None)
        self._cache[user_id] = (is_valid, time.monotonic())

    def invalidate(self, user_id: str) -> None:
        self._cache.pop(user_id, None)


_obo_cache = _OboCache()


def _make_json_403(detail: str) -> tuple[Message, Message]:
    """构造 403 JSON 响应的 ASGI 消息对。"""
    body = json.dumps({"error": detail, "error_id": ""}).encode()
    start: Message = {
        "type": "http.response.start",
        "status": 403,
        "headers": [
            (b"content-type", b"application/json"),
        ],
    }
    body_msg: Message = {
        "type": "http.response.body",
        "body": body,
    }
    return start, body_msg


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
        if payload is None:
            start, body = _make_json_401("无效或过期的 token")
            await send(start)
            await send(body)
            return

        token_type = payload.get("type")

        # ── 服务令牌（Bot 代理调用）──
        if token_type == "service":
            # C1: 来源 IP 限制
            client_host = scope.get("client", ("", 0))[0]
            allowed_ips = os.environ.get(
                "EXCELMANUS_SERVICE_TOKEN_ALLOWED_IPS", _DEFAULT_ALLOWED_IPS,
            )
            if not _check_ip_allowed(client_host, allowed_ips):
                logger.warning(
                    "Service token 请求被拒绝: 来源 IP %s 不在白名单内", client_host,
                )
                start, body = _make_json_403("来源 IP 不在允许范围内")
                await send(start)
                await send(body)
                return

            on_behalf_of = (headers.get(b"x-on-behalf-of") or b"").decode("latin-1").strip()

            # C3: X-On-Behalf-Of 一致性校验
            if on_behalf_of and not on_behalf_of.startswith("channel_anon:"):
                cached_valid = _obo_cache.get(on_behalf_of)
                if cached_valid is None:
                    # 缓存未命中 → 查 DB
                    state_obj = getattr(app_state, "state", None) if app_state else None
                    user_store = getattr(state_obj, "user_store", None) if state_obj else None
                    if user_store is not None:
                        target_user = user_store.get_by_id(on_behalf_of)
                        cached_valid = target_user is not None and target_user.is_active
                        _obo_cache.put(on_behalf_of, cached_valid)
                    else:
                        # 无 user_store（认证组件未完全初始化）→ 放行
                        cached_valid = True
                if not cached_valid:
                    logger.warning(
                        "X-On-Behalf-Of 校验失败: 用户 %s 不存在或已禁用",
                        on_behalf_of,
                    )
                    start, body = _make_json_403(
                        "代理用户不存在或已禁用",
                    )
                    await send(start)
                    await send(body)
                    return

            state = scope.setdefault("state", {})
            state["user_role"] = "service"
            state["is_service_token"] = True
            if on_behalf_of:
                state["user_id"] = on_behalf_of
            # 不携带 X-On-Behalf-Of 时不设 user_id，下游按匿名处理
            await self.app(scope, receive, send)
            return

        # ── 普通用户访问令牌 ──
        if token_type != "access":
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
