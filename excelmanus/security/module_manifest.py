"""Security 策略清单：供静态分析与运行时沙盒共享。"""

from __future__ import annotations

NETWORK_MODULES: frozenset[str] = frozenset({
    "requests",
    "urllib",
    "urllib.request",
    "urllib.parse",
    "urllib.error",
    "httpx",
    "aiohttp",
    "socket",
    "ssl",
    "http",
    "http.client",
    "http.server",
    "http.cookiejar",
    "ftplib",
    "smtplib",
    "imaplib",
    "poplib",
    "xmlrpc",
    "xmlrpc.client",
    "xmlrpc.server",
    "websocket",
    "websockets",
})

MODULE_ROOT_ALIASES: dict[str, str] = {
    "_socket": "socket",
    "_ssl": "ssl",
}

SOCKET_CONSTRUCTOR_NAMES: tuple[str, ...] = ("socket", "SocketType")
SOCKET_MODULE_BLOCKED_CALLS: tuple[str, ...] = (
    "create_connection",
    "create_server",
    "socketpair",
    "fromfd",
)
RAW_SOCKET_MODULE_BLOCKED_CALLS: tuple[str, ...] = ("socketpair", "fromfd")


def module_root(name: str) -> str:
    return name.split(".")[0]


def normalize_module_root(root: str) -> str:
    return MODULE_ROOT_ALIASES.get(root, root)


def canonical_module_root(module_name: str) -> str:
    return normalize_module_root(module_root(module_name))
