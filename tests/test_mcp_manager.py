"""MCPManager 工具名前缀函数单元测试。"""

from __future__ import annotations

import pytest

from excelmanus.mcp.manager import (
    _normalize_server_name,
    _prefix_registry,
    add_tool_prefix,
    format_tool_result,
    parse_tool_prefix,
)


# ── 辅助：每个测试前清空注册表 ────────────────────────────────────


@pytest.fixture(autouse=True)
def _clear_registry():
    """每个测试前后清空模块级注册表，避免测试间干扰。"""
    _prefix_registry.clear()
    yield
    _prefix_registry.clear()


# ── _normalize_server_name ────────────────────────────────────────


class TestNormalizeServerName:
    """测试 server_name 规范化。"""

    def test_no_dash(self):
        assert _normalize_server_name("filesystem") == "filesystem"

    def test_single_dash(self):
        assert _normalize_server_name("web-search") == "web_search"

    def test_multiple_dashes(self):
        assert _normalize_server_name("my-cool-server") == "my_cool_server"

    def test_already_underscore(self):
        assert _normalize_server_name("my_server") == "my_server"

    def test_mixed(self):
        assert _normalize_server_name("my-server_v2") == "my_server_v2"


# ── add_tool_prefix ──────────────────────────────────────────────


class TestAddToolPrefix:
    """测试工具名前缀添加。"""

    def test_basic_format(self):
        """设计文档示例：filesystem + read_file → mcp_filesystem_read_file"""
        result = add_tool_prefix("filesystem", "read_file")
        assert result == "mcp_filesystem_read_file"

    def test_dash_in_server_name(self):
        """设计文档示例：web-search + search → mcp_web_search_search"""
        result = add_tool_prefix("web-search", "search")
        assert result == "mcp_web_search_search"

    def test_registers_in_registry(self):
        """add_tool_prefix 应将映射写入注册表。"""
        prefixed = add_tool_prefix("my-server", "do_stuff")
        assert prefixed in _prefix_registry
        assert _prefix_registry[prefixed] == ("my_server", "do_stuff")

    def test_prefix_starts_with_mcp(self):
        result = add_tool_prefix("srv", "tool")
        assert result.startswith("mcp_")


# ── parse_tool_prefix ────────────────────────────────────────────


class TestParseToolPrefix:
    """测试工具名前缀还原。"""

    def test_round_trip_simple(self):
        """简单 server_name 的 round-trip。"""
        prefixed = add_tool_prefix("filesystem", "read_file")
        server, tool = parse_tool_prefix(prefixed)
        assert server == "filesystem"
        assert tool == "read_file"

    def test_round_trip_with_dash(self):
        """含 `-` 的 server_name 的 round-trip（通过注册表精确还原）。"""
        prefixed = add_tool_prefix("web-search", "search")
        server, tool = parse_tool_prefix(prefixed)
        assert server == "web_search"
        assert tool == "search"

    def test_round_trip_complex_tool_name(self):
        """tool_name 包含多个 `_` 的 round-trip。"""
        prefixed = add_tool_prefix("db-server", "get_user_by_id")
        server, tool = parse_tool_prefix(prefixed)
        assert server == "db_server"
        assert tool == "get_user_by_id"

    def test_fallback_without_registry(self):
        """注册表中不存在时，回退到字符串切分。"""
        # 不经过 add_tool_prefix，直接解析
        server, tool = parse_tool_prefix("mcp_myserver_mytool")
        assert server == "myserver"
        assert tool == "mytool"

    def test_fallback_tool_with_underscores(self):
        """回退模式下，tool_name 中的 `_` 被保留。"""
        server, tool = parse_tool_prefix("mcp_srv_get_all_items")
        assert server == "srv"
        assert tool == "get_all_items"

    def test_invalid_no_prefix(self):
        """不以 mcp_ 开头应抛出 ValueError。"""
        with pytest.raises(ValueError, match="不以 'mcp_' 开头"):
            parse_tool_prefix("some_random_name")

    def test_invalid_no_tool_name(self):
        """只有 mcp_ 和 server，没有 tool_name 部分。"""
        with pytest.raises(ValueError, match="格式不合法"):
            parse_tool_prefix("mcp_serveronly")

    def test_invalid_empty_after_prefix(self):
        """mcp_ 后面为空。"""
        with pytest.raises(ValueError, match="格式不合法"):
            parse_tool_prefix("mcp_")


