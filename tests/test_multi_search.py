"""多搜索引擎集成测试。

覆盖：
1. config.py — 新增搜索引擎配置字段和环境变量解析
2. builtin.py — 多引擎注册逻辑（默认+可选模式）
3. builtin.py — 降级逻辑（默认引擎不可用时回退 exa）
4. builtin.py — Exa API 密钥支持（付费模式）
5. builtin.py — npx 检测与 stdio 引擎
6. context_builder.py — 多引擎使用提示
7. ToolCallCard.tsx — 前端工具分类（通过模式验证）
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from unittest.mock import patch

from excelmanus.mcp.builtin import (
    BUILTIN_SEARCH_SERVER_NAMES,
    _BRAVE_SERVER_NAME,
    _EXA_SERVER_NAME,
    _TAVILY_SERVER_NAME,
    _build_brave_config,
    _build_exa_config,
    _build_tavily_config,
    _has_npx,
    _try_build_engine,
    get_builtin_mcp_configs,
)
from excelmanus.mcp.config import MCPServerConfig
from excelmanus.mcp.manager import MCPManager


# ── 辅助 ────────────────────────────────────────────────────


@dataclass
class _FakeConfig:
    """最小化的 Config 替身，覆盖多搜索引擎字段。"""

    exa_search_enabled: bool = True
    search_default_provider: str = "exa"
    exa_api_key: str | None = None
    tavily_api_key: str | None = None
    brave_api_key: str | None = None


# ── 常量与名称集合 ──────────────────────────────────────────


class TestBuiltinSearchServerNames:
    """BUILTIN_SEARCH_SERVER_NAMES 常量验证。"""

    def test_contains_all_three_engines(self):
        assert "exa" in BUILTIN_SEARCH_SERVER_NAMES
        assert "tavily" in BUILTIN_SEARCH_SERVER_NAMES
        assert "brave" in BUILTIN_SEARCH_SERVER_NAMES

    def test_is_frozenset(self):
        assert isinstance(BUILTIN_SEARCH_SERVER_NAMES, frozenset)


# ── 单引擎配置构建 ──────────────────────────────────────────


class TestBuildExaConfig:
    """Exa 引擎配置构建。"""

    def test_free_mode_no_headers(self):
        cfg = _build_exa_config(_FakeConfig())
        assert cfg.name == _EXA_SERVER_NAME
        assert cfg.scope == "search"
        assert cfg.auto_approve == ["*"]
        assert cfg.headers == {}

    def test_paid_mode_with_api_key(self):
        cfg = _build_exa_config(_FakeConfig(exa_api_key="test-key-123"))
        assert cfg.headers == {"x-api-key": "test-key-123"}
        assert cfg.name == "exa"

    def test_transport_is_valid(self):
        cfg = _build_exa_config(_FakeConfig())
        assert cfg.transport in ("streamable_http", "sse")


class TestBuildTavilyConfig:
    """Tavily 引擎配置构建。"""

    def test_basic_config(self):
        cfg = _build_tavily_config("tavily-key-abc")
        assert cfg.name == _TAVILY_SERVER_NAME
        assert cfg.transport == "stdio"
        assert cfg.command == "npx"
        assert "-y" in cfg.args
        assert cfg.env == {"TAVILY_API_KEY": "tavily-key-abc"}
        assert cfg.scope == "search"
        assert cfg.auto_approve == ["*"]


class TestBuildBraveConfig:
    """Brave 引擎配置构建。"""

    def test_basic_config(self):
        cfg = _build_brave_config("brave-key-xyz")
        assert cfg.name == _BRAVE_SERVER_NAME
        assert cfg.transport == "stdio"
        assert cfg.command == "npx"
        assert "-y" in cfg.args
        assert cfg.env == {"BRAVE_API_KEY": "brave-key-xyz"}
        assert cfg.scope == "search"
        assert cfg.auto_approve == ["*"]


# ── npx 检测 ───────────────────────────────────────────────


class TestHasNpx:
    """npx 可用性检测。"""

    def test_returns_bool(self):
        assert isinstance(_has_npx(), bool)

    @patch("excelmanus.mcp.builtin.shutil.which", return_value=None)
    def test_no_npx(self, mock_which):
        assert _has_npx() is False

    @patch("excelmanus.mcp.builtin.shutil.which", return_value="/usr/bin/npx")
    def test_has_npx(self, mock_which):
        assert _has_npx() is True


# ── get_builtin_mcp_configs 多引擎逻辑 ─────────────────────


class TestGetBuiltinMcpConfigsMultiEngine:
    """测试 get_builtin_mcp_configs 的多引擎启用策略。"""

    def test_disabled_returns_empty(self):
        """exa_search_enabled=False 禁用全部搜索引擎。"""
        cfg = _FakeConfig(exa_search_enabled=False)
        assert get_builtin_mcp_configs(cfg) == []

    def test_default_exa_only(self):
        """默认配置只启用 exa（无其他引擎密钥）。"""
        configs = get_builtin_mcp_configs(_FakeConfig())
        assert len(configs) == 1
        assert configs[0].name == "exa"

    @patch("excelmanus.mcp.builtin._has_npx", return_value=True)
    def test_exa_default_with_tavily_key(self, _):
        """默认 exa + 配置了 tavily 密钥 → 两个引擎。"""
        cfg = _FakeConfig(tavily_api_key="tk")
        configs = get_builtin_mcp_configs(cfg)
        names = {c.name for c in configs}
        assert names == {"exa", "tavily"}

    @patch("excelmanus.mcp.builtin._has_npx", return_value=True)
    def test_exa_default_with_brave_key(self, _):
        """默认 exa + 配置了 brave 密钥 → 两个引擎。"""
        cfg = _FakeConfig(brave_api_key="bk")
        configs = get_builtin_mcp_configs(cfg)
        names = {c.name for c in configs}
        assert names == {"exa", "brave"}

    @patch("excelmanus.mcp.builtin._has_npx", return_value=True)
    def test_all_keys_configured(self, _):
        """所有密钥都配置 → 三个引擎全部启用。"""
        cfg = _FakeConfig(
            exa_api_key="ek",
            tavily_api_key="tk",
            brave_api_key="bk",
        )
        configs = get_builtin_mcp_configs(cfg)
        names = {c.name for c in configs}
        assert names == {"exa", "tavily", "brave"}

    @patch("excelmanus.mcp.builtin._has_npx", return_value=True)
    def test_exa_with_api_key_has_headers(self, _):
        """Exa 配置了 API 密钥时 headers 包含 x-api-key。"""
        cfg = _FakeConfig(exa_api_key="my-exa-key")
        configs = get_builtin_mcp_configs(cfg)
        exa_cfg = [c for c in configs if c.name == "exa"][0]
        assert exa_cfg.headers == {"x-api-key": "my-exa-key"}


# ── 降级逻辑 ───────────────────────────────────────────────


class TestDefaultProviderFallback:
    """默认引擎不可用时降级回 exa。"""

    @patch("excelmanus.mcp.builtin._has_npx", return_value=True)
    def test_tavily_default_without_key_fallback(self, _):
        """默认 tavily 但缺少密钥 → 降级回 exa。"""
        cfg = _FakeConfig(search_default_provider="tavily")
        configs = get_builtin_mcp_configs(cfg)
        assert len(configs) >= 1
        assert configs[0].name == "exa"

    @patch("excelmanus.mcp.builtin._has_npx", return_value=True)
    def test_tavily_default_with_key(self, _):
        """默认 tavily 且有密钥 → 使用 tavily。"""
        cfg = _FakeConfig(search_default_provider="tavily", tavily_api_key="tk")
        configs = get_builtin_mcp_configs(cfg)
        assert configs[0].name == "tavily"

    @patch("excelmanus.mcp.builtin._has_npx", return_value=False)
    def test_tavily_default_no_npx_fallback(self, _):
        """默认 tavily 有密钥但无 npx → 降级回 exa。"""
        cfg = _FakeConfig(search_default_provider="tavily", tavily_api_key="tk")
        configs = get_builtin_mcp_configs(cfg)
        assert configs[0].name == "exa"

    @patch("excelmanus.mcp.builtin._has_npx", return_value=True)
    def test_brave_default_without_key_fallback(self, _):
        """默认 brave 但缺少密钥 → 降级回 exa。"""
        cfg = _FakeConfig(search_default_provider="brave")
        configs = get_builtin_mcp_configs(cfg)
        assert configs[0].name == "exa"

    @patch("excelmanus.mcp.builtin._has_npx", return_value=True)
    def test_brave_default_with_key(self, _):
        """默认 brave 且有密钥 → 使用 brave。"""
        cfg = _FakeConfig(search_default_provider="brave", brave_api_key="bk")
        configs = get_builtin_mcp_configs(cfg)
        assert configs[0].name == "brave"


# ── npx 不可用时 stdio 引擎被跳过 ──────────────────────────


class TestNpxDependency:
    """stdio 引擎（tavily/brave）在无 npx 时被跳过。"""

    @patch("excelmanus.mcp.builtin._has_npx", return_value=False)
    def test_tavily_skipped_without_npx(self, _):
        """有 tavily 密钥但无 npx → tavily 不启用。"""
        cfg = _FakeConfig(tavily_api_key="tk")
        configs = get_builtin_mcp_configs(cfg)
        names = {c.name for c in configs}
        assert "tavily" not in names
        assert "exa" in names  # 默认引擎仍然可用

    @patch("excelmanus.mcp.builtin._has_npx", return_value=False)
    def test_brave_skipped_without_npx(self, _):
        """有 brave 密钥但无 npx → brave 不启用。"""
        cfg = _FakeConfig(brave_api_key="bk")
        configs = get_builtin_mcp_configs(cfg)
        names = {c.name for c in configs}
        assert "brave" not in names
        assert "exa" in names


# ── _try_build_engine ──────────────────────────────────────


class TestTryBuildEngine:
    """_try_build_engine 辅助函数。"""

    def test_unknown_engine_returns_none(self):
        result = _try_build_engine(_FakeConfig(), "unknown_engine", lambda: True, is_default=True)
        assert result is None

    def test_exa_default_always_builds(self):
        result = _try_build_engine(_FakeConfig(), "exa", lambda: True, is_default=True)
        assert result is not None
        assert result.name == "exa"

    def test_exa_non_default_without_key_returns_none(self):
        """非默认 exa 无密钥 → 不启用（避免重复）。"""
        result = _try_build_engine(_FakeConfig(), "exa", lambda: True, is_default=False)
        assert result is None

    def test_exa_non_default_with_key_builds(self):
        """非默认 exa 有密钥 → 启用。"""
        cfg = _FakeConfig(exa_api_key="ek")
        result = _try_build_engine(cfg, "exa", lambda: True, is_default=False)
        assert result is not None
        assert result.name == "exa"

    def test_tavily_requires_key(self):
        result = _try_build_engine(_FakeConfig(), "tavily", lambda: True, is_default=False)
        assert result is None

    def test_tavily_requires_npx(self):
        cfg = _FakeConfig(tavily_api_key="tk")
        result = _try_build_engine(cfg, "tavily", lambda: False, is_default=False)
        assert result is None

    def test_tavily_with_key_and_npx(self):
        cfg = _FakeConfig(tavily_api_key="tk")
        result = _try_build_engine(cfg, "tavily", lambda: True, is_default=False)
        assert result is not None
        assert result.name == "tavily"


# ── Config 环境变量解析 ─────────────────────────────────────


class TestConfigEnvVarParsing:
    """搜索引擎相关环境变量解析。"""

    def test_search_default_provider_env(self):
        """EXCELMANUS_SEARCH_DEFAULT 环境变量。"""
        from excelmanus.config import load_config

        env = {
            "EXCELMANUS_API_KEY": "test",
            "EXCELMANUS_MODEL": "test-model",
            "EXCELMANUS_SEARCH_DEFAULT": "tavily",
        }
        with patch.dict(os.environ, env, clear=False):
            config = load_config()
            assert config.search_default_provider == "tavily"

    def test_search_default_provider_invalid_fallback(self):
        """无效的 EXCELMANUS_SEARCH_DEFAULT 回退到 exa。"""
        from excelmanus.config import load_config

        env = {
            "EXCELMANUS_API_KEY": "test",
            "EXCELMANUS_MODEL": "test-model",
            "EXCELMANUS_SEARCH_DEFAULT": "invalid_engine",
        }
        with patch.dict(os.environ, env, clear=False):
            config = load_config()
            assert config.search_default_provider == "exa"

    def test_exa_api_key_env(self):
        """EXCELMANUS_EXA_API_KEY 环境变量。"""
        from excelmanus.config import load_config

        env = {
            "EXCELMANUS_API_KEY": "test",
            "EXCELMANUS_MODEL": "test-model",
            "EXCELMANUS_EXA_API_KEY": "exa-secret",
        }
        with patch.dict(os.environ, env, clear=False):
            config = load_config()
            assert config.exa_api_key == "exa-secret"

    def test_tavily_api_key_env(self):
        """EXCELMANUS_TAVILY_API_KEY 环境变量。"""
        from excelmanus.config import load_config

        env = {
            "EXCELMANUS_API_KEY": "test",
            "EXCELMANUS_MODEL": "test-model",
            "EXCELMANUS_TAVILY_API_KEY": "tavily-secret",
        }
        with patch.dict(os.environ, env, clear=False):
            config = load_config()
            assert config.tavily_api_key == "tavily-secret"

    def test_brave_api_key_env(self):
        """EXCELMANUS_BRAVE_API_KEY 环境变量。"""
        from excelmanus.config import load_config

        env = {
            "EXCELMANUS_API_KEY": "test",
            "EXCELMANUS_MODEL": "test-model",
            "EXCELMANUS_BRAVE_API_KEY": "brave-secret",
        }
        with patch.dict(os.environ, env, clear=False):
            config = load_config()
            assert config.brave_api_key == "brave-secret"

    def test_no_api_keys_default_none(self):
        """未设置 API 密钥环境变量时默认为 None。"""
        from excelmanus.config import load_config

        env = {
            "EXCELMANUS_API_KEY": "test",
            "EXCELMANUS_MODEL": "test-model",
        }
        # 清除可能存在的搜索密钥环境变量
        for key in ("EXCELMANUS_EXA_API_KEY", "EXCELMANUS_TAVILY_API_KEY", "EXCELMANUS_BRAVE_API_KEY"):
            os.environ.pop(key, None)
        with patch.dict(os.environ, env, clear=False):
            config = load_config()
            assert config.exa_api_key is None
            assert config.tavily_api_key is None
            assert config.brave_api_key is None


# ── 合并逻辑（用户覆盖） ──────────────────────────────────


class TestMergeBuiltinWithUserConfigs:
    """用户 mcp.json 中同名配置覆盖内置搜索引擎。"""

    def test_user_exa_overrides_builtin(self):
        """用户配置了同名 'exa' → 内置 exa 被跳过。"""
        manager = MCPManager("/tmp", app_config=_FakeConfig())
        user_cfg = MCPServerConfig(
            name="exa",
            transport="sse",
            url="https://custom-exa.example.com/sse",
        )
        merged = manager._merge_builtin_configs([user_cfg])
        exa_configs = [c for c in merged if c.name == "exa"]
        assert len(exa_configs) == 1
        assert exa_configs[0].url == "https://custom-exa.example.com/sse"

    @patch("excelmanus.mcp.builtin._has_npx", return_value=True)
    def test_user_tavily_overrides_builtin(self, _):
        """用户配置了同名 'tavily' → 内置 tavily 被跳过。"""
        cfg = _FakeConfig(tavily_api_key="tk")
        manager = MCPManager("/tmp", app_config=cfg)
        user_cfg = MCPServerConfig(
            name="tavily",
            transport="stdio",
            command="custom-tavily",
        )
        merged = manager._merge_builtin_configs([user_cfg])
        tavily_configs = [c for c in merged if c.name == "tavily"]
        assert len(tavily_configs) == 1
        assert tavily_configs[0].command == "custom-tavily"


# ── context_builder MCP 使用提示 ──────────────────────────


class TestContextBuilderHints:
    """验证 context_builder 中 MCP 使用提示字典。"""

    def test_hints_include_all_search_engines(self):
        from excelmanus.engine_core.context_builder import ContextBuilder

        hints = ContextBuilder._MCP_USAGE_HINTS
        assert "exa" in hints
        assert "tavily" in hints
        assert "brave" in hints
        assert "context7" in hints

    def test_exa_hint_mentions_coverage(self):
        from excelmanus.engine_core.context_builder import ContextBuilder

        assert "覆盖面" in ContextBuilder._MCP_USAGE_HINTS["exa"] or "通用" in ContextBuilder._MCP_USAGE_HINTS["exa"]

    def test_tavily_hint_mentions_ai(self):
        from excelmanus.engine_core.context_builder import ContextBuilder

        assert "AI" in ContextBuilder._MCP_USAGE_HINTS["tavily"]

    def test_brave_hint_mentions_privacy(self):
        from excelmanus.engine_core.context_builder import ContextBuilder

        assert "隐私" in ContextBuilder._MCP_USAGE_HINTS["brave"]
