"""Block SSRF when the host fetches a user-supplied URL."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urljoin, urlparse

import httpx

_BLOCKED_HOSTS = frozenset({
    "localhost",
    "localhost.localdomain",
    "metadata.google.internal",
    "metadata.google.internal.",
})
_BLOCKED_EXACT_IPS = frozenset({
    "169.254.169.254",
    "fd00:ec2::254",
    "metadata.internal",
})


class UnsafeURLError(ValueError):
    """URL is not a public http(s) target."""


def _host_blocked(host: str) -> bool:
    h = (host or "").strip().lower().rstrip(".")
    if not h or h in _BLOCKED_HOSTS:
        return True
    if h.endswith(".localhost") or h.endswith(".local"):
        return True
    return False


def _ip_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if ip.is_private or ip.is_loopback or ip.is_link_local:
        return True
    if ip.is_multicast or ip.is_reserved or ip.is_unspecified:
        return True
    if getattr(ip, "is_site_local", False):
        return True
    if str(ip) in _BLOCKED_EXACT_IPS:
        return True
    return False


def assert_public_http_url(url: str) -> str:
    raw = (url or "").strip()
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"}:
        raise UnsafeURLError("仅支持 http/https 链接")
    if parsed.username or parsed.password:
        raise UnsafeURLError("URL 不能包含用户名或密码")
    host = parsed.hostname or ""
    if _host_blocked(host):
        raise UnsafeURLError("禁止访问该主机")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None and _ip_blocked(ip):
        raise UnsafeURLError("禁止访问该地址")
    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
    except OSError as exc:
        raise UnsafeURLError("无法解析主机名") from exc
    for info in infos:
        sockaddr = info[4]
        if not sockaddr:
            continue
        try:
            resolved = ipaddress.ip_address(sockaddr[0])
        except ValueError:
            continue
        if _ip_blocked(resolved):
            raise UnsafeURLError("禁止访问解析到的内网地址")
    return raw


async def fetch_public_http(url: str, *, max_bytes: int, timeout: float = 60.0) -> bytes:
    """GET a public URL, re-checking every redirect hop. Stream with a size cap."""
    current = assert_public_http_url(url)
    async with httpx.AsyncClient(follow_redirects=False, timeout=timeout) as client:
        for _ in range(5):
            assert_public_http_url(current)
            async with client.stream("GET", current) as resp:
                if resp.is_redirect:
                    location = resp.headers.get("location") or ""
                    if not location:
                        raise UnsafeURLError("重定向缺少 Location")
                    current = urljoin(str(resp.url), location)
                    continue
                resp.raise_for_status()
                chunks: list[bytes] = []
                total = 0
                async for chunk in resp.aiter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        raise UnsafeURLError(f"文件过大 (>{max_bytes // (1024 * 1024)} MB)")
                    chunks.append(chunk)
                return b"".join(chunks)
        raise UnsafeURLError("重定向次数过多")