# ══════════════════════════════════════════════════════════════════
# MCPManager 单元测试
# ══════════════════════════════════════════════════════════════════

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from excelmanus.mcp.config import MCPServerConfig
from excelmanus.mcp.manager import MCPManager, make_tool_def
from excelmanus.tools.registry import ToolDef, ToolRegistry


# ── 辅助工厂 ──────────────────────────────────────────────────────


def _make_config(name: str = "test-server", **overrides) -> MCPServerConfig:
    """快速创建 MCPServerConfig 测试实例。"""
    defaults = dict(
        name=name,
        transport="stdio",
        command="echo",
        args=[],
        env={},
        timeout=30,
    )
    defaults.update(overrides)
    return MCPServerConfig(**defaults)


def _make_mcp_tool(name: str = "remote_tool", description: str = "描述") -> SimpleNamespace:
    """创建模拟的 MCP 工具定义对象（duck typing）。"""
    return SimpleNamespace(
        name=name,
        description=description,
        inputSchema={"type": "object", "properties": {}},
    )


def _make_mock_client(
    config: MCPServerConfig | None = None,
    tools: list | None = None,
    connect_error: Exception | None = None,
    close_error: Exception | None = None,
) -> AsyncMock:
    """创建模拟的 MCPClientWrapper。"""
    client = AsyncMock()
    client._config = config or _make_config()
    if connect_error:
        client.connect.side_effect = connect_error
    if close_error:
        client.close.side_effect = close_error
    client.discover_tools.return_value = tools or []
    client.bind_managed_pids = MagicMock()
    client.managed_pids = set()
    return client


# ── 工具名冲突处理（Requirement 4.3）────────────────────────────


class TestMCPManagerToolConflict:
    """测试 MCPManager 工具名冲突处理逻辑。"""

    @pytest.mark.asyncio
    async def test_skip_tool_conflicting_with_builtin(self, caplog):
        """远程工具名（含前缀）与已注册的内置工具冲突时，应跳过并记录 WARNING。"""
        registry = ToolRegistry()
        # 预先注册一个"内置工具"，名称恰好与远程工具前缀名相同
        builtin_tool = ToolDef(
            name="mcp_test_server_remote_tool",
            description="内置工具",
            input_schema={},
            func=lambda: None,
        )
        registry.register_tool(builtin_tool)

        cfg = _make_config(name="test-server")
        mock_client = _make_mock_client(
            config=cfg,
            tools=[_make_mcp_tool("remote_tool")],
        )

        manager = MCPManager()
        with (
            patch(
                "excelmanus.mcp.config.MCPConfigLoader"
            ) as mock_loader_cls,
            patch(
                "excelmanus.mcp.manager.MCPClientWrapper",
                return_value=mock_client,
            ),
            caplog.at_level(logging.WARNING, logger="excelmanus.mcp.manager"),
        ):
            mock_loader_cls.load.return_value = [cfg]
            await manager.initialize(registry)

        # 验证：冲突工具被跳过，WARNING 日志包含"冲突"
        assert any("冲突" in r.message for r in caplog.records if r.levelno == logging.WARNING)
        # registry 中仍然只有原来的内置工具
        assert registry.get_tool_names() == ["mcp_test_server_remote_tool"]

    @pytest.mark.asyncio
    async def test_skip_tool_conflicting_between_servers(self, caplog):
        """两个不同 MCP Server 的工具名冲突时，应跳过后者并记录 WARNING。"""
        registry = ToolRegistry()

        cfg_a = _make_config(name="server-a")
        cfg_b = _make_config(name="server-a")  # 同名 server → 同前缀 → 冲突

        # 两个 server 都提供同名工具
        tool = _make_mcp_tool("do_stuff")
        mock_client_a = _make_mock_client(config=cfg_a, tools=[tool])
        mock_client_b = _make_mock_client(config=cfg_b, tools=[tool])

        # MCPClientWrapper 按调用顺序返回不同 mock
        clients_iter = iter([mock_client_a, mock_client_b])

        manager = MCPManager()
        with (
            patch(
                "excelmanus.mcp.config.MCPConfigLoader"
            ) as mock_loader_cls,
            patch(
                "excelmanus.mcp.manager.MCPClientWrapper",
                side_effect=lambda cfg: next(clients_iter),
            ),
            caplog.at_level(logging.WARNING, logger="excelmanus.mcp.manager"),
        ):
            mock_loader_cls.load.return_value = [cfg_a, cfg_b]
            await manager.initialize(registry)

        # 验证：第二个 server 的同名工具被跳过
        conflict_warnings = [
            r for r in caplog.records
            if r.levelno == logging.WARNING and "冲突" in r.message
        ]
        assert len(conflict_warnings) >= 1
        # 只注册了一个工具
        tool_names = registry.get_tool_names()
        assert tool_names.count("mcp_server_a_do_stuff") == 1


