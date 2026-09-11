"""内置 Exa 搜索集成测试。

覆盖：
1. builtin.py — 配置生成与开关
2. MCPManager._merge_builtin_configs — 合并逻辑与用户覆盖
3. Exa 内置 MCP 配置与开关
4. config.py — exa_search_enabled 环境变量解析
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest

from excelmanus.mcp.builtin import (
    _EXA_MCP_URL,
    _EXA_SERVER_NAME,
    _EXA_SSE_URL,
    _EXA_STREAMABLE_HTTP_URL,
    _sdk_supports_streamable_http,
    get_builtin_mcp_configs,
)
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


# ── get_builtin_mcp_configs ─────────────────────────────────


class TestGetBuiltinMcpConfigs:
    """测试 builtin.py 的配置生成。"""

    def test_enabled_returns_exa(self):
        configs = get_builtin_mcp_configs(_FakeConfig(exa_search_enabled=True))
        assert len(configs) == 1
        cfg = configs[0]
        assert cfg.name == _EXA_SERVER_NAME
        assert cfg.auto_approve == ["*"]
        assert cfg.timeout == 30
        # transport 取决于 SDK 版本
        assert cfg.transport in ("streamable_http", "sse")
        if cfg.transport == "streamable_http":
            assert cfg.url == _EXA_STREAMABLE_HTTP_URL
        else:
            assert cfg.url == _EXA_SSE_URL

    def test_disabled_returns_empty(self):
        configs = get_builtin_mcp_configs(_FakeConfig(exa_search_enabled=False))
        assert configs == []


# ── SDK 能力检测与 SSE 降级 ─────────────────────────────────


class TestSdkFallback:
    """测试 SDK 能力检测和 SSE 降级逻辑。"""

    def test_sdk_detection_returns_bool(self):
        """_sdk_supports_streamable_http 返回布尔值。"""
        result = _sdk_supports_streamable_http()
        assert isinstance(result, bool)

    def test_fallback_uses_sse_transport(self):
        """SDK 不支持 streamable_http 时降级为 SSE。"""
        configs = get_builtin_mcp_configs(_FakeConfig(exa_search_enabled=True))
        cfg = configs[0]
        if not _sdk_supports_streamable_http():
            assert cfg.transport == "sse"
            assert cfg.url == _EXA_SSE_URL

    def test_fallback_uses_streamable_http_when_available(self, monkeypatch):
        """SDK 支持 streamable_http 时使用 streamable_http。"""
        import excelmanus.mcp.builtin as builtin_mod
        monkeypatch.setattr(builtin_mod, "_sdk_supports_streamable_http", lambda: True)
        configs = get_builtin_mcp_configs(_FakeConfig(exa_search_enabled=True))
        cfg = configs[0]
        assert cfg.transport == "streamable_http"
        assert cfg.url == _EXA_STREAMABLE_HTTP_URL

    def test_sse_url_differs_from_streamable_http_url(self):
        """SSE 和 streamable_http 端点 URL 不同。"""
        assert _EXA_SSE_URL != _EXA_STREAMABLE_HTTP_URL
        assert _EXA_SSE_URL == "https://mcp.exa.ai/sse"
        assert _EXA_STREAMABLE_HTTP_URL == "https://mcp.exa.ai/mcp"


# ── MCPManager._merge_builtin_configs ───────────────────────


class TestMergeBuiltinConfigs:
    """测试 MCPManager 的内置配置合并逻辑。"""

    def test_no_app_config_returns_user_as_is(self):
        """无 app_config 时原样返回用户配置。"""
        mgr = MCPManager(workspace_root=".", app_config=None)
        user_cfgs = [
            MCPServerConfig(name="excel", transport="stdio", command="node"),
        ]
        result = mgr._merge_builtin_configs(user_cfgs)
        assert result is user_cfgs

    def test_builtin_appended(self):
        """内置配置追加到用户配置末尾。"""
        mgr = MCPManager(
            workspace_root=".",
            app_config=_FakeConfig(exa_search_enabled=True),
        )
        user_cfgs = [
            MCPServerConfig(name="excel", transport="stdio", command="node"),
        ]
        result = mgr._merge_builtin_configs(user_cfgs)
        assert len(result) == 2
        assert result[0].name == "excel"
        assert result[1].name == "exa"
        assert result[1].url in (_EXA_STREAMABLE_HTTP_URL, _EXA_SSE_URL)

    def test_user_override_builtin(self):
        """用户 mcp.json 同名配置覆盖内置。"""
        mgr = MCPManager(
            workspace_root=".",
            app_config=_FakeConfig(exa_search_enabled=True),
        )
        user_exa = MCPServerConfig(
            name="exa",
            transport="sse",
            url="https://custom-exa.example.com/mcp",
        )
        user_cfgs = [user_exa]
        result = mgr._merge_builtin_configs(user_cfgs)
        assert len(result) == 1
        assert result[0].url == "https://custom-exa.example.com/mcp"

    def test_disabled_no_injection(self):
        """exa_search_enabled=False 时不注入内置。"""
        mgr = MCPManager(
            workspace_root=".",
            app_config=_FakeConfig(exa_search_enabled=False),
        )
        user_cfgs = [
            MCPServerConfig(name="excel", transport="stdio", command="node"),
        ]
        result = mgr._merge_builtin_configs(user_cfgs)
        assert len(result) == 1
        assert result[0].name == "excel"

    def test_empty_user_configs_only_builtin(self):
        """无用户配置时仅有内置。"""
        mgr = MCPManager(
            workspace_root=".",
            app_config=_FakeConfig(exa_search_enabled=True),
        )
        result = mgr._merge_builtin_configs([])
        assert len(result) == 1
        assert result[0].name == "exa"


# ── config 环境变量解析 ──────────────────────────────────────


class TestExaSearchConfig:
    """测试 exa_search_enabled 配置。"""

    def test_default_enabled(self):
        """默认开启。"""
        from excelmanus.config import ExcelManusConfig

        assert ExcelManusConfig.exa_search_enabled is True

    def test_env_var_false(self, monkeypatch):
        """EXCELMANUS_EXA_SEARCH=false 关闭。"""
        monkeypatch.setenv("EXCELMANUS_EXA_SEARCH", "false")
        from excelmanus.config import _parse_bool

        result = _parse_bool("false", "EXCELMANUS_EXA_SEARCH", True)
        assert result is False

    def test_env_var_true(self, monkeypatch):
        """EXCELMANUS_EXA_SEARCH=true 开启。"""
        monkeypatch.setenv("EXCELMANUS_EXA_SEARCH", "true")
        from excelmanus.config import _parse_bool

        result = _parse_bool("true", "EXCELMANUS_EXA_SEARCH", True)
        assert result is True


# ── MCPServerConfig 常量验证 ─────────────────────────────────


class TestExaConstants:
    """验证内置常量正确性。"""

    def test_exa_server_name(self):
        assert _EXA_SERVER_NAME == "exa"

    def test_exa_url(self):
        assert _EXA_MCP_URL == "https://mcp.exa.ai/mcp"
