"""MCP 配置与上下文注解测试。"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest

from excelmanus.mcp.builtin import get_builtin_mcp_configs, _EXA_SERVER_NAME
from excelmanus.mcp.config import MCPConfigLoader, MCPServerConfig
from excelmanus.mcp.manager import MCPManager


# ── 辅助 ────────────────────────────────────────────────────


@dataclass
class _FakeConfig:
    exa_search_enabled: bool = True
    search_default_provider: str = "exa"
    exa_api_key: str | None = None
    tavily_api_key: str | None = None
    brave_api_key: str | None = None


# ── 1. MCPServerConfig.scope 字段 ────────────────────────────


class TestMCPServerConfigScope:
    """MCPServerConfig.scope 字段默认值和赋值。"""

    def test_default_scope_is_always(self):
        cfg = MCPServerConfig(name="test", transport="sse", url="http://x")
        assert cfg.scope == "always"

    def test_custom_scope(self):
        cfg = MCPServerConfig(name="test", transport="sse", url="http://x", scope="search")
        assert cfg.scope == "search"

    def test_scope_preserved_in_dataclass(self):
        cfg = MCPServerConfig(
            name="test", transport="stdio", command="node",
            scope="dev_docs",
        )
        assert cfg.scope == "dev_docs"


# ── 2. MCPConfigLoader._parse_scope ─────────────────────────


class TestParseScope:
    """mcp.json scope 字段解析。"""

    def test_default_when_absent(self):
        result = MCPConfigLoader._parse_scope("test", {})
        assert result == "always"

    def test_parses_valid_scope(self):
        result = MCPConfigLoader._parse_scope("test", {"scope": "search"})
        assert result == "search"

    def test_lowercases_scope(self):
        result = MCPConfigLoader._parse_scope("test", {"scope": "Dev_Docs"})
        assert result == "dev_docs"

    def test_strips_whitespace(self):
        result = MCPConfigLoader._parse_scope("test", {"scope": "  search  "})
        assert result == "search"

    def test_invalid_type_fallback(self):
        result = MCPConfigLoader._parse_scope("test", {"scope": 123})
        assert result == "always"

    def test_empty_string_fallback(self):
        result = MCPConfigLoader._parse_scope("test", {"scope": ""})
        assert result == "always"

    def test_none_value_default(self):
        result = MCPConfigLoader._parse_scope("test", {"scope": None})
        assert result == "always"


class TestMcpJsonScopeIntegration:
    """通过 _parse_config 解析 scope 字段集成测试。"""

    def test_scope_parsed_for_sse_server(self):
        data = {
            "mcpServers": {
                "exa": {
                    "transport": "sse",
                    "url": "https://mcp.exa.ai/sse",
                    "scope": "search",
                }
            }
        }
        configs = MCPConfigLoader._parse_config(data)
        assert len(configs) == 1
        assert configs[0].scope == "search"

    def test_scope_default_when_absent(self):
        data = {
            "mcpServers": {
                "test": {
                    "transport": "sse",
                    "url": "https://example.com/sse",
                }
            }
        }
        configs = MCPConfigLoader._parse_config(data)
        assert len(configs) == 1
        assert configs[0].scope == "always"

    def test_scope_parsed_for_stdio_server(self):
        data = {
            "mcpServers": {
                "context7": {
                    "transport": "stdio",
                    "command": "node",
                    "args": ["server.js"],
                    "scope": "dev_docs",
                }
            }
        }
        configs = MCPConfigLoader._parse_config(data)
        assert len(configs) == 1
        assert configs[0].scope == "dev_docs"


# ── 3. Exa 内置 scope="search" ──────────────────────────────


class TestExaBuiltinScope:
    """Exa 内置 MCP Server 的 scope 配置。"""

    def test_exa_scope_is_search(self):
        configs = get_builtin_mcp_configs(_FakeConfig(exa_search_enabled=True))
        assert len(configs) == 1
        assert configs[0].scope == "search"

    def test_exa_name_unchanged(self):
        configs = get_builtin_mcp_configs(_FakeConfig(exa_search_enabled=True))
        assert configs[0].name == _EXA_SERVER_NAME


# ── 4. MCPManager._tool_scopes 追踪 ─────────────────────────


class TestMCPManagerToolScopes:
    """MCPManager tool_scopes 属性测试。"""

    def test_initial_empty(self):
        mgr = MCPManager(workspace_root=".")
        assert mgr.tool_scopes == {}

    def test_tool_scopes_returns_copy(self):
        mgr = MCPManager(workspace_root=".")
        mgr._tool_scopes["mcp_exa_search"] = "search"
        scopes = mgr.tool_scopes
        scopes["mcp_extra"] = "always"
        assert "mcp_extra" not in mgr._tool_scopes


# ── 10. _build_mcp_context_notice 场景注解 ────────────────────


class TestMCPContextNotice:
    """_build_mcp_context_notice 场景注解与搜索指南。"""

    def _make_engine_for_notice(self, servers, tool_scopes=None):
        engine = MagicMock()
        engine._mcp_manager.get_server_info.return_value = servers
        engine._mcp_manager.tool_scopes = tool_scopes or {}
        return engine

    def test_exa_usage_hint_in_notice(self):
        from excelmanus.engine_core.context_builder import ContextBuilder
        servers = [
            {"name": "exa", "status": "ready", "tool_count": 1, "tools": ["web_search_exa"]},
        ]
        engine = self._make_engine_for_notice(servers, {"mcp_exa_web_search_exa": "search"})
        cb = ContextBuilder.__new__(ContextBuilder)
        cb._engine = engine
        notice = cb._build_mcp_context_notice()
        assert "通用网页搜索" in notice

    def test_context7_usage_hint_in_notice(self):
        from excelmanus.engine_core.context_builder import ContextBuilder
        servers = [
            {"name": "context7", "status": "ready", "tool_count": 2, "tools": ["resolve_library_id", "query_docs"]},
        ]
        engine = self._make_engine_for_notice(servers, {"mcp_context7_resolve_library_id": "dev_docs"})
        cb = ContextBuilder.__new__(ContextBuilder)
        cb._engine = engine
        notice = cb._build_mcp_context_notice()
        assert "编程库/框架" in notice

    def test_search_guide_when_both_exa_and_context7(self):
        from excelmanus.engine_core.context_builder import ContextBuilder
        servers = [
            {"name": "exa", "status": "ready", "tool_count": 1, "tools": ["web_search_exa"]},
            {"name": "context7", "status": "ready", "tool_count": 2, "tools": ["resolve_library_id"]},
        ]
        scopes = {
            "mcp_exa_web_search_exa": "search",
            "mcp_context7_resolve_library_id": "dev_docs",
        }
        engine = self._make_engine_for_notice(servers, scopes)
        cb = ContextBuilder.__new__(ContextBuilder)
        cb._engine = engine
        notice = cb._build_mcp_context_notice()
        assert "搜索工具选择指南" in notice
        assert "exa" in notice.lower()
        assert "context7" in notice.lower()

    def test_search_guide_only_exa(self):
        from excelmanus.engine_core.context_builder import ContextBuilder
        servers = [
            {"name": "exa", "status": "ready", "tool_count": 1, "tools": ["web_search_exa"]},
        ]
        scopes = {"mcp_exa_web_search_exa": "search"}
        engine = self._make_engine_for_notice(servers, scopes)
        cb = ContextBuilder.__new__(ContextBuilder)
        cb._engine = engine
        notice = cb._build_mcp_context_notice()
        assert "搜索指南" in notice
        # 不应出现 "搜索工具选择指南"（无 context7 无需对比）
        assert "搜索工具选择指南" not in notice

    def test_no_guide_without_search_tools(self):
        from excelmanus.engine_core.context_builder import ContextBuilder
        servers = [
            {"name": "excel", "status": "ready", "tool_count": 3, "tools": ["read_sheet"]},
        ]
        scopes = {"mcp_excel_read_sheet": "always"}
        engine = self._make_engine_for_notice(servers, scopes)
        cb = ContextBuilder.__new__(ContextBuilder)
        cb._engine = engine
        notice = cb._build_mcp_context_notice()
        assert "搜索指南" not in notice
        assert "搜索工具选择指南" not in notice

    def test_empty_servers_returns_empty(self):
        from excelmanus.engine_core.context_builder import ContextBuilder
        engine = self._make_engine_for_notice([])
        cb = ContextBuilder.__new__(ContextBuilder)
        cb._engine = engine
        assert cb._build_mcp_context_notice() == ""

    def test_no_ready_servers_returns_empty(self):
        from excelmanus.engine_core.context_builder import ContextBuilder
        servers = [{"name": "exa", "status": "connect_failed", "tool_count": 0, "tools": []}]
        engine = self._make_engine_for_notice(servers)
        cb = ContextBuilder.__new__(ContextBuilder)
        cb._engine = engine
        assert cb._build_mcp_context_notice() == ""


