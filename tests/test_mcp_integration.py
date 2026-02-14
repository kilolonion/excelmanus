"""MCP 集成单元测试。

测试 MCP 工具注册后出现在 tool scope 中（Requirement 4.4），
以及无配置时 Agent 正常启动（Requirement 1.8）。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from excelmanus.config import ExcelManusConfig
from excelmanus.engine import AgentEngine
from excelmanus.mcp.config import MCPServerConfig
from excelmanus.mcp.manager import MCPManager, make_tool_def
from excelmanus.tools.registry import ToolDef, ToolRegistry


# ── 辅助工厂 ──────────────────────────────────────────────


def _make_config(**overrides) -> ExcelManusConfig:
    """创建测试用配置。"""
    defaults = {
        "api_key": "test-key",
        "base_url": "https://test.example.com/v1",
        "model": "test-model",
        "workspace_root": ".",
    }
    defaults.update(overrides)
    return ExcelManusConfig(**defaults)


def _make_mcp_server_config(name: str = "test-server", **overrides) -> MCPServerConfig:
    """创建测试用 MCPServerConfig。"""
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


def _make_mcp_tool(name: str = "remote_tool", description: str = "远程工具描述") -> SimpleNamespace:
    """创建模拟的 MCP 工具定义对象。"""
    return SimpleNamespace(
        name=name,
        description=description,
        inputSchema={"type": "object", "properties": {"arg": {"type": "string"}}},
    )


def _make_mock_client(
    config: MCPServerConfig | None = None,
    tools: list | None = None,
) -> AsyncMock:
    """创建模拟的 MCPClientWrapper。"""
    client = AsyncMock()
    client._config = config or _make_mcp_server_config()
    client.discover_tools.return_value = tools or []
    return client


def _make_builtin_tool(name: str = "builtin_tool") -> ToolDef:
    """创建一个简单的内置工具。"""
    return ToolDef(
        name=name,
        description="内置测试工具",
        input_schema={"type": "object", "properties": {}},
        func=lambda: "ok",
    )


# ══════════════════════════════════════════════════════════
# 测试 1：MCP 工具注册后出现在 tool scope 中（Requirement 4.4）
# ══════════════════════════════════════════════════════════


class TestMCPToolRegistration:
    """验证 MCP 工具注册后出现在 ToolRegistry 中，且具有正确的 mcp_ 前缀。"""

    @pytest.mark.asyncio
    async def test_mcp_tools_appear_in_registry(self):
        """MCP 工具注册后应出现在 registry.get_tool_names() 中。"""
        registry = ToolRegistry()
        cfg = _make_mcp_server_config(name="my-server")
        mock_client = _make_mock_client(
            config=cfg,
            tools=[
                _make_mcp_tool("read_file", "读取文件"),
                _make_mcp_tool("write_file", "写入文件"),
            ],
        )

        manager = MCPManager()
        with (
            patch("excelmanus.mcp.config.MCPConfigLoader") as mock_loader_cls,
            patch(
                "excelmanus.mcp.manager.MCPClientWrapper",
                return_value=mock_client,
            ),
        ):
            mock_loader_cls.load.return_value = [cfg]
            await manager.initialize(registry)

        tool_names = registry.get_tool_names()
        # 验证 MCP 工具已注册
        assert "mcp_my_server_read_file" in tool_names
        assert "mcp_my_server_write_file" in tool_names

    @pytest.mark.asyncio
    async def test_mcp_tools_have_correct_prefix(self):
        """注册的 MCP 工具名应以 mcp_ 开头。"""
        registry = ToolRegistry()
        cfg = _make_mcp_server_config(name="web-search")
        mock_client = _make_mock_client(
            config=cfg,
            tools=[_make_mcp_tool("search", "搜索")],
        )

        manager = MCPManager()
        with (
            patch("excelmanus.mcp.config.MCPConfigLoader") as mock_loader_cls,
            patch(
                "excelmanus.mcp.manager.MCPClientWrapper",
                return_value=mock_client,
            ),
        ):
            mock_loader_cls.load.return_value = [cfg]
            await manager.initialize(registry)

        tool_names = registry.get_tool_names()
        assert len(tool_names) == 1
        assert tool_names[0] == "mcp_web_search_search"
        assert tool_names[0].startswith("mcp_")

    @pytest.mark.asyncio
    async def test_mcp_tools_coexist_with_builtin(self):
        """MCP 工具应与内置工具共存于同一 registry 中。"""
        registry = ToolRegistry()
        registry.register_tool(_make_builtin_tool("builtin_add"))

        cfg = _make_mcp_server_config(name="ext")
        mock_client = _make_mock_client(
            config=cfg,
            tools=[_make_mcp_tool("do_stuff")],
        )

        manager = MCPManager()
        with (
            patch("excelmanus.mcp.config.MCPConfigLoader") as mock_loader_cls,
            patch(
                "excelmanus.mcp.manager.MCPClientWrapper",
                return_value=mock_client,
            ),
        ):
            mock_loader_cls.load.return_value = [cfg]
            await manager.initialize(registry)

        tool_names = registry.get_tool_names()
        assert "builtin_add" in tool_names
        assert "mcp_ext_do_stuff" in tool_names
        assert len(tool_names) == 2


# ══════════════════════════════════════════════════════════
# 测试 2：无配置时 Agent 正常启动（Requirement 1.8）
# ══════════════════════════════════════════════════════════


class TestNoConfigNormalStartup:
    """验证无 MCP 配置时 Agent 正常启动，不影响已有工具。"""

    @pytest.mark.asyncio
    async def test_no_config_keeps_existing_tools(self):
        """无 MCP 配置时，registry 保留原有内置工具，不添加 MCP 工具。"""
        registry = ToolRegistry()
        registry.register_tool(_make_builtin_tool("builtin_read"))
        registry.register_tool(_make_builtin_tool("builtin_write"))

        original_names = set(registry.get_tool_names())

        manager = MCPManager()
        with patch("excelmanus.mcp.config.MCPConfigLoader") as mock_loader_cls:
            mock_loader_cls.load.return_value = []
            await manager.initialize(registry)

        # 验证：原有工具不变
        assert set(registry.get_tool_names()) == original_names

    @pytest.mark.asyncio
    async def test_no_config_no_mcp_tools(self):
        """无 MCP 配置时，不应有任何 mcp_ 前缀的工具被注册。"""
        registry = ToolRegistry()
        registry.register_tool(_make_builtin_tool("my_tool"))

        manager = MCPManager()
        with patch("excelmanus.mcp.config.MCPConfigLoader") as mock_loader_cls:
            mock_loader_cls.load.return_value = []
            await manager.initialize(registry)

        mcp_tools = [n for n in registry.get_tool_names() if n.startswith("mcp_")]
        assert mcp_tools == []

    @pytest.mark.asyncio
    async def test_no_config_no_errors(self):
        """无 MCP 配置时，initialize 不应抛出任何异常。"""
        registry = ToolRegistry()
        manager = MCPManager()
        with patch("excelmanus.mcp.config.MCPConfigLoader") as mock_loader_cls:
            mock_loader_cls.load.return_value = []
            # 不应抛出异常
            await manager.initialize(registry)

        assert manager.connected_servers == []


# ══════════════════════════════════════════════════════════
# 测试 3：AgentEngine initialize_mcp / shutdown_mcp 集成
# ══════════════════════════════════════════════════════════


class TestAgentEngineMCPIntegration:
    """测试 AgentEngine 的 initialize_mcp 和 shutdown_mcp 方法。"""

    @pytest.mark.asyncio
    async def test_initialize_mcp_calls_manager(self):
        """engine.initialize_mcp() 应委托给 _mcp_manager.initialize()。"""
        config = _make_config()
        registry = ToolRegistry()

        with patch("openai.AsyncOpenAI"):
            engine = AgentEngine(config=config, registry=registry)

        # 替换 _mcp_manager 为 mock
        mock_manager = AsyncMock(spec=MCPManager)
        engine._mcp_manager = mock_manager

        await engine.initialize_mcp()

        mock_manager.initialize.assert_awaited_once_with(registry)

    @pytest.mark.asyncio
    async def test_shutdown_mcp_calls_manager(self):
        """engine.shutdown_mcp() 应委托给 _mcp_manager.shutdown()。"""
        config = _make_config()
        registry = ToolRegistry()

        with patch("openai.AsyncOpenAI"):
            engine = AgentEngine(config=config, registry=registry)

        mock_manager = AsyncMock(spec=MCPManager)
        engine._mcp_manager = mock_manager

        await engine.shutdown_mcp()

        mock_manager.shutdown.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_engine_creates_mcp_manager(self):
        """AgentEngine 初始化时应创建 MCPManager 实例。"""
        config = _make_config(workspace_root="/tmp/test")
        registry = ToolRegistry()

        with patch("openai.AsyncOpenAI"):
            engine = AgentEngine(config=config, registry=registry)

        assert isinstance(engine._mcp_manager, MCPManager)
        assert engine._mcp_manager._workspace_root == "/tmp/test"


# ══════════════════════════════════════════════════════════
# 测试 4：MCP Skillpack 自动生成
# ══════════════════════════════════════════════════════════


class TestMCPSkillpackGeneration:
    """验证从已连接 MCP Server 的工具元数据自动生成 Skillpack。"""

    def test_generate_skillpacks_from_connected_servers(self):
        """每个已连接 MCP Server 应生成一个对应的 Skillpack。"""
        manager = MCPManager()
        cfg = _make_mcp_server_config(name="context7")
        mock_client = _make_mock_client(
            config=cfg,
            tools=[
                _make_mcp_tool("resolve-library-id", "解析库标识符"),
                _make_mcp_tool("query-docs", "查询文档"),
            ],
        )
        # 模拟已连接状态
        manager._clients["context7"] = mock_client
        mock_client._tools = mock_client.discover_tools.return_value

        skillpacks = manager.generate_skillpacks()

        assert len(skillpacks) == 1
        sp = skillpacks[0]
        assert sp.name == "mcp_context7"
        assert sp.allowed_tools == ["mcp:context7:*"]
        assert sp.source == "system"
        assert sp.priority == 3
        assert sp.user_invocable is True
        assert "resolve-library-id" in sp.instructions
        assert "query-docs" in sp.instructions

    def test_generate_skillpacks_normalizes_server_name(self):
        """server name 中的 - 应被替换为 _ 作为 Skillpack 名称。"""
        manager = MCPManager()
        cfg = _make_mcp_server_config(name="sequential-thinking")
        mock_client = _make_mock_client(
            config=cfg,
            tools=[_make_mcp_tool("think", "思考工具")],
        )
        manager._clients["sequential-thinking"] = mock_client
        mock_client._tools = mock_client.discover_tools.return_value

        skillpacks = manager.generate_skillpacks()

        assert len(skillpacks) == 1
        assert skillpacks[0].name == "mcp_sequential_thinking"
        # allowed_tools 使用原始 server name（含 -）
        assert skillpacks[0].allowed_tools == ["mcp:sequential-thinking:*"]

    def test_generate_skillpacks_multiple_servers(self):
        """多个 MCP Server 应各生成独立的 Skillpack。"""
        manager = MCPManager()
        for name, tools in [
            ("context7", [_make_mcp_tool("resolve-library-id")]),
            ("excel", [_make_mcp_tool("read_sheet"), _make_mcp_tool("write_sheet")]),
            ("git", [_make_mcp_tool("git_status")]),
        ]:
            cfg = _make_mcp_server_config(name=name)
            client = _make_mock_client(config=cfg, tools=tools)
            client._tools = client.discover_tools.return_value
            manager._clients[name] = client

        skillpacks = manager.generate_skillpacks()

        assert len(skillpacks) == 3
        names = {sp.name for sp in skillpacks}
        assert names == {"mcp_context7", "mcp_excel", "mcp_git"}

    def test_generate_skillpacks_skips_server_with_no_tools(self):
        """无工具的 MCP Server 不应生成 Skillpack。"""
        manager = MCPManager()
        cfg = _make_mcp_server_config(name="empty-server")
        mock_client = _make_mock_client(config=cfg, tools=[])
        mock_client._tools = []
        manager._clients["empty-server"] = mock_client

        skillpacks = manager.generate_skillpacks()

        assert skillpacks == []

    def test_generate_skillpacks_no_connected_servers(self):
        """无已连接 Server 时返回空列表。"""
        manager = MCPManager()
        assert manager.generate_skillpacks() == []


class TestSkillpackInjection:
    """验证 SkillpackLoader.inject_skillpacks() 注入逻辑。"""

    def _make_loader(self):
        """创建一个最小化的 SkillpackLoader。"""
        from excelmanus.skillpacks.loader import SkillpackLoader
        config = _make_config()
        registry = ToolRegistry()
        return SkillpackLoader(config=config, tool_registry=registry)

    def _make_skillpack(self, name: str = "mcp_test"):
        """创建一个测试用 Skillpack。"""
        from excelmanus.skillpacks.models import Skillpack
        return Skillpack(
            name=name,
            description="测试 MCP Skillpack",
            allowed_tools=[f"mcp:test:*"],
            triggers=[],
            instructions="测试指引",
            source="system",
            root_dir="",
        )

    def test_inject_adds_to_skillpacks(self):
        """注入的 Skillpack 应出现在 get_skillpacks() 中。"""
        loader = self._make_loader()
        sp = self._make_skillpack("mcp_context7")

        count = loader.inject_skillpacks([sp])

        assert count == 1
        assert "mcp_context7" in loader.get_skillpacks()

    def test_inject_does_not_overwrite_existing(self):
        """同名 Skillpack 已存在时应跳过注入。"""
        loader = self._make_loader()
        existing = self._make_skillpack("mcp_excel")
        loader._skillpacks["mcp_excel"] = existing

        new_sp = self._make_skillpack("mcp_excel")
        count = loader.inject_skillpacks([new_sp])

        assert count == 0
        # 原有的保留不变
        assert loader.get_skillpacks()["mcp_excel"] is existing

    def test_inject_multiple(self):
        """批量注入多个 Skillpack。"""
        loader = self._make_loader()
        sps = [
            self._make_skillpack("mcp_a"),
            self._make_skillpack("mcp_b"),
            self._make_skillpack("mcp_c"),
        ]

        count = loader.inject_skillpacks(sps)

        assert count == 3
        assert set(loader.get_skillpacks().keys()) == {"mcp_a", "mcp_b", "mcp_c"}


class TestExcelMCPPathAdaptation:
    """验证 Excel MCP 路径参数的自动纠偏。"""

    def _make_call_tool_client(self, captured: dict[str, object], timeout: int = 30):
        async def _call_tool(tool_name: str, arguments: dict[str, object]):
            captured["tool_name"] = tool_name
            captured["arguments"] = arguments
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text="ok")],
            )

        return SimpleNamespace(
            _config=SimpleNamespace(timeout=timeout),
            call_tool=_call_tool,
        )

    def _make_excel_tool(self, name: str = "excel_describe_sheets"):
        return SimpleNamespace(
            name=name,
            description="Excel 测试工具",
            inputSchema={
                "type": "object",
                "properties": {"fileAbsolutePath": {"type": "string"}},
            },
        )

    def test_excel_relative_path_auto_resolved(self, tmp_path):
        """Excel MCP 相对路径应自动转为工作区绝对路径。"""
        captured: dict[str, object] = {}
        client = self._make_call_tool_client(captured)
        tool_def = make_tool_def(
            "excel",
            client,
            self._make_excel_tool(),
            workspace_root=str(tmp_path),
        )

        result = tool_def.func(fileAbsolutePath="demo_sales_data.xlsx")

        assert result == "ok"
        called_args = captured["arguments"]
        assert isinstance(called_args, dict)
        assert called_args["fileAbsolutePath"] == str(
            (tmp_path / "demo_sales_data.xlsx").resolve(),
        )

    def test_excel_invalid_absolute_path_fallback_to_workspace_same_name(self, tmp_path):
        """Excel MCP 错误绝对路径应回退到工作区同名文件。"""
        workbook = tmp_path / "demo_sales_data.xlsx"
        workbook.write_bytes(b"placeholder")

        captured: dict[str, object] = {}
        client = self._make_call_tool_client(captured)
        tool_def = make_tool_def(
            "excel",
            client,
            self._make_excel_tool("excel_read_sheet"),
            workspace_root=str(tmp_path),
        )

        result = tool_def.func(
            fileAbsolutePath="/tmp/dataset/task_1749690261/demo_sales_data.xlsx",
            sheetName="Sheet1",
        )

        assert result == "ok"
        called_args = captured["arguments"]
        assert isinstance(called_args, dict)
        assert called_args["fileAbsolutePath"] == str(workbook.resolve())

    def test_non_excel_server_keeps_arguments_unchanged(self, tmp_path):
        """非 Excel MCP 工具不应改写参数。"""
        captured: dict[str, object] = {}
        client = self._make_call_tool_client(captured)
        mcp_tool = SimpleNamespace(
            name="status",
            description="Git 状态",
            inputSchema={"type": "object", "properties": {"fileAbsolutePath": {"type": "string"}}},
        )
        tool_def = make_tool_def(
            "git",
            client,
            mcp_tool,
            workspace_root=str(tmp_path),
        )

        result = tool_def.func(fileAbsolutePath="demo_sales_data.xlsx")

        assert result == "ok"
        called_args = captured["arguments"]
        assert isinstance(called_args, dict)
        assert called_args["fileAbsolutePath"] == "demo_sales_data.xlsx"
