"""MCP Server 模块：将 Skills 暴露为 MCP 工具供外部 AI 客户端调用。

通过 stdio 传输方式与客户端通信，符合 MCP SDK 标准实现。
"""

from __future__ import annotations

import asyncio
import importlib
import json
import pkgutil

from mcp import types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from excelmanus.config import load_config
from excelmanus.logger import get_logger, setup_logging
from excelmanus.skills import (
    SkillRegistry,
    ToolExecutionError,
    ToolNotFoundError,
)

logger = get_logger("mcp")


def _format_mcp_error(error_type: str, message: str) -> str:
    """统一 MCP 错误文本格式，包含错误类型与描述。"""
    return f"{error_type}: {message}"


def _init_skill_guards(
    workspace_root: str, package_name: str = "excelmanus.skills"
) -> None:
    """为支持 init_guard 的 Skill 模块注入 workspace_root。"""
    try:
        package = importlib.import_module(package_name)
    except ImportError:
        logger.warning("无法导入 Skill 包 '%s'，跳过 guard 初始化", package_name)
        return

    package_path = getattr(package, "__path__", None)
    if package_path is None:
        logger.warning("Skill 包 '%s' 没有 __path__，跳过 guard 初始化", package_name)
        return

    for _, module_name, _ in pkgutil.iter_modules(package_path):
        full_name = f"{package_name}.{module_name}"
        try:
            module = importlib.import_module(full_name)
        except Exception:
            logger.warning("导入 Skill 模块 '%s' 失败，跳过 guard 初始化", full_name, exc_info=True)
            continue

        init_guard = getattr(module, "init_guard", None)
        if not callable(init_guard):
            continue

        try:
            init_guard(workspace_root)
            logger.debug(
                "已初始化 Skill guard: %s (workspace_root=%s)",
                full_name,
                workspace_root,
            )
        except Exception:
            logger.warning("初始化 Skill guard 失败: %s", full_name, exc_info=True)


def create_mcp_server(registry: SkillRegistry) -> Server:
    """将 registry 中工具转换为 MCP 工具并注册 call_tool handler。

    Args:
        registry: 已完成工具注册的 SkillRegistry 实例。

    Returns:
        配置好 handler 的 MCP Server 实例。
    """
    server = Server("excelmanus-mcp")

    @server.list_tools()
    async def handle_list_tools() -> list[types.Tool]:
        """返回所有已注册工具的 MCP 工具定义。"""
        tools: list[types.Tool] = []
        for tool_def in registry.get_all_tools():
            tools.append(
                types.Tool(
                    name=tool_def.name,
                    description=tool_def.description,
                    inputSchema=tool_def.input_schema,
                )
            )
        return tools

    @server.call_tool()
    async def handle_call_tool(
        name: str, arguments: dict | None
    ) -> list[types.TextContent]:
        """将 MCP 请求参数映射为对应 Skill 工具函数并执行。

        成功时返回工具执行结果；失败时抛出异常由 SDK 转换为错误响应。
        """
        safe_arguments = arguments or {}
        try:
            result = registry.call_tool(name, safe_arguments)
            # 将结果统一转为字符串
            if isinstance(result, str):
                text = result
            else:
                text = json.dumps(result, ensure_ascii=False, default=str)
            return [types.TextContent(type="text", text=text)]
        except ToolNotFoundError as exc:
            raise ValueError(
                _format_mcp_error("ToolNotFoundError", str(exc))
            ) from exc
        except ToolExecutionError as exc:
            raise RuntimeError(
                _format_mcp_error("ToolExecutionError", str(exc))
            ) from exc
        except Exception as exc:
            raise RuntimeError(
                _format_mcp_error("MCPServerError", str(exc))
            ) from exc

    return server


async def _run_stdio_server_async() -> None:
    """异步启动 MCP Server（stdio 传输）。"""
    # 加载配置并初始化日志
    config = load_config()
    setup_logging(config.log_level)

    # 创建 SkillRegistry 并自动发现 Skills
    registry = SkillRegistry()
    registry.auto_discover()
    _init_skill_guards(config.workspace_root)

    tool_count = len(registry.get_all_tools())
    logger.info("MCP Server 启动，已注册 %d 个工具", tool_count)

    # 创建 MCP Server
    server = create_mcp_server(registry)

    # 通过 stdio 传输启动服务
    async with stdio_server() as (read_stream, write_stream):
        init_options = server.create_initialization_options()
        await server.run(read_stream, write_stream, init_options)


def run_stdio_server() -> None:
    """以 stdio 传输方式启动 MCP Server。

    作为 pyproject.toml 中 excelmanus-mcp 入口点的目标函数。
    """
    asyncio.run(_run_stdio_server_async())
