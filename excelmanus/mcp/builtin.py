"""内置 MCP Server 配置。

提供开箱即用的搜索引擎 MCP Server（Exa / Tavily / Brave），无需用户手动配置 mcp.json。
用户可通过 mcp.json 中同名配置覆盖内置 Server，或通过环境变量关闭。

搜索引擎启用策略（"默认+可选"模式）：
1. ``exa_search_enabled=False`` → 禁用全部内置搜索引擎
2. ``search_default_provider`` 指定的默认引擎始终启用
3. 非默认引擎仅在配置了对应 API 密钥后额外启用
4. 默认引擎为 tavily/brave 但缺少 API 密钥 → 降级回 exa 并发出警告
"""

from __future__ import annotations

import logging
import shutil
from typing import TYPE_CHECKING

from excelmanus.mcp.config import MCPServerConfig

if TYPE_CHECKING:
    from collections.abc import Callable

    from excelmanus.config import Config

logger = logging.getLogger("excelmanus.mcp.builtin")

# ── Exa ────────────────────────────────────────────────────────────────
_EXA_STREAMABLE_HTTP_URL = "https://mcp.exa.ai/mcp"
_EXA_SSE_URL = "https://mcp.exa.ai/sse"
_EXA_SERVER_NAME = "exa"

# 向后兼容：测试和外部引用可能使用旧常量名
_EXA_MCP_URL = _EXA_STREAMABLE_HTTP_URL

# ── Tavily ─────────────────────────────────────────────────────────────
_TAVILY_SERVER_NAME = "tavily"
_TAVILY_NPX_PACKAGE = "@tavily/mcp@latest"

# ── Brave ──────────────────────────────────────────────────────────────
_BRAVE_SERVER_NAME = "brave"
_BRAVE_NPX_PACKAGE = "@modelcontextprotocol/server-brave-search"

# 所有内置搜索引擎名称集合（供外部引用）
BUILTIN_SEARCH_SERVER_NAMES = frozenset({
    _EXA_SERVER_NAME, _TAVILY_SERVER_NAME, _BRAVE_SERVER_NAME,
})


def _sdk_supports_streamable_http() -> bool:
    """检测当前 MCP SDK 是否支持 streamable_http 传输。"""
    try:
        from mcp.client.streamable_http import streamable_http_client  # noqa: F401
        return True
    except Exception:
        return False


def _has_npx() -> bool:
    """检测系统中是否可用 npx（Node.js 包执行器）。"""
    return shutil.which("npx") is not None


def _build_exa_config(
    config: "Config",
) -> MCPServerConfig:
    """构建 Exa MCP Server 配置。

    Exa 支持免费模式（无 API Key）和付费模式（通过 ``x-api-key`` header 传递）。
    """
    if _sdk_supports_streamable_http():
        transport = "streamable_http"
        url = _EXA_STREAMABLE_HTTP_URL
    else:
        transport = "sse"
        url = _EXA_SSE_URL
        logger.info(
            "MCP SDK 不支持 streamable_http，Exa 搜索降级为 SSE 传输 (%s)",
            url,
        )

    headers: dict[str, str] = {}
    if config.exa_api_key:
        headers["x-api-key"] = config.exa_api_key
        logger.debug("Exa 搜索使用 API 密钥（付费模式）")
    else:
        logger.debug("Exa 搜索使用免费模式（无 API 密钥）")

    return MCPServerConfig(
        name=_EXA_SERVER_NAME,
        transport=transport,
        url=url,
        headers=headers,
        timeout=30,
        auto_approve=["*"],
        scope="search",
    )


def _build_tavily_config(api_key: str) -> MCPServerConfig:
    """构建 Tavily MCP Server 配置（stdio 传输，需要 Node.js）。"""
    return MCPServerConfig(
        name=_TAVILY_SERVER_NAME,
        transport="stdio",
        command="npx",
        args=["-y", _TAVILY_NPX_PACKAGE],
        env={"TAVILY_API_KEY": api_key},
        timeout=30,
        auto_approve=["*"],
        scope="search",
    )


def _build_brave_config(api_key: str) -> MCPServerConfig:
    """构建 Brave Search MCP Server 配置（stdio 传输，需要 Node.js）。"""
    return MCPServerConfig(
        name=_BRAVE_SERVER_NAME,
        transport="stdio",
        command="npx",
        args=["-y", _BRAVE_NPX_PACKAGE],
        env={"BRAVE_API_KEY": api_key},
        timeout=30,
        auto_approve=["*"],
        scope="search",
    )


