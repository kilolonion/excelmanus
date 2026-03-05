"""Exa 搜索安装健壮性测试。

覆盖：
1. api.py 共享 MCPManager 传递 app_config（根因修复）
2. _merge_builtin_configs 无 app_config 时的防御性 WARNING
3. _merge_builtin_configs 记录 _builtin_server_names
4. _retry_failed_builtin_servers 指数退避重试
5. 搜索路由诊断日志（无搜索 MCP 工具时 WARNING）
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pytest

from excelmanus.mcp.config import MCPServerConfig
from excelmanus.mcp.manager import MCPManager


# ── 辅助 ────────────────────────────────────────────────────


@dataclass
class _FakeConfig:
    """最小化的 Config 替身。"""

    exa_search_enabled: bool = True
    search_default_provider: str = "exa"
    exa_api_key: str | None = None
    tavily_api_key: str | None = None
    brave_api_key: str | None = None
    workspace_root: str = "."


def _make_manager(*, app_config=None) -> MCPManager:
    return MCPManager(workspace_root=".", app_config=app_config)


# ── Fix 1: api.py 共享 MCPManager 传递 app_config ──────────


class TestApiSharedManagerAppConfig:
    """验证 api.py 中共享 MCPManager 创建时传递 app_config。"""

    def test_api_startup_passes_app_config(self):
        """api.py 的 MCPManager 构造应包含 app_config=_config。"""
        import ast
        import inspect

        import excelmanus.api as api_module

        source = inspect.getsource(api_module)
        tree = ast.parse(source)

        # 查找 MCPManager(...) 调用
        found_calls = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                # 匹配 MCPManager(...)
                if isinstance(func, ast.Name) and func.id == "MCPManager":
                    found_calls.append(node)
                elif isinstance(func, ast.Attribute) and func.attr == "MCPManager":
                    found_calls.append(node)

        # 至少有一个 MCPManager 调用
        assert found_calls, "api.py 中未找到 MCPManager 调用"

        # 检查是否有 app_config 关键字参数
        has_app_config = False
        for call in found_calls:
            for kw in call.keywords:
                if kw.arg == "app_config":
                    has_app_config = True
                    break

        assert has_app_config, (
            "api.py 中的 MCPManager 调用缺少 app_config 参数，"
            "这会导致内置 MCP Server（如 Exa 搜索）不被注入"
        )


# ── Fix 2: _merge_builtin_configs 防御性 WARNING ───────────


class TestMergeBuiltinDefensiveWarning:
    """无 app_config 时应输出 WARNING 日志。"""

    def test_no_app_config_logs_warning(self, caplog):
        """app_config=None 时 _merge_builtin_configs 应记录 WARNING。"""
        mgr = _make_manager(app_config=None)
        user_cfgs = [
            MCPServerConfig(name="test", transport="stdio", command="node"),
        ]

        with caplog.at_level(logging.WARNING, logger="excelmanus.mcp.manager"):
            result = mgr._merge_builtin_configs(user_cfgs)

        assert result is user_cfgs
        assert any("app_config" in record.message for record in caplog.records), (
            "无 app_config 时未输出 WARNING 日志"
        )

    def test_with_app_config_no_warning(self, caplog):
        """有 app_config 时不应有关于 app_config 的 WARNING。"""
        mgr = _make_manager(app_config=_FakeConfig(exa_search_enabled=True))
        user_cfgs = []

        with caplog.at_level(logging.WARNING, logger="excelmanus.mcp.manager"):
            mgr._merge_builtin_configs(user_cfgs)

        app_config_warnings = [
            r for r in caplog.records
            if "app_config" in r.message and r.levelno >= logging.WARNING
        ]
        assert not app_config_warnings


# ── Fix 3: _builtin_server_names 追踪 ─────────────────────


class TestBuiltinServerNamesTracking:
    """_merge_builtin_configs 应记录注入的内置 server 名称。"""

    def test_builtin_names_recorded(self):
        """注入 Exa 后 _builtin_server_names 应包含 'exa'。"""
        mgr = _make_manager(app_config=_FakeConfig(exa_search_enabled=True))
        mgr._merge_builtin_configs([])
        assert "exa" in mgr._builtin_server_names

    def test_builtin_names_empty_when_disabled(self):
        """禁用 Exa 时 _builtin_server_names 应为空。"""
        mgr = _make_manager(app_config=_FakeConfig(exa_search_enabled=False))
        mgr._merge_builtin_configs([])
        assert len(mgr._builtin_server_names) == 0

    def test_builtin_names_not_recorded_when_user_overrides(self):
        """用户覆盖内置 server 时，不应记录到 _builtin_server_names。"""
        mgr = _make_manager(app_config=_FakeConfig(exa_search_enabled=True))
        user_cfgs = [
            MCPServerConfig(name="exa", transport="sse", url="https://custom.example.com"),
        ]
        mgr._merge_builtin_configs(user_cfgs)
        assert "exa" not in mgr._builtin_server_names

    def test_no_app_config_builtin_names_empty(self):
        """无 app_config 时 _builtin_server_names 应为空。"""
        mgr = _make_manager(app_config=None)
        mgr._merge_builtin_configs([])
        assert len(mgr._builtin_server_names) == 0


# ── Fix 4: _retry_failed_builtin_servers ───────────────────


class TestRetryFailedBuiltinServers:
    """测试内置 MCP Server 连接失败的后台重试机制。"""

    @pytest.mark.asyncio
    async def test_retry_recovers_on_second_attempt(self):
        """第二次重试成功时应注册工具并停止重试。"""
        mgr = _make_manager(app_config=_FakeConfig(exa_search_enabled=True))

        cfg = MCPServerConfig(
            name="exa", transport="sse", url="https://mcp.exa.ai/sse",
            scope="search",
        )

        # 模拟 server state 为 connect_failed
        from excelmanus.mcp.manager import _ServerRuntimeState
        mgr._server_states["exa"] = _ServerRuntimeState(
            name="exa", transport="sse", status="connect_failed",
        )

        registry = MagicMock()
        registry.get_tool_names.return_value = []

        # 第一次重试失败，第二次成功
        call_count = 0
        mock_tool_def = MagicMock()
        mock_tool_def.name = "mcp_exa_web_search_exa"

        async def mock_connect(cfg, registry, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [], []  # 第一次失败
            # 第二次成功 — 更新状态
            mgr._server_states["exa"].status = "ready"
            return [mock_tool_def], ["mcp_exa_web_search_exa"]

        mgr._connect_and_register_server = mock_connect

        await mgr._retry_failed_builtin_servers(
            [cfg], registry, max_retries=3, base_delay=0.01,
        )

        assert call_count == 2
        assert mgr._server_states["exa"].status == "ready"
        registry.register_tools.assert_called_once()
        assert "mcp_exa_web_search_exa" in mgr._auto_approved_tools

    @pytest.mark.asyncio
    async def test_retry_exhausted_logs_warning(self, caplog):
        """重试全部耗尽时应记录 WARNING。"""
        mgr = _make_manager(app_config=_FakeConfig(exa_search_enabled=True))

        cfg = MCPServerConfig(
            name="exa", transport="sse", url="https://mcp.exa.ai/sse",
            scope="search",
        )

        from excelmanus.mcp.manager import _ServerRuntimeState
        mgr._server_states["exa"] = _ServerRuntimeState(
            name="exa", transport="sse", status="connect_failed",
        )

        registry = MagicMock()
        registry.get_tool_names.return_value = []

        async def always_fail(cfg, registry, **kwargs):
            return [], []

        mgr._connect_and_register_server = always_fail

        with caplog.at_level(logging.WARNING, logger="excelmanus.mcp.manager"):
            await mgr._retry_failed_builtin_servers(
                [cfg], registry, max_retries=2, base_delay=0.01,
            )

        assert any("重试全部耗尽" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_retry_skips_already_recovered(self):
        """如果所有 server 在重试前已恢复，应立即返回。"""
        mgr = _make_manager(app_config=_FakeConfig(exa_search_enabled=True))

        cfg = MCPServerConfig(
            name="exa", transport="sse", url="https://mcp.exa.ai/sse",
            scope="search",
        )

        from excelmanus.mcp.manager import _ServerRuntimeState
        mgr._server_states["exa"] = _ServerRuntimeState(
            name="exa", transport="sse", status="ready",  # 已恢复
        )

        registry = MagicMock()
        connect_called = False

        async def should_not_be_called(cfg, registry, **kwargs):
            nonlocal connect_called
            connect_called = True
            return [], []

        mgr._connect_and_register_server = should_not_be_called

        await mgr._retry_failed_builtin_servers(
            [cfg], registry, max_retries=3, base_delay=0.01,
        )

        assert not connect_called

    @pytest.mark.asyncio
    async def test_retry_first_attempt_success(self):
        """第一次重试即成功。"""
        mgr = _make_manager(app_config=_FakeConfig(exa_search_enabled=True))

        cfg = MCPServerConfig(
            name="exa", transport="sse", url="https://mcp.exa.ai/sse",
            scope="search",
        )

        from excelmanus.mcp.manager import _ServerRuntimeState
        mgr._server_states["exa"] = _ServerRuntimeState(
            name="exa", transport="sse", status="connect_failed",
        )

        registry = MagicMock()
        registry.get_tool_names.return_value = []
        mock_tool = MagicMock()
        mock_tool.name = "mcp_exa_search"

        async def succeed_immediately(cfg, registry, **kwargs):
            mgr._server_states["exa"].status = "ready"
            return [mock_tool], ["mcp_exa_search"]

        mgr._connect_and_register_server = succeed_immediately

        await mgr._retry_failed_builtin_servers(
            [cfg], registry, max_retries=3, base_delay=0.01,
        )

        assert mgr._server_states["exa"].status == "ready"
        registry.register_tools.assert_called_once()


# ── Fix 5: 搜索路由诊断日志 ────────────────────────────────


class TestSearchRouteDiagnosticLog:
    """搜索路由激活但无搜索 MCP 工具时应输出 WARNING。"""

    def _make_engine_and_builder(self, *, mcp_scopes=None, domain_tools=None):
        """构建带 mock 的 engine 和 MetaToolBuilder。"""
        engine = MagicMock()
        engine._mcp_manager = MagicMock()
        engine._mcp_manager.tool_scopes = mcp_scopes or {}

        # 设置 registry
        schemas = domain_tools or []
        engine._registry = MagicMock()
        engine._registry.get_tiered_schemas.return_value = schemas

        from excelmanus.engine_core.meta_tools import MetaToolBuilder
        builder = MetaToolBuilder(engine)
        builder.build_meta_tools = MagicMock(return_value=[])
        return builder

    def test_search_route_no_search_tools_warns(self, caplog):
        """搜索路由激活但无搜索工具时应 WARNING。"""
        builder = self._make_engine_and_builder(
            mcp_scopes={},  # 无任何 MCP 工具
            domain_tools=[
                {"type": "function", "function": {"name": "memory_read_topic"}},
            ],
        )

        with caplog.at_level(logging.WARNING, logger="excelmanus.engine_core.meta_tools"):
            builder.build_v5_tools_impl(route_tool_tags=("search",))

        assert any(
            "搜索路由已激活但无可用的搜索 MCP 工具" in r.message
            for r in caplog.records
        ), "缺少搜索工具诊断 WARNING"

    def test_search_route_with_exa_no_warning(self, caplog):
        """搜索路由激活且 Exa 可用时不应 WARNING。"""
        builder = self._make_engine_and_builder(
            mcp_scopes={"mcp_exa_web_search_exa": "search"},
            domain_tools=[
                {"type": "function", "function": {"name": "memory_read_topic"}},
                {"type": "function", "function": {"name": "mcp_exa_web_search_exa"}},
            ],
        )

        with caplog.at_level(logging.WARNING, logger="excelmanus.engine_core.meta_tools"):
            builder.build_v5_tools_impl(route_tool_tags=("search",))

        search_warnings = [
            r for r in caplog.records
            if "搜索路由已激活但无可用的搜索 MCP 工具" in r.message
        ]
        assert not search_warnings

    def test_non_search_route_no_diagnostic(self, caplog):
        """非搜索路由标签不触发搜索诊断。"""
        builder = self._make_engine_and_builder(
            mcp_scopes={},
            domain_tools=[
                {"type": "function", "function": {"name": "read_excel"}},
            ],
        )

        with caplog.at_level(logging.WARNING, logger="excelmanus.engine_core.meta_tools"):
            builder.build_v5_tools_impl(route_tool_tags=("data_read",))

        search_warnings = [
            r for r in caplog.records
            if "搜索路由已激活" in r.message
        ]
        assert not search_warnings


# ── 集成：initialize 流程中的内置重试调度 ──────────────────


class TestInitializeBuiltinRetryScheduling:
    """验证 initialize 完成后对失败的内置 server 启动重试任务。"""

    @pytest.mark.asyncio
    async def test_failed_builtin_triggers_retry_task(self):
        """内置 server 连接失败后应启动重试后台任务。"""
        import asyncio

        mgr = _make_manager(app_config=_FakeConfig(exa_search_enabled=True))
        registry = MagicMock()
        registry.get_tool_names.return_value = []
        registry.register_tools = MagicMock()

        # Mock MCPConfigLoader.load 返回空（只有内置）
        with patch("excelmanus.mcp.config.MCPConfigLoader") as mock_loader:
            mock_loader.load.return_value = []

            # Mock try_resolve_from_cache 直通
            with patch("excelmanus.mcp.npx_cache.try_resolve_from_cache", side_effect=lambda c: c):
                # Mock _connect_and_register_server 模拟 Exa 连接失败
                async def fail_exa(cfg, registry, **kwargs):
                    from excelmanus.mcp.manager import _ServerRuntimeState
                    mgr._server_states[cfg.name] = _ServerRuntimeState(
                        name=cfg.name, transport=cfg.transport,
                        status="connect_failed",
                    )
                    return [], []

                mgr._connect_and_register_server = fail_exa

                # Mock _retry_failed_builtin_servers 以避免真实重试
                retry_called_with = []

                async def capture_retry(configs, reg, **kwargs):
                    retry_called_with.extend(c.name for c in configs)

                mgr._retry_failed_builtin_servers = capture_retry

                await mgr.initialize(registry)

                # 等待后台任务完成（create_task 调度的重试任务）
                if mgr._background_tasks:
                    await asyncio.gather(*mgr._background_tasks, return_exceptions=True)

        assert "exa" in retry_called_with, (
            "Exa 连接失败后应触发 _retry_failed_builtin_servers"
        )

    @pytest.mark.asyncio
    async def test_successful_builtin_no_retry_task(self):
        """内置 server 连接成功时不应启动重试任务。"""
        mgr = _make_manager(app_config=_FakeConfig(exa_search_enabled=True))
        registry = MagicMock()
        registry.get_tool_names.return_value = []
        registry.register_tools = MagicMock()

        with patch("excelmanus.mcp.config.MCPConfigLoader") as mock_loader:
            mock_loader.load.return_value = []

            with patch("excelmanus.mcp.npx_cache.try_resolve_from_cache", side_effect=lambda c: c):
                mock_tool = MagicMock()
                mock_tool.name = "mcp_exa_search"

                async def succeed_exa(cfg, registry, **kwargs):
                    from excelmanus.mcp.manager import _ServerRuntimeState
                    mgr._server_states[cfg.name] = _ServerRuntimeState(
                        name=cfg.name, transport=cfg.transport,
                        status="ready",
                    )
                    return [mock_tool], []

                mgr._connect_and_register_server = succeed_exa

                retry_called = False

                async def should_not_retry(*args, **kwargs):
                    nonlocal retry_called
                    retry_called = True

                mgr._retry_failed_builtin_servers = should_not_retry

                await mgr.initialize(registry)

        assert not retry_called
