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


# ── MCP 指南不再注入 system ─────────────────────────────────


def test_mcp_guide_not_injected_into_system():
    from unittest.mock import MagicMock

    from excelmanus.prompt.assemble import prepare_system_prompts_for_request

    engine = MagicMock()
    engine.memory.system_prompt = "You are ExcelManus."
    engine._prompt_composer = None
    engine._transient_hook_contexts = []
    engine.full_access_enabled = False
    engine.max_context_tokens = 100000
    engine._effective_system_mode.return_value = "multi"
    engine.state.prompt_injection_snapshots = []
    engine.state.injected_context_fingerprint = None
    engine._current_chat_mode = "write"
    engine._present_as = "native"
    engine._runtime_vars = {"workspace_root": "/tmp/ws", "model": "test-model"}
    prompts, error = prepare_system_prompts_for_request(engine, [])
    assert error is None
    blob = "\n".join(prompts)
    assert "搜索工具选择指南" not in blob
    assert "通用网页搜索" not in blob


