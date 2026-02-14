"""MCP 客户端封装模块。

封装与单个 MCP Server 的连接、工具发现和工具调用逻辑。
支持 stdio、SSE 和 Streamable HTTP 三种传输方式。

使用 ``contextlib.AsyncExitStack`` 管理 MCP SDK 异步上下文管理器的生命周期，
确保 ``connect()`` 返回后传输层和 session 仍保持活跃，直到显式调用 ``close()``。
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import AsyncExitStack
from typing import Any

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.types import CallToolResult

try:
    from mcp.client.streamable_http import streamable_http_client
except Exception:  # pragma: no cover - 兼容旧版 MCP SDK
    streamable_http_client = None  # type: ignore[assignment]

from excelmanus.mcp.config import MCPServerConfig

logger = logging.getLogger("excelmanus.mcp.client")


class MCPClientWrapper:
    """单个 MCP Server 的客户端封装。

    通过 MCP SDK 与远程 Server 通信，提供工具发现和工具调用能力。

    典型用法::

        client = MCPClientWrapper(config)
        await client.connect()
        tools = await client.discover_tools()
        result = await client.call_tool("tool_name", {"arg": "value"})
        await client.close()
    """

    def __init__(self, config: MCPServerConfig) -> None:
        self._config = config
        self._session: ClientSession | None = None
        self._exit_stack: AsyncExitStack | None = None
        self._tools: list[dict] = []  # 原始工具列表缓存
        self._managed_pids: set[int] = set()

    async def connect(self) -> None:
        """建立与 MCP Server 的连接。

        根据 ``config.transport`` 选择传输方式：
        - ``stdio``：启动子进程，通过 stdin/stdout 通信
        - ``sse``：连接到 HTTP SSE 端点
        - ``streamable_http``：连接到 MCP Streamable HTTP 端点

        使用 ``AsyncExitStack`` 保持上下文管理器的生命周期，
        直到调用 ``close()`` 时统一释放。

        Raises:
            Exception: 连接失败时抛出底层异常。
        """
        self._exit_stack = AsyncExitStack()

        try:
            if self._config.transport == "stdio":
                read_stream, write_stream = await self._connect_stdio()
            elif self._config.transport == "sse":
                read_stream, write_stream = await self._connect_sse()
            else:
                read_stream, write_stream = await self._connect_streamable_http()

            # 创建并进入 ClientSession 上下文
            session = await self._exit_stack.enter_async_context(
                ClientSession(read_stream, write_stream)
            )
            # 初始化 MCP 会话（握手）
            await session.initialize()
            self._session = session

            logger.info(
                "已连接 MCP Server '%s' (transport=%s)",
                self._config.name,
                self._config.transport,
            )
        except Exception:
            # 连接失败时清理已进入的上下文
            await self._cleanup_exit_stack()
            raise

    async def _connect_stdio(self) -> tuple[Any, Any]:
        """建立 stdio 传输连接，返回 (read_stream, write_stream)。"""
        params = StdioServerParameters(
            command=self._config.command,
            args=self._config.args or [],
            env=self._config.env or None,
        )
        read_stream, write_stream = await self._exit_stack.enter_async_context(
            stdio_client(params)
        )
        return read_stream, write_stream

    async def _connect_sse(self) -> tuple[Any, Any]:
        """建立 SSE 传输连接，返回 (read_stream, write_stream)。"""
        headers = self._config.headers or None
        read_stream, write_stream = await self._exit_stack.enter_async_context(
            sse_client(self._config.url, headers=headers)
        )
        return read_stream, write_stream

    async def _connect_streamable_http(self) -> tuple[Any, Any]:
        """建立 Streamable HTTP 传输连接，返回 (read_stream, write_stream)。"""
        if streamable_http_client is None:
            raise RuntimeError("当前 mcp SDK 不支持 streamable_http 传输")

        client_headers = self._config.headers or None
        http_client = await self._exit_stack.enter_async_context(
            httpx.AsyncClient(
                headers=client_headers,
                timeout=self._config.timeout,
            )
        )
        read_stream, write_stream, _ = await self._exit_stack.enter_async_context(
            streamable_http_client(self._config.url, http_client=http_client)
        )
        return read_stream, write_stream

    async def discover_tools(self) -> list[dict]:
        """调用 tools/list 获取远程工具定义。

        返回 MCP 协议格式的工具列表（每个元素为 MCP Tool 对象）。
        结果会被缓存，后续调用直接返回缓存。

        Returns:
            MCP Tool 对象列表。

        Raises:
            RuntimeError: 未连接时调用。
        """
        if self._session is None:
            raise RuntimeError(
                f"MCP Server '{self._config.name}' 未连接，请先调用 connect()"
            )

        result = await self._session.list_tools()
        # 缓存原始工具列表（Tool 对象）
        self._tools = result.tools
        logger.debug(
            "MCP Server '%s' 发现 %d 个工具",
            self._config.name,
            len(self._tools),
        )
        return self._tools

    async def call_tool(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> CallToolResult:
        """调用远程工具并返回 MCP CallToolResult。

        ``tool_name`` 为原始名称（不含前缀）。
        超时由 ``config.timeout`` 控制。

        Args:
            tool_name: 远程工具的原始名称。
            arguments: 工具调用参数字典。

        Returns:
            MCP ``CallToolResult`` 对象。

        Raises:
            RuntimeError: 未连接时调用。
            asyncio.TimeoutError: 工具调用超时。
            Exception: MCP Server 返回错误。
        """
        if self._session is None:
            raise RuntimeError(
                f"MCP Server '{self._config.name}' 未连接，请先调用 connect()"
            )

        logger.debug(
            "调用 MCP 工具 '%s' (server=%s), 参数: %s",
            tool_name,
            self._config.name,
            _truncate_args(arguments),
        )

        result = await asyncio.wait_for(
            self._session.call_tool(tool_name, arguments),
            timeout=self._config.timeout,
        )

        # 检查 MCP Server 是否返回了错误标记
        if getattr(result, "isError", False):
            error_text = _extract_error_text(result)
            raise RuntimeError(
                f"MCP 工具 '{tool_name}' 执行失败 (server={self._config.name}): "
                f"{error_text}"
            )

        return result

    async def close(self) -> None:
        """关闭连接，释放资源。

        安全调用：即使未连接或已关闭也不会抛出异常。
        """
        self._session = None
        self._tools = []
        await self._cleanup_exit_stack()
        self._managed_pids.clear()
        logger.debug("已关闭 MCP Server '%s' 连接", self._config.name)

    @property
    def is_connected(self) -> bool:
        """连接是否处于活跃状态。"""
        return self._session is not None

    @property
    def managed_pids(self) -> set[int]:
        """返回由当前 client 启动并托管的本地 MCP 进程 PID。"""
        return set(self._managed_pids)

    def bind_managed_pids(self, pids: set[int]) -> None:
        """绑定当前 client 托管的本地 MCP 进程 PID 集合。"""
        self._managed_pids = {int(pid) for pid in pids if int(pid) > 0}

    async def _cleanup_exit_stack(self) -> None:
        """安全关闭 AsyncExitStack。"""
        if self._exit_stack is not None:
            try:
                await self._exit_stack.aclose()
            except BaseException as exc:
                logger.warning(
                    "关闭 MCP Server '%s' 的 ExitStack 时出错: %s",
                    self._config.name,
                    exc,
                )
            finally:
                self._exit_stack = None


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _truncate_args(arguments: dict[str, Any], max_len: int = 200) -> str:
    """截断参数字符串用于日志输出。"""
    text = str(arguments)
    if len(text) > max_len:
        return text[:max_len] + "..."
    return text


def _extract_error_text(result: CallToolResult) -> str:
    """从 CallToolResult 中提取错误文本。"""
    parts: list[str] = []
    for item in getattr(result, "content", []):
        if getattr(item, "type", None) == "text":
            text = getattr(item, "text", "")
            if text:
                parts.append(text)
    return "\n".join(parts) if parts else "未知错误"
