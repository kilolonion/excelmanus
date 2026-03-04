"""内置 MCP Server 配置。

提供开箱即用的 MCP Server（如 Exa 免费搜索），无需用户手动配置 mcp.json。
用户可通过 mcp.json 中同名配置覆盖内置 Server，或通过环境变量关闭。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from excelmanus.mcp.config import MCPServerConfig

if TYPE_CHECKING:
    from excelmanus.config import Config

logger = logging.getLogger("excelmanus.mcp.builtin")

# Exa 免费搜索 MCP 端点（无需 API Key）
_EXA_STREAMABLE_HTTP_URL = "https://mcp.exa.ai/mcp"  # streamable_http 端点
_EXA_SSE_URL = "https://mcp.exa.ai/sse"  # SSE 回退端点
_EXA_SERVER_NAME = "exa"

# 向后兼容：测试和外部引用可能使用旧常量名
_EXA_MCP_URL = _EXA_STREAMABLE_HTTP_URL


def _sdk_supports_streamable_http() -> bool:
    """检测当前 MCP SDK 是否支持 streamable_http 传输。"""
    try:
        from mcp.client.streamable_http import streamable_http_client  # noqa: F401
        return True
    except Exception:
        return False


def get_builtin_mcp_configs(config: "Config") -> list[MCPServerConfig]:
    """根据当前配置返回内置 MCP Server 列表。

    内置 Server 在 MCPManager.initialize() 中与用户配置合并，
    用户 mcp.json 中同名条目优先（覆盖内置）。

    Args:
        config: 应用配置实例。

    Returns:
        内置 MCPServerConfig 列表。
    """
    configs: list[MCPServerConfig] = []

    if config.exa_search_enabled:
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
        configs.append(
            MCPServerConfig(
                name=_EXA_SERVER_NAME,
                transport=transport,
                url=url,
                timeout=30,
                auto_approve=["*"],
            )
        )
        logger.debug("内置 Exa 搜索已启用 (transport=%s)", transport)
    else:
        logger.debug("内置 Exa 搜索已禁用（EXCELMANUS_EXA_SEARCH=false）")

    return configs
