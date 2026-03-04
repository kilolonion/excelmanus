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
_EXA_MCP_URL = "https://mcp.exa.ai/mcp"
_EXA_SERVER_NAME = "exa"


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
        configs.append(
            MCPServerConfig(
                name=_EXA_SERVER_NAME,
                transport="streamable_http",
                url=_EXA_MCP_URL,
                timeout=30,
                auto_approve=["*"],
            )
        )
        logger.debug("内置 Exa 搜索已启用")
    else:
        logger.debug("内置 Exa 搜索已禁用（EXCELMANUS_EXA_SEARCH=false）")

    return configs