# ── 日志输出（Requirements 7.1, 7.3）────────────────────────────


class TestMCPManagerLogging:
    """测试 MCPManager 日志输出。"""

    @pytest.mark.asyncio
    async def test_info_log_on_success(self, caplog):
        """连接成功时应记录 INFO 日志，包含 Server 数量和工具数量。"""
        registry = ToolRegistry()
        cfg = _make_config(name="my-server")
        mock_client = _make_mock_client(
            config=cfg,
            tools=[_make_mcp_tool("tool_a"), _make_mcp_tool("tool_b")],
        )

        manager = MCPManager()
        with (
            patch(
                "excelmanus.mcp.config.MCPConfigLoader"
            ) as mock_loader_cls,
            patch(
                "excelmanus.mcp.manager.MCPClientWrapper",
                return_value=mock_client,
            ),
            caplog.at_level(logging.INFO, logger="excelmanus.mcp.manager"),
        ):
            mock_loader_cls.load.return_value = [cfg]
            await manager.initialize(registry)

        # 验证 INFO 日志包含 Server 数量和工具数量
        info_records = [r for r in caplog.records if r.levelno == logging.INFO]
        assert any("1" in r.message and "2" in r.message for r in info_records), (
            f"期望 INFO 日志包含 Server 数量(1)和工具数量(2)，"
            f"实际日志: {[r.message for r in info_records]}"
        )

    @pytest.mark.asyncio
    async def test_error_log_on_connection_failure(self, caplog):
        """连接失败时应记录 ERROR 日志。"""
        registry = ToolRegistry()
        cfg = _make_config(name="bad-server")
        mock_client = _make_mock_client(
            config=cfg,
            connect_error=ConnectionError("连接被拒绝"),
        )

        manager = MCPManager()
        with (
            patch(
                "excelmanus.mcp.config.MCPConfigLoader"
            ) as mock_loader_cls,
            patch(
                "excelmanus.mcp.manager.MCPClientWrapper",
                return_value=mock_client,
            ),
            caplog.at_level(logging.ERROR, logger="excelmanus.mcp.manager"),
        ):
            mock_loader_cls.load.return_value = [cfg]
            await manager.initialize(registry)

        # 验证 ERROR 日志
        error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(error_records) >= 1
        assert any("bad-server" in r.message for r in error_records)


class TestMCPManagerStateSemantics:
    """测试 MCP 连接状态语义。"""

    @pytest.mark.asyncio
    async def test_discover_failure_not_in_connected_servers(self):
        registry = ToolRegistry()
        cfg = _make_config(name="discover-bad")
        mock_client = _make_mock_client(config=cfg)
        mock_client.discover_tools.side_effect = RuntimeError("list failed")

        manager = MCPManager()
        with (
            patch("excelmanus.mcp.config.MCPConfigLoader") as mock_loader_cls,
            patch("excelmanus.mcp.manager.MCPClientWrapper", return_value=mock_client),
        ):
            mock_loader_cls.load.return_value = [cfg]
            await manager.initialize(registry)

        assert manager.connected_servers == []
        info = manager.get_server_info()
        assert len(info) == 1
        assert info[0]["status"] == "discover_failed"
        assert "list failed" in str(info[0]["last_error"])
        mock_client.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_connect_failure_marked_as_connect_failed(self):
        registry = ToolRegistry()
        cfg = _make_config(name="connect-bad")
        mock_client = _make_mock_client(
            config=cfg,
            connect_error=RuntimeError("connect failed"),
        )

        manager = MCPManager()
        with (
            patch("excelmanus.mcp.config.MCPConfigLoader") as mock_loader_cls,
            patch("excelmanus.mcp.manager.MCPClientWrapper", return_value=mock_client),
        ):
            mock_loader_cls.load.return_value = [cfg]
            await manager.initialize(registry)

        assert manager.connected_servers == []
        info = manager.get_server_info()
        assert len(info) == 1
        assert info[0]["status"] == "connect_failed"
        assert "connect failed" in str(info[0]["last_error"])


