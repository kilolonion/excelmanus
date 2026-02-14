"""MCP Client 模块。

为 ExcelManus 提供 MCP（Model Context Protocol）Client 能力，
支持连接外部 MCP Server、发现远程工具并注册到 ToolRegistry。
"""

from excelmanus.mcp.config import MCPConfigLoader, MCPServerConfig
from excelmanus.mcp.manager import MCPManager

__all__ = [
    "MCPConfigLoader",
    "MCPManager",
    "MCPServerConfig",
]