# 引擎名 → (构建函数, 获取密钥函数, 是否需要密钥)
_ENGINE_BUILDERS: dict[str, tuple] = {
    "exa": (_build_exa_config, lambda c: c.exa_api_key, False),
    "tavily": (lambda c: _build_tavily_config(c.tavily_api_key), lambda c: c.tavily_api_key, True),
    "brave": (lambda c: _build_brave_config(c.brave_api_key), lambda c: c.brave_api_key, True),
}


def get_builtin_mcp_configs(config: "Config") -> list[MCPServerConfig]:
    """根据当前配置返回内置 MCP Server 列表。

    内置 Server 在 MCPManager.initialize() 中与用户配置合并，
    用户 mcp.json 中同名条目优先（覆盖内置）。

    启用策略（"默认+可选"模式）：
    1. ``exa_search_enabled=False`` → 返回空列表
    2. 默认引擎始终启用；非默认引擎在有 API 密钥时额外启用
    3. 默认引擎为 tavily/brave 但缺少密钥 → 降级回 exa

    Args:
        config: 应用配置实例。

    Returns:
        内置 MCPServerConfig 列表。
    """
    if not config.exa_search_enabled:
        logger.debug("内置搜索引擎已禁用（EXCELMANUS_EXA_SEARCH=false）")
        return []

    configs: list[MCPServerConfig] = []
    default_provider = config.search_default_provider
    npx_available: bool | None = None  # 延迟检测

    def _check_npx() -> bool:
        nonlocal npx_available
        if npx_available is None:
            npx_available = _has_npx()
            if not npx_available:
                logger.warning(
                    "系统中未找到 npx（Node.js），stdio 传输的搜索引擎将不可用。"
                    "请安装 Node.js 以使用 Tavily/Brave 搜索。"
                )
        return npx_available

    # ── 1. 启用默认引擎 ──────────────────────────────────────────
    default_cfg = _try_build_engine(config, default_provider, _check_npx, is_default=True)
    if default_cfg:
        configs.append(default_cfg)
        logger.info("默认搜索引擎: %s", default_provider)
    else:
        # 默认引擎不可用（缺密钥或缺 npx），降级回 exa
        if default_provider != "exa":
            logger.warning(
                "默认搜索引擎 '%s' 不可用（缺少 API 密钥或 Node.js），降级为 exa",
                default_provider,
            )
            exa_cfg = _build_exa_config(config)
            configs.append(exa_cfg)
            logger.info("默认搜索引擎: exa（降级）")

    # ── 2. 额外启用有密钥的非默认引擎 ────────────────────────────
    enabled_names = {cfg.name for cfg in configs}
    for engine_name in ("exa", "tavily", "brave"):
        if engine_name in enabled_names:
            continue
        extra_cfg = _try_build_engine(config, engine_name, _check_npx, is_default=False)
        if extra_cfg:
            configs.append(extra_cfg)

    if not configs:
        logger.warning("没有可用的内置搜索引擎")

    return configs


def _try_build_engine(
    config: "Config",
    engine_name: str,
    check_npx: "Callable[[], bool]",
    *,
    is_default: bool,
) -> MCPServerConfig | None:
    """尝试构建指定引擎的 MCPServerConfig。

    返回 None 表示该引擎不可用（缺密钥/缺 npx）。
    """
    if engine_name not in _ENGINE_BUILDERS:
        logger.warning("未知搜索引擎: %s", engine_name)
        return None

    _, get_key, requires_key = _ENGINE_BUILDERS[engine_name]

    # 检查 API 密钥
    if requires_key and not get_key(config):
        if is_default:
            logger.warning(
                "默认搜索引擎 '%s' 缺少 API 密钥（需设置环境变量）",
                engine_name,
            )
        return None

    # 非默认引擎且无密钥（exa 免费可跳过密钥检查，但作为非默认引擎时需要密钥才启用）
    if not is_default and engine_name == "exa" and not get_key(config):
        return None

    # 检查 npx 依赖（仅 stdio 引擎需要）
    if engine_name in ("tavily", "brave") and not check_npx():
        if is_default:
            logger.warning(
                "默认搜索引擎 '%s' 需要 Node.js (npx) 但未安装",
                engine_name,
            )
        return None

    # 构建配置
    builder = _ENGINE_BUILDERS[engine_name][0]
    cfg = builder(config)
    if not is_default:
        logger.info("额外启用搜索引擎: %s", engine_name)
    return cfg