class TestFormatToolResult:
    """测试 MCP 工具结果格式化。"""

    def test_prefers_text_content(self):
        result = SimpleNamespace(
            content=[SimpleNamespace(type="text", text="hello")],
            structuredContent={"ok": True},
        )
        assert format_tool_result(result) == "hello"

    def test_falls_back_to_structured_content(self):
        result = SimpleNamespace(content=[], structuredContent={"ok": True, "n": 1})
        text = format_tool_result(result)
        assert '"ok": true' in text
        assert '"n": 1' in text

    def test_formats_resource_content(self):
        resource = SimpleNamespace(uri="file:///tmp/a.txt", mimeType="text/plain")
        result = SimpleNamespace(
            content=[SimpleNamespace(type="resource", resource=resource)],
            structuredContent=None,
        )
        assert "resource uri=file:///tmp/a.txt" in format_tool_result(result)


# ── 资源清理（Requirement 2.5）───────────────────────────────────


class TestMCPManagerShutdown:
    """测试 MCPManager 资源清理。"""

    @pytest.mark.asyncio
    async def test_shutdown_calls_close_on_all_clients(self):
        """shutdown() 应调用所有 client 的 close()。"""
        manager = MCPManager()
        client_a = _make_mock_client()
        client_b = _make_mock_client()
        # 直接注入 _clients 模拟已连接状态
        manager._clients = {"server-a": client_a, "server-b": client_b}

        await manager.shutdown()

        client_a.close.assert_awaited_once()
        client_b.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_shutdown_clears_connected_servers(self):
        """shutdown() 后 connected_servers 应为空。"""
        manager = MCPManager()
        manager._clients = {"server-a": _make_mock_client()}

        assert len(manager.connected_servers) == 1
        await manager.shutdown()
        assert manager.connected_servers == []

    @pytest.mark.asyncio
    async def test_shutdown_tolerates_close_failure(self, caplog):
        """shutdown() 中某个 client.close() 失败不影响其他 client 的关闭。"""
        manager = MCPManager()
        client_ok = _make_mock_client()
        client_bad = _make_mock_client(close_error=RuntimeError("关闭失败"))

        # client_bad 排在前面，验证不影响 client_ok
        manager._clients = {"bad": client_bad, "ok": client_ok}

        with caplog.at_level(logging.WARNING, logger="excelmanus.mcp.manager"):
            await manager.shutdown()

        # 两个 client 的 close 都被调用
        client_bad.close.assert_awaited_once()
        client_ok.close.assert_awaited_once()
        # connected_servers 已清空
        assert manager.connected_servers == []
        # 记录了 WARNING 日志
        assert any("关闭" in r.message and "bad" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_shutdown_tolerates_cancelled_error(self, caplog):
        """shutdown() 遇到 CancelledError 也应继续并清空连接。"""
        manager = MCPManager()
        client_bad = _make_mock_client()
        client_bad.close.side_effect = asyncio.CancelledError()
        client_ok = _make_mock_client()

        manager._clients = {"bad": client_bad, "ok": client_ok}

        with caplog.at_level(logging.WARNING, logger="excelmanus.mcp.manager"):
            await manager.shutdown()

        client_bad.close.assert_awaited_once()
        client_ok.close.assert_awaited_once()
        assert manager.connected_servers == []
        assert any("关闭 MCP Server 'bad'" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_shutdown_runs_workspace_cleanup_for_managed_pids(self):
        """shutdown() 应将受管 PID 传给工作区兜底清理函数。"""
        manager = MCPManager(workspace_root="/tmp/workspace")
        client = _make_mock_client()
        client.managed_pids = {101, 102}
        manager._clients = {"srv": client}
        manager._managed_workspace_pids = {100}

        with patch(
            "excelmanus.mcp.manager.terminate_workspace_mcp_processes",
            return_value=set(),
        ) as mock_cleanup:
            await manager.shutdown()

        mock_cleanup.assert_called_once()
        kwargs = mock_cleanup.call_args.kwargs
        assert kwargs["workspace_root"] == "/tmp/workspace"
        assert kwargs["candidate_pids"] == {100, 101, 102}
