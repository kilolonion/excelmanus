"""MCPClientWrapper 单元测试。

使用 unittest.mock 模拟 MCP SDK 接口，测试：
- 工具调用超时处理（Requirement 5.4）
- 工具调用错误处理（Requirement 5.5）
- 未连接时调用 discover_tools / call_tool 应抛出 RuntimeError
- close() 安全调用（即使未连接也不抛异常）
- is_connected 属性正确反映连接状态
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from excelmanus.mcp.client import MCPClientWrapper, _extract_error_text
from excelmanus.mcp.config import MCPServerConfig


# ── 辅助工厂 ──────────────────────────────────────────────────────


def _stdio_config(**overrides) -> MCPServerConfig:
    """创建 stdio 类型的测试配置。"""
    defaults = dict(
        name="test-server",
        transport="stdio",
        command="echo",
        args=["hello"],
        timeout=5,
    )
    defaults.update(overrides)
    return MCPServerConfig(**defaults)


def _sse_config(**overrides) -> MCPServerConfig:
    """创建 SSE 类型的测试配置。"""
    defaults = dict(
        name="sse-server",
        transport="sse",
        url="http://localhost:8080/sse",
        timeout=5,
    )
    defaults.update(overrides)
    return MCPServerConfig(**defaults)


def _streamable_config(**overrides) -> MCPServerConfig:
    """创建 Streamable HTTP 类型的测试配置。"""
    defaults = dict(
        name="streamable-server",
        transport="streamable_http",
        url="http://localhost:8080/mcp",
        timeout=5,
    )
    defaults.update(overrides)
    return MCPServerConfig(**defaults)


# ── Mock 辅助 ─────────────────────────────────────────────────────


def _make_mock_session() -> AsyncMock:
    """创建模拟的 ClientSession，包含 initialize / list_tools / call_tool。"""
    session = AsyncMock()
    session.initialize = AsyncMock()
    session.list_tools = AsyncMock()
    session.call_tool = AsyncMock()
    return session


class _FakeAsyncCM:
    """模拟异步上下文管理器，返回指定的 (read, write) 元组。"""

    def __init__(self, read_stream=None, write_stream=None):
        self.read_stream = read_stream or MagicMock()
        self.write_stream = write_stream or MagicMock()

    async def __aenter__(self):
        return (self.read_stream, self.write_stream)

    async def __aexit__(self, *args):
        pass


class _ReturnAsyncCM:
    """通用异步上下文管理器，直接返回传入值。"""

    def __init__(self, value):
        self._value = value

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, *args):
        pass


# ── is_connected 属性 ─────────────────────────────────────────────


class TestIsConnected:
    """测试 is_connected 属性正确反映连接状态。"""

    def test_initially_not_connected(self):
        """新建实例应为未连接状态。"""
        client = MCPClientWrapper(_stdio_config())
        assert client.is_connected is False

    @pytest.mark.asyncio
    async def test_connected_after_connect(self):
        """成功 connect() 后应为已连接状态。"""
        config = _stdio_config()
        client = MCPClientWrapper(config)
        mock_session = _make_mock_session()

        with (
            patch(
                "excelmanus.mcp.client.stdio_client",
                return_value=_FakeAsyncCM(),
            ),
            patch(
                "excelmanus.mcp.client.ClientSession",
                return_value=_FakeSessionCM(mock_session),
            ),
        ):
            await client.connect()

        assert client.is_connected is True
        await client.close()

    @pytest.mark.asyncio
    async def test_not_connected_after_close(self):
        """close() 后应恢复为未连接状态。"""
        config = _stdio_config()
        client = MCPClientWrapper(config)
        mock_session = _make_mock_session()

        with (
            patch(
                "excelmanus.mcp.client.stdio_client",
                return_value=_FakeAsyncCM(),
            ),
            patch(
                "excelmanus.mcp.client.ClientSession",
                return_value=_FakeSessionCM(mock_session),
            ),
        ):
            await client.connect()

        await client.close()
        assert client.is_connected is False


class _FakeSessionCM:
    """模拟 ClientSession 作为异步上下文管理器。"""

    def __init__(self, session: AsyncMock):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *args):
        pass


class _FailingExitStack:
    """模拟 aclose() 抛错的 ExitStack。"""

    def __init__(self, exc: BaseException):
        self._exc = exc

    async def aclose(self):
        raise self._exc


# ── 未连接时调用 ──────────────────────────────────────────────────


class TestNotConnected:
    """未连接时调用 discover_tools / call_tool 应抛出 RuntimeError。"""

    @pytest.mark.asyncio
    async def test_discover_tools_raises_when_not_connected(self):
        """未连接时 discover_tools() 应抛出 RuntimeError。"""
        client = MCPClientWrapper(_stdio_config())
        with pytest.raises(RuntimeError, match="未连接"):
            await client.discover_tools()

    @pytest.mark.asyncio
    async def test_call_tool_raises_when_not_connected(self):
        """未连接时 call_tool() 应抛出 RuntimeError。"""
        client = MCPClientWrapper(_stdio_config())
        with pytest.raises(RuntimeError, match="未连接"):
            await client.call_tool("some_tool", {"arg": "val"})


# ── close() 安全调用 ─────────────────────────────────────────────


class TestCloseSafety:
    """close() 即使未连接也不应抛出异常。"""

    @pytest.mark.asyncio
    async def test_close_without_connect(self):
        """未连接时调用 close() 不抛异常。"""
        client = MCPClientWrapper(_stdio_config())
        # 不应抛出任何异常
        await client.close()
        assert client.is_connected is False

    @pytest.mark.asyncio
    async def test_close_twice(self):
        """连续调用两次 close() 不抛异常。"""
        config = _stdio_config()
        client = MCPClientWrapper(config)
        mock_session = _make_mock_session()

        with (
            patch(
                "excelmanus.mcp.client.stdio_client",
                return_value=_FakeAsyncCM(),
            ),
            patch(
                "excelmanus.mcp.client.ClientSession",
                return_value=_FakeSessionCM(mock_session),
            ),
        ):
            await client.connect()

        await client.close()
        await client.close()  # 第二次也不应抛异常
        assert client.is_connected is False

    @pytest.mark.asyncio
    async def test_close_swallows_cancelled_error_from_exit_stack(self):
        """ExitStack 关闭抛 CancelledError 时，close() 仍应安全返回。"""
        client = MCPClientWrapper(_stdio_config())
        client._exit_stack = _FailingExitStack(asyncio.CancelledError())
        await client.close()
        assert client.is_connected is False

    def test_bind_managed_pids_normalizes_values(self):
        """bind_managed_pids() 应过滤无效 PID 并返回副本。"""
        client = MCPClientWrapper(_stdio_config())
        client.bind_managed_pids({100, -1, 0, 200})
        assert client.managed_pids == {100, 200}
        copied = client.managed_pids
        copied.add(300)
        assert client.managed_pids == {100, 200}


# ── 工具调用超时处理（Requirement 5.4）────────────────────────────


class TestCallToolTimeout:
    """当 call_tool 超时时应抛出 asyncio.TimeoutError。

    Validates: Requirement 5.4
    """

    @pytest.mark.asyncio
    async def test_call_tool_timeout_raises(self):
        """工具调用超过 config.timeout 时应抛出 TimeoutError。"""
        config = _stdio_config(timeout=1)
        client = MCPClientWrapper(config)
        mock_session = _make_mock_session()

        # 模拟 call_tool 耗时超过 timeout
        async def _slow_call(*args, **kwargs):
            await asyncio.sleep(10)

        mock_session.call_tool = _slow_call

        with (
            patch(
                "excelmanus.mcp.client.stdio_client",
                return_value=_FakeAsyncCM(),
            ),
            patch(
                "excelmanus.mcp.client.ClientSession",
                return_value=_FakeSessionCM(mock_session),
            ),
        ):
            await client.connect()

        with pytest.raises(asyncio.TimeoutError):
            await client.call_tool("slow_tool", {"arg": "val"})

        await client.close()

    @pytest.mark.asyncio
    async def test_call_tool_within_timeout_succeeds(self):
        """工具调用在 timeout 内完成应正常返回结果。"""
        config = _stdio_config(timeout=5)
        client = MCPClientWrapper(config)
        mock_session = _make_mock_session()

        # 模拟正常返回（无 isError）
        mock_result = MagicMock()
        mock_result.isError = False
        mock_result.content = []
        mock_session.call_tool = AsyncMock(return_value=mock_result)

        with (
            patch(
                "excelmanus.mcp.client.stdio_client",
                return_value=_FakeAsyncCM(),
            ),
            patch(
                "excelmanus.mcp.client.ClientSession",
                return_value=_FakeSessionCM(mock_session),
            ),
        ):
            await client.connect()

        result = await client.call_tool("fast_tool", {"x": 1})
        assert result is mock_result

        await client.close()


# ── 工具调用错误处理（Requirement 5.5）────────────────────────────


class TestCallToolError:
    """当 MCP Server 返回 isError=True 时应抛出 RuntimeError。

    Validates: Requirement 5.5
    """

    @pytest.mark.asyncio
    async def test_is_error_raises_runtime_error(self):
        """MCP Server 返回 isError=True 时应抛出 RuntimeError。"""
        config = _stdio_config(timeout=5)
        client = MCPClientWrapper(config)
        mock_session = _make_mock_session()

        # 构造 isError=True 的返回结果
        error_content = MagicMock()
        error_content.type = "text"
        error_content.text = "工具执行出错：参数无效"

        mock_result = MagicMock()
        mock_result.isError = True
        mock_result.content = [error_content]
        mock_session.call_tool = AsyncMock(return_value=mock_result)

        with (
            patch(
                "excelmanus.mcp.client.stdio_client",
                return_value=_FakeAsyncCM(),
            ),
            patch(
                "excelmanus.mcp.client.ClientSession",
                return_value=_FakeSessionCM(mock_session),
            ),
        ):
            await client.connect()

        with pytest.raises(RuntimeError, match="执行失败"):
            await client.call_tool("bad_tool", {"arg": "invalid"})

        await client.close()

    @pytest.mark.asyncio
    async def test_error_message_contains_content_text(self):
        """RuntimeError 的消息应包含 MCP Server 返回的错误文本。"""
        config = _stdio_config(timeout=5)
        client = MCPClientWrapper(config)
        mock_session = _make_mock_session()

        error_content = MagicMock()
        error_content.type = "text"
        error_content.text = "文件不存在"

        mock_result = MagicMock()
        mock_result.isError = True
        mock_result.content = [error_content]
        mock_session.call_tool = AsyncMock(return_value=mock_result)

        with (
            patch(
                "excelmanus.mcp.client.stdio_client",
                return_value=_FakeAsyncCM(),
            ),
            patch(
                "excelmanus.mcp.client.ClientSession",
                return_value=_FakeSessionCM(mock_session),
            ),
        ):
            await client.connect()

        with pytest.raises(RuntimeError, match="文件不存在"):
            await client.call_tool("read_file", {"path": "/nonexistent"})

        await client.close()

    @pytest.mark.asyncio
    async def test_error_with_empty_content(self):
        """isError=True 但 content 为空时，错误消息应包含 '未知错误'。"""
        config = _stdio_config(timeout=5)
        client = MCPClientWrapper(config)
        mock_session = _make_mock_session()

        mock_result = MagicMock()
        mock_result.isError = True
        mock_result.content = []
        mock_session.call_tool = AsyncMock(return_value=mock_result)

        with (
            patch(
                "excelmanus.mcp.client.stdio_client",
                return_value=_FakeAsyncCM(),
            ),
            patch(
                "excelmanus.mcp.client.ClientSession",
                return_value=_FakeSessionCM(mock_session),
            ),
        ):
            await client.connect()

        with pytest.raises(RuntimeError, match="未知错误"):
            await client.call_tool("err_tool", {})

        await client.close()


# ── discover_tools 测试 ──────────────────────────────────────────


class TestDiscoverTools:
    """测试 discover_tools() 正常返回工具列表。"""

    @pytest.mark.asyncio
    async def test_discover_tools_returns_tool_list(self):
        """连接后 discover_tools() 应返回 session.list_tools() 的结果。"""
        config = _stdio_config()
        client = MCPClientWrapper(config)
        mock_session = _make_mock_session()

        # 模拟 list_tools 返回
        tool_a = MagicMock(name="tool_a")
        tool_b = MagicMock(name="tool_b")
        list_result = MagicMock()
        list_result.tools = [tool_a, tool_b]
        mock_session.list_tools = AsyncMock(return_value=list_result)

        with (
            patch(
                "excelmanus.mcp.client.stdio_client",
                return_value=_FakeAsyncCM(),
            ),
            patch(
                "excelmanus.mcp.client.ClientSession",
                return_value=_FakeSessionCM(mock_session),
            ),
        ):
            await client.connect()

        tools = await client.discover_tools()
        assert len(tools) == 2
        assert tools[0] is tool_a
        assert tools[1] is tool_b

        await client.close()

    @pytest.mark.asyncio
    async def test_discover_tools_uses_cache_within_ttl(self):
        """在 TTL 窗口内重复 discover_tools() 应命中缓存。"""
        config = _stdio_config()
        client = MCPClientWrapper(config)
        mock_session = _make_mock_session()

        tool_a = MagicMock(name="tool_a")
        list_result = MagicMock()
        list_result.tools = [tool_a]
        mock_session.list_tools = AsyncMock(return_value=list_result)

        with (
            patch(
                "excelmanus.mcp.client.stdio_client",
                return_value=_FakeAsyncCM(),
            ),
            patch(
                "excelmanus.mcp.client.ClientSession",
                return_value=_FakeSessionCM(mock_session),
            ),
        ):
            await client.connect()

        first = await client.discover_tools(cache_ttl_seconds=60)
        second = await client.discover_tools(cache_ttl_seconds=60)

        assert first[0] is tool_a
        assert second[0] is tool_a
        mock_session.list_tools.assert_awaited_once()

        await client.close()

    @pytest.mark.asyncio
    async def test_discover_tools_refreshes_after_ttl_expired(self):
        """TTL 过期后应重新调用 session.list_tools()。"""
        config = _stdio_config()
        client = MCPClientWrapper(config)
        mock_session = _make_mock_session()

        tool_a = MagicMock(name="tool_a")
        tool_b = MagicMock(name="tool_b")
        list_result_a = MagicMock()
        list_result_a.tools = [tool_a]
        list_result_b = MagicMock()
        list_result_b.tools = [tool_b]
        mock_session.list_tools = AsyncMock(side_effect=[list_result_a, list_result_b])

        with (
            patch(
                "excelmanus.mcp.client.stdio_client",
                return_value=_FakeAsyncCM(),
            ),
            patch(
                "excelmanus.mcp.client.ClientSession",
                return_value=_FakeSessionCM(mock_session),
            ),
        ):
            await client.connect()

        with patch(
            "excelmanus.mcp.client.time.monotonic",
            side_effect=[100.0, 103.0, 120.0],
        ):
            first = await client.discover_tools(cache_ttl_seconds=10)
            second = await client.discover_tools(cache_ttl_seconds=10)
            third = await client.discover_tools(cache_ttl_seconds=10)

        assert first[0] is tool_a
        assert second[0] is tool_a
        assert third[0] is tool_b
        assert mock_session.list_tools.await_count == 2

        await client.close()

    @pytest.mark.asyncio
    async def test_discover_tools_force_refresh_bypasses_cache(self):
        """force_refresh=True 时应跳过缓存直接刷新。"""
        config = _stdio_config()
        client = MCPClientWrapper(config)
        mock_session = _make_mock_session()

        tool_a = MagicMock(name="tool_a")
        tool_b = MagicMock(name="tool_b")
        list_result_a = MagicMock()
        list_result_a.tools = [tool_a]
        list_result_b = MagicMock()
        list_result_b.tools = [tool_b]
        mock_session.list_tools = AsyncMock(side_effect=[list_result_a, list_result_b])

        with (
            patch(
                "excelmanus.mcp.client.stdio_client",
                return_value=_FakeAsyncCM(),
            ),
            patch(
                "excelmanus.mcp.client.ClientSession",
                return_value=_FakeSessionCM(mock_session),
            ),
        ):
            await client.connect()

        first = await client.discover_tools(cache_ttl_seconds=60)
        second = await client.discover_tools(
            cache_ttl_seconds=60,
            force_refresh=True,
        )

        assert first[0] is tool_a
        assert second[0] is tool_b
        assert mock_session.list_tools.await_count == 2

        await client.close()


# ── SSE 传输方式连接测试 ─────────────────────────────────────────


class TestSSEConnect:
    """测试 SSE 传输方式的连接。"""

    @pytest.mark.asyncio
    async def test_sse_connect_uses_sse_client(self):
        """SSE 配置应使用 sse_client 建立连接。"""
        config = _sse_config()
        client = MCPClientWrapper(config)
        mock_session = _make_mock_session()

        with (
            patch(
                "excelmanus.mcp.client.sse_client",
                return_value=_FakeAsyncCM(),
            ) as mock_sse,
            patch(
                "excelmanus.mcp.client.ClientSession",
                return_value=_FakeSessionCM(mock_session),
            ),
        ):
            await client.connect()
            mock_sse.assert_called_once_with(config.url, headers=None)

        assert client.is_connected is True
        await client.close()


class TestStreamableHTTPConnect:
    """测试 Streamable HTTP 传输方式的连接。"""

    @pytest.mark.asyncio
    async def test_streamable_http_connect_uses_streamable_client(self):
        """Streamable 配置应使用 streamable_http_client 建立连接。"""
        config = _streamable_config(
            headers={"Authorization": "Bearer token"},
            timeout=9,
        )
        client = MCPClientWrapper(config)
        mock_session = _make_mock_session()
        fake_http_client = MagicMock()

        with (
            patch(
                "excelmanus.mcp.client.httpx.AsyncClient",
                return_value=_ReturnAsyncCM(fake_http_client),
            ) as mock_async_client,
            patch(
                "excelmanus.mcp.client.streamable_http_client",
                return_value=_ReturnAsyncCM((MagicMock(), MagicMock(), MagicMock())),
            ) as mock_streamable,
            patch(
                "excelmanus.mcp.client.ClientSession",
                return_value=_FakeSessionCM(mock_session),
            ),
        ):
            await client.connect()
            mock_async_client.assert_called_once_with(
                headers=config.headers,
                timeout=config.timeout,
            )
            mock_streamable.assert_called_once_with(
                config.url,
                http_client=fake_http_client,
            )

        assert client.is_connected is True
        await client.close()

    @pytest.mark.asyncio
    async def test_streamable_http_unsupported_raises_runtime_error(self):
        """SDK 不支持 streamable_http 时应抛出可读错误。"""
        config = _streamable_config()
        client = MCPClientWrapper(config)
        with patch("excelmanus.mcp.client.streamable_http_client", None):
            with pytest.raises(RuntimeError, match="不支持 streamable_http"):
                await client.connect()


# ── _extract_error_text 辅助函数测试 ─────────────────────────────


class TestExtractErrorText:
    """测试 _extract_error_text 辅助函数。"""

    def test_single_text_content(self):
        """单个 text 类型 content 应返回其文本。"""
        item = MagicMock()
        item.type = "text"
        item.text = "出错了"
        result = MagicMock()
        result.content = [item]
        assert _extract_error_text(result) == "出错了"

    def test_multiple_text_contents(self):
        """多个 text 类型 content 应用换行拼接。"""
        item1 = MagicMock()
        item1.type = "text"
        item1.text = "错误1"
        item2 = MagicMock()
        item2.type = "text"
        item2.text = "错误2"
        result = MagicMock()
        result.content = [item1, item2]
        assert _extract_error_text(result) == "错误1\n错误2"

    def test_no_text_content(self):
        """无 text 类型 content 应返回 '未知错误'。"""
        item = MagicMock()
        item.type = "image"
        item.text = ""
        result = MagicMock()
        result.content = [item]
        assert _extract_error_text(result) == "未知错误"

    def test_empty_content_list(self):
        """空 content 列表应返回 '未知错误'。"""
        result = MagicMock()
        result.content = []
        assert _extract_error_text(result) == "未知错误"
